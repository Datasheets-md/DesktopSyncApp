import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from PyQt6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PyQt6.QtGui import QAction
from PyQt6.QtCore import QTimer, QThread, pyqtSignal

from config import load_config, save_config
from sync_engine import run_sync
from icon import icon_ok, icon_syncing, icon_error


class SyncWorker(QThread):
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, server_url, config):
        super().__init__()
        self.server_url = server_url
        self.config = config

    def run(self):
        try:
            result = run_sync(self.server_url, self.config)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class KiCadSyncApp:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        self.app.setApplicationName("KiCadSync")

        self.config = load_config()
        self.sync_worker = None

        self._build_tray()
        self._build_timer()

        self._set_state("ok", "Ready")
        QTimer.singleShot(2000, self._on_sync_now)

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

        self._set_state("syncing", "Syncing...")
        self.config = load_config()

        self.sync_worker = SyncWorker(self.server_url, self.config)
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
