"""AI judge (stage 2): prompt, Ollama call options, safety checks, error handling.

Ollama itself is never contacted: a fake client records what would be sent.
"""

import json

import httpx
import ollama
import pytest

from pipeline.ai_judge import (
    OLLAMA_KEEP_ALIVE,
    SYSTEM_PROMPT,
    UNAVAILABLE_REASON,
    NameVerdict,
    build_user_prompt,
    check_ollama_available,
    judge_name_with_ai,
    list_local_models,
    model_is_pulled,
    sanitize_verdict,
    unavailable_verdict,
)
from pipeline.splitter import split_email_local


def verdict(**kw):
    base = {"is_real_name": True, "confidence": 90, "reason": "r",
            "final_name": None, "name_parts": []}
    base.update(kw)
    return NameVerdict(**base)


# ----------------------------------------------------------------- prompt

class TestPrompt:
    def test_system_prompt_states_the_no_invention_rule(self):
        assert "never invent names" in SYSTEM_PROMPT
        assert "is_real_name=false and final_name=null" in SYSTEM_PROMPT

    def test_user_prompt_contains_required_fields(self):
        info = split_email_local("jane_doe+news@acme.com")
        payload = json.loads(build_user_prompt(info).split("\n", 1)[1])
        assert payload == {"raw_local": "jane_doe+news", "domain": "acme.com",
                           "tokens": ["jane", "doe"], "hint": None}

    def test_user_prompt_includes_dictionary_evidence_when_present(self):
        info = dict(split_email_local("john.sales@acme.com"),
                    dictionary={"tags": {"john": ["first_name"]}})
        assert '"dictionary"' in build_user_prompt(info)

    def test_non_ascii_is_kept_readable(self):
        info = split_email_local("josé@acme.com")
        assert "josé" in build_user_prompt(info)


# ----------------------------------------------------------------- sanitize

class TestSanitize:
    info = split_email_local("jdoe@acme.com")

    def test_invented_name_is_rejected(self):
        v = sanitize_verdict(verdict(final_name="John Doe", name_parts=["John", "Doe"]),
                             self.info)
        assert v.is_real_name is False and v.final_name is None and v.name_parts == []
        assert "John" in v.reason

    def test_initial_with_dot_is_accepted(self):
        v = sanitize_verdict(verdict(name_parts=["J.", "Doe"]), self.info)
        assert v.is_real_name and v.final_name == "J. Doe"

    def test_final_name_is_rebuilt_from_parts(self):
        info = split_email_local("mary.van.der.berg@x.nl")
        v = sanitize_verdict(verdict(final_name="something else",
                                     name_parts=["Mary", "van", "der", "Berg"]), info)
        assert v.final_name == "Mary van der Berg"

    def test_parts_are_derived_from_final_name_if_missing(self):
        info = split_email_local("john.doe@acme.com")
        v = sanitize_verdict(verdict(final_name="John Doe", name_parts=[]), info)
        assert v.name_parts == ["John", "Doe"]

    def test_not_real_never_has_final_name(self):
        info = split_email_local("john.doe@acme.com")
        v = sanitize_verdict(verdict(is_real_name=False, final_name="John Doe",
                                     name_parts=["John", "Doe"]), info)
        assert v.final_name is None and v.is_real_name is False
        assert v.name_parts == ["John", "Doe"]     # verified parts are kept

    def test_plus_tag_letters_cannot_be_used(self):
        info = split_email_local("jane+news@acme.com")
        v = sanitize_verdict(verdict(name_parts=["Jane", "News"]), info)
        assert v.is_real_name is False

    def test_empty_parts_are_ignored(self):
        info = split_email_local("john.doe@acme.com")
        v = sanitize_verdict(verdict(name_parts=["John", " ", "", "Doe"]), info)
        assert v.name_parts == ["John", "Doe"]


# ----------------------------------------------------------------- judge

