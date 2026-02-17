import json
import sys
import urllib.error
import urllib.request


def _api_get(server_url, endpoint):
    url = server_url.rstrip("/") + "/" + endpoint.lstrip("/")
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "KiCadSync/1.0")
    req.add_header("Accept", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code} fetching {url}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  Error fetching {url}: {e}", file=sys.stderr)
        return None


def fetch_components(server_url, page_limit=100):
    all_components = []
    page = 1
    while True:
        data = _api_get(
            server_url,
            f"api/priv_components/?page={page}&limit={page_limit}"
        )
        if not data:
            break

        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("results", [])
        else:
            break

        if not items:
            break

        ready = [c for c in items if c.get("processing_status") == 1]
        all_components.extend(ready)
        print(f"  Page {page}: {len(items)} components ({len(ready)} ready), total: {len(all_components)}")

        if isinstance(data, dict) and data.get("next"):
            page += 1
        else:
            break

    return all_components


def fetch_digikey(server_url, component_uuid):
    return _api_get(
        server_url,
        f"api/priv_components/{component_uuid}/digikey/"
    )
