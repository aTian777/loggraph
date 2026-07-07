from __future__ import annotations

import re
from difflib import SequenceMatcher

WILDCARD = r".+?"


def normalize_text(text: str) -> str:
    text = str(text or "")
    text = " ".join(text.split())
    return text


def template_to_regex(template: str) -> str:
    """Convert common logger templates into a permissive full-message regex."""
    t = normalize_text(template)
    if not t:
        return ""
    parts: list[str] = []
    i = 0
    while i < len(t):
        if t[i] == "%" and i + 1 < len(t) and t[i + 1] in "sdrfioxX":
            parts.append(WILDCARD)
            i += 2
        elif t[i] == "{" and "}" in t[i + 1:]:
            j = t.find("}", i + 1)
            inside = t[i + 1:j]
            # Treat normal format fields as variables; keep escaped braces simple.
            if not inside or re.match(r"[A-Za-z_][\w.:-]*$", inside):
                parts.append(WILDCARD)
            else:
                parts.append(WILDCARD)
            i = j + 1
        else:
            parts.append(re.escape(t[i]))
            i += 1
    return r"(?s).*" + "".join(parts).replace(r"\ ", r"\s+") + r".*"


def template_matches(template: str, message: str) -> bool:
    rx = template_to_regex(template)
    if not rx:
        return False
    return re.match(rx, normalize_text(message)) is not None


def canonical_for_similarity(text: str) -> str:
    text = normalize_text(text).lower()
    text = re.sub(r"\b[0-9a-f]{8}-[0-9a-f-]{27,}\b", " <uuid> ", text)
    text = re.sub(r"\b\d+\b", " <num> ", text)
    text = re.sub(r"%[sdrfioxX]", " <var> ", text)
    text = re.sub(r"\{[^}]*\}", " <var> ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def similarity(a: str, b: str) -> float:
    ca, cb = canonical_for_similarity(a), canonical_for_similarity(b)
    if not ca or not cb:
        return 0.0
    if ca in cb or cb in ca:
        return 0.95
    # Fast path: if lengths differ too much, skip expensive SequenceMatcher
    len_ratio = min(len(ca), len(cb)) / max(len(ca), len(cb)) if max(len(ca), len(cb)) > 0 else 0
    if len_ratio < 0.4:
        return 0.0
    # For very similar strings, use word overlap as fast approximation
    words_a = set(ca.split())
    words_b = set(cb.split())
    if words_a and words_b:
        overlap = len(words_a & words_b) / max(len(words_a), len(words_b))
        if overlap > 0.8:
            return 0.85 + (overlap - 0.8) * 0.5  # Scale to 0.85-0.95 range
    return SequenceMatcher(None, ca, cb).ratio()
