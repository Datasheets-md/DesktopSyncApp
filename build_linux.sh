#!/bin/bash
echo "Building dBsync for Linux..."

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
        --name dBsync \
        --add-data "dbsync.json:." \
        --clean \
        dbsync.py

    if [ $? -eq 0 ]; then
        # Make executable
        chmod +x dist/dBsync

        # Optional: Create AppImage
        if command -v appimagetool &> /dev/null; then
            echo "Creating AppImage..."

            # Create AppDir structure
            mkdir -p dist/dBsync.AppDir/usr/bin
            mkdir -p dist/dBsync.AppDir/usr/share/applications
            mkdir -p dist/dBsync.AppDir/usr/share/icons/hicolor/256x256/apps

            # Copy executable
            cp dist/dBsync dist/dBsync.AppDir/usr/bin/

            # Create desktop file
            cat > dist/dBsync.AppDir/dBsync.desktop <<EOF
[Desktop Entry]
Name=dBsync
Exec=dBsync
Icon=dbsync
Type=Application
Categories=Development;Engineering;
Comment=Sync components from Datasheets.md to KiCad
EOF

            # Copy desktop file
            cp dist/dBsync.AppDir/dBsync.desktop dist/dBsync.AppDir/usr/share/applications/

            # Create AppRun
            cat > dist/dBsync.AppDir/AppRun <<'EOF'
#!/bin/bash
HERE="$(dirname "$(readlink -f "${0}")")"
exec "${HERE}/usr/bin/dBsync" "$@"
EOF
            chmod +x dist/dBsync.AppDir/AppRun

            # Build AppImage
            ARCH=x86_64 appimagetool dist/dBsync.AppDir dist/dBsync.AppImage

            echo ""
            echo "Build complete!"
            echo "Executable: dist/dBsync"
            echo "AppImage: dist/dBsync.AppImage"
        else
            echo ""
            echo "Build complete: dist/dBsync"
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