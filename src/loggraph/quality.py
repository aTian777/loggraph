from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from loggraph.analyzer import analyze_log
from loggraph.graph.store import load_index
from loggraph.logs.templates import normalize_text
from loggraph.profile import default_profile_path, load_project_profile, render_profile_suggestion

GENERIC_PROFILE_TOKENS = {
    "message", "msg", "send", "recv", "receive", "received", "default", "connected",
    "success", "failed", "error", "event", "data", "info", "debug", "true", "false",
    "请求", "响应", "消息", "成功", "失败", "状态", "事件",
}

SESSION_KEY_PATTERN = re.compile(r"\b(?:traceId|requestId|reqId|deliveryId|orderId|taskId|sessionId|sid|uuid|sn)\b\s*[=:]|\{[^}]*id[^}]*\}|%[sd]", re.I)
DURATION_PATTERN = re.compile(r"\b(duration|elapsed|cost|took|耗时)\b", re.I)
STATE_PATTERN = re.compile(r"\b(state|status)\b|状态|Await|Pending|Success|Failed|Timeout", re.I)
ERROR_DETAIL_PATTERN = re.compile(r"\b(exception|throwable|errorCode|code|reason|cause|stack)\b|异常|原因|错误码", re.I)
LITERAL_WORD_PATTERN = re.compile(r"[A-Za-z\u4e00-\u9fff]{3,}")
PLACEHOLDER_PATTERN = re.compile(r"%\w|\{[^}]*\}|\b\d+\b|0x[0-9a-f]+|[0-9a-f]{8,}", re.I)


def suggest_app_identifiers(project: str | Path) -> list[str]:
    root = Path(project)
    candidates: list[str] = []
    for gradle in list(root.glob("*/build.gradle")) + list(root.glob("*/build.gradle.kts")):
        text = gradle.read_text(encoding="utf-8", errors="ignore")
        for pattern in [r"namespace\s*[= ]\s*[\"']([^\"']+)[\"']", r"applicationId\s*[= ]\s*[\"']([^\"']+)[\"']"]:
            for match in re.findall(pattern, text):
                if match not in candidates:
                    candidates.append(match)
    manifest = root / "app" / "src" / "main" / "AndroidManifest.xml"
    if manifest.exists():
        text = manifest.read_text(encoding="utf-8", errors="ignore")
        for match in re.findall(r"package=[\"']([^\"']+)[\"']", text):
            if match not in candidates:
                candidates.append(match)
    return candidates


def doctor_project(project: str | Path, index_path: str | Path, *, log_file: str | Path | None = None, query: str = "", all_lines: bool = False) -> dict[str, Any]:
    project_path = Path(project)
    index = Path(index_path)
    profile_path = default_profile_path(project_path)
    profile = load_project_profile(project_path)
    status = {
        "project": str(project_path.resolve()),
        "index": str(index),
        "index_exists": index.exists(),
        "index_stale": False,
        "newest_source_mtime": 0.0,
        "index_mtime": index.stat().st_mtime if index.exists() else 0.0,
        "profile": str(profile_path),
        "profile_exists": profile_path.exists(),
        "app_identifiers": profile.get("app_identifiers", []),
        "exclude_paths": profile.get("exclude_paths", []),
        "suggested_app_identifiers": suggest_app_identifiers(project_path),
        "profile_health": profile_health(profile),
        "log_file": str(log_file) if log_file else "",
        "query": query,
        "recommended_next": [],
    }
    if not status["index_exists"]:
        status["recommended_next"].append("loggraph init <project>")
    else:
        newest = newest_source_mtime(project_path, exclude_paths=profile.get("exclude_paths", []))
        status["newest_source_mtime"] = newest
        status["index_stale"] = newest > status["index_mtime"]
        if status["index_stale"]:
            status["recommended_next"].append("loggraph init <project>")
    if not status["profile_exists"]:
        status["recommended_next"].append("loggraph profile init <project>")
    if not status["app_identifiers"] and status["suggested_app_identifiers"]:
        status["recommended_next"].append("add app_identifiers to .loggraph/profile.yaml")
    if status["index_exists"]:
        idx = load_index(index)
        status["functions"] = len(idx.functions)
        status["log_sites"] = len(idx.log_sites)
        status["learned_patterns"] = len(idx.metadata.get("event_profile", {}).get("learned_patterns", []))
        status["recommended_next"].append("loggraph audit <project>")
    if log_file and status["profile_exists"] and status["index_exists"]:
        lint = lint_profile(project_path, index, log_file=log_file, query=query, all_lines=all_lines, fix_suggest=True)
        status["profile_lint"] = {
            "problems": len(lint.get("problems", [])),
            "warnings": sum(1 for p in lint.get("problems", []) if p.get("severity") == "warning"),
            "errors": sum(1 for p in lint.get("problems", []) if p.get("severity") == "error"),
            "infos": sum(1 for p in lint.get("problems", []) if p.get("severity") == "info"),
            "cleanup_candidates": sum(len(v) if isinstance(v, list) else sum(len(items) for items in v.values()) for v in (lint.get("cleanup_patch") or {}).values()),
        }
        q = f" --query {query!r}" if query else ""
        status["recommended_next"].insert(0, f"loggraph explain <project> --log-file {Path(log_file).name}{q}")
        status["recommended_next"].insert(1, f"loggraph profile lint <project> --log-file {Path(log_file).name}{q} --fix-suggest")
    return status


