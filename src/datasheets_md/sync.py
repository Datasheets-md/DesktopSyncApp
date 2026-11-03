"""The sync engine behind `dsmd` and the GUI (Spec 27a 3.4-3.6).

Every function takes a `Repo` and/or an `http.Client` and a `say` callback
for progress lines, and returns plain data the CLI prints (or serialises with
--json) and the GUI shows. Nothing here prints by itself.
"""
import difflib
import io
import json
import os
import shutil
import webbrowser
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed

import yaml

from . import __version__, library
from .http import ApiError
from .merge import has_conflict, merge
from .repo import Repo, RepoError

SYNC = "/workspace/sync"
BATCH_FILES = 100
BATCH_PARTS = 20
SETTINGS_DIR = "settings/"     # every file under it is editable; the server lists them
EDITABLE = ("parameters.jsonl", "pins.csv", "cad.yaml")
MARKDOWN = "datasheet.md"      # one per family datasheet folder (datasheets/<manufacturer>/<pdf name>/)
IMAGES = "images"               # the figures datasheet.md links to, next to it
LIBRARY_DIR = "library"


def _quiet(*_args, **_kw):
    pass


def _manifest(client, repo=None, parts=None, incremental=False):
    """The server's manifest. `incremental` (a pull of a full working copy that
    holds a cursor) asks only for the parts changed since: the answer's
    `delta` is then True and `deleted` lists the parts removed since. A server
    without revisions ignores `since` and answers the full manifest."""
    sparse = parts if parts is not None else (repo.state.get("sparse") if repo else None)
    params = {"parts": ",".join(sparse)} if sparse else {}
    since = repo.state.get("cursor") if (repo and incremental and not sparse) else None
    if since:
        params["since"] = since
    manifest = client.get(f"{SYNC}/manifest/", params=params or None)
    manifest["delta"] = bool(since) and "cursor" in manifest
    return manifest


def _say_pending(manifest, say):
    if manifest.get("pending"):
        say(f"The server is still recording {manifest['pending']} changed part(s); pull again in a minute to get them.")


def _download(client, paths, say=_quiet):
    """Yield {path: {etag, content}} for BATCH_FILES paths at a time, so a caller can
    put each batch on disk before asking for the next."""
    for start in range(0, len(paths), BATCH_FILES):
        chunk = paths[start:start + BATCH_FILES]
        yield client.post(f"{SYNC}/files/", {"paths": chunk})["files"]
        say(f"  downloaded {min(start + BATCH_FILES, len(paths))}/{len(paths)} files")


def _markdown(client, folders, say=_quiet):
    """Yield (folder, datasheet.md text, {png name: bytes}) for {folder: part uuid} as
    each arrives: the family's stored document with its figures and every element-id
    sentinel kept, downloaded through one of its parts, 8 at a time. A datasheet gone
    since the manifest is left out. Stopping early drops the downloads still queued."""
    def one(folder, uuid):
        try:
            resp = client.get(f"/workspace/components/{uuid}/download-markdown/",
                              params={"images": "zip", "metadata": "keep", "title": "omit"}, raw=True)
        except ApiError as exc:
            if exc.status == 404:
                return folder, None
            raise
        with zipfile.ZipFile(io.BytesIO(resp.content)) as archive:
            names = archive.namelist()
            md = next(n for n in names if "/" not in n and n.endswith(".md"))
            images = {n[len(IMAGES) + 1:]: archive.read(n) for n in names
                      if n.startswith(IMAGES + "/") and not n.endswith("/")}
            return folder, (archive.read(md).decode("utf-8"), images)
    pool = ThreadPoolExecutor(max_workers=8)
    try:
        futures = {pool.submit(one, folder, uuid) for folder, uuid in folders.items()}
        for done, future in enumerate(as_completed(futures), 1):
            futures.discard(future)                 # let the images go once they are on disk
            folder, got = future.result()
            if got:
                yield (folder, *got)
            if len(folders) <= 20 or done % 10 == 0 or done == len(folders):
                say(f"  datasheets {done}/{len(folders)}")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _put_images(folder, images):
    """Replace a datasheet's images/ folder with these PNGs (read-only, never merged)."""
    shutil.rmtree(folder, ignore_errors=True)
    for name, data in images.items():
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(data)


