import requests
from auth import API_BASE

# The public REST API is main-api's own documented surface, versioned under
# /api/v1 (browse it at https://datasheets.md/api/v1/docs/). The separate
# api-service gateway that used to answer at /api-service was retired in
# September 2026; every route it proxied now lives here under its own name.
API_PREFIX = "/api/v1"


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
    if resp.status_code == 403:
        raise RuntimeError(
            "This API token is not allowed on the sync endpoints -- mint a new "
            "one under Integrations -> REST API"
        )
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
        f"{api_root}/workspace/components/",
        headers=headers,
        params={"limit": 1},
        timeout=15,
    )
    _raise_for_status(resp, "workspace")
    return True


def fetch_components(config, with_parameters=True):
    """List the caller's workspace parts, shaped for the sqlite builder.

    Every list row already carries its own `param_data`, so a full workspace
    costs one call per page rather than one call per part. `with_parameters`
    now only decides whether that tree is flattened into parameter columns --
    a PDF/markdown-only sync skips the work, not a fetch.

    Identity and CAD fields are read from the row first and from its
    `metadata` overlay second: the row holds what the workspace part itself
    sets, the overlay what it inherits from its public twin, and a part that
    inherits its symbol still belongs in the synced library."""
    api_root, headers = _base(config)

    components = []
    page = 1
    page_size = 100

    while True:
        resp = requests.get(
            f"{api_root}/workspace/components/",
            headers=headers,
            params={"limit": page_size, "page": page},
            timeout=60,
        )
        _raise_for_status(resp, "workspace")

        # A paginated envelope, not a bare array: the rows are under
        # `components` and `has_next` says whether to ask for another page.
        payload = resp.json() or {}
        items = payload.get("components") or []
        for item in items:
            uuid = str(item.get("uuid", ""))
            all_params = (
                _flatten_param_data(item.get("param_data") or {})
                if with_parameters else []
            )
            # manufacturer and category live only on the overlay; part_number and
            # the CAD refs exist on both and the row wins when it has a value.
            meta = (item.get("metadata") or [{}])[0] or {}
            # Package/Description are promoted to their standard metadata columns;
            # drop them from the parameter list so they don't also appear as their
            # own (duplicate) columns in the SQLite tables.
            promoted = _PACKAGE_KEYS | _DESCRIPTION_KEYS
            parameters = [p for p in all_params if (p.get("key") or "").strip().lower() not in promoted]
            components.append({
                "uuid": uuid,
                "processing_status": 1,
                "metadata": [{
                    "part_number": item.get("part_number") or meta.get("part_number") or "",
                    "description": (item.get("description") or meta.get("description")
                                    or _pick(all_params, _DESCRIPTION_KEYS)),
                    "kicad_symbol": item.get("kicad_symbol") or meta.get("kicad_symbol") or "",
                    "kicad_footprint": item.get("kicad_footprint") or meta.get("kicad_footprint") or "",
                    "manufacturer": meta.get("manufacturer") or "",
                    "package": _pick(all_params, _PACKAGE_KEYS) or meta.get("package") or "",
                    "category": meta.get("category") or "",
                }],
                "parameters": parameters,
            })

        if not payload.get("has_next"):
            break
        page += 1

    print(f"  Fetched {len(components)} components from API")
    return components


def fetch_cad_export(config):
    """Fetch generated symbols + standard footprints for every part in the
    workspace. Returns {"symbol_lib": str, "parts": [{part_number, manufacturer,
    symbol_name, kicad_sym, footprint_ref, kicad_mod}, ...]}. Parts without a
    generated symbol are omitted by the server."""
    api_root, headers = _base(config)
    resp = requests.get(
        f"{api_root}/workspace/components/cad-export/",
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
        f"{api_root}/workspace/components/{uuid}/download-pdf/",
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
        f"{api_root}/workspace/components/{uuid}/download-markdown/",
        headers=headers,
        params={"clean": "1"},
        timeout=60,
    )
    if resp.status_code == 404:
        return None
    _raise_for_status(resp, "markdown")
    return resp.text


def fetch_markdown_bundle(config, uuid):
    """Zip holding the digitised markdown plus an images/ folder of its figures,
    which the .md links by relative path. Same route as `fetch_markdown`, asked
    for as a zip. None when the part has no markdown available (a 404)."""
    api_root, headers = _base(config)
    resp = requests.get(
        f"{api_root}/workspace/components/{uuid}/download-markdown/",
        headers=headers,
        params={"images": "zip"},
        timeout=120,
    )
    if resp.status_code == 404:
        return None
    _raise_for_status(resp, "markdown bundle")
    return resp.content
