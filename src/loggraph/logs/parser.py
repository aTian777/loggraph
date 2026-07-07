from __future__ import annotations

import json
import re
from loggraph.models import LogEntry
from .traceback import parse_traceback

LEVELS = r"DEBUG|INFO|WARNING|WARN|ERROR|EXCEPTION|CRITICAL|FATAL"


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
    patterns = [
        rf"(?P<ts>\d{{4}}-\d{{2}}-\d{{2}}[ T]\S+)\s+(?P<level>{LEVELS})\s+(?P<logger>[\w.$:-]+)?\s*[:-]?\s*(?P<msg>.*)",
        rf"(?P<level>{LEVELS})\s+\[(?P<logger>[^\]]+)\]\s+(?P<msg>.*)",
        rf"(?P<level>{LEVELS})\s*[:-]\s*(?P<msg>.*)",
    ]
    for p in patterns:
        m = re.match(p, line, re.I)
        if m:
            gd = m.groupdict()
            return LogEntry(raw=line, message=(gd.get("msg") or "").strip(), level=(gd.get("level") or "").upper(), timestamp=gd.get("ts") or "", logger=gd.get("logger") or "")
    return LogEntry(raw=line, message=line)


def _split_blocks(text: str) -> list[str]:
    lines = text.splitlines()
    blocks: list[str] = []
    cur: list[str] = []
    in_tb = False
    for line in lines:
        starts = bool(re.match(rf"(\d{{4}}-\d{{2}}-\d{{2}}|{LEVELS}\b|\{{)", line, re.I))
        if cur and starts and not in_tb:
            blocks.append("\n".join(cur))
            cur = []
        cur.append(line)
        if line.startswith("Traceback (most recent call last):"):
            in_tb = True
        elif in_tb and re.match(r"\w+(Error|Exception|Warning):", line.strip()):
            in_tb = False
    if cur:
        blocks.append("\n".join(cur))
    return blocks
