"""Dictionary stage: resolve names from US first-name / surname lists.

Runs between the splitter (stage 1) and the AI judge (stage 2):

* A confident dictionary match (e.g. ``john.doe`` = known first name + known
  surname) produces a final verdict directly, and the AI is skipped.
* Generic/role mailboxes (``info``, ``sales.team``, ``noreply``) are marked
  "not a person" directly, and the AI is skipped.
* Anything else goes to the AI with *evidence*: which tokens are known first
  names / surnames, plus a suggested split for glued tokens (``johndoe``).

Data:
* Bundled lists in ``data/`` (US SSA + Census, see tools/build_name_data.py).
* User lists in ``%APPDATA%/EmailNameResolver/dictionaries/`` (plain text,
  one name per line). User entries always count as confident matches.
"""

from __future__ import annotations

import math
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from .ai_judge import NameVerdict
from .splitter import HINT_INITIAL_SURNAME, HINT_PARTICLE, PARTICLES

KIND_FIRST = "first"
KIND_SURNAME = "surname"
KIND_ROLE = "role"

USER_FILES: Dict[str, str] = {
    KIND_FIRST: "first_names.txt",
    KIND_SURNAME: "surnames.txt",
    KIND_ROLE: "role_words.txt",
}
_USER_FILE_HEADERS: Dict[str, str] = {
    KIND_FIRST: "# Your own first names, one per line. Always trusted.\n",
    KIND_SURNAME: "# Your own surnames, one per line. Always trusted.\n",
    KIND_ROLE: ("# Words that are never a person's name (e.g. info, sales, "
                "coolguy), one per line.\n"),
}

# Score given to user-supplied names: high enough to pass every threshold.
USER_SCORE = 10 ** 9

_HEADER_WORDS = {"name", "names", "first", "firstname", "first_name", "surname",
                 "surnames", "last", "lastname", "last_name", "word", "words"}

SOURCE_DICTIONARY = "dictionary"


@dataclass(frozen=True)
class Thresholds:
    """Minimum frequencies for a match to count as *confident*.

    NOTE: Very rare entries are excluded from confident matches because the
    Census list contains real but rare surnames that are also common words
    (e.g. "Team", "Office"). Rare names are still passed to the AI as hints.
    """

    first_min_ppm: int = 20            # separated tokens, e.g. john.doe
    surname_min_count: int = 500
    glued_first_min_ppm: int = 150     # glued tokens, e.g. johndoe
    glued_surname_min_count: int = 2000
    glued_min_surname_len: int = 3
    glued_ambiguity_margin: float = 2.0  # natural-log score gap to runner-up


def bundled_data_dir() -> Path:
    """Return the folder holding the bundled ``data/`` files.

    Works both from source and inside a PyInstaller build (``sys._MEIPASS``).
    """
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "data"
    return Path(__file__).resolve().parents[1] / "data"


def clean_name(raw: str) -> str:
    """Normalise a dictionary entry: lowercase, letters only, length > 1.

    Args:
        raw: A name as written in a file.

    Returns:
        The cleaned name, or ``""`` if it is not a usable single word.
    """
    name = raw.strip().strip("\"'").lower()
    return name if len(name) > 1 and name.isalpha() else ""


def parse_word_lines(lines: Iterable[str]) -> Dict[str, int]:
    """Parse ``name[<TAB or comma>score]`` lines into ``{name: score}``.

    ``#`` comments, blank lines, header words and non-alphabetic entries are
    skipped. Entries without a score get :data:`USER_SCORE`.

    Args:
        lines: Text lines (TSV, CSV first column, or plain list).

    Returns:
        Mapping of cleaned names to scores (highest wins on duplicates).
    """
    out: Dict[str, int] = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        sep = "\t" if "\t" in line else ("," if "," in line else (";" if ";" in line else None))
        cells = line.split(sep) if sep else [line]
        name = clean_name(cells[0])
        if not name or name in _HEADER_WORDS:
            continue
        score = USER_SCORE
        if len(cells) > 1:
            try:
                score = int(float(cells[1].strip()))
            except ValueError:
                score = USER_SCORE
        out[name] = max(out.get(name, 0), score)
    return out


