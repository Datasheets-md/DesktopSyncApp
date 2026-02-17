# KiCadSync

Desktop app that syncs private components from a Django server to a local SQLite database for use as a KiCad Database Library (ODBC).

```
Django API --> KiCadSync app --> kicadsync.sqlite --ODBC--> KiCad
```

## Install

```bash
pip install pystray Pillow
```

Also install the SQLite3 ODBC driver for your OS:

- **Ubuntu/Debian:** `sudo apt install libsqliteodbc`
- **Fedora:** `sudo dnf install sqliteodbc`
- **macOS:** `brew install sqliteodbc`
- **Windows:** download from http://www.ch-werner.de/sqliteodbc/

## Configuration

Edit `kicad_sync.json`:

```json
{
    "server_url": "http://100.68.56.30:8005",
    "sync_interval_sec": 300,
    "fetch_digikey": true,
    "page_limit": 100,
    "exclude_fields": []
}
```

## Usage

### System tray app (background sync)

```bash
python kicad_sync.py
```

A tray icon appears with a right-click menu:

- **Sync Now** — trigger immediate sync
- **Login...** — opens login dialog (server URL, email, password)
- **Logout** — clears stored credentials
- **Open Config** — opens `kicad_sync.json` in default editor
- **Quit** — stop the app

Icon colors: green = OK, orange = syncing, red = error / not logged in.

### One-shot sync (no GUI)

```bash
python sync_engine.py --email user@example.com --password yourpass --server https://your-server.example.com
```

`--server` overrides the value in `kicad_sync.json`.

## Connect to KiCad

1. Run a sync (either method above)
2. In KiCad: **Preferences > Manage Symbol Libraries**
3. Click **Add existing library to table** (folder icon)
4. Select `kicadsync.kicad_dbl` from the KiCadSync directory
5. Open the symbol chooser — synced parts appear grouped by category

## Generated files

| File | Description |
|---|---|
| `kicadsync.sqlite` | SQLite database with component data |
| `kicadsync.kicad_dbl` | KiCad database library config (ODBC) |
| `.token_store.json` | JWT credentials (chmod 600) |
