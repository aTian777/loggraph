"""Go source code parser using regex patterns."""
import re
from pathlib import Path
from loggraph.models import CodeIndex, FunctionNode, LogSite
from loggraph.logs.templates import template_to_regex
from .base import SourceParser

# Patterns for Go code
FUNC_PATTERN = re.compile(r'func\s+(?:\([^)]+\)\s+)?(\w+)\s*\(')
LOG_PATTERN = re.compile(r'log\.(Print|Println|Printf|Fatal|Fatalln|Fatalf|Panic|Panicln|Panicf)\s*\(\s*["`]([^"`]+)["`]')
FMT_LOG_PATTERN = re.compile(r'fmt\.(Print|Println|Printf)\s*\(\s*["`]([^"`]+)["`]')
LOGGER_PATTERN = re.compile(r'(?:logger|log)\.(Info|Warn|Error|Debug|Fatal|Panic)(?:f|ln)?\s*\(\s*["`]([^"`]+)["`]')


class GoParser(SourceParser):
    """Parser for Go source files."""
    
    def parse_file(self, path: Path, root: Path, index: CodeIndex) -> None:
        """Parse a Go file and extract functions, structs, and log statements."""
        try:
            content = path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            try:
                content = path.read_text(encoding='latin-1')
            except Exception:
                return
        
        lines = content.split('\n')
        module = path.relative_to(root).with_suffix("").as_posix().replace("/", ".")
        
        current_func = None
        
        for line_num, line in enumerate(lines, 1):
            # Track function boundaries
            func_match = FUNC_PATTERN.search(line)
            if func_match:
                func_name = func_match.group(1)
                fid = f"go:{module}:{func_name}"
                
                index.functions[fid] = FunctionNode(
                    id=fid,
                    name=func_name,
                    qualname=func_name,
                    module=module,
                    file=str(path),
                    start_line=line_num,
                    end_line=line_num,
                    kind="function"
                )
                current_func = func_name
            
            # Track log statements
            log_match = LOG_PATTERN.search(line)
            if log_match:
                level = log_match.group(1).lower()
                template = log_match.group(2)
                
                lid = f"log:go:{module}:{current_func or 'unknown'}:{line_num}"
                func_id = f"go:{module}:{current_func}" if current_func else None
                
                index.log_sites[lid] = LogSite(
                    id=lid,
                    function_id=func_id,
                    level=level,
                    template=template,
                    regex=template_to_regex(template),
                    file=str(path),
                    line=line_num
                )
            
            # Track fmt log statements
            fmt_match = FMT_LOG_PATTERN.search(line)
            if fmt_match:
                level = 'info'  # fmt.Print is typically info level
                template = fmt_match.group(2)
                
                lid = f"log:go:{module}:{current_func or 'unknown'}:{line_num}"
                func_id = f"go:{module}:{current_func}" if current_func else None
                
                index.log_sites[lid] = LogSite(
                    id=lid,
                    function_id=func_id,
                    level=level,
                    template=template,
                    regex=template_to_regex(template),
                    file=str(path),
                    line=line_num
                )
            
            # Track logger statements
            logger_match = LOGGER_PATTERN.search(line)
            if logger_match:
                level = logger_match.group(1).lower()
                template = logger_match.group(2)
                
                lid = f"log:go:{module}:{current_func or 'unknown'}:{line_num}"
                func_id = f"go:{module}:{current_func}" if current_func else None
                
                index.log_sites[lid] = LogSite(
                    id=lid,
                    function_id=func_id,
                    level=level,
                    template=template,
                    regex=template_to_regex(template),
                    file=str(path),
                    line=line_num
                )
