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

APP_TAG_HINTS = ("smart-recyclable---->", "插桩检测-红包", "Log日志", "BaseViewModel")


def default_cache_dir(project_root: str | Path) -> Path:
    return Path(project_root) / ".loggraph"


def default_index_path(project_root: str | Path) -> Path:
    return default_cache_dir(project_root) / "index.json"


def analyze_log(index_path: str | Path, log_file: str | Path, *, top: int = 3, app_only: bool = True) -> dict:
    index = load_index(index_path)
    locator = Locator(index)
    path = Path(log_file)
    lines = path.read_text(errors="ignore").splitlines()
    event_profile = index.metadata.get("event_profile", {})

    matches = []
    events = []
    analyzed_lines = 0
    for no, line in enumerate(lines, 1):
        if app_only and not any(hint in line for hint in APP_TAG_HINTS):
            continue
        analyzed_lines += 1
        entry = parse_log_block(line)
        if event := extract_event(entry, no, event_profile):
            events.append(event)
        candidates = locator.locate(entry, top=top)
        if candidates:
            matches.append({
                "line": no,
                "log": line,
                "candidates": [asdict(c) for c in candidates],
            })

    delivery_posts = extract_delivery_posts(lines)
    completed_rounds = [
        {"line": no, "time": line[:18], "log": line}
        for no, line in enumerate(lines, 1)
        if "一轮投递流程结束" in line
    ]

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
            "learned_patterns": len(event_profile.get("learned_patterns", [])),
            "session_keys": event_profile.get("session_keys", [])[:10],
            "states": event_profile.get("states", [])[:10],
        },
        "runtime_findings": summarize_events(events),
        "report_markdown": render_report(
            log_file=str(path),
            index_path=str(index_path),
            analyzed_lines=analyzed_lines,
            matches=matches,
            runtime_findings=summarize_events(events),
            max_matches=top,
        ),
        "domain_findings": {
            "delivery_posts": delivery_posts,
            "completed_rounds": completed_rounds,
            "bottle_count_from_rty_sum": sum(item["rty"] for item in delivery_posts),
        },
    }


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


def compact_summary(report: dict, *, max_matches: int = 10) -> dict:
    return {
        "index_summary": report["index_summary"],
        "analyzed_lines": report.get("analyzed_lines", 0),
        "matched_log_lines": report["matched_log_lines"],
        "top_matches": report["matches"][:max_matches],
        "event_profile_summary": report.get("event_profile_summary", {}),
        "runtime_findings": report.get("runtime_findings", {}),
        "domain_findings": report["domain_findings"],
        "report_markdown": report.get("report_markdown", ""),
    }


def render_report(*, log_file: str, index_path: str, analyzed_lines: int, matches: list[dict], runtime_findings: dict, max_matches: int = 3) -> str:
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
    else:
        lines.append("- No source candidates matched. Consider running with `--all-lines` or refreshing the index.")

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


def _to_int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
