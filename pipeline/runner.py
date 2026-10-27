"""Background pipeline runner: dictionary first, AI last, everything streamed.

A run has two phases so the slow AI never delays the fast work:

1. **Fast phase** (milliseconds per email): split every email, resolve what
   the dictionary can, flag invalid addresses, and emit a row for *every*
   email in input order. Emails that need the AI get a "waiting for AI"
   placeholder row.
2. **AI phase**: only the remaining emails go to Ollama, optionally several
   at once, and each result updates its placeholder row in place.

Domain tasks run in a separate thread pool at the same time, one per unique
domain, and update rows in place: mail-provider detection and domain
verification (DNS MX lookup, optional) and the company-website lookup
(optional). Free providers and disposable domains are classified instantly
without any network request.

The runner is UI-agnostic: every callback is delivered through ``post`` (the
app passes a function that schedules on the Kivy main thread).

Pause and Stop are honoured between emails; requests already sent to Ollama
finish first.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from storage.session import normalize_row
from storage.settings import Settings

from .ai_judge import (
    UNAVAILABLE_REASON,
    NameVerdict,
    judge_name_with_ai,
    list_local_models,
    make_client,
    model_is_pulled,
)
from .email_extract import email_key
from .mail_domain import (
    STATUS_CHECKING,
    STATUS_INVALID,
    STATUS_NO_DOMAIN,
    STATUS_NOT_CHECKED,
    STATUS_ROLE,
    STATUS_VALID,
    classify_from_mx,
    classify_offline,
    load_disposable_domains,
    lookup_mx,
    offline_provider,
)
from .name_dictionary import DictionaryStore, NameDictionary, format_name_part
from .splitter import format_tokens_rule_based, split_email_local
from .website import WebsiteResult, resolve_company_website_detailed

BANNER_OLLAMA_DOWN = "Ollama not reachable — AI judgment disabled. Rule-based results only."
PENDING_REASON = "waiting for AI..."
STOPPED_REASON = "not analysed (stopped). Use Re-analyze unresolved"
WEBSITE_WORKERS = 8
MAX_AI_PARALLEL = 4

Poster = Callable[..., None]


def rule_based_verdict(info: Dict[str, Any], reason: str) -> NameVerdict:
    """Build a non-AI verdict that still shows the rule-based split.

    NOTE: Without the AI judge nothing confirms the tokens are a real name,
    so ``is_real_name`` stays false and ``final_name`` stays empty (the user
    may fill it in). ``name_parts`` shows the splitter's formatted tokens.

    Args:
        info: Splitter output.
        reason: Why AI judgment was skipped.

    Returns:
        A :class:`NameVerdict`.
    """
    parts = format_tokens_rule_based(info)
    split = (info.get("dictionary") or {}).get("suggested_split")
    if split:
        parts = [format_name_part(p) for p in split]
    return NameVerdict(is_real_name=False, confidence=0, reason=reason,
                       final_name=None, name_parts=parts)


def verdict_fields(verdict: NameVerdict) -> Dict[str, Any]:
    """Row fields that come from a verdict."""
    return {
        "is_real_name": verdict.is_real_name,
        "confidence": verdict.confidence,
        "final_name": verdict.final_name or "",
        "name_parts": list(verdict.name_parts),
        "reason": verdict.reason,
    }


def build_row(email: str, verdict: NameVerdict,
              website: Optional[WebsiteResult] = None) -> Dict[str, Any]:
    """Assemble a table row from pipeline results.

    Args:
        email: The email as entered.
        verdict: The (possibly rule-based) verdict.
        website: Optional website lookup result.

    Returns:
        A row dict with every column the table and exporter need.
    """
    return normalize_row({
        "email": email,
        **verdict_fields(verdict),
        "company_website": (website.url if website and website.url else ""),
        "website_verified": bool(website and website.verified),
    })


@dataclass
class RunStats:
    """Counters reported when a run ends."""

    total: int = 0
    processed: int = 0
    dictionary_hits: int = 0
    ai_total: int = 0
    ai_done: int = 0
    stopped: bool = False
    phase_seconds: Dict[str, float] = field(default_factory=dict)


@dataclass
class RunCallbacks:
    """Callbacks the runner invokes (always through ``post``).

    * ``on_row(row)``: a new row (fast phase, input order).
    * ``on_update(key, changes)``: update the row whose ``email_key`` is key.
    * ``on_progress(processed, total, ai_done, ai_total)``.
    * ``on_phase(name, count)``: ``"fast"`` or ``"ai"`` phase started.
    * ``on_banner(message)``: at most once per run.
    * ``on_done(stats)``.
    """

    on_row: Callable[[Dict[str, Any]], None]
    on_update: Callable[[str, Dict[str, Any]], None]
    on_progress: Callable[[int, int, int, int], None]
    on_phase: Callable[[str, int], None]
    on_banner: Callable[[str], None]
    on_done: Callable[[RunStats], None]


class PipelineRunner(threading.Thread):
    """Runs the two-phase pipeline over a list of emails in a background thread."""

    def __init__(self, emails: List[str], settings: Settings, callbacks: RunCallbacks,
                 post: Poster, stop_event: threading.Event,
                 pause_event: threading.Event,
                 dictionary_store: Optional[DictionaryStore] = None) -> None:
        """Store the work list, settings and callbacks.

        Args:
            emails: Deduplicated emails to process, in order.
            settings: A snapshot of the current settings.
            callbacks: Where results go.
            post: ``post(fn, *args)`` delivers a callback (e.g. on the UI thread).
            stop_event: Set by the UI to stop between emails.
            pause_event: Set by the UI to pause between emails.
            dictionary_store: Name dictionary source (used when enabled).
        """
        super().__init__(name="pipeline-runner", daemon=True)
        self.emails = emails
        self.settings = settings
        self.cb = callbacks
        self._post_fn = post
        self.stop_event = stop_event
        self.pause_event = pause_event
        self._store = dictionary_store
        self._dictionary: Optional[NameDictionary] = None
        self._client: Any = None
        self._ai_enabled = True
        self._skip_reason = ""
        self._banner_sent = False
        self._lock = threading.Lock()
        self.stats = RunStats(total=len(emails))
        self._disposable: Set[str] = load_disposable_domains()
        self._dns_domains: Dict[str, List[str]] = {}   # domain -> email keys
        self._role_keys: Set[str] = set()

    # -- plumbing -------------------------------------------------------------

    def _post(self, fn: Callable, *args: Any) -> None:
        """Deliver a callback through the poster."""
        self._post_fn(fn, *args)

    def _progress(self) -> None:
        """Report progress counters."""
        s = self.stats
        self._post(self.cb.on_progress, s.processed, s.total, s.ai_done, s.ai_total)

    def wait_if_paused(self) -> bool:
        """Block while paused. Returns False if the run was stopped."""
        while self.pause_event.is_set() and not self.stop_event.is_set():
            time.sleep(0.15)
        return not self.stop_event.is_set()

    def _disable_ai(self, banner: str, reason: str) -> None:
        """Turn off AI judgment for the rest of the run and show one banner."""
        with self._lock:
            self._ai_enabled = False
            self._skip_reason = reason
            if self._banner_sent:
                return
            self._banner_sent = True
        self._post(self.cb.on_banner, banner)

    def _preflight(self) -> None:
        """Check once that Ollama is up and the model is pulled."""
        models = list_local_models(self.settings.base_url)
        if models is None:
            self._disable_ai(BANNER_OLLAMA_DOWN, f"{UNAVAILABLE_REASON}; rule-based split only")
            return
        if not model_is_pulled(self.settings.model, models):
            model = self.settings.model
            self._disable_ai(
                f"Ollama model '{model}' not found (run: ollama pull {model}). "
                "AI judgment disabled. Rule-based results only.",
                f"ollama model not found: {model}; rule-based split only")
            return
        self._client = make_client(self.settings.base_url)

    # -- domains ------------------------------------------------------------------------

    def _is_role(self, info: Dict[str, Any]) -> bool:
        """True when every token is a role word (info, sales, noreply...)."""
        tokens = info.get("tokens") or []
        return bool(tokens) and self._dictionary is not None and all(
            self._dictionary.is_role(t) for t in tokens)

    def initial_domain_fields(self, email: str, info: Dict[str, Any]) -> Dict[str, Any]:
        """Provider/status known without network; queue a DNS lookup otherwise."""
        if not info.get("valid"):
            return {"mail_provider": "", "email_status": STATUS_INVALID}
        domain = info["domain"]
        key = email_key(email)
        role = self._is_role(info)
        if role:
            self._role_keys.add(key)
        offline = classify_offline(domain, self._disposable)
        if offline is not None:
            status = STATUS_ROLE if (role and offline.status == STATUS_VALID) else offline.status
            return {"mail_provider": offline.provider, "email_status": status}
        if self.settings.check_domains:
            self._dns_domains.setdefault(domain, []).append(key)
            return {"mail_provider": STATUS_CHECKING, "email_status": STATUS_CHECKING}
        return {"mail_provider": offline_provider(domain),
                "email_status": STATUS_ROLE if role else STATUS_NOT_CHECKED}

    # -- phases ---------------------------------------------------------------------

    def fast_phase(self) -> List[Tuple[str, Dict[str, Any]]]:
        """Emit a row for every email; return the ones that need the AI.

        Returns:
            ``[(email, info_with_dictionary_evidence), ...]`` in input order.
        """
        pending: List[Tuple[str, Dict[str, Any]]] = []
        for email in self.emails:
            if not self.wait_if_paused():
                break
            info = split_email_local(email)
            if not info["valid"]:
                verdict = NameVerdict(is_real_name=False, confidence=0,
                                      reason="invalid email", final_name=None,
                                      name_parts=[])
            else:
                verdict = None
                if self._dictionary is not None:
                    verdict = self._dictionary.resolve(info)
                    if verdict is not None:
                        self.stats.dictionary_hits += 1
                    else:
                        info = dict(info, dictionary=self._dictionary.evidence(info))
                if verdict is None and self._ai_enabled:
                    row = build_row(email, rule_based_verdict(info, PENDING_REASON))
                    row.update(self.initial_domain_fields(email, info))
                    row["_pending"] = True
                    pending.append((email, info))
                    self._post(self.cb.on_row, row)
                    continue
                if verdict is None:
                    verdict = rule_based_verdict(info, self._skip_reason)
            self.stats.processed += 1
            row = build_row(email, verdict)
            row.update(self.initial_domain_fields(email, info))
            self._post(self.cb.on_row, row)
            self._progress()
        return pending

    def _judge(self, email: str, info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """AI-judge one email (runs in the AI pool). None if stopped first."""
        if not self.wait_if_paused():
            return None
        with self._lock:
            enabled, skip_reason = self._ai_enabled, self._skip_reason
        if not enabled:
            return verdict_fields(rule_based_verdict(info, skip_reason))
        verdict = judge_name_with_ai(info, self.settings.model,
                                     host=self.settings.base_url, client=self._client)
        if verdict.reason == UNAVAILABLE_REASON:
            # Ollama went away mid-run: banner once, rule-based for the rest.
            self._disable_ai(BANNER_OLLAMA_DOWN, f"{UNAVAILABLE_REASON}; rule-based split only")
            verdict = rule_based_verdict(info, f"{UNAVAILABLE_REASON}; rule-based split only")
        return verdict_fields(verdict)

    def ai_phase(self, pending: List[Tuple[str, Dict[str, Any]]]) -> None:
        """Send the remaining emails to the AI and update their rows."""
        self.stats.ai_total = len(pending)
        if not pending:
            return
        self._post(self.cb.on_phase, "ai", len(pending))
        self._progress()
        workers = max(1, min(MAX_AI_PARALLEL, int(self.settings.ai_parallel or 1)))
        unfinished = {email_key(email): (email, info) for email, info in pending}
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ai") as pool:
            futures = {pool.submit(self._judge, email, info): email for email, info in pending}
            for future in as_completed(futures):
                email = futures[future]
                try:
                    changes = future.result()
                except Exception as exc:  # never let one email kill the run
                    changes = verdict_fields(NameVerdict(
                        is_real_name=False, confidence=0, reason=f"error: {exc}"[:300],
                        final_name=None, name_parts=[]))
                if changes is None:  # stopped before it started
                    continue
                key = email_key(email)
                unfinished.pop(key, None)
                self.stats.ai_done += 1
                self.stats.processed += 1
                self._post(self.cb.on_update, key, changes)
                self._progress()
        # Rows never sent to the AI because the user pressed Stop.
        for key, (_email, info) in unfinished.items():
            self._post(self.cb.on_update, key,
                       verdict_fields(rule_based_verdict(info, STOPPED_REASON)))

    def start_domain_tasks(self) -> Optional[ThreadPoolExecutor]:
        """Run DNS checks and website lookups in parallel, one task per domain."""
        web_domains: Dict[str, List[str]] = {}
        if self.settings.resolve_website:
            for email in self.emails:
                if "@" in email:
                    domain = email.rsplit("@", 1)[1].strip().lower()
                    web_domains.setdefault(domain, []).append(email_key(email))
        domains = set(web_domains) | set(self._dns_domains)
        if not domains:
            return None
        pool = ThreadPoolExecutor(max_workers=WEBSITE_WORKERS, thread_name_prefix="domain")

        def task(domain: str) -> None:
            if self.stop_event.is_set():
                return
            exists = True
            keys = self._dns_domains.get(domain)
            if keys:
                info = classify_from_mx(lookup_mx(domain, self.settings.dns_service))
                exists = info.status != STATUS_NO_DOMAIN
                for key in keys:
                    status = info.status
                    if status == STATUS_VALID and key in self._role_keys:
                        status = STATUS_ROLE
                    self._post(self.cb.on_update, key,
                               {"mail_provider": info.provider, "email_status": status})
            if domain in web_domains and exists and not self.stop_event.is_set():
                result = resolve_company_website_detailed(domain)
                if result.url:
                    for key in web_domains[domain]:
                        self._post(self.cb.on_update, key, {
                            "company_website": result.url,
                            "website_verified": result.verified})

        for domain in sorted(domains):
            pool.submit(task, domain)
        return pool

    # -- main -------------------------------------------------------------------------

    def run(self) -> None:
        """Run both phases and report stats."""
        web_pool: Optional[ThreadPoolExecutor] = None
        try:
            started = time.monotonic()
            if self.settings.use_dictionary and self._store is not None:
                try:
                    self._dictionary = self._store.get()
                except Exception:  # missing/corrupt data: run without it
                    self._dictionary = None
            self._preflight()
            self._post(self.cb.on_phase, "fast", len(self.emails))
            pending = self.fast_phase()
            self.stats.phase_seconds["fast"] = time.monotonic() - started
            # NOTE: Started after the fast phase so every row already exists
            # when its website result arrives (the fast phase takes only ms).
            web_pool = self.start_domain_tasks()
            ai_started = time.monotonic()
            if not self.stop_event.is_set():
                self.ai_phase(pending)
            else:
                for email, info in pending:
                    self._post(self.cb.on_update, email_key(email),
                               verdict_fields(rule_based_verdict(info, STOPPED_REASON)))
            self.stats.phase_seconds["ai"] = time.monotonic() - ai_started
        finally:
            if web_pool is not None:
                web_pool.shutdown(wait=not self.stop_event.is_set(), cancel_futures=True)
            self.stats.stopped = self.stop_event.is_set() and self.stats.processed < self.stats.total
            self._post(self.cb.on_done, self.stats)