def _read_word_file(path: Path) -> Dict[str, int]:
    """Read a word file, returning ``{}`` if it is missing or unreadable."""
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace") as fh:
            return parse_word_lines(fh)
    except OSError:
        return {}


def ensure_user_files(user_dir: Path) -> Path:
    """Create the user dictionary folder and empty template files.

    Args:
        user_dir: Folder for the user's own lists.

    Returns:
        ``user_dir`` (it exists after this call).
    """
    user_dir.mkdir(parents=True, exist_ok=True)
    for kind, filename in USER_FILES.items():
        path = user_dir / filename
        if not path.exists():
            path.write_text(_USER_FILE_HEADERS[kind], encoding="utf-8")
    return user_dir


def add_user_words(user_dir: Path, kind: str, words: Iterable[str]) -> int:
    """Append new words to one of the user's lists (duplicates skipped).

    Args:
        user_dir: Folder for the user's own lists.
        kind: ``"first"``, ``"surname"`` or ``"role"``.
        words: Words to add (cleaned with :func:`clean_name`).

    Returns:
        The number of words actually added.
    """
    if kind not in USER_FILES:
        raise ValueError(f"unknown dictionary kind: {kind}")
    ensure_user_files(user_dir)
    path = user_dir / USER_FILES[kind]
    existing = set(_read_word_file(path))
    new: List[str] = []
    for word in words:
        name = clean_name(word)
        if name and name not in existing:
            existing.add(name)
            new.append(name)
    if new:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        if text and not text.endswith("\n"):
            text += "\n"
        path.write_text(text + "\n".join(new) + "\n", encoding="utf-8")
    return len(new)


def format_name_part(token: str) -> str:
    """Capitalise one name part (``"mcdonald"`` -> ``"McDonald"``)."""
    if token in PARTICLES:
        return token
    if token.startswith("mc") and len(token) > 3:
        return "Mc" + token[2:].capitalize()
    return token.capitalize()


