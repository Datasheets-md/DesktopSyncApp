# Build Instructions

## Prerequisites

```bash
pip install pyinstaller PyQt6 requests
```

## Quick build (all platforms)

```bash
python3 build_all.py
```

## Platform-specific builds

### macOS

```bash
./build.sh
```

Output: `dist/dBsync.app`

To distribute:
```bash
cd dist
zip -r dBsync-macOS.zip dBsync.app
```

### Windows

```batch
build_windows.bat
```

Output: `dist/dBsync.exe`

Single portable executable, ready to distribute.

### Linux

```bash
./build_linux.sh
```

Output: `dist/dBsync` (binary) or `dist/dBsync.AppImage`

AppImage is recommended for distribution (works on any Linux).

## Manual build with PyInstaller

### macOS
```bash
python3 -m PyInstaller --onedir --windowed --name dBsync --add-data "dbsync.json:." --clean dbsync.py
rm -rf dist/dBsync
```

### Windows
```bash
python -m PyInstaller --onefile --windowed --name dBsync --add-data "dbsync.json;." --clean dbsync.py
```

### Linux
```bash
python3 -m PyInstaller --onefile --windowed --name dBsync --add-data "dbsync.json:." --clean dbsync.py
chmod +x dist/dBsync
```

## Troubleshooting

### "pyinstaller: command not found"
```bash
python3 -m PyInstaller [arguments]
```

### "No module named PyQt6"
```bash
pip install PyQt6
```

### macOS: "Cannot be opened because it is from an unidentified developer"
- Right-click the app → Open
- Or sign the app with Apple Developer ID

### Windows: "Windows protected your PC"
- Click "More info" → "Run anyway"
- Or sign the exe with code signing certificate

### Linux: Permission denied
```bash
chmod +x dBsync
```