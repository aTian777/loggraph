from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class FunctionNode:
    id: str
    name: str
    qualname: str
    module: str
    file: str
    start_line: int
    end_line: int
    kind: str = "function"


@dataclass
class CallEdge:
    caller: str
    callee: str
    file: str
    line: int
    confidence: float = 0.5


@dataclass
class LogSite:
    id: str
    function_id: str
    level: str
    template: str
    regex: str
    file: str
    line: int
    logger: str = ""


@dataclass
class StackFrame:
    file: str
    line: int
    function: str
    source: str = ""


@dataclass
class LogEntry:
    raw: str
    message: str
    level: str = ""
    timestamp: str = ""
    logger: str = ""
    module: str = ""
    function: str = ""
    pathname: str = ""
    lineno: int | None = None
    exception_type: str = ""
    stack_frames: list[StackFrame] = field(default_factory=list)
    fields: dict[str, Any] = field(default_factory=dict)


@dataclass
class Candidate:
    id: str
    score: float
    function_id: str
    function: str
    file: str
    line: int
    reasons: list[str] = field(default_factory=list)
    log_site_id: str | None = None
    callers: list[str] = field(default_factory=list)
    callees: list[str] = field(default_factory=list)


@dataclass
class CodeIndex:
    root: str
    functions: dict[str, FunctionNode] = field(default_factory=dict)
    calls: list[CallEdge] = field(default_factory=list)
    log_sites: dict[str, LogSite] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "functions": {k: asdict(v) for k, v in self.functions.items()},
            "calls": [asdict(c) for c in self.calls],
            "log_sites": {k: asdict(v) for k, v in self.log_sites.items()},
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CodeIndex":
        idx = cls(root=data.get("root", ""), metadata=data.get("metadata", {}))
        idx.functions = {k: FunctionNode(**v) for k, v in data.get("functions", {}).items()}
        idx.calls = [CallEdge(**v) for v in data.get("calls", [])]
        idx.log_sites = {k: LogSite(**v) for k, v in data.get("log_sites", {}).items()}
        return idx