class NameDictionary:
    """In-memory first-name, surname and role-word lists with resolution rules."""

    def __init__(self, first: Dict[str, int], surnames: Dict[str, int],
                 roles: Set[str], thresholds: Thresholds = Thresholds()) -> None:
        """Store the lists.

        Args:
            first: ``{first_name: peak ppm}``.
            surnames: ``{surname: count}``.
            roles: Role/generic mailbox words.
            thresholds: Confidence thresholds.
        """
        self.first = first
        self.surnames = surnames
        self.roles = roles
        self.t = thresholds

    # -- loading ---------------------------------------------------------------

    @classmethod
    def load(cls, bundled_dir: Optional[Path] = None,
             user_dir: Optional[Path] = None) -> "NameDictionary":
        """Load bundled lists and merge the user's own lists on top.

        Args:
            bundled_dir: Folder with ``first_names.tsv``, ``surnames.tsv`` and
                ``role_words.txt`` (defaults to the app's ``data/``).
            user_dir: Folder with the user's lists, or None.

        Returns:
            A ready :class:`NameDictionary`.
        """
        bundled_dir = bundled_dir or bundled_data_dir()
        first = _read_word_file(bundled_dir / "first_names.tsv")
        surnames = _read_word_file(bundled_dir / "surnames.tsv")
        roles = set(_read_word_file(bundled_dir / "role_words.txt"))
        if user_dir is not None:
            for name in _read_word_file(user_dir / USER_FILES[KIND_FIRST]):
                first[name] = USER_SCORE
            for name in _read_word_file(user_dir / USER_FILES[KIND_SURNAME]):
                surnames[name] = USER_SCORE
            user_roles = set(_read_word_file(user_dir / USER_FILES[KIND_ROLE]))
            roles |= user_roles
            # A word the user marked "not a name" must never resolve as one.
            for name in user_roles:
                first.pop(name, None)
                surnames.pop(name, None)
        return cls(first, surnames, roles)

    # -- lookups -----------------------------------------------------------------

    def is_role(self, token: str) -> bool:
        """True if ``token`` is a role word or two glued role words (``salesteam``)."""
        if token in self.roles:
            return True
        return any(token[:i] in self.roles and token[i:] in self.roles
                   for i in range(2, len(token) - 1))

    def is_first(self, token: str, glued: bool = False) -> bool:
        """True if ``token`` is a first name common enough to trust."""
        need = self.t.glued_first_min_ppm if glued else self.t.first_min_ppm
        return self.first.get(token, 0) >= need

    def is_surname(self, token: str, glued: bool = False) -> bool:
        """True if ``token`` is a surname common enough to trust."""
        need = self.t.glued_surname_min_count if glued else self.t.surname_min_count
        return self.surnames.get(token, 0) >= need

    def tag_tokens(self, tokens: List[str]) -> Dict[str, List[str]]:
        """Label each token as ``first_name`` / ``surname`` / ``role_word``.

        Any presence in the lists counts here (no thresholds), because tags
        are evidence for the AI, not decisions.

        Args:
            tokens: Splitter tokens.

        Returns:
            ``{token: [tags...]}`` (empty list = not in any list).
        """
        tags: Dict[str, List[str]] = {}
        for tok in tokens:
            found = []
            if tok in self.first:
                found.append("first_name")
            if tok in self.surnames:
                found.append("surname")
            if self.is_role(tok):
                found.append("role_word")
            tags[tok] = found
        return tags

    def split_glued(self, token: str) -> Optional[Tuple[str, str, bool]]:
        """Find the best ``first + surname`` split of a glued token.

        Args:
            token: A single lowercase token such as ``"johndoe"``.

        Returns:
            ``(first, surname, confident)`` or None if no split uses a known
            first name followed by a known surname.
        """
        candidates: List[Tuple[float, str, str]] = []
        for i in range(2, len(token) - 1):
            first, last = token[:i], token[i:]
            f_score = self.first.get(first, 0)
            s_score = self.surnames.get(last, 0)
            if f_score > 0 and s_score > 0:
                candidates.append((math.log(f_score) + math.log(s_score), first, last))
        if not candidates:
            return None
        candidates.sort(reverse=True)
        score, first, last = candidates[0]
        unambiguous = (len(candidates) == 1
                       or score - candidates[1][0] >= self.t.glued_ambiguity_margin)
        confident = (unambiguous
                     and len(last) >= self.t.glued_min_surname_len
                     and self.is_first(first, glued=True)
                     and self.is_surname(last, glued=True))
        return first, last, confident

    # -- AI evidence ----------------------------------------------------------------

    def evidence(self, info: Dict) -> Dict:
        """Build the dictionary evidence passed to the AI judge.

        Args:
            info: Splitter output.

        Returns:
            ``{"tags": {...}}`` plus ``"suggested_split"`` for a glued token.
        """
        tokens = list(info.get("tokens") or [])
        out: Dict = {"tags": self.tag_tokens(tokens)}
        # NOTE: No split is suggested for a token that is itself a known name
        # ("johnson" is not "john" + "son").
        if (len(tokens) == 1 and info.get("hint") is None
                and tokens[0] not in self.first and tokens[0] not in self.surnames):
            split = self.split_glued(tokens[0])
            if split:
                out["suggested_split"] = [split[0], split[1]]
        return out

    # -- resolution -------------------------------------------------------------------

    @staticmethod
    def _verdict(parts: List[str], confidence: int, reason: str) -> NameVerdict:
        """Build a positive dictionary verdict."""
        return NameVerdict(is_real_name=True, confidence=confidence,
                           reason=f"{SOURCE_DICTIONARY}: {reason}",
                           final_name=" ".join(parts), name_parts=parts)

    def resolve(self, info: Dict) -> Optional[NameVerdict]:
        """Resolve an email from the dictionary alone, if confidently possible.

        Args:
            info: Splitter output (see ``split_email_local``).

        Returns:
            A final :class:`NameVerdict` (AI can be skipped), or None when the
            AI should decide.
        """
        if not info.get("valid"):
            return None
        tokens: List[str] = list(info.get("tokens") or [])
        hint = info.get("hint")

        # 1. Generic/role mailbox: every token is a role word.
        if tokens and all(self.is_role(t) for t in tokens):
            return NameVerdict(
                is_real_name=False, confidence=2,
                reason=f"{SOURCE_DICTIONARY}: generic mailbox ({' '.join(tokens)})",
                final_name=None, name_parts=[])
        # NOTE: Mixed role + name tokens (e.g. john.sales) are left to the AI.
        if any(self.is_role(t) for t in tokens):
            return None

        # 2. Initial(s) + known surname: jdoe, j.doe, j.r.smith.
        if hint == HINT_INITIAL_SURNAME:
            last = info.get("candidate_last") or ""
            if self.is_surname(last):
                initials = [info.get("candidate_first") or ""] + list(info.get("candidate_middle") or [])
                parts = [i.upper() for i in initials if i] + [format_name_part(last)]
                return self._verdict(parts, 75, f"initial + known surname '{last}'")
            return None

        # 3. Name with particles: mary.van.der.berg.
        if hint == HINT_PARTICLE:
            first = info.get("candidate_first")
            middle = list(info.get("candidate_middle") or [])
            if (first and self.is_first(first) and self.is_surname(tokens[-1])
                    and all(self.is_first(m) or self.is_surname(m) for m in middle)):
                parts = [tokens[0].capitalize()] + [format_name_part(t) for t in tokens[1:]]
                return self._verdict(parts, 90, "known first name + surname with particles")
            return None

        # 4. Separated tokens.
        if len(tokens) == 2:
            a, b = tokens
            if self.is_first(a) and self.is_surname(b):
                return self._verdict([format_name_part(a), format_name_part(b)], 95,
                                     f"known first name '{a}' + known surname '{b}'")
            if self.is_surname(a) and self.is_first(b):
                return self._verdict([format_name_part(b), format_name_part(a)], 85,
                                     f"surname.firstname order ('{a}', '{b}')")
            return None
        if len(tokens) == 3:
            a, b, c = tokens
            if (self.is_first(a) and self.is_surname(c)
                    and (self.is_first(b) or self.is_surname(b))):
                return self._verdict([format_name_part(t) for t in tokens], 90,
                                     "known first name + middle name + surname")
            return None

        # 5. One glued token: johndoe -> John Doe.
        if len(tokens) == 1 and hint is None:
            token = tokens[0]
            if token in self.first or token in self.surnames:
                return None  # a lone name; the AI decides whether it's enough
            split = self.split_glued(token)
            if split and split[2]:
                first, last, _ = split
                return self._verdict([format_name_part(first), format_name_part(last)], 85,
                                     f"glued name split into '{first}' + '{last}'")
        return None


class DictionaryStore:
    """Thread-safe, lazily loaded holder for the app's :class:`NameDictionary`.

    Loading parses ~160k lines, so it happens in a background thread on
    first use; :meth:`invalidate` forces a reload after the user edits lists.
    """

    def __init__(self, user_dir_factory: Callable[[], Path]) -> None:
        """Remember how to find the user's dictionary folder.

        Args:
            user_dir_factory: Returns the user dictionary folder.
        """
        self._user_dir_factory = user_dir_factory
        self._lock = threading.Lock()
        self._dictionary: Optional[NameDictionary] = None

    @property
    def user_dir(self) -> Path:
        """The user's dictionary folder (created with templates if missing)."""
        return ensure_user_files(self._user_dir_factory())

    def get(self) -> NameDictionary:
        """Return the loaded dictionary, loading it if needed (blocking)."""
        with self._lock:
            if self._dictionary is None:
                self._dictionary = NameDictionary.load(user_dir=self.user_dir)
            return self._dictionary

    def invalidate(self) -> None:
        """Drop the cached dictionary so the next :meth:`get` reloads it."""
        with self._lock:
            self._dictionary = None
