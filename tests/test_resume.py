"""Stopping a clone keeps its work, library failures are reported, CAD is read in pages."""
import pytest

from datasheets_md import cli, library, sync
from datasheets_md.http import ApiError, Client
from datasheets_md.repo import Repo, RepoError

from conftest import PART, PATH, SHEET


class Down:
    """The server answering `path` with an error, for as long as it is set."""

    def __init__(self, server, monkeypatch):
        self.server, self.path, self.after, self.status = server, None, 0, 504
        self.asked = []
        get, post = server.get, server.post

        def fail(path):
            if self.path and path.startswith(self.path) or self.path and path.endswith(self.path):
                if self.after <= 0:
                    raise ApiError(f"HTTP {self.status}: Gateway Time-out", self.status)
                self.after -= 1

        def fake_get(path, params=None, **kw):
            fail(path)
            return get(path, params, **kw)

        def fake_post(path, body=None, **kw):
            fail(path)
            if path.endswith("/files/"):
                self.asked += body["paths"]
            return post(path, body, **kw)
        monkeypatch.setattr(server, "get", fake_get)
        monkeypatch.setattr(server, "post", fake_post)


@pytest.fixture
def down(server, monkeypatch):
    return Down(server, monkeypatch)


def stopped_clone(server, folder):
    with pytest.raises(ApiError):
        sync.clone(server, "7", folder)
    return Repo.find(folder)


def test_a_stopped_clone_keeps_its_files_and_the_same_command_continues(server, down, monkeypatch, tmp_path):
    monkeypatch.setattr(sync, "BATCH_FILES", 1)
    folder = tmp_path / "wc"
    down.path, down.after = "/files/", 2
    repo = stopped_clone(server, folder)
    kept = down.asked[:2]
    assert kept and all(repo.read(repo.working(p)) is not None for p in kept)   # on disk, before the failure
    assert repo.state["incomplete"] and not repo.state.get("cursor")

    report = sync.status(repo)
    assert not report["clean"] and str(folder) in report["incomplete"]
    for refused in (lambda: sync.fetch(repo, server), lambda: sync.push(repo, server, "m"),
                    lambda: sync.part_rm(repo, server, PART)):
        with pytest.raises(RepoError, match="did not finish"):
            refused()

    down.path, down.asked = None, []
    done = sync.clone(server, "7", folder)
    assert not set(kept) & set(down.asked)                                       # not asked for again
    assert done["library_error"] is None and done["files"] == len(server.parts[PART]["files"]) + len(server.settings)
    repo = Repo.find(folder)
    assert "incomplete" not in repo.state and sync.status(repo)["clean"]
    for name, text in server.parts[PART]["files"].items():
        assert repo.read(repo.working(f"{PATH}/{name}")) == text


def test_datasheets_written_before_a_failure_are_kept(server, down, tmp_path):
    repo_dir = tmp_path / "wc"
    down.path = "/download-markdown/"
    repo = stopped_clone(server, repo_dir)
    assert repo.read(repo.working(f"{PATH}/parameters.jsonl")) is not None       # the parts came first
    down.path = None
    sync.clone(server, "7", repo_dir)
    repo = Repo.find(repo_dir)
    assert repo.read(repo.working(f"{SHEET}/datasheet.md")) == server.datasheets[SHEET]["text"]


def test_continuing_follows_what_moved_or_left_on_the_server(server, down, monkeypatch, tmp_path):
    monkeypatch.setattr(sync, "BATCH_FILES", 1)
    folder = tmp_path / "wc"
    down.path, down.after = "/files/", 2
    repo = stopped_clone(server, folder)
    down.path = None
    server.parts[PART]["path"] = "parts/TI/MOVED"
    sync.clone(server, "7", folder)
    repo = Repo.find(folder)
    assert repo.parts()[PART]["path"] == "parts/TI/MOVED" and not repo.working(PATH).exists()
    assert sync.status(repo)["clean"]

    other = tmp_path / "wc2"
    down.path, down.after = "/files/", 2
    stopped_clone(server, other)
    down.path = None
    del server.parts[PART]
    sync.clone(server, "7", other)
    assert PART not in Repo.find(other).parts() and not (other / "parts/TI/MOVED").exists()


