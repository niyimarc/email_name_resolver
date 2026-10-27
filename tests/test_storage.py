"""Settings and session persistence (%APPDATA%/EmailNameResolver/...).

%APPDATA% is redirected to a temp folder by conftest, so these tests never
touch the real files.
"""

import json
import os
from pathlib import Path

import pytest

from storage.session import (
    ROW_DEFAULTS,
    clear_session,
    load_session,
    normalize_row,
    save_session,
    session_path,
)
from storage.settings import (
    Settings,
    app_data_dir,
    atomic_write_text,
    dictionaries_dir,
    load_settings,
    save_settings,
    settings_path,
)


# ----------------------------------------------------------------- app data dir

class TestAppDataDir:
    def test_uses_appdata_and_creates_folder(self, isolated_appdata):
        path = app_data_dir()
        assert path == isolated_appdata / "EmailNameResolver"
        assert path.is_dir()

    def test_falls_back_to_home_config_without_appdata(self, monkeypatch, tmp_path):
        monkeypatch.delenv("APPDATA", raising=False)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        assert app_data_dir() == tmp_path / ".config" / "EmailNameResolver"

    def test_file_locations(self, isolated_appdata):
        base = isolated_appdata / "EmailNameResolver"
        assert settings_path() == base / "settings.json"
        assert session_path() == base / "last_session.json"
        assert dictionaries_dir() == base / "dictionaries"


def test_atomic_write_replaces_file_and_leaves_no_temp(tmp_path):
    target = tmp_path / "sub" / "file.json"
    atomic_write_text(target, "one")
    atomic_write_text(target, "two")
    assert target.read_text(encoding="utf-8") == "two"
    assert [p.name for p in target.parent.iterdir()] == ["file.json"]


# ----------------------------------------------------------------- settings

class TestSettings:
    def test_defaults_when_no_file(self):
        assert load_settings() == Settings()

    def test_default_values(self):
        s = Settings()
        assert (s.model, s.base_url) == ("llama3.1", "http://localhost:11434")
        assert s.resolve_website is False and s.use_dictionary is True
        assert s.ai_parallel == 1 and s.check_domains is True
        assert s.dns_service == "cloudflare"
        assert (s.export_format, s.export_only_resolved) == ("mailing", True)

    def test_round_trip(self):
        original = Settings(model="llama3.2", base_url="http://10.0.0.5:11434",
                            resolve_website=True, use_dictionary=False, ai_parallel=3,
                            check_domains=False, dns_service="google",
                            export_format="full", export_only_resolved=False)
        save_settings(original)
        assert load_settings() == original

    def test_saved_file_is_readable_json(self):
        path = save_settings(Settings(model="mistral"))
        assert json.loads(path.read_text(encoding="utf-8"))["model"] == "mistral"

    @pytest.mark.parametrize("content", ["", "{not json", "[1, 2, 3]", "null"])
    def test_corrupt_file_gives_defaults(self, content):
        settings_path().write_text(content, encoding="utf-8")
        assert load_settings() == Settings()

    def test_wrong_types_fall_back_per_field(self):
        settings_path().write_text(json.dumps({
            "model": 123, "base_url": "http://ok:1", "resolve_website": "yes",
            "ai_parallel": "4"}), encoding="utf-8")
        s = load_settings()
        assert s.model == "llama3.1"              # wrong type -> default
        assert s.base_url == "http://ok:1"        # valid -> kept
        assert s.resolve_website is False
        assert s.ai_parallel == 1

    def test_blank_strings_fall_back_and_values_are_trimmed(self):
        settings_path().write_text(json.dumps({"model": "   ", "base_url": "  http://x:1  "}),
                                   encoding="utf-8")
        s = load_settings()
        assert s.model == "llama3.1"
        assert s.base_url == "http://x:1"

    @pytest.mark.parametrize("stored, expected", [(0, 1), (-5, 1), (2, 2), (9, 4)])
    def test_ai_parallel_is_clamped_1_to_4(self, stored, expected):
        settings_path().write_text(json.dumps({"ai_parallel": stored}), encoding="utf-8")
        assert load_settings().ai_parallel == expected

    def test_unknown_choices_fall_back(self):
        settings_path().write_text(json.dumps({"dns_service": "quad9",
                                               "export_format": "xlsx"}), encoding="utf-8")
        s = load_settings()
        assert s.dns_service == "cloudflare" and s.export_format == "mailing"

    def test_unknown_keys_are_ignored(self):
        settings_path().write_text(json.dumps({"model": "m", "future_option": 1}),
                                   encoding="utf-8")
        assert load_settings().model == "m"


