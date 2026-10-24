"""dBsync: the desktop face of dsmd (Spec 27a 4).

Every button calls the same `sync` function as the CLI, in a worker thread,
and the log pane shows the same lines the CLI prints. No logic lives here
that the CLI does not have.
"""
import json
import os
import sys
from pathlib import Path

import platformdirs
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .. import APP_NAME, __version__, auth, sync
from ..http import ApiError, Client
from ..repo import Repo, RepoError

PREFS = Path(platformdirs.user_config_dir("datasheets-md")) / "gui.json"


def _resource(name):
    base = getattr(sys, "_MEIPASS", None)
    return os.path.join(base, name) if base else str(Path(__file__).with_name(name))


def _prefs():
    try:
        return json.loads(PREFS.read_text())
    except (OSError, ValueError):
        return {}


def _save_prefs(**values):
    PREFS.parent.mkdir(parents=True, exist_ok=True)
    PREFS.write_text(json.dumps({**_prefs(), **values}, indent=2))


class Worker(QThread):
    line = Signal(str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, job):
        super().__init__()
        self.job = job

    def run(self):
        try:
            self.done.emit(self.job(self.line.emit))
        except (ApiError, RepoError, auth.AuthError) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # the window must survive anything a job raises
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class Window(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(720, 640)
        if os.path.exists(_resource("icon.png")):
            self.setWindowIcon(QIcon(_resource("icon.png")))
        prefs = _prefs()
        self.repo = None
        self.worker = None

        self.server = QLineEdit(prefs.get("server") or auth.default_server())
        self.who = QLabel("Not signed in")
        self.sign_in = QPushButton("Sign in")
        self.sign_out = QPushButton("Sign out")
        self.workspace = QComboBox()
        self.folder = QLineEdit(prefs.get("folder", ""))
        browse = QPushButton("Browse...")
        self.open_button = QPushButton("Clone / Open")
        self.toggles = {key: QCheckBox(text) for key, text in
                        (("markdown", "Markdown datasheets"), ("kicad", "KiCad library"), ("sqlite", "SQLite database"))}
        self.pull_button = QPushButton("Pull")
        self.push_button = QPushButton("Push...")
        self.status_tree = QTreeWidget()
        self.status_tree.setHeaderLabels(["Part", "File", "State"])
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)

        form = QFormLayout()
        form.addRow("Server", self.server)
        account = QHBoxLayout()
        for w in (self.who, self.sign_in, self.sign_out):
            account.addWidget(w)
        form.addRow("Account", account)
        form.addRow("Workspace", self.workspace)
        folder_row = QHBoxLayout()
        for w in (self.folder, browse, self.open_button):
            folder_row.addWidget(w)
        form.addRow("Folder", folder_row)
        toggle_row = QHBoxLayout()
        for box in self.toggles.values():
            toggle_row.addWidget(box)
        form.addRow("Pull", toggle_row)
        actions = QHBoxLayout()
        actions.addWidget(self.pull_button)
        actions.addWidget(self.push_button)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(actions)
        layout.addWidget(QLabel("Local changes"))
        layout.addWidget(self.status_tree, 2)
        layout.addWidget(QLabel("Log"))
        layout.addWidget(self.log, 1)

        self.sign_in.clicked.connect(self.do_sign_in)
        self.sign_out.clicked.connect(self.do_sign_out)
        browse.clicked.connect(self.do_browse)
        self.open_button.clicked.connect(self.do_open)
        self.pull_button.clicked.connect(self.do_pull)
        self.push_button.clicked.connect(self.do_push)
        for key, box in self.toggles.items():
            box.toggled.connect(lambda checked, key=key: self.repo and self.repo.set_config(**{key: checked}))

        self.refresh_account()
        if self.folder.text() and (Path(self.folder.text()) / ".dsmd").is_dir():
            self.do_open()
        self.update_buttons()

    # ----- helpers ---------------------------------------------------------------

    def server_url(self):
        return self.server.text().strip().rstrip("/")

    def client(self, workspace_id=None):
        return Client(self.server_url(), auth.Credentials(self.server_url()), workspace_id)

    def say(self, text):
        self.log.appendPlainText(text)

    def run(self, job, on_done):
        if self.worker and self.worker.isRunning():
            return
        self.worker = Worker(job)
        self.worker.line.connect(self.say)
        self.worker.done.connect(on_done)
        self.worker.failed.connect(lambda message: (self.say(f"Error: {message}"), self.update_buttons()))
        self.worker.finished.connect(self.update_buttons)
        self.set_busy(True)
        self.worker.start()

    def set_busy(self, busy):
        for w in (self.sign_in, self.sign_out, self.open_button, self.pull_button, self.push_button):
            w.setEnabled(not busy)

    def update_buttons(self):
        self.set_busy(False)
        signed_in = auth.Credentials(self.server_url()).stored() is not None or bool(os.environ.get("DSMD_TOKEN"))
        self.sign_in.setEnabled(not signed_in)
        self.sign_out.setEnabled(signed_in)
        self.pull_button.setEnabled(self.repo is not None)
        report = sync.status(self.repo) if self.repo else None
        modified = report and any(f["state"] == "modified" for p in report["parts"] for f in p["files"])
        blocked = report and any(f["state"] in ("conflicted", "read-only-edited")
                                 for p in report["parts"] for f in p["files"])
        self.push_button.setEnabled(bool(modified) and not blocked)
        self.show_status(report)

    def show_status(self, report):
        self.status_tree.clear()
        if not report:
            return
        for part in report["parts"]:
            for f in part["files"]:
                QTreeWidgetItem(self.status_tree, [part["path"], f["name"], f["state"]])
        for s in report["settings"]:
            QTreeWidgetItem(self.status_tree, ["settings", s["path"], s["state"]])
        for path in report["generated_edited"]:
            QTreeWidgetItem(self.status_tree, ["library", path, "generated - will be overwritten"])
        self.status_tree.resizeColumnToContents(0)

    # ----- account ---------------------------------------------------------------

    def refresh_account(self):
        def job(say):
            client = self.client()
            me = client.get("/users/me/", workspace=False)
            return me, client.get("/workspaces/", workspace=False)

        def done(result):
            me, workspaces = result
            self.who.setText(f"Signed in as {me.get('email')}")
            self.workspace.clear()
            for w in workspaces:
                self.workspace.addItem(f"{w.get('name')} ({w['id']})", w["id"])
            if self.repo:
                index = self.workspace.findData(self.repo.state["workspace_id"])
                self.workspace.setCurrentIndex(max(index, 0))
        if auth.Credentials(self.server_url()).stored() or os.environ.get("DSMD_TOKEN"):
            self.run(job, done)

    def do_sign_in(self):
        server = self.server_url()
        _save_prefs(server=server)
        self.run(lambda say: auth.login(server, say=say), lambda _: self.refresh_account())

    def do_sign_out(self):
        auth.logout(self.server_url())
        self.who.setText("Not signed in")
        self.workspace.clear()
        self.update_buttons()

    # ----- working copy ---------------------------------------------------------------

    def do_browse(self):
        folder = QFileDialog.getExistingDirectory(self, "Working copy folder", self.folder.text() or str(Path.home()))
        if folder:
            self.folder.setText(folder)

    def attach(self, root):
        self.repo = Repo.find(root)
        _save_prefs(folder=str(self.repo.root))
        cfg = self.repo.config()
        for key, box in self.toggles.items():
            box.blockSignals(True)
            box.setChecked(cfg[key])
            box.blockSignals(False)
        self.say(f"Working copy {self.repo.root} ({self.repo.state.get('workspace_name')})")
        self.update_buttons()

    def do_open(self):
        folder = self.folder.text().strip()
        if not folder:
            QMessageBox.information(self, APP_NAME, "Choose a folder first.")
            return
        if (Path(folder) / ".dsmd").is_dir():
            try:
                self.attach(folder)
            except RepoError as exc:
                self.say(f"Error: {exc}")
            return
        workspace_id = self.workspace.currentData()
        if workspace_id is None:
            QMessageBox.information(self, APP_NAME, "Sign in and pick a workspace to clone.")
            return
        self.run(lambda say: sync.clone(self.client(), workspace_id, folder, say=say),
                 lambda result: (self.say(f"Cloned {result['parts']} parts"), self.attach(folder)))

    def repo_client(self):
        return self.client(self.repo.state["workspace_id"]) if self.repo else None

    def do_pull(self):
        repo, client = self.repo, self.repo_client()

        def job(say):
            with repo.lock():
                return sync.pull(repo, client, say=say)

        def done(report):
            self.say(f"Pulled {report['changed_files']} files; {len(report['conflicts'])} conflicts")
            for c in report["conflicts"]:
                self.say(f"CONFLICT {c['path']} ({c['conflicts']} rows)")
            for w in report["warnings"]:
                self.say(f"warning {w}")
            self.update_buttons()
        self.run(job, done)

    def do_push(self):
        message, ok = QInputDialog.getText(self, "Push", "What changed and why?")
        if not ok or not message.strip():
            return
        repo, client = self.repo, self.repo_client()

        def job(say):
            with repo.lock():
                return sync.push(repo, client, message.strip(), say=say)

        def done(result):
            for r in result["applied"]:
                self.say(f"applied  {r['path']}")
            for r in result["stale"]:
                self.say(f"stale    {r['path']}: pull first, then push again")
            for r in result["rejected"]:
                for e in r["errors"]:
                    self.say(f"rejected {r['path']}/{e.get('file') or ''}:{e.get('line') or ''} {e['error']}")
            self.update_buttons()
        self.run(job, done)


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    window = Window()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
