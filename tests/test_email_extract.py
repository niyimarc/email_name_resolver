"""Tests for email extraction, cleaning and deduplication of messy input."""

import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.email_extract import (  # noqa: E402
    dedupe_emails,
    email_key,
    extract_emails,
    normalize_email,
    parse_email_input,
    read_emails_from_file,
)

MESSY_CSV = '''Name,Email,Phone,,extra
"Doe, John",JOHN.DOE@ACME.COM,555-1234
Jane Smith <jane.smith@Example.org>; bob@x.com, Bob@X.com
"mailto:Mary.Van.Der.Berg@berg.nl?subject=hi"
  'o'neil@irish.ie'   ,  sarahjones@acme.com.
contact: tom [at] hanks [dot] com | twitter @tomhanks | broken john@ | user@localhost
https://site.com/?ref=1&email=kev.lee@corp.co.uk ;;;; JOHN.DOE@acme.com
'''

EXPECTED = [
    "JOHN.DOE@acme.com", "jane.smith@example.org", "bob@x.com",
    "Mary.Van.Der.Berg@berg.nl", "o'neil@irish.ie", "sarahjones@acme.com",
    "tom@hanks.com", "kev.lee@corp.co.uk",
]


def test_extracts_from_messy_text():
    r = extract_emails(MESSY_CSV)
    assert r.emails == EXPECTED
    assert r.duplicates == 2                       # Bob@X.com, JOHN.DOE@acme.com
    assert r.skipped == ["john@", "user@localhost"]  # @tomhanks is not reported
    assert "8 new email(s) added, 2 duplicate(s) removed" in r.summary()


def test_one_line_any_separator():
    r = extract_emails("a@x.com b@x.com,c@x.com;d@x.com|e@x.com\te@x.com")
    assert r.emails == ["a@x.com", "b@x.com", "c@x.com", "d@x.com", "e@x.com"]
    assert r.duplicates == 1


def test_existing_emails_count_as_duplicates():
    r = extract_emails("john@x.com, new@x.com", existing=["JOHN@X.COM"])
    assert r.emails == ["new@x.com"] and r.duplicates == 1


def test_normalize_and_key():
    assert normalize_email(" <mailto:John.Doe@ACME.Com.> ") == "John.Doe@acme.com"
    assert email_key("John.Doe@acme.com") == email_key("john.doe@ACME.com".lower())


def test_parse_box_keeps_invalid_typed_entries():
    assert parse_email_input("john@x.com\n<jane@y.com>; bad\nfoo@") == [
        "john@x.com", "jane@y.com", "bad", "foo@"]


def test_dedupe_against_table():
    unique, dups, already = dedupe_emails(
        ["a@x.com", "A@x.com", "b@x.com"], existing={"b@x.com"})
    assert unique == ["a@x.com"] and dups == 1 and already == 1


def test_read_utf16_csv(tmp_path):
    path = tmp_path / "excel_unicode.txt"
    path.write_bytes("Email\tName\njosé@café.com\tx\nmaria@acme.com\tMaría\n".encode("utf-16"))
    r = read_emails_from_file(str(path))
    assert r.emails == ["maria@acme.com"]   # non-ASCII domain is skipped
    assert r.skipped == ["josé@café.com"]


def test_read_xlsx_any_column(tmp_path):
    path = tmp_path / "list.xlsx"
    shared = ('<?xml version="1.0"?><sst><si><t>Contact</t></si>'
              '<si><t>John &lt;john@acme.com&gt;</t></si><si><t>notes</t></si></sst>')
    sheet = ('<?xml version="1.0"?><worksheet><sheetData>'
             '<row><c t="s"><v>0</v></c><c t="inlineStr"><is><t>jane@x.org</t></is></c></row>'
             '<row><c t="s"><v>1</v></c><c t="inlineStr"><is><t>JANE@X.org</t></is></c></row>'
             '</sheetData></worksheet>')
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("xl/sharedStrings.xml", shared)
        zf.writestr("xl/worksheets/sheet1.xml", sheet)
    r = read_emails_from_file(str(path))
    assert sorted(r.emails) == ["jane@x.org", "john@acme.com"]
    assert r.duplicates == 1


# ---------------------------------------------------------------- more cases

import pytest  # noqa: E402

from pipeline.email_extract import decode_bytes, read_file_text  # noqa: E402


@pytest.mark.parametrize("text, expected", [
    ("", []),
    ("no emails here at all", []),
    ("UPPER@EXAMPLE.COM", ["UPPER@example.com"]),
    ("first.last+tag@sub.domain.co.uk", ["first.last+tag@sub.domain.co.uk"]),
    ("a@b.co, a@b.co, A@B.CO", ["a@b.co"]),
    ("x@y.com.", ["x@y.com"]),
    ("(x@y.com)", ["x@y.com"]),
    ("mailto:Me@Site.ORG", ["Me@site.org"]),
    ("john [at] acme (dot) com", ["john@acme.com"]),
])
def test_extract_cases(text, expected):
    assert extract_emails(text).emails == expected


def test_summary_lists_at_most_five_skipped_examples():
    text = " ".join(f"bad{i}@" for i in range(8))
    summary = extract_emails(text).summary("file.csv")
    assert summary.startswith("file.csv: 0 new email(s) added")
    assert "8 invalid entr(ies) skipped" in summary and summary.endswith("...)")


@pytest.mark.parametrize("raw, encoding", [
    ("José,jose@acme.com\n", "utf-8"),
    ("José,jose@acme.com\n", "utf-8-sig"),
    ("José,jose@acme.com\n", "cp1252"),
    ("José,jose@acme.com\n", "utf-16"),
    ("José,jose@acme.com\n", "utf-16-le"),
])
def test_decode_bytes_handles_common_encodings(raw, encoding):
    assert decode_bytes(raw.encode(encoding)) == raw


def test_legacy_xls_gives_helpful_error(tmp_path):
    path = tmp_path / "old.xls"
    path.write_bytes(b"\xd0\xcf\x11\xe0")
    with pytest.raises(ValueError, match="xlsx"):
        read_file_text(str(path))


def test_large_list_is_fast_and_deduplicated():
    import time
    text = "\n".join(f"user{i % 5000}@corp{i % 7}.com" for i in range(30000))
    started = time.perf_counter()
    result = extract_emails(text)
    assert time.perf_counter() - started < 3
    assert len(result.emails) == len({(i % 5000, i % 7) for i in range(30000)})
