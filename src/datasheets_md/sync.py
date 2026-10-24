"""The sync engine behind `dsmd` and the GUI (Spec 27a 3.4-3.6).

Every function takes a `Repo` and/or an `http.Client` and a `say` callback
for progress lines, and returns plain data the CLI prints (or serialises with
--json) and the GUI shows. Nothing here prints by itself.
"""
import difflib
import json
import os
import shutil
import webbrowser

import yaml

from . import library
from .http import ApiError
from .merge import has_conflict, merge
from .repo import Repo, RepoError

SYNC = "/workspace/sync"
BATCH_FILES = 100
BATCH_PARTS = 20
EDITABLE = ("parameters.jsonl", "pins.csv", "cad.yaml")
MARKDOWN = "datasheet.md"
LIBRARY_DIR = "library"


def _quiet(*_args, **_kw):
    pass


def _manifest(client, repo=None, parts=None):
    sparse = parts if parts is not None else (repo.state.get("sparse") if repo else None)
    params = {"parts": ",".join(sparse)} if sparse else None
    return client.get(f"{SYNC}/manifest/", params=params)


def _download(client, paths, say=_quiet):
    """{path: {etag, content}} for every path, BATCH_FILES per request."""
    out = {}
    for start in range(0, len(paths), BATCH_FILES):
        chunk = paths[start:start + BATCH_FILES]
        out.update(client.post(f"{SYNC}/files/", {"paths": chunk})["files"])
        if len(paths) > BATCH_FILES:
            say(f"  downloaded {min(start + BATCH_FILES, len(paths))}/{len(paths)} files")
    return out


def _wanted(repo, files):
    """The manifest's files minus what dsmd.toml says not to pull."""
    if repo.config()["markdown"]:
        return files
    return {name: etag for name, etag in files.items() if name != MARKDOWN}


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
    workspace = resolve_workspace(client, workspace_ident)
    client.workspace_id = workspace["id"]
    manifest = _manifest(client, parts=parts)
    workspace = {**manifest["workspace"]}
    repo = Repo.create(directory, client.server, workspace)
    with repo.lock():
        repo.state.update(renderer=manifest["renderer"], sparse=parts or None)
        wanted = {}
        for part in manifest["parts"]:
            for name, etag in _wanted(repo, part["files"]).items():
                wanted[f"{part['path']}/{name}"] = (part["uuid"], name, etag)
        wanted.update({path: (None, path, etag) for path, etag in manifest["settings"].items()})
        say(f"Cloning {workspace['name']} ({len(manifest['parts'])} parts, {len(wanted)} files) into {repo.root}")
        files = _download(client, list(wanted), say)
        for part in manifest["parts"]:
            repo.state["parts"][part["uuid"]] = {"path": part["path"], "files": {}}
        for path, (uuid, name, _) in wanted.items():
            got = files.get(path)
            if got is None:
                continue
            repo.put(path, got["content"])
            if uuid:
                repo.state["parts"][uuid]["files"][name] = got["etag"]
            else:
                repo.state["settings"][path] = got["etag"]
        repo.save()
        build_library(repo, client, say)
    return {"root": str(repo.root), "workspace": workspace, "parts": len(manifest["parts"]), "files": len(files)}


# ----- fetch / pull ----------------------------------------------------------------------

