import json

import pytest
from conftest import PART, PATH, SHEET, etag, jsonl, row

from datasheets_md import cli, sync
from datasheets_md.repo import Repo, RepoError


def states(repo):
    return {f["path"]: f["state"] for p in sync.status(repo)["parts"] for f in p["files"]}


def test_clone_writes_working_base_state(repo, server):
    for name, text in server.parts[PART]["files"].items():
        rel = f"{PATH}/{name}"
        assert repo.read(repo.working(rel)) == text == repo.read(repo.base(rel))
        assert repo.parts()[PART]["files"][name] == etag(text)
    assert repo.read(repo.working("settings/mappings/1-default.yaml")) == "name: default\n"
    assert (repo.root / ".gitignore").read_text() == ".dsmd/\n"
    assert repo.config() == {"markdown": True, "kicad": True, "sqlite": True}
    assert (repo.root / "library" / "datasheets.sqlite").exists()
    assert sync.status(repo)["clean"]


def test_state_round_trip(repo):
    repo.state["parts"][PART]["files"]["x"] = "R1:abc"
    repo.save()
    again = Repo.find(repo.root / "parts")
    assert again.state == repo.state


def test_lock_refuses_a_second_writer(repo):
    with repo.lock():
        with pytest.raises(RepoError):
            with Repo.find(repo.root).lock():
                pass
    (repo.dot / "lock").write_text("999999999")          # a dead pid is taken over
    with repo.lock():
        pass


def test_status_classification(repo):
    repo.write(repo.working(f"{PATH}/parameters.jsonl"), "{}\n")
    repo.write(repo.working(f"{PATH}/part.yaml"), "uuid: changed\n")
    repo.write(repo.working(f"{PATH}/pins.csv"), "<<<<<<< local\n=======\n>>>>>>> server\n")
    repo.working(f"{PATH}/cad.yaml").unlink()
    repo.write(repo.working("parts/X/Y/notes.txt"), "mine")
    repo.write(repo.working("library/sym-lib-table"), "edited")
    report = sync.status(repo)
    assert states(repo) == {f"{PATH}/parameters.jsonl": "modified", f"{PATH}/part.yaml": "read-only-edited",
                            f"{PATH}/pins.csv": "conflicted", f"{PATH}/cad.yaml": "deleted"}
    assert report["untracked"] == ["parts/X/Y"]
    assert report["generated_edited"] == ["library/sym-lib-table"]


def test_diff_and_restore(repo):
    rel = f"{PATH}/parameters.jsonl"
    repo.write(repo.working(rel), repo.read(repo.working(rel)).replace('"1.9"', '"2.0"'))
    text = sync.diff(repo, "SN74HC00N")
    assert '-{"k":"a1"' in text and '+{"k":"a1"' in text
    assert sync.restore(repo, PATH) == [rel]
    assert sync.diff(repo) == ""


def test_push_applies_and_takes_the_server_version(repo, server):
    rel = f"{PATH}/parameters.jsonl"
    edited = repo.read(repo.working(rel)).replace('"1.9"', '"2.0"')
    repo.write(repo.working(rel), edited)
    result = sync.push(repo, server, "fix VOH")
    assert [r["path"] for r in result["applied"]] == [PATH]
    sent = server.pushes[0]["parts"][0]
    assert sent["uuid"] == PART and list(sent["files"]) == ["parameters.jsonl"]
    assert sent["files"]["parameters.jsonl"]["base_etag"] != etag(edited)
    assert repo.read(repo.base(rel)) == edited and repo.parts()[PART]["files"]["parameters.jsonl"] == etag(edited)
    assert sync.status(repo)["clean"]


def test_settings_are_editable_and_pushed_first(repo, server):
    rel = "settings/mappings/1-default.yaml"
    repo.write(repo.working(rel), "name: default\ncustom_categories: [Gate drivers]\n")
    assert sync.status(repo)["settings"] == [{"path": rel, "state": "modified"}]
    result = sync.push(repo, server, "add a category")
    assert result["applied"] == [{"path": rel, "errors": []}]
    sent = server.pushes[0]
    assert sent["parts"] == [] and list(sent["settings"]) == [rel]
    assert server.settings[rel] == repo.read(repo.base(rel))
    assert sync.status(repo)["clean"]


