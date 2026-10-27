"""Runner: domain checks, website lookups and Ollama failure modes.

DNS, websites and Ollama are all faked; callbacks are recorded directly.
"""

import threading
import time

import ollama
import pytest

import pipeline.mail_domain as md
import pipeline.runner as runner_mod
from pipeline.email_extract import email_key
from pipeline.runner import STOPPED_REASON, PipelineRunner, RunCallbacks
from pipeline.website import WebsiteResult
from storage.settings import Settings

MX = {
    "acme.com": md.MxResult("ok", ["aspmx.l.google.com"]),
    "contoso.com": md.MxResult("ok", ["contoso-com.mail.protection.outlook.com"]),
    "gone.biz": md.MxResult("nxdomain"),
    "nomail.org": md.MxResult("null_mx"),
    "flaky.net": md.MxResult("error"),
}


class Recorder:
    def __init__(self):
        self.lock = threading.Lock()
        self.rows = {}
        self.updates = []
        self.banners = []
        self.done = threading.Event()
        self.stats = None

    def post(self, fn, *args):
        with self.lock:
            fn(*args)

    def callbacks(self):
        def on_row(row):
            self.rows[email_key(row["email"])] = dict(row)

        def on_update(key, changes):
            self.updates.append((key, changes))
            self.rows[key].update(changes)

        def on_done(stats):
            self.stats = stats
            self.done.set()

        return RunCallbacks(on_row=on_row, on_update=on_update,
                            on_progress=lambda *a: None, on_phase=lambda *a: None,
                            on_banner=self.banners.append, on_done=on_done)


class _Store:
    def __init__(self, d):
        self.d = d

    def get(self):
        return self.d


@pytest.fixture
def run(monkeypatch, dictionary, fake_ollama):
    """run(emails, settings=..., client=..., models=..., websites=...) -> Recorder."""

    def _run(emails, settings=None, client=None, models=("llama3.1:latest",), websites=None):
        dns_calls, web_calls = [], []

        def fake_lookup(domain, service="cloudflare", session=None):
            dns_calls.append((domain, service))
            return MX.get(domain, md.MxResult("error"))

        def fake_website(domain, cache=None, session=None):
            web_calls.append(domain)
            return (websites or {}).get(domain, WebsiteResult(f"https://{domain}", False))

        monkeypatch.setattr(runner_mod, "lookup_mx", fake_lookup)
        monkeypatch.setattr(runner_mod, "resolve_company_website_detailed", fake_website)
        monkeypatch.setattr(runner_mod, "list_local_models",
                            lambda host: None if models is None else list(models))
        client = client or fake_ollama()
        monkeypatch.setattr(runner_mod, "make_client", lambda host: client)
        rec = Recorder()
        r = PipelineRunner(list(emails), settings or Settings(), rec.callbacks(), rec.post,
                           threading.Event(), threading.Event(),
                           dictionary_store=_Store(dictionary))
        r.start()
        assert rec.done.wait(10), "runner did not finish"
        rec.dns_calls, rec.web_calls, rec.client = dns_calls, web_calls, client
        return rec

    return _run


def status(rec, email):
    row = rec.rows[email_key(email)]
    return row["mail_provider"], row["email_status"]


class TestDomainChecks:
    EMAILS = ["john.doe@acme.com", "sarah.jones@contoso.com", "mike.brown@gmail.com",
              "info@acme.com", "mary.smith@gone.biz", "kevin.lee@nomail.org",
              "tom.hanks@flaky.net", "jane.taylor@mailinator.com", "bad"]

    def test_provider_and_status_for_every_kind_of_domain(self, run):
        rec = run(self.EMAILS)
        assert status(rec, "john.doe@acme.com") == ("Google Workspace", "valid")
        assert status(rec, "sarah.jones@contoso.com") == ("Microsoft 365", "valid")
        assert status(rec, "mike.brown@gmail.com") == ("Gmail", "valid")
        assert status(rec, "info@acme.com") == ("Google Workspace", "role account")
        assert status(rec, "mary.smith@gone.biz") == ("No mail server", "domain doesn't exist")
        assert status(rec, "kevin.lee@nomail.org") == ("No mail server", "no mail server")
        assert status(rec, "tom.hanks@flaky.net") == ("Unknown", "unknown (lookup failed)")
        assert status(rec, "jane.taylor@mailinator.com") == ("Other free provider", "disposable")
        assert status(rec, "bad") == ("", "invalid syntax")

    def test_one_dns_lookup_per_company_domain_and_none_for_free(self, run):
        rec = run(self.EMAILS)
        domains = sorted(d for d, _ in rec.dns_calls)
        assert domains == ["acme.com", "contoso.com", "flaky.net", "gone.biz", "nomail.org"]

    def test_dns_service_setting_is_used(self, run):
        rec = run(["john.doe@acme.com"], Settings(dns_service="google"))
        assert rec.dns_calls == [("acme.com", "google")]

    def test_checks_disabled(self, run):
        rec = run(["john.doe@acme.com", "mike.brown@gmail.com"], Settings(check_domains=False))
        assert rec.dns_calls == []
        assert status(rec, "john.doe@acme.com") == ("Business / other (not checked)",
                                                    "not checked")
        assert status(rec, "mike.brown@gmail.com") == ("Gmail", "valid")


