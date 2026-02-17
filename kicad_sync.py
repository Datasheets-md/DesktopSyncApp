import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from PyQt6.QtWidgets import (
    QApplication, QSystemTrayIcon, QMenu, QDialog,
    QVBoxLayout, QLabel, QLineEdit, QPushButton,
    QFormLayout, QMessageBox,
)
from PyQt6.QtGui import QAction
from PyQt6.QtCore import QTimer, QThread, pyqtSignal, Qt

from config import load_config, save_config
from auth import login, logout, get_valid_token, is_logged_in
from sync_engine import run_sync
from icon import icon_ok, icon_syncing, icon_error


class SyncWorker(QThread):
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, server_url, token, config):
        super().__init__()
        self.server_url = server_url
        self.token = token
        self.config = config

    def run(self):
        try:
            result = run_sync(self.server_url, self.token, self.config)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class LoginDialog(QDialog):
    def __init__(self, server_url, parent=None):
        super().__init__(parent)
        self.setWindowTitle("KiCadSync Login")
        self.setFixedSize(400, 220)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)

        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.server_input = QLineEdit(server_url)
        self.email_input = QLineEdit()
        self.email_input.setPlaceholderText("user@example.com")
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText("password")
        form.addRow("Server URL:", self.server_input)
        form.addRow("Email:", self.email_input)
        form.addRow("Password:", self.password_input)
        layout.addLayout(form)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: red;")
        layout.addWidget(self.status_label)

        self.login_btn = QPushButton("Login")
        self.login_btn.setDefault(True)
        self.login_btn.clicked.connect(self._do_login)
        layout.addWidget(self.login_btn)

        self.result_token = None
        self.result_server = None

    def _do_login(self):
        server = self.server_input.text().strip()
        email = self.email_input.text().strip()
        password = self.password_input.text()

        if not server or not email or not password:
            self.status_label.setText("All fields required")
            return

        self.login_btn.setEnabled(False)
        self.status_label.setText("Logging in...")
        self.status_label.setStyleSheet("color: gray;")
        QApplication.processEvents()

        try:
            token = login(server, email, password)
            self.result_token = token
            self.result_server = server
            self.accept()
        except Exception as e:
            self.status_label.setText(str(e)[:60])
            self.status_label.setStyleSheet("color: red;")
            self.login_btn.setEnabled(True)


class KiCadSyncApp:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        self.app.setApplicationName("KiCadSync")

        self.config = load_config()
        self.sync_worker = None

        self._build_tray()
        self._build_timer()

        if is_logged_in():
            self._set_state("ok", "Ready")
            QTimer.singleShot(2000, self._on_sync_now)
        else:
            self._set_state("error", "Not logged in")

    @property
    def server_url(self):
        return self.config.get("server_url", "")

    @property
    def sync_interval(self):
        return self.config.get("sync_interval_sec", 300)

    def _build_tray(self):
        self.tray = QSystemTrayIcon(icon_error(), self.app)

        menu = QMenu()

        self.status_action = QAction("KiCadSync")
        self.status_action.setEnabled(False)
        menu.addAction(self.status_action)

        menu.addSeparator()

        sync_action = QAction("Sync Now", menu)
        sync_action.triggered.connect(self._on_sync_now)
        menu.addAction(sync_action)

        login_action = QAction("Login...", menu)
        login_action.triggered.connect(self._on_login)
        menu.addAction(login_action)

        logout_action = QAction("Logout", menu)
        logout_action.triggered.connect(self._on_logout)
        menu.addAction(logout_action)

        menu.addSeparator()

        config_action = QAction("Open Config", menu)
        config_action.triggered.connect(self._on_open_config)
        menu.addAction(config_action)

        quit_action = QAction("Quit", menu)
        quit_action.triggered.connect(self._on_quit)
        menu.addAction(quit_action)

        self.tray.setContextMenu(menu)
        self.tray.setToolTip("KiCadSync")
        self.tray.show()

    def _build_timer(self):
        self.timer = QTimer()
        self.timer.timeout.connect(self._on_sync_now)
        self.timer.start(self.sync_interval * 1000)

    def _set_state(self, state, message):
        if state == "ok":
            self.tray.setIcon(icon_ok())
        elif state == "syncing":
            self.tray.setIcon(icon_syncing())
        else:
            self.tray.setIcon(icon_error())
        self.status_action.setText(message)
        self.tray.setToolTip(f"KiCadSync - {message}")

    def _on_sync_now(self):
        if self.sync_worker and self.sync_worker.isRunning():
            return

        token = get_valid_token(self.server_url)
        if not token:
            self._set_state("error", "Not logged in")
            return

        self._set_state("syncing", "Syncing...")
        self.config = load_config()

        self.sync_worker = SyncWorker(self.server_url, token, self.config)
        self.sync_worker.finished.connect(self._on_sync_done)
        self.sync_worker.error.connect(self._on_sync_error)
        self.sync_worker.start()

    def _on_sync_done(self, result):
        if result.get("error"):
            self._set_state("error", f"Error: {result['error']}")
        else:
            self._set_state("ok", f"{result['components']} parts in {result['tables']} tables")

    def _on_sync_error(self, msg):
        self._set_state("error", f"Error: {msg[:40]}")

    def _on_login(self):
        dlg = LoginDialog(self.server_url)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            if dlg.result_server != self.server_url:
                self.config["server_url"] = dlg.result_server
                save_config(self.config)
            self._set_state("ok", "Logged in")
            self._on_sync_now()

    def _on_logout(self):
        logout()
        self._set_state("error", "Logged out")

    def _on_open_config(self):
        config_path = os.path.join(SCRIPT_DIR, "kicad_sync.json")
        if sys.platform == "win32":
            os.startfile(config_path)
        elif sys.platform == "darwin":
            os.system(f'open "{config_path}"')
        else:
            os.system(f'xdg-open "{config_path}" 2>/dev/null &')

    def _on_quit(self):
        self.timer.stop()
        if self.sync_worker and self.sync_worker.isRunning():
            self.sync_worker.wait(3000)
        self.tray.hide()
        self.app.quit()

    def run(self):
        sys.exit(self.app.exec())


def main():
    app = KiCadSyncApp()
    app.run()


if __name__ == "__main__":
    main()
