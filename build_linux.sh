#!/bin/bash
echo "Building dBsync for Linux..."

# Ask for version update
echo "Current version: $(python3 -c 'from version import __version__; print(__version__)' 2>/dev/null || echo 'unknown')"
read -p "Enter new version (press Enter to keep current): " new_version
if [ ! -z "$new_version" ]; then
    python3 set_version.py "$new_version"
fi
echo ""

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

        # Rename with version
        VERSION=$(python3 -c "from version import __version__; print(__version__)" 2>/dev/null || echo "0.0.0")
        mv dist/dBsync "dist/dBsync-${VERSION}"
        chmod +x "dist/dBsync-${VERSION}"

        # Optional: Create AppImage
        if command -v appimagetool &> /dev/null; then
            echo "Creating AppImage..."

            # Create AppDir structure
            mkdir -p dist/dBsync.AppDir/usr/bin
            mkdir -p dist/dBsync.AppDir/usr/share/applications
            mkdir -p dist/dBsync.AppDir/usr/share/icons/hicolor/256x256/apps

            # Copy executable (use versioned name as source)
            cp "dist/dBsync-${VERSION}" dist/dBsync.AppDir/usr/bin/dBsync

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

            # Copy icon
            if [ -f icon.png ]; then
                cp icon.png dist/dBsync.AppDir/dbsync.png
                cp icon.png dist/dBsync.AppDir/usr/share/icons/hicolor/256x256/apps/dbsync.png
            fi

            # Create AppRun
            cat > dist/dBsync.AppDir/AppRun <<'EOF'
#!/bin/bash
HERE="$(dirname "$(readlink -f "${0}")")"
exec "${HERE}/usr/bin/dBsync" "$@"
EOF
            chmod +x dist/dBsync.AppDir/AppRun

            # Build AppImage
            ARCH=x86_64 appimagetool dist/dBsync.AppDir "dist/dBsync-${VERSION}.AppImage"

            echo ""
            echo "Build complete!"
            echo "Executable: dist/dBsync-${VERSION}"
            echo "AppImage: dist/dBsync-${VERSION}.AppImage"
        else
            echo ""
            echo "Build complete: dist/dBsync-${VERSION}"
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