def test_every_listed_mapping_is_pulled_and_editable(repo, server):
    rel = "settings/mappings/9-intel-key-only.yaml"
    server.settings[rel] = "name: Intel (Key only)\nactive_for: [kicad_export]\n"
    sync.pull(repo, server)
    assert "kicad_export" in repo.read(repo.working(rel))
    repo.write(repo.working(rel), "name: Intel (Key only)\nactive_for: [kicad_export]\ncustom_units: []\n")
    assert sync.status(repo)["settings"] == [{"path": rel, "state": "modified"}]
    assert sync.push(repo, server, "edit")["applied"] == [{"path": rel, "errors": []}]
    assert list(server.pushes[0]["settings"]) == [rel]


def test_setting_gone_from_server_is_removed_unless_edited(repo, server):
    clean, edited = "settings/mappings/1-default.yaml", "settings/mapping.yaml"
    server.settings[edited] = "name: old layout\n"
    sync.pull(repo, server)
    repo.write(repo.working(edited), "name: old layout\ncustom_units: []\n")
    del server.settings[clean], server.settings[edited]
    report = sync.pull(repo, server)
    assert not repo.working(clean).exists() and not repo.working(edited).exists()
    assert "custom_units" in repo.read(repo.working(edited + ".local"))
    assert edited in report["warnings"][0] and "local edits" in report["warnings"][0]
    assert not ({clean, edited} & set(repo.state["settings"]))
    assert sync.status(repo)["settings"] == []


def test_new_mapping_file_is_pushed_and_takes_the_servers_name(repo, server):
    rel, served = "settings/mappings/mine.yaml", "settings/mappings/2-mine.yaml"
    repo.write(repo.working(rel), "name: Mine\n")
    assert sync.status(repo)["settings"] == [{"path": rel, "state": "new"}]
    assert "+name: Mine" in sync.diff(repo)
    assert sync.push(repo, server, "add")["applied"] == [{"path": rel, "errors": []}]
    assert server.pushes[0]["settings"][rel] == {"base_etag": None, "content": "name: Mine\n"}
    assert not repo.working(rel).exists() and repo.read(repo.working(served)) == "name: Mine\n"
    assert served in repo.state["settings"] and rel not in repo.state["settings"]
    assert sync.status(repo)["clean"]


def test_deleting_a_mapping_file_deletes_on_push_and_restore_undoes_it(repo, server):
    rel = "settings/mappings/9-old.yaml"
    server.settings[rel] = "name: Old\n"
    sync.pull(repo, server)
    repo.working(rel).unlink()
    assert sync.status(repo)["settings"] == [{"path": rel, "state": "deleted"}]
    sync.restore(repo, rel)
    assert sync.status(repo)["clean"]
    repo.working(rel).unlink()
    assert sync.push(repo, server, "drop")["applied"] == [{"path": rel, "errors": []}]
    assert server.pushes[0]["settings"][rel]["delete"] is True and rel not in server.settings
    assert rel not in repo.state["settings"] and sync.status(repo)["clean"]


def test_deleting_the_symbol_style_sends_nothing(repo, server):
    repo.working("settings/symbol_style.yaml").unlink()
    sync.push(repo, server, "nothing")
    assert server.pushes == []


def test_rejected_new_mapping_stays_new(repo, server, monkeypatch):
    rel = "settings/mappings/bad.yaml"
    repo.write(repo.working(rel), "description: no name\n")
    monkeypatch.setattr(server, "post", lambda path, body=None, **_: {"parts": [], "settings": [
        {"path": rel, "status": "rejected", "errors": [{"file": rel, "error": "a new mapping needs a name"}], "files": {}}]})
    assert sync.push(repo, server, "add")["rejected"][0]["path"] == rel
    assert repo.working(rel).exists()
    assert sync.status(repo)["settings"] == [{"path": rel, "state": "new"}]


