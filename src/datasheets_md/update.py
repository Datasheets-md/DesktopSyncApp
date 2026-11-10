"""Newer releases on PyPI: the once-a-day check behind the notice (after every CLI command, and
the banner in the desktop app) and `dsmd update` (`dsmd self-update` before 2.4.2).

The notice asks the index at most once a day (cached in the user cache dir),
gives up after two seconds and never fails a command. DSMD_NO_UPDATE_CHECK=1
or CI=... turns it off; DSMD_UPDATE_INDEX points both at another index
(https://test.pypi.org for pre-release testing).

`update` upgrades the install it runs from, the way it was made: pipx and
uv tool environments through their own tool (which keeps the original
--pre / index options), a venv or a user install through its own pip. A
Python the OS manages (PEP 668) is refused with the command to use instead.
"""
import importlib
import importlib.util
import json
import os
import shlex
import shutil
import site
import subprocess
import sys
import sysconfig
import time
from importlib import metadata
from pathlib import Path

import requests
from packaging.version import InvalidVersion, Version

from . import __version__, user_dir

PACKAGE = "datasheets-md"
GUI_PACKAGE = "datasheets-md-gui"
PYPI = "https://pypi.org"
CHECK_EVERY = 24 * 3600


def index_url():
    return os.environ.get("DSMD_UPDATE_INDEX", PYPI).rstrip("/")


def latest_version(timeout=2):
    """Newest unyanked release on the index; pre-releases only for a pre-release install."""
    resp = requests.get(f"{index_url()}/pypi/{PACKAGE}/json", timeout=timeout,
                        headers={"User-Agent": f"dsmd/{__version__}"})
    resp.raise_for_status()
    pre = Version(__version__).is_prerelease
    best = None
    for text, files in (resp.json().get("releases") or {}).items():
        try:
            version = Version(text)
        except InvalidVersion:
            continue
        if not files or all(f.get("yanked") for f in files) or (version.is_prerelease and not pre):
            continue
        best = max(best, version) if best else version
    return str(best) if best else None


def newer(latest):
    try:
        return bool(latest) and Version(latest) > Version(__version__)
    except InvalidVersion:
        return False


def _cache_file():
    return user_dir() / "update-check.json"


def available():
    """The newer release, or None. A failed check is cached too, so an
    offline machine pays the two-second timeout once a day, not every command."""
    if os.environ.get("DSMD_NO_UPDATE_CHECK") or os.environ.get("CI"):
        return None
    path = _cache_file()
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cached = {}
    if cached.get("index") != index_url() or time.time() - cached.get("checked_at", 0) > CHECK_EVERY:
        try:
            latest = latest_version()
        except (requests.RequestException, ValueError):
            latest = None
        cached = {"checked_at": time.time(), "index": index_url(), "latest": latest}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(cached), encoding="utf-8")
        except OSError:
            pass
    latest = cached.get("latest")
    return latest if newer(latest) else None


def notice():
    """The 'newer version' line, or None."""
    latest = available()
    return f"dsmd {latest} is available (you have {__version__}); run: dsmd update" if latest else None


def _installed_package():
    """The GUI wrapper when present: it pins datasheets-md, so it is the one to upgrade."""
    try:
        metadata.distribution(GUI_PACKAGE)
        return GUI_PACKAGE
    except metadata.PackageNotFoundError:
        return PACKAGE


def installed_version():
    """What is on disk now, not what this process imported."""
    importlib.invalidate_caches()
    return metadata.version(PACKAGE)


def upgrade_command(version):
    """(argv, None) that upgrades this install to `version`, or (None, why not)."""
    prefix = Path(sys.prefix)
    if (prefix / "pipx_metadata.json").exists():
        pipx = shutil.which("pipx")
        return ([pipx, "upgrade", prefix.name], None) if pipx else (None, f"installed with pipx; run: pipx upgrade {prefix.name}")
    if (prefix / "uv-receipt.toml").exists():
        uv = shutil.which("uv")
        return ([uv, "tool", "upgrade", prefix.name], None) if uv else (None, f"installed with uv; run: uv tool upgrade {prefix.name}")
    package = _installed_package()
    in_venv = sys.prefix != sys.base_prefix
    if not in_venv and (Path(sysconfig.get_path("stdlib")) / "EXTERNALLY-MANAGED").exists():
        return None, (f"this Python is managed by the operating system; reinstall with pipx "
                      f"(pipx install {package}) or into a venv, see the README")
    if importlib.util.find_spec("pip") is None:
        return None, f"no pip in {sys.prefix}; upgrade {package} to {version} with the tool that installed it"
    argv = [sys.executable, "-m", "pip", "install", "--upgrade", f"{package}=={version}"]
    if index_url() != PYPI:
        argv += ["--index-url", f"{index_url()}/simple/", "--extra-index-url", f"{PYPI}/simple/"]
    if not in_venv and Path(__file__).resolve().is_relative_to(Path(site.getusersitepackages()).resolve()):
        argv.append("--user")
    return argv, None


def update_to(latest, say):
    """Upgrade this install to `latest`, for `dsmd update` and the app's Update button.
    Returns {"updated": True} or {"deferred": True} (Windows finishes in a new console once
    this process exits); raises UpdateError with what to do instead."""
    argv, why_not = upgrade_command(latest)
    if argv is None:
        raise UpdateError(why_not)
    say(f"Updating dsmd {__version__} -> {latest}: {shlex.join(argv)}")
    code = run(argv)
    if code:
        raise UpdateError(f"the update failed (exit {code}); the command above shows why")
    if code is None:
        return {"deferred": True}
    installed = installed_version()
    if installed != latest:                 # pipx and uv exit 0 when they find nothing to do
        raise UpdateError(f"dsmd is still {installed} after the update, not {latest}; it was probably installed "
                          "without --pre or from another index, so reinstall it the way you installed it")
    return {"updated": True}


class UpdateError(Exception):
    pass


def run(argv):
    """Exit code of the upgrade, or None when it was handed to a new console:
    Windows will not let the running dsmd.exe be replaced, so it starts after
    this process exits."""
    if os.name == "nt":
        line = subprocess.list2cmdline(argv)
        subprocess.Popen(f'cmd /s /c "timeout /t 2 /nobreak >nul & {line} & pause"',
                         creationflags=subprocess.CREATE_NEW_CONSOLE)
        return None
    return subprocess.call(argv, stdout=sys.stderr)   # keep --json stdout one document
