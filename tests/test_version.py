"""version.py: the single source of the app version and developer info."""

import re
import subprocess
import sys

import pytest

import version


def test_version_is_semantic():
    assert re.fullmatch(r"\d+\.\d+\.\d+", version.__version__)


@pytest.mark.parametrize("text, expected", [
    ("1.0.0", (1, 0, 0, 0)), ("2.13.7", (2, 13, 7, 0)), (" 1.2.3 ", (1, 2, 3, 0)),
])
def test_version_tuple(text, expected):
    assert version.version_tuple(text) == expected


@pytest.mark.parametrize("bad", ["1.0", "v1.0.0", "1.0.0-beta", "1.a.0", ""])
def test_version_tuple_rejects_bad_versions(bad):
    with pytest.raises(ValueError):
        version.version_tuple(bad)


def test_developer_info_is_filled_in():
    assert version.APP_NAME and version.APP_ID and version.DEVELOPER
    assert version.REPO_URL.startswith("https://github.com/")
    assert version.DEVELOPER in version.copyright_line()
    assert version.about_text() == f"v{version.__version__}  |  by {version.DEVELOPER}"


def test_running_version_py_prints_the_version():
    """The release workflow compares the tag with `python version.py`."""
    out = subprocess.run([sys.executable, "version.py"], capture_output=True, text=True,
                         check=True).stdout.strip()
    assert out == version.__version__


def test_app_id_matches_appdata_folder_and_exe_name():
    from storage.settings import APP_DIR_NAME
    assert version.APP_ID == APP_DIR_NAME == "EmailNameResolver"