def test_a_kept_edit_of_a_gone_mapping_does_not_read_as_new(repo, server):
    rel = "settings/mappings/9-old.yaml"
    server.settings[rel] = "name: Old\n"
    sync.pull(repo, server)
    repo.write(repo.working(rel), "name: Old\ncustom_units: []\n")
    del server.settings[rel]
    sync.pull(repo, server)
    assert sync.status(repo)["settings"] == []


def test_push_of_a_rename_moves_the_file(repo, server, monkeypatch):
    old, new = "settings/mappings/1-default.yaml", "settings/mappings/1-renamed.yaml"
    repo.write(repo.working(old), "name: renamed\n")
    real_post = server.post

    def post(path, body=None, **kwargs):
        response = real_post(path, body, **kwargs)
        if path.endswith("/push/"):
            server.settings[new] = server.settings.pop(old)
            for outcome in response["settings"]:
                outcome["files"] = {new: outcome["files"][old]}
        return response
    monkeypatch.setattr(server, "post", post)
    sync.push(repo, server, "rename")
    assert not repo.working(old).exists() and repo.read(repo.working(new)) == "name: renamed\n"
    assert list(repo.state["settings"]).count(new) == 1 and old not in repo.state["settings"]
    assert sync.status(repo)["clean"]


def test_settings_merge_by_key_on_pull(repo, server):
    rel = "settings/symbol_style.yaml"
    repo.write(repo.working(rel), "style: {}\nreference_designators: {ICs: U}\n")
    server.settings[rel] = "style: {pin_length: 2}\n"
    report = sync.pull(repo, server)
    assert report["conflicts"] == [] and report["merged"] == [rel]
    text = repo.read(repo.working(rel))
    assert "pin_length: 2" in text and "ICs: U" in text


    repo.write(repo.working(f"{PATH}/part.yaml"), "mine\n")
    repo.write(repo.working(f"{PATH}/datasheet.md"), "mine\n")
    repo.write(repo.working(f"{PATH}/pins.csv"), "changed\n")
    with pytest.raises(sync.PushRefused):
        sync.push(repo, server, "x")
    assert server.pushes == []


def test_push_reports_stale_and_rejected(repo, server):
    rel = f"{PATH}/parameters.jsonl"
    repo.write(repo.working(rel), repo.read(repo.working(rel)).replace('"1.9"', '"2.0"'))
    server.reject = "unconfirm is not supported"
    assert sync.push(repo, server, "x")["rejected"][0]["errors"][0]["line"] == 2
    server.reject = None
    server.parts[PART]["files"]["parameters.jsonl"] += '{"k":"z","section":"power","name":"P","value":"1"}\n'
    assert len(sync.push(repo, server, "x")["stale"]) == 1
    assert states(repo) == {rel: "modified"}


def test_pull_merges_rows_and_conflicts_on_the_same_row(repo, server):
    rel = f"{PATH}/parameters.jsonl"
    repo.write(repo.working(rel), repo.read(repo.working(rel)).replace('"VOL","value":"0.1"', '"VOL","value":"L"'))
    files = server.parts[PART]["files"]
    files["parameters.jsonl"] = files["parameters.jsonl"].replace('"VOH","value":"1.9"', '"VOH","value":"S"')
    report = sync.pull(repo, server)
    assert report["conflicts"] == [] and report["merged"] == [rel]
    text = repo.read(repo.working(rel))
    assert '"S"' in text and '"L"' in text
    assert repo.read(repo.base(rel)) == files["parameters.jsonl"]
    # now the same row on both sides
    files["parameters.jsonl"] = files["parameters.jsonl"].replace('"VOL","value":"0.1"', '"VOL","value":"S2"')
    report = sync.pull(repo, server)
    assert report["conflicts"] == [{"path": rel, "conflicts": 1}]
    assert states(repo) == {rel: "conflicted"}
    with pytest.raises(RepoError):
        sync.pull(repo, server)                         # resolve first


