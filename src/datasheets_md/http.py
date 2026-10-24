"""HTTP session to the datasheets.md API: base URL, bearer token, workspace
header, retry with backoff, and one error type for every failure."""
import time

import requests

from . import __version__

API_PREFIX = "/api/v1"
RETRY_STATUSES = (429, 502, 503, 504)
RETRIES = 3


class ApiError(Exception):
    """A failed call. `status` is the HTTP status (0 when the server was not
    reached), `code` the server's machine code when it sent one."""

    def __init__(self, message, status=0, code=None):
        super().__init__(message)
        self.status, self.code = status, code


def _describe(resp):
    try:
        body = resp.json()
    except ValueError:
        body = None
    code = message = None
    if isinstance(body, dict):
        code = body.get("error") if isinstance(body.get("error"), str) else None
        message = body.get("message") or body.get("detail") or body.get("error_description")
    if resp.status_code == 401:
        message = message or "not signed in, or the session expired; run dsmd login"
    elif resp.status_code == 403 and code == "api_key_not_allowed":
        message = "this API key may not call this endpoint; sign in with dsmd login instead"
    elif resp.status_code == 404:
        message = message or "not found"
    return ApiError(f"HTTP {resp.status_code}: {message or code or resp.reason}", resp.status_code, code)


class Client:
    """One server, one credential, optionally one workspace.

    `auth` is an `auth.Credentials`: `.token()` gives the bearer, `.refresh()`
    tries to renew it after a 401 and says whether it did."""

    def __init__(self, server, auth, workspace_id=None, session=None):
        self.server = server.rstrip("/")
        self.auth = auth
        self.workspace_id = workspace_id
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = f"dsmd/{__version__}"

    def request(self, method, path, *, json=None, params=None, workspace=True, timeout=120, raw=False):
        url = path if path.startswith("http") else f"{self.server}{API_PREFIX}{path}"
        refreshed = False
        for attempt in range(RETRIES + 1):
            headers = {"Authorization": f"Bearer {self.auth.token()}"}
            if workspace and self.workspace_id is not None:
                headers["X-Workspace-Id"] = str(self.workspace_id)
            try:
                resp = self.session.request(method, url, json=json, params=params, headers=headers, timeout=timeout)
            except requests.RequestException as exc:
                if attempt == RETRIES:
                    raise ApiError(f"cannot reach {self.server}: {exc}") from None
                time.sleep(2 ** attempt)
                continue
            if resp.status_code == 401 and not refreshed and self.auth.refresh():
                refreshed = True
                continue
            if resp.status_code in RETRY_STATUSES and attempt < RETRIES:
                delay = resp.headers.get("Retry-After")
                time.sleep(min(float(delay), 30) if delay and delay.isdigit() else 2 ** attempt)
                continue
            if resp.status_code >= 400:
                raise _describe(resp)
            return resp if raw else (resp.json() if resp.content else None)
        raise ApiError(f"giving up on {method} {path}")

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, json=body, **kw)

    def delete(self, path, **kw):
        return self.request("DELETE", path, **kw)
