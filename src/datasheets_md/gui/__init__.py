"""The dBsync launcher. Imports Qt only inside main: on Windows `dsmd-gui` has no
console, so anything that fails before this point would vanish without a trace."""
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path


def open_log(path):
    """(path, file) for the app's output. A log never stops the window: a locked-down
    profile can refuse the per-user folder, so fall back to the temp folder, then to nothing."""
    for candidate in (Path(path), Path(tempfile.gettempdir()) / "dsmd-gui.log"):
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            return candidate, open(candidate, "ab")
        except OSError:
            continue
    return None, subprocess.DEVNULL


def main():
    try:
        from .app import main as run
        return run()
    except Exception:
        text = traceback.format_exc()
        if sys.stderr:
            print(text, file=sys.stderr)
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, text, "dBsync could not start", 0x10)
        return 1
