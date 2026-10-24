"""PyInstaller entry for the standalone dBsync binaries (the [gui] extra's window)."""
import sys

from datasheets_md.gui.app import main

if __name__ == "__main__":
    sys.exit(main())
