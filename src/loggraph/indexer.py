from __future__ import annotations

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from loggraph.models import CodeIndex
from loggraph.parsers.python_ast import PythonAstParser
from loggraph.parsers.kotlin_regex import KotlinRegexParser
from loggraph.parsers.java_regex import JavaParser
from loggraph.parsers.typescript_regex import TypeScriptParser
from loggraph.parsers.go_regex import GoParser
from loggraph.parsers.c_cpp_regex import CppRegexParser


class Indexer:
    def __init__(self, max_workers: int | None = None, incremental: bool = True) -> None:
        self.parsers = {
            ".py": PythonAstParser(),
            ".kt": KotlinRegexParser(),
            ".java": JavaParser(),
            ".ts": TypeScriptParser(),
            ".tsx": TypeScriptParser(),
            ".go": GoParser(),
            ".c": CppRegexParser(),
            ".h": CppRegexParser(),
            ".cc": CppRegexParser(),
            ".cpp": CppRegexParser(),
            ".cxx": CppRegexParser(),
            ".hpp": CppRegexParser(),
            ".hh": CppRegexParser(),
            ".hxx": CppRegexParser(),
        }
        self.max_workers = max_workers
        self.incremental = incremental

    def build(self, root: str | Path, existing_index: CodeIndex = None) -> CodeIndex:
        """Build code index with optional parallel processing and incremental updates."""
        root_path = Path(root).resolve()
        
        # Use existing index if provided and incremental mode is enabled
        if existing_index and self.incremental:
            index = existing_index
            index.metadata["incremental"] = True
        else:
            index = CodeIndex(root=str(root_path), metadata={"languages": ["python", "kotlin", "java", "typescript", "go", "c", "cpp"]})
        
        # Collect files to parse.
        discovered_files: set[str] = set()
        files_to_parse: list[Path] = []
        timestamps = index.metadata.setdefault("file_timestamps", {}) if self.incremental else {}
        for path in root_path.rglob("*"):
            if path.is_file() and path.suffix in self.parsers and not self._skip(path):
                file_key = str(path)
                discovered_files.add(file_key)
                current_mtime = path.stat().st_mtime
                if self.incremental and existing_index and timestamps.get(file_key) == current_mtime:
                    continue  # File hasn't changed.
                files_to_parse.append(path)
        
        if self.incremental and existing_index:
            # Remove stale entries for deleted files and for changed files before re-parsing.
            stale_files = set(timestamps) - discovered_files
            changed_files = {str(path) for path in files_to_parse}
            for file_path in stale_files | changed_files:
                self._remove_file_entries(index, file_path)
                timestamps.pop(file_path, None)
        
        # Parse files in parallel if max_workers is set.
        if self.max_workers and len(files_to_parse) > 1:
            self._parse_parallel(files_to_parse, root_path, index)
        else:
            self._parse_sequential(files_to_parse, root_path, index)
        
        # Update file timestamps for incremental mode.
        if self.incremental:
            for path in files_to_parse:
                timestamps[str(path)] = path.stat().st_mtime
        
        self._resolve_call_edges(index)
        return index
    
    def _parse_sequential(self, files: list[Path], root_path: Path, index: CodeIndex) -> None:
        """Parse files sequentially."""
        for path in files:
            self.parsers[path.suffix].parse_file(path, root_path, index)
    
    def _parse_parallel(self, files: list[Path], root_path: Path, index: CodeIndex) -> None:
        """Parse files in parallel using thread-local indexes, then merge results."""
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = [executor.submit(self._parse_one, path, root_path) for path in files]
            for future in as_completed(futures):
                self._merge_index(index, future.result())

    def _parse_one(self, path: Path, root_path: Path) -> CodeIndex:
        partial = CodeIndex(root=str(root_path), metadata={})
        self.parsers[path.suffix].parse_file(path, root_path, partial)
        return partial

    def _merge_index(self, index: CodeIndex, partial: CodeIndex) -> None:
        index.functions.update(partial.functions)
        index.calls.extend(partial.calls)
        index.log_sites.update(partial.log_sites)

    def _remove_file_entries(self, index: CodeIndex, file_path: str) -> None:
        removed_functions = {fid for fid, fn in index.functions.items() if fn.file == file_path}
        for fid in removed_functions:
            index.functions.pop(fid, None)
        for lid, site in list(index.log_sites.items()):
            if site.file == file_path or site.function_id in removed_functions:
                index.log_sites.pop(lid, None)
        index.calls = [
            edge
            for edge in index.calls
            if edge.file != file_path and edge.caller not in removed_functions and edge.callee not in removed_functions
        ]

    def _skip(self, path: Path) -> bool:
        parts = {part.lower() for part in path.parts}
        skip_dirs = {
            ".git", "__pycache__", ".venv", "venv", "node_modules", ".gradle",
            "build", "dist", ".idea", ".vscode", "third_party", "third-party",
            "vendor", "external", "generated", "cmake-build-debug", "cmake-build-release",
        }
        if parts & skip_dirs:
            return True
        return any(part.startswith(("ncnn", "glslang", "spirv")) for part in parts)

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
