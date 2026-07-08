"""TypeScript source code parser using regex patterns."""
import re
from pathlib import Path
from loggraph.models import CodeIndex, FunctionNode, LogSite
from loggraph.logs.templates import template_to_regex
from .base import SourceParser

# Patterns for TypeScript code
CLASS_PATTERN = re.compile(r'(?:export\s+)?(?:abstract\s+)?class\s+(\w+)')
FUNCTION_PATTERN = re.compile(r'(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*(?:<[^>]*>)?\s*\(')
METHOD_PATTERN = re.compile(r'(?:public|private|protected)?\s*(?:async\s+)?(\w+)\s*\([^)]*\)\s*(?::\s*[^{]+)?\s*\{')
ARROW_FUNCTION_PATTERN = re.compile(r'(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\([^)]*\)\s*(?::\s*[^=]+)?\s*=>')
LOG_PATTERN = re.compile(r'console\.(log|info|warn|error|debug)\s*\(\s*["\'`]([^"\'`]+)["\'`]')
LOG_TEMPLATE_PATTERN = re.compile(r'console\.(log|info|warn|error|debug)\s*\(\s*`([^`]+)`')


class TypeScriptParser(SourceParser):
    """Parser for TypeScript source files."""
    
    def parse_file(self, path: Path, root: Path, index: CodeIndex) -> None:
        """Parse a TypeScript file and extract classes, functions, and log statements."""
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
        current_function = None
        
        for line_num, line in enumerate(lines, 1):
            # Track class boundaries
            class_match = CLASS_PATTERN.search(line)
            if class_match:
                current_class = class_match.group(1)
            
            # Track function boundaries
            func_match = FUNCTION_PATTERN.search(line)
            if func_match:
                func_name = func_match.group(1)
                qualname = f"{current_class}.{func_name}" if current_class else func_name
                fid = f"ts:{module}:{qualname}"
                
                index.functions[fid] = FunctionNode(
                    id=fid,
                    name=func_name,
                    qualname=qualname,
                    module=module,
                    file=str(path),
                    start_line=line_num,
                    end_line=line_num,
                    kind="method" if current_class else "function"
                )
                current_function = func_name
            
            # Track method boundaries
            method_match = METHOD_PATTERN.search(line)
            if method_match and current_class:
                method_name = method_match.group(1)
                if method_name not in ['if', 'for', 'while', 'switch', 'catch']:
                    qualname = f"{current_class}.{method_name}"
                    fid = f"ts:{module}:{qualname}"
                    
                    index.functions[fid] = FunctionNode(
                        id=fid,
                        name=method_name,
                        qualname=qualname,
                        module=module,
                        file=str(path),
                        start_line=line_num,
                        end_line=line_num,
                        kind="method"
                    )
                    current_function = method_name
            
            # Track arrow functions
            arrow_match = ARROW_FUNCTION_PATTERN.search(line)
            if arrow_match:
                func_name = arrow_match.group(1)
                qualname = f"{current_class}.{func_name}" if current_class else func_name
                fid = f"ts:{module}:{qualname}"
                
                index.functions[fid] = FunctionNode(
                    id=fid,
                    name=func_name,
                    qualname=qualname,
                    module=module,
                    file=str(path),
                    start_line=line_num,
                    end_line=line_num,
                    kind="method" if current_class else "function"
                )
                current_function = func_name
            
            # Track log statements
            log_match = LOG_PATTERN.search(line) or LOG_TEMPLATE_PATTERN.search(line)
            if log_match:
                level = log_match.group(1).lower()
                template = log_match.group(2)
                
                # Create a log site ID
                lid = f"log:ts:{module}:{current_function or 'unknown'}:{line_num}"
                
                # Find the function this log belongs to
                func_id = None
                if current_function:
                    qualname = f"{current_class}.{current_function}" if current_class else current_function
                    func_id = f"ts:{module}:{qualname}"
                
                index.log_sites[lid] = LogSite(
                    id=lid,
                    function_id=func_id,
                    level=level,
                    template=template,
                    regex=template_to_regex(template),
                    file=str(path),
                    line=line_num
                )
