@echo off
echo Building KiCadSync for Windows...

REM Remove old builds
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build

REM Build with PyInstaller
python -m PyInstaller ^
    --onefile ^
    --windowed ^
    --name KiCadSync ^
    --add-data "kicad_sync.json;." ^
    --icon icon.ico ^
    --clean ^
    kicad_sync.py

if %errorlevel% equ 0 (
    echo.
    echo Build complete: dist\KiCadSync.exe
) else (
    echo.
    echo Build failed!
    exit /b 1
)