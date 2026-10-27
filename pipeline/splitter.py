"""Stage 1: rule-based splitting of an email local part into name tokens.

Pure Python, no third-party dependencies. The output of
:func:`split_email_local` is the only thing the AI judge (stage 2) is
allowed to work with, so the splitter never guesses names: it only
extracts and labels tokens that are literally present in the address.
"""

from __future__ import annotations

from typing import Dict, List, Optional

# Lowercase name particles that usually belong to the surname
# (e.g. "Mary van der Berg", "Omar al Rashid").
PARTICLES = frozenset({"van", "der", "de", "da", "bin", "al", "von", "la", "le"})

# Characters the spec says to split on. Any other non-alphanumeric
# character is also treated as a separator.
# NOTE: The spec lists only ".", "_" and "-". Characters such as "'" or "~"
# would otherwise glue tokens together (o'neil -> "oneil" is fine, but
# "john~doe" -> "johndoe" is not), so every non-alphanumeric character is
# treated as a separator. This is the safest default for tokenisation.
SEPARATORS = frozenset({".", "_", "-"})

# Two-consonant onsets that commonly start an English given name or word.
# Used to tell "jdoe" (initial + surname) apart from "smith" or "chris".
_COMMON_ONSETS = frozenset({
    "bl", "br", "ch", "cl", "cr", "dr", "dw", "fl", "fr", "gh", "gl", "gn",
    "gr", "kh", "kl", "kn", "kr", "ph", "pl", "pr", "ps", "qu", "rh", "sc",
    "sh", "sk", "sl", "sm", "sn", "sp", "st", "sv", "sw", "th", "tr", "ts",
    "tw", "vl", "wh", "wr", "zh",
})
_VOWELS = frozenset("aeiouy")

HINT_INITIAL_SURNAME = "initial+surname"
HINT_PARTICLE = "particle"


def _split_case_and_digits(segment: str) -> List[str]:
    """Split a separator-free segment on letter/digit and camelCase boundaries.

    Examples:
        "john123"  -> ["john", "123"]
        "JohnDoe"  -> ["John", "Doe"]
        "JSmith"   -> ["J", "Smith"]
        "JOHNDoe"  -> ["JOHN", "Doe"]

    Args:
        segment: A string that contains no separator characters.

    Returns:
        The pieces in their original order and original case.
    """
    pieces: List[str] = []
    current = ""
    for i, ch in enumerate(segment):
        if not ch.isalnum():
            if current:
                pieces.append(current)
                current = ""
            continue
        if current:
            prev = current[-1]
            nxt = segment[i + 1] if i + 1 < len(segment) else ""
            boundary = (
                prev.isdigit() != ch.isdigit()                       # letter <-> digit
                or (prev.islower() and ch.isupper())                 # camelCase
                or (prev.isupper() and ch.isupper() and nxt.islower())  # "JSmith"
            )
            if boundary:
                pieces.append(current)
                current = ""
        current += ch
    if current:
        pieces.append(current)
    return pieces


def _tokenize(local: str) -> List[str]:
    """Return every alphabetic piece of ``local`` (any length), lowercased.

    Args:
        local: The local part with any ``+tag`` already removed.

    Returns:
        Alphabetic pieces in original order. Digit pieces are dropped.
    """
    segments: List[str] = []
    current = ""
    for ch in local:
        if ch in SEPARATORS or not ch.isalnum():
            if current:
                segments.append(current)
            current = ""
        else:
            current += ch
    if current:
        segments.append(current)

    pieces: List[str] = []
    for segment in segments:
        for piece in _split_case_and_digits(segment):
            if piece.isalpha():
                pieces.append(piece.lower())
    return pieces


def _looks_like_glued_initial(token: str) -> bool:
    """Heuristic for a single glued token such as ``jdoe`` or ``jsmith``.

    The token starts with two consonants that are not a common onset
    ("jd", "js", "tb"), and the remainder contains a vowel, so it can be
    read as a one-letter initial followed by a surname.

    Args:
        token: A lowercase alphabetic token.

    Returns:
        True when the token plausibly is ``<initial><surname>``.
    """
    if not 4 <= len(token) <= 14 or not token.isascii():
        return False
    first, second = token[0], token[1]
    if first in _VOWELS or second in _VOWELS:
        return False
    if token[:2] in _COMMON_ONSETS:
        return False
    return any(ch in _VOWELS for ch in token[1:])


def _empty_result(raw_local: str, domain: str, valid: bool,
                  error: Optional[str]) -> Dict:
    """Build a result dict with no tokens.

    Args:
        raw_local: The local part as given.
        domain: The domain as given (lowercased).
        valid: Whether the address is structurally valid.
        error: Error label for invalid addresses, else None.

    Returns:
        A dict with every key :func:`split_email_local` promises.
    """
    return {
        "raw_local": raw_local,
        "domain": domain,
        "tokens": [],
        "candidate_first": None,
        "candidate_middle": [],
        "candidate_last": None,
        "token_count": 0,
        "hint": None,
        "valid": valid,
        "error": error,
    }


