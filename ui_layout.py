"""Screen-size helpers that make the window fit any monitor.

Nothing here imports Kivy, so :func:`prepare_window` can run *before* Kivy
starts (Kivy reads the window size and the interface scale only once, at
import time). Everything else is pure arithmetic and is unit tested.

How the app stays usable on every screen:

* **Window size** comes from the monitor's free area (screen minus taskbar),
  not a fixed 1360 x 820, so it never opens larger than the screen.
* **Interface size** (Settings) scales buttons and text on top of Windows
  display scaling, for screens where everything looks too big or too small.
* **Layout mode** ("wide", "medium", "narrow") is picked from the window
  width and hides the least important table columns on small windows; the
  hidden values are still shown when a row's Reason is clicked.
* **Popups** are sized to fit inside the window.
"""

from __future__ import annotations

import os
import sys
from typing import Dict, NamedTuple, Optional, Tuple

# Layout breakpoints, in dp (density-independent pixels).
WIDE_MIN_DP = 1200
MEDIUM_MIN_DP = 900
SHORT_MAX_DP = 620          # windows lower than this get a shorter input box

MODE_WIDE = "wide"
MODE_MEDIUM = "medium"
MODE_NARROW = "narrow"

# Table column widths (fractions) per layout mode. A column missing from a
# mode is hidden in that mode.
COLUMNS = ("email", "real", "confidence", "final", "parts", "reason",
           "website", "provider", "status")
COLUMN_HINTS: Dict[str, Dict[str, float]] = {
    MODE_WIDE: {"email": 0.17, "real": 0.065, "confidence": 0.065, "final": 0.16,
                "parts": 0.12, "reason": 0.14, "website": 0.10, "provider": 0.10,
                "status": 0.08},
    MODE_MEDIUM: {"email": 0.22, "real": 0.08, "confidence": 0.08, "final": 0.19,
                  "reason": 0.17, "provider": 0.13, "status": 0.13},
    MODE_NARROW: {"email": 0.30, "real": 0.10, "final": 0.26, "reason": 0.18,
                  "status": 0.16},
}

# Window size limits, in dp.
MAX_WINDOW_DP = (1700, 1050)
MIN_WINDOW_DP = (720, 480)
SCREEN_FRACTION = 0.9


class WorkArea(NamedTuple):
    """Usable part of the screen (taskbar excluded), in screen pixels."""

    left: int
    top: int
    width: int
    height: int


class WindowGeometry(NamedTuple):
    """Initial window placement and minimum size, in screen pixels."""

    left: int
    top: int
    width: int
    height: int
    min_width: int
    min_height: int


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def parse_scale(choice: str) -> Optional[float]:
    """Turn an "Interface size" choice into a factor.

    Args:
        choice: ``"Auto"`` or a percentage such as ``"90%"``.

    Returns:
        ``0.9`` for ``"90%"``; None for ``"Auto"`` or anything invalid.
    """
    text = (choice or "").strip().rstrip("%").strip()
    try:
        value = float(text)
    except ValueError:
        return None
    if not 50 <= value <= 200:
        return None
    return value / 100.0


def layout_mode(width_dp: float) -> str:
    """Pick the layout for a window ``width_dp`` wide.

    Returns:
        ``"wide"`` (all columns), ``"medium"`` or ``"narrow"``.
    """
    if width_dp >= WIDE_MIN_DP:
        return MODE_WIDE
    if width_dp >= MEDIUM_MIN_DP:
        return MODE_MEDIUM
    return MODE_NARROW


def column_hints(mode: str) -> Dict[str, float]:
    """Width fraction of every column in ``mode`` (0 means hidden)."""
    shown = COLUMN_HINTS.get(mode, COLUMN_HINTS[MODE_WIDE])
    return {name: shown.get(name, 0.0) for name in COLUMNS}