# ----------------------------------------------------------------- session

class TestNormalizeRow:
    def test_fills_every_default_key(self):
        assert set(normalize_row({})) == set(ROW_DEFAULTS)

    def test_drops_internal_keys(self):
        assert "_pending" not in normalize_row({"email": "a@b.co", "_pending": True})

    @pytest.mark.parametrize("raw, expected", [(150, 100), (-3, 0), ("42", 42),
                                               ("abc", 0), (None, 0), (77.9, 77)])
    def test_confidence_is_coerced_and_clamped(self, raw, expected):
        assert normalize_row({"confidence": raw})["confidence"] == expected

    def test_types_are_coerced(self):
        row = normalize_row({"email": None, "is_real_name": 1, "name_parts": "John",
                             "final_name": None, "website_verified": "x"})
        assert row["email"] == "" and row["is_real_name"] is True
        assert row["name_parts"] == []           # a string is not a list of parts
        assert row["final_name"] == "" and row["website_verified"] is True

    def test_name_parts_become_strings(self):
        assert normalize_row({"name_parts": ["A", 1]})["name_parts"] == ["A", "1"]


class TestSession:
    def test_missing_file_gives_empty_list(self):
        assert load_session() == []

    def test_round_trip_keeps_user_edits(self, sample_rows):
        sample_rows[0]["final_name"] = "Johnny Doe"     # a user edit
        save_session(sample_rows)
        loaded = load_session()
        assert len(loaded) == len(sample_rows)
        assert loaded[0]["final_name"] == "Johnny Doe"
        assert loaded[1]["name_parts"] == ["Mary", "van", "der", "Berg"]
        assert loaded[4]["email_status"] == "disposable"

    def test_file_has_version_and_timestamp(self, sample_rows):
        payload = json.loads(save_session(sample_rows).read_text(encoding="utf-8"))
        assert payload["version"] == 1 and "saved_at" in payload

    def test_unicode_is_preserved(self):
        save_session([{"email": "jose@x.es", "final_name": "José García"}])
        assert load_session()[0]["final_name"] == "José García"

    @pytest.mark.parametrize("content", ["", "{", '{"rows": "nope"}', "42"])
    def test_corrupt_file_gives_empty_list(self, content):
        session_path().write_text(content, encoding="utf-8")
        assert load_session() == []

    def test_legacy_plain_list_and_junk_rows(self):
        session_path().write_text(json.dumps([{"email": "a@b.co"}, "junk", 3]),
                                  encoding="utf-8")
        assert [r["email"] for r in load_session()] == ["a@b.co"]

    def test_clear_removes_file_and_is_idempotent(self, sample_rows):
        save_session(sample_rows)
        clear_session()
        assert not session_path().exists()
        clear_session()                          # second call must not fail
        assert load_session() == []

    def test_save_does_not_mutate_input(self, sample_rows):
        sample_rows[0]["_pending"] = True
        save_session(sample_rows)
        assert sample_rows[0]["_pending"] is True
        assert not any("_pending" in r for r in load_session())


def test_session_lives_in_appdata(isolated_appdata, sample_rows):
    path = save_session(sample_rows)
    assert str(path).startswith(str(isolated_appdata))
    assert os.path.basename(path) == "last_session.json"
