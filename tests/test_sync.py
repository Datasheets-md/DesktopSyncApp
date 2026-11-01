import json

import pytest
from conftest import PART, PATH, etag

from datasheets_md import cli, sync
from datasheets_md.repo import Repo, RepoError


def states(repo):
    return {f["path"]: f["state"] for p in sync.status(repo)["parts"] for f in p["files"]}


def test_clone_writes_working_base_state(repo, server):
    for name, text in server.parts[PART]["files"].items():
        rel = f"{PATH}/{name}"
        assert repo.read(repo.working(rel)) == text == repo.read(repo.base(rel))
        assert repo.parts()[PART]["files"][name] == etag(text)
    assert repo.read(repo.working("settings/mapping.yaml")) == "name: default\n"
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


def test_push_refuses_read_only_edits(repo, server):
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


def test_markdown_off_drops_datasheet_md(repo, server):
    repo.set_config(markdown=False)
    sync.pull(repo, server)
    assert not repo.working(f"{PATH}/datasheet.md").exists()
    assert "datasheet.md" not in repo.parts()[PART]["files"]
    assert not repo.working(f"{PATH}/images").exists()


def test_markdown_comes_with_its_figures_and_follows_server_changes(repo, server):
    images = repo.working(f"{PATH}/images")
    assert (images / "ff44.png").read_bytes() == b"PNG1"
    assert "{{figure:ff44" in repo.read(repo.working(f"{PATH}/datasheet.md"))
    assert sync.status(repo)["clean"]
    part = server.parts[PART]
    part["files"]["datasheet.md"] += "{{figure:aa11 bbox=0,0,1,1}}\n![Pins](images/aa11.png)\n"
    part["images"] = {"aa11.png": b"PNG2"}
    sync.pull(repo, server)
    assert sorted(p.name for p in images.iterdir()) == ["aa11.png"]
    assert "aa11" in repo.read(repo.working(f"{PATH}/datasheet.md"))
    server.parts.clear()
    sync.pull(repo, server)
    assert not repo.working(PATH).exists()


def test_cli_status_json(repo, capsys):
    assert cli.main(["--json", "-C", str(repo.root), "status"]) == 0
    assert json.loads(capsys.readouterr().out)["clean"] is True
    assert cli.main(["status", "--json", "-C", str(repo.root)]) == 0
    assert json.loads(capsys.readouterr().out)["clean"] is True


def test_cli_outside_a_working_copy(tmp_path, capsys):
    assert cli.main(["-C", str(tmp_path), "status"]) == 1
    assert "not a dsmd working copy" in capsys.readouterr().err