def _wanted(files):
    """A part's manifest files minus datasheet.md, which revisions recorded before
    the shared datasheet folders still list per part."""
    return {name: etag for name, etag in files.items() if name != MARKDOWN}


def _datasheets(repo, manifest):
    """{folder: {etag, part}} the server lists, or {} when dsmd.toml turns markdown off."""
    return (manifest.get("datasheets") or {}) if repo.config()["markdown"] else {}


def _sheets(repo):
    """{folder: etag} of the datasheets in the working copy."""
    return repo.state.setdefault("datasheets", {})


# ----- clone ---------------------------------------------------------------------------

def resolve_workspace(client, ident):
    """A workspace by id or (case-insensitive) name among the caller's memberships."""
    if str(ident).isdigit():
        return {"id": int(ident), "name": None}
    workspaces = client.get("/workspaces/", workspace=False)
    hits = [w for w in workspaces if (w.get("name") or "").lower() == str(ident).lower()]
    if len(hits) != 1:
        names = ", ".join(f"{w['id']} {w.get('name')}" for w in workspaces)
        raise RepoError(f"no single workspace named {ident!r}; yours are: {names}")
    return hits[0]


def clone(client, workspace_ident, directory, parts=None, say=_quiet):
    """Files reach the disk as they arrive and the state records them, so a clone
    that stops keeps its work: the same call on the same folder continues it. The
    cursor is set only at the end, and the working copy refuses fetch and push
    until then. A failed library step leaves a finished clone (`library_error`)."""
    say("Reading the workspace...")
    workspace = resolve_workspace(client, workspace_ident)
    client.workspace_id = workspace["id"]
    manifest = _manifest(client, parts=parts)
    workspace = {**manifest["workspace"]}
    repo = Repo.unfinished(directory, client.server, workspace["id"])
    if repo:
        say(f"Continuing the clone in {repo.root}")
    else:
        repo = Repo.create(directory, client.server, workspace)
    with repo.lock():
        repo.state.update(renderer=manifest["renderer"], sparse=parts or None, incomplete=True)
        repo.save()
        _say_pending(manifest, say)
        try:
            files, sheets = _clone_files(repo, client, manifest, say)
        except (ApiError, KeyboardInterrupt):
            say("Stopped; everything downloaded so far is kept. Run the same dsmd clone command to continue.")
            raise
        del repo.state["incomplete"]
        repo.state["cursor"] = manifest.get("cursor")
        repo.save()
        error = _try_library(repo, client, say)
    return {"root": str(repo.root), "workspace": workspace, "parts": len(manifest["parts"]), "files": files,
            "datasheets": sheets, "library_error": error}