def test_a_finished_folder_is_never_cloned_over(repo, server):
    with pytest.raises(RepoError, match="not empty"):
        sync.clone(server, "7", repo.root)


def test_another_workspaces_unfinished_clone_is_not_continued(server, down, tmp_path):
    folder = tmp_path / "wc"
    down.path = "/files/"
    repo = stopped_clone(server, folder)
    repo.state["workspace_id"] = 99
    repo.save()
    down.path = None
    with pytest.raises(RepoError, match="not empty"):
        sync.clone(server, "7", folder)


CAD = "/workspace/components/cad-export/"


def test_a_failed_library_leaves_a_finished_clone_and_the_next_pull_builds_it(server, down, tmp_path):
    down.path = CAD
    done = sync.clone(server, "7", tmp_path / "wc")
    assert "dsmd pull --library" in done["library_error"]
    repo = Repo.find(tmp_path / "wc")
    assert "incomplete" not in repo.state
    assert repo.read(repo.working(f"{SHEET}/datasheet.md")) is not None
    report = sync.status(repo)
    assert report["library_missing"] and report["nothing_to_push"] and not report["clean"]

    assert "not built" in sync.pull(repo, server)["library_error"]               # no changes, still tried
    down.path = None
    assert sync.pull(repo, server)["library_error"] is None                      # no changes, retried
    assert sync.status(repo)["clean"]


def test_an_empty_library_from_an_older_failed_clone_counts_as_missing(repo):
    for item in repo.working("library").iterdir():
        item.unlink() if item.is_file() else None
    if any(repo.working("library").iterdir()):
        pytest.skip("library holds folders here")
    assert sync.status(repo)["library_missing"]


def test_cli_says_what_a_failed_library_and_a_stopped_clone_mean(server, down, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "_client", lambda *a, **k: server)
    down.path = CAD
    assert cli.main(["clone", "7", str(tmp_path / "wc")]) == cli.EXIT_PARTIAL
    assert "library/ was not built" in capsys.readouterr().out
    assert cli.main(["-C", str(tmp_path / "wc"), "status"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "library/ is not built" in out and "working copy clean" not in out and "Nothing to push." in out

    down.path = "/files/"
    assert cli.main(["clone", "7", str(tmp_path / "wc2")]) == cli.EXIT_ERROR
    assert "Run the same dsmd clone command" in capsys.readouterr().err


def test_cad_is_read_in_pages_without_retrying_a_timeout():
    calls = []

    class Pages:
        def get(self, path, params=None, **kw):
            calls.append((params["page"], kw))
            return {"parts": [{"uuid": params["page"]}], "total_count": 2, "has_next": params["page"] < 2}
    said = []
    assert library.fetch_cad(Pages(), said.append) == [{"uuid": 1}, {"uuid": 2}]
    assert [c[0] for c in calls] == [1, 2] and all(c[1] == {"retry_timeouts": False} for c in calls)
    assert said == ["  library/ CAD 1/2", "  library/ CAD 2/2"]


def test_a_server_without_paging_answers_once():
    class Whole:
        def get(self, path, params=None, **kw):
            return {"parts": [1, 2, 3]}
    assert library.fetch_cad(Whole(), lambda _: None) == [1, 2, 3]


class Resp:
    status_code, content, reason, headers = 504, b"", "Gateway Time-out", {}

    def json(self):
        raise ValueError


class Session:
    headers = {}

    def __init__(self):
        self.calls = 0

    def request(self, *a, **k):
        self.calls += 1
        return Resp()


class Token:
    def token(self):
        return "t"

    def refresh(self):
        return False


@pytest.mark.parametrize("kw, calls", [({"retry_timeouts": False}, 1), ({}, 4)])
def test_a_504_is_retried_only_when_the_call_allows_it(monkeypatch, kw, calls):
    monkeypatch.setattr("datasheets_md.http.time.sleep", lambda _: None)
    session = Session()
    with pytest.raises(ApiError) as err:
        Client("https://x", Token(), session=session).get("/y", **kw)
    assert err.value.status == 504 and session.calls == calls
