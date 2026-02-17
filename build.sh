#!/bin/bash
cd "$(dirname "$0")"
pyinstaller \
    --onefile \
    --windowed \
    --name KiCadSync \
    --add-data "kicad_sync.json:." \
    kicad_sync.py
echo ""
echo "Build complete: dist/KiCadSync"