def _clone_files(repo, client, manifest, say):
    """Bring the working copy to the manifest, skipping what an earlier try already
    wrote. Returns how many files and datasheets it now holds."""
    _drop_stale(repo, manifest)
    parts = repo.parts()
    wanted = {}
    for part in manifest["parts"]:
        parts.setdefault(part["uuid"], {"path": part["path"], "files": {}})["path"] = part["path"]
        for name, etag in _wanted(part["files"]).items():
            wanted[f"{part['path']}/{name}"] = (part["uuid"], name, etag)
    wanted.update({path: (None, path, etag) for path, etag in manifest["settings"].items()})

    def recorded(uuid, name):
        return parts[uuid]["files"].get(name) if uuid else repo.state["settings"].get(name)
    todo = [path for path, (uuid, name, etag) in wanted.items() if recorded(uuid, name) != etag]
    listed, have = _datasheets(repo, manifest), _sheets(repo)
    sheets = {folder: sheet["part"] for folder, sheet in listed.items() if have.get(folder) != sheet["etag"]}
    say(f"Cloning {repo.state.get('workspace_name')} ({len(manifest['parts'])} parts, {len(todo)} files, "
        f"{len(sheets)} datasheets) into {repo.root}")
    for batch in _download(client, todo, say):
        for path, got in batch.items():
            uuid, name, _ = wanted[path]
            repo.put(path, got["content"])
            (parts[uuid]["files"] if uuid else repo.state["settings"])[name] = got["etag"]
        repo.save()
    for done, (folder, text, images) in enumerate(_markdown(client, sheets, say), 1):
        repo.put(f"{folder}/{MARKDOWN}", text)
        shutil.rmtree(repo.working(folder) / IMAGES, ignore_errors=True)
        _put_images(repo.working(folder) / IMAGES, images)
        have[folder] = listed[folder]["etag"]
        if done % 10 == 0:
            repo.save()
    repo.save()
    files = sum(recorded(uuid, name) is not None for uuid, name, _ in wanted.values())
    return files, sum(folder in have for folder in listed)


def _drop_stale(repo, manifest):
    """What an earlier try of a clone wrote that the server has since moved or removed."""
    parts, report = repo.parts(), {"warnings": []}
    listed = {part["uuid"]: part for part in manifest["parts"]}
    for uuid, entry in list(parts.items()):
        part = listed.get(uuid)
        if part and entry["path"] != part["path"] and repo.move(entry["path"], part["path"]):
            entry["path"] = part["path"]
            continue
        wanted = _wanted(part["files"]) if part and entry["path"] == part["path"] else {}
        for name in [n for n in entry["files"] if n not in wanted]:
            repo.remove(f"{entry['path']}/{name}")
            del entry["files"][name]
        if not part:
            del parts[uuid]
    sheets, have = _datasheets(repo, manifest), _sheets(repo)
    for folder in [f for f in have if f not in sheets]:
        _drop_datasheet(repo, folder, report)
        del have[folder]


# ----- fetch / pull ----------------------------------------------------------------------

def fetch(repo, client, say=_quiet):
    """Download what changed on the server into .dsmd/incoming/ (not the working copy)."""
    _require_finished(repo)
    say("Checking the server for changes...")
    manifest = _manifest(client, repo, incremental=True)
    _say_pending(manifest, say)
    known = repo.parts()
    wanted = {}
    for part in manifest["parts"]:
        have = known.get(part["uuid"], {}).get("files", {})
        for name, etag in _wanted(part["files"]).items():
            if have.get(name) != etag:
                wanted[f"{part['path']}/{name}"] = f"{part['uuid']}/{name}"
    for path, etag in manifest["settings"].items():
        if repo.state["settings"].get(path) != etag:
            wanted[path] = path
    incoming = repo.dot / "incoming"
    shutil.rmtree(incoming, ignore_errors=True)
    pending = {}
    for batch in _download(client, list(wanted), say):
        for path, got in batch.items():
            repo.write(repo.incoming(wanted[path]), got["content"])
            pending[wanted[path]] = got["etag"]
    sheets, have = _datasheets(repo, manifest), _sheets(repo)
    changed = {folder: sheet["part"] for folder, sheet in sheets.items() if have.get(folder) != sheet["etag"]}
    pending_sheets = {}
    for folder, text, images in _markdown(client, changed, say):
        repo.write(repo.incoming(f"{folder}/{MARKDOWN}"), text)
        _put_images(repo.incoming(f"{folder}/{IMAGES}"), images)
        pending_sheets[folder] = sheets[folder]["etag"]
    repo.write(incoming / "manifest.json", json.dumps({"manifest": manifest, "pending": pending,
                                                       "datasheets": pending_sheets}))
    new = [p for p in manifest["parts"] if p["uuid"] not in known]
    gone = _gone(manifest, known)
    moved = [p for p in manifest["parts"] if p["uuid"] in known and known[p["uuid"]]["path"] != p["path"]]
    changed_parts = {key.split("/", 1)[0] for key in pending if "/" in key and not key.startswith("settings/")}
    return {"changed_files": len(pending), "changed_parts": len(changed_parts - {p["uuid"] for p in new}),
            "new_parts": len(new), "removed_parts": len(gone), "moved_parts": len(moved),
            "changed_datasheets": len(pending_sheets)}


