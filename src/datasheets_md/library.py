"""library/: the workspace's KiCad library, regenerated on pull (Spec 27a 3.7).

A port of dBsync 1.4.0's run_sync without the document mirrors: one SQLite
database (a table per category), the KiCad database-library descriptor that
points at it, the merged symbol library, the footprint folder and the project
lib-tables. Inputs are the same two calls as dBsync (`workspace/components/`
and `workspace/components/cad-export/`). Everything here is generated.
"""
import hashlib
import json
import os
import platform
import re
import sqlite3
import unicodedata

LIB = "datasheets"
SQLITE = f"{LIB}.sqlite"
DBL = f"{LIB}.kicad_dbl"
KEY_COLUMN = "IPN"
STANDARD_COLUMNS = ["IPN", "Value", "Description", "Symbols", "Footprints", "Manufacturer", "MPN", "Package",
                    "Category", "Datasheet", "ComponentUrl", "Stock", "Price_USD", "DigiKey_PN"]
_NON_PARAM_SECTIONS = {"header"}
_PACKAGE_KEYS = {"package", "package/case", "case", "package / case"}
_DESCRIPTION_KEYS = {"description"}
_SAFE = re.compile(r"[^A-Za-z0-9_.+-]")


# ----- fetching ----------------------------------------------------------------------

def _row_value(row):
    value = row.get("value") or row.get("typ") or row.get("max") or row.get("min")
    if value in (None, ""):
        return ""
    unit = row.get("unit")
    return f"{value} {unit}".strip() if unit else str(value)


def _flatten(param_data):
    """[{key, value}] from a unified tree; skips meta sections, header and overflow rows."""
    out = []
    for section, rows in (param_data or {}).items():
        if section.startswith(("$", "_")) or section in _NON_PARAM_SECTIONS or not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict) and row.get("_status") != "overflow" and row.get("name"):
                out.append({"key": row["name"], "value": _row_value(row)})
    return out


def _pick(params, keys):
    return next((p["value"] for p in params if p["key"].strip().lower() in keys), "")


def fetch_components(client):
    """Every workspace part shaped for the SQLite builder; one call per 100 parts."""
    components, page = [], 1
    while True:
        payload = client.get("/workspace/components/", params={"limit": 100, "page": page, "include": "param_data"})
        for item in payload.get("components") or []:
            meta = (item.get("metadata") or [{}])[0] or {}
            params = _flatten(item.get("param_data"))
            promoted = _PACKAGE_KEYS | _DESCRIPTION_KEYS
            components.append({
                "part_number": item.get("part_number") or meta.get("part_number") or "",
                "description": item.get("description") or meta.get("description") or _pick(params, _DESCRIPTION_KEYS),
                "kicad_symbol": item.get("kicad_symbol") or meta.get("kicad_symbol") or "",
                "kicad_footprint": item.get("kicad_footprint") or meta.get("kicad_footprint") or "",
                "manufacturer": meta.get("manufacturer") or "",
                "package": _pick(params, _PACKAGE_KEYS) or meta.get("package") or "",
                "category": meta.get("category") or "",
                "parameters": [p for p in params if p["key"].strip().lower() not in promoted],
            })
        if not payload.get("has_next"):
            return components
        page += 1


# ----- SQLite ------------------------------------------------------------------------

def sanitize_column(name):
    name = unicodedata.normalize("NFKC", name).replace('"', "").replace("'", "")
    return re.sub(r"\s+", " ", name).strip()


def sanitize_table(name):
    if "→" in name:
        name = name.split("→")[-1].strip()
    elif "/" in name:
        name = name.split("/")[-1].strip()
    for old, new in (("\\", "_"), (" ", "_"), ("-", "_"), (".", "_"), (",", "_"), ("\n", "_"), ("\t", "_")):
        name = name.replace(old, new)
    for gone in '()[]{}"\'\r':
        name = name.replace(gone, "")
    while "__" in name:
        name = name.replace("__", "_")
    return name.strip("_")


def _dedup(columns):
    seen, out = set(), []
    for column in columns:
        if column.lower() not in seen:
            seen.add(column.lower())
            out.append(column)
    return out


def param_columns(components):
    standard = {c.lower() for c in STANDARD_COLUMNS}
    seen = {}
    for comp in components:
        for p in comp["parameters"]:
            column = sanitize_column(p["key"])
            if column and column.lower() not in standard:
                seen.setdefault(column.lower(), column)
    return sorted(seen.values())


