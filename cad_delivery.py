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


def _extract_symbol_block(kicad_sym_text: str) -> str | None:
    """Return the top-level `(symbol "..." ...)` s-expression from a single-symbol
    library, with balanced parentheses (string-literal aware). None if absent."""
    start = kicad_sym_text.find("(symbol ")
    if start == -1:
        return None
    depth = 0
    in_str = False
    escaped = False
    for i in range(start, len(kicad_sym_text)):
        ch = kicad_sym_text[i]
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
                return kicad_sym_text[start:i + 1]
    return None


def merge_symbol_lib(parts) -> str:
    """Build one KiCad symbol library from each part's generated .kicad_sym."""
    blocks = []
    for part in parts:
        text = part.get("kicad_sym") or ""
        block = _extract_symbol_block(text)
        if block:
            blocks.append("  " + block)
    header = (
        "(kicad_symbol_lib\n"
        "  (version 20241209)\n"
        '  (generator "datasheets.md")\n'
        '  (generator_version "9.0")\n'
    )
    return header + "\n".join(blocks) + "\n)\n"


def write_pretty(parts, pretty_dir) -> int:
    """Write one <name>.kicad_mod per part with a standard footprint. Returns the
    count written."""
    os.makedirs(pretty_dir, exist_ok=True)
    written = 0
    for part in parts:
        mod = part.get("kicad_mod")
        ref = part.get("footprint_ref") or ""
        if not mod or ":" not in ref:
            continue
        name = ref.split(":", 1)[1]
        safe = re.sub(r"[^A-Za-z0-9_.+-]", "_", name)
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
