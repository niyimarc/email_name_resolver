"""Stage 2: local Ollama judgment of the splitter output.

The model is used strictly as a *judge and formatter*: it decides whether
the tokens found by the splitter plausibly form a real person's name and
how to capitalise and order them. It is never allowed to add names. As a
second line of defence, :func:`judge_name_with_ai` rejects any verdict
whose name parts do not literally appear in the email's local part.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, ValidationError

try:  # The ollama client is a required dependency, but never crash on import.
    import ollama
except ImportError:  # pragma: no cover - exercised only on broken installs
    ollama = None  # type: ignore[assignment]

try:  # httpx is installed by ollama; used only to classify network errors.
    import httpx
    _HTTPX_ERRORS: tuple = (httpx.HTTPError,)
except ImportError:  # pragma: no cover
    _HTTPX_ERRORS = ()

DEFAULT_MODEL = "llama3.1"
DEFAULT_HOST = "http://localhost:11434"
OLLAMA_TIMEOUT_SECONDS = 120
# Keep the model loaded between requests/runs so it isn't reloaded from disk
# (loading an 8B model can take 10-30 s). Ollama's own default is 5 minutes.
OLLAMA_KEEP_ALIVE = "30m"
# Cap on generated tokens: a verdict needs ~40-60; this stops runaway output.
OLLAMA_NUM_PREDICT = 160
UNAVAILABLE_REASON = "ollama unavailable"

SYSTEM_PROMPT = (
    "You are a strict name judge for email addresses.\n"
    "You never invent names; you only judge and format tokens; if unsure, "
    "set is_real_name=false and final_name=null.\n"
    "Rules:\n"
    "1. You receive the email local part (raw_local), its domain, the tokens "
    "a rule-based splitter extracted, and an optional hint.\n"
    "2. Decide whether the tokens plausibly form a real human's name. Role or "
    "generic mailboxes (info, sales, admin, support, noreply, contact, team, "
    "hello, billing), nicknames, handles, brand names and random strings are "
    "NOT real names.\n"
    "3. Only use letters that appear in raw_local. Never add a first name, "
    "surname, or letters that are not there. Never expand an initial into a "
    "full name: for hint 'initial+surname' (e.g. 'jdoe') the best you may "
    "produce is ['J', 'Doe'] and final_name 'J Doe'.\n"
    "4. Format: capitalise each part (e.g. 'Mary'); keep surname particles "
    "(van, der, de, da, bin, al, von, la, le) lowercase when they are not the "
    "first part, and list each particle as its own element of name_parts.\n"
    "5. final_name is name_parts joined with single spaces. If "
    "is_real_name is false, final_name must be null.\n"
    "6. confidence is an integer 0-100 for how sure you are that this is a "
    "real person's name. reason is at most 12 words.\n"
    "7. An optional 'dictionary' field gives evidence from US name lists: "
    "'tags' says which tokens are known first names, surnames or role words "
    "(info, sales, admin...), and 'suggested_split' proposes how a glued token "
    "may split (e.g. 'sarahjones' -> ['sarah', 'jones']). Use it as evidence, "
    "not proof: a token missing from the lists can still be a real non-US "
    "name, and common words can appear in the surname list.\n"
    "Respond only with JSON matching the given schema."
)


class NameVerdict(BaseModel):
    """Structured verdict returned by the AI judge."""

    is_real_name: bool
    confidence: int = Field(ge=0, le=100)  # 0-100
    reason: str
    final_name: Optional[str]  # null if not credible
    name_parts: List[str]      # ordered parts, e.g. ["Mary","van","der","Berg"]


def unavailable_verdict() -> NameVerdict:
    """Return the verdict used whenever Ollama cannot be reached.

    Returns:
        A non-credible verdict whose reason is ``"ollama unavailable"``.
    """
    return NameVerdict(is_real_name=False, confidence=0,
                       reason=UNAVAILABLE_REASON, final_name=None, name_parts=[])


def build_user_prompt(info: Dict[str, Any]) -> str:
    """Build the user message passed to the model.

    Args:
        info: A dict returned by ``split_email_local``.

    Returns:
        A JSON-formatted prompt with ``raw_local``, ``domain``, ``tokens``
        and ``hint`` (plus ``dictionary`` evidence when present).
    """
    payload = {
        "raw_local": info.get("raw_local", ""),
        "domain": info.get("domain", ""),
        "tokens": list(info.get("tokens", [])),
        "hint": info.get("hint"),
    }
    if info.get("dictionary"):
        payload["dictionary"] = info["dictionary"]
    return (
        "Judge this email local part. Use only the given tokens/letters.\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def _letters(text: str) -> str:
    """Return only the lowercase letters of ``text``."""
    return "".join(ch for ch in text.lower() if ch.isalpha())


def sanitize_verdict(verdict: NameVerdict, info: Dict[str, Any]) -> NameVerdict:
    """Enforce the no-invention rule and internal consistency on a verdict.

    Every name part must appear (letters only, case-insensitive) inside the
    email's local part; otherwise the verdict is downgraded to not credible.

    Args:
        verdict: The verdict produced by the model.
        info: The splitter output the verdict was based on.

    Returns:
        A consistent, possibly downgraded, verdict.
    """
    local_letters = _letters(str(info.get("raw_local", "")).split("+", 1)[0])
    parts = [p.strip() for p in verdict.name_parts if p and p.strip()]
    if not parts and verdict.final_name:
        parts = verdict.final_name.split()

    invented = [p for p in parts if not _letters(p) or _letters(p) not in local_letters]
    if invented:
        return NameVerdict(
            is_real_name=False,
            confidence=0,
            reason="rejected: AI output contained text not present in the email ("
                   + ", ".join(invented) + ")",
            final_name=None,
            name_parts=[],
        )

    if not verdict.is_real_name or not parts:
        # NOTE: Keep the parts the model formatted (they are verified above)
        # but never present a final name for a non-credible verdict.
        return verdict.model_copy(update={"is_real_name": False,
                                          "final_name": None,
                                          "name_parts": parts})
    return verdict.model_copy(update={"final_name": " ".join(parts),
                                      "name_parts": parts})


def make_client(host: str = DEFAULT_HOST) -> Any:
    """Create an Ollama client bound to ``host`` with the 120 s timeout.

    Args:
        host: Base URL of the Ollama server.

    Returns:
        An ``ollama.Client`` instance, or None if the package is missing.
    """
    if ollama is None:
        return None
    return ollama.Client(host=host, timeout=OLLAMA_TIMEOUT_SECONDS)


def judge_name_with_ai(info: Dict[str, Any], model: str = DEFAULT_MODEL, *,
                       host: str = DEFAULT_HOST,
                       client: Any = None) -> NameVerdict:
    """Ask the local Ollama model to judge and format the splitter tokens.

    Uses ``chat(..., format=NameVerdict.model_json_schema(),
    options={"temperature": 0})``.

    NOTE: The spec names ``ollama.chat``. The module-level helper cannot take
    a custom base URL or timeout, so an ``ollama.Client`` (same ``chat``
    signature) is used, configured with the user's base URL and a 120 s
    timeout. Pass ``client`` to reuse one connection across many emails.

    Args:
        info: A dict returned by ``split_email_local``.
        model: Ollama model name, e.g. ``"llama3.1"``.
        host: Ollama base URL, used when ``client`` is not given.
        client: Optional pre-built client (anything with a ``chat`` method).

    Returns:
        A sanitised :class:`NameVerdict`. On connection problems the verdict
        has ``is_real_name=False`` and ``reason="ollama unavailable"``.
    """
    if not info.get("valid", True):
        return NameVerdict(is_real_name=False, confidence=0,
                           reason="invalid email", final_name=None, name_parts=[])
    if not info.get("tokens") and info.get("hint") is None:
        # Nothing for the model to judge; never ask it to make something up.
        return NameVerdict(is_real_name=False, confidence=0,
                           reason="no name-like tokens found", final_name=None,
                           name_parts=[])

    if client is None:
        client = make_client(host)
    if client is None:
        return unavailable_verdict()

    try:
        response = client.chat(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(info)},
            ],
            format=NameVerdict.model_json_schema(),
            options={"temperature": 0, "num_predict": OLLAMA_NUM_PREDICT},
            keep_alive=OLLAMA_KEEP_ALIVE,
        )
    except (ConnectionError, TimeoutError, OSError) + _HTTPX_ERRORS:
        return unavailable_verdict()
    except Exception as exc:  # ollama.ResponseError and anything unexpected
        status = getattr(exc, "status_code", None)
        detail = getattr(exc, "error", None) or str(exc)
        if status == 404:
            return NameVerdict(is_real_name=False, confidence=0,
                               reason=f"ollama model not found: {model}",
                               final_name=None, name_parts=[])
        return NameVerdict(is_real_name=False, confidence=0,
                           reason=f"ollama error: {detail}"[:300],
                           final_name=None, name_parts=[])

    try:
        content = response["message"]["content"]
    except (KeyError, TypeError):
        content = getattr(getattr(response, "message", None), "content", "")

    try:
        data = json.loads(content or "")
        if isinstance(data, dict) and isinstance(data.get("confidence"), (int, float)):
            # NOTE: Clamp instead of rejecting a slightly out-of-range score.
            data["confidence"] = int(max(0, min(100, round(data["confidence"]))))
        verdict = NameVerdict.model_validate(data)
    except (ValueError, ValidationError):
        return NameVerdict(is_real_name=False, confidence=0,
                           reason="invalid AI response", final_name=None,
                           name_parts=[])
    return sanitize_verdict(verdict, info)


def check_ollama_available(host: str = DEFAULT_HOST, timeout: float = 3.0) -> bool:
    """Quickly check whether an Ollama server answers at ``host``.

    Called once from the background worker before a run so a dead server
    does not cost one failed request per email.

    Args:
        host: Ollama base URL.
        timeout: Seconds to wait for the server.

    Returns:
        True if ``GET <host>/api/tags`` succeeds.
    """
    import requests  # local import keeps this module importable without it

    try:
        resp = requests.get(host.rstrip("/") + "/api/tags", timeout=timeout)
        return resp.ok
    except requests.RequestException:
        return False


def list_local_models(host: str = DEFAULT_HOST, timeout: float = 3.0) -> Optional[List[str]]:
    """Return the model names pulled into the local Ollama server.

    Args:
        host: Ollama base URL.
        timeout: Seconds to wait for the server.

    Returns:
        A list of model names (e.g. ``["llama3.1:latest"]``) or None when
        the server cannot be reached.
    """
    import requests

    try:
        resp = requests.get(host.rstrip("/") + "/api/tags", timeout=timeout)
        resp.raise_for_status()
        return [m.get("name", "") for m in resp.json().get("models", [])]
    except (requests.RequestException, ValueError):
        return None


def model_is_pulled(model: str, available: List[str]) -> bool:
    """Tell whether ``model`` is among ``available`` (``:latest`` aware).

    Args:
        model: The configured model name, e.g. ``"llama3.1"``.
        available: Names from :func:`list_local_models`.

    Returns:
        True if the model (or ``model:latest``) is present.
    """
    wanted = {model, model + ":latest"} if ":" not in model else {model}
    return any(name in wanted for name in available)