def profile_health(profile: dict[str, Any]) -> dict[str, int]:
    return {
        "app_identifiers": len(profile.get("app_identifiers") or []),
        "exclude_paths": len(profile.get("exclude_paths") or []),
        "session_keys": len(profile.get("session_keys") or []),
        "entities": len(profile.get("entities") or {}),
        "events": len(profile.get("events") or {}),
        "expected_sequences": len(profile.get("expected_sequences") or {}),
    }


def newest_source_mtime(project: Path, *, exclude_paths: list[str] | None = None) -> float:
    newest = 0.0
    exclude_paths = exclude_paths or []
    suffixes = {".py", ".kt", ".java", ".ts", ".tsx", ".js", ".go", ".c", ".cc", ".cpp", ".h", ".hpp"}
    for path in project.rglob("*"):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        rel = path.relative_to(project).as_posix()
        if _skip_doctor_path(rel, exclude_paths):
            continue
        try:
            newest = max(newest, path.stat().st_mtime)
        except OSError:
            continue
    return newest


def _skip_doctor_path(rel: str, exclude_paths: list[str]) -> bool:
    if rel.startswith(".loggraph/") or "/build/" in f"/{rel}/" or "/.git/" in f"/{rel}/":
        return True
    for pattern in exclude_paths:
        normalized = str(pattern).replace("\\", "/")
        literal = normalized.replace("**/", "").replace("/**", "").replace("*", "")
        if literal and literal in rel:
            return True
    return False


def render_doctor_report(status: dict[str, Any]) -> str:
    lines = [
        "# LogGraph Doctor",
        "",
        f"Project: `{status['project']}`",
        f"Index exists: {status['index_exists']} (`{status['index']}`)",
        f"Profile exists: {status['profile_exists']} (`{status['profile']}`)",
        f"App identifiers: {', '.join(status.get('app_identifiers') or []) or 'none'}",
        f"Suggested app identifiers: {', '.join(status.get('suggested_app_identifiers') or []) or 'none'}",
        f"Exclude paths: {', '.join(status.get('exclude_paths') or []) or 'none'}",
    ]
    if status.get("index_exists"):
        lines.extend([
            f"Functions: {status.get('functions', 0)}",
            f"Log sites: {status.get('log_sites', 0)}",
            f"Learned patterns: {status.get('learned_patterns', 0)}",
            f"Index stale: {status.get('index_stale', False)}",
        ])
    health = status.get("profile_health") or {}
    if health:
        lines.extend([
            "",
            "## Profile health",
            f"- App identifiers: {health.get('app_identifiers', 0)}",
            f"- Session keys: {health.get('session_keys', 0)}",
            f"- Entities: {health.get('entities', 0)}",
            f"- Events: {health.get('events', 0)}",
            f"- Expected sequences: {health.get('expected_sequences', 0)}",
            f"- Exclude paths: {health.get('exclude_paths', 0)}",
        ])
    lint = status.get("profile_lint")
    if lint:
        lines.extend([
            "",
            "## Log-aware profile lint",
            f"- Problems: {lint.get('problems', 0)}",
            f"- Warnings: {lint.get('warnings', 0)}",
            f"- Errors: {lint.get('errors', 0)}",
            f"- Info: {lint.get('infos', 0)}",
            f"- Cleanup candidates: {lint.get('cleanup_candidates', 0)}",
        ])
    lines.extend(["", "## Recommended next"])
    lines.extend([f"- {item}" for item in status.get("recommended_next", [])] or ["- No immediate action."])
    return "\n".join(lines)


