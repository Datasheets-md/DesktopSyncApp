"""datasheets-md: the `dsmd` CLI and the dBsync GUI on one sync engine."""
from pathlib import Path

__version__ = "2.4.2"
APP_NAME = "dBsync"


def user_dir():
    """Sign-ins, GUI preferences, the log and the update check, on every platform.
    The home folder: a user can always write there, unlike some AppData\\Local setups."""
    return Path.home() / ".datasheets-md"
