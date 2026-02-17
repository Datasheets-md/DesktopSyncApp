import os
import sqlite3

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, "kicadsync.sqlite")

KEY_COLUMN = "IPN"

STANDARD_COLUMNS = [
    "IPN",
    "Value",
    "Description",
    "Symbols",
    "Footprints",
    "Manufacturer",
    "MPN",
    "Package",
    "Category",
    "Datasheet",
    "ComponentUrl",
    "Stock",
    "Price_USD",
    "DigiKey_PN",
]


def sanitize_column_name(name):
    name = name.replace("/", "_").replace("\\", "_").replace("-", "_")
    name = name.replace(" ", "_")
    name = "".join(c if c.isalnum() or c == "_" else "_" for c in name)
    while "__" in name:
        name = name.replace("__", "_")
    return name.strip("_")


def sanitize_table_name(name):
    if "\u2192" in name:
        name = name.split("\u2192")[-1].strip()
    elif "/" in name:
        name = name.split("/")[-1].strip()
    return sanitize_column_name(name)


def open_db(path=None):
    path = path or DB_PATH
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def ensure_table(cur, table_name, all_columns):
    col_defs = ", ".join(f'"{c}" TEXT' for c in all_columns)
    target_schema = f'CREATE TABLE "{table_name}" ({col_defs}, PRIMARY KEY("{KEY_COLUMN}"))'

    cur.execute(
        'SELECT sql FROM sqlite_master WHERE type="table" AND name=?',
        (table_name,),
    )
    row = cur.fetchone()
    if row:
        existing_sql = row[0]
        if existing_sql != target_schema:
            cur.execute(f'DROP TABLE "{table_name}"')
            cur.execute(target_schema)
    else:
        cur.execute(target_schema)

    cur.execute(f'PRAGMA table_info("{table_name}")')
    existing = {r[1] for r in cur.fetchall()}
    for col in all_columns:
        if col not in existing:
            cur.execute(f'ALTER TABLE "{table_name}" ADD COLUMN "{col}" TEXT DEFAULT ""')


def upsert_rows(cur, table_name, columns, rows):
    placeholders = ", ".join("?" for _ in columns)
    col_names = ", ".join(f'"{c}"' for c in columns)
    sql = f'INSERT OR REPLACE INTO "{table_name}" ({col_names}) VALUES ({placeholders})'
    for row in rows:
        values = [row.get(col, "") for col in columns]
        cur.execute(sql, values)


def delete_stale_rows(cur, table_name, valid_ipns):
    if not valid_ipns:
        return
    cur.execute(f'SELECT "{KEY_COLUMN}" FROM "{table_name}"')
    existing = {r[0] for r in cur.fetchall()}
    stale = existing - set(valid_ipns)
    for ipn in stale:
        cur.execute(f'DELETE FROM "{table_name}" WHERE "{KEY_COLUMN}" = ?', (ipn,))


def drop_empty_tables(cur):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    for (name,) in cur.fetchall():
        cur.execute(f'SELECT COUNT(*) FROM "{name}"')
        if cur.fetchone()[0] == 0:
            cur.execute(f'DROP TABLE "{name}"')


def get_existing_tables(cur):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {r[0] for r in cur.fetchall()}
