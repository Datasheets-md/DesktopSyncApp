import json
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "dbsync.json")

DEFAULTS = {
    "api_url": "https://datasheets.md",
    "sync_interval_sec": 300,
    "exclude_fields": [],
}

def load_config(path=None):
    path = path or CONFIG_PATH
    config = dict(DEFAULTS)
    if os.path.exists(path):
        with open(path, "r") as f:
            user = json.load(f)
        config.update(user)
    return config

def save_config(config, path=None):
    path = path or CONFIG_PATH
    with open(path, "w") as f:
        json.dump(config, f, indent=4)
