"""Company website resolution (stage 3) with a fake HTTP session."""

import pytest
import requests

from pipeline.website import (
    FREE_PROVIDERS,
    WEBSITE_TIMEOUT_SECONDS,
    WebsiteResolver,
    WebsiteResult,
    is_free_provider,
    resolve_company_website,
    resolve_company_website_detailed,
)


class FakeSession:
    """Records requests; ``outcome`` is a final URL string or an exception."""

    def __init__(self, outcome, fake_response):
        self.outcome = outcome
        self.fake_response = fake_response
        self.calls = []
        self.responses = []
        self.closed = False

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        resp = self.fake_response(url=self.outcome)
        self.responses.append(resp)
        return resp

    def close(self):
        self.closed = True


@pytest.fixture
def session(fake_response):
    return lambda outcome: FakeSession(outcome, fake_response)


def test_spec_free_provider_list_is_complete():
    expected = {"gmail.com", "googlemail.com", "yahoo.com", "outlook.com", "hotmail.com",
                "live.com", "icloud.com", "me.com", "proton.me", "protonmail.com",
                "aol.com", "gmx.com", "mail.com", "yandex.com", "zoho.com"}
    assert expected <= FREE_PROVIDERS


@pytest.mark.parametrize("domain", ["gmail.com", "GMAIL.COM", " yahoo.com. "])
def test_free_providers_return_none_without_network(domain, session):
    s = session("https://never.example")
    assert is_free_provider(domain)
    assert resolve_company_website(domain, session=s) is None
    assert s.calls == []


@pytest.mark.parametrize("domain", ["", "localhost", "192.168.1.10", "bad_domain.com"])
def test_non_public_domains_return_none(domain, session):
    s = session("https://x.com")
    assert resolve_company_website(domain, session=s) is None
    assert s.calls == []


def test_request_options(session):
    s = session("https://acme.com/")
    resolve_company_website("acme.com", session=s)
    url, kw = s.calls[0]
    assert url == "https://acme.com"
    assert kw["timeout"] == WEBSITE_TIMEOUT_SECONDS == 3
    assert kw["allow_redirects"] is True and kw["stream"] is True
    assert "Mozilla" in kw["headers"]["User-Agent"]
    assert s.responses[0].closed          # body never downloaded


@pytest.mark.parametrize("final_url, expected", [
    ("https://acme.com/", "https://acme.com"),
    ("https://www.acme.com/home?x=1", "https://www.acme.com"),
    ("https://shop.acme.com/", "https://shop.acme.com"),
    ("http://acme.com/", "http://acme.com"),
    ("https://acme.com:8443/", "https://acme.com:8443"),
])
def test_on_domain_redirects_are_verified(session, final_url, expected):
    result = resolve_company_website_detailed("acme.com", session=session(final_url))
    assert result == WebsiteResult(expected, True)


@pytest.mark.parametrize("final_url", ["https://godaddy.com/parked",
                                       "https://notacme.com/", "https://acme.com.evil.io/"])
def test_off_domain_redirect_falls_back_unverified(session, final_url):
    result = resolve_company_website_detailed("acme.com", session=session(final_url))
    assert result == WebsiteResult("https://acme.com", False)


@pytest.mark.parametrize("error", [requests.ConnectionError("x"), requests.Timeout("x"),
                                   requests.TooManyRedirects("x")])
def test_failed_request_falls_back_unverified(session, error):
    result = resolve_company_website_detailed("acme.com", session=session(error))
    assert result == WebsiteResult("https://acme.com", False)
    assert resolve_company_website("acme.com", session=session(error)) == "https://acme.com"


def test_cache_avoids_repeat_requests(session):
    s = session("https://www.acme.com/")
    cache = {}
    first = resolve_company_website_detailed("acme.com", cache, s)
    second = resolve_company_website_detailed("ACME.com.", cache, s)
    assert first == second and len(s.calls) == 1


def test_resolver_class_uses_its_own_cache(monkeypatch, fake_response):
    calls = []

    def fake_get(self, url, **kw):
        calls.append(url)
        return fake_response(url="https://www.acme.com/")

    monkeypatch.setattr(requests.Session, "get", fake_get)
    resolver = WebsiteResolver()
    assert resolver.resolve("acme.com").verified
    assert resolver.resolve("acme.com").url == "https://www.acme.com"
    assert calls == ["https://acme.com"]
    resolver.close()
