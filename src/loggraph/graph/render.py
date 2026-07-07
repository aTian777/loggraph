from __future__ import annotations

import subprocess
from pathlib import Path
from loggraph.models import CodeIndex, Candidate


def render_dot(index: CodeIndex, candidates: list[Candidate]) -> str:
    focus = {c.function_id for c in candidates}
    for c in candidates:
        focus.update(c.callers[:5])
        focus.update(x for x in c.callees[:5] if x in index.functions)
    lines = ["digraph LogGraph {", "  rankdir=LR;", "  node [shape=box,fontname=Helvetica];"]
    for fid in sorted(focus):
        fn = index.functions.get(fid)
        if not fn:
            continue
        is_cand = any(c.function_id == fid for c in candidates)
        color = "red" if is_cand else "gray"
        style = "filled" if is_cand else "solid"
        fill = "mistyrose" if is_cand else "white"
        label = f"{fn.qualname}\\n{Path(fn.file).name}:{fn.start_line}"
        lines.append(f'  "{fid}" [label="{label}", color="{color}", style="{style}", fillcolor="{fill}"];')
    for lid, site in index.log_sites.items():
        if site.function_id in focus:
            label = f"{site.level.upper()} {site.template}".replace('"', "'")
            lines.append(f'  "{lid}" [label="{label}", shape=note, color=orange, style=filled, fillcolor=lightyellow];')
            lines.append(f'  "{site.function_id}" -> "{lid}" [label="emits", color=orange];')
    for e in index.calls:
        if e.caller in focus and e.callee in focus:
            lines.append(f'  "{e.caller}" -> "{e.callee}" [color=gray];')
    lines.append("}")
    return "\n".join(lines) + "\n"


def render(index: CodeIndex, candidates: list[Candidate], out: str | Path) -> Path:
    out = Path(out)
    dot = render_dot(index, candidates)
    if out.suffix == ".dot":
        out.write_text(dot, encoding="utf-8")
        return out
    dot_path = out.with_suffix(".dot")
    dot_path.write_text(dot, encoding="utf-8")
    fmt = out.suffix.lstrip(".") or "svg"
    try:
        subprocess.run(["dot", f"-T{fmt}", str(dot_path), "-o", str(out)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return out
    except Exception:
        return dot_path
