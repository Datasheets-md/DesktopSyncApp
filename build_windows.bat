@echo off
echo Building dBsync for Windows...

REM Show current version
for /f "delims=" %%i in ('python -c "from version import __version__; print(__version__)" 2^>nul') do set current_version=%%i
if "%current_version%"=="" set current_version=unknown
echo Current version: %current_version%

REM Ask for new version
set /p new_version="Enter new version (press Enter to keep current): "
if not "%new_version%"=="" (
    python set_version.py %new_version%
)
echo.

REM Remove old builds
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build

REM Build with PyInstaller
REM Check if icon exists
if exist icon.ico (
    echo Using icon.ico
    python -m PyInstaller ^
        --onefile ^
        --windowed ^
        --name dBsync ^
        --add-data "dbsync.json;." ^
        --icon icon.ico ^
        --clean ^
        dbsync.py
) else (
    echo No icon.ico found, building without icon
    python -m PyInstaller ^
        --onefile ^
        --windowed ^
        --name dBsync ^
        --add-data "dbsync.json;." ^
        --clean ^
        dbsync.py
)

if %errorlevel% equ 0 (
    echo.
    echo Build complete: dist\dBsync.exe
) else (
    echo.
    echo Build failed!
    exit /b 1
)