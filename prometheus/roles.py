"""Role prompt loading + robust structured-output parsing.

Meta-agents (architect/strategist/improver) and sub-agents are instructed to emit a
single JSON object. Models don't always comply perfectly (stray prose, code fences),
so extract_json() is defensive.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_ROLES_DIR = Path(__file__).parent / "roles"


def load_role(name: str) -> str:
    path = _ROLES_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"role prompt not found: {path}")
    return path.read_text()


def extract_json(text: str) -> dict[str, Any]:
    """Pull the first JSON object out of a model response, tolerating fences and prose."""
    text = (text or "").strip()
    if not text:
        raise ValueError("empty response; expected JSON")

    # 1) whole thing is JSON
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    # 2) fenced ```json ... ``` (or plain ``` ... ```)
    if "```" in text:
        for fence in ("```json", "```JSON", "```"):
            if fence in text:
                after = text.split(fence, 1)[1]
                body = after.split("```", 1)[0]
                try:
                    obj = json.loads(body.strip())
                    if isinstance(obj, dict):
                        return obj
                except json.JSONDecodeError:
                    continue

    # 3) first balanced {...}
    start = text.find("{")
    if start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = text[start : i + 1]
                        try:
                            obj = json.loads(candidate)
                            if isinstance(obj, dict):
                                return obj
                        except json.JSONDecodeError:
                            break

    raise ValueError(f"could not parse JSON from response: {text[:300]!r}")
