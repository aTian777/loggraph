from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from loggraph.models import Candidate
from loggraph.logs.traceback import filename_matches


@dataclass
class EvalResult:
    total: int
    top1: int
    top3: int
    accuracy: float
    failures: list[dict]


def is_correct(c: Candidate, expected: dict, tolerance: int = 3) -> bool:
    exp_file = expected.get("file", "")
    exp_func = expected.get("function", "")
    exp_line = int(expected.get("line") or 0)
    file_ok = filename_matches(c.file, exp_file)
    func_ok = exp_func in {c.function, c.function.split(".")[-1]} or c.function.endswith("." + exp_func)
    line_ok = not exp_line or abs(c.line - exp_line) <= tolerance
    return file_ok and func_ok and line_ok


def summarize(results: list[tuple[dict, list[Candidate]]], top: int = 3, tolerance: int = 3) -> EvalResult:
    total = len(results)
    top1 = 0
    topn = 0
    failures = []
    for item, candidates in results:
        expected = item["expected"]
        if candidates and is_correct(candidates[0], expected, tolerance):
            top1 += 1
        if any(is_correct(c, expected, tolerance) for c in candidates[:top]):
            topn += 1
        else:
            failures.append({"id": item.get("id"), "expected": expected, "candidates": [c.__dict__ for c in candidates[:top]]})
    return EvalResult(total, top1, topn, (topn / total if total else 0.0), failures)
