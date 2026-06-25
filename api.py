import requests
from auth import API_BASE


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_connection(config):
    token = (config.get("api_token") or "").strip()
    if not token:
        raise RuntimeError("Paste your API token first")
    api_url = config.get("api_url", API_BASE).rstrip("/")
    resp = requests.get(
        f"{api_url}/api/priv_components",
        headers=_headers(token),
        params={"page": 1, "limit": 1},
        timeout=15,
    )
    if resp.status_code == 401:
        raise RuntimeError("Invalid or revoked API token")
    if resp.status_code != 200:
        raise RuntimeError(f"API error (HTTP {resp.status_code})")
    return True


def fetch_components(config):
    token = (config.get("api_token") or "").strip()
    if not token:
        raise RuntimeError("Paste your API token first")
    api_url = config.get("api_url", API_BASE).rstrip("/")

    components = []
    page = 1

    while True:
        resp = requests.get(
            f"{api_url}/api/priv_components",
            headers=_headers(token),
            params={"page": page, "limit": 100},
            timeout=60,
        )

        if resp.status_code == 401:
            raise RuntimeError("Invalid or revoked API token")
        if resp.status_code != 200:
            raise RuntimeError(f"API error (HTTP {resp.status_code})")

        data = resp.json()
        raw_components = data.get("components", [])

        for raw in raw_components:
            if raw.get("param_extraction_status") != 2:
                continue

            metadata = raw.get("metadata") or []
            meta = metadata[0] if metadata else {}

            comp = {
                "uuid": str(raw.get("uuid", "")),
                "digikey_status": raw.get("digikey_status"),
                "processing_status": 1,
                "metadata": [{
                    "part_number": meta.get("part_number") or "",
                    "description": meta.get("description") or "",
                    "kicad_symbol": meta.get("kicad_symbol") or "",
                    "kicad_footprint": meta.get("kicad_footprint") or "",
                    "manufacturer": meta.get("manufacturer") or "",
                    "package": meta.get("package") or "",
                    "category": meta.get("category") or "",
                }],
                "parameters": [],
            }

            for p in raw.get("parameters") or []:
                comp["parameters"].append({
                    "key": p.get("key") or "",
                    "value": p.get("value") or "",
                    "unit": p.get("unit") or "",
                })

            components.append(comp)

        if not data.get("has_next", False):
            break
        page += 1

    print(f"  Fetched {len(components)} components from API")
    return components


def fetch_cad_export(config):
    """Fetch generated symbols + standard footprints for every part in scope.

    Returns {"symbol_lib": str, "parts": [{part_number, manufacturer, kicad_sym,
    footprint_ref, kicad_mod}, ...]}. Parts without a generated symbol are
    omitted by the server.
    """
    token = (config.get("api_token") or "").strip()
    if not token:
        raise RuntimeError("Paste your API token first")
    api_url = config.get("api_url", API_BASE).rstrip("/")
    resp = requests.get(
        f"{api_url}/api/priv_components/cad-export/",
        headers=_headers(token),
        timeout=120,
    )
    if resp.status_code == 401:
        raise RuntimeError("Invalid or revoked API token")
    if resp.status_code != 200:
        raise RuntimeError(f"API error (HTTP {resp.status_code})")
    return resp.json()
