"""The launcher must not need Qt, and a refused folder must not stop or hide anything."""
import sys
from pathlib import Path

import pytest

from datasheets_md import auth, gui, update


def test_open_log_falls_back_when_the_folder_is_refused(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setattr(gui.tempfile, "gettempdir", lambda: str(tmp_path))
    path, log = gui.open_log(blocker / "sub" / "gui.log")
    log.close()
    assert path == tmp_path / "dsmd-gui.log"


def test_open_log_gives_up_quietly(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setattr(gui.tempfile, "gettempdir", lambda: str(blocker))
    assert gui.open_log(blocker / "gui.log") == (None, gui.subprocess.DEVNULL)


def test_main_reports_an_import_failure(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "datasheets_md.gui.app", None)
    assert gui.main() == 1
    assert "Error" in capsys.readouterr().err


def test_saving_credentials_to_a_refused_folder_is_an_auth_error(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setattr(auth, "credentials_path", lambda: blocker / "sub" / "credentials.json")
    with pytest.raises(auth.AuthError, match="cannot save the sign-in"):
        auth._save({"servers": {}})


def test_everything_lives_in_the_home_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert auth.credentials_path() == tmp_path / ".datasheets-md" / "credentials.json"
    assert update._cache_file() == tmp_path / ".datasheets-md" / "update-check.json"