def test_pull_saves_a_read_only_edit_aside(repo, server):
    rel = f"{PATH}/part.yaml"
    repo.write(repo.working(rel), "mine\n")
    server.parts[PART]["files"]["part.yaml"] += "cad_revision: 2\n"
    report = sync.pull(repo, server)
    assert report["saved_local"] == [rel + ".local"]
    assert repo.read(repo.working(rel + ".local")) == "mine\n"
    assert repo.read(repo.working(rel)) == server.parts[PART]["files"]["part.yaml"]


def test_pull_moves_a_renamed_part_and_drops_a_removed_one(repo, server):
    server.parts[PART]["path"] = "parts/Texas_Instruments/SN74HC00N"
    report = sync.pull(repo, server)
    assert report["moved_parts"] == 1
    assert repo.working("parts/Texas_Instruments/SN74HC00N/pins.csv").exists()
    assert not repo.working(PATH).exists() and not repo.base(PATH).exists()
    assert repo.parts()[PART]["path"] == "parts/Texas_Instruments/SN74HC00N"
    server.parts.clear()
    sync.pull(repo, server)
    assert repo.parts() == {} and not repo.working("parts/Texas_Instruments").exists()


def test_pull_keeps_a_removed_part_with_local_edits(repo, server):
    rel = f"{PATH}/pins.csv"
    repo.write(repo.working(rel), "edited\n")
    server.parts.clear()
    report = sync.pull(repo, server)
    assert report["kept"] == [PATH] and repo.read(repo.working(rel)) == "edited\n"
    assert sync.status(repo)["untracked"] == [PATH]


def test_markdown_off_drops_the_datasheets(repo, server):
    repo.set_config(markdown=False)
    sync.pull(repo, server)
    assert not repo.working("datasheets").exists() and repo.state["datasheets"] == {}


def test_datasheet_is_one_folder_shared_by_the_family(repo, server):
    images = repo.working(f"{SHEET}/images")
    assert (images / "ff44.png").read_bytes() == b"PNG1"
    assert "{{figure:ff44" in repo.read(repo.working(f"{SHEET}/datasheet.md"))
    assert not repo.working(f"{PATH}/datasheet.md").exists() and not repo.working(f"{PATH}/images").exists()
    assert sync.status(repo)["clean"]
    sheet = server.datasheets[SHEET]
    sheet["text"] += "{{figure:aa11 bbox=0,0,1,1}}\n![Pins](images/aa11.png)\n"
    sheet["images"] = {"aa11.png": b"PNG2"}
    assert sync.pull(repo, server)["changed_datasheets"] == 1
    assert sorted(p.name for p in images.iterdir()) == ["aa11.png"]
    assert "aa11" in repo.read(repo.working(f"{SHEET}/datasheet.md"))
    assert sync.pull(repo, server)["changed_datasheets"] == 0
    server.datasheets.clear()
    sync.pull(repo, server)
    assert not repo.working("datasheets").exists() and not repo.base("datasheets").exists()


def test_edited_datasheet_is_read_only(repo, server):
    rel = f"{SHEET}/datasheet.md"
    repo.write(repo.working(rel), "mine\n")
    assert sync.status(repo)["datasheets"] == [{"path": rel, "state": "read-only-edited"}]
    assert rel in sync._selected(repo, None)
    with pytest.raises(sync.PushRefused):
        sync.push(repo, server, "x")
    server.datasheets[SHEET]["text"] += "more\n"
    assert sync.pull(repo, server)["saved_local"] == [rel + ".local"]
    assert repo.read(repo.working(rel)) == server.datasheets[SHEET]["text"]


def test_pull_drops_the_per_part_copies_of_an_older_clone(repo, server):
    rel = f"{PATH}/datasheet.md"
    repo.put(rel, "old copy\n")
    repo.write(repo.working(f"{PATH}/images/ff44.png"), "PNG")
    repo.state["parts"][PART]["files"]["datasheet.md"] = "R1:mdk"
    repo.save()
    sync.pull(repo, server)
    assert not repo.working(rel).exists() and not repo.base(rel).exists()
    assert not repo.working(f"{PATH}/images").exists()
    assert "datasheet.md" not in repo.parts()[PART]["files"] and repo.working(f"{PATH}/pins.csv").exists()