class TestJudge:
    def test_valid_answer_and_ollama_call_options(self, fake_ollama):
        client = fake_ollama({"john.doe": {"is_real_name": True, "confidence": 93,
                                           "reason": "ok", "final_name": "John Doe",
                                           "name_parts": ["John", "Doe"]}})
        v = judge_name_with_ai(split_email_local("john.doe@acme.com"), "llama3.1",
                               client=client)
        assert (v.is_real_name, v.confidence, v.final_name) == (True, 93, "John Doe")
        call = client.calls[0]
        assert call["model"] == "llama3.1"
        assert call["format"] == NameVerdict.model_json_schema()
        assert call["options"]["temperature"] == 0
        assert call["options"]["num_predict"] > 0
        assert call["keep_alive"] == OLLAMA_KEEP_ALIVE
        assert call["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT}

    def test_invalid_email_never_calls_ollama(self, fake_ollama):
        client = fake_ollama()
        v = judge_name_with_ai(split_email_local("not-an-email"), "m", client=client)
        assert v.reason == "invalid email" and client.calls == []

    def test_no_tokens_never_calls_ollama(self, fake_ollama):
        client = fake_ollama()
        v = judge_name_with_ai(split_email_local("12345@acme.com"), "m", client=client)
        assert v.is_real_name is False and client.calls == []

    @pytest.mark.parametrize("error", [
        ConnectionError("down"), TimeoutError("slow"), OSError("refused"),
        httpx.ConnectError("refused"), httpx.ReadTimeout("120s"),
    ])
    def test_connection_problems_give_unavailable_verdict(self, fake_ollama, error):
        client = fake_ollama({"john.doe": error})
        v = judge_name_with_ai(split_email_local("john.doe@acme.com"), "m", client=client)
        assert v == unavailable_verdict()
        assert v.reason == UNAVAILABLE_REASON == "ollama unavailable"

    def test_missing_model(self, fake_ollama):
        client = fake_ollama({"john.doe": ollama.ResponseError("model not found", 404)})
        v = judge_name_with_ai(split_email_local("john.doe@acme.com"), "nope", client=client)
        assert v.reason == "ollama model not found: nope" and not v.is_real_name

    def test_other_server_error(self, fake_ollama):
        client = fake_ollama({"john.doe": ollama.ResponseError("boom", 500)})
        v = judge_name_with_ai(split_email_local("john.doe@acme.com"), "m", client=client)
        assert v.reason.startswith("ollama error") and not v.is_real_name

    @pytest.mark.parametrize("content", ["not json", "{}", '{"is_real_name": "maybe"}', "[]"])
    def test_malformed_answers_are_rejected(self, fake_ollama, content):
        client = fake_ollama({"john.doe": content})
        v = judge_name_with_ai(split_email_local("john.doe@acme.com"), "m", client=client)
        assert v.reason == "invalid AI response" and not v.is_real_name

    @pytest.mark.parametrize("raw, expected", [(140, 100), (-20, 0), (55.6, 56)])
    def test_out_of_range_confidence_is_clamped(self, fake_ollama, raw, expected):
        client = fake_ollama({"john.doe": {"is_real_name": True, "confidence": raw,
                                           "reason": "r", "final_name": "John Doe",
                                           "name_parts": ["John", "Doe"]}})
        v = judge_name_with_ai(split_email_local("john.doe@acme.com"), "m", client=client)
        assert v.confidence == expected

    def test_object_style_response_is_supported(self):
        class Msg:
            content = json.dumps({"is_real_name": False, "confidence": 5, "reason": "x",
                                  "final_name": None, "name_parts": []})

        class Resp:
            message = Msg()

            def __getitem__(self, key):
                raise TypeError("not subscriptable")

        class Client:
            def chat(self, **kw):
                return Resp()

        v = judge_name_with_ai(split_email_local("john.doe@acme.com"), "m", client=Client())
        assert v.reason == "x"

    def test_unreachable_host_with_real_client(self):
        # The autouse no_network fixture blocks sockets: a real client must
        # report "ollama unavailable" rather than crash.
        v = judge_name_with_ai(split_email_local("john.doe@acme.com"), "m",
                               host="http://127.0.0.1:9")
        assert v.reason == UNAVAILABLE_REASON


# ----------------------------------------------------------------- server checks

class TestServerChecks:
    def test_list_models_and_availability(self, monkeypatch, fake_response):
        calls = []

        def fake_get(url, timeout):
            calls.append((url, timeout))
            return fake_response({"models": [{"name": "llama3.1:latest"},
                                             {"name": "mistral:7b"}]})

        import requests
        monkeypatch.setattr(requests, "get", fake_get)
        assert list_local_models("http://h:11434/") == ["llama3.1:latest", "mistral:7b"]
        assert check_ollama_available("http://h:11434") is True
        assert calls[0] == ("http://h:11434/api/tags", 3.0)

    def test_unreachable_server(self, monkeypatch):
        import requests

        def fail(*a, **k):
            raise requests.ConnectionError("down")

        monkeypatch.setattr(requests, "get", fail)
        assert list_local_models("http://h:1") is None
        assert check_ollama_available("http://h:1") is False

    def test_bad_json_from_server(self, monkeypatch, fake_response):
        import requests
        monkeypatch.setattr(requests, "get", lambda *a, **k: fake_response(ValueError("x")))
        assert list_local_models("http://h:1") is None

    @pytest.mark.parametrize("model, available, expected", [
        ("llama3.1", ["llama3.1:latest"], True),
        ("llama3.1", ["llama3.1"], True),
        ("llama3.1:8b", ["llama3.1:latest"], False),
        ("huihui_ai/dolphin3-abliterated:8b-llama3.1-q4_K_M",
         ["huihui_ai/dolphin3-abliterated:8b-llama3.1-q4_K_M"], True),
        ("mistral", [], False),
    ])
    def test_model_is_pulled(self, model, available, expected):
        assert model_is_pulled(model, available) is expected
