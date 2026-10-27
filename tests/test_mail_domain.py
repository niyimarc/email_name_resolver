"""Tests for mail provider detection and domain verification (no network)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pipeline.mail_domain as md  # noqa: E402


class FakeResp:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class FakeDoH:
    """Pretends to be Cloudflare/Google DNS-over-HTTPS."""

    ZONES = {
        ("acme.com", "MX"): {"Status": 0, "Answer": [
            {"type": 15, "data": "10 alt1.aspmx.l.google.com."},
            {"type": 15, "data": "1 aspmx.l.google.com."}]},
        ("contoso.com", "MX"): {"Status": 0, "Answer": [
            {"type": 15, "data": "0 contoso-com.mail.protection.outlook.com."}]},
        ("tinyshop.io", "MX"): {"Status": 0, "Answer": [
            {"type": 15, "data": "10 mx1.tinyshop-hosting.net."}]},
        ("nomail.org", "MX"): {"Status": 0, "Answer": [{"type": 15, "data": "0 ."}]},
        ("webonly.org", "MX"): {"Status": 0},
        ("webonly.org", "A"): {"Status": 0},
        ("gone.example", "MX"): {"Status": 3},
    }

    def __init__(self):
        self.calls = []

    def get(self, url, params, headers, timeout):
        assert timeout == md.DNS_TIMEOUT_SECONDS
        assert headers["accept"] == "application/dns-json"
        self.calls.append((url, params["name"], params["type"]))
        key = (params["name"], params["type"])
        if key not in self.ZONES:
            raise md.requests.ConnectionError("offline")
        return FakeResp(self.ZONES[key])


def classify(domain, service="cloudflare"):
    return md.classify_from_mx(md.lookup_mx(domain, service, session=FakeDoH()))


def test_providers_from_mx():
    assert classify("acme.com").provider == md.GOOGLE_WORKSPACE
    assert classify("acme.com").mx_hosts[0] == "aspmx.l.google.com"   # best priority first
    assert classify("contoso.com").provider == md.MICROSOFT_365
    assert classify("tinyshop.io").provider == md.OTHER_BUSINESS
    assert classify("acme.com").status == md.STATUS_VALID


def test_undeliverable_domains():
    assert classify("gone.example").status == md.STATUS_NO_DOMAIN
    assert classify("nomail.org").status == md.STATUS_NO_MAIL       # RFC 7505 null MX
    assert classify("webonly.org").status == md.STATUS_NO_MAIL      # no MX, no A
    assert classify("offline.test").status == md.STATUS_UNKNOWN     # network error


def test_google_dns_service_url():
    doh = FakeDoH()
    md.lookup_mx("acme.com", "google", session=doh)
    assert doh.calls[0][0] == md.DNS_SERVICES["google"]


def test_offline_classification():
    disposable = md.load_disposable_domains()
    assert len(disposable) > 5000 and "mailinator.com" in disposable
    assert md.classify_offline("mailinator.com", disposable).status == md.STATUS_DISPOSABLE
    assert md.classify_offline("Gmail.com", disposable).provider == md.GMAIL
    assert md.classify_offline("hotmail.co.uk", disposable).provider == md.OUTLOOK
    assert md.classify_offline("acme.com", disposable) is None   # needs DNS


# ---------------------------------------------------------------- more cases

import pytest  # noqa: E402


@pytest.mark.parametrize("hosts, expected", [
    (["smtp.google.com"], md.GOOGLE_WORKSPACE),                 # newer single-MX setup
    (["aspmx2.googlemail.com"], md.GOOGLE_WORKSPACE),           # legacy backup MX
    (["acme-com.mail.protection.outlook.com"], md.MICROSOFT_365),
    (["mx.zoho.eu"], "Zoho Mail"),
    (["mailstore1.secureserver.net"], "GoDaddy"),
    (["eu-smtp-inbound-1.mimecast.com"], "Mimecast gateway"),
    (["mx0a-001.pphosted.com"], "Proofpoint gateway"),
    (["mx.notgoogle.com.example"], md.OTHER_BUSINESS),          # suffix must match exactly
    ([], md.OTHER_BUSINESS),
])
def test_provider_from_mx(hosts, expected):
    assert md.provider_from_mx(hosts) == expected


def test_a_record_fallback_counts_as_deliverable():
    class Doh(FakeDoH):
        ZONES = {**FakeDoH.ZONES,
                 ("legacy.org", "MX"): {"Status": 0},
                 ("legacy.org", "A"): {"Status": 0, "Answer": [{"type": 1, "data": "1.2.3.4"}]}}

    info = md.classify_from_mx(md.lookup_mx("legacy.org", session=Doh()))
    assert info.status == md.STATUS_VALID and info.provider == md.OTHER_BUSINESS


def test_servfail_is_unknown_not_dead():
    class Doh(FakeDoH):
        ZONES = {("broken.org", "MX"): {"Status": 2}}

    info = md.classify_from_mx(md.lookup_mx("broken.org", session=Doh()))
    assert info.status == md.STATUS_UNKNOWN


def test_malformed_mx_records_are_skipped():
    class Doh(FakeDoH):
        ZONES = {("odd.org", "MX"): {"Status": 0, "Answer": [
            {"type": 15, "data": "garbage"}, {"type": 5, "data": "cname.odd.org."},
            {"type": 15, "data": "x mx.odd.org."}, {"type": 15, "data": "5 mx.google.com."}]}}

    mx = md.lookup_mx("odd.org", session=Doh())
    assert mx.status == "ok" and mx.hosts == ["mx.google.com"]


def test_invalid_json_from_dns_service_is_an_error():
    class BadJson:
        def get(self, *a, **k):
            return type("R", (), {"raise_for_status": lambda s: None,
                                  "json": lambda s: (_ for _ in ()).throw(ValueError("x"))})()

    assert md.lookup_mx("acme.com", session=BadJson()).status == "error"


def test_slug_and_offline_provider():
    assert md.slug("Outlook / Hotmail") == "outlook_hotmail"
    assert md.slug("!!!") == "other"
    assert md.offline_provider("gmail.com") == md.GMAIL
    assert md.offline_provider("acme.com").startswith("Business / other")


def test_missing_disposable_list_gives_empty_set(tmp_path):
    assert md.load_disposable_domains(tmp_path / "nope.txt") == set()


def test_free_provider_lookup_is_case_and_dot_insensitive():
    assert md.free_provider("GMAIL.COM.") == md.GMAIL
    assert md.free_provider("acme.com") is None