def _gone(manifest, known):
    """The known parts the server no longer has: listed in `deleted` by an
    incremental manifest, absent from a full one."""
    if manifest.get("delta"):
        return [u for u in manifest.get("deleted", []) if u in known]
    listed = {p["uuid"] for p in manifest["parts"]}
    return [u for u in known if u not in listed]


def _merge_into(repo, rel, name, server_text, editable, report):
    """Bring one pulled file into the working copy and base (3.5)."""
    base, local = repo.read(repo.base(rel)), repo.read(repo.working(rel))
    if local is None or local == base or local == server_text:
        repo.put(rel, server_text)
        return
    if not editable:
        repo.write(repo.working(rel + ".local"), local)
        repo.put(rel, server_text)
        report["saved_local"].append(rel + ".local")
        return
    text, conflicts = merge(name, base, local, server_text)
    repo.write(repo.working(rel), text)
    repo.write(repo.base(rel), server_text)
    if conflicts:
        report["conflicts"].append({"path": rel, "conflicts": conflicts})
    elif text != server_text:
        report["merged"].append(rel)


def _unmodified(repo, rel):
    return repo.read(repo.working(rel)) == repo.read(repo.base(rel))


def pull(repo, client, *, rebuild_library=False, say=_quiet):
    conflicted = [f["path"] for p in status(repo)["parts"] for f in p["files"] if f["state"] == "conflicted"]
    if conflicted:
        raise RepoError("resolve the conflict markers first (or dsmd restore the file):\n  " + "\n  ".join(conflicted))
    summary = fetch(repo, client, say)
    saved = json.loads(repo.read(repo.dot / "incoming" / "manifest.json"))
    manifest, pending = saved["manifest"], saved["pending"]
    report = {**summary, "conflicts": [], "merged": [], "saved_local": [], "kept": [], "warnings": []}
    parts = repo.parts()
    _drop_part_copies(repo, report)
    for part in manifest["parts"]:
        uuid = part["uuid"]
        entry = parts.get(uuid)
        if entry is not None and entry["path"] != part["path"]:
            if repo.move(entry["path"], part["path"]):
                say(f"  moved {entry['path']} -> {part['path']}")
                entry["path"] = part["path"]
            else:
                report["warnings"].append(f"{part['path']} exists locally; {entry['path']} was not moved")
                continue
        if entry is None:
            entry = parts[uuid] = {"path": part["path"], "files": {}}
        wanted = _wanted(part["files"])
        for name in list(entry["files"]):
            if name not in wanted:                          # the server no longer has this file
                rel = f"{entry['path']}/{name}"
                repo.remove(rel, working=_unmodified(repo, rel))
                del entry["files"][name]
        for name in wanted:
            key = f"{uuid}/{name}"
            if key not in pending:
                continue
            rel = f"{entry['path']}/{name}"
            _merge_into(repo, rel, name, repo.read(repo.incoming(key)), name in EDITABLE, report)
            entry["files"][name] = pending[key]
    for path in manifest["settings"]:
        if path in pending:
            _merge_into(repo, path, path, repo.read(repo.incoming(path)), True, report)
            repo.state["settings"][path] = pending[path]
    dropped = [path for path in repo.state["settings"] if path not in manifest["settings"]]
    for path in dropped:
        _drop_setting(repo, path, report)
    _pull_datasheets(repo, _datasheets(repo, manifest), saved.get("datasheets") or {}, report)
    for uuid in _gone(manifest, parts):
        entry = parts.pop(uuid)
        dirty = [n for n in entry["files"] if not _unmodified(repo, f"{entry['path']}/{n}")]
        for name in entry["files"]:
            repo.remove(f"{entry['path']}/{name}", working=not dirty)
        if dirty:
            report["kept"].append(entry["path"])
            report["warnings"].append(f"{entry['path']} was removed on the server but has local edits; kept, untracked")
    repo.state["renderer"] = manifest["renderer"]
    repo.state["cursor"] = manifest.get("cursor")
    repo.save()
    shutil.rmtree(repo.dot / "incoming", ignore_errors=True)
    changed = summary["changed_files"] or summary["new_parts"] or summary["removed_parts"] or dropped
    if changed or rebuild_library or library_missing(repo):
        report["library_error"] = _try_library(repo, client, say)
        if report["library_error"]:
            report["warnings"].append(report["library_error"])
    return report


