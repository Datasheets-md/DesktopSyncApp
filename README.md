# dBsync

Syncs private electronic components from Datasheets.md to KiCad.

## How it works

1. User creates a personal API token at https://datasheets.md/integrations/api and pastes it into the app
2. App fetches the parts in your workspace via the public REST API using the token as `Authorization: Bearer dsh_...`
3. You pick what to synchronise with the checkboxes:
   - **SQLite database** - the component database as a `.sqlite` file (usable by KiCad and other tools/systems)
   - **KiCad library files** - symbols, footprints, and the KiCad database-library descriptor (needs the SQLite database)
   - **PDF datasheets** - the original manufacturer PDFs
   - **Markdown datasheets** - the digitised text datasheets
4. User adds the library to KiCad

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

Depending on which checkboxes are enabled, in the output folder:

- `dbsync.sqlite` - the component database (SQLite)
- `datasheets.kicad_sym` - KiCad symbol library
- `datasheets.pretty/` - KiCad footprints (one `.kicad_mod` per part)
- `dbsync.kicad_dbl`, `sym-lib-table`, `fp-lib-table` - KiCad database-library descriptor + project lib tables
- `pdf/` - original manufacturer PDF datasheets
- `markdown/` - digitised text datasheets

`dbsync.json` (next to the app) holds the configuration.

## Configuration

The app connects to the public Datasheets.md REST API under `https://datasheets.md/api-service`
(override the server in the app for dev or self-hosted instances).

The API token is saved locally in `dbsync.json` after you click Test or Sync. Revoke
it any time at https://datasheets.md/integrations/api if a device is lost.
