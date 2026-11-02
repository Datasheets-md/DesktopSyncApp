"""`dsmd` -- a git-like working copy of a datasheets.md workspace.

Exit codes: 0 ok; 1 error; 2 completed, but with conflicts or stale/rejected parts.
"""
import argparse
import json
import os
import sys
from importlib import resources

from . import __version__, auth, sync
from .http import ApiError, Client
from .repo import Repo, RepoError

EXIT_OK, EXIT_ERROR, EXIT_PARTIAL = 0, 1, 2


class Output:
    def __init__(self, as_json=False, quiet=False):
        self.as_json, self.quiet = as_json, quiet

    def say(self, *lines):
        """Progress: stderr, so --json stdout stays one document."""
        if not self.quiet:
            for line in lines:
                print(line, file=sys.stderr)

    def result(self, data, text=None):
        if self.as_json:
            print(json.dumps(data, indent=2, default=str))
        elif text and not self.quiet:
            print(text)


def _client(server=None, workspace_id=None):
    server = auth.default_server(server)
    return Client(server, auth.Credentials(server), workspace_id)


def _repo_client(args):
    repo = Repo.find(args.C or ".")
    return repo, _client(repo.state["server"], repo.state["workspace_id"])


# ----- commands ----------------------------------------------------------------------

def cmd_login(args, out):
    server = auth.default_server(args.server)
    done = auth.login(server, open_browser=not args.no_browser, say=out.say)
    me = _client(server).get("/users/me/", workspace=False)
    out.result({**done, "email": me.get("email")}, f"Signed in to {server} as {me.get('email')}")


def cmd_logout(args, out):
    server = auth.default_server(args.server)
    had = auth.logout(server)
    out.result({"server": server, "signed_out": had}, f"Signed out of {server}" if had else f"Not signed in to {server}")


def cmd_whoami(args, out):
    client = _client(args.server)
    me = client.get("/users/me/", workspace=False)
    via = "DSMD_TOKEN" if os.environ.get("DSMD_TOKEN") else "dsmd login"
    out.result({"server": client.server, "email": me.get("email"), "username": me.get("username"), "via": via},
               f"{me.get('email')} on {client.server} (via {via})")


def cmd_workspaces(args, out):
    rows = _client(args.server).get("/workspaces/", workspace=False)
    text = "\n".join(f"{w['id']:>6}  {w.get('name')}{'  (default)' if w.get('is_default') else ''}  [{w.get('user_role')}]"
                     for w in rows)
    out.result(rows, text or "No workspaces.")


def cmd_clone(args, out):
    client = _client(args.server)
    parts = [p.strip() for p in args.parts.split(",") if p.strip()] if args.parts else None
    directory = args.dir or str(args.workspace).replace(" ", "-").lower()
    done = sync.clone(client, args.workspace, os.path.join(args.C or ".", directory), parts=parts, say=out.say)
    out.result(done, f"Cloned {done['parts']} parts into {done['root']}")


def cmd_fetch(args, out):
    repo, client = _repo_client(args)
    with repo.lock():
        got = sync.fetch(repo, client, say=out.say)
    out.result(got, (f"{got['changed_files']} files changed on the server ({got['new_parts']} new parts, "
                     f"{got['removed_parts']} removed, {got['moved_parts']} moved); run dsmd pull to merge them"))


def cmd_pull(args, out):
    repo, client = _repo_client(args)
    with repo.lock():
        report = sync.pull(repo, client, rebuild_library=args.library, say=out.say)
    lines = [f"Pulled: {report['changed_files']} files, {report['new_parts']} new parts, "
             f"{report['removed_parts']} removed, {report['moved_parts']} moved"]
    lines += [f"merged    {p}" for p in report["merged"]]
    lines += [f"CONFLICT  {c['path']} ({c['conflicts']} rows)" for c in report["conflicts"]]
    lines += [f"saved     {p} (your edit of a read-only file)" for p in report["saved_local"]]
    lines += [f"warning   {w}" for w in report["warnings"]]
    out.result(report, "\n".join(lines))
    return EXIT_PARTIAL if report["conflicts"] else EXIT_OK


def cmd_status(args, out):
    repo = Repo.find(args.C or ".")
    report = sync.status(repo)
    lines = [f"Workspace {report['workspace']['name']} ({report['workspace']['id']}) on {report['server']}"]
    for part in report["parts"]:
        lines.append(f"{part['path']}/")
        lines += [f"    {f['state']:<18} {f['name']}" for f in part["files"]]
    lines += [f"{s['state']:<22} {s['path']}" for s in report["settings"]]
    lines += [f"generated, will be overwritten: {p}" for p in report["generated_edited"]]
    lines += [f"untracked, ignored: {p}/" for p in report["untracked"]]
    if report["clean"]:
        lines.append("Nothing to push; working copy clean.")
    out.result(report, "\n".join(lines))


