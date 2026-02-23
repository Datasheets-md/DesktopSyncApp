#!/usr/bin/env python3
"""
Cross-platform build script for dBsync
Builds executable for the current platform
"""

import os
import sys
import platform
import shutil
import subprocess

def clean_build():
    """Remove old build artifacts"""
    if os.path.exists('dist'):
        shutil.rmtree('dist')
    if os.path.exists('build'):
        shutil.rmtree('build')
    if os.path.exists('dBsync.spec'):
        os.remove('dBsync.spec')

def build_application():
    """Build application for current platform"""

    system = platform.system()

    # Base PyInstaller command
    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--name', 'dBsync',
        '--add-data', f'dbsync.json{os.pathsep}.',
        '--clean',
        'dbsync.py'
    ]

    if system == 'Darwin':  # macOS
        print("Building for macOS...")
        cmd.extend(['--onedir', '--windowed'])
        if os.path.exists('icon-windowed.icns'):
            cmd.extend(['--icon', 'icon-windowed.icns'])

    elif system == 'Windows':
        print("Building for Windows...")
        cmd.extend(['--onefile', '--windowed'])
        if os.path.exists('icon.ico'):
            cmd.extend(['--icon', 'icon.ico'])

    elif system == 'Linux':
        print("Building for Linux...")
        cmd.extend(['--onefile', '--windowed'])
        if os.path.exists('icon.png'):
            cmd.extend(['--icon', 'icon.png'])

    else:
        print(f"Unsupported platform: {system}")
        return False

    print(f"Running: {' '.join(cmd)}")

    # Run PyInstaller
    result = subprocess.run(cmd, capture_output=False, text=True)

    if result.returncode != 0:
        print("Build failed!")
        return False

    # Platform-specific post-processing
    if system == 'Darwin':
        # Remove extra directory on macOS
        extra_dir = os.path.join('dist', 'dBsync')
        if os.path.exists(extra_dir) and not extra_dir.endswith('.app'):
            shutil.rmtree(extra_dir)
        print("\nBuild complete: dist/dBsync.app")

    elif system == 'Windows':
        print("\nBuild complete: dist/dBsync.exe")

    elif system == 'Linux':
        # Make executable on Linux
        exe_path = os.path.join('dist', 'dBsync')
        if os.path.exists(exe_path):
            os.chmod(exe_path, 0o755)
        print("\nBuild complete: dist/dBsync")
        print("To create AppImage, run: ./build_linux.sh")

    return True

def main():
    print(f"dBsync Builder")
    print(f"Platform: {platform.system()} {platform.machine()}")
    print(f"Python: {sys.version}")
    print("-" * 50)

    # Ask for version update
    try:
        from version import __version__
        print(f"Current version: {__version__}")
    except:
        print("Current version: unknown")

    new_version = input("Enter new version (press Enter to keep current): ").strip()
    if new_version:
        import set_version
        set_version.set_version(new_version)
    print("")

    # Clean old builds
    print("Cleaning old builds...")
    clean_build()

    # Build application
    if build_application():
        print("\n✅ Build successful!")

        # Show distribution info
        print("\nDistribution notes:")

        if platform.system() == 'Darwin':
            print("- For distribution: zip the .app bundle")
            print("- Users may need to right-click → Open on first launch")

        elif platform.system() == 'Windows':
            print("- For distribution: use the .exe file directly")
            print("- Consider code signing to avoid Windows Defender warnings")

        elif platform.system() == 'Linux':
            print("- For distribution: use AppImage (most compatible)")
            print("- Or distribute the binary with instructions")
    else:
        print("\n❌ Build failed!")
        sys.exit(1)

if __name__ == "__main__":
    main()