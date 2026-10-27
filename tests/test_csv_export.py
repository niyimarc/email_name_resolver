"""Tests for the CSV exporters (full analysis and email-sender list)."""

import csv
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from exporters.csv_export import (  # noqa: E402
    CSV_COLUMNS,
    MAILING_COLUMNS,
    count_resolved,
    export_mailing_csv,
    export_rows_to_csv,
    filter_rows,
    split_full_name,
)

ROWS = [
    {"email": "john.doe@acme.com", "is_real_name": True, "confidence": 95,
     "final_name": "John Doe", "name_parts": ["John", "Doe"], "reason": "dictionary",
     "company_website": "https://www.acme.com"},
    {"email": "mary.van.der.berg@berg.nl", "is_real_name": True, "confidence": 90,
     "final_name": "Mary van der Berg", "name_parts": ["Mary", "van", "der", "Berg"],
     "reason": "dictionary", "company_website": ""},
    {"email": "jdoe@acme.com", "is_real_name": True, "confidence": 75,
     "final_name": "J Doe", "name_parts": ["J", "Doe"], "reason": "dictionary",
     "company_website": "https://www.acme.com"},
    {"email": "info@acme.com", "is_real_name": False, "confidence": 2,
     "final_name": "", "name_parts": [], "reason": "generic mailbox", "company_website": ""},
    {"email": "coolguy@gmail.com", "is_real_name": False, "confidence": 10,
     "final_name": "Cool Guy", "name_parts": [], "reason": "user typed a name",
     "company_website": ""},
    {"email": "bad", "is_real_name": False, "confidence": 0, "final_name": "",
     "name_parts": [], "reason": "invalid email", "company_website": ""},
]


def read_csv(path):
    raw = Path(path).read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM for Excel
    return list(csv.reader(raw.decode("utf-8-sig").splitlines()))


@pytest.mark.parametrize("full, expected", [
    ("John Doe", ("John", "Doe")),
    ("John Paul Smith", ("John", "Smith")),
    ("Mary van der Berg", ("Mary", "van der Berg")),
    ("J Doe", ("", "Doe")),          # initial only -> blank first name
    ("J. R. Smith", ("", "Smith")),
    ("Zorblax", ("Zorblax", "")),
    ("", ("", "")),
])
def test_split_full_name(full, expected):
    assert split_full_name(full) == expected


def test_counts_use_final_name_including_user_edits():
    assert count_resolved(ROWS) == (4, 2)


def test_filter_rows():
    kept, unresolved, invalid = filter_rows(ROWS, only_resolved=True, drop_invalid=True)
    assert [r["email"] for r in kept] == [
        "john.doe@acme.com", "mary.van.der.berg@berg.nl", "jdoe@acme.com", "coolguy@gmail.com"]
    assert (unresolved, invalid) == (1, 1)
    kept, unresolved, invalid = filter_rows(ROWS, only_resolved=False, drop_invalid=True)
    assert len(kept) == 5 and (unresolved, invalid) == (0, 1)


def test_mailing_export(tmp_path):
    kept, _, _ = filter_rows(ROWS, only_resolved=True, drop_invalid=True)
    path = tmp_path / "list.csv"
    assert export_mailing_csv(kept, path) == 4
    rows = read_csv(path)
    assert rows[0] == MAILING_COLUMNS
    assert rows[1] == ["john.doe@acme.com", "John", "Doe", "John Doe",
                       "https://www.acme.com", "", ""]
    assert rows[2] == ["mary.van.der.berg@berg.nl", "Mary", "van der Berg",
                       "Mary van der Berg", "", "", ""]
    assert rows[3] == ["jdoe@acme.com", "", "Doe", "J Doe", "https://www.acme.com", "", ""]


def test_full_export_unchanged(tmp_path):
    path = tmp_path / "full.csv"
    assert export_rows_to_csv(ROWS, path) == len(ROWS)
    rows = read_csv(path)
    assert rows[0] == CSV_COLUMNS
    assert rows[2][4] == "Mary | van | der | Berg"


def test_select_rows_by_provider_status_and_sort():
    from exporters.csv_export import ExportSelection, group_by_provider, select_rows
    rows = [
        {"email": "b@acme.com", "final_name": "B", "mail_provider": "Google Workspace",
         "email_status": "valid"},
        {"email": "z@gmail.com", "final_name": "Z", "mail_provider": "Gmail",
         "email_status": "valid"},
        {"email": "a@gmail.com", "final_name": "A", "mail_provider": "Gmail",
         "email_status": "valid"},
        {"email": "x@mailinator.com", "final_name": "X", "mail_provider": "Other free provider",
         "email_status": "disposable"},
        {"email": "q@gone.example", "final_name": "Q", "mail_provider": "No mail server",
         "email_status": "domain doesn't exist"},
        {"email": "n@acme.com", "final_name": "", "mail_provider": "Google Workspace",
         "email_status": "valid"},
    ]
    kept, skipped = select_rows(rows, ExportSelection())
    assert [r["email"] for r in kept] == ["a@gmail.com", "z@gmail.com", "b@acme.com"]
    assert skipped == {"provider": 0, "undeliverable": 2, "unresolved": 1}

    kept, skipped = select_rows(rows, ExportSelection(providers={"Gmail"}))
    assert [r["email"] for r in kept] == ["a@gmail.com", "z@gmail.com"]
    assert skipped["provider"] == 4

    kept, _ = select_rows(rows, ExportSelection(only_resolved=False,
                                                skip_undeliverable=False,
                                                sort_by_domain=False))
    assert len(kept) == 6
    groups = group_by_provider(kept)
    assert list(groups) == ["Google Workspace", "Gmail", "Other free provider",
                            "No mail server"]


