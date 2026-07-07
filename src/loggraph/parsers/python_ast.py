from __future__ import annotations

import ast
from pathlib import Path
from loggraph.models import CodeIndex, FunctionNode, CallEdge, LogSite
from loggraph.logs.templates import template_to_regex
from .base import SourceParser

LOG_LEVELS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "fatal"}


class PythonAstParser(SourceParser):
    def parse_file(self, path: Path, root: Path, index: CodeIndex) -> None:
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            return
        module = path.relative_to(root).with_suffix("").as_posix().replace("/", ".")
        parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = self._qualname(node, parents, module)
                fid = f"py:{module}:{qual}"
                index.functions[fid] = FunctionNode(
                    id=fid,
                    name=node.name,
                    qualname=qual,
                    module=module,
                    file=str(path),
                    start_line=getattr(node, "lineno", 1),
                    end_line=getattr(node, "end_lineno", getattr(node, "lineno", 1)),
                    kind="method" if "." in qual else "function",
                )
                self._extract_calls(node, fid, path, index)
                self._extract_logs(node, fid, path, index)

    def _qualname(self, node: ast.AST, parents: dict[ast.AST, ast.AST], module: str) -> str:
        names = [getattr(node, "name", "<anon>")]
        cur = parents.get(node)
        while cur is not None:
            if isinstance(cur, ast.ClassDef):
                names.append(cur.name)
            elif isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.append(cur.name)
            cur = parents.get(cur)
        return ".".join(reversed(names))

    def _call_name(self, call: ast.Call) -> str:
        f = call.func
        if isinstance(f, ast.Name):
            return f.id
        if isinstance(f, ast.Attribute):
            base = self._expr_name(f.value)
            return f"{base}.{f.attr}" if base else f.attr
        return ""

    def _expr_name(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            base = self._expr_name(node.value)
            return f"{base}.{node.attr}" if base else node.attr
        if isinstance(node, ast.Call):
            return self._call_name(node)
        return ""

    def _extract_calls(self, func: ast.AST, fid: str, path: Path, index: CodeIndex) -> None:
        for sub in ast.walk(func):
            if isinstance(sub, ast.Call):
                name = self._call_name(sub)
                if name:
                    index.calls.append(CallEdge(fid, name, str(path), getattr(sub, "lineno", 0)))

    def _extract_logs(self, func: ast.AST, fid: str, path: Path, index: CodeIndex) -> None:
        for sub in ast.walk(func):
            if not isinstance(sub, ast.Call):
                continue
            call_name = self._call_name(sub)
            level = call_name.split(".")[-1].lower() if call_name else ""
            if level not in LOG_LEVELS:
                continue
            if "." not in call_name and level not in {"error", "exception", "critical", "fatal"}:
                continue
            template = self._extract_template(sub)
            if not template:
                continue
            lid = f"log:{fid}:{getattr(sub, 'lineno', 0)}:{len(index.log_sites)}"
            index.log_sites[lid] = LogSite(
                id=lid,
                function_id=fid,
                level=level,
                template=template,
                regex=template_to_regex(template),
                file=str(path),
                line=getattr(sub, "lineno", 0),
                logger=call_name,
            )

    def _extract_template(self, call: ast.Call) -> str:
        if not call.args:
            return ""
        return self._string_like(call.args[0])

    def _string_like(self, node: ast.AST) -> str:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Str):
            return node.s
        if isinstance(node, ast.JoinedStr):
            out = []
            for v in node.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    out.append(v.value)
                elif isinstance(v, ast.Str):
                    out.append(v.s)
                elif isinstance(v, ast.FormattedValue):
                    out.append("{}")
            return "".join(out)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
            return self._string_like(node.left)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
            return self._string_like(node.func.value)
        return ""
