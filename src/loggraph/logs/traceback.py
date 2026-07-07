from __future__ import annotations

import re
from pathlib import Path
from loggraph.models import StackFrame

FRAME_RE = re.compile(r'^\s*File "(?P<file>[^"]+)", line (?P<line>\d+), in (?P<func>[\w<>]+)')
EXC_RE = re.compile(r'^(?P<type>[\w.]+(?:Error|Exception|Warning))(?::\s*(?P<msg>.*))?$')


def parse_traceback(text: str) -> tuple[list[StackFrame], str, str]:
    lines = text.splitlines()
    frames: list[StackFrame] = []
    exc_type = ""
    exc_msg = ""
    for i, line in enumerate(lines):
        # Fast path: check if line contains 'File "' before regex
        if 'File "' in line:
            m = FRAME_RE.match(line)
            if m:
                source = lines[i + 1].strip() if i + 1 < len(lines) else ""
                frames.append(StackFrame(m.group("file"), int(m.group("line")), m.group("func"), source))
                continue
        # Fast path: check if line contains ':' before regex for exception
        stripped = line.strip()
        if ':' in stripped or 'Error' in stripped or 'Exception' in stripped or 'Warning' in stripped:
            e = EXC_RE.match(stripped)
            if e:
                exc_type = e.group("type")
                exc_msg = e.group("msg") or stripped
    return frames, exc_type, exc_msg


def filename_matches(index_file: str, frame_file: str) -> bool:
    if not frame_file:
        return False
    return Path(index_file).name == Path(frame_file).name or str(index_file).endswith(frame_file)
