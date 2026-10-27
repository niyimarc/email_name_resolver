"""Mail provider detection and domain-level email verification.

For each domain this answers two questions:

* **Who hosts the mailbox?** Free providers are known by domain (gmail.com is
  "Gmail"). For company domains the public **MX record** (the DNS entry that
  says which server receives the domain's mail) reveals the host:
  ``aspmx.l.google.com`` means Google Workspace, ``*.mail.protection.outlook.com``
  means Microsoft 365, and so on.
* **Can this address receive mail at all?** A domain that doesn't exist, or has
  no mail server, can't receive email. Disposable/throwaway domains and role
  mailboxes (info@, sales@) are flagged as risky.

Python's standard library cannot look up MX records, and the app's dependency
rules exclude DNS libraries, so lookups use **DNS-over-HTTPS** (Cloudflare or
Google public DNS) through ``requests``. Only domain names are sent, never the
email addresses.

NOTE: This verifies the *domain*, not the individual mailbox. Checking that a
mailbox exists needs an SMTP conversation with the mail server, which is
unreliable (Gmail, Yahoo, Microsoft 365 and catch-all domains do not reveal it)
and can get the checking IP blacklisted, so it is deliberately not done here.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

import requests

DNS_TIMEOUT_SECONDS = 3

DNS_SERVICES: Dict[str, str] = {
    "cloudflare": "https://cloudflare-dns.com/dns-query",
    "google": "https://dns.google/resolve",
}

# -- provider names --------------------------------------------------------------

GMAIL = "Gmail"
OUTLOOK = "Outlook / Hotmail"
YAHOO = "Yahoo"
ICLOUD = "iCloud"
AOL = "AOL"
PROTON = "Proton"
OTHER_FREE = "Other free provider"
GOOGLE_WORKSPACE = "Google Workspace"
MICROSOFT_365 = "Microsoft 365"
OTHER_BUSINESS = "Other business"
UNKNOWN = "Unknown"
NO_MAIL = "No mail server"

# Free/personal mailbox providers, by email domain.
FREE_PROVIDER_DOMAINS: Dict[str, str] = {}
for _label, _domains in {
    GMAIL: ["gmail.com", "googlemail.com"],
    OUTLOOK: ["outlook.com", "hotmail.com", "live.com", "msn.com", "passport.com",
              "hotmail.co.uk", "hotmail.fr", "hotmail.de", "hotmail.it", "hotmail.es",
              "live.co.uk", "live.fr", "live.de", "live.nl", "live.ca", "live.com.au",
              "outlook.fr", "outlook.de", "outlook.es", "outlook.it", "outlook.co.uk",
              "windowslive.com"],
    YAHOO: ["yahoo.com", "ymail.com", "rocketmail.com", "yahoo.co.uk", "yahoo.fr",
            "yahoo.de", "yahoo.es", "yahoo.it", "yahoo.ca", "yahoo.com.au", "yahoo.co.in",
            "yahoo.co.jp", "yahoo.com.br", "yahoo.com.mx", "yahoo.ie", "yahoo.in"],
    ICLOUD: ["icloud.com", "me.com", "mac.com"],
    AOL: ["aol.com", "aim.com", "aol.co.uk"],
    PROTON: ["proton.me", "protonmail.com", "protonmail.ch", "pm.me"],
    OTHER_FREE: ["gmx.com", "gmx.net", "gmx.de", "gmx.co.uk", "mail.com", "email.com",
                 "yandex.com", "yandex.ru", "ya.ru", "zoho.com", "zohomail.com",
                 "web.de", "t-online.de", "mail.ru", "inbox.ru", "list.ru", "bk.ru",
                 "qq.com", "163.com", "126.com", "sina.com", "naver.com", "daum.net",
                 "fastmail.com", "tutanota.com", "tuta.io", "hushmail.com",
                 "btinternet.com", "sky.com", "virginmedia.com", "talktalk.net",
                 "orange.fr", "wanadoo.fr", "free.fr", "laposte.net", "libero.it",
                 "comcast.net", "verizon.net", "att.net", "sbcglobal.net", "cox.net",
                 "charter.net", "earthlink.net", "rediffmail.com", "seznam.cz"],
}.items():
    for _d in _domains:
        FREE_PROVIDER_DOMAINS[_d] = _label

# Mail hosts recognised from MX hostnames (checked in order, suffix match).
MX_SIGNATURES: List[tuple] = [
    ("google.com", GOOGLE_WORKSPACE),
    ("googlemail.com", GOOGLE_WORKSPACE),
    ("mail.protection.outlook.com", MICROSOFT_365),
    ("outlook.com", MICROSOFT_365),
    ("zoho.com", "Zoho Mail"), ("zoho.eu", "Zoho Mail"), ("zoho.in", "Zoho Mail"),
    ("zohomail.com", "Zoho Mail"),
    ("secureserver.net", "GoDaddy"),
    ("protonmail.ch", "Proton (business)"),
    ("messagingengine.com", "Fastmail"),
    ("emailsrvr.com", "Rackspace"),
    ("yandex.net", "Yandex 360"), ("yandex.ru", "Yandex 360"),
    ("titan.email", "Titan"),
    ("privateemail.com", "Namecheap Private Email"),
    ("hostinger.com", "Hostinger"),
    ("ionos.com", "IONOS"), ("ionos.co.uk", "IONOS"), ("ionos.de", "IONOS"),
    ("1and1.com", "IONOS"), ("kundenserver.de", "IONOS"),
    ("awsapps.com", "Amazon WorkMail"),
    ("icloud.com", "iCloud (custom domain)"),
    ("yahoodns.net", "Yahoo (business)"),
    ("mimecast.com", "Mimecast gateway"), ("mimecast.co.za", "Mimecast gateway"),
    ("pphosted.com", "Proofpoint gateway"),
    ("barracudanetworks.com", "Barracuda gateway"),
    ("iphmx.com", "Cisco gateway"),
]

# -- statuses ---------------------------------------------------------------------

STATUS_VALID = "valid"
STATUS_INVALID = "invalid syntax"
STATUS_NO_DOMAIN = "domain doesn't exist"
STATUS_NO_MAIL = "no mail server"
STATUS_DISPOSABLE = "disposable"
STATUS_ROLE = "role account"
STATUS_UNKNOWN = "unknown (lookup failed)"
STATUS_NOT_CHECKED = "not checked"
STATUS_CHECKING = "checking..."

# Statuses an email tool should never be sent to.
UNDELIVERABLE_STATUSES = {STATUS_INVALID, STATUS_NO_DOMAIN, STATUS_NO_MAIL, STATUS_DISPOSABLE}


def _data_dir() -> Path:
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) / "data" if base else Path(__file__).resolve().parents[1] / "data"


def load_disposable_domains(path: Optional[Path] = None) -> Set[str]:
    """Load the bundled disposable-domain list (empty set if missing).

    Args:
        path: Override file location.

    Returns:
        Lowercase domains.
    """
    path = path or _data_dir() / "disposable_domains.txt"
    try:
        with path.open(encoding="utf-8") as fh:
            return {line.strip().lower() for line in fh
                    if line.strip() and not line.startswith("#")}
    except OSError:
        return set()


@dataclass
class MxResult:
    """Outcome of a DNS lookup for one domain."""

    status: str                       # "ok", "no_mx", "nxdomain", "null_mx", "error"
    hosts: List[str] = field(default_factory=list)  # MX hosts, best first
    has_address: bool = False         # an A record exists (implicit MX)


@dataclass
class DomainInfo:
    """Provider and deliverability verdict for one email domain."""

    provider: str
    status: str
    mx_hosts: List[str] = field(default_factory=list)


def _doh_query(domain: str, rtype: str, service: str,
               session: Optional[requests.Session]) -> dict:
    """Run one DNS-over-HTTPS JSON query."""
    url = DNS_SERVICES.get(service, DNS_SERVICES["cloudflare"])
    getter = session.get if session is not None else requests.get
    resp = getter(url, params={"name": domain, "type": rtype},
                  headers={"accept": "application/dns-json"},
                  timeout=DNS_TIMEOUT_SECONDS)
    resp.raise_for_status()
    return resp.json()


def lookup_mx(domain: str, service: str = "cloudflare",
              session: Optional[requests.Session] = None) -> MxResult:
    """Look up a domain's mail servers via DNS-over-HTTPS.

    Args:
        domain: Email domain, e.g. ``"acme.com"``.
        service: ``"cloudflare"`` or ``"google"``.
        session: Optional ``requests.Session`` to reuse connections.

    Returns:
        An :class:`MxResult`. Network problems give ``status="error"``.
    """
    try:
        data = _doh_query(domain, "MX", service, session)
        if data.get("Status") == 3:
            return MxResult("nxdomain")
        if data.get("Status") not in (0, None):
            return MxResult("error")
        records = []
        for answer in data.get("Answer") or []:
            if answer.get("type") != 15:
                continue
            parts = str(answer.get("data", "")).split()
            if len(parts) == 2:
                try:
                    records.append((int(parts[0]), parts[1].rstrip(".").lower()))
                except ValueError:
                    continue
        if records:
            records.sort()
            hosts = [h for _, h in records]
            if hosts == [""]:  # RFC 7505 "null MX": domain accepts no mail
                return MxResult("null_mx")
            return MxResult("ok", hosts)
        # No MX: mail falls back to the domain's A record (RFC 5321).
        data = _doh_query(domain, "A", service, session)
        has_a = any(a.get("type") == 1 for a in data.get("Answer") or [])
        return MxResult("no_mx", has_address=has_a)
    except (requests.RequestException, ValueError):
        return MxResult("error")


def provider_from_mx(hosts: Iterable[str]) -> str:
    """Name the mail host from MX hostnames (best-priority host first)."""
    for host in hosts:
        for suffix, label in MX_SIGNATURES:
            if host == suffix or host.endswith("." + suffix):
                return label
    return OTHER_BUSINESS


def free_provider(domain: str) -> Optional[str]:
    """Return the free provider label for a domain, or None."""
    return FREE_PROVIDER_DOMAINS.get(domain.lower().strip().rstrip("."))


def classify_offline(domain: str, disposable: Set[str]) -> Optional[DomainInfo]:
    """Classify without network when possible (free or disposable domains).

    Returns:
        A final :class:`DomainInfo`, or None if a DNS lookup is needed.
    """
    domain = domain.lower().strip().rstrip(".")
    if domain in disposable:
        return DomainInfo(OTHER_FREE, STATUS_DISPOSABLE)
    label = free_provider(domain)
    if label:
        return DomainInfo(label, STATUS_VALID)
    return None


def classify_from_mx(mx: MxResult) -> DomainInfo:
    """Turn an MX lookup into provider + status."""
    if mx.status == "ok":
        return DomainInfo(provider_from_mx(mx.hosts), STATUS_VALID, mx.hosts)
    if mx.status == "nxdomain":
        return DomainInfo(NO_MAIL, STATUS_NO_DOMAIN)
    if mx.status == "null_mx":
        return DomainInfo(NO_MAIL, STATUS_NO_MAIL)
    if mx.status == "no_mx":
        # NOTE: No MX but an A record: mail *may* be delivered to the host
        # itself (RFC 5321 implicit MX). Rare and unreliable, so it is shown as
        # "no mail server" only when there is no A record either.
        if mx.has_address:
            return DomainInfo(OTHER_BUSINESS, STATUS_VALID)
        return DomainInfo(NO_MAIL, STATUS_NO_MAIL)
    return DomainInfo(UNKNOWN, STATUS_UNKNOWN)


def offline_provider(domain: str) -> str:
    """Provider label without DNS: free provider name or 'Business / other'."""
    return free_provider(domain) or "Business / other (not checked)"


def slug(label: str) -> str:
    """File-name friendly version of a provider label (``"Outlook / Hotmail"`` -> ``outlook_hotmail``)."""
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or "other"
