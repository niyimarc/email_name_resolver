"""Shared pytest fixtures for the Email Name Resolver test suite.

Safety rules applied to EVERY test (autouse fixtures):

* ``isolated_appdata``: %APPDATA% points at a temporary folder, so tests never
  read or overwrite your real settings, session or name lists.
* ``no_network``: real network connections are blocked. Tests that exercise
  Ollama, DNS or website lookups use the fakes below instead, so the suite is
  fast, offline and gives the same result on every machine.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from typing import Any, Dict, List, Optional

import pytest

from pipeline.name_dictionary import NameDictionary


# --------------------------------------------------------------------------- safety

@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path, monkeypatch):
    """Redirect %APPDATA% (and HOME) to a temp folder for every test."""
    appdata = tmp_path / "appdata"
    appdata.mkdir()
    monkeypatch.setenv("APPDATA", str(appdata))
    return appdata


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fail loudly if any test tries to open a real network connection."""
    def blocked(*_args, **_kwargs):
        raise OSError("network access is disabled in tests (use a fake)")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


# --------------------------------------------------------------------------- data

@pytest.fixture(scope="session")
def dictionary() -> NameDictionary:
    """The bundled US name lists (loaded once for the whole run)."""
    return NameDictionary.load()


@pytest.fixture
def sample_rows() -> List[Dict[str, Any]]:
    """A small results table covering every kind of row."""
    return [
        {"email": "john.doe@acme.com", "is_real_name": True, "confidence": 95,
         "final_name": "John Doe", "name_parts": ["John", "Doe"],
         "reason": "dictionary: known first name", "company_website": "https://www.acme.com",
         "website_verified": True, "mail_provider": "Google Workspace", "email_status": "valid"},
        {"email": "mary.van.der.berg@berg.nl", "is_real_name": True, "confidence": 90,
         "final_name": "Mary van der Berg", "name_parts": ["Mary", "van", "der", "Berg"],
         "reason": "dictionary", "company_website": "", "website_verified": False,
         "mail_provider": "Microsoft 365", "email_status": "valid"},
        {"email": "jane@gmail.com", "is_real_name": True, "confidence": 70,
         "final_name": "Jane", "name_parts": ["Jane"], "reason": "AI judged",
         "company_website": "", "website_verified": False,
         "mail_provider": "Gmail", "email_status": "valid"},
        {"email": "info@acme.com", "is_real_name": False, "confidence": 2,
         "final_name": "", "name_parts": [], "reason": "dictionary: generic mailbox (info)",
         "company_website": "", "website_verified": False,
         "mail_provider": "Google Workspace", "email_status": "role account"},
        {"email": "x@mailinator.com", "is_real_name": True, "confidence": 80,
         "final_name": "X Ray", "name_parts": ["X", "Ray"], "reason": "AI judged",
         "company_website": "", "website_verified": False,
         "mail_provider": "Other free provider", "email_status": "disposable"},
        {"email": "bad", "is_real_name": False, "confidence": 0, "final_name": "",
         "name_parts": [], "reason": "invalid email", "company_website": "",
         "website_verified": False, "mail_provider": "", "email_status": "invalid syntax"},
    ]


# --------------------------------------------------------------------------- fakes

class FakeOllamaClient:
    """Stands in for ``ollama.Client``: returns a scripted verdict per raw_local.

    ``answers`` maps raw_local -> verdict dict (or an Exception to raise).
    Unknown emails get a "not a name" verdict. Every call is recorded.
    """

    def __init__(self, answers: Optional[Dict[str, Any]] = None, delay: float = 0.0):
        self.answers = answers or {}
        self.delay = delay
        self.calls: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def chat(self, **kwargs):
        payload = json.loads(kwargs["messages"][1]["content"].split("\n", 1)[1])
        with self._lock:
            self.calls.append({**kwargs, "payload": payload})
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                time.sleep(self.delay)
            answer = self.answers.get(payload["raw_local"], {
                "is_real_name": False, "confidence": 10, "reason": "AI: not a name",
                "final_name": None, "name_parts": []})
            if isinstance(answer, BaseException):
                raise answer
            content = answer if isinstance(answer, str) else json.dumps(answer)
            return {"message": {"content": content}}
        finally:
            with self._lock:
                self.active -= 1


@pytest.fixture
def fake_ollama():
    """Factory: ``fake_ollama({"john.doe": {...verdict...}}, delay=0.1)``."""
    return FakeOllamaClient


class FakeResponse:
    """Minimal ``requests.Response`` stand-in."""

    def __init__(self, json_data=None, url: str = "", status: int = 200):
        self._json = json_data
        self.url = url
        self.status_code = status
        self.ok = 200 <= status < 300
        self.closed = False

    def json(self):
        if isinstance(self._json, Exception):
            raise self._json
        return self._json

    def raise_for_status(self):
        import requests
        if not self.ok:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def close(self):
        self.closed = True


@pytest.fixture
def fake_response():
    """Factory for :class:`FakeResponse`."""
    return FakeResponse
