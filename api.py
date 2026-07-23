import requests
from auth import API_BASE

# The public REST API is mounted under /api-service on the datasheets.md domain
# (nginx strips the prefix before proxying to the api-service container). The app
# talks to this public API -- NOT main-api's internal routes -- so the open-source
# client never hard-codes internal backend paths.
API_PREFIX = "/api-service"


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _base(config) -> tuple[str, dict]:
    """(api_root, auth_headers) for the configured server + token. Raises when the
    token is missing."""
    token = (config.get("api_token") or "").strip()
    if not token:
        raise RuntimeError("Paste your API token first")
    api_url = config.get("api_url", API_BASE).rstrip("/")
    return f"{api_url}{API_PREFIX}", _headers(token)


def _raise_for_status(resp, what: str):
    if resp.status_code == 401:
        raise RuntimeError("Invalid or revoked API token")
    if resp.status_code != 200:
        raise RuntimeError(f"API error fetching {what} (HTTP {resp.status_code})")


# Sections in the unified datasheet that don't hold user-facing parameter rows.
_NON_PARAM_SECTIONS = {"header"}
# param_data keys we surface as the standard Package/Description columns rather
# than as their own parameter columns.
_PACKAGE_KEYS = {"package", "package/case", "case", "package / case"}
_DESCRIPTION_KEYS = {"description"}


def _row_value(row: dict) -> str:
    """Render a unified-datasheet row to a single value string. Prefers an
    explicit value, else typ/max/min, appending the unit when present."""
    value = row.get("value") or row.get("typ") or row.get("max") or row.get("min")
    if value in (None, ""):
        return ""
    unit = row.get("unit")
    return f"{value} {unit}".strip() if unit else str(value)


def _flatten_param_data(param_data: dict) -> list:
    """Flatten the unified param_data tree into the flat [{key, value, unit}]
    list the sqlite builder consumes. Skips header/internal sections and the
    overflow shadow rows (LLM duplicates of canonical rows)."""
    out = []
    for section_key, rows in (param_data or {}).items():
        if section_key.startswith("$") or section_key.startswith("_"):
            continue
        if section_key in _NON_PARAM_SECTIONS or not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict) or row.get("_status") == "overflow":
                continue
            name = row.get("name")
            if not name:
                continue
            out.append({"key": name, "value": _row_value(row), "unit": ""})
    return out


def _pick(params: list, keys: set) -> str:
    """First value among params whose (lower-cased) key is in `keys`."""
    for p in params:
        if (p.get("key") or "").strip().lower() in keys:
            return p.get("value") or ""
    return ""


def test_connection(config):
    api_root, headers = _base(config)
    resp = requests.get(
        f"{api_root}/api/workspace/components",
        headers=headers,
        params={"limit": 1},
        timeout=15,
    )
    _raise_for_status(resp, "workspace")
    return True


def _fetch_parameters(api_root, headers, uuid):
    """The unified datasheet (param_data tree) for one workspace part. Best-effort:
    a per-part failure yields no parameters rather than aborting the whole sync."""
    resp = requests.get(
        f"{api_root}/api/workspace/components/{uuid}/parameters",
        headers=headers,
        timeout=60,
    )
    if resp.status_code != 200:
        return {}
    return (resp.json() or {}).get("param_data") or {}


def fetch_components(config, with_parameters=True):
    """List the caller's workspace parts, shaped for the sqlite builder.

    When `with_parameters` is False the per-part parameter fetch is skipped
    (the N+1 call the sqlite/KiCad libraries need but a PDF/markdown-only sync
    does not) -- each part still carries its identity + CAD refs."""
    api_root, headers = _base(config)

    components = []
    offset = 0
    page_size = 100

    while True:
        resp = requests.get(
            f"{api_root}/api/workspace/components",
            headers=headers,
            params={"limit": page_size, "offset": offset},
            timeout=60,
        )
        _raise_for_status(resp, "workspace")

        items = resp.json() or []
        for item in items:
            uuid = str(item.get("uuid", ""))
            all_params = (
                _flatten_param_data(_fetch_parameters(api_root, headers, uuid))
                if with_parameters and uuid else []
            )
            # Package/Description are promoted to their standard metadata columns;
            # drop them from the parameter list so they don't also appear as their
            # own (duplicate) columns in the SQLite tables.
            promoted = _PACKAGE_KEYS | _DESCRIPTION_KEYS
            parameters = [p for p in all_params if (p.get("key") or "").strip().lower() not in promoted]
            components.append({
                "uuid": uuid,
                "processing_status": 1,
                "metadata": [{
                    "part_number": item.get("part_number") or "",
                    "description": _pick(all_params, _DESCRIPTION_KEYS),
                    "kicad_symbol": item.get("kicad_symbol") or "",
                    "kicad_footprint": item.get("kicad_footprint") or "",
                    "manufacturer": item.get("manufacturer") or "",
                    "package": _pick(all_params, _PACKAGE_KEYS),
                    "category": item.get("category") or "",
                }],
                "parameters": parameters,
            })

        if len(items) < page_size:
            break
        offset += page_size

    print(f"  Fetched {len(components)} components from API")
    return components


def fetch_cad_export(config):
    """Fetch generated symbols + standard footprints for every part in the
    workspace. Returns {"symbol_lib": str, "parts": [{part_number, manufacturer,
    symbol_name, kicad_sym, footprint_ref, kicad_mod}, ...]}. Parts without a
    generated symbol are omitted by the server."""
    api_root, headers = _base(config)
    resp = requests.get(
        f"{api_root}/api/workspace/cad-export",
        headers=headers,
        timeout=120,
    )
    _raise_for_status(resp, "CAD export")
    return resp.json()


def fetch_pdf(config, uuid):
    """Original PDF datasheet bytes for a workspace part, or None when the part
    has no PDF available (a 404 from the server)."""
    api_root, headers = _base(config)
    resp = requests.get(
        f"{api_root}/api/workspace/components/{uuid}/pdf",
        headers=headers,
        timeout=120,
    )
    if resp.status_code == 404:
        return None
    _raise_for_status(resp, "PDF")
    return resp.content


def fetch_markdown(config, uuid):
    """Digitised markdown datasheet for a workspace part, or None when the part
    has no markdown available (a 404 from the server)."""
    api_root, headers = _base(config)
    resp = requests.get(
        f"{api_root}/api/workspace/components/{uuid}/markdown",
        headers=headers,
        timeout=60,
    )
    if resp.status_code == 404:
        return None
    _raise_for_status(resp, "markdown")
    return resp.text