def _drop_setting(repo, path, report):
    """A settings file the server no longer lists (a mapping renamed or deleted,
    or the single settings/mapping.yaml of older versions): edits stay, untracked."""
    unmodified = _unmodified(repo, path)
    if not unmodified:
        report["warnings"].append(f"{path} is gone from the server but has local edits; kept, untracked. "
                                  f"Redo them in the matching file under {SETTINGS_DIR}")
    repo.remove(path, working=unmodified)
    del repo.state["settings"][path]


def _drop_datasheet(repo, folder, report):
    """Remove a datasheet.md and its images/; an edited datasheet.md stays, untracked."""
    rel = f"{folder}/{MARKDOWN}"
    unmodified = _unmodified(repo, rel)
    if unmodified:
        shutil.rmtree(repo.working(f"{folder}/{IMAGES}"), ignore_errors=True)
    else:
        report["warnings"].append(f"{rel} has local edits; kept, untracked")
    repo.remove(rel, working=unmodified)


def _drop_part_copies(repo, report):
    """Clones made before the shared datasheet folders hold a datasheet.md and an
    images/ folder in every part folder: remove them (once; the state forgets them)."""
    for entry in repo.parts().values():
        if entry["files"].pop(MARKDOWN, None) is not None:
            _drop_datasheet(repo, entry["path"], report)


def _pull_datasheets(repo, listed, pending, report):
    """Bring the fetched datasheets in and drop the ones the server no longer lists."""
    have = _sheets(repo)
    for folder, etag in pending.items():
        rel = f"{folder}/{MARKDOWN}"
        _merge_into(repo, rel, MARKDOWN, repo.read(repo.incoming(rel)), False, report)
        shutil.rmtree(repo.working(folder) / IMAGES, ignore_errors=True)
        if repo.incoming(f"{folder}/{IMAGES}").is_dir():
            shutil.copytree(repo.incoming(f"{folder}/{IMAGES}"), repo.working(folder) / IMAGES)
        have[folder] = etag
    for folder in [f for f in have if f not in listed]:
        _drop_datasheet(repo, folder, report)
        del have[folder]


def build_library(repo, client, say=_quiet):
    """`library_pending` stays set until a build finishes, so the next pull tries again."""
    cfg = repo.config()
    repo.state["library_pending"] = True
    repo.save()
    result = library.build(client, str(repo.working(LIBRARY_DIR)), kicad=cfg["kicad"], sqlite=cfg["sqlite"], say=say)
    repo.state["library"] = result["files"]
    del repo.state["library_pending"]
    repo.save()
    return result


def _try_library(repo, client, say):
    """Build library/; a server failure is a message, not an error: the parts and datasheets are in place."""
    try:
        build_library(repo, client, say)
    except ApiError as exc:
        return f"library/ was not built ({exc}); the parts and datasheets are in place, retry with dsmd pull --library"
    return None


