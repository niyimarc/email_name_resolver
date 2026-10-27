"""Find, clean and deduplicate email addresses in messy text and files.

Input is pulled out by pattern rather than by position, so it works on any
layout: one email per line, comma/semicolon/space separated, emails in any
CSV column (or several per cell), "John Doe <john@x.com>" style entries,
quoted cells, ``mailto:`` links, broken or mixed delimiters, junk rows,
header rows anywhere, and common obfuscations such as ``john [at] x [dot] com``.

Supported files: ``.txt``, ``.csv``, ``.tsv`` (any delimiter or none) and
``.xlsx`` (read with the standard library). Encodings: UTF-8 (with or
without BOM), UTF-16 (Excel "Unicode text"), Windows-1252 and Latin-1.
"""

from __future__ import annotations

import html
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Set, Tuple

# Practical address pattern. NOTE: The local part allows letters, digits and
# . _ % + ' - only. RFC 5322 also allows rare symbols such as = / ? # ! that
# bulk email tools usually reject, and allowing them would glue URL text
# onto addresses ("?email=john@x.com" -> "email=john@x.com").
EMAIL_RE = re.compile(
    r"[A-Za-z0-9_%+'-]+(?:\.[A-Za-z0-9_%+'-]+)*"
    r"@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}"
    r"(?![A-Za-z0-9-])"
)

# Any run of non-separator text that contains "@" (used to report skips).
_AT_TOKEN_RE = re.compile(r"[^\s,;<>()\[\]{}\"|]*@[^\s,;<>()\[\]{}\"|]*")

_OBFUSCATED_AT_RE = re.compile(r"\s*[\[\(\{]\s*at\s*[\]\)\}]\s*", re.IGNORECASE)
_OBFUSCATED_DOT_RE = re.compile(r"\s*[\[\(\{]\s*dot\s*[\]\)\}]\s*", re.IGNORECASE)
_BOX_SPLIT_RE = re.compile(r"[\s,;]+")

MAX_SKIPPED_EXAMPLES = 5


@dataclass
class ExtractResult:
    """Emails found in some input, plus what was skipped."""

    emails: List[str] = field(default_factory=list)   # unique, in first-seen order
    duplicates: int = 0                               # repeats removed
    skipped: List[str] = field(default_factory=list)  # "@" entries that aren't emails

    def summary(self, source: str = "") -> str:
        """One-line human summary, e.g. for the status bar.

        Args:
            source: Optional prefix such as ``"Pasted"`` or a file name.

        Returns:
            Text like ``"120 new email(s) added, 15 duplicate(s) removed, ..."``.
        """
        parts = [f"{len(self.emails)} new email(s) added"]
        if self.duplicates:
            parts.append(f"{self.duplicates} duplicate(s) removed")
        if self.skipped:
            examples = ", ".join(self.skipped[:MAX_SKIPPED_EXAMPLES])
            more = "..." if len(self.skipped) > MAX_SKIPPED_EXAMPLES else ""
            parts.append(f"{len(self.skipped)} invalid entr(ies) skipped ({examples}{more})")
        text = ", ".join(parts)
        return f"{source}: {text}" if source else text


def normalize_email(email: str) -> str:
    """Tidy one address: trim wrappers/punctuation and lowercase the domain.

    The local part keeps its original case (it is technically
    case-sensitive and the original case helps the name splitter, e.g.
    ``JohnDoe``).

    Args:
        email: An address, possibly with ``mailto:``, quotes or brackets.

    Returns:
        The cleaned address (may still be invalid if the input was).
    """
    email = email.strip().strip("<>\"'()[]{}").strip()
    if email.lower().startswith("mailto:"):
        email = email[7:]
    email = email.split("?", 1)[0]  # mailto:x@y.com?subject=...
    if "@" not in email:
        return email
    local, domain = email.rsplit("@", 1)
    local = local.strip(".'")
    domain = domain.strip().strip(".").lower()
    return f"{local}@{domain}"


def email_key(email: str) -> str:
    """Return the deduplication key for an email.

    The local part is compared case-insensitively and the domain exactly
    (domains are lowercased by :func:`normalize_email`, so ``ACME.com`` and
    ``acme.com`` match).

    Args:
        email: A cleaned email string.

    Returns:
        ``local.lower() + "@" + domain`` (or the raw text if there is no ``@``).
    """
    if "@" not in email:
        return email
    local, domain = email.rsplit("@", 1)
    return f"{local.lower()}@{domain}"


def _deobfuscate(text: str) -> str:
    """Turn ``john [at] acme [dot] com`` into ``john@acme.com``."""
    if "at" not in text.lower():
        return text
    text = _OBFUSCATED_AT_RE.sub("@", text)
    return _OBFUSCATED_DOT_RE.sub(".", text)


