from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path
from urllib.parse import parse_qs, urlparse

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

    matches = []
    for no, line in enumerate(lines, 1):
        if app_only and not any(hint in line for hint in APP_TAG_HINTS):
            continue
        candidates = locator.locate(parse_log_block(line), top=top)
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
        "matched_log_lines": len(matches),
        "matches": matches,
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
        "matched_log_lines": report["matched_log_lines"],
        "top_matches": report["matches"][:max_matches],
        "domain_findings": report["domain_findings"],
    }


def _to_int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
