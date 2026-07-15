import argparse
import sys
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from config import load_config
from api import fetch_components, fetch_cad_export
import cad_delivery
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
    _dedup_columns,
)
from dbl import write_dbl

def extract_component(component, param_names, cad_map=None):
    metadata = component.get("metadata") or []
    meta = metadata[0] if metadata else {}

    # When the part's symbol/footprint were delivered self-contained, point the
    # database library at the delivered `datasheets:` libs; else fall back to the
    # stock KiCad ref (which relies on the user's installed libraries).
    delivered = (cad_map or {}).get(meta.get("part_number", "")) or {}

    row = {
        "IPN": meta.get("part_number", ""),
        "Value": meta.get("part_number", ""),
        "Description": meta.get("description", ""),
        "Symbols": delivered.get("symbol") or meta.get("kicad_symbol", ""),
        "Footprints": delivered.get("footprint") or meta.get("kicad_footprint", ""),
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

    # Track which columns we've already filled to avoid overwriting with duplicates
    filled_params = set()

    for p in component.get("parameters") or []:
        key = p.get("key", "")
        col = sanitize_column_name(key)
        if col and col in param_names and col not in STANDARD_COLUMNS:
            # Only use the first occurrence of a sanitized column name
            if col not in filled_params:
                value = p.get("value", "")
                unit = p.get("unit", "")
                row[col] = f"{value} {unit}".strip() if unit else str(value)
                filled_params.add(col)

    return row

def discover_param_names(components):
    # Case-insensitive set for dedup and standard column conflict detection
    standard_lower = {c.lower() for c in STANDARD_COLUMNS}
    seen_lower = {}  # lowercase -> first-seen original name

    for comp in components:
        for p in comp.get("parameters") or []:
            key = p.get("key", "")
            if not key:
                continue

            col = sanitize_column_name(key)

            if not col:
                continue

            col_lower = col.lower()

            # Skip if it conflicts with standard columns (case-insensitive)
            if col_lower in standard_lower:
                print(f"Warning: Parameter '{key}' conflicts with standard column. Skipping.")
                continue

            # Keep first-seen casing, skip case-insensitive duplicates
            if col_lower not in seen_lower:
                seen_lower[col_lower] = col

    result = sorted(seen_lower.values())
    print(f"  Unique parameter columns: {len(result)}")

    return result

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

    db_path = os.path.join(output_dir, "dbsync.sqlite")
    dbl_path = os.path.join(output_dir, "dbsync.kicad_dbl")

    print("Fetching components...")
    components = fetch_components(config)
    if not components:
        return {"tables": 0, "components": 0, "error": "No components found"}

    print(f"Fetched {len(components)} ready components")

    param_columns = discover_param_names(components)
    print(f"Discovered {len(param_columns)} unique parameters")

    grouped = group_by_category(components)
    print(f"Grouped into {len(grouped)} categories")

    # Fetch the CAD export up front so the database library can reference the
    # symbols/footprints we deliver self-contained. Best-effort: a failure just
    # falls back to stock KiCad refs. The same payload is delivered below.
    cad_export = None
    try:
        cad_export = fetch_cad_export(config)
    except Exception as e:
        print(f"  CAD export fetch failed: {e}")
    cad_map = cad_delivery.ref_map(cad_export)

    # Combine standard columns and parameter columns, case-insensitive dedup
    all_columns = _dedup_columns(list(STANDARD_COLUMNS) + param_columns)

    table_rows = {}
    for table_name, comps in grouped.items():
        rows = []
        for comp in comps:
            row = extract_component(comp, set(param_columns), cad_map)
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

    # Deliver per-part symbols (one merged datasheets.kicad_sym) + footprints
    # (datasheets.pretty/, one .kicad_mod per part) from the export fetched
    # above. Best-effort: a delivery failure must not fail the database sync.
    cad = {"symbols": 0, "footprints": 0}
    try:
        cad = cad_delivery.deliver(cad_export, output_dir)
        print(f"Delivered {cad['symbols']} symbols, {cad['footprints']} footprints")
    except Exception as e:
        print(f"  CAD delivery skipped: {e}")

    return {
        "tables": len(active_tables),
        "components": total_parts,
        "symbols": cad["symbols"],
        "footprints": cad["footprints"],
        "error": None,
    }

def main():
    parser = argparse.ArgumentParser(description="Sync private components to local SQLite for KiCad")
    parser.add_argument("--config", help="Path to config file")
    args = parser.parse_args()

    config = load_config(args.config)

    print("Connecting to API...")
    result = run_sync(config)
    if result["error"]:
        print(f"Sync error: {result['error']}", file=sys.stderr)
        sys.exit(1)

    print(f"\nDone! {result['components']} components in {result['tables']} tables.")
    print(f"Delivered {result['symbols']} symbols + {result['footprints']} footprints "
          "into datasheets.kicad_sym / datasheets.pretty.")
    print("\nRe-open the symbol chooser in KiCad to see updates.")

if __name__ == "__main__":
    main()
