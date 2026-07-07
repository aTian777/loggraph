from __future__ import annotations

import json
from pathlib import Path
from loggraph.models import CodeIndex


def save_index(index: CodeIndex, path: str | Path) -> None:
    Path(path).write_text(json.dumps(index.to_dict(), indent=2, sort_keys=True), encoding="utf-8")


def load_index(path: str | Path) -> CodeIndex:
    return CodeIndex.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
