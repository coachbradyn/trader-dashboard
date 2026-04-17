"""
Robust JSON extraction from LLM responses.

LLMs (especially with web_search or grounding) often wrap the requested JSON
in prose, markdown fences, or intersperse it with tool-result blocks that
contain their own ``{}`` pairs. The naive ``re.search(r'\\{[\\s\\S]*\\}')``
greedily matches from the *first* ``{`` to the *last* ``}``, which can span
unrelated objects and produce invalid JSON.

``extract_json_object`` walks the string character-by-character to find the
first *balanced* top-level ``{…}`` and returns it, or None.
"""
from __future__ import annotations

import json


def extract_json_object(text: str) -> dict | None:
    """Return the first balanced top-level JSON object in *text*, parsed.

    Returns ``None`` when no valid JSON object is found.
    """
    clean = text.strip().replace("```json", "").replace("```", "").strip()

    # Fast path: the whole string is valid JSON.
    try:
        obj = json.loads(clean)
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, ValueError):
        pass

    # Walk to find balanced braces.
    start = clean.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape_next = False
    for i in range(start, len(clean)):
        ch = clean[i]
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = clean[start:i + 1]
                try:
                    obj = json.loads(candidate)
                    if isinstance(obj, dict):
                        return obj
                except (json.JSONDecodeError, ValueError):
                    # This balanced block wasn't valid JSON (rare but
                    # possible with embedded braces in comments). Reset
                    # and keep scanning for the next top-level ``{``.
                    next_start = clean.find("{", i + 1)
                    if next_start == -1:
                        return None
                    # Recurse on the remainder — depth resets to 0.
                    return extract_json_object(clean[next_start:])
    return None
