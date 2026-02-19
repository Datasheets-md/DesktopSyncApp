#!/usr/bin/env python3
import sys

def set_version(version):
    with open('version.py', 'w') as f:
        f.write(f'__version__ = "{version}"\n')
        f.write('__app_name__ = "dBsync"\n')
    print(f"Version set to: {version}")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        current_version = None
        try:
            from version import __version__
            current_version = __version__
        except:
            current_version = "1.0.0"

        print(f"Current version: {current_version}")
        version = input("Enter new version (e.g., 1.0.2): ").strip()
        if not version:
            print("No version provided, keeping current version")
            sys.exit(0)
    else:
        version = sys.argv[1]

    set_version(version)