import json
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "dbsync.json")

DEFAULTS = {
    "api_url": "https://datasheets.md",
    "sync_interval_sec": 300,
    "exclude_fields": [],
    "api_token": "",
    # What to synchronise. The SQLite database (usable by any tool that reads it)
    # and the KiCad library files (symbols + footprints + database-library
    # descriptor) sync by default; the bulk PDF/markdown datasheet mirrors are
    # opt-in. The KiCad library descriptor points at the SQLite database, so
    # sync_kicad implies sync_sqlite (enforced in the GUI + sync engine).
    "sync_sqlite": True,
    "sync_kicad": True,
    "sync_pdf": False,
    "sync_markdown": False,
}

# Carried over from the pre-1.1 password-login flow. Dropped on load + save
# so they never get re-written. The password was being persisted in plaintext.
LEGACY_KEYS = ("user_email", "user_password")


def load_config(path=None):
    path = path or CONFIG_PATH
    config = dict(DEFAULTS)
    if os.path.exists(path):
        with open(path, "r") as f:
            user = json.load(f)
        for k in LEGACY_KEYS:
            user.pop(k, None)
        config.update(user)
    return config


def save_config(config, path=None):
    path = path or CONFIG_PATH
    sanitised = {k: v for k, v in config.items() if k not in LEGACY_KEYS}
    with open(path, "w") as f:
        json.dump(sanitised, f, indent=4)
