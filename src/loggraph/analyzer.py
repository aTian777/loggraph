from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from loggraph.events import extract_event, summarize_events
from loggraph.graph.store import load_index
from loggraph.logs.parser import parse_log_block
from loggraph.matchers.locator import Locator
from loggraph.profile import load_project_profile, merge_profiles

APP_TAG_HINTS = ("smart-recyclable---->", "插桩检测-红包", "Log日志", "BaseViewModel", "com.hlkj.rvm")
LOGCAT_HEADER_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3}\s+\d+-\d+\s+\S+\s+(?P<package>\S+)\s+[VDIWEF]\b")
LOGCAT_CONTINUATION_PATTERN = re.compile(r"^\s*[│|]\s?(?P<msg>.*)$")


def default_cache_dir(project_root: str | Path) -> Path:
    return Path(project_root) / ".loggraph"


def default_index_path(project_root: str | Path) -> Path:
    return default_cache_dir(project_root) / "index.json"


def analyze_log(index_path: str | Path, log_file: str | Path, *, top: int = 3, app_only: bool = True, project: str | Path | None = None, context: int = 0, source_context: int = 3, detail: str = "normal", query: str = "") -> dict:
    index = load_index(index_path)
    locator = Locator(index)
    path = Path(log_file)
    lines = path.read_text(errors="ignore").splitlines()
    project_root = Path(project) if project else Path(index.root or Path(index_path).parent.parent)
    event_profile = merge_profiles(index.metadata.get("event_profile", {}), load_project_profile(project_root))

    query_tokens = query_terms(query)
    matches = []
    events = []
    analyzed_lines = 0
    for no, raw in iter_logical_log_entries(lines, app_only=app_only):
        if query_tokens and not text_matches_query(raw, query_tokens):
            continue
        analyzed_lines += 1
        entry = parse_log_block(raw)
        if event := extract_event(entry, no, event_profile):
            events.append(event)
        candidates = locator.locate(entry, top=top)
        if candidates:
            item = {
                "line": no,
                "log": raw,
                "candidates": enrich_candidates([asdict(c) for c in candidates], source_context=source_context),
            }
            if context > 0:
                item["context"] = context_window(lines, no, context)
            matches.append(item)

    delivery_posts = extract_delivery_posts(lines)
    completed_rounds = [
        {"line": no, "time": line[:18], "log": line}
        for no, line in enumerate(lines, 1)
        if "一轮投递流程结束" in line
    ]

    runtime_findings = summarize_events(events, profile=event_profile)
    runtime_findings["hypotheses"] = generate_hypotheses(runtime_findings)
    context_windows = build_context_windows(lines, runtime_findings, matches, context=context)

    return {
        "index_path": str(index_path),
        "log_file": str(path),
        "index_summary": {
            "functions": len(index.functions),
            "calls": len(index.calls),
            "log_sites": len(index.log_sites),
        },
        "analyzed_lines": analyzed_lines,
        "matched_log_lines": len(matches),
        "matches": matches,
        "event_profile_summary": {
            "source": event_profile.get("source", "none"),
            "manual_profile": event_profile.get("manual_profile", False),
            "learned_patterns": len(event_profile.get("learned_patterns", [])),
            "session_keys": event_profile.get("session_keys", [])[:10],
            "states": event_profile.get("states", [])[:10],
        },
        "runtime_findings": runtime_findings,
        "context_windows": context_windows,
        "report_markdown": render_report(
            log_file=str(path),
            index_path=str(index_path),
            analyzed_lines=analyzed_lines,
            matches=matches,
            runtime_findings=runtime_findings,
            context_windows=context_windows,
            max_matches=top,
            detail=detail,
        ),
        "domain_findings": {
            "delivery_posts": delivery_posts,
            "completed_rounds": completed_rounds,
            "bottle_count_from_rty_sum": sum(item["rty"] for item in delivery_posts),
        },
    }


def iter_logical_log_entries(lines: list[str], *, app_only: bool) -> list[tuple[int, str]]:
    entries: list[tuple[int, str]] = []
    current_no = 0
    current_lines: list[str] = []
    current_is_app = False

    def flush() -> None:
        if current_lines and (not app_only or current_is_app):
            entries.append((current_no, "\n".join(current_lines)))

    for no, line in enumerate(lines, 1):
        header_match = LOGCAT_HEADER_PATTERN.match(line)
        is_header = bool(header_match)
        if is_header:
            flush()
            current_no = no
            current_lines = [line]
            package_name = header_match.group("package") if header_match else ""
            current_is_app = package_name == "com.hlkj.rvm" or any(hint != "com.hlkj.rvm" and hint in line for hint in APP_TAG_HINTS)
            continue
        if current_lines:
            current_lines.append(line)
            continue
        if not app_only or any(hint in line for hint in APP_TAG_HINTS):
            entries.append((no, line))
    flush()
    return entries


