import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QFormLayout,
    QFileDialog, QMessageBox,
)
from PyQt6.QtCore import QThread, pyqtSignal, Qt
from config import load_config, save_config
from sync_engine import run_sync, export_to_kicad_sym
from version import __version__, __app_name__


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


class dBSyncWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{__app_name__} v{__version__}")
        self.setFixedSize(450, 280)

        self.config = load_config()
        self.sync_worker = None

        layout = QVBoxLayout(self)

        # Login form
        form = QFormLayout()
        self.email_input = QLineEdit(self.config.get("user_email", ""))
        self.email_input.setPlaceholderText("user@example.com")
        self.password_input = QLineEdit(self.config.get("user_password", ""))
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText("password")
        form.addRow("Email:", self.email_input)
        form.addRow("Password:", self.password_input)
        layout.addLayout(form)

        # Output folder
        folder_layout = QHBoxLayout()
        self.folder_input = QLineEdit(self.config.get("output_dir", SCRIPT_DIR))
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
        folder = QFileDialog.getExistingDirectory(self, "Select output folder", self.folder_input.text())
        if folder:
            self.folder_input.setText(folder)

    def _on_sync(self):
        email = self.email_input.text().strip()
        password = self.password_input.text()
        output_dir = self.folder_input.text().strip()

        if not email or not password:
            self.status_label.setText("Enter email and password")
            self.status_label.setStyleSheet("color: red;")
            return

        if not output_dir or not os.path.isdir(output_dir):
            self.status_label.setText("Select a valid output folder")
            self.status_label.setStyleSheet("color: red;")
            return

        # Save config
        self.config["user_email"] = email
        self.config["user_password"] = password
        self.config["output_dir"] = output_dir
        save_config(self.config)

        self.sync_btn.setEnabled(False)
        self.status_label.setText("Connecting...")
        self.status_label.setStyleSheet("color: gray;")

        self.sync_worker = SyncWorker(self.config)
        self.sync_worker.finished.connect(self._on_done)
        self.sync_worker.error.connect(self._on_error)
        self.sync_worker.start()

    def _on_done(self, result):
        self.sync_btn.setEnabled(True)
        if result.get("error"):
            self.status_label.setText(f"Error: {result['error']}")
            self.status_label.setStyleSheet("color: red;")
        else:
            n = result["components"]
            t = result["tables"]
            if result.get("symbol_library_created"):
                self.status_label.setText(f"Done! {n} components in {t} categories. Library ready!")
            else:
                self.status_label.setText(f"Done! {n} components in {t} categories")
            self.status_label.setStyleSheet("color: green;")

    def _on_error(self, msg):
        self.sync_btn.setEnabled(True)
        self.status_label.setText(msg[:80])
        self.status_label.setStyleSheet("color: red;")


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(__app_name__)
    window = dBSyncWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
