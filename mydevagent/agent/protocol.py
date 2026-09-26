"""Tool protocol: native function calling or the text format `<tool name="...">{json}</tool>`.

The text format is more reliable with small local models (7B), which often get native function
calling wrong. The parser is lenient: it accepts ```json blocks, trailing commas and small
mistakes, and when it fails it returns a clear error the model can fix on the next step.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

TOOL_RE = re.compile(r"<tool\s+name\s*=\s*[\"']?([\w.-]+)[\"']?\s*>(.*?)</tool>", re.DOTALL | re.IGNORECASE)
UNCLOSED_RE = re.compile(r"<tool\s+name\s*=\s*[\"']?([\w.-]+)[\"']?\s*>(.*)\Z", re.DOTALL | re.IGNORECASE)


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    id: str = ""


def _loads(raw: str) -> dict[str, Any]:
    text = raw.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    if not text:
        return {}
    try:
        value = json.loads(text, strict=False)
    except json.JSONDecodeError:
        repaired = re.sub(r",\s*([}\]])", r"\1", text)  # trailing commas
        value = json.loads(repaired, strict=False)  # strict=False: accepts real newlines inside strings
    if not isinstance(value, dict):
        raise ValueError("arguments must be a JSON object")
    return value


def parse_text_calls(text: str) -> tuple[str, list[ToolCall]]:
    """→ (text outside the tools, calls). An unclosed block at the end of the output is still read."""
    calls: list[ToolCall] = []
    for match in TOOL_RE.finditer(text):
        calls.append(_make_call(match.group(1), match.group(2)))
    outside = TOOL_RE.sub("", text)
    unclosed = UNCLOSED_RE.search(outside)
    if unclosed:
        calls.append(_make_call(unclosed.group(1), unclosed.group(2)))
        outside = outside[: unclosed.start()]
    return outside.strip(), calls


def _make_call(name: str, raw: str) -> ToolCall:
    try:
        return ToolCall(name=name, args=_loads(raw))
    except (ValueError, json.JSONDecodeError) as exc:
        return ToolCall(name=name, error=f"invalid JSON arguments for {name}: {exc}. "
                                         "Resend the call with a valid JSON object.")


def parse_native_calls(calls: list[dict[str, Any]]) -> list[ToolCall]:
    out = []
    for call in calls:
        try:
            out.append(ToolCall(name=call["name"], args=_loads(call.get("arguments") or "{}"), id=call.get("id", "")))
        except (ValueError, json.JSONDecodeError) as exc:
            out.append(ToolCall(name=call["name"], id=call.get("id", ""), error=f"invalid JSON arguments: {exc}"))
    return out


def describe_tools(specs: list[dict[str, Any]]) -> str:
    """Compact instructions for the text protocol (same in every call → prefix cache)."""
    lines = [
        "# Tools",
        "You work directly on the user's project with these tools. To call a tool, write EXACTLY:",
        '<tool name="TOOL_NAME">{"arg": "value"}</tool>',
        "Arguments are a JSON object (escape newlines as \\n inside strings). You may call several tools in "
        "one message; they run in order and you get the results in the next message. When the task is "
        "complete, reply WITHOUT any tool call: that reply is your final answer to the user.",
        "",
    ]
    lines.append("Available tools:")
    for spec in specs:
        params = spec["parameters"].get("properties", {})
        required = set(spec["parameters"].get("required", []))
        args = ", ".join(f"{name}{'' if name in required else '?'}: {p.get('type', 'any')}"
                         for name, p in params.items())
        lines.append(f"- {spec['name']}({args}) — {spec['description']}")
    lines += [
        "",
        "Example (user asks: rename x to count in app.py):",
        '<tool name="read_file">{"path": "app.py"}</tool>',
        "→ you receive the file with line numbers, then:",
        '<tool name="edit_file">{"path": "app.py", "old_string": "x = 0", "new_string": "count = 0"}</tool>',
        "→ after the result, you finish with a short answer: Renamed x to count in app.py.",
        "",
        "IMPORTANT: never paste whole files or code for the user to copy. Change files ONLY through tool calls.",
    ]
    return "\n".join(lines)