def lint_profile(project: str | Path, index_path: str | Path, *, log_file: str | Path | None = None, query: str = "", all_lines: bool = False, fix_suggest: bool = False) -> dict[str, Any]:
    project_path = Path(project)
    profile_path = default_profile_path(project_path)
    profile = load_project_profile(project_path)
    problems: list[dict[str, Any]] = []
    suggestions: list[str] = []
    event_match_counts: dict[str, int] = {}
    session_key_counts: dict[str, int] = {}
    profile_warnings: list[dict[str, Any]] = []

    if not profile_path.exists():
        problems.append({"severity": "error", "type": "missing_profile", "message": f"Profile does not exist: {profile_path}"})
        suggestions.append("Run `loggraph profile init <project>` or `loggraph profile suggest <project> --from-log <log>`.")
        return _profile_lint_payload(project_path, profile_path, problems, suggestions, event_match_counts, session_key_counts, profile_warnings, log_file, query, fix_suggest=fix_suggest)

    events = profile.get("events") or {}
    expected_sequences = profile.get("expected_sequences") or {}
    defined_event_names = set(events.keys()) | {str(spec.get("type")) for spec in events.values() if isinstance(spec, dict) and spec.get("type")}

    for key in profile.get("session_keys") or []:
        key_text = str(key)
        if _looks_like_function_name(key_text):
            problems.append({"severity": "warning", "type": "suspicious_session_key", "message": f"Session key `{key_text}` looks like a function or method name, not a correlation key."})
            suggestions.append(f"Remove `{key_text}` from `session_keys` unless it really appears as a stable log correlation field.")

    for entity, spec in (profile.get("entities") or {}).items():
        aliases = spec.get("aliases", []) if isinstance(spec, dict) else []
        for alias in aliases:
            alias_text = str(alias).strip()
            if _is_generic_profile_token(alias_text):
                problems.append({"severity": "warning", "type": "generic_alias", "message": f"Entity `{entity}` alias `{alias_text}` is too generic and may over-match unrelated logs."})
                suggestions.append(f"Remove or narrow alias `{alias_text}` under entity `{entity}`.")

    for name, spec in events.items():
        if not isinstance(spec, dict):
            problems.append({"severity": "warning", "type": "invalid_event", "message": f"Event `{name}` should be a mapping with `type` and `patterns`."})
            continue
        patterns = [str(item) for item in spec.get("patterns") or []]
        if not patterns:
            problems.append({"severity": "warning", "type": "empty_event", "message": f"Event `{name}` has no patterns."})
            suggestions.append(f"Add distinctive patterns to event `{name}` or remove it.")
        for pattern in patterns:
            if _is_generic_profile_token(pattern):
                problems.append({"severity": "warning", "type": "generic_event_pattern", "message": f"Event `{name}` pattern `{pattern}` is too generic."})
                suggestions.append(f"Replace `{pattern}` with a more distinctive phrase for event `{name}`.")

    for sequence_name, sequence in expected_sequences.items():
        if not isinstance(sequence, list):
            problems.append({"severity": "warning", "type": "invalid_sequence", "message": f"Expected sequence `{sequence_name}` should be a list."})
            continue
        for label in sequence:
            label_text = str(label)
            if label_text not in defined_event_names:
                problems.append({"severity": "warning", "type": "undefined_sequence_event", "message": f"Expected sequence `{sequence_name}` references `{label_text}`, but no profile event with that name/type is defined."})
                suggestions.append(f"Define event `{label_text}` or remove it from sequence `{sequence_name}`.")

    if log_file:
        raw_text = Path(log_file).read_text(encoding="utf-8", errors="ignore")
        filtered_text = _filter_text_for_query(raw_text, query)
        for name, spec in events.items():
            if not isinstance(spec, dict):
                continue
            count = sum(_count_pattern_hits(filtered_text, str(pattern)) for pattern in spec.get("patterns") or [])
            event_match_counts[name] = count
            if count == 0:
                problems.append({"severity": "warning", "type": "unmatched_event", "message": f"Event `{name}` did not match the analyzed log slice."})
                suggestions.append(f"Check patterns for event `{name}` or analyze a wider log window.")
        for key in profile.get("session_keys") or []:
            key_text = str(key)
            session_key_counts[key_text] = len(re.findall(rf"\b{re.escape(key_text)}\b\s*[=:]", filtered_text))
            if session_key_counts[key_text] == 0:
                problems.append({"severity": "info", "type": "unused_session_key", "message": f"Session key `{key_text}` was not observed in the analyzed log slice."})
        try:
            report = analyze_log(index_path, log_file, project=project_path, app_only=not all_lines, context=0, query=query)
            profile_warnings = report.get("profile_warnings", [])
            for warning in profile_warnings:
                normalized_warning = _normalize_profile_warning(warning, event_match_counts)
                problems.append(normalized_warning)
                if normalized_warning.get("type") == "sequence_session_mismatch":
                    suggestions.append("Split the expected sequence or add a shared correlation/session key before treating the missing event as a failure.")
                elif warning.get("suggestion"):
                    suggestions.append(str(warning["suggestion"]))
        except Exception as exc:
            problems.append({"severity": "warning", "type": "analysis_failed", "message": f"Could not run log-based profile validation: {exc}"})

    suggestions = _dedupe_strings(suggestions)
    return _profile_lint_payload(project_path, profile_path, problems, suggestions, event_match_counts, session_key_counts, profile_warnings, log_file, query, fix_suggest=fix_suggest)


