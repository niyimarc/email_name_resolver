"""Stage 3 (optional): resolve a company website from an email domain."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Dict, Optional
from urllib.parse import urlsplit

import requests

WEBSITE_TIMEOUT_SECONDS = 3

FREE_PROVIDERS = frozenset({
    "gmail.com", "googlemail.com", "yahoo.com", "outlook.com", "hotmail.com",
    "live.com", "icloud.com", "me.com", "proton.me", "protonmail.com",
    "aol.com", "gmx.com", "mail.com", "yandex.com", "zoho.com",
})

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}

_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)


@dataclass(frozen=True)
class WebsiteResult:
    """A resolved website and whether a live request confirmed it."""

    url: Optional[str]
    verified: bool


def is_free_provider(domain: str) -> bool:
    """Tell whether ``domain`` is a known personal/free mail provider.

    Args:
        domain: An email domain such as ``"gmail.com"``.

    Returns:
        True for providers in :data:`FREE_PROVIDERS`.
    """
    return (domain or "").strip().lower().rstrip(".") in FREE_PROVIDERS


def _host_matches(host: str, domain: str) -> bool:
    """Return True if ``host`` equals ``domain`` or is a subdomain of it.

    A leading ``www.`` on either side is ignored.
    """
    host = host.lower().rstrip(".")
    domain = domain.lower().rstrip(".")
    if domain.startswith("www."):
        domain = domain[4:]
    return host == domain or host.endswith("." + domain)


def _resolve_uncached(domain: str, session: Optional[requests.Session]) -> WebsiteResult:
    """Do the actual network check for one custom domain.

    Args:
        domain: A normalised, non-free domain.
        session: Optional ``requests.Session`` to reuse connections.

    Returns:
        A :class:`WebsiteResult`. Failed or off-domain checks fall back to
        ``https://<domain>`` with ``verified=False``.
    """
    guess = f"https://{domain}"
    if not _DOMAIN_RE.match(domain):
        # NOTE: Not a public hostname (e.g. "localhost" or an IP); there is
        # no sensible website to guess, so return None.
        return WebsiteResult(None, False)

    getter = session.get if session is not None else requests.get
    try:
        resp = getter(guess, timeout=WEBSITE_TIMEOUT_SECONDS, allow_redirects=True,
                      headers=BROWSER_HEADERS, stream=True)
        try:
            final = urlsplit(resp.url)
        finally:
            resp.close()  # stream=True: do not download the page body
    except requests.RequestException:
        return WebsiteResult(guess, False)

    host = final.hostname or ""
    if final.scheme in ("http", "https") and host and _host_matches(host, domain):
        port = f":{final.port}" if final.port else ""
        return WebsiteResult(f"{final.scheme}://{host}{port}", True)
    return WebsiteResult(guess, False)


def resolve_company_website(domain: str,
                            cache: Optional[Dict[str, WebsiteResult]] = None,
                            session: Optional[requests.Session] = None) -> Optional[str]:
    """Resolve the company website for an email domain.

    Free/personal providers return None. For custom domains,
    ``https://<domain>`` is requested (3 s timeout, redirects followed,
    browser-like User-Agent). If the final host is the domain or one of its
    subdomains, its ``scheme://host`` is returned; otherwise the best guess
    ``https://<domain>`` is returned (unverified).

    Args:
        domain: The email domain.
        cache: Optional per-run dict used as an in-memory cache.
        session: Optional ``requests.Session``.

    Returns:
        The website URL, or None for free providers and invalid domains.
    """
    return resolve_company_website_detailed(domain, cache, session).url


def resolve_company_website_detailed(domain: str,
                                     cache: Optional[Dict[str, WebsiteResult]] = None,
                                     session: Optional[requests.Session] = None
                                     ) -> WebsiteResult:
    """Like :func:`resolve_company_website` but also reports verification.

    Args:
        domain: The email domain.
        cache: Optional per-run dict used as an in-memory cache.
        session: Optional ``requests.Session``.

    Returns:
        A :class:`WebsiteResult` with ``url`` and ``verified``.
    """
    key = (domain or "").strip().lower().rstrip(".")
    if not key or key in FREE_PROVIDERS:
        return WebsiteResult(None, False)
    if cache is not None and key in cache:
        return cache[key]
    result = _resolve_uncached(key, session)
    if cache is not None:
        cache[key] = result
    return result


class WebsiteResolver:
    """Per-run website resolver holding its own cache and HTTP session.

    Create one per Analyze run so the cache lives exactly as long as the run.
    """

    def __init__(self) -> None:
        """Create an empty cache and a reusable HTTP session."""
        self._cache: Dict[str, WebsiteResult] = {}
        self._lock = threading.Lock()
        self._session = requests.Session()

    def resolve(self, domain: str) -> WebsiteResult:
        """Resolve ``domain`` using this run's cache.

        Args:
            domain: The email domain.

        Returns:
            A :class:`WebsiteResult`.
        """
        with self._lock:
            return resolve_company_website_detailed(domain, self._cache, self._session)

    def close(self) -> None:
        """Release the HTTP session."""
        self._session.close()
