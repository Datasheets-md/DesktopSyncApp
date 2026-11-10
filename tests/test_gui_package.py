import sys
from pathlib import Path

import datasheets_md

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

PYPROJECT = Path(__file__).parents[1] / "gui-package" / "pyproject.toml"


def test_gui_package_tracks_the_core_version():
    project = tomllib.loads(PYPROJECT.read_text())["project"]
    assert project["version"] == datasheets_md.__version__
    assert project["dependencies"] == [f"datasheets-md[gui]=={datasheets_md.__version__}"]