def _row(comp, columns, delivered):
    refs = delivered.get(comp["part_number"]) or {}
    row = {"IPN": comp["part_number"], "Value": comp["part_number"], "Description": comp["description"],
           "Symbols": refs.get("symbol") or comp["kicad_symbol"],
           "Footprints": refs.get("footprint") or comp["kicad_footprint"],
           "Manufacturer": comp["manufacturer"], "MPN": comp["part_number"], "Package": comp["package"],
           "Category": comp["category"]}
    for p in comp["parameters"]:
        column = sanitize_column(p["key"])
        if column in columns and column not in row:
            row[column] = p["value"]
    return row


def write_sqlite(path, components, columns, delivered):
    """Rewrite the database: one table per category keyed by IPN. Returns the table names."""
    tables = {}
    for comp in components:
        if comp["part_number"]:
            name = sanitize_table(comp["category"] or "") or "Uncategorized"
            tables.setdefault(name, []).append(_row(comp, set(columns), delivered))
    all_columns = _dedup(STANDARD_COLUMNS + columns)
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except FileNotFoundError:
            pass
    conn = sqlite3.connect(path)
    try:
        cur = conn.cursor()
        definition = ", ".join(f'"{c}" TEXT' for c in all_columns)
        placeholders = ", ".join("?" for _ in all_columns)
        names = ", ".join(f'"{c}"' for c in all_columns)
        for table, rows in sorted(tables.items()):
            cur.execute(f'CREATE TABLE "{table}" ({definition}, PRIMARY KEY("{KEY_COLUMN}"))')
            cur.executemany(f'INSERT OR REPLACE INTO "{table}" ({names}) VALUES ({placeholders})',
                            [[row.get(c, "") for c in all_columns] for row in rows])
        conn.commit()
    finally:
        conn.close()
    return sorted(tables)


# ----- KiCad database library -----------------------------------------------------------

_COMMON_FIELDS = [
    ("Value", True, True, False), ("Manufacturer", False, True, True), ("MPN", False, True, True),
    ("Package", False, True, True), ("Category", False, True, True), ("Stock", False, True, True),
    ("Price_USD", False, True, True), ("DigiKey_PN", False, True, True), ("Datasheet", False, False, False),
    ("ComponentUrl", False, False, False),
]


def _odbc_connection():
    system = platform.system()
    if system == "Darwin":
        driver = next((p for p in ("/opt/homebrew/lib/libsqlite3odbc.dylib", "/usr/local/lib/libsqlite3odbc.dylib")
                       if os.path.exists(p)), "SQLite3")
        return f"Driver={{{driver}}};Database=${{CWD}}/{SQLITE}"
    if system == "Windows":
        return f"Driver={{SQLite3 ODBC Driver}};Database=${{CWD}}/{SQLITE}"
    return f"Driver={{SQLite3}};Database=${{CWD}}/{SQLITE}"


def dbl_document(tables, columns):
    libraries = []
    for table in tables:
        fields = []
        for column, on_add, in_chooser, show_name in _COMMON_FIELDS:
            entry = {"column": column, "name": column, "visible_on_add": on_add, "visible_in_chooser": in_chooser}
            if show_name:
                entry["show_name"] = True
            fields.append(entry)
        fields += [{"column": c, "name": c.replace("_", " "), "visible_on_add": False,
                    "visible_in_chooser": False, "show_name": True} for c in columns]
        libraries.append({"name": table, "table": table, "key": KEY_COLUMN, "symbols": "Symbols",
                          "footprints": "Footprints", "fields": fields, "properties": {"description": "Description"}})
    return {"meta": {"version": 0}, "name": "datasheets.md library",
            "description": "Components synced from your datasheets.md workspace",
            "source": {"type": "odbc", "dsn": "", "username": "", "password": "", "timeout_seconds": 2,
                       "connection_string": _odbc_connection()},
            "globally_unique_keys": True, "libraries": libraries}


# ----- symbols and footprints -------------------------------------------------------------

def _symbol_blocks(text):
    """Each top-level `(symbol "..." ...)` s-expression, string-literal aware."""
    i, n = 0, len(text)
    while True:
        start = text.find("(symbol ", i)
        if start == -1:
            return
        depth, in_str, escaped, end = 0, False, False, None
        for j in range(start, n):
            ch = text[j]
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    end = j + 1
                    break
        if end is None:
            return
        yield text[start:end]
        i = end


_SYMBOL_NAME = re.compile(r'\(symbol\s+"((?:[^"\\]|\\.)*)"')


