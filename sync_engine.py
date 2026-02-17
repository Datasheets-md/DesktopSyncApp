import argparse
import sys
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from config import load_config
from auth import login, get_valid_token
from api import fetch_components, fetch_digikey
from db import (
    STANDARD_COLUMNS,
    open_db,
    ensure_table,
    upsert_rows,
    delete_stale_rows,
    drop_empty_tables,
    get_existing_tables,
    sanitize_column_name,
    sanitize_table_name,
)
from dbl import write_dbl


def extract_component(component, server_url, digikey_data, param_names):
    metadata = component.get("metadata") or []
    meta = metadata[0] if metadata else {}

    uuid = component.get("uuid", "")

    row = {
        "IPN": meta.get("part_number", ""),
        "Value": meta.get("part_number", ""),
        "Description": meta.get("description", ""),
        "Symbols": meta.get("kicad_symbol", ""),
        "Footprints": meta.get("kicad_footprint", ""),
        "Manufacturer": meta.get("manufacturer", ""),
        "MPN": meta.get("part_number", ""),
        "Package": meta.get("package", ""),
        "Category": meta.get("category", ""),
        "Datasheet": "",
        "ComponentUrl": f"{server_url.rstrip('/')}/components/{uuid}" if uuid else "",
        "Stock": "",
        "Price_USD": "",
        "DigiKey_PN": "",
    }

    if digikey_data:
        row["Datasheet"] = digikey_data.get("datasheet_url", "")
        row["Stock"] = str(digikey_data.get("quantity_available", ""))
        row["Price_USD"] = str(digikey_data.get("unit_price", ""))
        row["DigiKey_PN"] = digikey_data.get("digikey_part_number", "")

    for p in component.get("parameters") or []:
        key = p.get("key", "")
        col = sanitize_column_name(key)
        if col and col in param_names:
            value = p.get("value", "")
            unit = p.get("unit", "")
            row[col] = f"{value} {unit}".strip() if unit else str(value)

    return row


def discover_param_names(components):
    names = set()
    for comp in components:
        for p in comp.get("parameters") or []:
            key = p.get("key", "")
            col = sanitize_column_name(key)
            if col:
                names.add(col)
    return sorted(names)


def group_by_category(components):
    groups = {}
    for comp in components:
        metadata = comp.get("metadata") or []
        meta = metadata[0] if metadata else {}
        cat_name = meta.get("category", "") or "Uncategorized"
        table_name = sanitize_table_name(cat_name)
        if not table_name:
            table_name = "Uncategorized"
        if table_name not in groups:
            groups[table_name] = []
        groups[table_name].append(comp)
    return groups


def run_sync(server_url, token, config=None):
    config = config or load_config()
    fetch_dk = config.get("fetch_digikey", True)
    page_limit = config.get("page_limit", 100)
    exclude_fields = set(config.get("exclude_fields", []))

    print("Fetching components...")
    components = fetch_components(server_url, token, page_limit)
    if not components:
        return {"tables": 0, "components": 0, "error": "No components found"}

    print(f"Fetched {len(components)} ready components")

    digikey_cache = {}
    if fetch_dk:
        for comp in components:
            if comp.get("digikey_status") == 2:
                uuid = comp.get("uuid", "")
                if uuid:
                    dk = fetch_digikey(server_url, token, uuid)
                    if dk:
                        digikey_cache[uuid] = dk
                        print(f"  DigiKey data for {uuid[:8]}...")
        print(f"Fetched DigiKey data for {len(digikey_cache)} components")

    param_columns = discover_param_names(components)
    print(f"Discovered {len(param_columns)} unique parameters")

    grouped = group_by_category(components)
    print(f"Grouped into {len(grouped)} categories")

    all_columns = list(STANDARD_COLUMNS) + param_columns

    table_rows = {}
    for table_name, comps in grouped.items():
        rows = []
        for comp in comps:
            uuid = comp.get("uuid", "")
            dk = digikey_cache.get(uuid)
            row = extract_component(comp, server_url, dk, set(param_columns))
            if row["IPN"]:
                rows.append(row)
        if rows:
            table_rows[table_name] = rows

    conn = open_db()
    cur = conn.cursor()

    existing_tables = get_existing_tables(cur)
    active_tables = []
    total_parts = 0

    for table_name, rows in sorted(table_rows.items()):
        ensure_table(cur, table_name, all_columns)
        upsert_rows(cur, table_name, all_columns, rows)
        valid_ipns = [r["IPN"] for r in rows]
        delete_stale_rows(cur, table_name, valid_ipns)
        active_tables.append(table_name)
        total_parts += len(rows)
        print(f"  {table_name}: {len(rows)} parts")

    stale_tables = existing_tables - set(active_tables)
    for t in stale_tables:
        cur.execute(f'DROP TABLE IF EXISTS "{t}"')
        print(f"  Dropped stale table: {t}")

    drop_empty_tables(cur)

    conn.commit()
    conn.close()

    write_dbl(active_tables, param_columns, exclude_fields)
    print(f"Wrote kicadsync.kicad_dbl with {len(active_tables)} libraries")

    return {
        "tables": len(active_tables),
        "components": total_parts,
        "error": None,
    }


def main():
    parser = argparse.ArgumentParser(description="Sync private components to local SQLite for KiCad")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--server")
    parser.add_argument("--config")
    args = parser.parse_args()

    config = load_config(args.config)
    server_url = args.server or config.get("server_url", "")
    if not server_url:
        print("Error: set server_url in kicad_sync.json or use --server", file=sys.stderr)
        sys.exit(1)

    print(f"Logging in to {server_url}...")
    token = login(server_url, args.email, args.password)
    print("Login successful")

    result = run_sync(server_url, token, config)
    if result["error"]:
        print(f"Sync error: {result['error']}", file=sys.stderr)
        sys.exit(1)

    print(f"\nDone! {result['components']} components in {result['tables']} tables.")
    print("Re-open the symbol chooser in KiCad to see updates.")


if __name__ == "__main__":
    main()
