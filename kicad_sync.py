import os
import sys
import threading
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import pystray

from config import load_config
from auth import login, logout, get_valid_token, is_logged_in
from sync_engine import run_sync
from icon import icon_ok, icon_syncing, icon_error


class KiCadSyncApp:

    def __init__(self):
        self.config = load_config()
        self.tray = None
        self.last_status = "Starting..."
        self.sync_running = False
        self.stop_event = threading.Event()

    @property
    def server_url(self):
        return self.config.get("server_url", "")

    @property
    def sync_interval(self):
        return self.config.get("sync_interval_sec", 300)

    def _build_menu(self):
        return pystray.Menu(
            pystray.MenuItem("KiCadSync", None, enabled=False),
            pystray.MenuItem(lambda _: self.last_status, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Sync Now", self._on_sync_now),
            pystray.MenuItem("Login...", self._on_login),
            pystray.MenuItem("Logout", self._on_logout),
            pystray.MenuItem("Open Config", self._on_open_config),
            pystray.MenuItem("Quit", self._on_quit),
        )

    def _update_icon(self, state):
        if not self.tray:
            return
        if state == "ok":
            self.tray.icon = icon_ok()
        elif state == "syncing":
            self.tray.icon = icon_syncing()
        else:
            self.tray.icon = icon_error()
        self.tray.update_menu()

    def _on_sync_now(self, icon=None, item=None):
        if self.sync_running:
            return
        threading.Thread(target=self._do_sync, daemon=True).start()

    def _on_login(self, icon=None, item=None):
        threading.Thread(target=self._show_login_dialog, daemon=True).start()

    def _on_logout(self, icon=None, item=None):
        logout()
        self.last_status = "Logged out"
        self._update_icon("error")

    def _on_open_config(self, icon=None, item=None):
        config_path = os.path.join(SCRIPT_DIR, "kicad_sync.json")
        if sys.platform == "win32":
            os.startfile(config_path)
        elif sys.platform == "darwin":
            os.system(f'open "{config_path}"')
        else:
            os.system(f'xdg-open "{config_path}" 2>/dev/null &')

    def _on_quit(self, icon=None, item=None):
        self.stop_event.set()
        if self.tray:
            self.tray.stop()

    def _show_login_dialog(self):
        import tkinter as tk

        root = tk.Tk()
        root.title("KiCadSync Login")
        root.resizable(False, False)
        root.attributes("-topmost", True)

        w, h = 350, 220
        x = (root.winfo_screenwidth() - w) // 2
        y = (root.winfo_screenheight() - h) // 2
        root.geometry(f"{w}x{h}+{x}+{y}")

        frame = tk.Frame(root, padx=20, pady=15)
        frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(frame, text="Server URL:").grid(row=0, column=0, sticky="w", pady=2)
        server_var = tk.StringVar(value=self.server_url)
        tk.Entry(frame, textvariable=server_var, width=35).grid(row=0, column=1, pady=2)

        tk.Label(frame, text="Email:").grid(row=1, column=0, sticky="w", pady=2)
        email_var = tk.StringVar()
        tk.Entry(frame, textvariable=email_var, width=35).grid(row=1, column=1, pady=2)

        tk.Label(frame, text="Password:").grid(row=2, column=0, sticky="w", pady=2)
        pass_var = tk.StringVar()
        tk.Entry(frame, textvariable=pass_var, show="*", width=35).grid(row=2, column=1, pady=2)

        status_var = tk.StringVar()
        tk.Label(frame, textvariable=status_var, fg="red").grid(
            row=3, column=0, columnspan=2, pady=5
        )

        def do_login():
            url = server_var.get().strip()
            email = email_var.get().strip()
            pw = pass_var.get()
            if not url or not email or not pw:
                status_var.set("All fields required")
                return
            try:
                status_var.set("Logging in...")
                root.update()
                login(url, email, pw)
                if url != self.server_url:
                    self.config["server_url"] = url
                    from config import save_config
                    save_config(self.config)
                self.last_status = "Logged in"
                self._update_icon("ok")
                root.destroy()
                self._on_sync_now()
            except Exception as e:
                status_var.set(str(e)[:50])

        tk.Button(frame, text="Login", command=do_login, width=15).grid(
            row=4, column=0, columnspan=2, pady=10
        )

        root.mainloop()

    def _do_sync(self):
        if self.sync_running:
            return
        self.sync_running = True
        self.last_status = "Syncing..."
        self._update_icon("syncing")

        try:
            token = get_valid_token(self.server_url)
            if not token:
                self.last_status = "Not logged in"
                self._update_icon("error")
                return

            self.config = load_config()
            result = run_sync(self.server_url, token, self.config)

            if result["error"]:
                self.last_status = f"Error: {result['error']}"
                self._update_icon("error")
            else:
                self.last_status = (
                    f"OK: {result['components']} parts in {result['tables']} tables"
                )
                self._update_icon("ok")

        except Exception as e:
            self.last_status = f"Error: {str(e)[:40]}"
            self._update_icon("error")
        finally:
            self.sync_running = False

    def _scheduler_loop(self):
        time.sleep(2)
        if is_logged_in() and not self.stop_event.is_set():
            self._do_sync()

        while not self.stop_event.is_set():
            self.stop_event.wait(timeout=self.sync_interval)
            if self.stop_event.is_set():
                break
            if is_logged_in():
                self._do_sync()

    def run(self):
        initial_icon = icon_ok() if is_logged_in() else icon_error()
        self.last_status = "Ready" if is_logged_in() else "Not logged in"

        self.tray = pystray.Icon(
            name="KiCadSync",
            icon=initial_icon,
            title="KiCadSync",
            menu=self._build_menu(),
        )

        sched = threading.Thread(target=self._scheduler_loop, daemon=True)
        sched.start()

        self.tray.run()


def main():
    app = KiCadSyncApp()
    app.run()


if __name__ == "__main__":
    main()