def library_missing(repo):
    """library/ should exist and does not, or its last build did not finish."""
    cfg = repo.config()
    if not (cfg["kicad"] or cfg["sqlite"]):
        return False
    folder = repo.working(LIBRARY_DIR)
    return bool(repo.state.get("library_pending")) or not folder.is_dir() or not any(folder.iterdir())


def _resume_command(repo):
    return f"dsmd clone {repo.state['workspace_id']} {repo.root} --server {repo.state['server']}"


def _require_finished(repo):
    if repo.state.get("incomplete"):
        raise RepoError(f"this clone did not finish; continue it with: {_resume_command(repo)}")


# ----- status / diff / restore ----------------------------------------------------------------

def _file_state(repo, rel, name):
    local, base = repo.read(repo.working(rel)), repo.read(repo.base(rel))
    if local is None:
        return "deleted"
    if local == base:
        return None
    if has_conflict(local):
        return "conflicted"
    return "modified" if name in EDITABLE or name.startswith(SETTINGS_DIR) else "read-only-edited"


def status(repo):
    parts, tracked = [], set()
    for uuid, entry in sorted(repo.parts().items(), key=lambda kv: kv[1]["path"]):
        tracked.add(entry["path"])
        files = []
        for name in sorted(entry["files"]):
            rel = f"{entry['path']}/{name}"
            state = _file_state(repo, rel, name)
            if state:
                files.append({"name": name, "path": rel, "state": state})
            if repo.working(rel + ".local").exists():
                files.append({"name": name + ".local", "path": rel + ".local", "state": "saved-local-copy"})
        if files:
            parts.append({"uuid": uuid, "path": entry["path"], "files": files})
    settings = []
    for rel in sorted(repo.state.get("settings", {})):
        state = _file_state(repo, rel, rel)
        if state:
            settings.append({"path": rel, "state": state})
    datasheets = []
    for folder in sorted(repo.state.get("datasheets", {})):
        rel = f"{folder}/{MARKDOWN}"
        state = _file_state(repo, rel, MARKDOWN)
        if state:
            datasheets.append({"path": rel, "state": state})
        if repo.working(rel + ".local").exists():
            datasheets.append({"path": rel + ".local", "state": "saved-local-copy"})
    untracked = []
    parts_dir = repo.working("parts")
    if parts_dir.is_dir():
        for manufacturer in sorted(p for p in parts_dir.iterdir() if p.is_dir()):
            for folder in sorted(p for p in manufacturer.iterdir() if p.is_dir()):
                rel = f"parts/{manufacturer.name}/{folder.name}"
                if rel not in tracked:
                    untracked.append(rel)
    generated = [f"{LIBRARY_DIR}/{rel}" for rel in library.edited(str(repo.working(LIBRARY_DIR)), repo.state.get("library"))]
    incomplete, missing = bool(repo.state.get("incomplete")), library_missing(repo)
    return {"workspace": {"id": repo.state["workspace_id"], "name": repo.state.get("workspace_name")},
            "server": repo.state["server"], "parts": parts, "settings": settings, "datasheets": datasheets,
            "untracked": untracked, "generated_edited": generated,
            "incomplete": _resume_command(repo) if incomplete else None, "library_missing": missing,
            "nothing_to_push": not (parts or settings or datasheets or generated),
            "clean": not (parts or settings or datasheets or generated or incomplete or missing)}


def _selected(repo, target):
    """Tracked file paths matching a part identifier or a path prefix (all when target is None)."""
    paths = [f"{e['path']}/{n}" for e in repo.parts().values() for n in e["files"]]
    paths += list(repo.state.get("settings", {}))
    paths += [f"{folder}/{MARKDOWN}" for folder in repo.state.get("datasheets", {})]
    if not target:
        return paths
    rel = os.path.relpath(os.path.abspath(target), repo.root) if os.path.exists(target) else target.rstrip("/")
    hits = [p for p in paths if p == rel or p.startswith(rel + "/")]
    if hits:
        return hits
    uuid = repo.resolve(target)
    folder = repo.parts()[uuid]["path"]
    return [p for p in paths if p.startswith(folder + "/")]


