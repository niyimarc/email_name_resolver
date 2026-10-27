"""Email-to-name pipeline: rule-based split, AI judgment, website lookup."""

from .splitter import PARTICLES, format_tokens_rule_based, split_email_local
from .ai_judge import (
    DEFAULT_HOST,
    DEFAULT_MODEL,
    UNAVAILABLE_REASON,
    NameVerdict,
    check_ollama_available,
    judge_name_with_ai,
)
from .name_dictionary import DictionaryStore, NameDictionary
from .website import FREE_PROVIDERS, WebsiteResolver, resolve_company_website

__all__ = [
    "PARTICLES", "split_email_local", "format_tokens_rule_based",
    "NameVerdict", "judge_name_with_ai", "check_ollama_available",
    "DEFAULT_MODEL", "DEFAULT_HOST", "UNAVAILABLE_REASON",
    "NameDictionary", "DictionaryStore",
    "FREE_PROVIDERS", "resolve_company_website", "WebsiteResolver",
]
