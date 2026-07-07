from __future__ import annotations

import re
from pathlib import Path
from loggraph.models import CodeIndex, FunctionNode, CallEdge, LogSite
from loggraph.logs.templates import template_to_regex
from .base import SourceParser

FUN_RE = re.compile(r"\bfun\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")
CLASS_RE = re.compile(r"\b(class|object|interface)\s+([A-Za-z_][A-Za-z0-9_]*)")
CALL_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)\s*\(")
LOG_RE = re.compile(r"\b(?:L|Log|logger)\.([A-Za-z][A-Za-z0-9_]*)\s*(?:\([^)]*\))?\s*\{\s*\"(.*?)\"", re.S)
ANDROID_LOG_RE = re.compile(r"\bLog\.([diwev])\s*\([^,]+,\s*\"(.*?)\"", re.S)


class KotlinRegexParser(SourceParser):
    """Lightweight Kotlin parser for LogGraph indexing.

    It is intentionally conservative: it extracts function ranges by brace balance,
    approximate class context, simple call names, and common project logging style
    such as `L.ri { "message" }`.
    """

    def parse_file(self, path: Path, root: Path, index: CodeIndex) -> None:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return
        module = path.relative_to(root).with_suffix("").as_posix().replace("/", ".")
        lines = text.splitlines()
        class_at_line = self._class_context(lines)
        for m in FUN_RE.finditer(text):
            start_line = text.count("\n", 0, m.start()) + 1
            end_line, body = self._function_body(text, m.end(), start_line)
            name = m.group(1)
            cls = class_at_line.get(start_line, "")
            qual = f"{cls}.{name}" if cls else name
            fid = f"kt:{module}:{qual}"
            index.functions[fid] = FunctionNode(fid, name, qual, module, str(path), start_line, end_line, "method" if cls else "function")
            self._extract_calls(body, fid, path, start_line, index)
            self._extract_logs(body, fid, path, start_line, index)

    def _class_context(self, lines: list[str]) -> dict[int, str]:
        result: dict[int, str] = {}
        stack: list[tuple[str, int]] = []
        brace = 0
        pending: tuple[str, int] | None = None
        for i, line in enumerate(lines, 1):
            cm = CLASS_RE.search(line)
            if cm:
                pending = (cm.group(2), brace)
            opens = line.count("{")
            closes = line.count("}")
            if pending and opens:
                stack.append((pending[0], brace + opens - closes))
                pending = None
            ctx = stack[-1][0] if stack else ""
            result[i] = ctx
            brace += opens - closes
            while stack and brace < stack[-1][1]:
                stack.pop()
        return result

    def _function_body(self, text: str, pos: int, start_line: int) -> tuple[int, str]:
        brace_pos = text.find("{", pos)
        if brace_pos < 0:
            return start_line, ""
        depth = 0
        for i in range(brace_pos, len(text)):
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end_line = text.count("\n", 0, i) + 1
                    return end_line, text[brace_pos:i + 1]
        return text.count("\n") + 1, text[brace_pos:]

    def _extract_calls(self, body: str, fid: str, path: Path, start_line: int, index: CodeIndex) -> None:
        for m in CALL_RE.finditer(body):
            name = m.group(1)
            if name in {"if", "for", "while", "when", "catch", "return"}:
                continue
            line = start_line + body.count("\n", 0, m.start())
            index.calls.append(CallEdge(fid, name, str(path), line, 0.4))

    def _extract_logs(self, body: str, fid: str, path: Path, start_line: int, index: CodeIndex) -> None:
        for m in LOG_RE.finditer(body):
            level = m.group(1).lower()
            template = self._clean_template(m.group(2))
            line = start_line + body.count("\n", 0, m.start())
            self._add_site(index, fid, level, template, path, line, f"L.{m.group(1)}")
        for m in ANDROID_LOG_RE.finditer(body):
            level = m.group(1).lower()
            template = self._clean_template(m.group(2))
            line = start_line + body.count("\n", 0, m.start())
            self._add_site(index, fid, level, template, path, line, f"Log.{m.group(1)}")

    def _clean_template(self, template: str) -> str:
        return re.sub(r"\$\{[^}]+\}|\$[A-Za-z_][A-Za-z0-9_]*", "{}", template)

    def _add_site(self, index: CodeIndex, fid: str, level: str, template: str, path: Path, line: int, logger: str) -> None:
        if not template:
            return
        lid = f"log:{fid}:{line}:{len(index.log_sites)}"
        index.log_sites[lid] = LogSite(lid, fid, level, template, template_to_regex(template), str(path), line, logger)
