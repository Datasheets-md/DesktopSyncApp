"""The working copy on disk (Spec 27a 3.3).

    .dsmd/state.json   server, workspace, renderer, per-part paths and file etags
    .dsmd/base/        the pristine copy of every synced file (what status/diff/merge compare to)
    .dsmd/incoming/    what `dsmd fetch` downloaded and `dsmd pull` has not merged yet
    .dsmd/cache/       PDFs (`dsmd pdf`)
    dsmd.toml          what to pull and which library outputs to build
"""
import json
import os
import shutil
from contextlib import contextmanager
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

DOT = ".dsmd"
STATE = "state.json"
CONFIG = "dsmd.toml"
DEFAULT_CONFIG = """# dsmd working-copy settings
[pull]
# Pull each part's digitised datasheet as datasheet.md (read-only).
markdown = true

[library]
# Build library/ (KiCad symbols, footprints, database library) on pull.
kicad = true
sqlite = true
"""
GITIGNORE = ".dsmd/\n"


class RepoError(Exception):
    pass


class Repo:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.dot = self.root / DOT
        self.state = {}

    # ----- discovery ---------------------------------------------------------

    @classmethod
    def find(cls, start="."):
        here = Path(start).resolve()
        for folder in (here, *here.parents):
            if (folder / DOT / STATE).is_file():
                repo = cls(folder)
                repo.load()
                return repo
        raise RepoError("not a dsmd working copy (no .dsmd/ here or above); run dsmd clone first")

    @classmethod
    def create(cls, root, server, workspace):
        root = Path(root)
        if root.exists() and any(root.iterdir()):
            raise RepoError(f"{root} exists and is not empty")
        root.mkdir(parents=True, exist_ok=True)
        repo = cls(root)
        (repo.dot / "base").mkdir(parents=True)
        repo.state = {"server": server, "workspace_id": workspace["id"], "workspace_name": workspace.get("name"),
                      "renderer": None, "sparse": None, "parts": {}, "settings": {}, "library": {}}
        (repo.root / CONFIG).write_text(DEFAULT_CONFIG)
        (repo.root / ".gitignore").write_text(GITIGNORE)
        repo.save()
        return repo

    # ----- state ---------------------------------------------------------------

    def load(self):
        self.state = json.loads((self.dot / STATE).read_text())
        return self.state

    def save(self):
        tmp = self.dot / (STATE + ".tmp")
        tmp.write_text(json.dumps(self.state, indent=2, sort_keys=True))
        os.replace(tmp, self.dot / STATE)

    def config(self):
        try:
            data = tomllib.loads((self.root / CONFIG).read_text())
        except (OSError, tomllib.TOMLDecodeError):
            data = {}
        pull, library = data.get("pull", {}), data.get("library", {})
        return {"markdown": bool(pull.get("markdown", True)),
                "kicad": bool(library.get("kicad", True)), "sqlite": bool(library.get("sqlite", True))}

    def set_config(self, **values):
        current = {**self.config(), **values}
        text = DEFAULT_CONFIG
        for key in ("markdown", "kicad", "sqlite"):
            text = text.replace(f"{key} = true", f"{key} = {'true' if current[key] else 'false'}")
        (self.root / CONFIG).write_text(text)

    @contextmanager
    def lock(self):
        """One dsmd (CLI or GUI) at a time per working copy; a lock left by a dead process is taken over."""
        path = self.dot / "lock"
        for _ in range(2):
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            except FileExistsError:
                try:
                    pid = int(path.read_text().strip() or 0)
                except (OSError, ValueError):
                    pid = 0
                if pid and _alive(pid):
                    raise RepoError(f"another dsmd (pid {pid}) is working in {self.root}") from None
                path.unlink(missing_ok=True)
                continue
            with os.fdopen(fd, "w") as fh:
                fh.write(str(os.getpid()))
            break
        else:
            raise RepoError(f"cannot take {path}")
        try:
            yield self
        finally:
            path.unlink(missing_ok=True)

    # ----- files ---------------------------------------------------------------

    def working(self, rel):
        return self.root / rel

    def base(self, rel):
        return self.dot / "base" / rel

    def incoming(self, key):
        return self.dot / "incoming" / key

    @staticmethod
    def read(path):
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None

    @staticmethod
    def write(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)

    def put(self, rel, text):
        """Write a server version to both the working copy and the base."""
        self.write(self.working(rel), text)
        self.write(self.base(rel), text)

    def remove(self, rel, working=True):
        if working:
            self.working(rel).unlink(missing_ok=True)
        self.base(rel).unlink(missing_ok=True)
        for top in ((self.root, self.dot / "base") if working else (self.dot / "base",)):
            _prune(top / rel, top)

    def move(self, old, new):
        """Move a part folder (working and base) to its new server path; False when the target is taken."""
        if self.working(new).exists():
            return False
        for top in (self.root, self.dot / "base"):
            if (top / old).exists():
                (top / new).parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(top / old), str(top / new))
                _prune(top / old, top)
        return True

    # ----- parts -----------------------------------------------------------------

    def parts(self):
        return self.state.get("parts", {})

    def resolve(self, ident):
        """A part by uuid, uuid prefix, folder path or part number (case-insensitive)."""
        ident = ident.strip().rstrip("/")
        parts = self.parts()
        if ident in parts:
            return ident
        rel = os.path.relpath(Path(ident).resolve(), self.root) if Path(ident).exists() else ident
        hits = [u for u, p in parts.items()
                if u.startswith(ident) or p["path"] == rel or rel.startswith(p["path"] + "/")
                or p["path"].rsplit("/", 1)[-1].lower() == ident.lower()]
        if not hits:
            raise RepoError(f"no part matches {ident!r}")
        if len(hits) > 1:
            raise RepoError(f"{ident!r} matches {len(hits)} parts: " + ", ".join(parts[u]["path"] for u in hits))
        return hits[0]


def _alive(pid):
    if os.name == "nt":
        # os.kill(pid, 0) is CTRL_C_EVENT on Windows; ask the kernel instead.
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _prune(path, top):
    """Remove empty folders from path's parent up to (not including) top."""
    folder = path.parent if not path.is_dir() else path
    while folder != top and top in folder.parents:
        try:
            folder.rmdir()
        except OSError:
            return
        folder = folder.parent
