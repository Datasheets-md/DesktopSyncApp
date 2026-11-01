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
dsmd gui                              # the desktop app (needs the [gui] extra)
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
settings/mapping.yaml         read-only
settings/symbol_style.yaml    read-only
parts/<MFR>/<PN>/
  part.yaml                   identity (read-only)
  parameters.jsonl            unified parameters, one row per line  -- editable
  pins.csv                    physical pins, one per line           -- editable
  cad.yaml                    CAD configuration                     -- editable
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
