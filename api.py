import psycopg2.extras
from auth import connect, authenticate

def fetch_components(config):
    email = config.get("user_email", "")
    password = config.get("user_password", "")
    if not email or not password:
        raise RuntimeError("user_email and user_password must be set in kicad_sync.json")

    conn = connect(config)
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    owner_id = authenticate(cur, email, password)

    cur.execute("""
        SELECT
            c.uuid AS uuid,
            c.digikey_status,
            c.param_extraction_status,
            m.part_number,
            m.description,
            m.kicad_symbol,
            m.kicad_footprint,
            m.package,
            mfr.name AS manufacturer,
            cat.name AS category
        FROM backend_main_private_component_object c
        LEFT JOIN backend_main_private_component_metadata m
            ON m.parent_component_id = c.id
        LEFT JOIN backend_main_manufacturer mfr
            ON mfr.id = m.manufacturer_id
        LEFT JOIN backend_main_component_category cat
            ON cat.id = m.category_id
        WHERE c.param_extraction_status = 2
          AND c.owner_id = %s
    """, (owner_id,))

    comp_rows = cur.fetchall()

    comp_ids = {}
    components = []
    for row in comp_rows:
        uuid_str = str(row["uuid"])
        comp = {
            "uuid": uuid_str,
            "digikey_status": row["digikey_status"],
            "processing_status": 1,
            "metadata": [{
                "part_number": row["part_number"] or "",
                "description": row["description"] or "",
                "kicad_symbol": row["kicad_symbol"] or "",
                "kicad_footprint": row["kicad_footprint"] or "",
                "manufacturer": row["manufacturer"] or "",
                "package": row["package"] or "",
                "category": row["category"] or "",
            }],
            "parameters": [],
        }
        components.append(comp)
        comp_ids[uuid_str] = comp

    if comp_ids:
        cur.execute("""
            SELECT
                c.uuid AS comp_uuid,
                p.key,
                p.value,
                p.unit
            FROM backend_main_private_component_parameter p
            JOIN backend_main_private_component_object c ON c.id = p.parent_component_id
            WHERE c.owner_id = %s AND c.uuid = ANY(%s)
        """, (owner_id, list(comp_ids.keys()),))

        for row in cur.fetchall():
            comp = comp_ids.get(str(row["comp_uuid"]))
            if comp:
                comp["parameters"].append({
                    "key": row["key"] or "",
                    "value": row["value"] or "",
                    "unit": row["unit"] or "",
                })

    cur.close()
    conn.close()

    print(f"  Fetched {len(components)} components from database")
    return components
