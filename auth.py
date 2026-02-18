import requests

API_BASE = "https://datasheets.md"


def login(config):
    email = config.get("user_email", "")
    password = config.get("user_password", "")
    if not email or not password:
        raise RuntimeError("user_email and user_password must be set in dbsync.json")

    api_url = config.get("api_url", API_BASE).rstrip("/")
    url = f"{api_url}/api/auth/login"

    resp = requests.post(url, json={"email": email, "password": password}, timeout=30)

    if resp.status_code == 401:
        raise RuntimeError("Invalid email or password")
    if resp.status_code != 200:
        raise RuntimeError(f"Login failed (HTTP {resp.status_code})")

    data = resp.json()
    token = data.get("access")
    if not token:
        raise RuntimeError("No access token in login response")

    return token
