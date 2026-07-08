from __future__ import annotations

import json
import re
from loggraph.models import LogEntry
from .traceback import parse_traceback

LEVELS = r"DEBUG|INFO|WARNING|WARN|ERROR|EXCEPTION|CRITICAL|FATAL"

# Pre-compile regex patterns for performance
_PATTERNS = [
    # Log4j format: 2024-01-15 10:30:45,123 INFO [thread] logger - message
    re.compile(rf"(?P<ts>\d{{4}}-\d{{2}}-\d{{2}} \d{{2}}:\d{{2}}:\d{{2}},\d{{3}})\s+(?P<level>{LEVELS})\s+\[(?P<thread>[^\]]+)\]\s+(?P<logger>[\w.$:-]+)\s*-\s*(?P<msg>.*)", re.I),
    # Logback format: 2024-01-15 10:30:45.123 [thread] LEVEL logger - message
    re.compile(rf"(?P<ts>\d{{4}}-\d{{2}}-\d{{2}} \d{{2}}:\d{{2}}:\d{{2}}\.\d{{3}})\s+\[(?P<thread>[^\]]+)\]\s+(?P<level>{LEVELS})\s+(?P<logger>[\w.$:-]+)\s*-\s*(?P<msg>.*)", re.I),
    # Standard format: 2024-01-15 10:30:45 INFO logger.name - message
    re.compile(rf"(?P<ts>\d{{4}}-\d{{2}}-\d{{2}}[ T]\S+)\s+(?P<level>{LEVELS})\s+(?P<logger>[\w.$:-]+)?\s*[:-]?\s*(?P<msg>.*)", re.I),
    # Bracket format: INFO [logger.name] message
    re.compile(rf"(?P<level>{LEVELS})\s+\[(?P<logger>[^\]]+)\]\s+(?P<msg>.*)", re.I),
    # Simple format: INFO: message
    re.compile(rf"(?P<level>{LEVELS})\s*[:-]\s*(?P<msg>.*)", re.I),
    # Syslog format: Jan 15 10:30:45 hostname program[pid]: message
    re.compile(rf"(?P<ts>[A-Z][a-z]{{2}}\s+\d{{1,2}}\s+\d{{2}}:\d{{2}}:\d{{2}})\s+(?P<host>\S+)\s+(?P<program>\S+?)(?:\[(?P<pid>\d+)\])?:\s*(?P<msg>.*)"),
    # Key-value format: timestamp=... level=... message=...
    re.compile(rf"(?:timestamp|time)=(?P<ts>[^\s]+)\s+.*?(?:level)=(?P<level>\S+)\s+.*?(?:message|msg)=(?P<msg>.+?)(?:\s+\w+=|$)", re.I),
]
_SPLIT_PATTERN = re.compile(rf"(\d{{4}}-\d{{2}}-\d{{2}}|{LEVELS}\b|\{{|[A-Z][a-z]{{2}}\s+\d{{1,2}}|(?:timestamp|time)=)", re.I)
_JAVA_EXCEPTION = re.compile(r"^[\w.]+(?:Error|Exception|Throwable)(?:\s*:.*)?$")
_JAVA_STACK_FRAME = re.compile(r"^\s+at\s+[\w.$]+\([\w.]+:\d+\)$")


def parse_log_text(text: str) -> list[LogEntry]:
    blocks = _split_blocks(text)
    return [parse_log_block(b) for b in blocks if b.strip()]


def parse_log_block(block: str) -> LogEntry:
    block = block.rstrip("\n")
    tb_frames, exc_type, exc_msg = parse_traceback(block)
    first = block.splitlines()[0] if block.splitlines() else block
    entry = _parse_json(first) or _parse_plain(first)
    if not entry:
        entry = LogEntry(raw=block, message=first)
    entry.raw = block
    if tb_frames:
        entry.stack_frames = tb_frames
        entry.exception_type = exc_type
        if exc_msg and (not entry.message or entry.message == first):
            entry.message = exc_msg
    return entry


def _parse_json(line: str) -> LogEntry | None:
    try:
        obj = json.loads(line)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    msg = str(obj.get("message") or obj.get("msg") or obj.get("event") or "")
    level = str(obj.get("level") or obj.get("levelname") or "").upper()
    lineno = obj.get("lineno") or obj.get("line")
    try:
        lineno = int(lineno) if lineno is not None else None
    except Exception:
        lineno = None
    return LogEntry(
        raw=line,
        message=msg,
        level=level,
        timestamp=str(obj.get("timestamp") or obj.get("time") or ""),
        logger=str(obj.get("logger") or obj.get("name") or ""),
        module=str(obj.get("module") or ""),
        function=str(obj.get("function") or obj.get("funcName") or ""),
        pathname=str(obj.get("pathname") or obj.get("file") or ""),
        lineno=lineno,
        fields=obj,
    )


def _parse_plain(line: str) -> LogEntry | None:
    for p in _PATTERNS:
        m = p.match(line)
        if m:
            gd = m.groupdict()
            # Handle syslog format specially
            if 'program' in gd and 'host' in gd:
                # Syslog format
                return LogEntry(
                    raw=line,
                    message=(gd.get("msg") or "").strip(),
                    level="INFO",  # Syslog doesn't have level in standard format
                    timestamp=gd.get("ts") or "",
                    logger=gd.get("program") or "",
                )
            return LogEntry(
                raw=line,
                message=(gd.get("msg") or "").strip(),
                level=(gd.get("level") or "").upper(),
                timestamp=gd.get("ts") or "",
                logger=gd.get("logger") or "",
            )
    return LogEntry(raw=line, message=line)


def _split_blocks(text: str) -> list[str]:
    """Split log text into individual log entries, handling multi-line entries like stacktraces."""
    lines = text.splitlines()
    blocks: list[str] = []
    cur: list[str] = []
    in_exception = False
    
    for line in lines:
        # Check if this line is a Java stacktrace frame
        is_stack_frame = _JAVA_STACK_FRAME.match(line) or line.strip().startswith("...")
        
        # Check if this line is a Java exception
        is_exception = _JAVA_EXCEPTION.match(line) or line.startswith("Traceback (most recent call last):")
        
        # If we're in an exception block, continue adding lines
        if in_exception:
            cur.append(line)
            # If this is a new exception (not a stack frame), end the previous exception
            if is_exception and not is_stack_frame:
                in_exception = False
            continue
        
        # Check if this line starts an exception
        if is_exception:
            in_exception = True
            # Add exception to current block (don't start a new block)
            cur.append(line)
            continue
        
        # Check if this line starts a new log entry
        starts = bool(_SPLIT_PATTERN.match(line))
        
        # If this is a new log entry and we have accumulated lines, save them
        if cur and starts:
            blocks.append("\n".join(cur))
            cur = []
        
        cur.append(line)
    
    # Don't forget the last block
    if cur:
        blocks.append("\n".join(cur))
    
    return blocks