def split_email_local(email: str) -> Dict:
    """Split an email address into candidate name tokens.

    Steps: split on the last ``@``; strip ``+tag``; split on ``.``, ``_``
    and ``-``; split letter/digit and camelCase boundaries; keep alphabetic
    tokens longer than one character, lowercased, in order.

    Args:
        email: The email address to analyse.

    Returns:
        A dict with keys ``raw_local``, ``domain``, ``tokens``,
        ``candidate_first``, ``candidate_middle``, ``candidate_last``,
        ``token_count`` and ``hint`` (``"initial+surname"``, ``"particle"``
        or None), plus ``valid`` (bool) and ``error`` (``"invalid email"``
        or None).
    """
    text = (email or "").strip()
    if "@" not in text:
        return _empty_result(text, "", False, "invalid email")

    # NOTE: rsplit on the last "@" so a quoted local part containing "@"
    # still yields the real domain.
    raw_local, domain = text.rsplit("@", 1)
    domain = domain.strip().lower().rstrip(".")
    if not raw_local or not domain or any(c.isspace() for c in text):
        return _empty_result(raw_local, domain, False, "invalid email")

    local_no_tag = raw_local.split("+", 1)[0]
    pieces = _tokenize(local_no_tag)
    tokens = [p for p in pieces if len(p) > 1]

    result = _empty_result(raw_local, domain, True, None)
    result["tokens"] = tokens
    result["token_count"] = len(tokens)

    # --- initial + surname -------------------------------------------------
    # Case A: separate initials, e.g. "j.doe", "JDoe", "j.r.smith".
    if (len(pieces) >= 2 and all(len(p) == 1 for p in pieces[:-1])
            and len(pieces[-1]) > 1):
        result["hint"] = HINT_INITIAL_SURNAME
        result["candidate_first"] = pieces[0]
        result["candidate_middle"] = pieces[1:-1]
        result["candidate_last"] = pieces[-1]
        return result

    # Case B: glued, e.g. "jdoe", "jsmith".
    if len(pieces) == 1 and len(tokens) == 1 and _looks_like_glued_initial(tokens[0]):
        result["hint"] = HINT_INITIAL_SURNAME
        result["candidate_first"] = tokens[0][0]
        result["candidate_last"] = tokens[0][1:]
        return result

    if not tokens:
        return result

    # --- particles -----------------------------------------------------------
    # NOTE: A particle only counts when it is not the final token, and not
    # the first token unless another particle follows it. This keeps given
    # names such as "Al" (al.smith) or "Van" (van.morrison) from being
    # flagged, which is the safer reading.
    has_particle = False
    for i, tok in enumerate(tokens[:-1]):
        if tok not in PARTICLES:
            continue
        if i > 0 or tokens[1] in PARTICLES:
            has_particle = True
            break

    result["candidate_first"] = tokens[0]
    if len(tokens) == 1:
        return result

    if has_particle:
        result["hint"] = HINT_PARTICLE
        # Particles directly before the last token belong to the surname:
        # mary.van.der.berg -> first "mary", last "van der berg".
        start = len(tokens) - 1
        while start - 1 >= 1 and tokens[start - 1] in PARTICLES:
            start -= 1
        if start == 1 and tokens[0] in PARTICLES:
            # Whole address is a surname such as "van.der.berg".
            result["candidate_first"] = None
            start = 0
        result["candidate_last"] = " ".join(tokens[start:])
        result["candidate_middle"] = tokens[1:start] if start > 1 else []
        return result

    result["candidate_middle"] = tokens[1:-1]
    result["candidate_last"] = tokens[-1]
    return result


def format_tokens_rule_based(info: Dict) -> List[str]:
    """Format splitter tokens as display name parts without any AI.

    Used when Ollama is unavailable. Tokens are title-cased; particles stay
    lowercase unless they come first. Initial+surname hints become
    ``["J", "Doe"]``.

    Args:
        info: A dict returned by :func:`split_email_local`.

    Returns:
        Ordered display name parts (may be empty).
    """
    if not info.get("valid"):
        return []
    if info.get("hint") == HINT_INITIAL_SURNAME:
        parts = [info["candidate_first"], *info.get("candidate_middle", [])]
        parts = [p.upper() for p in parts if p]
        last = info.get("candidate_last")
        if last:
            parts.append(last.capitalize())
        return parts
    parts: List[str] = []
    for i, tok in enumerate(info.get("tokens", [])):
        parts.append(tok if (i > 0 and tok in PARTICLES) else tok.capitalize())
    return parts