def merge_symbols(parts):
    """One KiCad symbol library; a glyph shared by many parts (and its extends chain) is written once."""
    blocks, seen = [], set()
    for part in parts:
        for block in _symbol_blocks(part.get("kicad_sym") or ""):
            match = _SYMBOL_NAME.match(block)
            if match and match.group(1) not in seen:
                seen.add(match.group(1))
                blocks.append("  " + block)
    return ('(kicad_symbol_lib\n  (version 20241209)\n  (generator "datasheets.md")\n'
            '  (generator_version "9.0")\n' + "\n".join(blocks) + "\n)\n")


def _footprint_name(ref):
    return _SAFE.sub("_", ref.split(":", 1)[1]) if ref and ":" in ref else None


def ref_map(parts):
    """part_number -> {symbol, footprint} as `datasheets:<name>` refs, for the delivered parts."""
    out = {}
    for part in parts:
        if not part.get("part_number"):
            continue
        entry = {}
        if part.get("symbol_name"):
            entry["symbol"] = f"{LIB}:{part['symbol_name']}"
        footprint = _footprint_name(part.get("footprint_ref") or "")
        if footprint and part.get("kicad_mod"):
            entry["footprint"] = f"{LIB}:{footprint}"
        out[part["part_number"]] = entry
    return out


def _lib_tables():
    sym = ('(sym_lib_table\n  (version 7)\n'
           f'  (lib (name "{LIB}")(type "KiCad")(uri "${{KIPRJMOD}}/{LIB}.kicad_sym")(options "")'
           '(descr "datasheets.md generated symbols"))\n)\n')
    fp = ('(fp_lib_table\n  (version 7)\n'
          f'  (lib (name "{LIB}")(type "KiCad")(uri "${{KIPRJMOD}}/{LIB}.pretty")(options "")'
          '(descr "datasheets.md footprints"))\n)\n')
    return {"sym-lib-table": sym, "fp-lib-table": fp}


# ----- build ---------------------------------------------------------------------------------

def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build(client, out_dir, *, kicad=True, sqlite=True, say=print):
    """Regenerate out_dir. Returns {"files": {relpath: sha256}, "components", "tables", "symbols", "footprints"}
    -- the hashes of the text outputs, so `status` can tell when one was edited by hand."""
    if not (kicad or sqlite):
        return {"files": {}, "components": 0, "tables": 0, "symbols": 0, "footprints": 0}
    os.makedirs(out_dir, exist_ok=True)
    components = fetch_components(client)
    export = client.get("/workspace/components/cad-export/") if kicad else {}
    parts = (export or {}).get("parts") or []
    delivered = ref_map(parts) if kicad else {}
    columns = param_columns(components)
    tables = write_sqlite(os.path.join(out_dir, SQLITE), components, columns, delivered)
    hashes, symbols, footprints = {}, 0, 0
    if kicad:
        hashes[DBL] = _write(os.path.join(out_dir, DBL), json.dumps(dbl_document(tables, columns), indent=4))
        if parts:
            hashes[f"{LIB}.kicad_sym"] = _write(os.path.join(out_dir, f"{LIB}.kicad_sym"), merge_symbols(parts))
            symbols = sum(1 for p in parts if (p.get("kicad_sym") or "").strip())
        pretty = os.path.join(out_dir, f"{LIB}.pretty")
        for name in os.listdir(pretty) if os.path.isdir(pretty) else []:
            os.remove(os.path.join(pretty, name))
        for part in parts:
            name = _footprint_name(part.get("footprint_ref") or "")
            if name and part.get("kicad_mod"):
                hashes[f"{LIB}.pretty/{name}.kicad_mod"] = _write(os.path.join(pretty, f"{name}.kicad_mod"),
                                                                 part["kicad_mod"])
        footprints = sum(1 for key in hashes if key.endswith(".kicad_mod"))
        for name, text in _lib_tables().items():
            hashes[name] = _write(os.path.join(out_dir, name), text)
    say(f"library/: {len(components)} parts in {len(tables)} tables, {symbols} symbols, {footprints} footprints")
    return {"files": hashes, "components": len(components), "tables": len(tables),
            "symbols": symbols, "footprints": footprints}


def edited(out_dir, hashes):
    """Generated text files whose content no longer matches what build() wrote."""
    changed = []
    for rel, digest in sorted((hashes or {}).items()):
        try:
            with open(os.path.join(out_dir, rel), "rb") as fh:
                if hashlib.sha256(fh.read()).hexdigest() != digest:
                    changed.append(rel)
        except FileNotFoundError:
            changed.append(rel)
    return changed