def render_profile_lint_report(report: dict[str, Any]) -> str:
    lines = [
        "# LogGraph Profile Lint",
        "",
        f"Project: `{report.get('project')}`",
        f"Profile: `{report.get('profile')}`",
    ]
    if report.get("log_file"):
        lines.append(f"Log file: `{report.get('log_file')}`")
    if report.get("query"):
        lines.append(f"Query: `{report.get('query')}`")
    counts = report.get("counts", {})
    lines.extend([
        "",
        "## Summary",
        f"- Problems: {len(report.get('problems', []))}",
        f"- Event rules checked: {counts.get('events', 0)}",
        f"- Expected sequences checked: {counts.get('expected_sequences', 0)}",
    ])
    lines.extend(["", "## Problems"])
    for problem in report.get("problems", []):
        lines.append(f"- [{problem.get('severity', 'warning')}] {problem.get('message', '')}")
    if not report.get("problems"):
        lines.append("- No profile quality problems detected by generic lint rules.")
    if report.get("event_match_counts"):
        lines.extend(["", "## Event pattern matches"])
        for name, count in sorted(report["event_match_counts"].items()):
            lines.append(f"- `{name}`: {count}")
    if report.get("session_key_counts"):
        lines.extend(["", "## Session key observations"])
        for key, count in sorted(report["session_key_counts"].items()):
            lines.append(f"- `{key}`: {count}")
    lines.extend(["", "## Suggestions"])
    lines.extend([f"- {item}" for item in report.get("suggestions", [])] or ["- No immediate profile changes suggested."])
    if report.get("fix_suggestions"):
        lines.extend(["", "## Suggested cleanup"])
        for item in report["fix_suggestions"]:
            target = f" `{item.get('target')}`" if item.get("target") else ""
            lines.append(f"- **{item.get('action')}**{target}: {item.get('reason')}")
    cleanup_patch = report.get("cleanup_patch") or {}
    if cleanup_patch:
        lines.extend(["", "## Cleanup patch preview"])
        for key, values in cleanup_patch.items():
            if not values:
                continue
            lines.append(f"- `{key}`:")
            if isinstance(values, list):
                for value in values:
                    lines.append(f"  - `{value}`")
            elif isinstance(values, dict):
                for name, items in values.items():
                    lines.append(f"  - `{name}`: {', '.join(str(item) for item in items)}")
        lines.append("- This is a review-only cleanup patch; LogGraph does not apply deletions automatically.")
    return "\n".join(lines)


