# Building the standalone dBsync binaries

The binaries wrap the `[gui]` extra with PyInstaller; CI does this in
`.github/workflows/build.yml` (dispatch it, or push a `v*` tag). Locally:

```bash
pip install ".[gui]" pyinstaller
python -m PyInstaller --onefile --windowed --name dBsync \
    --add-data "src/datasheets_md/gui/icon.png:." \
    --icon src/datasheets_md/gui/icon.png \
    --clean packaging/dbsync.py
```

On Windows use `;` instead of `:` in `--add-data` and `--icon icon.ico`; on
macOS build `--onedir` with `--icon icon-windowed.icns`.

The version is `__version__` in `src/datasheets_md/__init__.py`.
