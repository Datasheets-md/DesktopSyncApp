import json
import os
import stat
import time
import urllib.error
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_STORE = os.path.join(SCRIPT_DIR, ".token_store.json")


def _read_tokens():
    if not os.path.exists(TOKEN_STORE):
        return {}
    with open(TOKEN_STORE, "r") as f:
        return json.load(f)


def _write_tokens(data):
    with open(TOKEN_STORE, "w") as f:
        json.dump(data, f)
    os.chmod(TOKEN_STORE, stat.S_IRUSR | stat.S_IWUSR)


def _parse_jwt_exp(token):
    import base64
    try:
        payload = token.split(".")[1]
        payload += "=" * (4 - len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        return data.get("exp", 0)
    except Exception:
        return 0


def _post_json(url, body):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "KiCadSync/1.0")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def login(server_url, email, password):
    url = server_url.rstrip("/") + "/api/auth/login"
    result = _post_json(url, {"email": email, "password": password})
    access = result.get("access_token", "")
    if not access:
        raise RuntimeError("Login failed: no access token returned")
    _write_tokens({
        "access": access,
        "server_url": server_url,
    })
    return access


def get_valid_token(server_url):
    tokens = _read_tokens()
    access = tokens.get("access", "")
    if not access:
        return None
    exp = _parse_jwt_exp(access)
    if exp and time.time() > (exp - 60):
        return None  # token expired, re-login required
    return access


def logout():
    if os.path.exists(TOKEN_STORE):
        os.remove(TOKEN_STORE)


def is_logged_in():
    tokens = _read_tokens()
    return bool(tokens.get("access"))
