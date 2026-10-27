"""Pure helper functions in main.py (needs Kivy installed; no window is opened)."""

import os

import pytest

pytest.importorskip("kivy")
pytestmark = pytest.mark.ui

import main  # noqa: E402


@pytest.mark.parametrize("seconds, text", [
    (0, "0s"), (0.4, "0s"), (45, "45s"), (59.6, "1m 00s"), (65, "1m 05s"),
    (3599, "59m 59s"), (3600, "1h 00m"), (7322, "2h 02m"), (-5, "0s"),
])
def test_format_duration(seconds, text):
    assert main.format_duration(seconds) == text


def test_status_colours():
    green, red, amber = main.status_color("valid"), main.status_color("disposable"), \
        main.status_color("role account")
    assert green[1] > green[0]          # greenish
    assert red[0] > red[1]              # reddish
    assert amber != green and amber != red
    assert main.status_color("not checked") == main.status_color("")


@pytest.mark.parametrize("name", ["icon.ico", "icon_small.png", "logo.png", "logo.svg"])
def test_assets_exist(name):
    assert os.path.exists(main.asset_path(name))


def test_kivy_does_not_eat_command_line_args():
    assert os.environ.get("KIVY_NO_ARGS") == "1"
