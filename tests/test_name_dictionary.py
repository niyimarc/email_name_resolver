"""Tests for the dictionary stage, using the bundled US name lists.

Run from the project root with:  python -m pytest -q
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.ai_judge import build_user_prompt  # noqa: E402
from pipeline.name_dictionary import (  # noqa: E402
    KIND_FIRST,
    KIND_ROLE,
    KIND_SURNAME,
    NameDictionary,
    add_user_words,
    parse_word_lines,
)
from pipeline.splitter import split_email_local  # noqa: E402


@pytest.fixture(scope="module")
def nd():
    return NameDictionary.load()


def resolve(nd, email):
    return nd.resolve(split_email_local(email))


def test_bundled_lists_loaded(nd):
    assert len(nd.first) > 5000
    assert len(nd.surnames) > 100000
    assert "info" in nd.roles


@pytest.mark.parametrize("email, name, conf", [
    ("john.doe@acme.com", "John Doe", 95),
    ("doe.john@acme.com", "John Doe", 85),          # surname.first order
    ("johndoe@acme.com", "John Doe", 85),           # glued
    ("sarahjones@acme.com", "Sarah Jones", 85),
    ("jamesmcdonald@acme.com", "James McDonald", 85),
    ("john.paul.smith@acme.com", "John Paul Smith", 90),
    ("mary.van.der.berg@acme.nl", "Mary van der Berg", 90),
    ("jdoe@acme.com", "J Doe", 75),
    ("j.r.smith@acme.com", "J R Smith", 75),
])
def test_confident_matches_skip_ai(nd, email, name, conf):
    v = resolve(nd, email)
    assert v is not None and v.is_real_name
    assert v.final_name == name
    assert v.confidence == conf
    assert v.reason.startswith("dictionary:")


@pytest.mark.parametrize("email", [
    "info@acme.com", "sales.team@acme.com", "no-reply@acme.com",
    "salesteam@acme.com", "admin2@acme.com",
])
def test_role_mailboxes_are_not_people(nd, email):
    v = resolve(nd, email)
    assert v is not None and not v.is_real_name and v.final_name is None


@pytest.mark.parametrize("email", [
    "coolguy123@gmail.com",          # handle
    "john@acme.com",                 # lone first name
    "john.sales@acme.com",           # mixed name + role word
    "oluwaseun.adeyemi@acme.com",    # not in US first-name list
    "johnson@acme.com",              # whole token is a known name
])
def test_uncertain_cases_go_to_ai(nd, email):
    assert resolve(nd, email) is None


def test_evidence_for_ai(nd):
    ev = nd.evidence(split_email_local("john.sales@acme.com"))
    assert "first_name" in ev["tags"]["john"]
    assert "role_word" in ev["tags"]["sales"]
    # whole known names are never given a bogus split
    assert "suggested_split" not in nd.evidence(split_email_local("johnson@x.com"))
    info = split_email_local("john.sales@acme.com")
    info["dictionary"] = ev
    assert '"dictionary"' in build_user_prompt(info)


def test_parse_word_lines_handles_headers_tsv_csv():
    lines = ["# comment", "first_name,count", "Zorblax,12", "Quentavious", "two words", ""]
    parsed = parse_word_lines(lines)
    assert parsed["zorblax"] == 12
    assert "quentavious" in parsed
    assert "first_name" not in parsed and "two words" not in parsed


def test_user_lists_extend_and_block(tmp_path):
    assert add_user_words(tmp_path, KIND_FIRST, ["Zorblax", "zorblax"]) == 1
    assert add_user_words(tmp_path, KIND_SURNAME, ["Quibbleworth"]) == 1
    assert add_user_words(tmp_path, KIND_ROLE, ["coolguy"]) == 1
    nd = NameDictionary.load(user_dir=tmp_path)

    v = nd.resolve(split_email_local("zorblax.quibbleworth@acme.com"))
    assert v is not None and v.final_name == "Zorblax Quibbleworth"

    v = nd.resolve(split_email_local("coolguy@gmail.com"))
    assert v is not None and not v.is_real_name

    # a word the user blocked never resolves as a name
    add_user_words(tmp_path, KIND_ROLE, ["garden"])
    nd = NameDictionary.load(user_dir=tmp_path)
    assert "garden" not in nd.surnames


# ---------------------------------------------------------------- more cases

from pipeline.name_dictionary import (  # noqa: E402
    USER_FILES,
    DictionaryStore,
    ensure_user_files,
    format_name_part,
)


@pytest.mark.parametrize("token, expected", [
    ("john", "John"), ("mcdonald", "McDonald"), ("mc", "Mc"),
    ("van", "van"), ("o", "O"),
])
def test_format_name_part(token, expected):
    assert format_name_part(token) == expected


@pytest.mark.parametrize("token, is_role", [
    ("info", True), ("noreply", True), ("salesteam", True), ("supportdesk", False),
    ("john", False), ("it", True),
])
def test_role_words_including_glued_pairs(nd, token, is_role):
    assert nd.is_role(token) is is_role


def test_rare_surname_is_not_confident(nd):
    # "office" is a real but rare US surname: never enough on its own
    assert nd.surnames.get("office", 0) < nd.t.surname_min_count
    assert nd.resolve(split_email_local("mark.zzyzxq@acme.com")) is None


def test_ambiguous_glued_split_goes_to_ai(nd):
    split = nd.split_glued("johnsmith")
    assert split[:2] == ("john", "smith") and split[2] is True
    assert nd.split_glued("zzzqqq") is None


def test_ensure_user_files_creates_templates_once(tmp_path):
    ensure_user_files(tmp_path)
    for name in USER_FILES.values():
        assert (tmp_path / name).read_text(encoding="utf-8").startswith("#")
    (tmp_path / USER_FILES["first"]).write_text("# mine\nzorblax\n", encoding="utf-8")
    ensure_user_files(tmp_path)                      # must not overwrite
    assert "zorblax" in (tmp_path / USER_FILES["first"]).read_text(encoding="utf-8")


def test_add_user_words_rejects_unknown_kind(tmp_path):
    with pytest.raises(ValueError):
        add_user_words(tmp_path, "nicknames", ["x"])


def test_add_user_words_skips_junk(tmp_path):
    assert add_user_words(tmp_path, KIND_FIRST, ["", "a", "two words", "Ok1", "Valid"]) == 1


def test_dictionary_store_loads_lazily_and_reloads(tmp_path):
    user_dir = tmp_path / "dicts"
    store = DictionaryStore(lambda: user_dir)
    first = store.get()
    assert store.get() is first                       # cached
    add_user_words(store.user_dir, KIND_FIRST, ["zorblax"])
    assert "zorblax" not in store.get().first         # still the cached copy
    store.invalidate()
    assert "zorblax" in store.get().first             # reloaded


def test_user_name_overrides_thresholds(tmp_path):
    add_user_words(tmp_path, KIND_SURNAME, ["office"])   # rare census surname
    nd2 = NameDictionary.load(user_dir=tmp_path)
    v = nd2.resolve(split_email_local("mark.office@acme.com"))
    # "office" is still a role word, so the mixed address goes to the AI
    assert v is None