def _profile_lint_payload(project: Path, profile_path: Path, problems: list[dict[str, Any]], suggestions: list[str], event_match_counts: dict[str, int], session_key_counts: dict[str, int], profile_warnings: list[dict[str, Any]], log_file: str | Path | None, query: str, *, fix_suggest: bool = False) -> dict[str, Any]:
    profile = load_project_profile(project)
    unique_problems = _dedupe_problem_dicts(problems)
    return {
        "project": str(project.resolve()),
        "profile": str(profile_path),
        "profile_exists": profile_path.exists(),
        "log_file": str(log_file) if log_file else "",
        "query": query,
        "counts": {
            "app_identifiers": len(profile.get("app_identifiers") or []),
            "session_keys": len(profile.get("session_keys") or []),
            "events": len(profile.get("events") or {}),
            "expected_sequences": len(profile.get("expected_sequences") or {}),
        },
        "problems": unique_problems,
        "suggestions": _dedupe_strings(suggestions),
        "fix_suggestions": generate_profile_fix_suggestions(unique_problems, event_match_counts, session_key_counts) if fix_suggest else [],
        "cleanup_patch": generate_cleanup_patch(unique_problems, event_match_counts) if fix_suggest else {},
        "event_match_counts": event_match_counts,
        "session_key_counts": session_key_counts,
        "profile_warnings": profile_warnings,
    }


def generate_profile_fix_suggestions(problems: list[dict[str, Any]], event_match_counts: dict[str, int], session_key_counts: dict[str, int]) -> list[dict[str, str]]:
    fixes: list[dict[str, str]] = []
    for problem in problems:
        message = str(problem.get("message", ""))
        problem_type = str(problem.get("type", ""))
        if problem_type == "unused_session_key":
            key = _extract_backtick_value(message)
            if key:
                fixes.append({"action": "review_remove_session_key", "target": key, "reason": "It was not observed in this log slice; remove it only if absent from representative logs."})
        elif problem_type == "suspicious_session_key":
            key = _extract_backtick_value(message)
            if key:
                fixes.append({"action": "remove_session_key", "target": key, "reason": "It looks like a method/function name rather than a stable correlation field."})
        elif problem_type == "generic_event_pattern":
            values = re.findall(r"`([^`]+)`", message)
            if len(values) >= 2:
                fixes.append({"action": "narrow_event_pattern", "target": values[0], "reason": f"Pattern `{values[1]}` is too generic and may over-match unrelated logs."})
        elif problem_type == "generic_alias":
            values = re.findall(r"`([^`]+)`", message)
            if len(values) >= 2:
                fixes.append({"action": "remove_or_narrow_alias", "target": values[1], "reason": f"Alias under entity `{values[0]}` is too generic."})
        elif problem_type == "unmatched_event":
            event = _extract_backtick_value(message)
            if event and event_match_counts.get(event, 0) == 0:
                fixes.append({"action": "review_event_patterns", "target": event, "reason": "No patterns matched this log slice; update the patterns or remove the event if it is not relevant."})
        elif problem_type == "sequence_session_mismatch":
            event = _extract_backtick_value(message)
            fixes.append({"action": "review_expected_sequence", "target": event, "reason": "The event matched logs but not the same session/timeline; split the sequence or add a shared correlation key."})
        elif problem_type == "undefined_sequence_event":
            values = re.findall(r"`([^`]+)`", message)
            if len(values) >= 2:
                fixes.append({"action": "remove_or_define_sequence_event", "target": values[1], "reason": f"Sequence `{values[0]}` references an event not defined in profile."})
    return _dedupe_fix_suggestions(fixes)


