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

Output: `dist/KiCadSync.app`

To distribute:
```bash
cd dist
zip -r KiCadSync-macOS.zip KiCadSync.app
```

### Windows

```batch
build_windows.bat
```

Output: `dist/KiCadSync.exe`

Single portable executable, ready to distribute.

### Linux

```bash
./build_linux.sh
```

Output: `dist/KiCadSync` (binary) or `dist/KiCadSync.AppImage`

AppImage is recommended for distribution (works on any Linux).

## Manual build with PyInstaller

### macOS
```bash
python3 -m PyInstaller --onedir --windowed --name KiCadSync --add-data "kicad_sync.json:." --clean kicad_sync.py
rm -rf dist/KiCadSync
```

### Windows
```bash
python -m PyInstaller --onefile --windowed --name KiCadSync --add-data "kicad_sync.json;." --clean kicad_sync.py
```

### Linux
```bash
python3 -m PyInstaller --onefile --windowed --name KiCadSync --add-data "kicad_sync.json:." --clean kicad_sync.py
chmod +x dist/KiCadSync
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
chmod +x KiCadSync
```