# datasheets-md (`dsmd`, dBsync)

A git-like working copy of your [datasheets.md](https://datasheets.md) workspace:
the parts' unified parameters, pin tables and CAD configuration as plain text
files you can grep, edit and push back, plus the workspace's KiCad library.

```bash
pip install datasheets-md             # the dsmd CLI
pip install datasheets-md-gui         # CLI + the dBsync desktop app (adds PySide6 to an existing install)
```

Ubuntu/Debian refuse a system-wide `pip install` ("externally-managed-environment").
Install into a venv and link the command onto your PATH:

```bash
python3 -m venv ~/.venvs/dsmd && ~/.venvs/dsmd/bin/pip install datasheets-md
ln -s ~/.venvs/dsmd/bin/dsmd ~/.local/bin/dsmd
```

or let pipx do both: `pipx install datasheets-md`.

`dsmd` looks for a new release once a day and prints one line when there is
one. `dsmd self-update` upgrades the install the way it was made (pipx, uv tool,
or the pip of its venv). Set `DSMD_NO_UPDATE_CHECK=1` to turn the check off; it
also stays off under `CI`, with `--json` or `--quiet`, and when stderr is not a
terminal.

A clone of a large workspace can stop part way (network, Ctrl-C, a closed laptop)
without losing what it downloaded: files reach the disk as they arrive, and running
the same `dsmd clone` command again continues it. Until it finishes, `dsmd status`
says so and `fetch`, `pull` and `push` refuse. If only the library step fails, the
parts and datasheets are in place, `dsmd` exits with code 2, and `dsmd pull --library`
(or the next `dsmd pull`) builds `library/`.

## Use

```bash
dsmd login                            # browser sign-in (DSMD_TOKEN=dsh_... for headless use)
dsmd workspaces                       # pick one
dsmd clone "My workspace" lib && cd lib
dsmd status                           # what you changed
dsmd diff                             # ... as a diff
dsmd push -m "Fix VOH typ from page 5"
dsmd pull                             # server changes, merged row by row
dsmd log [PART]                       # server-side history
dsmd part add SN74HC00N [--manufacturer TI]
dsmd part rm SN74HC00N
dsmd pdf SN74HC00N                    # PDF into .dsmd/cache/
dsmd open SN74HC00N                   # the part's web page
dsmd categories                       # catalogue category ids, for mapping files
dsmd feedback "a setting I need"      # tell the datasheets.md team (--kind bug|suggestion)
dsmd gui                              # the desktop app (needs datasheets-md-gui)
dsmd self-update                      # upgrade to the latest release (--check only asks)
dsmd help agents                      # the guide for AI agents
```

Every command takes `--json`, `--quiet` and `-C <dir>`. Exit codes: `0` ok,
`1` error, `2` done but with conflicts or stale/rejected parts.

`DSMD_SERVER` (or `--server`) points at another instance, e.g.
`https://api-dev.datasheets.md`.

## The working copy

```
.dsmd/                        state, pristine base copies, cache (gitignored)
dsmd.toml                     what to pull, which library outputs to build
settings/mappings/*.yaml      every workspace mapping (categories, parameter names, CAD fields, active_for) -- edit, add or delete a file, admin
settings/symbol_style.yaml    symbol generation settings and reference designators     -- editable, admin
parts/<MFR>/<PN>/
  part.yaml                   identity, and `datasheet:` -> its datasheet.md (read-only)
  parameters.jsonl            unified parameters, one row per line  -- editable
  pins.csv                    physical pins, one per line           -- editable
  cad.yaml                    CAD configuration                     -- editable
datasheets/<MFR>/<PDF name>/  one per family, shared by its parts
  datasheet.md                digitised datasheet with its element ids (read-only)
  images/                     the figures datasheet.md links to (read-only)
library/                      generated KiCad library (rebuilt on pull)
  datasheets.sqlite, datasheets.kicad_dbl, datasheets.kicad_sym,
  datasheets.pretty/, sym-lib-table, fp-lib-table
```

How it differs from git: one remote, no branches, no staging or local commits.
`push` is applied atomically per part; the server may normalise what you
pushed and sends the result straight back. Merges are by row key (`k`) and pin
`id`, so edits to different rows never conflict. History lives on the server.

To use the library in KiCad, open `library/` as a project folder (the lib
tables point at the symbols and footprints) or add `datasheets.kicad_dbl`
as a database library (needs the SQLite ODBC driver).

## Develop

```bash
pip install -e ".[gui,test]"
pytest
```

Standalone dBsync binaries (no Python needed) are built by
`.github/workflows/build.yml` with PyInstaller from `packaging/dbsync.py`.
