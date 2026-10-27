"""Export result rows to CSV files that Excel and email senders open cleanly.

Two formats:

* **Full analysis** (:func:`export_rows_to_csv`): every pipeline column.
* **Email sender list** (:func:`export_mailing_csv`): ``Email Address,
  First Name, Last Name, Full Name, Company Website, Mail Provider,
  Email Status``. These header names
  are auto-matched by Mailchimp, Brevo/Sendinblue, MailerLite, GMass and
  most other bulk email tools, so the file imports without manual mapping.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, Union

from pipeline.splitter import PARTICLES

# NOTE: The first seven columns are the original spec, unchanged and in
# order. mail_provider and email_status were added later (provider detection
# and domain verification) and are appended at the end so tools reading the
# original columns by position keep working.
CSV_COLUMNS: List[str] = [
    "email", "is_real_name", "confidence", "final_name",
    "name_parts", "reason", "company_website", "mail_provider", "email_status",
]
NAME_PARTS_SEPARATOR = " | "

MAILING_COLUMNS: List[str] = [
    "Email Address", "First Name", "Last Name", "Full Name", "Company Website",
    "Mail Provider", "Email Status",
]

# Statuses an email tool should never be sent to (see pipeline.mail_domain).
UNDELIVERABLE_STATUSES = {"invalid syntax", "domain doesn't exist", "no mail server",
                          "disposable"}

FORMAT_MAILING = "mailing"
FORMAT_FULL = "full"

# Leading characters that make Excel treat a cell as a formula.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def default_export_filename(now: Optional[datetime] = None,
                            export_format: str = FORMAT_FULL) -> str:
    """Return the default export file name for ``now``.

    Args:
        now: Timestamp to use (defaults to the current local time).
        export_format: ``"full"`` -> ``email_names_YYYYMMDD_HHMMSS.csv``;
            ``"mailing"`` -> ``email_list_YYYYMMDD_HHMMSS.csv``.

    Returns:
        The default file name.
    """
    now = now or datetime.now()
    prefix = "email_list" if export_format == FORMAT_MAILING else "email_names"
    return f"{prefix}_{now:%Y%m%d_%H%M%S}.csv"


def is_resolved(row: Dict[str, Any]) -> bool:
    """Tell whether a row has a usable name.

    A row is resolved when its Final Name is non-empty. That covers AI and
    dictionary matches (which always fill it) and names the user typed in
    by hand; non-credible verdicts always leave it empty.

    Args:
        row: A result row.

    Returns:
        True if the row's ``final_name`` contains text.
    """
    return bool(str(row.get("final_name") or "").strip())


def is_valid_email(email: str) -> bool:
    """Return True for a structurally valid address (``local@domain``)."""
    email = (email or "").strip()
    if "@" not in email or any(c.isspace() for c in email):
        return False
    local, domain = email.rsplit("@", 1)
    return bool(local) and bool(domain)


def count_resolved(rows: Iterable[Dict[str, Any]]) -> Tuple[int, int]:
    """Count resolved and unresolved rows.

    Args:
        rows: Result rows.

    Returns:
        ``(resolved, unresolved)``.
    """
    resolved = unresolved = 0
    for row in rows:
        if is_resolved(row):
            resolved += 1
        else:
            unresolved += 1
    return resolved, unresolved


def filter_rows(rows: Iterable[Dict[str, Any]], only_resolved: bool,
                drop_invalid: bool) -> Tuple[List[Dict[str, Any]], int, int]:
    """Select the rows to export.

    Args:
        rows: Result rows.
        only_resolved: Skip rows without a Final Name.
        drop_invalid: Skip structurally invalid email addresses.

    Returns:
        ``(kept_rows, skipped_unresolved, skipped_invalid)``.
    """
    kept: List[Dict[str, Any]] = []
    skipped_unresolved = skipped_invalid = 0
    for row in rows:
        if drop_invalid and not is_valid_email(str(row.get("email") or "")):
            skipped_invalid += 1
        elif only_resolved and not is_resolved(row):
            skipped_unresolved += 1
        else:
            kept.append(row)
    return kept, skipped_unresolved, skipped_invalid


def provider_of(row: Dict[str, Any]) -> str:
    """The row's mail provider label (``"Unknown"`` if not detected)."""
    return str(row.get("mail_provider") or "").strip() or "Unknown"


