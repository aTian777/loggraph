from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def default_profile_path(project: str | Path) -> Path:
    return Path(project) / ".loggraph" / "profile.yaml"


def load_project_profile(project: str | Path) -> dict[str, Any]:
    path = default_profile_path(project)
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    stripped = text.strip()
    if not stripped:
        return {}
    if stripped.startswith("{"):
        data = json.loads(stripped)
        return data if isinstance(data, dict) else {}
    return parse_simple_yaml(stripped)


def merge_profiles(learned: dict[str, Any] | None, manual: dict[str, Any] | None) -> dict[str, Any]:
    learned = learned or {}
    manual = manual or {}
    merged = dict(learned)
    learned_patterns = list(learned.get("learned_patterns", []))
    manual_patterns = []
    for name, spec in (manual.get("events") or {}).items():
        if not isinstance(spec, dict):
            continue
        event_type = str(spec.get("type") or name)
        for pattern in spec.get("patterns") or []:
            manual_patterns.append({"pattern": str(pattern).lower(), "type": event_type, "source": "manual_profile"})
    merged["learned_patterns"] = manual_patterns + learned_patterns
    merged["session_keys"] = _unique((manual.get("session_keys") or []) + (learned.get("session_keys") or []))
    merged["states"] = _unique((manual.get("states") or []) + (learned.get("states") or []))
    merged["expected_sequences"] = manual.get("expected_sequences") or {}
    merged["entities"] = manual.get("entities") or {}
    merged["manual_events"] = manual.get("events") or {}
    merged["manual_profile"] = bool(manual)
    return merged


def render_profile_suggestion(profile: dict[str, Any]) -> str:
    lines = ["# .loggraph/profile.yaml suggestion", "", "session_keys:"]
    for key in profile.get("session_keys", [])[:20]:
        lines.append(f"  - {key}")
    lines.extend(["", "states:"])
    for state in profile.get("states", [])[:40]:
        lines.append(f"  - {state}")
    lines.extend(["", "events:"])
    used_names: set[str] = set()
    for idx, item in enumerate(profile.get("learned_patterns", [])[:30], 1):
        pattern = str(item.get("pattern", ""))
        name = _unique_name(_safe_name(pattern), used_names, idx)
        lines.extend([
            f"  {name}:",
            f"    type: project:{name}",
            "    patterns:",
            f"      - {pattern}",
        ])
    lines.extend(["", "expected_sequences:", "  # example_success:", "  #   - start", "  #   - finish"])
    return "\n".join(lines) + "\n"


def merge_manual_profiles(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base or {})
    merged["session_keys"] = _unique((base or {}).get("session_keys", []) + (patch or {}).get("session_keys", []))
    merged["states"] = _unique((base or {}).get("states", []) + (patch or {}).get("states", []))
    events = dict((base or {}).get("events") or {})
    events.update((patch or {}).get("events") or {})
    merged["events"] = events
    sequences = dict((base or {}).get("expected_sequences") or {})
    sequences.update((patch or {}).get("expected_sequences") or {})
    merged["expected_sequences"] = sequences
    return merged


def render_manual_profile(profile: dict[str, Any]) -> str:
    lines = ["session_keys:"]
    for key in profile.get("session_keys", []):
        lines.append(f"  - {key}")
    lines.extend(["", "states:"])
    for state in profile.get("states", []):
        lines.append(f"  - {state}")
    lines.extend(["", "events:"])
    for name, spec in (profile.get("events") or {}).items():
        lines.append(f"  {name}:")
        if isinstance(spec, dict):
            lines.append(f"    type: {spec.get('type', name)}")
            lines.append("    patterns:")
            for pattern in spec.get("patterns", []):
                lines.append(f"      - {pattern}")
    lines.extend(["", "expected_sequences:"])
    for name, sequence in (profile.get("expected_sequences") or {}).items():
        lines.append(f"  {name}:")
        for item in sequence:
            lines.append(f"    - {item}")
    return "\n".join(lines) + "\n"


def parse_simple_yaml(text: str) -> dict[str, Any]:
    """Parse the small YAML subset LogGraph profiles need.

    Supports top-level mappings, nested mappings, and scalar lists. This is not
    a general YAML parser; JSON is recommended for advanced values.
    """
    root: dict[str, Any] = {}
    stack: list[tuple[int, Any]] = [(-1, root)]
    last_key_at_indent: dict[int, tuple[Any, str]] = {}
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if line.startswith("- "):
            value = _parse_scalar(line[2:].strip())
            if isinstance(parent, list):
                parent.append(value)
            else:
                owner, key = last_key_at_indent.get(indent - 2, (None, ""))
                if isinstance(owner, dict) and key:
                    owner[key] = [value]
                    stack.append((indent, owner[key]))
            continue
        key, sep, rest = line.partition(":")
        if not sep:
            continue
        key = key.strip()
        rest = rest.strip()
        if rest:
            value = _parse_scalar(rest)
            if isinstance(parent, dict):
                parent[key] = value
                last_key_at_indent[indent] = (parent, key)
            continue
        value: dict[str, Any] = {}
        if isinstance(parent, dict):
            parent[key] = value
            last_key_at_indent[indent] = (parent, key)
            stack.append((indent, value))
    _normalize_empty_dict_lists(root)
    return root


def _normalize_empty_dict_lists(value: Any) -> None:
    if not isinstance(value, dict):
        return
    for key, child in list(value.items()):
        if isinstance(child, dict):
            if not child:
                value[key] = []
            else:
                _normalize_empty_dict_lists(child)


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(part.strip()) for part in inner.split(",")]
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    return value


def _unique(values: list[Any]) -> list[str]:
    seen = set()
    out = []
    for value in values:
        text = str(value)
        if text and text not in seen:
            out.append(text)
            seen.add(text)
    return out


def _safe_name(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_]+", "_", value.strip().lower()).strip("_")
    return name or "event"


def _unique_name(name: str, used: set[str], idx: int) -> str:
    if name == "event":
        name = f"event_{idx}"
    candidate = name
    suffix = 2
    while candidate in used:
        candidate = f"{name}_{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate
