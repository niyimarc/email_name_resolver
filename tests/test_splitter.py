"""Tests for the rule-based splitter (stage 1). No Ollama or network needed.

Run from the project root with:  python -m pytest -q
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.splitter import (  # noqa: E402
    PARTICLES,
    format_tokens_rule_based,
    split_email_local,
)
from pipeline.website import is_free_provider, resolve_company_website  # noqa: E402


def test_dot_separated_first_last():
    r = split_email_local("john.doe@acme.com")
    assert r["raw_local"] == "john.doe"
    assert r["domain"] == "acme.com"
    assert r["tokens"] == ["john", "doe"]
    assert r["candidate_first"] == "john"
    assert r["candidate_middle"] == []
    assert r["candidate_last"] == "doe"
    assert r["token_count"] == 2
    assert r["hint"] is None
    assert r["valid"] is True


def test_glued_lowercase_is_single_token_without_hint():
    r = split_email_local("johndoe@acme.com")
    assert r["tokens"] == ["johndoe"]
    assert r["token_count"] == 1
    assert r["candidate_first"] == "johndoe"
    assert r["candidate_last"] is None
    assert r["hint"] is None


def test_three_parts_have_middle():
    r = split_email_local("john.doe.smith@acme.com")
    assert r["tokens"] == ["john", "doe", "smith"]
    assert r["candidate_first"] == "john"
    assert r["candidate_middle"] == ["doe"]
    assert r["candidate_last"] == "smith"
    assert r["token_count"] == 3


@pytest.mark.parametrize("email, surname", [
    ("jdoe@acme.com", "doe"),
    ("jsmith@acme.com", "smith"),
])
def test_initial_plus_surname_hint(email, surname):
    r = split_email_local(email)
    assert r["hint"] == "initial+surname"
    assert r["candidate_first"] == email[0]
    assert r["candidate_last"] == surname
    assert format_tokens_rule_based(r) == [email[0].upper(), surname.capitalize()]


def test_initial_plus_surname_with_separator():
    r = split_email_local("j.doe@acme.com")
    assert r["tokens"] == ["doe"]  # single letters are not tokens
    assert r["hint"] == "initial+surname"
    assert r["candidate_first"] == "j"
    assert r["candidate_last"] == "doe"


def test_common_onset_is_not_initial_surname():
    assert split_email_local("smith@acme.com")["hint"] is None
    assert split_email_local("chris@acme.com")["hint"] is None


def test_digits_are_split_and_dropped():
    r = split_email_local("coolguy123@acme.com")
    assert r["tokens"] == ["coolguy"]
    assert r["token_count"] == 1
    assert r["hint"] is None


def test_plus_tag_is_stripped_and_underscore_split():
    r = split_email_local("jane_doe+news@acme.com")
    assert r["raw_local"] == "jane_doe+news"
    assert r["tokens"] == ["jane", "doe"]
    assert "news" not in r["tokens"]
    assert r["candidate_first"] == "jane"
    assert r["candidate_last"] == "doe"


def test_camel_case_split():
    r = split_email_local("JohnDoe@acme.com")
    assert r["raw_local"] == "JohnDoe"
    assert r["tokens"] == ["john", "doe"]
    assert r["candidate_first"] == "john"
    assert r["candidate_last"] == "doe"


def test_letter_digit_boundary_inside_name():
    r = split_email_local("john123doe@acme.com")
    assert r["tokens"] == ["john", "doe"]


def test_particle_hint_and_surname_grouping():
    r = split_email_local("mary.van.der.berg@acme.nl")
    assert r["tokens"] == ["mary", "van", "der", "berg"]
    assert r["hint"] == "particle"
    assert r["candidate_first"] == "mary"
    assert r["candidate_middle"] == []
    assert r["candidate_last"] == "van der berg"
    assert format_tokens_rule_based(r) == ["Mary", "van", "der", "Berg"]
    assert {"van", "der", "de", "da", "bin", "al", "von", "la", "le"} == set(PARTICLES)


def test_given_name_al_is_not_a_particle():
    assert split_email_local("al.smith@acme.com")["hint"] is None


@pytest.mark.parametrize("email", ["not-an-email", "@acme.com", "john@", "", "jo hn@acme.com"])
def test_invalid_email(email):
    r = split_email_local(email)
    assert r["valid"] is False
    assert r["error"] == "invalid email"
    assert r["tokens"] == []
    assert r["hint"] is None


def test_free_provider_domain():
    r = split_email_local("John.Doe@Gmail.com")
    assert r["domain"] == "gmail.com"
    assert r["tokens"] == ["john", "doe"]
    assert is_free_provider(r["domain"])
    # Free providers return None without any network request.
    assert resolve_company_website(r["domain"]) is None


def test_all_keys_present():
    r = split_email_local("x.y@acme.com")
    for key in ("raw_local", "domain", "tokens", "candidate_first",
                "candidate_middle", "candidate_last", "token_count", "hint"):
        assert key in r


# ---------------------------------------------------------------- more edge cases

@pytest.mark.parametrize("email, tokens", [
    ("JOHN.DOE@acme.com", ["john", "doe"]),                  # all caps
    ("john--doe@acme.com", ["john", "doe"]),                 # repeated separators
    (".john.doe.@acme.com", ["john", "doe"]),                # leading/trailing dots
    ("john~doe@acme.com", ["john", "doe"]),                  # other punctuation splits
    ("josé.garcía@acme.es", ["josé", "garcía"]),             # accented letters kept
    ("JohnPaulJones@acme.com", ["john", "paul", "jones"]),   # multi camelCase
    ("JOHNDoe@acme.com", ["john", "doe"]),                   # acronym + camelCase
    ("12345@acme.com", []),                                  # digits only
    ("a.b.c@acme.com", []),                                  # single letters only
])
def test_tokenisation_edge_cases(email, tokens):
    assert split_email_local(email)["tokens"] == tokens


def test_last_at_sign_is_used_for_domain():
    r = split_email_local('"odd@local"@acme.com')
    assert r["domain"] == "acme.com" and r["valid"]


def test_domain_is_lowercased_and_trailing_dot_removed():
    assert split_email_local("x.y@ACME.Com.")["domain"] == "acme.com"


def test_surrounding_whitespace_is_ignored():
    assert split_email_local("  john.doe@acme.com \n")["valid"] is True


def test_only_plus_tag_local_part():
    r = split_email_local("+news@acme.com")
    assert r["valid"] and r["tokens"] == []


@pytest.mark.parametrize("email, first, middle, last", [
    ("j.r.smith@acme.com", "j", ["r"], "smith"),
    ("van.der.berg@acme.nl", None, [], "van der berg"),
    ("omar.al.rashid@acme.com", "omar", [], "al rashid"),
    ("ana.maria.de.souza@acme.com", "ana", ["maria"], "de souza"),
])
def test_candidates(email, first, middle, last):
    r = split_email_local(email)
    assert (r["candidate_first"], r["candidate_middle"], r["candidate_last"]) == \
        (first, middle, last)


@pytest.mark.parametrize("email, hint", [
    ("tbrown@acme.com", "initial+surname"),
    ("chris@acme.com", None),        # "ch" is a normal start of a name
    ("stephen@acme.com", None),      # "st" too
    ("anna@acme.com", None),         # starts with a vowel
    ("jd@acme.com", None),           # too short to be initial+surname
    ("van.morrison@acme.com", None), # "Van" as a first name is not a particle
])
def test_hint_detection(email, hint):
    assert split_email_local(email)["hint"] == hint


def test_rule_based_formatting():
    assert format_tokens_rule_based(split_email_local("bad")) == []
    assert format_tokens_rule_based(split_email_local("omar.al.rashid@x.com")) == \
        ["Omar", "al", "Rashid"]
    assert format_tokens_rule_based(split_email_local("j.r.smith@x.com")) == ["J", "R", "Smith"]
