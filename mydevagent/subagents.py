"""Subagents as in Claude Code: a Markdown file with a name, description and system prompt.

    ---
    name: code-reviewer
    description: Reviews freshly changed code. Use it after every significant change.
    tools: Read, Grep, Glob        # optional: without it, it has every tool
    model: haiku                   # optional: haiku uses the fast model
    ---
    You are a strict reviewer…

The main agent calls them with the `task` tool: the subagent works in a context of its own (it does not
see the conversation), with its own tools and the usual permissions, and returns only its final report.
Folders: the project's `.mydevagent/agents` and `.claude/agents`, `~/.mydevagent/agents`, `~/.claude/agents`
and the plugins' `agents/`.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from .plugins import load_plugins
from .skills import short, split_frontmatter

# Claude Code's tools → MyDevAgent's
TOOL_NAMES = {"Read": ["read_file"], "Grep": ["grep"], "Glob": ["list_files"], "LS": ["list_files"],
              "Bash": ["bash", "run_tests"], "Edit": ["edit_file"], "MultiEdit": ["edit_file"],
              "Write": ["write_file"], "NotebookEdit": ["edit_file"], "NotebookRead": ["read_file"],
              "WebSearch": ["web_search"], "WebFetch": ["web_fetch"], "TodoWrite": ["todo_write"],
              "Skill": ["skill"]}


@dataclass
class SubAgent:
    name: str
    description: str
    prompt: str
    source: str
    tools: list[str] | None = None  # None = all
    model: str = ""

    @property
    def tier(self) -> str:
        return "fast" if self.model.lower() in ("haiku", "fast") else "main"

    def allowed(self) -> set[str] | None:
        if not self.tools:
            return None
        out: set[str] = set()
        for tool in self.tools:
            tool = tool.split("(", 1)[0]  # Bash(git:*) → Bash
            out.update(["mcp"] if tool.startswith("mcp__") else TOOL_NAMES.get(tool, [tool]))
        return out


def parse_tools(value: str) -> list[str] | None:
    """`Read, Grep` or `["Read", "Grep"]` (both show up in plugins)."""
    return [t.strip(" '\"") for t in value.strip().strip("[]").split(",") if t.strip(" '\"")] or None


def agent_dirs(root: Path) -> list[tuple[Path, str]]:
    home = Path.home()
    state = Path(os.environ.get("MYDEVAGENT_STATE_DIR", home / ".mydevagent"))
    dirs = [(root / ".mydevagent" / "agents", "project"), (root / ".claude" / "agents", "project"),
            (state / "agents", "user"), (home / ".claude" / "agents", "user")]
    for plugin in load_plugins(root).values():
        dirs += [(d, f"plugin {plugin.name}") for d in plugin.dirs("agents")]
    return dirs


def load_subagents(root: Path) -> dict[str, SubAgent]:
    """name → subagent. On a name clash the most specific folder wins (the project)."""
    found: dict[str, SubAgent] = {}
    for folder, source in agent_dirs(Path(root)):
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*.md")):
            try:
                meta, body = split_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            name = re.sub(r"[^\w-]+", "-", meta.get("name") or path.stem).strip("-").lower()
            if not name or name in found or not body.strip():
                continue
            tools = parse_tools(meta.get("tools", ""))
            found[name] = SubAgent(name, meta.get("description") or name, body.strip(), source, tools,
                                   meta.get("model", ""))
    return found


def subagents_prompt(agents: dict[str, SubAgent]) -> str:
    if not agents:
        return ""
    lines = "\n".join(f"- {a.name}: {short(a.description)}" for a in agents.values())
    return ("# Sub-agents\nFor a self-contained job that matches one of these agents, delegate it with the `task` "
            "tool: the agent works in its own context and returns only its report. Give it a complete prompt "
            "(it cannot see this conversation).\n" + lines)
