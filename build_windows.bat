@echo off
echo Building dBsync for Windows...

REM Remove old builds
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build

REM Build with PyInstaller
python -m PyInstaller ^
    --onefile ^
    --windowed ^
    --name dBsync ^
    --add-data "dbsync.json;." ^
    --icon icon.ico ^
    --clean ^
    dbsync.py

if %errorlevel% equ 0 (
    echo.
    echo Build complete: dist\dBsync.exe
) else (
    echo.
    echo Build failed!
    exit /b 1
)