"""C and C++ source parser using regex patterns."""
import re
from pathlib import Path
from loggraph.models import CodeIndex, FunctionNode, LogSite
from loggraph.logs.templates import template_to_regex
from .base import SourceParser

C_EXTENSIONS = {".c", ".h"}
CPP_EXTENSIONS = {".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx"}
CPP_HINT_PATTERN = re.compile(r"\b(class|namespace|template)\b|std::|\w+::\w+")

# Matches common C/C++ free functions and class methods at definition sites.
FUNCTION_PATTERN = re.compile(
    r"^\s*(?:template\s*<[^>]+>\s*)?"
    r"(?:(?:static|inline|extern|constexpr|virtual|friend|typename)\s+)*"
    r"(?:[\w:<>~*&]+\s+)+"
    r"(?P<name>[A-Za-z_]\w*(?:::[A-Za-z_]\w*)?)\s*"
    r"\([^;{}]*\)\s*(?:const\s*)?(?:noexcept\s*)?(?:override\s*)?(?:final\s*)?\{"
)

# Constructor/destructor definitions such as Foo::Foo(...) { or Foo::~Foo(...) {
CTOR_PATTERN = re.compile(
    r"^\s*(?P<name>[A-Za-z_]\w*::(?:~?[A-Za-z_]\w*))\s*"
    r"\([^;{}]*\)\s*(?:const\s*)?(?:noexcept\s*)?\{"
)

PRINTF_PATTERN = re.compile(r"\b(?:printf|fprintf|sprintf|snprintf|vprintf|vfprintf)\s*\([^;]*?[\"']([^\"']+)[\"']")
SYSLOG_PATTERN = re.compile(r"\bsyslog\s*\(\s*LOG_([A-Z]+)\s*,\s*[\"']([^\"']+)[\"']")
PERROR_PATTERN = re.compile(r"\bperror\s*\(\s*[\"']([^\"']+)[\"']\s*\)")
STD_STREAM_PATTERN = re.compile(r"\bstd::(?:cout|cerr|clog)\s*<<\s*[\"']([^\"']+)[\"']")
SPDLOG_PATTERN = re.compile(r"\bspdlog::(trace|debug|info|warn|error|critical)\s*\(\s*[\"']([^\"']+)[\"']")
LOGGER_PATTERN = re.compile(r"\b\w+(?:->|\.)(trace|debug|info|warn|warning|error|critical|fatal)\s*\(\s*[\"']([^\"']+)[\"']")
GLOG_PATTERN = re.compile(r"\bLOG\s*\(\s*(INFO|WARNING|WARN|ERROR|FATAL|DEBUG)\s*\)\s*<<\s*[\"']([^\"']+)[\"']")
FORMAT_TOKEN_PATTERN = re.compile(r"%(?:[-+ #0]*\d*(?:\.\d+)?[hljztL]*[diuoxXfFeEgGaAcspn%])|\{[^}]*\}")
LITERAL_WORD_PATTERN = re.compile(r"[A-Za-z\u4e00-\u9fff]{3,}")


class CppRegexParser(SourceParser):
    """Parser for C and C++ source files."""

    def parse_file(self, path: Path, root: Path, index: CodeIndex) -> None:
        """Parse C/C++ file and extract functions plus log/print emission sites."""
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                content = path.read_text(encoding="latin-1")
            except Exception:
                return

        language = self._language_for(path, content)
        module = path.relative_to(root).with_suffix("").as_posix().replace("/", ".")
        current_function = None
        current_base_depth = 0
        brace_depth = 0
        pending_signature = ""
        pending_start_line = 0

        for line_num, line in enumerate(content.split("\n"), 1):
            search_line = f"{pending_signature} {line}" if pending_signature else line
            function_match = CTOR_PATTERN.search(search_line) or FUNCTION_PATTERN.search(search_line)
            if function_match:
                raw_name = function_match.group("name")
                name = raw_name.split("::")[-1]
                start_line = pending_start_line or line_num
                fid = f"{language}:{module}:{raw_name}"
                index.functions[fid] = FunctionNode(
                    id=fid,
                    name=name,
                    qualname=raw_name,
                    module=module,
                    file=str(path),
                    start_line=start_line,
                    end_line=line_num,
                    kind="method" if "::" in raw_name else "function",
                )
                current_function = raw_name
                current_base_depth = brace_depth
                pending_signature = ""
                pending_start_line = 0
            elif self._could_be_multiline_signature(line):
                pending_signature = f"{pending_signature} {line}".strip() if pending_signature else line.strip()
                pending_start_line = pending_start_line or line_num
            elif pending_signature and (";" in line or "{" in line or "}" in line):
                pending_signature = ""
                pending_start_line = 0

            log_match = self._find_log(line)
            if log_match:
                level, template = log_match
                if self._is_generic_template(template):
                    continue
                lid = f"log:{language}:{module}:{current_function or 'unknown'}:{line_num}"
                func_id = f"{language}:{module}:{current_function}" if current_function else None
                index.log_sites[lid] = LogSite(
                    id=lid,
                    function_id=func_id,
                    level=level,
                    template=template,
                    regex=template_to_regex(template),
                    file=str(path),
                    line=line_num,
                )

            brace_depth += line.count("{") - line.count("}")
            if current_function and brace_depth <= current_base_depth:
                current_function = None

    def _language_for(self, path: Path, content: str) -> str:
        if path.suffix in CPP_EXTENSIONS:
            return "cpp"
        if path.suffix == ".h" and CPP_HINT_PATTERN.search(content):
            return "cpp"
        return "c"

    def _could_be_multiline_signature(self, line: str) -> bool:
        stripped = line.strip()
        if not stripped or stripped.startswith(("//", "#")):
            return False
        if ";" in stripped or "{" in stripped or "}" in stripped:
            return False
        if not any(ch in stripped for ch in "()"):
            return False
        return not re.match(r"^(if|for|while|switch|catch)\b", stripped)

    def _is_generic_template(self, template: str) -> bool:
        literal = FORMAT_TOKEN_PATTERN.sub(" ", template)
        return not LITERAL_WORD_PATTERN.search(literal)

    def _find_log(self, line: str) -> tuple[str, str] | None:
        if m := SYSLOG_PATTERN.search(line):
            return m.group(1).lower(), m.group(2)
        if m := SPDLOG_PATTERN.search(line):
            return m.group(1).lower(), m.group(2)
        if m := LOGGER_PATTERN.search(line):
            return m.group(1).lower(), m.group(2)
        if m := GLOG_PATTERN.search(line):
            return m.group(1).lower(), m.group(2)
        if m := PERROR_PATTERN.search(line):
            return "error", m.group(1)
        if m := STD_STREAM_PATTERN.search(line):
            return "info", m.group(1)
        if m := PRINTF_PATTERN.search(line):
            return "info", m.group(1)
        return None
