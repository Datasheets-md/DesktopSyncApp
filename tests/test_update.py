import json
import sys
import time

import pytest

from datasheets_md import cli, update
from datasheets_md.http import _describe


class Resp:
    def __init__(self, releases):
        self.body = {"releases": releases}

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


def file(yanked=False):
    return [{"filename": "x.whl", "yanked": yanked}]


RELEASES = {"1.9.0": file(), "2.0.0a9": file(), "2.0.0a10": file(), "2.0.0b1": file(yanked=True),
            "2.0.0rc1": [], "nonsense": file()}


@pytest.fixture
def index(monkeypatch):
    calls = []

    def get(url, **kw):
        calls.append(url)
        return Resp(dict(RELEASES))
    monkeypatch.setattr(update.requests, "get", get)
    return calls


@pytest.mark.parametrize("current, latest", [("2.0.0a9", "2.0.0a10"), ("1.8.0", "1.9.0")])
def test_latest_offers_pre_releases_only_to_pre_release_installs(index, monkeypatch, current, latest):
    monkeypatch.setattr(update, "__version__", current)
    assert update.latest_version() == latest


def test_a_final_release_beats_the_pre_releases(index, monkeypatch):
    monkeypatch.setattr(update, "__version__", "2.0.0a9")
    RELEASES["2.0.0"] = file()
    try:
        assert update.latest_version() == "2.0.0"
    finally:
        del RELEASES["2.0.0"]


def test_notice_asks_once_a_day_and_survives_being_offline(index, monkeypatch, tmp_path):
    monkeypatch.setattr(update, "__version__", "2.0.0a9")
    monkeypatch.setattr(update, "_cache_file", lambda: tmp_path / "check.json")
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("DSMD_NO_UPDATE_CHECK", raising=False)
    assert "dsmd 2.0.0a10 is available" in update.notice()
    assert update.notice() and len(index) == 1           # cached

    cached = json.loads((tmp_path / "check.json").read_text())
    (tmp_path / "check.json").write_text(json.dumps({**cached, "checked_at": time.time() - 2 * update.CHECK_EVERY}))
    monkeypatch.setattr(update.requests, "get", lambda *a, **k: (_ for _ in ()).throw(update.requests.ConnectionError()))
    assert update.notice() is None                       # offline: no line, no error, and cached
    assert json.loads((tmp_path / "check.json").read_text())["latest"] is None


def test_every_command_ends_with_the_notice_also_for_agents(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(update, "available", lambda: "9.9.9")
    assert cli.main(["--json", "-C", str(tmp_path), "status"]) == cli.EXIT_ERROR     # not a working copy
    err = capsys.readouterr().err
    assert err.rstrip().endswith("run: dsmd update") and "9.9.9 is available" in err
    assert cli.main(["--json", "help", "agents"]) == 0
    out = capsys.readouterr()
    assert json.loads(out.out)["agents"] and "dsmd update" in out.err             # stdout stays one document
    assert cli.main(["-q", "help", "agents"]) == 0 and "dsmd update" not in capsys.readouterr().err


def test_notice_is_off_in_ci(index, monkeypatch):
    monkeypatch.setenv("CI", "true")
    assert update.notice() is None and not index


@pytest.fixture
def prefix(monkeypatch, tmp_path):
    env = tmp_path / "venvs" / "datasheets-md-gui"
    env.mkdir(parents=True)
    monkeypatch.setattr(sys, "prefix", str(env))
    monkeypatch.setattr(update.shutil, "which", lambda name: f"/bin/{name}")
    return env


def test_pipx_and_uv_installs_upgrade_through_their_tool(prefix):
    (prefix / "pipx_metadata.json").write_text("{}")
    assert update.upgrade_command("2.1.0") == (["/bin/pipx", "upgrade", "datasheets-md-gui"], None)
    (prefix / "pipx_metadata.json").unlink()
    (prefix / "uv-receipt.toml").write_text("")
    assert update.upgrade_command("2.1.0") == (["/bin/uv", "tool", "upgrade", "datasheets-md-gui"], None)


def test_a_venv_upgrades_through_its_own_pip(prefix, monkeypatch):
    monkeypatch.setattr(sys, "base_prefix", "/usr")
    monkeypatch.setattr(update, "_installed_package", lambda: "datasheets-md")
    monkeypatch.setenv("DSMD_UPDATE_INDEX", "https://test.pypi.org/")
    argv, why_not = update.upgrade_command("2.1.0")
    assert argv[:6] == [sys.executable, "-m", "pip", "install", "--upgrade", "datasheets-md==2.1.0"]
    assert argv[6:] == ["--index-url", "https://test.pypi.org/simple/", "--extra-index-url", "https://pypi.org/simple/"]


def test_an_os_managed_python_is_refused(prefix, monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "base_prefix", sys.prefix)
    (tmp_path / "EXTERNALLY-MANAGED").write_text("")
    monkeypatch.setattr(update.sysconfig, "get_path", lambda name, *a: str(tmp_path))
    argv, why_not = update.upgrade_command("2.1.0")
    assert argv is None and "pipx install" in why_not


def test_update_runs_the_upgrade(monkeypatch, capsys):
    monkeypatch.setattr(update, "__version__", "2.0.0")
    monkeypatch.setattr(update, "latest_version", lambda timeout: "2.1.0")
    monkeypatch.setattr(update, "upgrade_command", lambda v: (["pip", "install", f"x=={v}"], None))
    ran = []
    monkeypatch.setattr(update, "run", lambda argv: ran.append(argv) or 0)
    monkeypatch.setattr(update, "installed_version", lambda: "2.1.0")
    assert cli.main(["update", "--check", "--json"]) == 0 and not ran
    assert json.loads(capsys.readouterr().out)["latest"] == "2.1.0"
    assert cli.main(["self-update", "--json"]) == 0            # the name before 2.4.2 still works
    assert ran == [["pip", "install", "x==2.1.0"]]
    assert json.loads(capsys.readouterr().out)["updated"] is True


def test_an_upgrade_that_changed_nothing_is_an_error(monkeypatch, capsys):
    monkeypatch.setattr(update, "__version__", "2.0.0")
    monkeypatch.setattr(update, "latest_version", lambda timeout: "2.1.0")
    monkeypatch.setattr(update, "upgrade_command", lambda v: (["uv", "tool", "upgrade", "x"], None))
    monkeypatch.setattr(update, "run", lambda argv: 0)      # "Nothing to upgrade", exit 0
    monkeypatch.setattr(update, "installed_version", lambda: "2.0.0")
    assert cli.main(["update"]) == cli.EXIT_ERROR
    assert "still 2.0.0" in capsys.readouterr().err


def test_update_refusal_is_an_error(monkeypatch):
    monkeypatch.setattr(update, "__version__", "2.0.0")
    monkeypatch.setattr(update, "latest_version", lambda timeout: "2.1.0")
    monkeypatch.setattr(update, "upgrade_command", lambda v: (None, "managed by the OS"))
    assert cli.main(["update", "-q"]) == cli.EXIT_ERROR


def test_server_426_says_how_to_update():
    class R:
        status_code, reason = 426, "Upgrade Required"

        def json(self):
            return {"error": "client_upgrade_required", "message": "this server needs dsmd 2.0.0 or newer; run dsmd self-update"}
    err = _describe(R())
    assert err.status == 426 and "dsmd self-update" in str(err)
