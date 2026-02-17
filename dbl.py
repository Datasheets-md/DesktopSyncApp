import json
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DBL_PATH = os.path.join(SCRIPT_DIR, "kicadsync.kicad_dbl")

def generate_kicad_dbl(table_names, param_columns, exclude_fields=None):
    exclude_fields = set(exclude_fields or [])

    common_fields = [
        ("Value",        "Value",        True,  True,  False),
        ("Manufacturer", "Manufacturer", False, True,  True),
        ("MPN",          "MPN",          False, True,  True),
        ("Package",      "Package",      False, True,  True),
        ("Category",     "Category",     False, True,  True),
        ("Stock",        "Stock",        False, True,  True),
        ("Price_USD",    "Price_USD",    False, True,  True),
        ("DigiKey_PN",   "DigiKey_PN",   False, True,  True),
        ("Datasheet",    "Datasheet",    False, False, False),
        ("ComponentUrl", "ComponentUrl", False, False, False),
    ]

    libraries = []
    for table_name in table_names:
        fields = []
        for col, name, vis_add, vis_choose, show_nm in common_fields:
            if col in exclude_fields:
                continue
            entry = {
                "column": col,
                "name": name,
                "visible_on_add": vis_add,
                "visible_in_chooser": vis_choose,
            }
            if show_nm:
                entry["show_name"] = True
            fields.append(entry)

        for col in param_columns:
            if col in exclude_fields:
                continue
            fields.append({
                "column": col,
                "name": col.replace("_", " "),
                "visible_on_add": False,
                "visible_in_chooser": False,
                "show_name": True,
            })

        libraries.append({
            "name": table_name,
            "table": table_name,
            "key": "IPN",
            "symbols": "Symbols",
            "footprints": "Footprints",
            "fields": fields,
            "properties": {
                "description": "Description",
            },
        })

    dbl = {
        "meta": {"version": 0},
        "name": "KiCadSync Library",
        "description": "Components synced from private component library",
        "source": {
            "type": "odbc",
            "dsn": "",
            "username": "",
            "password": "",
            "timeout_seconds": 2,
            "connection_string": "Driver={SQLite3 ODBC Driver};Database=${CWD}/kicadsync.sqlite",
        },
        "globally_unique_keys": True,
        "libraries": libraries,
    }

    return dbl

def write_dbl(table_names, param_columns, exclude_fields=None, path=None):
    path = path or DBL_PATH
    dbl = generate_kicad_dbl(table_names, param_columns, exclude_fields)
    with open(path, "w") as f:
        json.dump(dbl, f, indent=4)
    return path