def generate_cleanup_patch(problems: list[dict[str, Any]], event_match_counts: dict[str, int]) -> dict[str, Any]:
    """Build a conservative, review-only deletion patch from lint findings.

    The patch is intentionally structural JSON, not a YAML diff, because LogGraph's
    profile merge path is additive. A future cleanup apply command can consume this
    after explicit user review.
    """
    remove_session_keys: list[str] = []
    review_events: list[str] = []
    remove_events: list[str] = []
    review_sequences: dict[str, list[str]] = {}
    for problem in problems:
        problem_type = str(problem.get("type", ""))
        message = str(problem.get("message", ""))
        values = re.findall(r"`([^`]+)`", message)
        if problem_type == "suspicious_session_key" and values:
            remove_session_keys.append(values[0])
        elif problem_type == "unused_session_key" and values:
            # Unused in one log slice is not enough for automatic deletion; mark review-only.
            remove_session_keys.append(values[0])
        elif problem_type == "unmatched_event" and values:
            event = values[0]
            if event_match_counts.get(event, 0) == 0:
                review_events.append(event)
        elif problem_type in {"empty_event", "invalid_event"} and values:
            remove_events.append(values[0])
        elif problem_type == "sequence_session_mismatch" and values:
            sequence = str(problem.get("sequence") or "__unknown_sequence__")
            review_sequences.setdefault(sequence, []).append(values[0])
        elif problem_type == "undefined_sequence_event" and len(values) >= 2:
            review_sequences.setdefault(values[0], []).append(values[1])
    patch: dict[str, Any] = {}
    if remove_session_keys:
        patch["remove_session_keys"] = _dedupe_strings(remove_session_keys)
    if remove_events:
        patch["remove_events"] = _dedupe_strings(remove_events)
    if review_events:
        patch["review_events"] = _dedupe_strings(review_events)
    if review_sequences:
        patch["review_sequences"] = {name: _dedupe_strings(items) for name, items in review_sequences.items()}
    return patch


def _extract_backtick_value(message: str) -> str:
    match = re.search(r"`([^`]+)`", message)
    return match.group(1) if match else ""