def fetch(repo, client, say=_quiet):
    """Download what changed on the server into .dsmd/incoming/ (not the working copy)."""
    manifest = _manifest(client, repo)
    known = repo.parts()
    wanted = {}
    for part in manifest["parts"]:
        have = known.get(part["uuid"], {}).get("files", {})
        for name, etag in _wanted(repo, part["files"]).items():
            if have.get(name) != etag:
                wanted[f"{part['path']}/{name}"] = f"{part['uuid']}/{name}"
    for path, etag in manifest["settings"].items():
        if repo.state["settings"].get(path) != etag:
            wanted[path] = path
    files = _download(client, list(wanted), say) if wanted else {}
    incoming = repo.dot / "incoming"
    shutil.rmtree(incoming, ignore_errors=True)
    for path, key in wanted.items():
        if path in files:
            repo.write(repo.incoming(key), files[path]["content"])
    pending = {key: files[path]["etag"] for path, key in wanted.items() if path in files}
    repo.write(incoming / "manifest.json", json.dumps({"manifest": manifest, "pending": pending}))
    manifest_uuids = {p["uuid"] for p in manifest["parts"]}
    new = [p for p in manifest["parts"] if p["uuid"] not in known]
    gone = [u for u in known if u not in manifest_uuids]
    moved = [p for p in manifest["parts"] if p["uuid"] in known and known[p["uuid"]]["path"] != p["path"]]
    changed_parts = {key.split("/", 1)[0] for key in pending if "/" in key and not key.startswith("settings/")}
    return {"changed_files": len(pending), "changed_parts": len(changed_parts - {p["uuid"] for p in new}),
            "new_parts": len(new), "removed_parts": len(gone), "moved_parts": len(moved)}


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
        wanted = _wanted(repo, part["files"])
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
            _merge_into(repo, path, path, repo.read(repo.incoming(path)), False, report)
            repo.state["settings"][path] = pending[path]
    gone = [u for u in parts if u not in {p["uuid"] for p in manifest["parts"]}]
    for uuid in gone:
        entry = parts.pop(uuid)
        dirty = [n for n in entry["files"] if not _unmodified(repo, f"{entry['path']}/{n}")]
        for name in entry["files"]:
            repo.remove(f"{entry['path']}/{name}", working=not dirty)
        if dirty:
            report["kept"].append(entry["path"])
            report["warnings"].append(f"{entry['path']} was removed on the server but has local edits; kept, untracked")
    repo.state["renderer"] = manifest["renderer"]
    repo.save()
    shutil.rmtree(repo.dot / "incoming", ignore_errors=True)
    changed = summary["changed_files"] or summary["new_parts"] or summary["removed_parts"]
    if changed or rebuild_library:
        build_library(repo, client, say)
    return report


def build_library(repo, client, say=_quiet):
    cfg = repo.config()
    result = library.build(client, str(repo.working(LIBRARY_DIR)), kicad=cfg["kicad"], sqlite=cfg["sqlite"], say=say)
    repo.state["library"] = result["files"]
    repo.save()
    return result


# ----- status / diff / restore ----------------------------------------------------------------

def _file_state(repo, rel, name):
    local, base = repo.read(repo.working(rel)), repo.read(repo.base(rel))
    if local is None:
        return "deleted"
    if local == base:
        return None
    if has_conflict(local):
        return "conflicted"
    return "modified" if name in EDITABLE else "read-only-edited"


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
    untracked = []
    parts_dir = repo.working("parts")
    if parts_dir.is_dir():
        for manufacturer in sorted(p for p in parts_dir.iterdir() if p.is_dir()):
            for folder in sorted(p for p in manufacturer.iterdir() if p.is_dir()):
                rel = f"parts/{manufacturer.name}/{folder.name}"
                if rel not in tracked:
                    untracked.append(rel)
    generated = [f"{LIBRARY_DIR}/{rel}" for rel in library.edited(str(repo.working(LIBRARY_DIR)), repo.state.get("library"))]
    return {"workspace": {"id": repo.state["workspace_id"], "name": repo.state.get("workspace_name")},
            "server": repo.state["server"], "parts": parts, "settings": settings,
            "untracked": untracked, "generated_edited": generated,
            "clean": not (parts or settings or generated)}


def _selected(repo, target):
    """Tracked file paths matching a part identifier or a path prefix (all when target is None)."""
    paths = [f"{e['path']}/{n}" for e in repo.parts().values() for n in e["files"]]
    paths += list(repo.state.get("settings", {}))
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
    report = status(repo)
    blocked = [f["path"] for p in report["parts"] for f in p["files"] if f["state"] in ("read-only-edited", "conflicted")]
    blocked += [s["path"] for s in report["settings"] if s["state"] in ("read-only-edited", "conflicted")]
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
    result = {"applied": [], "stale": [], "rejected": []}
    if not items:
        return result
    cad_changed = False
    for start in range(0, len(items), BATCH_PARTS):
        batch = items[start:start + BATCH_PARTS]
        response = client.post(f"{SYNC}/push/", {"message": message, "parts": batch})
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
           "push", "log", "part_add", "part_rm", "pdf", "open_part", "build_library", "resolve_workspace"]
