"""Sign-in for dsmd and the GUI (Spec 27a 3.2).

Browser OAuth against main-api's authorization server: dynamic client
registration once per server, PKCE S256, and a loopback redirect on a free
port. Credentials live in the user config dir (mode 0600), keyed by server.
`DSMD_TOKEN` (a `dsh_` key) overrides them for headless use, `DSMD_SERVER`
the server.
"""
import base64
import hashlib
import http.server
import json
import os
import secrets
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path

import platformdirs
import requests

DEFAULT_SERVER = "https://api.datasheets.md"
CLIENT_NAME = "dsmd"
REDIRECT_PATH = "/callback"
LOGIN_TIMEOUT = 300
# Refresh this long before the access token expires.
EXPIRY_MARGIN = 60


class AuthError(Exception):
    pass


def credentials_path():
    return Path(platformdirs.user_config_dir("datasheets-md")) / "credentials.json"


def _load():
    path = credentials_path()
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {"servers": {}}


def _save(data):
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, path)


def default_server(explicit=None):
    """--server, else DSMD_SERVER, else the server last signed in to, else production."""
    return (explicit or os.environ.get("DSMD_SERVER") or _load().get("default_server") or DEFAULT_SERVER).rstrip("/")


def metadata(server):
    resp = requests.get(f"{server}/.well-known/oauth-authorization-server", timeout=30)
    if resp.status_code != 200:
        raise AuthError(f"{server} does not offer sign-in (HTTP {resp.status_code})")
    return resp.json()


class Credentials:
    """The bearer for one server: DSMD_TOKEN when set, else the stored OAuth tokens."""

    def __init__(self, server):
        self.server = server.rstrip("/")
        self.env_token = os.environ.get("DSMD_TOKEN") or None

    def stored(self):
        return _load().get("servers", {}).get(self.server)

    def token(self):
        if self.env_token:
            return self.env_token
        entry = self.stored()
        if not entry:
            raise AuthError(f"not signed in to {self.server}; run dsmd login --server {self.server}")
        if entry.get("expires_at", 0) - EXPIRY_MARGIN < time.time():
            self.refresh()
            entry = self.stored() or entry
        return entry["access_token"]

    def refresh(self):
        """Renew the access token with the refresh token; False when there is nothing to renew with."""
        if self.env_token:
            return False
        data = _load()
        entry = data.get("servers", {}).get(self.server)
        if not entry or not entry.get("refresh_token"):
            return False
        resp = requests.post(entry["token_endpoint"], data={
            "grant_type": "refresh_token", "refresh_token": entry["refresh_token"],
            "client_id": entry["client_id"]}, timeout=30)
        if resp.status_code != 200:
            return False
        _store_tokens(data, self.server, entry, resp.json())
        return True


def _store_tokens(data, server, entry, tokens):
    entry.update(access_token=tokens["access_token"],
                 refresh_token=tokens.get("refresh_token") or entry.get("refresh_token"),
                 expires_at=int(time.time()) + int(tokens.get("expires_in") or 3600))
    data.setdefault("servers", {})[server] = entry
    data["default_server"] = server
    _save(data)


def _pkce():
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


class _Callback(http.server.BaseHTTPRequestHandler):
    result = None

    def do_GET(self):  # noqa: N802 -- http.server's name
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != REDIRECT_PATH:
            self.send_response(404)
            self.end_headers()
            return
        type(self).result = dict(urllib.parse.parse_qsl(parsed.query))
        body = b"<html><body><h3>dsmd is signed in.</h3>You can close this window.</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def login(server, *, open_browser=True, say=print):
    """The browser flow. Returns the stored entry (without tokens)."""
    server = server.rstrip("/")
    meta = metadata(server)
    data = _load()
    entry = dict(data.get("servers", {}).get(server) or {})
    if not entry.get("client_id"):
        resp = requests.post(meta["registration_endpoint"], json={
            "client_name": CLIENT_NAME, "redirect_uris": [f"http://127.0.0.1{REDIRECT_PATH}"],
            "grant_types": ["authorization_code", "refresh_token"], "token_endpoint_auth_method": "none",
        }, timeout=30)
        if resp.status_code not in (200, 201):
            raise AuthError(f"client registration failed (HTTP {resp.status_code}): {resp.text[:200]}")
        entry["client_id"] = resp.json()["client_id"]
    entry.update(token_endpoint=meta["token_endpoint"], revocation_endpoint=meta.get("revocation_endpoint"))

    handler = type("Callback", (_Callback,), {"result": None})
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    redirect_uri = f"http://127.0.0.1:{httpd.server_port}{REDIRECT_PATH}"
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(16)
    url = meta["authorization_endpoint"] + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": entry["client_id"], "redirect_uri": redirect_uri,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": state})
    say(f"Opening the browser to sign in. If it does not open, visit:\n  {url}")
    if open_browser:
        webbrowser.open(url)
    thread = threading.Thread(target=_serve_until_called, args=(httpd, handler), daemon=True)
    thread.start()
    thread.join(LOGIN_TIMEOUT)
    httpd.server_close()
    result = handler.result
    if not result:
        raise AuthError("timed out waiting for the browser sign-in")
    if result.get("state") != state:
        raise AuthError("sign-in answered with the wrong state; try again")
    if "code" not in result:
        raise AuthError(f"sign-in refused: {result.get('error', 'no code returned')}")
    resp = requests.post(meta["token_endpoint"], data={
        "grant_type": "authorization_code", "code": result["code"], "redirect_uri": redirect_uri,
        "client_id": entry["client_id"], "code_verifier": verifier}, timeout=30)
    if resp.status_code != 200:
        raise AuthError(f"token exchange failed (HTTP {resp.status_code}): {resp.text[:200]}")
    _store_tokens(data, server, entry, resp.json())
    return {"server": server, "client_id": entry["client_id"]}


def _serve_until_called(httpd, handler):
    deadline = time.time() + LOGIN_TIMEOUT
    httpd.timeout = 1
    while handler.result is None and time.time() < deadline:
        httpd.handle_request()


def logout(server):
    """Revoke the refresh token and forget the server's credentials. True when there were any."""
    server = server.rstrip("/")
    data = _load()
    entry = data.get("servers", {}).pop(server, None)
    if entry is None:
        return False
    if entry.get("revocation_endpoint") and entry.get("refresh_token"):
        try:
            requests.post(entry["revocation_endpoint"], data={"token": entry["refresh_token"]}, timeout=15)
        except requests.RequestException:
            pass
    if data.get("default_server") == server:
        data.pop("default_server")
    if data.get("servers"):
        _save(data)
    else:
        credentials_path().unlink(missing_ok=True)
    return True