def is_undeliverable(row: Dict[str, Any]) -> bool:
    """True for invalid, non-existent, mail-less or disposable addresses."""
    return (not is_valid_email(str(row.get("email") or ""))
            or str(row.get("email_status") or "") in UNDELIVERABLE_STATUSES)


def provider_counts(rows: Iterable[Dict[str, Any]]) -> List[Tuple[str, int]]:
    """Count rows per provider, largest first.

    Args:
        rows: Result rows.

    Returns:
        ``[(provider, count), ...]`` sorted by count (desc) then name.
    """
    counts: Dict[str, int] = {}
    for row in rows:
        label = provider_of(row)
        counts[label] = counts.get(label, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))


def domain_sort_key(row: Dict[str, Any]) -> Tuple[str, str, str]:
    """Sort key: provider, then domain, then local part (case-insensitive)."""
    email = str(row.get("email") or "").lower()
    local, _, domain = email.rpartition("@")
    return provider_of(row).lower(), domain, local


@dataclass
class ExportSelection:
    """What to include in an export."""

    only_resolved: bool = True
    skip_undeliverable: bool = True
    providers: Optional[Set[str]] = None   # None = all providers
    sort_by_domain: bool = True


def select_rows(rows: Iterable[Dict[str, Any]], sel: ExportSelection
                ) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Filter (and optionally sort) rows for export.

    Args:
        rows: Result rows.
        sel: The user's choices.

    Returns:
        ``(kept_rows, skipped)`` where ``skipped`` counts rows left out per
        reason: ``"provider"``, ``"undeliverable"``, ``"unresolved"``.
    """
    kept: List[Dict[str, Any]] = []
    skipped = {"provider": 0, "undeliverable": 0, "unresolved": 0}
    for row in rows:
        if sel.providers is not None and provider_of(row) not in sel.providers:
            skipped["provider"] += 1
        elif sel.skip_undeliverable and is_undeliverable(row):
            skipped["undeliverable"] += 1
        elif sel.only_resolved and not is_resolved(row):
            skipped["unresolved"] += 1
        else:
            kept.append(row)
    if sel.sort_by_domain:
        kept.sort(key=domain_sort_key)
    return kept, skipped


def group_by_provider(rows: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Split rows into ``{provider: rows}`` (order of first appearance)."""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(provider_of(row), []).append(row)
    return groups


def per_provider_path(base: Union[str, Path], provider: str) -> Path:
    """``C:/x/email_list.csv`` + ``"Gmail"`` -> ``C:/x/email_list_gmail.csv``."""
    base = Path(base)
    label = re.sub(r"[^a-z0-9]+", "_", provider.lower()).strip("_") or "other"
    return base.with_name(f"{base.stem}_{label}{base.suffix or '.csv'}")


def split_full_name(full_name: str) -> Tuple[str, str]:
    """Split a display name into ``(first_name, last_name)`` for mail merge.

    * Middle names are dropped from First/Last (they stay in Full Name).
    * Surname particles stay with the surname: "Mary van der Berg" ->
      ("Mary", "van der Berg").
    * NOTE: A first name that is only an initial ("J Doe") is left blank, so
      email tools fall back to their default greeting instead of "Hi J,".

    Args:
        full_name: The row's Final Name.

    Returns:
        ``(first_name, last_name)``; either may be empty.
    """
    parts = full_name.split()
    if not parts:
        return "", ""
    first = parts[0]
    if len(first.strip(".")) <= 1:
        first = ""
    if len(parts) == 1:
        return first, ""
    start = len(parts) - 1
    while start - 1 >= 1 and parts[start - 1].lower() in PARTICLES:
        start -= 1
    return first, " ".join(parts[start:])


def row_to_mailing_record(row: Dict[str, Any]) -> List[str]:
    """Convert one result row into an email-sender CSV record.

    Args:
        row: A result row (Final Name includes the user's edits).

    Returns:
        Cell values matching :data:`MAILING_COLUMNS`.
    """
    full = " ".join(str(row.get("final_name") or "").split())
    first, last = split_full_name(full)
    return [
        _safe_cell(str(row.get("email") or "").strip()),
        _safe_cell(first),
        _safe_cell(last),
        _safe_cell(full),
        _safe_cell(str(row.get("company_website") or "")),
        _safe_cell(str(row.get("mail_provider") or "")),
        _safe_cell(str(row.get("email_status") or "")),
    ]


