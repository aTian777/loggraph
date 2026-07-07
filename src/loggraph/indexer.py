from __future__ import annotations

from pathlib import Path
from loggraph.models import CodeIndex
from loggraph.parsers.python_ast import PythonAstParser
from loggraph.parsers.kotlin_regex import KotlinRegexParser


class Indexer:
    def __init__(self) -> None:
        self.parsers = {".py": PythonAstParser(), ".kt": KotlinRegexParser()}

    def build(self, root: str | Path) -> CodeIndex:
        root_path = Path(root).resolve()
        index = CodeIndex(root=str(root_path), metadata={"languages": ["python", "kotlin"]})
        for path in root_path.rglob("*"):
            if path.is_file() and path.suffix in self.parsers and not self._skip(path):
                self.parsers[path.suffix].parse_file(path, root_path, index)
        self._resolve_call_edges(index)
        return index

    def _skip(self, path: Path) -> bool:
        parts = set(path.parts)
        return bool(parts & {".git", "__pycache__", ".venv", "venv", "node_modules"})

    def _resolve_call_edges(self, index: CodeIndex) -> None:
        by_name: dict[str, list[str]] = {}
        for fid, fn in index.functions.items():
            by_name.setdefault(fn.name, []).append(fid)
            by_name.setdefault(fn.qualname, []).append(fid)
            by_name.setdefault(f"{fn.module}.{fn.qualname}", []).append(fid)
        for edge in index.calls:
            raw = edge.callee
            tail = raw.split(".")[-1]
            candidates = by_name.get(raw) or by_name.get(tail) or []
            if len(candidates) == 1:
                edge.callee = candidates[0]
                edge.confidence = 0.9
