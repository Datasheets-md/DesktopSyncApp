"""Deliver generated KiCad symbols + standard footprints to the user's machine.

Symbols (one .kicad_sym file, many symbols) and footprints (a .pretty folder,
one .kicad_mod per part) follow KiCad's two library shapes. The server returns
each part's single-symbol .kicad_sym and the matching standard .kicad_mod; here
we merge the symbols into one `datasheets.kicad_sym` and fan the footprints into
`datasheets.pretty/`, then drop project-level lib-table files pointing at both.
"""

import os
import re

LIB_NAME = "datasheets"


def kicad_symbol_name(part_number: str) -> str:
    """Sanitize a part number into a KiCad symbol name. MUST match RESTAPI's
    `_kicad_symbol_name` so `datasheets:<name>` from the HTTP library resolves
    to the symbol delivered here."""
    return re.sub(r"[^A-Za-z0-9_.+-]", "_", part_number or "")


def _iter_symbol_blocks(kicad_sym_text: str):
    """Yield each top-level `(symbol "..." ...)` s-expression from a library, with
    balanced parentheses (string-literal aware). A part's payload usually holds
    one symbol, but a standard-glyph part that `extends` a parent carries the
    ancestor chain too -- all of which must land in the merged library."""
    i, n = 0, len(kicad_sym_text)
    while True:
        start = kicad_sym_text.find("(symbol ", i)
        if start == -1:
            return
        depth = 0
        in_str = escaped = False
        end = None
        for j in range(start, n):
            ch = kicad_sym_text[j]
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
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
        yield kicad_sym_text[start:end]
        i = end


def merge_symbol_lib(parts) -> str:
    """Build one KiCad symbol library from each part's delivered .kicad_sym."""
    blocks = []
    for part in parts:
        text = part.get("kicad_sym") or ""
        for block in _iter_symbol_blocks(text):
            blocks.append("  " + block)
    header = (
        "(kicad_symbol_lib\n"
        "  (version 20241209)\n"
        '  (generator "datasheets.md")\n'
        '  (generator_version "9.0")\n'
    )
    return header + "\n".join(blocks) + "\n)\n"


def _footprint_file_name(footprint_ref: str) -> str | None:
    """Sanitised `<name>` for a `<library>:<name>` footprint ref -- the stem of
    the .kicad_mod written into datasheets.pretty and the `datasheets:<name>`
    nickname. None when the ref is malformed."""
    if not footprint_ref or ":" not in footprint_ref:
        return None
    return re.sub(r"[^A-Za-z0-9_.+-]", "_", footprint_ref.split(":", 1)[1])


def ref_map(export) -> dict:
    """part_number -> {'symbol': 'datasheets:<name>', 'footprint': 'datasheets:<name>'}
    for parts actually delivered, so the database library (dbsync.kicad_dbl) can
    point at the delivered libs instead of the user's installed KiCad libraries.
    Footprint is only mapped when its .kicad_mod was delivered."""
    out: dict = {}
    for part in (export or {}).get("parts") or []:
        pn = part.get("part_number") or ""
        if not pn:
            continue
        entry: dict = {}
        sym = part.get("symbol_name") or ""
        if sym:
            entry["symbol"] = f"{LIB_NAME}:{sym}"
        fp = _footprint_file_name(part.get("footprint_ref") or "")
        if fp and part.get("kicad_mod"):
            entry["footprint"] = f"{LIB_NAME}:{fp}"
        out[pn] = entry
    return out


def write_pretty(parts, pretty_dir) -> int:
    """Write one <name>.kicad_mod per part with a standard footprint. Returns the
    count written."""
    os.makedirs(pretty_dir, exist_ok=True)
    written = 0
    for part in parts:
        mod = part.get("kicad_mod")
        safe = _footprint_file_name(part.get("footprint_ref") or "")
        if not mod or not safe:
            continue
        with open(os.path.join(pretty_dir, f"{safe}.kicad_mod"), "w", encoding="utf-8") as fh:
            fh.write(mod)
        written += 1
    return written


def write_lib_tables(output_dir) -> None:
    """Drop project-level sym-lib-table + fp-lib-table referencing the delivered
    libs (KiCad reads these when output_dir is opened as a project)."""
    sym = (
        "(sym_lib_table\n"
        "  (version 7)\n"
        f'  (lib (name "{LIB_NAME}")(type "KiCad")'
        f'(uri "${{KIPRJMOD}}/{LIB_NAME}.kicad_sym")(options "")'
        '(descr "datasheets.md generated symbols"))\n)\n'
    )
    fp = (
        "(fp_lib_table\n"
        "  (version 7)\n"
        f'  (lib (name "{LIB_NAME}")(type "KiCad")'
        f'(uri "${{KIPRJMOD}}/{LIB_NAME}.pretty")(options "")'
        '(descr "datasheets.md standard footprints"))\n)\n'
    )
    with open(os.path.join(output_dir, "sym-lib-table"), "w", encoding="utf-8") as fh:
        fh.write(sym)
    with open(os.path.join(output_dir, "fp-lib-table"), "w", encoding="utf-8") as fh:
        fh.write(fp)


def deliver(export, output_dir) -> dict:
    """Write the symbol lib, .pretty folder, and lib-tables from a cad-export
    payload. Returns a summary {symbols, footprints}."""
    parts = (export or {}).get("parts") or []
    if not parts:
        return {"symbols": 0, "footprints": 0}

    sym_path = os.path.join(output_dir, f"{LIB_NAME}.kicad_sym")
    with open(sym_path, "w", encoding="utf-8") as fh:
        fh.write(merge_symbol_lib(parts))

    footprints = write_pretty(parts, os.path.join(output_dir, f"{LIB_NAME}.pretty"))
    write_lib_tables(output_dir)

    symbols = sum(1 for p in parts if (p.get("kicad_sym") or "").strip())
    return {"symbols": symbols, "footprints": footprints}
