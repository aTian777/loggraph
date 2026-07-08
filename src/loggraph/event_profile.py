from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

from loggraph.logs.templates import normalize_text
from loggraph.models import CodeIndex

WORD_PATTERN = re.compile(r"[A-Za-z\u4e00-\u9fff][A-Za-z0-9_\u4e00-\u9fff-]{2,}")
PLACEHOLDER_PATTERN = re.compile(r"%\w|\{[^}]*\}|\b\d+\b|0x[0-9a-f]+|[0-9a-f]{8,}", re.I)
KEY_VALUE_PATTERN = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*(?:Id|ID|_id|Key|No|SN|sn)?)\b\s*[=:]")
CAMEL_STATE_PATTERN = re.compile(r"\b(?:Await|Wait|Pending|Loading|Success|Failed|Fail|Error|Timeout|Retry)[A-Za-z0-9_]*\b")

GENERIC_STOPWORDS = {
    "the", "and", "for", "with", "from", "this", "that", "http", "https", "post", "get", "put", "delete",
    "error", "failed", "failure", "timeout", "retry", "state", "status", "info", "debug", "warn", "warning",
    "null", "true", "false", "log", "logger",
}


def build_event_profile(index: CodeIndex, *, min_token_count: int = 1, max_patterns: int = 80) -> dict[str, Any]:
    """Learn a lightweight project event profile from indexed log templates.

    This intentionally learns from the project's own logging sites at init time.
    Generic built-in event rules remain a fallback, but project vocabulary comes
    from source logs and is cached inside the index metadata.
    """
    token_counts: Counter[str] = Counter()
    token_examples: dict[str, str] = {}
    logger_counts: Counter[str] = Counter()
    session_keys: Counter[str] = Counter()
    states: Counter[str] = Counter()
    token_log_sites: defaultdict[str, int] = defaultdict(int)

    for site in index.log_sites.values():
        template = site.template or ""
        normalized = normalize_text(PLACEHOLDER_PATTERN.sub(" ", template)).lower()
        site_tokens = set()
        for token in WORD_PATTERN.findall(normalized):
            token_l = token.lower()
            if token_l in GENERIC_STOPWORDS or len(token_l) < 3:
                continue
            token_counts[token_l] += 1
            token_examples.setdefault(token_l, template)
            site_tokens.add(token_l)
        for token in site_tokens:
            token_log_sites[token] += 1
        if site.logger:
            logger_counts[site.logger] += 1
        for key in KEY_VALUE_PATTERN.findall(template):
            session_keys[key] += 1
        for state in CAMEL_STATE_PATTERN.findall(template):
            states[state] += 1

    learned_patterns = []
    for token, count in token_counts.most_common(max_patterns):
        if count < min_token_count and token_log_sites[token] < 2:
            continue
        learned_patterns.append({
            "pattern": token,
            "type": f"project:{token}",
            "count": count,
            "log_sites": token_log_sites[token],
            "example": token_examples[token],
        })

    return {
        "version": 1,
        "source": "indexed_log_templates",
        "log_site_count": len(index.log_sites),
        "learned_patterns": learned_patterns,
        "session_keys": [key for key, _ in session_keys.most_common(20)],
        "states": [state for state, _ in states.most_common(40)],
        "loggers": [logger for logger, _ in logger_counts.most_common(40)],
    }
