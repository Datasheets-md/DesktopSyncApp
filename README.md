# KiCadSync

Syncs private electronic components from Datasheets.md to KiCad.

## How it works

1. User logs in with Datasheets.md credentials
2. App fetches user's private components from PostgreSQL database
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
python3 kicad_sync.py

# Or command line
python3 sync_engine.py --export-static
```

### Add to KiCad

1. Sync your components (creates `kicadsync.kicad_sym`)
2. In KiCad: Preferences → Manage Symbol Libraries
3. Add existing library → Select `kicadsync.kicad_sym`

## Files created

- `kicadsync.sqlite` - Local database
- `kicadsync.kicad_sym` - KiCad library file
- `kicad_sync.json` - Configuration

## Configuration

The app connects to:
- Host: 100.68.56.30
- Port: 5432
- Database: django_db

User credentials are saved locally after first login.

## License

MIT