def test_cli_status_json(repo, capsys):
    assert cli.main(["--json", "-C", str(repo.root), "status"]) == 0
    assert json.loads(capsys.readouterr().out)["clean"] is True
    assert cli.main(["status", "--json", "-C", str(repo.root)]) == 0
    assert json.loads(capsys.readouterr().out)["clean"] is True


def test_cli_outside_a_working_copy(tmp_path, capsys):
    assert cli.main(["-C", str(tmp_path), "status"]) == 1
    assert "not a dsmd working copy" in capsys.readouterr().err


def test_categories_and_feedback(server):
    assert [c["name"] for c in sync.categories(server)] == ["ICs", "Logic"]
    sync.feedback(server, "need a footprint style setting", "suggestion")
    sent = server.feedback[0]
    assert sent["kind"] == "suggestion" and sent["tool"] == "dsmd" and sent["message"].startswith("need")


def test_incremental_pull_reads_only_changes_and_deletions(tmp_path, server):
    server.incremental = True
    repo = Repo.find(sync.clone(server, "7", tmp_path / "wc")["root"])
    assert repo.state["cursor"] == "0.0"
    sync.pull(repo, server)
    assert server.since_seen[-1] == "0.0" and PART in repo.parts()  # absent from a delta is not deleted
    server.parts[PART]["files"]["pins.csv"] += "pin-c,3,EN,input,true,\n"
    server.touch(PART)
    report = sync.pull(repo, server)
    assert report["changed_files"] == 1 and "pin-c" in repo.read(repo.working(f"{PATH}/pins.csv"))
    assert repo.state["cursor"] == "1.0"
    server.delete(PART)
    assert sync.pull(repo, server)["removed_parts"] == 1
    assert repo.parts() == {} and repo.state["cursor"] == "1.1"


def _review_rows(repo):
    rel = f"{PATH}/parameters.jsonl"
    text = repo.read(repo.working(rel)) + jsonl(row("cad:pin_map", "Pin mapping", "2 symbol pins, 0 pads", section="cad_review"),
                                                row("cad:symbol", "Symbol", "Generated", section="cad_review"))
    repo.put(rel, text)
    return rel


def test_confirm_lists_and_flips_only_the_review_word(repo):
    rel = _review_rows(repo)
    listed = sync.confirm(repo, "SN74HC00N")
    assert [i["item"] for i in listed["items"]] == ["pin_map", "symbol"] and not listed["changed"]
    done = sync.confirm(repo, "SN74HC00N", ["pin_map", "a1"])
    assert done["changed"] == ["cad:pin_map", "a1"]
    changed = [line for line in sync.diff(repo).splitlines() if line[:1] in "+-" and line[1:2] == "{"]
    assert sorted(line.replace("confirmed", "unchecked")[1:] for line in changed if line[0] == "+") == \
        sorted(line[1:] for line in changed if line[0] == "-")
    assert sync.confirm(repo, "SN74HC00N", ["pin_map"])["changed"] == []
    sync.confirm(repo, "SN74HC00N", ["pin_map", "a1"], undo=True)
    assert sync.diff(repo) == "" and repo.read(repo.working(rel)) == repo.read(repo.base(rel))


def test_confirm_names_the_items_it_knows(repo):
    _review_rows(repo)
    with pytest.raises(RepoError, match="pin_map, symbol"):
        sync.confirm(repo, "SN74HC00N", ["footprint"])


def test_cli_confirm(repo, capsys):
    _review_rows(repo)
    assert cli.main(["-C", str(repo.root), "confirm", "SN74HC00N", "symbol"]) == 0
    assert "confirmed cad:symbol" in capsys.readouterr().out.replace("  ", " ").replace("  ", " ")
    assert cli.main(["--json", "-C", str(repo.root), "confirm", "SN74HC00N"]) == 0
    items = json.loads(capsys.readouterr().out)["items"]
    assert {i["item"]: i["review"] for i in items} == {"pin_map": "unchecked", "symbol": "confirmed"}
