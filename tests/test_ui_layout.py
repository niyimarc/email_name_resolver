"""Screen-fitting window size, layout modes and the Interface size setting."""

import json

import pytest

import ui_layout
from ui_layout import (
    COLUMNS,
    MODE_MEDIUM,
    MODE_NARROW,
    MODE_WIDE,
    WorkArea,
    column_hints,
    fit_size,
    initial_geometry,
    layout_mode,
    parse_scale,
    prepare_window,
)
from storage.settings import UI_SCALE_CHOICES, load_settings, settings_path


class FakeConfig:
    """Records Config.set calls."""

    def __init__(self):
        self.values = {}

    def set(self, section, key, value):
        self.values[(section, key)] = value


# -- interface size ---------------------------------------------------------------

@pytest.mark.parametrize("choice, expected", [
    ("80%", 0.8), ("100%", 1.0), ("125%", 1.25), (" 90 % ", 0.9),
    ("Auto", None), ("", None), ("abc", None), ("10%", None), ("500%", None),
])
def test_parse_scale(choice, expected):
    assert parse_scale(choice) == expected


def test_every_settings_choice_parses():
    for choice in UI_SCALE_CHOICES:
        assert choice == "Auto" or parse_scale(choice) is not None


def test_ui_scale_defaults_to_auto_and_rejects_junk():
    assert load_settings().ui_scale == "Auto"
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"ui_scale": "300%"}), encoding="utf-8")
    assert load_settings().ui_scale == "Auto"
    path.write_text(json.dumps({"ui_scale": "90%"}), encoding="utf-8")
    assert load_settings().ui_scale == "90%"


# -- layout modes -------------------------------------------------------------------

@pytest.mark.parametrize("width, mode", [
    (1920, MODE_WIDE), (1200, MODE_WIDE), (1199, MODE_MEDIUM), (900, MODE_MEDIUM),
    (899, MODE_NARROW), (500, MODE_NARROW),
])
def test_layout_mode(width, mode):
    assert layout_mode(width) == mode


@pytest.mark.parametrize("mode", [MODE_WIDE, MODE_MEDIUM, MODE_NARROW])
def test_column_hints_cover_every_column_and_sum_to_one(mode):
    hints = column_hints(mode)
    assert set(hints) == set(COLUMNS)
    assert sum(hints.values()) == pytest.approx(1.0)


def test_small_windows_hide_less_important_columns():
    wide, medium, narrow = (column_hints(m) for m in (MODE_WIDE, MODE_MEDIUM, MODE_NARROW))
    assert all(wide.values())
    assert medium["parts"] == medium["website"] == 0
    assert narrow["confidence"] == narrow["provider"] == 0
    for always in ("email", "real", "final", "reason", "status"):
        assert narrow[always] > 0


def test_unknown_mode_falls_back_to_wide():
    assert column_hints("huge") == column_hints(MODE_WIDE)


# -- window geometry ---------------------------------------------------------------

@pytest.mark.parametrize("area", [
    WorkArea(0, 0, 1366, 728),      # common laptop, taskbar excluded
    WorkArea(0, 0, 1024, 728),
    WorkArea(0, 0, 800, 560),       # tiny RDP window
    WorkArea(0, 0, 3840, 2100),     # 4K
    WorkArea(-1920, 0, 1920, 1040), # monitor left of the primary one
])
@pytest.mark.parametrize("density", [1.0, 1.25, 1.5, 2.0])
def test_window_always_fits_the_screen(area, density):
    g = initial_geometry(area, density)
    assert 0 < g.width <= area.width and 0 < g.height <= area.height
    assert area.left <= g.left and g.left + g.width <= area.left + area.width
    assert area.top <= g.top and g.top + g.height <= area.top + area.height
    assert g.min_width <= g.width and g.min_height <= g.height


def test_window_uses_most_of_a_normal_screen_and_is_centred():
    g = initial_geometry(WorkArea(0, 0, 1366, 728), 1.0)
    assert (g.width, g.height) == (1229, 655)
    assert g.left == (1366 - 1229) // 2 and g.top == (728 - 655) // 2


def test_window_is_capped_on_huge_screens():
    g = initial_geometry(WorkArea(0, 0, 3840, 2100), 1.0)
    assert (g.width, g.height) == (1700, 1050)


def test_bad_density_is_treated_as_one():
    assert initial_geometry(WorkArea(0, 0, 1000, 700), 0) == \
        initial_geometry(WorkArea(0, 0, 1000, 700), 1.0)


def test_fit_size_shrinks_only_when_needed():
    assert fit_size((500, 300), (1600, 900)) == (500, 300)
    assert fit_size((820, 600), (800, 500)) == (760, 475)


# -- prepare_window ------------------------------------------------------------------

def test_prepare_window_without_screen_info(monkeypatch):
    monkeypatch.setattr(ui_layout, "screen_work_area", lambda: None)
    monkeypatch.delenv("KIVY_METRICS_DENSITY", raising=False)
    cfg = FakeConfig()
    assert prepare_window(cfg, "Auto") is None
    assert cfg.values[("graphics", "width")] == "1100"
    assert cfg.values[("graphics", "minimum_height")] == "480"
    assert "KIVY_METRICS_DENSITY" not in __import__("os").environ


def test_prepare_window_uses_screen_and_scale(monkeypatch):
    monkeypatch.setattr(ui_layout, "screen_work_area", lambda: WorkArea(0, 0, 1366, 728))
    monkeypatch.setattr(ui_layout, "system_density", lambda: 1.5)
    monkeypatch.delenv("KIVY_METRICS_DENSITY", raising=False)
    cfg = FakeConfig()
    g = prepare_window(cfg, "80%")
    import os
    assert os.environ["KIVY_METRICS_DENSITY"] == "1.200"
    assert cfg.values[("graphics", "position")] == "custom"
    assert cfg.values[("graphics", "width")] == str(g.width)
    assert int(cfg.values[("graphics", "width")]) <= 1366


def test_prepare_window_respects_user_density_env(monkeypatch):
    monkeypatch.setattr(ui_layout, "screen_work_area", lambda: None)
    monkeypatch.setenv("KIVY_METRICS_DENSITY", "2")
    prepare_window(FakeConfig(), "80%")
    import os
    assert os.environ["KIVY_METRICS_DENSITY"] == "2"


def test_windows_queries_are_safe_off_windows(monkeypatch):
    monkeypatch.setattr(ui_layout.sys, "platform", "linux")
    assert ui_layout.system_density() == 1.0
    assert ui_layout.screen_work_area() is None
