import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QFormLayout,
    QFileDialog,
)
from PyQt6.QtCore import QThread, pyqtSignal, Qt
from PyQt6.QtGui import QIcon
from api import test_connection
from config import load_config, save_config
from sync_engine import run_sync, export_to_kicad_sym
from version import __version__, __app_name__


def _resource_path(filename: str) -> str:
    """Return absolute path to a bundled resource. PyInstaller's --onefile
    extracts data files into sys._MEIPASS at runtime; --onedir keeps them
    next to the script. Falls back to SCRIPT_DIR when run from source."""
    base = getattr(sys, "_MEIPASS", SCRIPT_DIR)
    return os.path.join(base, filename)


class SyncWorker(QThread):
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, config):
        super().__init__()
        self.config = config

    def run(self):
        try:
            result = run_sync(self.config)

            # Also export to KiCad symbol library format (no ODBC needed)
            output_dir = self.config.get("output_dir", ".")
            db_path = os.path.join(output_dir, "dbsync.sqlite")
            sym_path = os.path.join(output_dir, "dbsync.kicad_sym")

            if os.path.exists(db_path):
                export_to_kicad_sym(db_path, sym_path)
                result["symbol_library_created"] = True

            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class TestWorker(QThread):
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, config):
        super().__init__()
        self.config = config

    def run(self):
        try:
            test_connection(self.config)
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))


class dBSyncWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{__app_name__} v{__version__}")
        self.setFixedSize(500, 360)

        icon_path = _resource_path("icon.png")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        self.config = load_config()
        self.sync_worker = None
        self.test_worker = None

        layout = QVBoxLayout(self)

        form = QFormLayout()

        # API URL — defaults to prod; editable so dev/self-hosted users can override.
        self.url_input = QLineEdit(self.config.get("api_url", "https://datasheets.md"))
        self.url_input.setPlaceholderText("https://datasheets.md")
        form.addRow("Server URL:", self.url_input)

        # Token + Test
        token_row = QHBoxLayout()
        self.token_input = QLineEdit(self.config.get("api_token", ""))
        self.token_input.setPlaceholderText("dsh_...")
        self.token_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.test_btn = QPushButton("Test")
        self.test_btn.setFixedWidth(80)
        self.test_btn.clicked.connect(self._on_test)
        token_row.addWidget(self.token_input)
        token_row.addWidget(self.test_btn)
        form.addRow("API token:", token_row)

        help_label = QLabel(
            'Get a token from <a href="https://datasheets.md/integrations/api">'
            'datasheets.md/integrations/api</a> (or your dev/self-hosted server).'
        )
        help_label.setOpenExternalLinks(True)
        help_label.setStyleSheet("color: gray; font-size: 11px;")
        form.addRow("", help_label)
        layout.addLayout(form)

        # Output folder
        folder_layout = QHBoxLayout()
        self.folder_input = QLineEdit("")  # Always start with empty folder
        self.folder_input.setPlaceholderText("Select output folder...")
        browse_btn = QPushButton("Browse...")
        browse_btn.setFixedWidth(80)
        browse_btn.clicked.connect(self._browse_folder)
        folder_layout.addWidget(QLabel("Output folder:"))
        folder_layout.addWidget(self.folder_input)
        folder_layout.addWidget(browse_btn)
        layout.addLayout(folder_layout)

        # Status
        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_label)

        # Sync button
        self.sync_btn = QPushButton("Sync")
        self.sync_btn.setFixedHeight(40)
        self.sync_btn.clicked.connect(self._on_sync)
        layout.addWidget(self.sync_btn)

        # Version label
        version_label = QLabel(f"Version {__version__}")
        version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        version_label.setStyleSheet("color: gray; font-size: 10px;")
        layout.addWidget(version_label)

    def _browse_folder(self):
        start_dir = self.folder_input.text() or os.path.expanduser("~")
        folder = QFileDialog.getExistingDirectory(self, "Select output folder", start_dir)
        if folder:
            self.folder_input.setText(folder)

    def _save_token(self) -> str:
        token = self.token_input.text().strip()
        url = self.url_input.text().strip() or "https://datasheets.md"
        self.config["api_token"] = token
        self.config["api_url"] = url.rstrip("/")
        save_config(self.config)
        return token

    def _on_test(self):
        token = self._save_token()
        if not token:
            self._set_status("Paste your API token first", "red")
            return
        self.test_btn.setEnabled(False)
        self._set_status("Testing connection...", "gray")
        self.test_worker = TestWorker(self.config)
        self.test_worker.finished.connect(self._on_test_done)
        self.test_worker.error.connect(self._on_test_error)
        self.test_worker.start()

    def _on_test_done(self):
        self.test_btn.setEnabled(True)
        self._set_status("Connection OK", "green")

    def _on_test_error(self, msg):
        self.test_btn.setEnabled(True)
        self._set_status(msg[:80], "red")

    def _on_sync(self):
        token = self._save_token()
        output_dir = self.folder_input.text().strip()

        if not token:
            self._set_status("Paste your API token first", "red")
            return
        if not output_dir or not os.path.isdir(output_dir):
            self._set_status("Select a valid output folder", "red")
            return

        # output_dir is per-session, not persisted.
        self.config["output_dir"] = output_dir
        config_to_save = self.config.copy()
        config_to_save.pop("output_dir", None)
        save_config(config_to_save)

        self.sync_btn.setEnabled(False)
        self._set_status("Connecting...", "gray")

        self.sync_worker = SyncWorker(self.config)
        self.sync_worker.finished.connect(self._on_done)
        self.sync_worker.error.connect(self._on_error)
        self.sync_worker.start()

    def _on_done(self, result):
        self.sync_btn.setEnabled(True)
        if result.get("error"):
            self._set_status(f"Error: {result['error']}", "red")
            return
        n = result["components"]
        t = result["tables"]
        if result.get("symbol_library_created"):
            self._set_status(f"Done! {n} components in {t} categories. Library ready!", "green")
        else:
            self._set_status(f"Done! {n} components in {t} categories", "green")

    def _on_error(self, msg):
        self.sync_btn.setEnabled(True)
        self._set_status(msg[:80], "red")

    def _set_status(self, text: str, colour: str):
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {colour};")


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(__app_name__)
    window = dBSyncWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
