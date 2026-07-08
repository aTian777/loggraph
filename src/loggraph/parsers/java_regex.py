"""Java source code parser using regex patterns."""
import re
from pathlib import Path
from loggraph.models import CodeIndex, FunctionNode, LogSite
from loggraph.logs.templates import template_to_regex
from .base import SourceParser

# Patterns for Java code
CLASS_PATTERN = re.compile(r'(?:public|private|protected)?\s*(?:static\s+)?(?:final\s+)?class\s+(\w+)')
METHOD_PATTERN = re.compile(r'(?:public|private|protected)?\s*(?:static\s+)?(?:final\s+)?(?:synchronized\s+)?(?:\w+(?:<[^>]+>)?(?:\[\])?)\s+(\w+)\s*\([^)]*\)\s*(?:throws\s+[\w\s,]+)?\s*\{')
LOG_PATTERN = re.compile(r'(?:log|logger|LOG|LOGGER)\.(info|warn|error|debug|trace|fatal)\s*\(\s*["\']([^"\']+)["\']')
LOG_FORMAT_PATTERN = re.compile(r'(?:log|logger|LOG|LOGGER)\.(info|warn|error|debug|trace|fatal)\s*\(\s*(?:String\.format\s*\(\s*)?["\']([^"\']+)["\']')


class JavaParser(SourceParser):
    """Parser for Java source files."""
    
    def parse_file(self, path: Path, root: Path, index: CodeIndex) -> None:
        """Parse a Java file and extract classes, methods, and log statements."""
        try:
            content = path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            try:
                content = path.read_text(encoding='latin-1')
            except Exception:
                return
        
        lines = content.split('\n')
        module = path.relative_to(root).with_suffix("").as_posix().replace("/", ".")
        
        current_class = None
        current_method = None
        
        for line_num, line in enumerate(lines, 1):
            # Track class boundaries
            class_match = CLASS_PATTERN.search(line)
            if class_match:
                class_name = class_match.group(1)
                current_class = class_name
            
            # Track method boundaries
            method_match = METHOD_PATTERN.search(line)
            if method_match:
                method_name = method_match.group(1)
                qualname = f"{current_class}.{method_name}" if current_class else method_name
                fid = f"java:{module}:{qualname}"
                
                index.functions[fid] = FunctionNode(
                    id=fid,
                    name=method_name,
                    qualname=qualname,
                    module=module,
                    file=str(path),
                    start_line=line_num,
                    end_line=line_num,
                    kind="method" if current_class else "function"
                )
                current_method = method_name
            
            # Track log statements
            log_match = LOG_PATTERN.search(line) or LOG_FORMAT_PATTERN.search(line)
            if log_match:
                level = log_match.group(1).lower()
                template = log_match.group(2)
                
                # Create a log site ID
                lid = f"log:java:{module}:{current_method or 'unknown'}:{line_num}"
                
                # Find the function this log belongs to
                func_id = None
                if current_method:
                    qualname = f"{current_class}.{current_method}" if current_class else current_method
                    func_id = f"java:{module}:{qualname}"
                
                index.log_sites[lid] = LogSite(
                    id=lid,
                    function_id=func_id,
                    level=level,
                    template=template,
                    regex=template_to_regex(template),
                    file=str(path),
                    line=line_num
                )
