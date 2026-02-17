import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from PyQt6.QtWidgets import (
    QApplication, QSystemTrayIcon, QMenu, QDialog,
    QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QFormLayout, QTableWidget, QTableWidgetItem, QHeaderView,
)
from PyQt6.QtGui import QAction
from PyQt6.QtCore import QThread, pyqtSignal, Qt
from config import load_config, save_config
from auth import connect, authenticate
from api import fetch_components
from sync_engine import run_sync
from icon import icon_ok, icon_syncing, icon_error


class SyncWorker(QThread):
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, config):
        super().__init__()
        self.config = config

    def run(self):
        try:
            result = run_sync(self.config)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class FetchWorker(QThread):
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, config):
        super().__init__()
        self.config = config

    def run(self):
        try:
            components = fetch_components(self.config)
            self.finished.emit(components)
        except Exception as e:
            self.error.emit(str(e))


class LoginDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("KiCadSync Login")
        self.setFixedSize(400, 200)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)

        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.email_input = QLineEdit(config.get("user_email", ""))
        self.email_input.setPlaceholderText("user@example.com")
        self.password_input = QLineEdit(config.get("user_password", ""))
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText("password")
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

        self.config = config

    def _do_login(self):
        email = self.email_input.text().strip()
        password = self.password_input.text()

        if not email or not password:
            self.status_label.setText("All fields required")
            return

        self.login_btn.setEnabled(False)
        self.status_label.setText("Checking credentials...")
        self.status_label.setStyleSheet("color: gray;")
        QApplication.processEvents()

        try:
            import psycopg2.extras
            conn = connect(self.config)
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            authenticate(cur, email, password)
            cur.close()
            conn.close()

            self.config["user_email"] = email
            self.config["user_password"] = password
            save_config(self.config)
            self.accept()
        except Exception as e:
            self.status_label.setText(str(e)[:60])
            self.status_label.setStyleSheet("color: red;")
            self.login_btn.setEnabled(True)


class ComponentsDialog(QDialog):
    COLUMNS = [
        ("Part Number", "part_number"),
        ("Manufacturer", "manufacturer"),
        ("Category", "category"),
        ("Package", "package"),
        ("Description", "description"),
        ("Symbol", "kicad_symbol"),
        ("Footprint", "kicad_footprint"),
    ]

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("My Components")
        self.resize(900, 500)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)

        layout = QVBoxLayout(self)

        self.status_label = QLabel("Loading components...")
        layout.addWidget(self.status_label)

        self.table = QTableWidget()
        self.table.setColumnCount(len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels([c[0] for c in self.COLUMNS])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table)

        btn_layout = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(lambda: self._load(config))
        btn_layout.addStretch()
        btn_layout.addWidget(self.refresh_btn)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        self.fetch_worker = None
        self._load(config)

    def _load(self, config):
        self.refresh_btn.setEnabled(False)
        self.status_label.setText("Loading components...")
        self.fetch_worker = FetchWorker(config)
        self.fetch_worker.finished.connect(self._on_loaded)
        self.fetch_worker.error.connect(self._on_error)
        self.fetch_worker.start()

    def _on_loaded(self, components):
        self.table.setRowCount(len(components))
        for row_idx, comp in enumerate(components):
            meta = (comp.get("metadata") or [{}])[0]
            for col_idx, (_, key) in enumerate(self.COLUMNS):
                value = meta.get(key, "")
                self.table.setItem(row_idx, col_idx, QTableWidgetItem(value))

        self.status_label.setText(f"{len(components)} components")
        self.refresh_btn.setEnabled(True)

    def _on_error(self, msg):
        self.status_label.setText(f"Error: {msg[:60]}")
        self.refresh_btn.setEnabled(True)


class KiCadSyncApp:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        self.app.setApplicationName("KiCadSync")

        self.config = load_config()
        self.sync_worker = None
        self.components_dlg = None

        self._build_tray()

        if self.config.get("user_email") and self.config.get("user_password"):
            self._set_state("ok", "Ready")
        else:
            self._set_state("error", "Not logged in")

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

        components_action = QAction("My Components...", menu)
        components_action.triggered.connect(self._on_show_components)
        menu.addAction(components_action)

        menu.addSeparator()

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

        self._set_state("syncing", "Syncing...")
        self.config = load_config()

        self.sync_worker = SyncWorker(self.config)
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

    def _on_show_components(self):
        self.config = load_config()
        self.components_dlg = ComponentsDialog(self.config)
        self.components_dlg.show()

    def _on_login(self):
        dlg = LoginDialog(self.config)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.config = load_config()
            self._set_state("ok", "Logged in")

    def _on_logout(self):
        self.config["user_email"] = ""
        self.config["user_password"] = ""
        save_config(self.config)
        self.config = load_config()
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
