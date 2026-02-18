# dBsync

Syncs private electronic components from Datasheets.md to KiCad.

## How it works

1. User logs in with Datasheets.md credentials
2. App fetches user's private components via REST API
3. Components are saved locally as SQLite database
4. Exports to `.kicad_sym` format (KiCad symbol library)
5. User adds the library to KiCad

## Requirements

- Datasheets.md account with components
- KiCad 7.0+
- Python 3.9+

## Usage

### Download pre-built

Get the latest release for your platform from GitHub Releases.

### Run from source

```bash
# Install dependencies
pip install -r requirements.txt

# Run GUI
python3 dbsync.py

# Or command line
python3 sync_engine.py --export-static
```

### Add to KiCad

1. Sync your components (creates `dbsync.kicad_sym`)
2. In KiCad: Preferences → Manage Symbol Libraries
3. Add existing library → Select `dbsync.kicad_sym`

## Files created

- `dbsync.sqlite` - Local database
- `dbsync.kicad_sym` - KiCad library file
- `dbsync.json` - Configuration

## Configuration

The app connects to the Datasheets.md REST API (`https://datasheets.md`).

User credentials are saved locally after first login.