def test_per_provider_path():
    from exporters.csv_export import per_provider_path
    assert per_provider_path("C:/x/email_list.csv", "Outlook / Hotmail").name == \
        "email_list_outlook_hotmail.csv"


def test_provider_zip(tmp_path):
    import zipfile
    from exporters.csv_export import export_provider_zip, group_by_provider
    rows = [
        {"email": "a@gmail.com", "final_name": "Ann Lee", "mail_provider": "Gmail"},
        {"email": "b@acme.com", "final_name": "Bob Ray", "mail_provider": "Google Workspace"},
        {"email": "c@gmail.com", "final_name": "Cy Day", "mail_provider": "Gmail"},
    ]
    zip_path = tmp_path / "email_list_1.zip"
    contents = export_provider_zip(group_by_provider(rows), zip_path, "mailing")
    assert contents == {"email_list_1_gmail.csv": 2, "email_list_1_google_workspace.csv": 1}
    with zipfile.ZipFile(zip_path) as zf:
        assert zf.namelist() == list(contents)
        data = zf.read("email_list_1_gmail.csv")
    assert data.startswith(b"\xef\xbb\xbf")          # BOM kept for Excel
    lines = data.decode("utf-8-sig").splitlines()
    assert lines[0].startswith("Email Address,First Name,Last Name")
    assert lines[1].startswith("a@gmail.com,Ann,Lee,Ann Lee")
    assert not list(tmp_path.glob("*.tmp"))


# ---------------------------------------------------------------- more cases

from datetime import datetime  # noqa: E402

from exporters.csv_export import (  # noqa: E402
    ExportSelection,
    default_export_filename,
    is_undeliverable,
    is_valid_email,
    provider_counts,
    row_to_csv_record,
    rows_to_csv_bytes,
    select_rows,
)


def test_default_filenames():
    now = datetime(2026, 10, 27, 9, 5, 3)
    assert default_export_filename(now) == "email_names_20261027_090503.csv"
    assert default_export_filename(now, "mailing") == "email_list_20261027_090503.csv"


@pytest.mark.parametrize("value", ["=HYPERLINK(\"x\")", "+1", "-2", "@SUM(A1)"])
def test_formula_injection_is_neutralised(value):
    record = row_to_csv_record({"email": "a@b.co", "reason": value})
    assert record[5] == "'" + value


@pytest.mark.parametrize("email, ok", [("a@b.co", True), ("bad", False), ("@b.co", False),
                                       ("a@", False), ("a b@c.co", False)])
def test_is_valid_email(email, ok):
    assert is_valid_email(email) is ok


def test_undeliverable_and_counts(sample_rows):
    flags = [is_undeliverable(r) for r in sample_rows]
    assert flags == [False, False, False, False, True, True]
    assert count_resolved(sample_rows) == (4, 2)
    counts = dict(provider_counts(sample_rows))
    assert counts["Google Workspace"] == 2 and counts["Unknown"] == 1


def test_whitespace_final_name_is_unresolved():
    assert count_resolved([{"final_name": "   "}]) == (0, 1)


def test_select_keeps_role_accounts_when_names_not_required(sample_rows):
    kept, _ = select_rows(sample_rows, ExportSelection(only_resolved=False))
    assert "info@acme.com" in [r["email"] for r in kept]


def test_full_export_has_spec_columns_first(sample_rows):
    data, count = rows_to_csv_bytes(sample_rows, "full")
    header = data.decode("utf-8-sig").splitlines()[0].split(",")
    assert header[:7] == ["email", "is_real_name", "confidence", "final_name",
                          "name_parts", "reason", "company_website"]
    assert count == len(sample_rows)


def test_zip_handles_duplicate_file_names(tmp_path):
    import zipfile
    from exporters.csv_export import export_provider_zip
    groups = {"Gmail": [{"email": "a@gmail.com", "final_name": "A B"}],
              "GMAIL": [{"email": "c@gmail.com", "final_name": "C D"}]}
    contents = export_provider_zip(groups, tmp_path / "x.zip", "mailing")
    assert len(contents) == 2 and len(set(contents)) == 2
    assert len(zipfile.ZipFile(tmp_path / "x.zip").namelist()) == 2
