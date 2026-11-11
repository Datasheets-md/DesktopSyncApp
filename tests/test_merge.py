import json

from conftest import jsonl, row

from datasheets_md.merge import has_conflict, merge


def keys(text):
    return [json.loads(line).get("k") for line in text.splitlines() if line.startswith("{") and "\"k\"" in line]


BASE = jsonl(row("a", "VOH", "1"), row("b", "VOL", "2"), row("c", "Icc", "3", section="power"))


def test_only_local_changed_a_row():
    local = BASE.replace('"1"', '"1.5"')
    text, conflicts = merge("parameters.jsonl", BASE, local, BASE)
    assert (text, conflicts) == (local, 0)


def test_edits_to_different_rows_merge():
    local = BASE.replace('"VOH","value":"1"', '"VOH","value":"1.5"')
    server = BASE.replace('"VOL","value":"2"', '"VOL","value":"2.5"')
    text, conflicts = merge("parameters.jsonl", BASE, local, server)
    assert conflicts == 0
    assert '"1.5"' in text and '"2.5"' in text and keys(text) == ["a", "b", "c"]


def test_same_row_both_sides_conflicts_on_that_row_only():
    local = BASE.replace('"VOH","value":"1"', '"VOH","value":"L"')
    server = BASE.replace('"VOH","value":"1"', '"VOH","value":"S"')
    text, conflicts = merge("parameters.jsonl", BASE, local, server)
    assert conflicts == 1 and has_conflict(text)
    lines = text.splitlines()
    assert lines[0] == "<<<<<<< local" and '"L"' in lines[1] and lines[2] == "=======" and '"S"' in lines[3]
    assert lines[4] == ">>>>>>> server" and len(lines) == 7


def test_same_change_on_both_sides_is_no_conflict():
    both = BASE.replace('"1"', '"9"')
    assert merge("parameters.jsonl", BASE, both, both) == (both, 0)


def test_local_addition_goes_to_the_end_of_its_section():
    added = json.dumps({"section": "electrical", "name": "Vih", "value": "2"})
    local = BASE + added + "\n"
    server = BASE + jsonl(row("d", "Pd", "1", section="power"))
    text, conflicts = merge("parameters.jsonl", BASE, local, server)
    assert conflicts == 0
    lines = text.splitlines()
    assert lines[2] == added and keys(text) == ["a", "b", "c", "d"]


def test_deletes():
    without_b = jsonl(row("a", "VOH", "1"), row("c", "Icc", "3", section="power"))
    # deleted here, untouched there -> stays deleted
    assert keys(merge("parameters.jsonl", BASE, without_b, BASE)[0]) == ["a", "c"]
    # deleted there, untouched here -> gone
    assert keys(merge("parameters.jsonl", BASE, BASE.replace('"1"', '"5"'), without_b)[0]) == ["a", "c"]
    # deleted here, changed there -> conflict
    server = BASE.replace('"VOL","value":"2"', '"VOL","value":"7"')
    assert merge("parameters.jsonl", BASE, without_b, server)[1] == 1
    # changed here, deleted there -> conflict
    local = BASE.replace('"VOL","value":"2"', '"VOL","value":"7"')
    assert merge("parameters.jsonl", BASE, local, without_b)[1] == 1


PINS = "id,number,name,etype,on_symbol,description\npin-a,1,IN,input,true,\npin-b,2,OUT,output,true,\n"


def test_pins_merge_by_id():
    local = PINS.replace("pin-a,1,IN", "pin-a,1,IN_A")
    server = PINS.replace("pin-b,2,OUT", "pin-b,3,OUT")
    text, conflicts = merge("pins.csv", PINS, local, server)
    assert conflicts == 0
    assert text.splitlines()[1:] == ["pin-a,1,IN_A,input,true,", "pin-b,3,OUT,output,true,"]


def test_new_local_pin_is_kept_and_same_pin_conflicts():
    local = PINS.replace("pin-a,1,IN", "pin-a,1,X") + ",9,EN,input,true,Enable\n"
    server = PINS.replace("pin-a,1,IN", "pin-a,1,Y")
    text, conflicts = merge("pins.csv", PINS, local, server)
    assert conflicts == 1 and text.splitlines()[-1] == ",9,EN,input,true,Enable"


def test_yaml_merges_key_by_key():
    base = "symbol: {}\n"
    assert merge("cad.yaml", base, "symbol: {a: 1}\n", base) == ("symbol: {a: 1}\n", 0)
    # different keys, even nested, merge cleanly
    text, conflicts = merge("cad.yaml", base, "symbol: {a: 1}\n", "symbol: {b: 2}\n")
    assert conflicts == 0 and text == "symbol:\n  a: 1\n  b: 2\n"
    base = "categories:\n  ICs: {pin_length: 2}\nreference_designators:\n  ICs: U\n"
    local = base.replace("U", "IC")
    server = base + "style: {}\n"
    text, conflicts = merge("settings/symbol_style.yaml", base, local, server)
    assert conflicts == 0 and "ICs: IC" in text and "style: {}" in text
    # the same key changed both ways -> whole-file conflict
    text, conflicts = merge("settings/symbol_style.yaml", base, local, base.replace("U", "IC2"))
    assert conflicts == 1 and text.startswith("<<<<<<< local\n")
