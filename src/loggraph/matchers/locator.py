from __future__ import annotations

import re
from pathlib import Path
from loggraph.models import CodeIndex, LogEntry, Candidate
from loggraph.logs.templates import template_matches, similarity, normalize_text
from loggraph.logs.traceback import filename_matches


class Locator:
    def __init__(self, index: CodeIndex):
        self.index = index
        self._callers: dict[str, set[str]] = {}
        self._callees: dict[str, set[str]] = {}
        # Pre-compile regex patterns for fast matching
        self._compiled_regex: dict[str, re.Pattern] = {}
        # Build keyword index for fast filtering
        self._site_keywords: dict[str, set[str]] = {}
        for lid, site in index.log_sites.items():
            if site.regex:
                try:
                    self._compiled_regex[lid] = re.compile(site.regex)
                except re.error:
                    pass
            # Extract keywords from template (words >= 4 chars)
            keywords = {w for w in re.findall(r'[A-Za-z\u4e00-\u9fff]{4,}', site.template.lower())}
            self._site_keywords[lid] = keywords
        for e in index.calls:
            if e.caller in index.functions:
                if e.callee in index.functions:
                    self._callees.setdefault(e.caller, set()).add(e.callee)
                    self._callers.setdefault(e.callee, set()).add(e.caller)
                else:
                    self._callees.setdefault(e.caller, set()).add(e.callee)

    def locate(self, entry: LogEntry, top: int = 3) -> list[Candidate]:
        scores: dict[str, Candidate] = {}

        def add(fid: str, score: float, reason: str, line: int | None = None, log_site_id: str | None = None):
            fn = self.index.functions.get(fid)
            if not fn:
                return
            c = scores.get(fid)
            if not c:
                c = Candidate(
                    id=f"cand:{len(scores)}",
                    score=0.0,
                    function_id=fid,
                    function=fn.qualname,
                    file=fn.file,
                    line=line or fn.start_line,
                    reasons=[],
                    log_site_id=log_site_id,
                    callers=sorted(self._callers.get(fid, [])),
                    callees=sorted(self._callees.get(fid, [])),
                )
                scores[fid] = c
            c.score += score
            c.reasons.append(reason)
            if log_site_id:
                c.log_site_id = log_site_id
                site = self.index.log_sites[log_site_id]
                c.line = site.line
            elif line:
                c.line = line

        # Direct structured file/function/line evidence.
        if entry.pathname or entry.function or entry.lineno:
            for fid, fn in self.index.functions.items():
                if entry.pathname and not filename_matches(fn.file, entry.pathname):
                    continue
                if entry.function and entry.function not in {fn.name, fn.qualname, f"{fn.module}.{fn.qualname}"}:
                    continue
                score = 70.0
                if entry.lineno and fn.start_line <= entry.lineno <= fn.end_line:
                    score += 20.0
                add(fid, score, "structured file/function/line evidence", entry.lineno)

        # Traceback frames are strongest. Last frame is crash site.
        for pos, frame in enumerate(entry.stack_frames):
            weight = 95.0 if pos == len(entry.stack_frames) - 1 else 60.0
            for fid, fn in self.index.functions.items():
                if frame.function in {fn.name, fn.qualname.split(".")[-1]} and filename_matches(fn.file, frame.file):
                    bonus = 15.0 if fn.start_line <= frame.line <= fn.end_line else 0.0
                    add(fid, weight + bonus, f"traceback frame {Path(frame.file).name}:{frame.line} in {frame.function}", frame.line)

        # Logger template matching.
        msg = entry.message or entry.raw
        msg_normalized = normalize_text(msg)
        # Extract keywords from message for fast filtering - use split instead of regex for speed
        msg_lower = msg_normalized.lower()
        msg_keywords = {w for w in msg_lower.split() if len(w) >= 4 and w.isalnum()}
        
        for lid, site in self.index.log_sites.items():
            fn = self.index.functions.get(site.function_id)
            if not fn:
                continue
            
            # Fast keyword filter: skip if no shared keywords (unless template is very short)
            site_kw = self._site_keywords.get(lid, set())
            if site_kw and msg_keywords and not (site_kw & msg_keywords) and len(site.template) > 10:
                continue
            
            # Use pre-compiled regex for fast matching
            compiled = self._compiled_regex.get(lid)
            matched = False
            if compiled:
                matched = compiled.match(msg_normalized) is not None
            else:
                matched = template_matches(site.template, msg)
            
            if matched:
                score = 85.0
                if entry.level and site.level and entry.level.lower().startswith(site.level.lower()[:4]):
                    score += 8.0
                if entry.logger and (entry.logger in fn.module or entry.logger in fn.qualname):
                    score += 5.0
                add(site.function_id, score, f"template match: {site.template!r}", site.line, lid)
            elif len(msg) > 10 and len(site.template) > 10:
                # Only do fuzzy matching for reasonably long messages
                sim = similarity(site.template, msg)
                if sim >= 0.72:
                    score = 45.0 + (sim * 35.0)
                    add(site.function_id, score, f"fuzzy template similarity {sim:.2f}: {site.template!r}", site.line, lid)

        # Function/module names appearing in message/logger.
        # Pre-compute lowercase haystack once
        hay = f"{entry.raw} {entry.logger} {entry.module}".lower()
        # Build a set of words in the haystack for fast lookup
        hay_words = set(re.findall(r'[a-z\u4e00-\u9fff]{3,}', hay))
        # Only check functions whose names appear as words in the haystack
        for fid, fn in self.index.functions.items():
            name_lower = fn.name.lower()
            if len(name_lower) >= 3 and name_lower in hay_words:
                add(fid, 25.0, "function name appears in log evidence")
            elif len(fn.module) >= 3 and fn.module.lower() in hay_words:
                add(fid, 15.0, "module name appears in log evidence")

        # Context boost when traceback functions are caller/callee neighbors.
        frame_names = {f.function for f in entry.stack_frames}
        if frame_names:
            for fid, cand in list(scores.items()):
                neighbors = self._callers.get(fid, set()) | self._callees.get(fid, set())
                for n in neighbors:
                    fn = self.index.functions.get(n)
                    if fn and fn.name in frame_names:
                        cand.score += 10.0
                        cand.reasons.append("call graph neighbor appears in traceback")

        ranked = sorted(scores.values(), key=lambda c: (-c.score, c.file, c.line))
        return ranked[:top]
