from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

from loggraph.logs.templates import normalize_text
from loggraph.models import LogEntry

SESSION_PATTERN = re.compile(
    r"\b(?P<key>traceId|requestId|reqId|deliveryId|orderId|taskId|sessionId|sid|uuid|sn)\b\s*[=:]\s*(?P<value>[A-Za-z0-9_.:-]+)",
    re.I,
)
STATE_PATTERNS = [
    re.compile(r"\b(?:state|status)\b\s*[=:]\s*(?P<state>[A-Za-z_][\w.-]+)", re.I),
    re.compile(r"(?:enter|entered|进入|切换到|转到)\s*(?P<state>[A-Za-z_][\w.-]+)", re.I),
]
DURATION_PATTERN = re.compile(r"\b(?:duration|elapsed|cost|took|耗时)\b\s*[=:]?\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>ms|s|sec|seconds|毫秒|秒)?", re.I)
WORD_PATTERN = re.compile(r"[A-Za-z\u4e00-\u9fff][A-Za-z0-9_\u4e00-\u9fff-]{2,}")
PLACEHOLDER_PATTERN = re.compile(r"\b\d+\b|0x[0-9a-f]+|[0-9a-f]{8,}", re.I)

EVENT_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("exception", re.compile(r"\b(exception|traceback|crash|fatal)\b|异常|崩溃", re.I)),
    ("timeout", re.compile(r"\b(time[ -]?out|timed out|deadline)\b|超时", re.I)),
    ("retry", re.compile(r"\b(retry|again|attempt)\b|重试|再次", re.I)),
    ("state", re.compile(r"\b(state|status)\b|状态", re.I)),
    ("api_call", re.compile(r"\b(GET|POST|PUT|DELETE|PATCH)\b|-->|<--|/api/|http", re.I)),
    ("duration", DURATION_PATTERN),
    ("error", re.compile(r"\b(error|failed|failure|fail)\b|错误|失败", re.I)),
]


@dataclass
class RuntimeEvent:
    line: int
    type: str
    message: str
    timestamp: str = ""
    level: str = ""
    logger: str = ""
    session_key: str = ""
    session_id: str = ""
    state: str = ""
    duration_ms: float | None = None
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def extract_event(entry: LogEntry, line: int, profile: dict[str, Any] | None = None) -> RuntimeEvent | None:
    text = f"{entry.message or ''} {entry.raw or ''}"
    event_type = _event_type(entry, text, profile)
    if not event_type:
        return None
    duration = _duration_ms(text)
    session_key, session_id = _session(text, profile)
    state = _state(text, profile)
    evidence = []
    if entry.exception_type:
        evidence.append(f"exception={entry.exception_type}")
    if duration is not None:
        evidence.append(f"duration_ms={duration:g}")
    if state:
        evidence.append(f"state={state}")
    if session_id:
        evidence.append(f"{session_key}={session_id}")
    return RuntimeEvent(
        line=line,
        type=event_type,
        message=entry.message or entry.raw,
        timestamp=entry.timestamp,
        level=entry.level,
        logger=entry.logger,
        session_key=session_key,
        session_id=session_id,
        state=state,
        duration_ms=duration,
        evidence=evidence,
    )


