import argparse
import sys
import os
import json

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
        if col and col in param_names and col not in STANDARD_COLUMNS:
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
            if col and col not in STANDARD_COLUMNS:
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

    # Ensure no duplicate columns: param_columns already excludes STANDARD_COLUMNS
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

def export_to_kicad_sym(db_path, output_path):
    """Export database to KiCad symbol library format (.kicad_sym) in S-expression format"""
    import sqlite3

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Get all tables
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = cur.fetchall()

    # Start building S-expression format
    output = []
    output.append('(kicad_symbol_lib (version 20211014) (generator "dbsync")')

    total_components = 0

    for table_name, in tables:
        # Get all components from table
        cur.execute(f'SELECT * FROM "{table_name}"')
        columns = [desc[0] for desc in cur.description]
        components = cur.fetchall()

        for comp in components:
            row = dict(zip(columns, comp))
            ipn = row.get('IPN', '')
            if not ipn:
                continue

            # Clean component name for S-expression format
            symbol_name = ipn.replace(' ', '_').replace('/', '_').replace('\\', '_')

            # Determine reference prefix based on category
            ref_prefix = "U"
            category = (row.get('Category', '') or '').lower()
            if 'mosfet' in category or 'transistor' in category:
                ref_prefix = "Q"
            elif 'capacitor' in category:
                ref_prefix = "C"
            elif 'resistor' in category:
                ref_prefix = "R"
            elif 'inductor' in category:
                ref_prefix = "L"
            elif 'diode' in category:
                ref_prefix = "D"

            # Start symbol
            output.append(f'  (symbol "{symbol_name}" (pin_names (offset 0.254)) (in_bom yes) (on_board yes)')

            # Add properties
            prop_id = 0

            # Reference
            output.append(f'    (property "Reference" "{ref_prefix}" (id {prop_id}) (at 0 0 0)')
            output.append('      (effects (font (size 1.27 1.27)))')
            output.append('    )')
            prop_id += 1

            # Value
            value = row.get('Value', ipn)
            output.append(f'    (property "Value" "{value}" (id {prop_id}) (at 0 0 0)')
            output.append('      (effects (font (size 1.27 1.27)))')
            output.append('    )')
            prop_id += 1

            # Footprint
            footprint = row.get('Footprints', '')
            if footprint:
                output.append(f'    (property "Footprint" "{footprint}" (id {prop_id}) (at 0 0 0)')
                output.append('      (effects (font (size 1.27 1.27)) hide)')
                output.append('    )')
                prop_id += 1

            # Datasheet
            datasheet = row.get('Datasheet', '')
            if datasheet:
                output.append(f'    (property "Datasheet" "{datasheet}" (id {prop_id}) (at 0 0 0)')
                output.append('      (effects (font (size 1.27 1.27)) hide)')
                output.append('    )')
                prop_id += 1

            # Add other properties
            for col, val in row.items():
                if val and col not in ['IPN', 'Value', 'Symbols', 'Footprints', 'Datasheet']:
                    # Escape quotes in values
                    val_str = str(val).replace('"', '\\"')
                    col_name = col.replace('_', ' ')
                    output.append(f'    (property "{col_name}" "{val_str}" (id {prop_id}) (at 0 0 0)')
                    output.append('      (effects (font (size 1.27 1.27)) hide)')
                    output.append('    )')
                    prop_id += 1

            # Add a basic symbol graphic (rectangle) - users should use database symbols
            output.append('    (symbol "{}_0_1"'.format(symbol_name))
            output.append('      (rectangle (start -5.08 5.08) (end 5.08 -5.08)')
            output.append('        (stroke (width 0.254) (type default) (color 0 0 0 0))')
            output.append('        (fill (type background))')
            output.append('      )')
            output.append('    )')

            # Add pins (minimal - just power and ground for now)
            output.append('    (symbol "{}_1_1"'.format(symbol_name))
            output.append('      (pin power_in line (at 0 7.62 270) (length 2.54)')
            output.append('        (name "VCC" (effects (font (size 1.27 1.27))))')
            output.append('        (number "1" (effects (font (size 1.27 1.27))))')
            output.append('      )')
            output.append('      (pin power_in line (at 0 -7.62 90) (length 2.54)')
            output.append('        (name "GND" (effects (font (size 1.27 1.27))))')
            output.append('        (number "2" (effects (font (size 1.27 1.27))))')
            output.append('      )')
            output.append('    )')

            # Close symbol
            output.append('  )')

            total_components += 1

    # Close library
    output.append(')')

    # Write to file
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(output))

    conn.close()

    print(f"Exported {total_components} components to {output_path}")
    return total_components

def main():
    parser = argparse.ArgumentParser(description="Sync private components to local SQLite for KiCad")
    parser.add_argument("--config", help="Path to config file")
    parser.add_argument("--export-static", action="store_true",
                        help="Also export to static KiCad symbol library (no ODBC needed)")
    args = parser.parse_args()

    config = load_config(args.config)

    print("Connecting to API...")
    result = run_sync(config)
    if result["error"]:
        print(f"Sync error: {result['error']}", file=sys.stderr)
        sys.exit(1)

    print(f"\nDone! {result['components']} components in {result['tables']} tables.")

    # Export to static format if requested
    if args.export_static:
        output_dir = config.get("output_dir", ".")
        db_path = os.path.join(output_dir, "dbsync.sqlite")
        sym_path = os.path.join(output_dir, "dbsync.kicad_sym")

        print(f"\nExporting to static library...")
        export_to_kicad_sym(db_path, sym_path)
        print(f"Static library saved to: {sym_path}")
        print("You can add this file directly to KiCad - no ODBC drivers needed!")
    else:
        print("\nOptions:")
        print("1. Use with ODBC: Add dbsync_portable.kicad_dbl to KiCad (requires ODBC drivers)")
        print("2. Export static: Run with --export-static flag to create .kicad_sym (no drivers needed)")

    print("\nRe-open the symbol chooser in KiCad to see updates.")

if __name__ == "__main__":
    main()
