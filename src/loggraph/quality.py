from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from loggraph.analyzer import analyze_log
from loggraph.graph.store import load_index
from loggraph.logs.templates import normalize_text
from loggraph.profile import render_profile_suggestion

SESSION_KEY_PATTERN = re.compile(r"\b(?:traceId|requestId|reqId|deliveryId|orderId|taskId|sessionId|sid|uuid|sn)\b\s*[=:]|\{[^}]*id[^}]*\}|%[sd]", re.I)
DURATION_PATTERN = re.compile(r"\b(duration|elapsed|cost|took|耗时)\b", re.I)
STATE_PATTERN = re.compile(r"\b(state|status)\b|状态|Await|Pending|Success|Failed|Timeout", re.I)
ERROR_DETAIL_PATTERN = re.compile(r"\b(exception|throwable|errorCode|code|reason|cause|stack)\b|异常|原因|错误码", re.I)
LITERAL_WORD_PATTERN = re.compile(r"[A-Za-z\u4e00-\u9fff]{3,}")
PLACEHOLDER_PATTERN = re.compile(r"%\w|\{[^}]*\}|\b\d+\b|0x[0-9a-f]+|[0-9a-f]{8,}", re.I)


def audit_index(index_path: str | Path) -> dict[str, Any]:
    idx = load_index(index_path)
    sites = list(idx.log_sites.values())
    total = len(sites)
    with_session = [s for s in sites if SESSION_KEY_PATTERN.search(s.template)]
    with_duration = [s for s in sites if DURATION_PATTERN.search(s.template)]
    with_state = [s for s in sites if STATE_PATTERN.search(s.template)]
    error_sites = [s for s in sites if (s.level or "").lower() in {"error", "fatal", "critical", "exception"}]
    error_with_detail = [s for s in error_sites if ERROR_DETAIL_PATTERN.search(s.template)]
    generic = [s for s in sites if not LITERAL_WORD_PATTERN.search(normalize_text(s.template))]
    score = _quality_score(total, len(with_session), len(with_duration), len(with_state), len(error_sites), len(error_with_detail), len(generic))
    problems = []
    if total and len(with_session) / total < 0.5:
        problems.append("Fewer than 50% of log templates include a recognizable session/request key.")
    if total and len(with_duration) / total < 0.2:
        problems.append("Few log templates include duration/elapsed/cost fields.")
    if error_sites and len(error_with_detail) / len(error_sites) < 0.5:
        problems.append("Many error-level logs lack exception/errorCode/reason/cause details.")
    if generic:
        problems.append("Some log templates are too generic to be useful for matching.")
    return {
        "index": str(index_path),
        "score": score,
        "log_sites": total,
        "with_session_key": len(with_session),
        "with_duration": len(with_duration),
        "with_state": len(with_state),
        "error_log_sites": len(error_sites),
        "error_with_detail": len(error_with_detail),
        "generic_templates": [site_summary(s) for s in generic[:20]],
        "problems": problems,
        "recommendations": recommendations(problems),
        "report_markdown": "",
    }


def render_audit_report(report: dict[str, Any]) -> str:
    total = report.get("log_sites", 0) or 1
    lines = [
        "# LogGraph Logging Quality Audit",
        "",
        f"Score: {report.get('score', 0)}/100",
        "",
        "## Metrics",
        f"- Log sites: {report.get('log_sites', 0)}",
        f"- With session key: {report.get('with_session_key', 0)} ({report.get('with_session_key', 0) / total:.0%})",
        f"- With duration: {report.get('with_duration', 0)} ({report.get('with_duration', 0) / total:.0%})",
        f"- With state/status: {report.get('with_state', 0)} ({report.get('with_state', 0) / total:.0%})",
        f"- Error logs with details: {report.get('error_with_detail', 0)}/{report.get('error_log_sites', 0)}",
        "",
        "## Problems",
    ]
    lines.extend([f"- {p}" for p in report.get("problems", [])] or ["- No major logging quality problems detected by generic rules."])
    lines.extend(["", "## Recommendations"])
    lines.extend([f"- {r}" for r in report.get("recommendations", [])])
    generic = report.get("generic_templates", [])
    if generic:
        lines.extend(["", "## Generic templates"])
        for item in generic[:10]:
            lines.append(f"- `{item['template']}` at `{item['file']}:{item['line']}`")
    return "\n".join(lines)