def export_mailing_csv(rows: Iterable[Dict[str, Any]], path: Union[str, Path]) -> int:
    """Write rows as an email-sender import file (UTF-8 with BOM).

    Columns: ``Email Address, First Name, Last Name, Full Name,
    Company Website``.

    Args:
        rows: Rows to write (already filtered with :func:`filter_rows`).
        path: Destination file path.

    Returns:
        The number of data rows written.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(MAILING_COLUMNS)
        for row in rows:
            writer.writerow(row_to_mailing_record(row))
            count += 1
    return count


def _safe_cell(value: str) -> str:
    """Neutralise spreadsheet formula injection in a text cell.

    NOTE: Text such as an AI-written reason starting with "=" would run as a
    formula in Excel. Such cells are prefixed with an apostrophe, the
    standard safe default. Normal emails, names and URLs are unaffected.
    """
    if value and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def row_to_csv_record(row: Dict[str, Any]) -> List[str]:
    """Convert one result row into CSV cell values, in column order.

    NOTE: ``is_real_name`` is written as ``true``/``false`` (not yes/no) so
    downstream tools get a stable boolean contract.

    Args:
        row: A result row dict.

    Returns:
        Cell values matching :data:`CSV_COLUMNS`.
    """
    parts = row.get("name_parts") or []
    if isinstance(parts, str):
        parts = [parts]
    try:
        confidence = str(int(row.get("confidence") or 0))
    except (TypeError, ValueError):
        confidence = "0"
    return [
        _safe_cell(str(row.get("email") or "")),
        "true" if row.get("is_real_name") else "false",
        confidence,
        _safe_cell(str(row.get("final_name") or "")),
        _safe_cell(NAME_PARTS_SEPARATOR.join(str(p) for p in parts)),
        _safe_cell(str(row.get("reason") or "")),
        _safe_cell(str(row.get("company_website") or "")),
        _safe_cell(str(row.get("mail_provider") or "")),
        _safe_cell(str(row.get("email_status") or "")),
    ]


def export_rows_to_csv(rows: Iterable[Dict[str, Any]], path: Union[str, Path]) -> int:
    """Write rows to ``path`` as UTF-8-with-BOM CSV.

    Columns, in order: ``email, is_real_name, confidence, final_name,
    name_parts, reason, company_website``. ``name_parts`` is joined with
    ``" | "``.

    Args:
        rows: Row dicts (use the current table values, including edits).
        path: Destination file path.

    Returns:
        The number of data rows written.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(CSV_COLUMNS)
        for row in rows:
            writer.writerow(row_to_csv_record(row))
            count += 1
    return count


def rows_to_csv_bytes(rows: Iterable[Dict[str, Any]], export_format: str) -> Tuple[bytes, int]:
    """Render rows as UTF-8-with-BOM CSV bytes (same content as the file writers).

    Args:
        rows: Rows to write.
        export_format: ``"mailing"`` or ``"full"``.

    Returns:
        ``(csv_bytes, row_count)``.
    """
    mailing = export_format == FORMAT_MAILING
    buf = io.StringIO(newline="")
    writer = csv.writer(buf)
    writer.writerow(MAILING_COLUMNS if mailing else CSV_COLUMNS)
    count = 0
    for row in rows:
        writer.writerow(row_to_mailing_record(row) if mailing else row_to_csv_record(row))
        count += 1
    return buf.getvalue().encode("utf-8-sig"), count


def export_provider_zip(groups: Dict[str, List[Dict[str, Any]]], zip_path: Union[str, Path],
                        export_format: str) -> Dict[str, int]:
    """Write one CSV per mail provider into a single ZIP file.

    Inside ``email_list_20261027_190000.zip`` the files are named
    ``email_list_20261027_190000_gmail.csv``, ``..._microsoft_365.csv``, etc.

    Args:
        groups: ``{provider: rows}`` (see :func:`group_by_provider`).
        zip_path: Destination ``.zip`` path.
        export_format: ``"mailing"`` or ``"full"``.

    Returns:
        ``{file_name_inside_zip: row_count}`` in the order written.
    """
    zip_path = Path(zip_path)
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    base = zip_path.with_suffix(".csv")
    contents: Dict[str, int] = {}
    tmp = zip_path.with_name(zip_path.name + ".tmp")
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for provider, rows in groups.items():
                name = per_provider_path(base, provider).name
                if name in contents:  # two labels with the same slug
                    name = name.replace(".csv", f"_{len(contents) + 1}.csv")
                data, count = rows_to_csv_bytes(rows, export_format)
                zf.writestr(name, data)
                contents[name] = count
        tmp.replace(zip_path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return contents