def cmd_diff(args, out):
    repo = Repo.find(args.C or ".")
    text = sync.diff(repo, args.target)
    out.result({"diff": text}, text.rstrip("\n") if text else "No local changes.")


def cmd_restore(args, out):
    repo = Repo.find(args.C or ".")
    with repo.lock():
        done = sync.restore(repo, args.target)
    out.result({"restored": done}, "\n".join(f"restored {p}" for p in done) or "Nothing to restore.")


def _where(path, error):
    """`parts/X/Y/pins.csv:3` for a part's file, `settings/mapping.yaml:3` for a settings file."""
    name = error.get("file")
    where = path if not name or name == path else f"{path}/{name}"
    return where + (f":{error['line']}" if error.get("line") else "")


def cmd_push(args, out):
    repo, client = _repo_client(args)
    with repo.lock():
        result = sync.push(repo, client, args.message, say=out.say)
    lines = [f"applied   {r['path']}" for r in result["applied"]]
    lines += [f"stale     {r['path']}: changed on the server; run dsmd pull, then push again" for r in result["stale"]]
    for r in result["rejected"]:
        lines += [f"rejected  {_where(r['path'], e)}: {e['error']}" for e in r["errors"]] or [f"rejected  {r['path']}"]
    out.result(result, "\n".join(lines) or "Nothing to push.")
    return EXIT_PARTIAL if result["stale"] or result["rejected"] else EXIT_OK


def cmd_log(args, out):
    repo, client = _repo_client(args)
    data = sync.log(repo, client, args.part, args.page)
    lines = []
    for e in data["entries"]:
        counts = ", ".join(f"{k} {v}" if not isinstance(v, dict) else
                           f"{k}: " + " ".join(f"{op} {n}" for op, n in v.items() if n)
                           for k, v in (e.get("summary") or {}).items())
        lines.append(f"{e['at'][:19]}  {e.get('user') or '?'}  {e.get('part_number') or e['part_uuid']}  "
                     f"{e.get('message') or ''}\n    {counts}")
    out.result(data, "\n".join(lines) or "No pushes yet.")


def cmd_part(args, out):
    repo, client = _repo_client(args)
    with repo.lock():
        if args.part_cmd == "add":
            done = sync.part_add(repo, client, args.part_number, args.manufacturer, say=out.say)
            out.result(done, f"Added {args.part_number}")
            return
        uuid = repo.resolve(args.ident)
        path = repo.parts()[uuid]["path"]
        if not args.yes:
            if not sys.stdin.isatty():
                raise RepoError("refusing to remove without confirmation; pass --yes")
            if input(f"Remove {path} from the workspace on the server? [y/N] ").strip().lower() != "y":
                out.result({"removed": False}, "Cancelled.")
                return
        done = sync.part_rm(repo, client, uuid, say=out.say)
        out.result(done, f"Removed {path}")


def cmd_pdf(args, out):
    repo, client = _repo_client(args)
    path = sync.pdf(repo, client, args.part)
    out.result({"path": path}, path)


def cmd_open(args, out):
    repo = Repo.find(args.C or ".")
    url = sync.open_part(repo, args.part)
    out.result({"url": url}, url)


def cmd_categories(args, out):
    _, client = _repo_client(args)
    rows = sync.categories(client)
    names = {r["id"]: r["name"] for r in rows}
    out.result(rows, "\n".join(f"{r['id']}  {r['name']}"
                                + (f"  (under {names.get(r['parent_id'], '?')})" if r["parent_id"] else "")
                                + (f"  [{r['parts']} parts]" if r["parts"] else "") for r in rows))


def cmd_feedback(args, out):
    try:
        _, client = _repo_client(args)
    except RepoError:                       # outside a working copy: no workspace, still fine
        client = _client(args.server)
    sync.feedback(client, " ".join(args.message), args.kind)
    out.result({"sent": True}, "Sent. Thank you -- the datasheets.md team reads every note.")


def cmd_gui(args, out):
    try:
        from .gui.app import main as gui_main
    except ImportError as exc:
        raise RepoError(f'the GUI needs PySide6: pip install datasheets-md-gui ({exc})') from None
    return gui_main(["--foreground"] if args.foreground else [])


def cmd_help(args, out):
    if args.topic != "agents":
        raise RepoError("unknown help topic; try: dsmd help agents")
    text = resources.files("datasheets_md").joinpath("agents.md").read_text(encoding="utf-8")
    out.result({"agents": text}, text.rstrip("\n"))


# ----- parser ------------------------------------------------------------------------------