def summarize_events(events: list[RuntimeEvent], *, limit: int = 20, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    counts = Counter(event.type for event in events)
    sessions = Counter(event.session_id for event in events if event.session_id)
    suspicious = [event.to_dict() for event in events if event.type in {"error", "exception", "timeout", "retry"}]
    timeline = [event.to_dict() for event in events[:limit]]
    session_timelines = build_session_timelines(events, limit=limit, profile=profile)
    return {
        "event_count": len(events),
        "event_types": dict(counts),
        "sessions": dict(sessions.most_common(10)),
        "session_timelines": session_timelines,
        "timeline": timeline,
        "suspicious_events": suspicious[:limit],
        "missing_events": find_missing_events(session_timelines, profile or {}),
        "duration_stats": summarize_durations(events, profile=profile),
        "suggested_event_rules": suggest_event_rules(events),
    }


def build_session_timelines(events: list[RuntimeEvent], *, limit: int = 20, profile: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    grouped: dict[str, list[RuntimeEvent]] = {}
    for event in events:
        key = event.session_id or "__global__"
        grouped.setdefault(key, []).append(event)
    timelines = []
    for session_id, items in grouped.items():
        if session_id == "__global__" and len(grouped) > 1:
            continue
        labels = [_event_label(event, profile or {}) for event in items]
        timelines.append({
            "session_id": "" if session_id == "__global__" else session_id,
            "session_key": next((event.session_key for event in items if event.session_key), ""),
            "event_count": len(items),
            "event_types": dict(Counter(event.type for event in items)),
            "labels": labels,
            "events": [event.to_dict() for event in items[:limit]],
        })
    return sorted(timelines, key=lambda item: (-item["event_count"], item["session_id"]))[:limit]


def find_missing_events(session_timelines: list[dict[str, Any]], profile: dict[str, Any]) -> list[dict[str, Any]]:
    missing = []
    for sequence_name, sequence in (profile.get("expected_sequences") or {}).items():
        if not isinstance(sequence, list):
            continue
        expected = [str(item) for item in sequence]
        for timeline in session_timelines:
            observed = timeline.get("labels", [])
            cursor = 0
            absent = []
            for item in expected:
                try:
                    pos = observed.index(item, cursor)
                    cursor = pos + 1
                except ValueError:
                    absent.append(item)
            if absent:
                missing.append({
                    "session_id": timeline.get("session_id", ""),
                    "sequence": sequence_name,
                    "missing": absent,
                    "observed": observed,
                })
    return missing


def _event_label(event: RuntimeEvent, profile: dict[str, Any]) -> str:
    if not event.type.startswith("project:"):
        return event.type
    raw = event.type.split(":", 1)[1]
    for name, spec in (profile.get("manual_events") or {}).items():
        if raw == name or raw == str(spec.get("type", "")):
            return name
    return raw


def summarize_durations(events: list[RuntimeEvent], *, profile: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    grouped: dict[str, list[float]] = {}
    for event in events:
        if event.duration_ms is None:
            continue
        label = event.state or _event_label(event, profile or {})
        grouped.setdefault(label, []).append(event.duration_ms)
    stats = []
    for label, values in grouped.items():
        stats.append({
            "label": label,
            "count": len(values),
            "min_ms": min(values),
            "max_ms": max(values),
            "avg_ms": sum(values) / len(values),
        })
    return sorted(stats, key=lambda item: (-item["avg_ms"], item["label"]))


def suggest_event_rules(events: list[RuntimeEvent], *, limit: int = 8) -> list[dict[str, Any]]:
    """Suggest project-specific event patterns that a future profile could learn.

    This is intentionally conservative: it does not mutate project files. It surfaces
    recurring runtime vocabulary so an agent or user can promote it into a profile.
    """
    token_counts: Counter[str] = Counter()
    examples: dict[str, str] = {}
    for event in events:
        normalized = normalize_text(PLACEHOLDER_PATTERN.sub(" ", event.message)).lower()
        session_value = event.session_id.lower()
        for token in WORD_PATTERN.findall(normalized):
            token_l = token.lower()
            if session_value and token_l == session_value:
                continue
            if token_l in {"error", "failed", "failure", "timeout", "retry", "state", "status", "http", "post", "get"}:
                continue
            token_counts[token_l] += 1
            examples.setdefault(token_l, event.message)
    suggestions = []
    for token, count in token_counts.most_common(limit):
        if count < 2:
            continue
        suggestions.append({
            "pattern": token,
            "count": count,
            "reason": "recurring runtime vocabulary; consider promoting to .loggraph/profile.yaml event/entity rule",
            "example": examples[token],
        })
    return suggestions


def _event_type(entry: LogEntry, text: str, profile: dict[str, Any] | None = None) -> str:
    if entry.exception_type:
        return "exception"
    level = (entry.level or "").upper()
    for event_type, pattern in EVENT_RULES:
        if pattern.search(text):
            return event_type
    normalized = normalize_text(text).lower()
    for item in (profile or {}).get("learned_patterns", []):
        pattern = str(item.get("pattern") or "").lower()
        if pattern and pattern in normalized:
            return str(item.get("type") or f"project:{pattern}")
    if level in {"ERROR", "EXCEPTION", "CRITICAL", "FATAL"}:
        return "error"
    return ""


def _session(text: str, profile: dict[str, Any] | None = None) -> tuple[str, str]:
    for key in (profile or {}).get("session_keys", []):
        pattern = re.compile(rf"\b({re.escape(str(key))})\b\s*[=:]\s*([A-Za-z0-9_.:-]+)", re.I)
        if m := pattern.search(text):
            return m.group(1), m.group(2)
    if m := SESSION_PATTERN.search(text):
        return m.group("key"), m.group("value")
    return "", ""


def _state(text: str, profile: dict[str, Any] | None = None) -> str:
    for state in (profile or {}).get("states", []):
        if str(state) and str(state) in text:
            return str(state)
    for pattern in STATE_PATTERNS:
        if m := pattern.search(text):
            return m.group("state")
    return ""


def _duration_ms(text: str) -> float | None:
    m = DURATION_PATTERN.search(text)
    if not m:
        return None
    value = float(m.group("value"))
    unit = (m.group("unit") or "ms").lower()
    if unit in {"s", "sec", "seconds", "秒"}:
        return value * 1000
    return value
