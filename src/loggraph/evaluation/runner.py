from __future__ import annotations

import json
from pathlib import Path
from loggraph.indexer import Indexer
from loggraph.logs.parser import parse_log_block
from loggraph.matchers.locator import Locator
from .metrics import summarize, EvalResult


def load_corpus(path: str | Path) -> list[dict]:
    items = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            items.append(json.loads(line))
    return items


def evaluate(src: str | Path, corpus: str | Path, top: int = 3, tolerance: int = 3) -> EvalResult:
    index = Indexer().build(src)
    locator = Locator(index)
    pairs = []
    for item in load_corpus(corpus):
        entry = parse_log_block(item["log"])
        pairs.append((item, locator.locate(entry, top=top)))
    return summarize(pairs, top=top, tolerance=tolerance)