def initial_geometry(area: WorkArea, density: float = 1.0) -> WindowGeometry:
    """Size and centre the window inside ``area``.

    The window takes 90% of the free screen, capped at a comfortable maximum
    on very large monitors, and never more than the screen itself.

    Args:
        area: Usable screen area in pixels.
        density: Pixels per dp (Windows scaling x Interface size).

    Returns:
        Window placement and minimum size in pixels.
    """
    density = max(0.5, float(density or 1.0))
    width = min(int(area.width * SCREEN_FRACTION), int(MAX_WINDOW_DP[0] * density))
    height = min(int(area.height * SCREEN_FRACTION), int(MAX_WINDOW_DP[1] * density))
    width = max(1, min(width, area.width))
    height = max(1, min(height, area.height))
    min_width = min(int(MIN_WINDOW_DP[0] * density), width)
    min_height = min(int(MIN_WINDOW_DP[1] * density), height)
    left = area.left + (area.width - width) // 2
    top = area.top + (area.height - height) // 2
    return WindowGeometry(left, top, width, height, min_width, min_height)


def fit_size(want: Tuple[float, float], window: Tuple[float, float],
             margin: float = 0.95) -> Tuple[float, float]:
    """Shrink a popup size so it fits inside the window.

    Args:
        want: Preferred ``(width, height)`` in pixels.
        window: Current window ``(width, height)`` in pixels.
        margin: Largest fraction of the window the popup may use.

    Returns:
        ``(width, height)`` no larger than ``margin`` of the window.
    """
    return (min(want[0], window[0] * margin), min(want[1], window[1] * margin))


# ---------------------------------------------------------------------------
# Windows queries (all optional: any failure falls back to Kivy defaults)
# ---------------------------------------------------------------------------

def system_density() -> float:
    """Windows display scaling as a factor (1.0 = 100%, 1.5 = 150%)."""
    if sys.platform != "win32":
        return 1.0
    try:
        import ctypes

        dpi = ctypes.windll.user32.GetDpiForSystem()
        return dpi / 96.0 if dpi else 1.0
    except Exception:
        return 1.0


def screen_work_area() -> Optional[WorkArea]:
    """The primary monitor's free area (taskbar excluded), or None."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        rect = wintypes.RECT()
        spi_getworkarea = 0x0030
        if not ctypes.windll.user32.SystemParametersInfoW(
                spi_getworkarea, 0, ctypes.byref(rect), 0):
            return None
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if width <= 0 or height <= 0:
            return None
        return WorkArea(rect.left, rect.top, width, height)
    except Exception:
        return None


def prepare_window(config, ui_scale: str = "Auto") -> Optional[WindowGeometry]:
    """Apply the interface size and a screen-fitting window size.

    Must be called after ``from kivy.config import Config`` and before any
    other Kivy import.

    Args:
        config: ``kivy.config.Config``.
        ui_scale: The saved "Interface size" choice.

    Returns:
        The geometry used, or None when the screen size is unknown (then a
        conservative 1100 x 700 window is used).
    """
    density = system_density()
    factor = parse_scale(ui_scale)
    if factor is not None and not os.environ.get("KIVY_METRICS_DENSITY"):
        density *= factor
        os.environ["KIVY_METRICS_DENSITY"] = f"{density:.3f}"

    area = screen_work_area()
    if area is None:
        geometry = None
        size, minimum = (1100, 700), (720, 480)
    else:
        geometry = initial_geometry(area, density)
        size = (geometry.width, geometry.height)
        minimum = (geometry.min_width, geometry.min_height)
        config.set("graphics", "position", "custom")
        config.set("graphics", "left", str(geometry.left))
        config.set("graphics", "top", str(geometry.top))
    config.set("graphics", "width", str(size[0]))
    config.set("graphics", "height", str(size[1]))
    config.set("graphics", "minimum_width", str(minimum[0]))
    config.set("graphics", "minimum_height", str(minimum[1]))
    return geometry