def _dedupe_fix_suggestions(items: list[dict[str, str]]) -> list[dict[str, str]]:
    seen = set()
    unique = []
    for item in items:
        key = (item.get("action"), item.get("target"), item.get("reason"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _normalize_profile_warning(warning: dict[str, Any], event_match_counts: dict[str, int]) -> dict[str, Any]:
    message = str(warning.get("message", ""))
    warning_type = str(warning.get("type", "profile_warning"))
    if warning_type == "sequence_unobserved_event":
        match = re.search(r"references `([^`]+)`", message)
        label = match.group(1) if match else ""
        if label and event_match_counts.get(label, 0) > 0:
            return {
                "severity": "warning",
                "type": "sequence_session_mismatch",
                "sequence": warning.get("sequence", ""),
                "message": f"Expected sequence references `{label}`, and its patterns match the log, but it was not observed in the same session/timeline. The sequence may be over-broad or use mismatched correlation keys.",
            }
    return {"severity": "warning", "type": warning_type, "sequence": warning.get("sequence", ""), "message": message}


def _filter_text_for_query(text: str, query: str) -> str:
    terms = [term.lower() for term in re.findall(r"[A-Za-z0-9_\u4e00-\u9fff-]{2,}", query or "")]
    if not terms:
        return text
    lines = [line for line in text.splitlines() if any(term in line.lower() for term in terms)]
    return "\n".join(lines) if lines else text


def _count_pattern_hits(text: str, pattern: str) -> int:
    if not pattern:
        return 0
    return text.lower().count(pattern.lower())


def _looks_like_function_name(value: str) -> bool:
    return bool(re.search(r"[a-z][A-Za-z0-9]*[A-Z][A-Za-z0-9]*", value)) and len(value) > 14


def _is_generic_profile_token(value: str) -> bool:
    normalized = normalize_text(value).strip().lower()
    return normalized in GENERIC_PROFILE_TOKENS or (len(normalized) <= 2 and normalized.isascii())


def _dedupe_problem_dicts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    unique = []
    for item in items:
        key = (item.get("type"), item.get("message"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _dedupe_strings(items: list[str]) -> list[str]:
    seen = set()
    unique = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def load_cleanup_patch(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("cleanup_patch"), dict):
        return data["cleanup_patch"]
    return data if isinstance(data, dict) else {}


def cleanup_profile(project: str | Path, patch: dict[str, Any], *, apply: bool = False) -> dict[str, Any]:
    project_path = Path(project)
    profile_path = default_profile_path(project_path)
    profile = load_project_profile(project_path)
    remove_session_keys = [str(item) for item in patch.get("remove_session_keys") or []]
    remove_events = [str(item) for item in patch.get("remove_events") or []]
    review_events = [str(item) for item in patch.get("review_events") or []]
    review_sequences = patch.get("review_sequences") or {}

    before = {
        "session_keys": list(profile.get("session_keys") or []),
        "events": sorted((profile.get("events") or {}).keys()),
    }
    updated = dict(profile)
    updated["session_keys"] = [key for key in (profile.get("session_keys") or []) if str(key) not in set(remove_session_keys)]
    events = dict(profile.get("events") or {})
    for name in remove_events:
        events.pop(name, None)
    updated["events"] = events
    after = {
        "session_keys": list(updated.get("session_keys") or []),
        "events": sorted((updated.get("events") or {}).keys()),
    }
    applied = False
    if apply:
        from loggraph.profile import render_manual_profile
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_text(render_manual_profile(updated), encoding="utf-8")
        applied = True
    return {
        "project": str(project_path.resolve()),
        "profile": str(profile_path),
        "applied": applied,
        "removed": {
            "session_keys": [key for key in before["session_keys"] if key not in after["session_keys"]],
            "events": [name for name in before["events"] if name not in after["events"]],
        },
        "review_only": {
            "events": review_events,
            "sequences": review_sequences,
        },
        "before": before,
        "after": after,
    }


def diagnose_project(project: str | Path, index_path: str | Path, log_file: str | Path, *, query: str = "", all_lines: bool = False) -> dict[str, Any]:
    from loggraph.analyzer import analyze_log
    doctor = doctor_project(project, index_path, log_file=log_file, query=query, all_lines=all_lines)
    analysis = analyze_log(index_path, log_file, project=project, app_only=not all_lines, query=query, context=2, source_context=3, detail="normal")
    lint = lint_profile(project, index_path, log_file=log_file, query=query, all_lines=all_lines, fix_suggest=True)
    report = {
        "project": str(Path(project).resolve()),
        "log_file": str(log_file),
        "query": query,
        "doctor": doctor,
        "diagnosis": analysis.get("diagnosis", {}),
        "evidence_trace": analysis.get("evidence_trace", []),
        "profile_lint": lint,
        "top_matches": analysis.get("matches", [])[:5],
        "recommended_next": diagnose_recommendations(doctor, analysis, lint),
    }
    report["report_markdown"] = render_diagnosis_report(report)
    return report


def diagnose_recommendations(doctor: dict[str, Any], analysis: dict[str, Any], lint: dict[str, Any]) -> list[str]:
    recommendations: list[str] = []
    if doctor.get("index_stale"):
        recommendations.append("Rebuild the index with `loggraph init <project>` before trusting source locations.")
    source_rows = []
    for step in analysis.get("evidence_trace", []):
        label = str(step.get("label", ""))
        match = re.search(r"`([^`]+)`", label)
        if match and match.group(1) not in source_rows:
            source_rows.append(match.group(1))
    if source_rows:
        recommendations.append(f"Inspect source path around: {', '.join(source_rows[:3])}.")
    cleanup = lint.get("cleanup_patch") or {}
    if cleanup:
        recommendations.append("Review the cleanup_patch with `loggraph profile cleanup <project> --patch cleanup.json --dry-run` before applying safe removals.")
    if lint.get("problems"):
        recommendations.append("Review profile lint warnings before treating missing expected events as product bugs.")
    return recommendations or ["No immediate LogGraph follow-up required; inspect the top evidence trace manually."]


def render_diagnosis_report(report: dict[str, Any]) -> str:
    doctor = report.get("doctor", {})
    diagnosis = report.get("diagnosis", {})
    lint = report.get("profile_lint", {})
    lines = [
        "# LogGraph Diagnosis",
        "",
        f"Project: `{report.get('project')}`",
        f"Log file: `{report.get('log_file')}`",
    ]
    if report.get("query"):
        lines.append(f"Query: `{report.get('query')}`")
    lines.extend([
        "",
        "## Health",
        f"- Index exists: {doctor.get('index_exists')}",
        f"- Index stale: {doctor.get('index_stale')}",
        f"- Profile exists: {doctor.get('profile_exists')}",
        f"- Log sites: {doctor.get('log_sites', 0)}",
    ])
    health = doctor.get("profile_health") or {}
    if health:
        lines.extend([
            f"- Profile events: {health.get('events', 0)}",
            f"- Profile session keys: {health.get('session_keys', 0)}",
            f"- Profile expected sequences: {health.get('expected_sequences', 0)}",
        ])
    lines.extend(["", "## Diagnosis"])
    if diagnosis.get("summary"):
        lines.append(f"- {diagnosis['summary']}")
    for item in diagnosis.get("findings", [])[:6]:
        lines.append(f"- {item}")
    trace = report.get("evidence_trace") or []
    if trace:
        lines.extend(["", "## Evidence trace"])
        for idx, step in enumerate(trace[:8], 1):
            detail = f" — {step.get('detail')}" if step.get("detail") else ""
            lines.append(f"{idx}. {step.get('label')}{detail}")
            if step.get("source"):
                lines.append(f"   - source: `{step['source']}`")
            if step.get("line"):
                lines.append(f"   - log line: {step['line']}")
    lines.extend([
        "",
        "## Profile issues",
        f"- Problems: {len(lint.get('problems', []))}",
        f"- Cleanup candidates: {sum(len(v) if isinstance(v, list) else sum(len(items) for items in v.values()) for v in (lint.get('cleanup_patch') or {}).values())}",
    ])
    for problem in lint.get("problems", [])[:8]:
        lines.append(f"- [{problem.get('severity')}] {problem.get('message')}")
    cleanup = lint.get("cleanup_patch") or {}
    if cleanup:
        lines.extend(["", "## Cleanup patch preview"])
        for key, values in cleanup.items():
            lines.append(f"- `{key}`: {values}")
    lines.extend(["", "## Recommended next"])
    lines.extend([f"{idx}. {item}" for idx, item in enumerate(report.get("recommended_next", []), 1)])
    return "\n".join(lines)


def render_cleanup_report(report: dict[str, Any]) -> str:
    mode = "Applied" if report.get("applied") else "Dry run"
    lines = [
        "# LogGraph Profile Cleanup",
        "",
        f"Mode: {mode}",
        f"Profile: `{report.get('profile')}`",
        "",
        "## Safe removals",
    ]
    removed = report.get("removed", {})
    lines.append("### Session keys")
    lines.extend([f"- `{item}`" for item in removed.get("session_keys", [])] or ["- none"])
    lines.append("### Events")
    lines.extend([f"- `{item}`" for item in removed.get("events", [])] or ["- none"])
    review = report.get("review_only", {})
    if review.get("events") or review.get("sequences"):
        lines.extend(["", "## Review-only items"])
        if review.get("events"):
            lines.append("### Events to review")
            lines.extend([f"- `{item}`" for item in review.get("events", [])])
        if review.get("sequences"):
            lines.append("### Sequences to review")
            for name, items in review.get("sequences", {}).items():
                lines.append(f"- `{name}`: {', '.join(str(item) for item in items)}")
        lines.append("These were not modified automatically.")
    return "\n".join(lines)


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


def refine_profile(index_path: str | Path, log_file: str | Path, *, project: str | Path, all_lines: bool = False, query: str = "") -> dict[str, Any]:
    report = analyze_log(index_path, log_file, project=project, app_only=not all_lines, context=0, query=query)
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
        "app_identifiers": suggest_app_identifiers(project),
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


def sequence_from_log(index_path: str | Path, log_file: str | Path, *, project: str | Path, name: str, all_lines: bool = False, query: str = "") -> dict[str, Any]:
    report = analyze_log(index_path, log_file, project=project, app_only=not all_lines, context=0, query=query)
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
