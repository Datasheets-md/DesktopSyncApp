"""Three-way merge of a pulled file into a locally edited one (Spec 27a 3.5).

`parameters.jsonl` merges by row key `k`, `pins.csv` by pin `id`: two edits to
different rows never conflict, two different edits to the same row conflict
on that row only. The YAML files (cad.yaml, settings/*.yaml) merge key by key,
recursing into mappings, so edits under different keys never conflict; any
clash there falls back to a whole-file conflict. Everything else merges as a
whole file. A conflict is written inline:

    <<<<<<< local
    <the local line(s)>
    =======
    <the server line(s)>
    >>>>>>> server
"""
import csv
import io
import json

import yaml

LOCAL, MIDDLE, SERVER = "<<<<<<< local", "=======", ">>>>>>> server"
MARKER_PREFIXES = ("<<<<<<< ", ">>>>>>> ")


def has_conflict(text):
    return any(line == MIDDLE or line.startswith(MARKER_PREFIXES) for line in (text or "").splitlines())


def _block(local, server):
    return [LOCAL, *local, MIDDLE, *server, SERVER]


def merge_whole(base, local, server):
    """(text, conflicts) for a file merged as one unit."""
    if local == base or local == server:
        return server, 0
    if server == base:
        return local, 0
    lines = _block(local.rstrip("\n").split("\n"), server.rstrip("\n").split("\n"))
    return "\n".join(lines) + "\n", 1


def _three_way(base, local, server, extra):
    """Keyed merge. Each of base/local/server is an ordered list of
    (key, section, line, value); `value` is what equality compares. `extra`
    is local lines without a key (new rows): appended at the end of their
    section. Returns (lines, conflicts)."""
    b = {k: (s, line, v) for k, s, line, v in base}
    loc = {k: (s, line, v) for k, s, line, v in local}
    out, conflicts = [], 0   # out: [(section, [lines])]

    def take(section, lines):
        out.append((section, lines))

    for key, section, s_line, s_val in server:
        b_entry, l_entry = b.get(key), loc.get(key)
        if l_entry is None:
            if b_entry is None:
                take(section, [s_line])                       # new on the server
            elif b_entry[2] != s_val:
                take(section, _block([], [s_line]))           # deleted here, changed there
                conflicts += 1
            continue                                          # deleted here, unchanged there
        l_val = l_entry[2]
        if l_val == s_val or (b_entry is not None and b_entry[2] == l_val):
            take(section, [s_line])
        elif b_entry is not None and b_entry[2] == s_val:
            take(section, [l_entry[1]])
        else:
            take(section, _block([l_entry[1]], [s_line]))
            conflicts += 1
    server_keys = {k for k, *_ in server}
    for key, section, l_line, l_val in local:
        if key in server_keys:
            continue
        if key in b:
            if b[key][2] != l_val:                            # changed here, deleted there
                take(section, _block([l_line], []))
                conflicts += 1
            continue
        extra.append((section, l_line))                       # a local row with an unknown key
    for section, line in extra:
        last = max((i for i, (s, _) in enumerate(out) if s == section), default=None)
        if last is None:
            out.append((section, [line]))
        else:
            out.insert(last + 1, (section, [line]))
    return [line for _, lines in out for line in lines], conflicts


# ----- parameters.jsonl -------------------------------------------------------

def _jsonl(text):
    keyed, extra = [], []
    for line in (text or "").splitlines():
        if not line.strip() or line == MIDDLE or line.startswith(MARKER_PREFIXES):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            extra.append((None, line))
            continue
        if not isinstance(obj, dict):
            extra.append((None, line))
            continue
        section = obj.get("section")
        if obj.get("k"):
            keyed.append((obj["k"], section, line, obj))
        else:
            extra.append((section, line))
    return keyed, extra


def merge_jsonl(base, local, server):
    b, _ = _jsonl(base)
    loc, extra = _jsonl(local)
    s, _ = _jsonl(server)
    lines, conflicts = _three_way(b, loc, s, extra)
    return "".join(line + "\n" for line in lines), conflicts


# ----- pins.csv ------------------------------------------------------------------

def _csv_line(cells):
    buffer = io.StringIO()
    csv.writer(buffer, quoting=csv.QUOTE_MINIMAL, lineterminator="\n").writerow(cells)
    return buffer.getvalue().rstrip("\n")


def _csv(text):
    rows = list(csv.reader(io.StringIO(text or "")))
    header = rows[0] if rows else []
    keyed, extra = [], []
    for cells in rows[1:]:
        if not cells or not any(c.strip() for c in cells):
            continue
        if cells[0] in (MIDDLE,) or cells[0].startswith(MARKER_PREFIXES):
            continue
        if cells[0]:
            keyed.append((cells[0], None, _csv_line(cells), cells))
        else:
            extra.append((None, _csv_line(cells)))
    return header, keyed, extra


def merge_csv(base, local, server):
    b_head, b, _ = _csv(base)
    l_head, loc, extra = _csv(local)
    s_head, s, _ = _csv(server)
    if l_head != s_head:
        return merge_whole(base, local, server)
    lines, conflicts = _three_way(b, loc, s, extra)
    return "\n".join([_csv_line(s_head), *lines]) + "\n", conflicts


_MISSING = object()


class _Clash(Exception):
    pass


def _merge_value(base, local, server):
    if local == server:
        return local
    if local == base:
        return server
    if server == base:
        return local
    if isinstance(local, dict) and isinstance(server, dict):
        base = base if isinstance(base, dict) else {}
        out = {}
        for key in [*server, *(k for k in local if k not in server)]:
            value = _merge_value(base.get(key, _MISSING), local.get(key, _MISSING), server.get(key, _MISSING))
            if value is not _MISSING:
                out[key] = value
        return out
    raise _Clash


def merge_yaml(base, local, server):
    """(text, conflicts): key-by-key, written the way the server renders YAML."""
    try:
        mine, theirs = yaml.safe_load(local), yaml.safe_load(server)
        merged = _merge_value(yaml.safe_load(base) if base else {}, mine, theirs)
    except (_Clash, yaml.YAMLError):
        return merge_whole(base, local, server)
    if merged == theirs:
        return server, 0
    if merged == mine:
        return local, 0           # keep the user's own formatting
    return yaml.safe_dump(merged, sort_keys=True, allow_unicode=True, default_flow_style=False), 0


MERGERS = {"parameters.jsonl": merge_jsonl, "pins.csv": merge_csv, "cad.yaml": merge_yaml,
           "settings/mapping.yaml": merge_yaml, "settings/symbol_style.yaml": merge_yaml}


def merge(name, base, local, server):
    """(text, conflicts) for file `name`; `base` may be None for a file new on both sides."""
    if local == server:
        return server, 0
    if base is not None and local == base:
        return server, 0
    return MERGERS.get(name, merge_whole)(base or "", local, server)
