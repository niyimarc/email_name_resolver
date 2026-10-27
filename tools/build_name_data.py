"""Rebuild the bundled US name lists in ``data/`` from public sources.

Run from the project root (needs internet):  python tools/build_name_data.py

Sources (US government data, public domain):
  * First names: Social Security Administration baby names, the top 1,000
    names per sex per year 1880-2008, via
    https://github.com/hadley/data-baby-names
  * First names: US Census 1990 first-name lists (dist.male.first /
    dist.female.first), via the ``names`` package on PyPI.
  * Surnames: US Census 2000 surname list (151,671 surnames with counts),
    via https://github.com/fivethirtyeight/data (most-common-name)

Output (UTF-8, one entry per line, ``name<TAB>score``):
  * data/first_names.tsv  score = peak frequency in parts per million
  * data/surnames.tsv     score = number of people with that surname
"""

from __future__ import annotations

import csv
import io
import tarfile
import urllib.request
from pathlib import Path
from typing import Dict

SSA_URL = "https://raw.githubusercontent.com/hadley/data-baby-names/master/baby-names.csv"
SURNAMES_URL = ("https://raw.githubusercontent.com/fivethirtyeight/data/master/"
                "most-common-name/surnames.csv")
CENSUS_1990_URL = ("https://files.pythonhosted.org/packages/source/n/names/"
                   "names-0.3.0.tar.gz")

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def _download(url: str) -> bytes:
    """Fetch ``url`` and return the response body."""
    with urllib.request.urlopen(url, timeout=120) as resp:  # noqa: S310
        return resp.read()


def _clean(name: str) -> str:
    """Lowercase a name and keep it only if it is purely alphabetic (len > 1)."""
    name = name.strip().lower()
    return name if len(name) > 1 and name.isalpha() else ""


def build_first_names() -> Dict[str, int]:
    """Merge SSA and Census 1990 first names into ``{name: peak ppm}``."""
    scores: Dict[str, int] = {}
    text = _download(SSA_URL).decode("utf-8")
    for row in csv.DictReader(io.StringIO(text)):
        name = _clean(row["name"])
        if name:
            ppm = round(float(row["percent"]) * 1_000_000)
            scores[name] = max(scores.get(name, 0), ppm)

    archive = tarfile.open(fileobj=io.BytesIO(_download(CENSUS_1990_URL)))
    for member in archive.getmembers():
        if member.name.endswith((".male.first", ".female.first")):
            for line in archive.extractfile(member).read().decode("ascii").splitlines():
                parts = line.split()
                if len(parts) >= 2 and _clean(parts[0]):
                    ppm = round(float(parts[1]) * 10_000)  # percent -> ppm
                    name = _clean(parts[0])
                    scores[name] = max(scores.get(name, 0), max(ppm, 1))
    return scores


def build_surnames() -> Dict[str, int]:
    """Load Census 2000 surnames into ``{name: count}``."""
    counts: Dict[str, int] = {}
    text = _download(SURNAMES_URL).decode("utf-8")
    for row in csv.DictReader(io.StringIO(text)):
        name = _clean(row["name"])
        if name and row["count"].isdigit():
            counts[name] = int(row["count"])
    return counts


def write_tsv(path: Path, scores: Dict[str, int], header: str) -> None:
    """Write ``scores`` sorted by score (desc) then name."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(header + "\n")
        for name, score in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0])):
            fh.write(f"{name}\t{score}\n")


def main() -> None:
    """Download sources and regenerate the data files."""
    first = build_first_names()
    surnames = build_surnames()
    write_tsv(DATA_DIR / "first_names.tsv", first,
              "# US first names: SSA baby names (top 1000/year 1880-2008) + "
              "Census 1990. name<TAB>peak parts-per-million")
    write_tsv(DATA_DIR / "surnames.tsv", surnames,
              "# US surnames: Census 2000. name<TAB>number of people")
    print(f"first names: {len(first):,}  surnames: {len(surnames):,}")


if __name__ == "__main__":
    main()
