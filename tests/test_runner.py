"""Tests for the two-phase pipeline runner (no Kivy, fake Ollama)."""

import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pipeline.runner as runner_mod  # noqa: E402
from pipeline.email_extract import email_key  # noqa: E402
from pipeline.name_dictionary import NameDictionary  # noqa: E402
from pipeline.runner import (  # noqa: E402
    PENDING_REASON,
    STOPPED_REASON,
    PipelineRunner,
    RunCallbacks,
)
from storage.settings import Settings  # noqa: E402

AI_DELAY = 0.2
EMAILS = [
    "john.doe@acme.com",            # dictionary
    "coolguy123@gmail.com",         # AI
    "info@acme.com",                # dictionary (role)
    "oluwaseun.adeyemi@x.com",      # AI
    "bad",                          # invalid
    "kofi.mensah@x.com",            # AI (mensah not a common US surname)
    "zxqv.wplk@x.com",              # AI
]


class FakeOllama:
    """Answers like a model, slowly, and records concurrency."""

    def __init__(self):
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.calls = []

    def chat(self, **kwargs):
        payload = json.loads(kwargs["messages"][1]["content"].split("\n", 1)[1])
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.calls.append(payload["raw_local"])
        time.sleep(AI_DELAY)
        with self.lock:
            self.active -= 1
        real = payload["raw_local"] in ("oluwaseun.adeyemi", "kofi.mensah")
        parts = [t.capitalize() for t in payload["tokens"]] if real else []
        return {"message": {"content": json.dumps({
            "is_real_name": real, "confidence": 80 if real else 10, "reason": "AI judged",
            "final_name": " ".join(parts) if real else None, "name_parts": parts})}}


class Recorder:
    """Collects callbacks in order (called directly from runner threads)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.events = []
        self.rows = {}
        self.done = threading.Event()
        self.stats = None

    def post(self, fn, *args):
        with self.lock:
            fn(*args)

    def callbacks(self):
        def on_row(row):
            self.events.append(("row", row["email"], time.monotonic()))
            self.rows[email_key(row["email"])] = dict(row)

        def on_update(key, changes):
            self.events.append(("update", key, time.monotonic()))
            self.rows[key].update(changes)

        def on_done(stats):
            self.stats = stats
            self.done.set()

        return RunCallbacks(on_row=on_row, on_update=on_update,
                            on_progress=lambda *a: None, on_phase=lambda *a: None,
                            on_banner=lambda m: self.events.append(("banner", m, 0)),
                            on_done=on_done)


@pytest.fixture(scope="module")
def dictionary():
    return NameDictionary.load()


class _Store:
    def __init__(self, d):
        self.d = d

    def get(self):
        return self.d


def run(dictionary, monkeypatch, emails=EMAILS, parallel=1, ollama_up=True,
        control=None):
    fake = FakeOllama()
    monkeypatch.setattr(runner_mod, "list_local_models",
                        lambda host: ["llama3.1:latest"] if ollama_up else None)
    monkeypatch.setattr(runner_mod, "make_client", lambda host: fake)
    rec = Recorder()
    stop, pause = threading.Event(), threading.Event()
    r = PipelineRunner(list(emails), Settings(ai_parallel=parallel, check_domains=False),
                       rec.callbacks(),
                       rec.post, stop, pause, dictionary_store=_Store(dictionary))
    started = time.monotonic()
    r.start()
    if control:
        control(stop, pause)
    assert rec.done.wait(10)
    return rec, fake, time.monotonic() - started


def test_dictionary_rows_first_in_input_order_ai_last(dictionary, monkeypatch):
    rec, fake, _ = run(dictionary, monkeypatch)
    row_events = [e for e in rec.events if e[0] == "row"]
    # every email gets a row immediately, in input order
    assert [e[1] for e in row_events] == EMAILS
    # all rows exist before the first AI result arrives
    first_update = min(e[2] for e in rec.events if e[0] == "update")
    assert max(e[2] for e in row_events) < first_update
    # only the 4 uncertain emails reach the AI
    assert sorted(fake.calls) == sorted(["coolguy123", "oluwaseun.adeyemi",
                                         "kofi.mensah", "zxqv.wplk"])
    rows = rec.rows
    assert rows["john.doe@acme.com"]["final_name"] == "John Doe"
    assert rows["oluwaseun.adeyemi@x.com"]["final_name"] == "Oluwaseun Adeyemi"
    assert rows["coolguy123@gmail.com"]["reason"] == "AI judged"
    assert rows["bad"]["reason"] == "invalid email"
    assert rec.stats.dictionary_hits == 2 and rec.stats.ai_done == 4
    assert rec.stats.processed == rec.stats.total == len(EMAILS)


@pytest.mark.slow
def test_parallel_ai_is_faster(dictionary, monkeypatch):
    _, fake1, t1 = run(dictionary, monkeypatch, parallel=1)
    _, fake4, t4 = run(dictionary, monkeypatch, parallel=4)
    assert fake1.max_active == 1
    assert fake4.max_active > 1
    assert t4 < t1 * 0.6


def test_ollama_down_skips_ai_phase(dictionary, monkeypatch):
    rec, fake, _ = run(dictionary, monkeypatch, ollama_up=False)
    assert fake.calls == []
    assert all(PENDING_REASON not in r["reason"] for r in rec.rows.values())
    assert any(e[0] == "banner" for e in rec.events)


@pytest.mark.slow
def test_pause_and_stop(dictionary, monkeypatch):
    emails = [f"zz{i}qq.wplk@x.com" for i in range(10)]

    def control(stop, pause):
        time.sleep(AI_DELAY * 1.5)   # let ~1-2 AI calls happen
        pause.set()
        time.sleep(AI_DELAY * 3)     # paused: no new calls should start
        stop.set()

    rec, fake, _ = run(dictionary, monkeypatch, emails=emails, control=control)
    assert len(fake.calls) <= 3
    assert rec.stats.stopped
    stopped_rows = [r for r in rec.rows.values() if r["reason"] == STOPPED_REASON]
    assert len(stopped_rows) == len(emails) - rec.stats.ai_done