def extract_emails(text: str, existing: Optional[Iterable[str]] = None) -> ExtractResult:
    """Find every email address in free text, cleaned and deduplicated.

    Args:
        text: Any text: pasted lists, CSV content, sentences, etc.
        existing: Emails already collected; matches count as duplicates.

    Returns:
        An :class:`ExtractResult` with unique emails in first-seen order.
    """
    text = _deobfuscate(text or "")
    seen: Set[str] = {email_key(normalize_email(e)) for e in (existing or [])}
    result = ExtractResult()
    covered: List[Tuple[int, int]] = []
    for match in EMAIL_RE.finditer(text):
        covered.append(match.span())
        email = normalize_email(match.group(0))
        key = email_key(email)
        if key in seen:
            result.duplicates += 1
            continue
        seen.add(key)
        result.emails.append(email)

    # Report "@" entries that contained no valid address (e.g. "john@",
    # "@handle", "user@localhost"), without adding them.
    covered_iter = iter(covered)
    current = next(covered_iter, None)
    for match in _AT_TOKEN_RE.finditer(text):
        start, end = match.span()
        while current is not None and current[1] <= start:
            current = next(covered_iter, None)
        if current is not None and current[0] < end and current[1] > start:
            continue  # overlaps a real email
        token = match.group(0).strip(".'")
        # NOTE: "@handle" (social media) is not a broken email; don't report it.
        if token and not token.startswith("@"):
            result.skipped.append(token)
    return result


def parse_email_input(text: str) -> List[str]:
    """Split the input box into items to analyse (not deduplicated).

    Items containing a valid address are reduced to that address (so
    ``"<john@x.com>;"`` becomes ``john@x.com``). Items without one are kept
    as-is so they show up in the table as ``invalid email``, as the spec
    requires for hand-typed entries.

    NOTE: Paste and Load file already clean the box, so in practice this
    mostly sees one valid address per line.

    Args:
        text: Raw text from the input box.

    Returns:
        Cleaned, non-empty items in input order.
    """
    items: List[str] = []
    for token in _BOX_SPLIT_RE.split(_deobfuscate(text or "")):
        token = token.strip()
        if not token:
            continue
        found = EMAIL_RE.findall(token)
        if found:
            items.extend(normalize_email(e) for e in found)
        else:
            cleaned = normalize_email(token)
            if cleaned:
                items.append(cleaned)
    return items


def dedupe_emails(emails: List[str], existing: Optional[Set[str]] = None
                  ) -> Tuple[List[str], int, int]:
    """Remove duplicates from ``emails`` and skip ones already in the table.

    NOTE: Emails already present in the results table are skipped rather
    than re-analysed so user edits are never overwritten. Use Clear to start
    again from scratch.

    Args:
        emails: Candidate emails in input order.
        existing: Keys (see :func:`email_key`) already in the table.

    Returns:
        ``(unique_emails, duplicates_in_input, already_in_table)``.
    """
    existing = existing or set()
    seen: Set[str] = set()
    unique: List[str] = []
    duplicates = already = 0
    for email in emails:
        key = email_key(email)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        if key in existing:
            already += 1
            continue
        unique.append(email)
    return unique, duplicates, already


def decode_bytes(data: bytes) -> str:
    """Decode file bytes, detecting UTF-16/UTF-8 BOMs and falling back sensibly.

    Args:
        data: Raw file content.

    Returns:
        Decoded text (never raises; worst case is Latin-1).
    """
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    # UTF-16 without BOM: lots of NUL bytes in the first chunk.
    head = data[:2000]
    if head and head.count(b"\x00") > len(head) // 4:
        enc = "utf-16-le" if head[1:2] == b"\x00" else "utf-16-be"
        return data.decode(enc, errors="replace")
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _xlsx_text(path: Path) -> str:
    """Return all cell text from an .xlsx workbook (every sheet)."""
    chunks: List[str] = []
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            if name == "xl/sharedStrings.xml" or (
                    name.startswith("xl/worksheets/") and name.endswith(".xml")):
                xml = zf.read(name).decode("utf-8", errors="replace")
                # Each cell/string becomes its own line.
                xml = re.sub(r"</(t|v|si|c)>", "\n", xml)
                chunks.append(html.unescape(re.sub(r"<[^>]+>", "", xml)))
    return "\n".join(chunks)


def read_file_text(path: str) -> str:
    """Read any supported list file as plain text.

    Args:
        path: A ``.txt``, ``.csv``, ``.tsv`` or ``.xlsx`` file. Other
            extensions are read as text.

    Returns:
        The file's text.

    Raises:
        ValueError: For legacy ``.xls`` workbooks (save as .xlsx or .csv).
    """
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".xls":
        raise ValueError("Old .xls files are not supported. In Excel use "
                         "File > Save As > .xlsx or .csv, then load that file.")
    if suffix in (".xlsx", ".xlsm"):
        return _xlsx_text(p)
    return decode_bytes(p.read_bytes())


def read_emails_from_file(path: str, existing: Optional[Iterable[str]] = None) -> ExtractResult:
    """Extract unique emails from a file, whatever its layout.

    Args:
        path: File chosen by the user.
        existing: Emails already in the input box (counted as duplicates).

    Returns:
        An :class:`ExtractResult`.
    """
    return extract_emails(read_file_text(path), existing)
