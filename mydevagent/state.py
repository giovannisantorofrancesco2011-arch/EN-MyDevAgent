"""Blackboard shared by the team, and compact context rendering for each agent."""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from .config import ContextConfig
from .registry import Agent, AgentRegistry

PROGRESS_MARKER = "⟢ MyDevAgent"


def merge_dict(left: dict | None, right: dict | None) -> dict:
    return {**(left or {}), **(right or {})}


class TeamState(TypedDict, total=False):
    request: str
    history: str
    files: str
    rag: str
    image_notes: str
    research: str
    plan: str
    route: dict[str, Any]
    artifacts: Annotated[dict[str, str], merge_dict]
    test_report: str
    issues: Annotated[list[dict[str, Any]], operator.add]
    verdicts: Annotated[dict[str, str], merge_dict]
    round: int
    decision: str
    trace: Annotated[list[dict[str, Any]], operator.add]


SECTION_TITLES = {
    "request": "Request",
    "history": "Conversation so far",
    "files": "Attached files",
    "rag": "Relevant code from the local codebase",
    "research": "Research notes (web)",
    "image_notes": "Image notes",
    "plan": "Plan (Architect)",
    "artifacts": "Team artifacts",
    "test_report": "Test report (sandbox)",
    "issues": "Open review issues — fix all BLOCKER/MAJOR",
}
ORDER = ("history", "files", "rag", "image_notes", "research", "plan", "artifacts", "test_report", "issues",
         "request")


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = int(limit * 0.8)
    return text[:head] + "\n…[truncated]…\n" + text[-(limit - head) :]


def latest_issues(state: TeamState) -> list[dict[str, Any]]:
    issues = state.get("issues") or []
    if not issues:
        return []
    last = max(i.get("round", 0) for i in issues)
    return [i for i in issues if i.get("round", 0) == last]


def render_issues(issues: list[dict[str, Any]]) -> str:
    return "\n".join(f"- [{i['severity']}] ({i['agent']}) {i['text']}" for i in issues)


def render_artifacts(state: TeamState, registry: AgentRegistry, exclude: str | None = None) -> str:
    artifacts = state.get("artifacts") or {}
    parts = []
    for agent in registry:
        if agent.key in artifacts and agent.key != exclude:
            parts.append(f"### {agent.name} ({agent.key})\n{artifacts[agent.key]}")
    return "\n\n".join(parts)


def render_context(
    agent: Agent,
    state: TeamState,
    registry: AgentRegistry,
    cfg: ContextConfig,
    extra_reads: tuple[str, ...] = (),
) -> str:
    """Only the sections the agent reads (`reads` in agents.yaml) → shorter, faster prompts."""
    reads = set(agent.reads) | set(extra_reads)
    blocks = []
    for section in ORDER:
        if section not in reads:
            continue
        if section == "artifacts":
            content = render_artifacts(state, registry, exclude=agent.key)
            limit = cfg.max_section_chars * 3
        elif section == "issues":
            content = render_issues(latest_issues(state))
            limit = cfg.max_section_chars
        else:
            content = str(state.get(section) or "")
            limit = cfg.max_section_chars * (2 if section in ("files", "request") else 1)
        content = content.strip()
        if content:
            blocks.append(f"## {SECTION_TITLES[section]}\n{truncate(content, limit)}")
    return "\n\n".join(blocks)


def render_history(messages: list[dict[str, Any]], cfg: ContextConfig) -> str:
    turns = messages[-cfg.history_turns * 2 :] if cfg.history_turns else []
    lines = []
    for msg in turns:
        content = msg.get("content") or ""
        if isinstance(content, list):  # multimodal content: text parts only
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        content = "\n".join(line for line in content.splitlines() if not line.startswith(PROGRESS_MARKER))
        if content.strip():
            lines.append(f"{msg.get('role', 'user')}: {truncate(content.strip(), cfg.max_history_chars)}")
    return "\n".join(lines)


def render_files(files: dict[str, str], cfg: ContextConfig) -> str:
    return "\n\n".join(f"### {path}\n```\n{truncate(content, cfg.max_file_chars)}\n```"
                       for path, content in files.items())
