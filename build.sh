#!/bin/bash
cd "$(dirname "$0")"

# Try to find pyinstaller
if command -v pyinstaller &> /dev/null; then
    PYINSTALLER="pyinstaller"
elif python3 -m PyInstaller --help &> /dev/null; then
    PYINSTALLER="python3 -m PyInstaller"
else
    echo "Error: pyinstaller not found!"
    echo "Install it with: pip3 install pyinstaller"
    exit 1
fi

echo "Building dBsync..."

# Remove old build if exists
rm -rf dist/
rm -rf build/

$PYINSTALLER \
    --onedir \
    --windowed \
    --name dBsync \
    --add-data "dbsync.json:." \
    --clean \
    dbsync.py

if [ $? -eq 0 ]; then
    # Remove the directory, keep only the .app bundle
    rm -rf dist/dBsync
    echo ""
    echo "Build complete: dist/dBsync.app"
else
    echo ""
    echo "Build failed!"
    exit 1
fi
