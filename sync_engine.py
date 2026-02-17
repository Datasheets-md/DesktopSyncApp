import argparse
import sys
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from config import load_config
from api import fetch_components
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

def extract_component(component, param_names):
    metadata = component.get("metadata") or []
    meta = metadata[0] if metadata else {}

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
        "ComponentUrl": "",
        "Stock": "",
        "Price_USD": "",
        "DigiKey_PN": "",
    }

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

def run_sync(config=None):
    config = config or load_config()
    exclude_fields = set(config.get("exclude_fields", []))
    output_dir = config.get("output_dir", SCRIPT_DIR)

    db_path = os.path.join(output_dir, "kicadsync.sqlite")
    dbl_path = os.path.join(output_dir, "kicadsync.kicad_dbl")

    print("Fetching components...")
    components = fetch_components(config)
    if not components:
        return {"tables": 0, "components": 0, "error": "No components found"}

    print(f"Fetched {len(components)} ready components")

    param_columns = discover_param_names(components)
    print(f"Discovered {len(param_columns)} unique parameters")

    grouped = group_by_category(components)
    print(f"Grouped into {len(grouped)} categories")

    all_columns = list(STANDARD_COLUMNS) + param_columns

    table_rows = {}
    for table_name, comps in grouped.items():
        rows = []
        for comp in comps:
            row = extract_component(comp, set(param_columns))
            if row["IPN"]:
                rows.append(row)
        if rows:
            table_rows[table_name] = rows

    conn = open_db(db_path)
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

    write_dbl(active_tables, param_columns, exclude_fields, dbl_path)
    print(f"Wrote {dbl_path} with {len(active_tables)} libraries")

    return {
        "tables": len(active_tables),
        "components": total_parts,
        "error": None,
    }

def main():
    parser = argparse.ArgumentParser(description="Sync private components to local SQLite for KiCad")
    parser.add_argument("--config")
    args = parser.parse_args()

    config = load_config(args.config)

    print(f"Connecting to {config['db_host']}:{config['db_port']}/{config['db_name']}...")
    result = run_sync(config)
    if result["error"]:
        print(f"Sync error: {result['error']}", file=sys.stderr)
        sys.exit(1)

    print(f"\nDone! {result['components']} components in {result['tables']} tables.")
    print("Re-open the symbol chooser in KiCad to see updates.")

if __name__ == "__main__":
    main()