def diff(repo, target=None):
    out = []
    for rel in _selected(repo, target):
        local, base = repo.read(repo.working(rel)), repo.read(repo.base(rel))
        if local == base:
            continue
        out.extend(difflib.unified_diff((base or "").splitlines(keepends=True), (local or "").splitlines(keepends=True),
                                        f"a/{rel}", f"b/{rel}"))
    return "".join(line if line.endswith("\n") else line + "\n" for line in out)


def restore(repo, target=None):
    restored = []
    for rel in _selected(repo, target):
        base = repo.read(repo.base(rel))
        if base is not None and repo.read(repo.working(rel)) != base:
            repo.write(repo.working(rel), base)
            restored.append(rel)
    return restored


# ----- push ---------------------------------------------------------------------------------

class PushRefused(RepoError):
    pass


def push(repo, client, message, say=_quiet):
    _require_finished(repo)
    report = status(repo)
    blocked = [f["path"] for p in report["parts"] for f in p["files"] if f["state"] in ("read-only-edited", "conflicted")]
    blocked += [s["path"] for s in report["settings"] + report["datasheets"]
                if s["state"] in ("read-only-edited", "conflicted")]
    if blocked:
        raise PushRefused("nothing was pushed; fix these first (dsmd restore <path> for read-only files, "
                          "resolve the conflict markers otherwise):\n  " + "\n  ".join(blocked))
    items = []
    for part in report["parts"]:
        entry = repo.parts()[part["uuid"]]
        files = {}
        for f in part["files"]:
            if f["state"] == "modified":
                files[f["name"]] = {"base_etag": entry["files"][f["name"]],
                                    "content": repo.read(repo.working(f["path"]))}
        if files:
            items.append({"uuid": part["uuid"], "files": files})
    settings = {s["path"]: {"base_etag": repo.state["settings"][s["path"]], "content": repo.read(repo.working(s["path"]))}
                for s in report["settings"] if s["state"] == "modified"}
    result = {"applied": [], "stale": [], "rejected": []}
    if not items and not settings:
        return result
    cad_changed = False
    for start in range(0, max(len(items), 1), BATCH_PARTS):
        batch = items[start:start + BATCH_PARTS]
        body = {"message": message, "parts": batch}
        if start == 0 and settings:
            body["settings"] = settings          # applied before the parts, so new categories exist for them
        response = client.post(f"{SYNC}/push/", body)
        for outcome in response.get("settings") or []:
            path = outcome["path"]
            result.setdefault(outcome["status"], []).append({"path": path, "errors": outcome.get("errors") or []})
            if outcome["status"] == "applied":
                served_files = outcome.get("files") or {}
                if served_files and path not in served_files:    # a renamed mapping's file follows its name
                    repo.remove(path)
                    del repo.state["settings"][path]
                for served, file in served_files.items():
                    repo.put(served, file["content"])
                    repo.state["settings"][served] = file["etag"]
                cad_changed = True               # the mapping and symbol style feed the KiCad library
        sent = {item["uuid"]: item for item in batch}
        for outcome in response["parts"]:
            uuid = outcome["uuid"]
            entry = repo.parts().get(uuid, {"path": uuid, "files": {}})
            line = {"uuid": uuid, "path": entry["path"], "errors": outcome.get("errors") or []}
            result.setdefault(outcome["status"], []).append(line)
            if outcome["status"] != "applied":
                continue
            for path, body in (outcome.get("files") or {}).items():
                name = path.rsplit("/", 1)[-1]
                repo.put(f"{entry['path']}/{name}", body["content"])
                entry["files"][name] = body["etag"]
            cad_changed |= bool({"pins.csv", "cad.yaml"} & set(sent[uuid]["files"]))
    repo.save()
    if cad_changed:
        build_library(repo, client, say)
    return result


