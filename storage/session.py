"""Load and save the last results table in %APPDATA%/EmailNameResolver."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .settings import app_data_dir, atomic_write_text

SESSION_FILENAME = "last_session.json"
SESSION_VERSION = 1

ROW_DEFAULTS: Dict[str, Any] = {
    "email": "",
    "is_real_name": False,
    "confidence": 0,
    "final_name": "",
    "name_parts": [],
    "reason": "",
    "company_website": "",
    "website_verified": False,
    "mail_provider": "",
    "email_status": "",
}


def session_path() -> Path:
    """Return the path of ``last_session.json``."""
    return app_data_dir() / SESSION_FILENAME


def normalize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce a stored row into the shape the UI expects.

    Args:
        row: A row dict, possibly from an older or hand-edited file.

    Returns:
        A new dict containing exactly the keys in :data:`ROW_DEFAULTS`.
    """
    out = dict(ROW_DEFAULTS)
    out["email"] = str(row.get("email") or "")
    out["is_real_name"] = bool(row.get("is_real_name"))
    try:
        out["confidence"] = max(0, min(100, int(row.get("confidence") or 0)))
    except (TypeError, ValueError):
        out["confidence"] = 0
    out["final_name"] = str(row.get("final_name") or "")
    parts = row.get("name_parts") or []
    out["name_parts"] = [str(p) for p in parts] if isinstance(parts, list) else []
    out["reason"] = str(row.get("reason") or "")
    out["company_website"] = str(row.get("company_website") or "")
    out["website_verified"] = bool(row.get("website_verified"))
    out["mail_provider"] = str(row.get("mail_provider") or "")
    out["email_status"] = str(row.get("email_status") or "")
    return out


def save_session(rows: List[Dict[str, Any]], path: Optional[Path] = None) -> Path:
    """Save result rows to the session file.

    Args:
        rows: Row dicts (a snapshot; not mutated).
        path: Override file location (used by tests).

    Returns:
        The path written.
    """
    path = path or session_path()
    payload = {
        "version": SESSION_VERSION,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "rows": [normalize_row(r) for r in rows],
    }
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=1))
    return path


def load_session(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Load saved rows, returning an empty list if none or unreadable.

    Args:
        path: Override file location (used by tests).

    Returns:
        A list of normalised row dicts.
    """
    path = path or session_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    return [normalize_row(r) for r in rows if isinstance(r, dict)]


def clear_session(path: Optional[Path] = None) -> None:
    """Delete the saved session file if it exists.

    Args:
        path: Override file location (used by tests).
    """
    path = path or session_path()
    try:
        path.unlink()
    except FileNotFoundError:
        pass