def extract_delivery_posts(lines: list[str]) -> list[dict]:
    posts = []
    for no, line in enumerate(lines, 1):
        if "/mqtt/hyfr-rp?" not in line or "--> POST " not in line:
            continue
        url = line.split("--> POST ", 1)[1].split(" http/", 1)[0]
        query = parse_qs(urlparse(url).query)
        posts.append({
            "line": no,
            "time": line[:18],
            "rty": _to_int(query.get("rty", ["0"])[0]),
            "c": query.get("c", [""])[0],
            "wg": _to_int(query.get("wg", ["0"])[0]),
            "th": query.get("th", [""])[0],
            "ty": query.get("ty", [""])[0],
            "sn": query.get("sn", [""])[0],
            "url": url,
        })
    return posts


def write_analysis(report: dict, out: str | Path) -> None:
    Path(out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def compare_logs(index_path: str | Path, baseline_log: str | Path, target_log: str | Path, *, project: str | Path | None = None, top: int = 3, app_only: bool = True, context: int = 0, detail: str = "normal", query: str = "") -> dict:
    baseline = analyze_log(index_path, baseline_log, top=top, app_only=app_only, project=project, context=context, detail=detail, query=query)
    target = analyze_log(index_path, target_log, top=top, app_only=app_only, project=project, context=context, detail=detail, query=query)
    baseline_labels = _timeline_labels(baseline)
    target_labels = _timeline_labels(target)
    missing = [label for label in baseline_labels if label not in target_labels]
    extra = [label for label in target_labels if label not in baseline_labels]
    shared = [label for label in target_labels if label in baseline_labels]
    session_comparisons = compare_sessions(baseline, target)
    duration_anomalies = compare_duration_stats(baseline, target)
    result = {
        "baseline_log": str(baseline_log),
        "target_log": str(target_log),
        "baseline_summary": compact_summary(baseline, max_matches=5),
        "target_summary": compact_summary(target, max_matches=5),
        "shared_events": shared,
        "missing_in_target": missing,
        "extra_in_target": extra,
        "session_comparisons": session_comparisons,
        "duration_anomalies": duration_anomalies,
        "hypotheses": generate_compare_hypotheses(session_comparisons, duration_anomalies, target),
    }
    result["report_markdown"] = render_compare_report(result)
    return result


def compact_summary(report: dict, *, max_matches: int = 10) -> dict:
    return {
        "index_summary": report["index_summary"],
        "analyzed_lines": report.get("analyzed_lines", 0),
        "matched_log_lines": report["matched_log_lines"],
        "top_matches": report["matches"][:max_matches],
        "event_profile_summary": report.get("event_profile_summary", {}),
        "runtime_findings": report.get("runtime_findings", {}),
        "context_windows": report.get("context_windows", []),
        "domain_findings": report["domain_findings"],
        "report_markdown": report.get("report_markdown", ""),
    }


def render_report(*, log_file: str, index_path: str, analyzed_lines: int, matches: list[dict], runtime_findings: dict, context_windows: list[dict] | None = None, max_matches: int = 3, detail: str = "normal") -> str:
    lines = [
        "# LogGraph Findings",
        "",
        "## Summary",
        f"- Log file: `{log_file}`",
        f"- Index: `{index_path}`",
        f"- Analyzed lines: {analyzed_lines}",
        f"- Matched source-bearing log lines: {len(matches)}",
        f"- Extracted runtime events: {runtime_findings.get('event_count', 0)}",
        "",
        "## Key runtime evidence",
    ]
    suspicious = runtime_findings.get("suspicious_events", [])
    if suspicious:
        for event in suspicious[:10]:
            label = f"line {event.get('line')}: {event.get('type')}"
            evidence = ", ".join(event.get("evidence") or [])
            suffix = f" ({evidence})" if evidence else ""
            lines.append(f"- {label}{suffix} — {event.get('message', '')}")
    else:
        lines.append("- No obvious error/exception/timeout/retry events extracted by generic rules.")

    lines.extend(["", "## Likely source areas"])
    source_rows = _top_source_rows(matches, max_rows=max_matches)
    if source_rows:
        for idx, row in enumerate(source_rows, 1):
            lines.append(f"{idx}. `{row['function']}` — `{row['file']}:{row['line']}` ({row['score']:.1f})")
            for reason in row["reasons"][:2]:
                lines.append(f"   - {reason}")
            if detail in {"normal", "full"} and row.get("source_excerpt"):
                excerpt = row["source_excerpt"]
                lines.append(f"   - excerpt lines {excerpt['start_line']}-{excerpt['end_line']}")
    else:
        lines.append("- No source candidates matched. Consider running with `--all-lines` or refreshing the index.")

    if detail == "brief":
        return "\n".join(lines)

    session_timelines = runtime_findings.get("session_timelines", [])
    if session_timelines:
        lines.extend(["", "## Session timelines"])
        for session in session_timelines[:5]:
            label = f"{session.get('session_key')}={session.get('session_id')}" if session.get("session_id") else "global"
            lines.append(f"### {label}")
            for event in session.get("events", [])[:8]:
                lines.append(f"- line {event.get('line')}: {event.get('type')} — {event.get('message', '')}")
    duration_stats = runtime_findings.get("duration_stats", [])
    if duration_stats:
        lines.extend(["", "## Duration observations"])
        for item in duration_stats[:10]:
            lines.append(f"- `{item['label']}` avg {item['avg_ms']:.1f}ms, max {item['max_ms']:.1f}ms ({item['count']} sample(s))")

    hypotheses = runtime_findings.get("hypotheses", [])
    if hypotheses:
        lines.extend(["", "## Hypotheses"])
        for item in hypotheses[:10]:
            lines.append(f"- **{item['title']}** (confidence {item['confidence']:.2f})")
            for evidence in item.get("evidence", [])[:3]:
                lines.append(f"  - {evidence}")

    missing = runtime_findings.get("missing_events", [])
    if missing:
        lines.extend(["", "## Missing expected events"])
        for item in missing[:10]:
            session = item.get("session_id") or "global"
            lines.append(f"- session `{session}` sequence `{item.get('sequence')}` missing: {', '.join(item.get('missing', []))}")

    if detail == "full" and context_windows:
        lines.extend(["", "## Context windows"])
        for window in context_windows[:5]:
            lines.append(f"### Around line {window['line']}")
            for row in window["lines"]:
                marker = ">" if row["line"] == window["line"] else " "
                lines.append(f"{marker} {row['line']}: {row['text']}")

    lines.extend(["", "## Suggested next actions"])
    if source_rows:
        focus = " ".join(row["function"].split(".")[-1] for row in source_rows[:3])
        lines.append(f"- AI agent: inspect the source candidates above and explain the runtime path around `{focus}`.")
        lines.append(f"- CodeGraph/manual query suggestion: `{focus}`")
    else:
        lines.append("- Broaden log parsing with `--all-lines`, then inspect high-severity events and nearby timestamps.")
    suggestions = runtime_findings.get("suggested_event_rules", [])
    if suggestions:
        lines.append("- Promote recurring vocabulary to a future `.loggraph/profile.yaml` rule if it is meaningful:")
        for item in suggestions[:5]:
            lines.append(f"  - `{item['pattern']}` ({item['count']} hits)")
    return "\n".join(lines)


def _top_source_rows(matches: list[dict], *, max_rows: int) -> list[dict]:
    best: dict[str, dict] = {}
    for match in matches:
        for cand in match.get("candidates", []):
            fid = cand.get("function_id") or cand.get("function")
            if not fid:
                continue
            prev = best.get(fid)
            if prev is None or cand.get("score", 0) > prev.get("score", 0):
                best[fid] = cand
    return sorted(best.values(), key=lambda c: (-c.get("score", 0), c.get("file", ""), c.get("line", 0)))[:max_rows]


def enrich_candidates(candidates: list[dict], *, source_context: int) -> list[dict]:
    for cand in candidates:
        if source_context <= 0:
            continue
        path = Path(cand.get("file", ""))
        line = int(cand.get("line") or 0)
        if not path.exists() or line <= 0:
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue
        start = max(1, line - source_context)
        end = min(len(lines), line + source_context)
        cand["source_excerpt"] = {
            "file": str(path),
            "start_line": start,
            "end_line": end,
            "text": "\n".join(f"{idx}: {lines[idx - 1]}" for idx in range(start, end + 1)),
        }
    return candidates


def context_window(lines: list[str], line_no: int, radius: int) -> list[dict]:
    start = max(1, line_no - radius)
    end = min(len(lines), line_no + radius)
    return [{"line": idx, "text": lines[idx - 1]} for idx in range(start, end + 1)]


def build_context_windows(lines: list[str], runtime_findings: dict, matches: list[dict], *, context: int) -> list[dict]:
    if context <= 0:
        return []
    wanted = []
    for event in runtime_findings.get("suspicious_events", [])[:10]:
        if event.get("line"):
            wanted.append(int(event["line"]))
    for match in matches[:10]:
        if match.get("line"):
            wanted.append(int(match["line"]))
    windows = []
    seen = set()
    for line_no in wanted:
        if line_no in seen:
            continue
        seen.add(line_no)
        windows.append({"line": line_no, "lines": context_window(lines, line_no, context)})
    return windows


def _timeline_labels(report: dict) -> list[str]:
    labels = []
    for timeline in report.get("runtime_findings", {}).get("session_timelines", []):
        labels.extend(str(label) for label in timeline.get("labels", []))
    if not labels:
        labels = list(report.get("runtime_findings", {}).get("event_types", {}).keys())
    return labels


def render_compare_report(result: dict) -> str:
    lines = [
        "# LogGraph Compare Report",
        "",
        "## Inputs",
        f"- Baseline: `{result['baseline_log']}`",
        f"- Target: `{result['target_log']}`",
    ]
    hypotheses = result.get("hypotheses", [])
    if hypotheses:
        lines.extend(["", "## Hypotheses"])
        for item in hypotheses[:10]:
            lines.append(f"- **{item['title']}** (confidence {item['confidence']:.2f})")
            for evidence in item.get("evidence", [])[:3]:
                lines.append(f"  - {evidence}")
    lines.extend(["", "## Session comparisons"])
    for item in result.get("session_comparisons", [])[:10]:
        lines.append(f"### baseline `{item.get('baseline_session') or 'global'}` vs target `{item.get('target_session') or 'global'}`")
        lines.append(f"- shared: {', '.join(item.get('shared', [])) or 'none'}")
        lines.append(f"- missing in target: {', '.join(item.get('missing_in_target', [])) or 'none'}")
        lines.append(f"- extra in target: {', '.join(item.get('extra_in_target', [])) or 'none'}")
    duration_anomalies = result.get("duration_anomalies", [])
    if duration_anomalies:
        lines.extend(["", "## Duration anomalies"])
        for item in duration_anomalies[:10]:
            lines.append(f"- `{item['label']}` baseline {item['baseline_avg_ms']:.1f}ms → target {item['target_avg_ms']:.1f}ms ({item['ratio']:.1f}x, +{item['delta_ms']:.1f}ms)")
    lines.extend(["", "## Shared events"])
    shared = result.get("shared_events", [])
    lines.extend([f"- {item}" for item in shared[:20]] or ["- No shared events detected."])
    lines.extend(["", "## Missing in target"])
    missing = result.get("missing_in_target", [])
    lines.extend([f"- {item}" for item in missing[:20]] or ["- No baseline events missing from target."])
    lines.extend(["", "## Extra in target"])
    extra = result.get("extra_in_target", [])
    lines.extend([f"- {item}" for item in extra[:20]] or ["- No extra target events detected."])
    target_missing = result.get("target_summary", {}).get("runtime_findings", {}).get("missing_events", [])
    if target_missing:
        lines.extend(["", "## Target missing expected events"])
        for item in target_missing[:10]:
            lines.append(f"- session `{item.get('session_id') or 'global'}` sequence `{item.get('sequence')}` missing: {', '.join(item.get('missing', []))}")
    return "\n".join(lines)


def generate_hypotheses(runtime_findings: dict) -> list[dict]:
    hypotheses = []
    event_types = runtime_findings.get("event_types", {})
    missing = runtime_findings.get("missing_events", [])
    if missing and event_types.get("timeout"):
        hypotheses.append({
            "title": "Expected follow-up event missing before timeout",
            "confidence": 0.82,
            "evidence": [
                f"timeout events observed: {event_types.get('timeout', 0)}",
                f"missing expected events: {', '.join(sorted({m for item in missing for m in item.get('missing', [])}))}",
            ],
        })
    if event_types.get("retry", 0) >= 2:
        hypotheses.append({
            "title": "Retry loop or repeated recovery path",
            "confidence": 0.68,
            "evidence": [f"retry events observed: {event_types.get('retry', 0)}"],
        })
    if event_types.get("exception"):
        hypotheses.append({
            "title": "Unhandled exception path observed",
            "confidence": 0.74,
            "evidence": [f"exception events observed: {event_types.get('exception', 0)}"],
        })
    for stat in runtime_findings.get("duration_stats", []):
        if stat.get("max_ms", 0) >= 10_000:
            hypotheses.append({
                "title": f"Long-running stage `{stat['label']}`",
                "confidence": 0.62,
                "evidence": [f"max duration {stat['max_ms']:.1f}ms, avg {stat['avg_ms']:.1f}ms"],
            })
    return hypotheses


def compare_sessions(baseline: dict, target: dict) -> list[dict]:
    baseline_sessions = baseline.get("runtime_findings", {}).get("session_timelines", [])
    target_sessions = target.get("runtime_findings", {}).get("session_timelines", [])
    if not baseline_sessions and not target_sessions:
        return []
    target_by_id = {item.get("session_id", ""): item for item in target_sessions}
    comparisons = []
    used_targets = set()
    for idx, base in enumerate(baseline_sessions or [{"session_id": "", "labels": _timeline_labels(baseline)}]):
        base_id = base.get("session_id", "")
        target_item = target_by_id.get(base_id)
        if not target_item and idx < len(target_sessions):
            target_item = target_sessions[idx]
        if not target_item:
            target_item = {"session_id": "", "labels": []}
        used_targets.add(target_item.get("session_id", ""))
        base_labels = list(base.get("labels", []))
        target_labels = list(target_item.get("labels", []))
        comparisons.append({
            "baseline_session": base_id,
            "target_session": target_item.get("session_id", ""),
            "shared": [label for label in target_labels if label in base_labels],
            "missing_in_target": [label for label in base_labels if label not in target_labels],
            "extra_in_target": [label for label in target_labels if label not in base_labels],
            "baseline_labels": base_labels,
            "target_labels": target_labels,
        })
    for target_item in target_sessions:
        if target_item.get("session_id", "") in used_targets:
            continue
        comparisons.append({
            "baseline_session": "",
            "target_session": target_item.get("session_id", ""),
            "shared": [],
            "missing_in_target": [],
            "extra_in_target": list(target_item.get("labels", [])),
            "baseline_labels": [],
            "target_labels": list(target_item.get("labels", [])),
        })
    return comparisons


def compare_duration_stats(baseline: dict, target: dict) -> list[dict]:
    baseline_stats = {item["label"]: item for item in baseline.get("runtime_findings", {}).get("duration_stats", [])}
    target_stats = {item["label"]: item for item in target.get("runtime_findings", {}).get("duration_stats", [])}
    anomalies = []
    for label, target_item in target_stats.items():
        base_item = baseline_stats.get(label)
        if not base_item:
            continue
        base_avg = float(base_item.get("avg_ms") or 0)
        target_avg = float(target_item.get("avg_ms") or 0)
        if base_avg <= 0:
            continue
        ratio = target_avg / base_avg
        delta = target_avg - base_avg
        if ratio >= 2.0 and delta >= 500:
            anomalies.append({
                "label": label,
                "baseline_avg_ms": base_avg,
                "target_avg_ms": target_avg,
                "delta_ms": delta,
                "ratio": ratio,
            })
    return sorted(anomalies, key=lambda item: (-item["ratio"], -item["delta_ms"]))


def generate_compare_hypotheses(session_comparisons: list[dict], duration_anomalies: list[dict], target: dict) -> list[dict]:
    hypotheses = []
    for comparison in session_comparisons:
        missing = comparison.get("missing_in_target", [])
        target_labels = comparison.get("target_labels", [])
        if missing and "timeout" in target_labels:
            hypotheses.append({
                "title": "Target timed out before completing baseline path",
                "confidence": 0.84,
                "evidence": [
                    f"missing from target: {', '.join(missing)}",
                    f"target labels: {', '.join(target_labels)}",
                ],
            })
            break
    if duration_anomalies:
        top = duration_anomalies[0]
        hypotheses.append({
            "title": f"Target duration regression in `{top['label']}`",
            "confidence": 0.78,
            "evidence": [f"baseline {top['baseline_avg_ms']:.1f}ms vs target {top['target_avg_ms']:.1f}ms ({top['ratio']:.1f}x)"],
        })
    hypotheses.extend(target.get("runtime_findings", {}).get("hypotheses", [])[:3])
    return hypotheses


def query_terms(query: str) -> list[str]:
    if not query:
        return []
    terms = re.findall(r"[A-Za-z0-9_\u4e00-\u9fff]{2,}", query.lower())
    expanded = []
    for term in terms:
        expanded.append(term)
        if term == "pcb":
            expanded.extend(["主控", "称重", "皮带", "ota_pcb"])
        if term in {"await", "等待"}:
            expanded.extend(["await", "waiting", "等待"])
    return sorted(set(expanded))


def text_matches_query(text: str, terms: list[str]) -> bool:
    hay = text.lower()
    return any(term.lower() in hay for term in terms)


def _to_int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
