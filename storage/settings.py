"""Load and save user settings in %APPDATA%/EmailNameResolver/settings.json."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Optional

APP_DIR_NAME = "EmailNameResolver"
SETTINGS_FILENAME = "settings.json"


def app_data_dir() -> Path:
    """Return (and create) the per-user data folder for the app.

    On Windows this is ``%APPDATA%/EmailNameResolver``.

    NOTE: If ``APPDATA`` is not set (e.g. running tests on Linux/macOS),
    ``~/.config/EmailNameResolver`` is used instead.

    Returns:
        The folder path; it exists after this call.
    """
    base = os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / ".config"
    path = root / APP_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically (temp file + replace).

    Args:
        path: Destination file.
        text: UTF-8 content.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


@dataclass
class Settings:
    """User-editable settings."""

    model: str = "llama3.1"
    base_url: str = "http://localhost:11434"
    resolve_website: bool = False
    # Resolve confident matches from the US name lists and skip the AI.
    use_dictionary: bool = True
    # Emails sent to Ollama at the same time (1-4). Higher helps with a GPU.
    ai_parallel: int = 1
    # Detect the mail provider (Google Workspace, Microsoft 365...) and check
    # the domain can receive email, via DNS-over-HTTPS (domains only are sent).
    check_domains: bool = True
    dns_service: str = "cloudflare"      # "cloudflare" or "google"
    # Last choices in the Export dialog.
    export_format: str = "mailing"       # "mailing" or "full"
    export_only_resolved: bool = True


def dictionaries_dir() -> Path:
    """Return the folder for the user's own name lists.

    Returns:
        ``%APPDATA%/EmailNameResolver/dictionaries`` (not created here).
    """
    return app_data_dir() / "dictionaries"


def settings_path() -> Path:
    """Return the path of ``settings.json``."""
    return app_data_dir() / SETTINGS_FILENAME


def load_settings(path: Optional[Path] = None) -> Settings:
    """Load settings, falling back to defaults for missing or bad values.

    Args:
        path: Override file location (used by tests).

    Returns:
        A :class:`Settings` instance. A missing or corrupt file yields defaults.
    """
    path = path or settings_path()
    defaults = Settings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return defaults
    if not isinstance(raw, dict):
        return defaults

    values = {}
    for f in fields(Settings):
        value = raw.get(f.name, getattr(defaults, f.name))
        expected = type(getattr(defaults, f.name))
        if not isinstance(value, expected) or (expected is str and not value.strip()):
            value = getattr(defaults, f.name)
        values[f.name] = value.strip() if isinstance(value, str) else value
    values["ai_parallel"] = max(1, min(4, values["ai_parallel"]))
    if values["dns_service"] not in ("cloudflare", "google"):
        values["dns_service"] = defaults.dns_service
    if values["export_format"] not in ("mailing", "full"):
        values["export_format"] = defaults.export_format
    return Settings(**values)


def save_settings(settings: Settings, path: Optional[Path] = None) -> Path:
    """Persist settings as JSON.

    Args:
        settings: Settings to save.
        path: Override file location (used by tests).

    Returns:
        The path written.
    """
    path = path or settings_path()
    atomic_write_text(path, json.dumps(asdict(settings), indent=2))
    return path