def _globals(suppress):
    """The global flags. Subcommands accept them too, with SUPPRESS defaults so
    `dsmd --json status` is not reset by the subparser's own default."""
    kw = {"default": argparse.SUPPRESS} if suppress else {}
    flags = argparse.ArgumentParser(add_help=False)
    flags.add_argument("--json", action="store_true", help="machine-readable output on stdout", **kw)
    flags.add_argument("--quiet", "-q", action="store_true", help="no progress output", **kw)
    flags.add_argument("-C", metavar="DIR", help="run as if started in DIR", **kw)
    return flags


def parser():
    common = _globals(suppress=True)
    p = argparse.ArgumentParser(prog="dsmd", parents=[_globals(suppress=False)],
                                description="A git-like working copy of your datasheets.md workspace.",
                                epilog="Settings are files: edit settings/mapping.yaml or settings/symbol_style.yaml "
                                       "(owner/admin), then dsmd push. Guide: dsmd help agents")
    p.add_argument("--version", action="version", version=f"dsmd {__version__}")
    sub = p.add_subparsers(dest="command", required=True, metavar="<command>")

    def add(name, fn, help_text):
        cmd = sub.add_parser(name, parents=[common], help=help_text, description=help_text)
        cmd.set_defaults(fn=fn)
        return cmd

    for name, fn, text in (("login", cmd_login, "sign in through the browser"),
                           ("logout", cmd_logout, "sign out and revoke the stored session"),
                           ("whoami", cmd_whoami, "show who you are signed in as")):
        cmd = add(name, fn, text)
        cmd.add_argument("--server", help=f"API server (default: DSMD_SERVER or {auth.DEFAULT_SERVER})")
        if name == "login":
            cmd.add_argument("--no-browser", action="store_true", help="print the sign-in URL instead of opening it")
    add("workspaces", cmd_workspaces, "list your workspaces").add_argument("--server")
    cmd = add("clone", cmd_clone, "create a working copy of a workspace")
    cmd.add_argument("workspace", help="workspace id or name")
    cmd.add_argument("dir", nargs="?", help="target folder (default: the workspace name)")
    cmd.add_argument("--parts", help="comma-separated part uuids for a sparse clone")
    cmd.add_argument("--server")
    add("fetch", cmd_fetch, "download server changes without merging them")
    add("pull", cmd_pull, "fetch and merge server changes; rebuild library/").add_argument(
        "--library", action="store_true", help="rebuild library/ even when nothing changed")
    add("status", cmd_status, "show local changes")
    add("diff", cmd_diff, "show local changes as a diff").add_argument("target", nargs="?", help="part or path")
    add("restore", cmd_restore, "discard local changes").add_argument("target", nargs="?", help="part or path")
    add("push", cmd_push, "send local changes (parts and settings) to the workspace").add_argument(
        "-m", "--message", required=True, help="what changed and why")
    cmd = add("log", cmd_log, "server-side history of pushes")
    cmd.add_argument("part", nargs="?")
    cmd.add_argument("--page", type=int, default=1)
    part = add("part", cmd_part, "add or remove parts in the workspace")
    part_sub = part.add_subparsers(dest="part_cmd", required=True, metavar="<add|rm>")
    padd = part_sub.add_parser("add", parents=[common], help="add a public catalogue part")
    padd.add_argument("part_number")
    padd.add_argument("--manufacturer")
    prm = part_sub.add_parser("rm", parents=[common], help="remove a part from the workspace")
    prm.add_argument("ident", help="part number, uuid or folder")
    prm.add_argument("--yes", "-y", action="store_true", help="do not ask")
    add("pdf", cmd_pdf, "download a part's PDF into .dsmd/cache and print its path").add_argument("part")
    add("open", cmd_open, "open a part's web page").add_argument("part")
    add("categories", cmd_categories, "list the catalogue categories (the ids settings/mapping.yaml's category_map uses)")
    cmd = add("feedback", cmd_feedback, "tell the datasheets.md team about a bug or a missing setting")
    cmd.add_argument("message", nargs="+")
    cmd.add_argument("--kind", choices=sync.FEEDBACK_KINDS, default="other")
    cmd.add_argument("--server")
    add("gui", cmd_gui, "launch the desktop app in the background (needs datasheets-md-gui)").add_argument(
        "--foreground", action="store_true", help="stay attached to the terminal and print its output")
    add("help", cmd_help, "extra help topics").add_argument("topic", choices=["agents"])
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    out = Output(args.json, args.quiet)
    try:
        code = args.fn(args, out)
    except (ApiError, RepoError, auth.AuthError) as exc:
        if args.json:
            print(json.dumps({"error": str(exc), "status": getattr(exc, "status", None)}))
        else:
            print(f"dsmd: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return EXIT_ERROR
    return code or EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
