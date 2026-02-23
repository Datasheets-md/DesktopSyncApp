#!/bin/bash
cd "$(dirname "$0")"

# Ask for version update
echo "Current version: $(python3 -c 'from version import __version__; print(__version__)' 2>/dev/null || echo 'unknown')"
read -p "Enter new version (press Enter to keep current): " new_version
if [ ! -z "$new_version" ]; then
    python3 set_version.py "$new_version"
fi
echo ""

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
    --icon icon-windowed.icns \
    --clean \
    dbsync.py

if [ $? -eq 0 ]; then
    # Remove the directory, keep only the .app bundle
    rm -rf dist/dBsync

    # Rename with version
    VERSION=$(python3 -c "from version import __version__; print(__version__)" 2>/dev/null || echo "0.0.0")
    mv dist/dBsync.app "dist/dBsync-${VERSION}.app"
    echo ""
    echo "Build complete: dist/dBsync-${VERSION}.app"
else
    echo ""
    echo "Build failed!"
    exit 1
fi