# ----- log / parts / pdf / open ------------------------------------------------------------------

def log(repo, client, target=None, page=1):
    params = {"page": page}
    if target:
        params["part"] = repo.resolve(target)
    return client.get(f"{SYNC}/log/", params=params)


def part_add(repo, client, part_number, manufacturer=None, say=_quiet):
    _require_finished(repo)
    found = client.get("/public/search/", params={"q": part_number, "limit": 50}, workspace=False)
    rows = [r for r in found.get("components") or [] if (r.get("part_number") or "").lower() == part_number.lower()]
    if manufacturer:
        rows = [r for r in rows if manufacturer.lower() in (r.get("manufacturer") or "").lower()]
    if not rows:
        raise RepoError(f"{part_number} is not in the public catalogue"
                        + (f" for manufacturer {manufacturer}" if manufacturer else ""))
    if len(rows) > 1:
        options = ", ".join(sorted({r.get("manufacturer") or "?" for r in rows}))
        raise RepoError(f"{part_number} exists for several manufacturers ({options}); pass --manufacturer")
    created = client.post("/workspace/components/", {"public_component_uuid": rows[0]["uuid"]})
    uuid = (created or {}).get("uuid")
    say(f"Added {part_number} ({rows[0].get('manufacturer')}) to the workspace")
    if repo.state.get("sparse") and uuid:
        repo.state["sparse"].append(uuid)
        repo.save()
    return {"uuid": uuid, "public_uuid": rows[0]["uuid"], "pull": pull(repo, client, say=say)}


def part_rm(repo, client, ident, say=_quiet):
    _require_finished(repo)
    uuid = repo.resolve(ident)
    client.delete(f"/workspace/components/{uuid}/")
    say(f"Removed {repo.parts()[uuid]['path']} from the workspace")
    if repo.state.get("sparse") and uuid in repo.state["sparse"]:
        repo.state["sparse"].remove(uuid)
        repo.save()
    return {"uuid": uuid, "pull": pull(repo, client, say=say)}


def pdf(repo, client, ident):
    uuid = repo.resolve(ident)
    resp = client.get(f"/workspace/components/{uuid}/download-pdf/", raw=True)
    name = repo.parts()[uuid]["path"].rsplit("/", 1)[-1]
    target = repo.dot / "cache" / f"{name}.pdf"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(resp.content)
    return str(target)


def categories(client):
    """The catalogue's categories -- the ids a mapping file's category_map is keyed by."""
    return client.get("/workspace/mappings/system-categories/")["categories"]


FEEDBACK_KINDS = ("bug", "suggestion", "praise", "other")


def feedback(client, message, kind="other"):
    """Send a note to the datasheets.md team -- the channel the MCP's send_feedback uses."""
    return client.post("/mcp/feedback/", {"message": message, "kind": kind, "tool": "dsmd",
                                          "context": {"client": f"dsmd {__version__}"}})


def web_url(repo, ident):
    uuid = repo.resolve(ident)
    text = repo.read(repo.base(f"{repo.parts()[uuid]['path']}/part.yaml")) or "{}"
    return (yaml.safe_load(text) or {}).get("web_url")


def open_part(repo, ident):
    url = web_url(repo, ident)
    if not url:
        raise RepoError("this part has no web page recorded; run dsmd pull")
    webbrowser.open(url)
    return url


__all__ = ["ApiError", "RepoError", "PushRefused", "clone", "fetch", "pull", "status", "diff", "restore",
           "push", "log", "part_add", "part_rm", "pdf", "open_part", "build_library", "resolve_workspace", "feedback", "categories"]
