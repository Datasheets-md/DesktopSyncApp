#!/bin/bash
echo "Building KiCadSync for Linux..."

# Remove old builds
rm -rf dist/
rm -rf build/

# Detect if we're building on Linux
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    # For AppImage (recommended for distribution)
    if command -v pyinstaller &> /dev/null; then
        PYINSTALLER="pyinstaller"
    else
        PYINSTALLER="python3 -m PyInstaller"
    fi

    # Build single file executable
    $PYINSTALLER \
        --onefile \
        --windowed \
        --name KiCadSync \
        --add-data "kicad_sync.json:." \
        --clean \
        kicad_sync.py

    if [ $? -eq 0 ]; then
        # Make executable
        chmod +x dist/KiCadSync

        # Optional: Create AppImage
        if command -v appimagetool &> /dev/null; then
            echo "Creating AppImage..."

            # Create AppDir structure
            mkdir -p dist/KiCadSync.AppDir/usr/bin
            mkdir -p dist/KiCadSync.AppDir/usr/share/applications
            mkdir -p dist/KiCadSync.AppDir/usr/share/icons/hicolor/256x256/apps

            # Copy executable
            cp dist/KiCadSync dist/KiCadSync.AppDir/usr/bin/

            # Create desktop file
            cat > dist/KiCadSync.AppDir/KiCadSync.desktop <<EOF
[Desktop Entry]
Name=KiCadSync
Exec=KiCadSync
Icon=kicadsync
Type=Application
Categories=Development;Engineering;
Comment=Sync components from Datasheets.md to KiCad
EOF

            # Copy desktop file
            cp dist/KiCadSync.AppDir/KiCadSync.desktop dist/KiCadSync.AppDir/usr/share/applications/

            # Create AppRun
            cat > dist/KiCadSync.AppDir/AppRun <<'EOF'
#!/bin/bash
HERE="$(dirname "$(readlink -f "${0}")")"
exec "${HERE}/usr/bin/KiCadSync" "$@"
EOF
            chmod +x dist/KiCadSync.AppDir/AppRun

            # Build AppImage
            ARCH=x86_64 appimagetool dist/KiCadSync.AppDir dist/KiCadSync.AppImage

            echo ""
            echo "Build complete!"
            echo "Executable: dist/KiCadSync"
            echo "AppImage: dist/KiCadSync.AppImage"
        else
            echo ""
            echo "Build complete: dist/KiCadSync"
            echo "To create AppImage, install appimagetool:"
            echo "  wget https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"
            echo "  chmod +x appimagetool-x86_64.AppImage"
            echo "  sudo mv appimagetool-x86_64.AppImage /usr/local/bin/appimagetool"
        fi
    else
        echo ""
        echo "Build failed!"
        exit 1
    fi
else
    echo "This script is for Linux. Use build.sh for macOS or build_windows.bat for Windows."
    exit 1
fi