def refine_profile(index_path: str | Path, log_file: str | Path, *, project: str | Path, all_lines: bool = False) -> dict[str, Any]:
    report = analyze_log(index_path, log_file, project=project, app_only=not all_lines, context=0)
    runtime = report.get("runtime_findings", {})
    suggestions = runtime.get("suggested_event_rules", [])
    session_keys = report.get("event_profile_summary", {}).get("session_keys", [])
    states = report.get("event_profile_summary", {}).get("states", [])
    learned = [
        {"pattern": item["pattern"], "type": f"project:{_safe_name(item['pattern'])}", "example": item.get("example", "")}
        for item in suggestions
    ]
    seen = {item["pattern"] for item in learned}
    for item in _tokens_from_runtime(runtime):
        if item["pattern"] not in seen:
            learned.append(item)
            seen.add(item["pattern"])
    profile = {
        "session_keys": session_keys,
        "states": states,
        "learned_patterns": learned[:30],
    }
    patch = render_profile_suggestion(profile)
    return {
        "log_file": str(log_file),
        "patch_yaml": patch,
        "basis": {
            "event_types": runtime.get("event_types", {}),
            "missing_events": runtime.get("missing_events", []),
            "suggested_event_rules": suggestions,
        },
    }


def sequence_from_log(index_path: str | Path, log_file: str | Path, *, project: str | Path, name: str, all_lines: bool = False) -> dict[str, Any]:
    report = analyze_log(index_path, log_file, project=project, app_only=not all_lines, context=0)
    labels = []
    for timeline in report.get("runtime_findings", {}).get("session_timelines", []):
        for label in timeline.get("labels", []):
            if label not in labels:
                labels.append(label)
        if labels:
            break
    if not labels:
        labels = list(report.get("runtime_findings", {}).get("event_types", {}).keys())
    lines = ["expected_sequences:", f"  {name}:"] + [f"    - {label}" for label in labels]
    return {"name": name, "sequence": labels, "yaml": "\n".join(lines) + "\n"}


def _tokens_from_runtime(runtime: dict[str, Any]) -> list[dict[str, str]]:
    tokens: list[dict[str, str]] = []
    stop = {"error", "failed", "failure", "timeout", "retry", "state", "status", "duration", "deliveryid"}
    for event in runtime.get("timeline", []) + runtime.get("suspicious_events", []):
        message = normalize_text(PLACEHOLDER_PATTERN.sub(" ", event.get("message", ""))).lower()
        session_id = str(event.get("session_id") or "").lower()
        for token in re.findall(r"[a-z\u4e00-\u9fff][a-z0-9_\u4e00-\u9fff-]{2,}", message):
            if token in stop or token == session_id:
                continue
            tokens.append({"pattern": token, "type": f"project:{_safe_name(token)}", "example": event.get("message", "")})
    unique = []
    seen = set()
    for item in tokens:
        if item["pattern"] not in seen:
            unique.append(item)
            seen.add(item["pattern"])
    return unique


def site_summary(site: Any) -> dict[str, Any]:
    return {"template": site.template, "file": site.file, "line": site.line, "level": site.level}


def recommendations(problems: list[str]) -> list[str]:
    base = [
        "Include a stable session/request key in flow logs, e.g. deliveryId/requestId/traceId.",
        "Add duration/elapsedMs to boundaries and timeout logs.",
        "For error logs, include exception/reason/errorCode where available.",
        "Prefer distinctive event wording over generic templates such as only `%s` or `{}`.",
    ]
    return base if problems else ["Keep session keys, state markers, and duration fields consistent across related events."]


def _quality_score(total: int, session_count: int, duration_count: int, state_count: int, error_count: int, error_detail_count: int, generic_count: int) -> int:
    if total == 0:
        return 0
    score = 40
    score += int(25 * (session_count / total))
    score += int(15 * (duration_count / total))
    score += int(10 * (state_count / total))
    if error_count:
        score += int(10 * (error_detail_count / error_count))
    else:
        score += 5
    score -= min(20, generic_count * 2)
    return max(0, min(100, score))


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", value.strip().lower()).strip("_") or "event"
