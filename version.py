"""App version and developer information: the ONE place to edit them.

Everything else reads from here:

* the window title ("Email Name Resolver v1.0.0"),
* the Help guide header,
* the .exe's Windows properties (right-click > Properties > Details),
* the GitHub release check (the tag must equal "v" + __version__).

When you release a new version, change ``__version__`` below.
Use MAJOR.MINOR.PATCH:

* PATCH (1.0.0 -> 1.0.1): bug fixes only
* MINOR (1.0.1 -> 1.1.0): new features, nothing removed
* MAJOR (1.1.0 -> 2.0.0): big changes (e.g. CSV columns renamed)
"""

from __future__ import annotations

from typing import Tuple

__version__ = "1.1.0"

APP_NAME = "Email Name Resolver"
APP_ID = "EmailNameResolver"            # .exe name, %APPDATA% folder name
DESCRIPTION = "Finds people's names in email lists using a local AI (Ollama)"

DEVELOPER = "niyimarc"                  # shown in the app and in .exe Properties
CONTACT_EMAIL = ""                      # optional, e.g. "you@example.com"
REPO_URL = "https://github.com/niyimarc/email_name_resolver"
COPYRIGHT_YEAR = "2026"


def version_tuple(version: str = __version__) -> Tuple[int, int, int, int]:
    """Return the version as 4 integers for Windows, e.g. ``(1, 0, 0, 0)``.

    Args:
        version: A ``MAJOR.MINOR.PATCH`` string.

    Returns:
        ``(major, minor, patch, 0)``.

    Raises:
        ValueError: If the version is not three dot-separated numbers.
    """
    parts = version.strip().split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"version must look like 1.2.3, got {version!r}")
    major, minor, patch = (int(p) for p in parts)
    return major, minor, patch, 0


def copyright_line() -> str:
    """``"Copyright 2026 niyimarc"``."""
    return f"Copyright {COPYRIGHT_YEAR} {DEVELOPER}"


def about_text() -> str:
    """One line for the UI, e.g. ``"v1.0.0  |  by niyimarc"``."""
    return f"v{__version__}  |  by {DEVELOPER}"


if __name__ == "__main__":  # `python version.py` prints the version (used in docs)
    print(__version__)