class TestWebsites:
    def test_websites_resolved_once_per_domain_and_applied_to_rows(self, run):
        rec = run(["john.doe@acme.com", "jane.roe@acme.com", "x.y@gone.biz"],
                  Settings(resolve_website=True),
                  websites={"acme.com": WebsiteResult("https://www.acme.com", True)})
        assert rec.web_calls == ["acme.com"]            # gone.biz doesn't exist: skipped
        for email in ("john.doe@acme.com", "jane.roe@acme.com"):
            row = rec.rows[email_key(email)]
            assert row["company_website"] == "https://www.acme.com"
            assert row["website_verified"] is True

    def test_websites_off_by_default(self, run):
        rec = run(["john.doe@acme.com"])
        assert rec.web_calls == [] and rec.rows[email_key("john.doe@acme.com")][
            "company_website"] == ""


class TestOllamaFailures:
    def test_ollama_down_banner_once_and_rule_based_rows(self, run):
        rec = run(["coolguy123@gmail.com", "zxq.wpl@acme.com"], models=None)
        assert len(rec.banners) == 1 and "Ollama not reachable" in rec.banners[0]
        assert rec.client.calls == []
        reasons = {r["reason"] for r in rec.rows.values()}
        assert reasons == {"ollama unavailable; rule-based split only"}

    def test_model_not_pulled(self, run):
        rec = run(["coolguy123@gmail.com"], Settings(model="mistral"))
        assert "mistral" in rec.banners[0] and "ollama pull mistral" in rec.banners[0]
        assert rec.client.calls == []

    def test_ollama_dies_mid_run(self, run, fake_ollama):
        emails = [f"zz{i}qq.wplk@acme.com" for i in range(5)]
        client = fake_ollama({"zz0qq.wplk": ConnectionError("gone"),
                              "zz1qq.wplk": ConnectionError("gone"),
                              "zz2qq.wplk": ConnectionError("gone"),
                              "zz3qq.wplk": ConnectionError("gone"),
                              "zz4qq.wplk": ConnectionError("gone")})
        rec = run(emails, client=client)
        assert len(rec.banners) == 1                     # never repeated per row
        assert len(client.calls) == 1                    # AI disabled after the failure
        assert all("ollama unavailable" in r["reason"] for r in rec.rows.values())
        assert rec.stats.processed == len(emails)

    def test_model_error_is_reported_per_row(self, run, fake_ollama):
        client = fake_ollama({"zzqq.wplk": ollama.ResponseError("bad", 500)})
        rec = run(["zzqq.wplk@acme.com"], client=client)
        assert rec.rows[email_key("zzqq.wplk@acme.com")]["reason"].startswith("ollama error")

    def test_dictionary_off_sends_everything_to_ai(self, run):
        rec = run(["john.doe@acme.com", "info@acme.com"], Settings(use_dictionary=False))
        sent = sorted(c["payload"]["raw_local"] for c in rec.client.calls)
        assert sent == ["info", "john.doe"]


@pytest.mark.slow
def test_stop_while_paused_before_start_processes_nothing(monkeypatch, dictionary,
                                                          fake_ollama):
    """Pause, then Stop, before the first email: nothing is processed or sent to the AI.

    (Stopping during the AI phase is covered by test_runner.test_pause_and_stop.)
    """
    monkeypatch.setattr(runner_mod, "list_local_models", lambda h: ["llama3.1:latest"])
    monkeypatch.setattr(runner_mod, "make_client", lambda h: fake_ollama(delay=0.2))
    rec = Recorder()
    stop, pause = threading.Event(), threading.Event()
    pause.set()                                         # start paused...
    r = PipelineRunner([f"zz{i}qq.wplk@acme.com" for i in range(20)],
                       Settings(check_domains=False), rec.callbacks(), rec.post,
                       stop, pause, dictionary_store=_Store(dictionary))
    r.start()
    time.sleep(0.3)
    stop.set()                                          # ...then stop
    assert rec.done.wait(5)
    assert rec.stats.stopped
    assert rec.stats.processed == 0 and rec.rows == {}
    assert not any(row["reason"] == STOPPED_REASON for row in rec.rows.values())
