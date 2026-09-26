"""Hooks in the Claude Code format: commands that run on their own at certain moments of the agent's work.

    {"hooks": {"PostToolUse": [{"matcher": "Edit|Write",
                                "hooks": [{"type": "command", "command": "ruff format ."}]}]}}

Where: `~/.claude/settings.json`, `~/.mydevagent/settings.json`, plugins (`hooks/hooks.json`) and, only after
you have said yes once, the project's (`.claude/settings.json`, `.claude/settings.local.json`,
`.mydevagent/settings.json`, plugins in `.mydevagent/plugins`): a downloaded repository cannot run
commands on your PC without asking you.

Events: PreToolUse (can block a tool), PostToolUse, UserPromptSubmit (can block the request or
add context), Stop (can ask the agent to keep going), SessionStart (adds context),
SubagentStop. The command receives the event JSON on stdin; exit code 2 = block, and stderr explains why.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import trust
from .plugins import load_plugins

EVENTS = ("PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop", "SubagentStop", "SessionStart")
TOOL_EVENTS = ("PreToolUse", "PostToolUse")
# Claude Code's tool names, so plugin matchers ("Edit|Write", "Bash") work here too
CLAUDE_NAMES = {"bash": "Bash", "run_tests": "Bash", "edit_file": "Edit", "write_file": "Write",
                "read_file": "Read", "grep": "Grep", "list_files": "Glob", "web_search": "WebSearch",
                "todo_write": "TodoWrite", "skill": "Skill", "task": "Task",
                "web_fetch": "WebFetch"}


@dataclass
class Hook:
    event: str
    matcher: str
    command: str
    timeout: int = 60
    source: str = ""  # where it comes from: claude code · user · project · plugin X
    project: bool = False  # defined inside the project: needs your consent
    plugin_root: Path | None = None
    unsupported: str = ""  # why MyDevAgent doesn't run it (still visible in /hooks)

    def matches(self, target: str, *names: str) -> bool:
        if self.matcher in ("", "*"):
            return True
        return any(re.fullmatch(self.matcher, n) for n in (target, *names) if n)


@dataclass
class Outcome:
    blocked: bool = False
    reason: str = ""  # for the model (for UserPromptSubmit: for you)
    context: str = ""  # extra context for the model
    notes: list[str] = field(default_factory=list)  # to show you: errors, systemMessage

    def add(self, attr: str, text: str) -> None:
        text = text.strip()
        if text:
            setattr(self, attr, (getattr(self, attr) + "\n" + text).strip())


# ---------------------------------------------------------------------- loading
def _json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def parse(config: dict, source: str, project: bool = False, plugin_root: Path | None = None) -> list[Hook]:
    out = []
    for event, groups in (config.get("hooks") or {}).items():
        for group in groups if isinstance(groups, list) else []:
            for spec in group.get("hooks", []) if isinstance(group, dict) else []:
                # ponytail: only "command" hooks; "prompt" ones (evaluated by an LLM) are ignored
                if isinstance(spec, dict) and spec.get("type", "command") == "command" and spec.get("command"):
                    skip = [k for k in ("if", "async", "asyncRewake") if spec.get(k)]
                    unsupported = ("not supported yet: " + ", ".join(skip)) if skip else (
                        "" if event in EVENTS else "event not supported yet")
                    out.append(Hook(event, str(group.get("matcher") or ""), str(spec["command"]),
                                    int(spec.get("timeout") or 60), source, project, plugin_root, unsupported))
    return out


def _plugin_configs(plugin) -> list[dict]:
    """hooks/hooks.json plus the "hooks" field of plugin.json (a path, a list or the hooks themselves)."""
    declared = plugin.manifest.get("hooks")
    items = declared if isinstance(declared, list) else [declared] if declared else []
    default = plugin.path / "hooks" / "hooks.json"
    configs = [_json(default)]
    for item in items:
        if isinstance(item, dict):
            configs.append(item if "hooks" in item else {"hooks": item})
        elif isinstance(item, str) and (plugin.path / item).resolve() != default.resolve():
            configs.append(_json(plugin.path / item))
    return configs


def all_hooks(root: Path) -> list[Hook]:
    """All the hooks found, including the project's not yet authorized ones."""
    root = Path(root)
    state = Path(os.environ.get("MYDEVAGENT_STATE_DIR", Path.home() / ".mydevagent"))
    settings = [(Path.home() / ".claude" / "settings.json", "claude code", False),
                (state / "settings.json", "user", False),
                (root / ".claude" / "settings.json", "project", True),
                (root / ".claude" / "settings.local.json", "project", True),
                (root / ".mydevagent" / "settings.json", "project", True)]
    configs = [(_json(path), source, project) for path, source, project in settings]
    if any(c.get("disableAllHooks") for c, _, _ in configs):
        return []
    hooks = [h for config, source, project in configs for h in parse(config, source, project)]
    for plugin in load_plugins(root).values():
        for config in _plugin_configs(plugin):
            hooks += parse(config, f"plugin {plugin.name}", plugin.source == "project", plugin.path)
    return hooks


def _items(hooks: list[Hook]) -> list[str]:
    return [f"{h.event}|{h.matcher}|{h.command}" for h in hooks if h.project]


def untrusted(root: Path) -> list[Hook]:
    """The project's hooks waiting for your OK (again, if they changed since then)."""
    found = all_hooks(root)
    return [] if trust.is_trusted(root, "hooks", _items(found)) else [h for h in found if h.project]


def allow(root: Path) -> None:
    trust.trust(root, "hooks", _items(all_hooks(root)))


def load_hooks(root: Path) -> list[Hook]:
    """The active hooks: the project's only if you authorized them."""
    pending = untrusted(root)
    return [h for h in all_hooks(root) if not (h.project and pending)]


# -------------------------------------------------------------------- execution
def _shell(command: str) -> list[str] | str:
    """On Windows plugin hooks are written for bash: use Git's bash if present (like Claude Code)."""
    if sys.platform != "win32":
        return command
    git = shutil.which("git")
    bash = Path(git).resolve().parent.parent / "bin" / "bash.exe" if git else None
    return [str(bash), "-c", command] if bash and bash.is_file() else command


class Hooks:
    def __init__(self, root: Path, hooks: list[Hook] | None = None, session_id: str = "") -> None:
        self.root = Path(root).resolve()
        self.hooks = load_hooks(self.root) if hooks is None else hooks
        self.session_id = session_id or uuid.uuid4().hex
        self.mode = "default"
        self.session_context = ""  # what the SessionStart hooks added

    def start(self, source: str = "startup") -> Outcome:
        outcome = self.run("SessionStart", target=source, payload={"source": source})
        self.session_context = outcome.context
        return outcome

    def __bool__(self) -> bool:
        return bool(self.hooks)

    def run(self, event: str, *, target: str = "", payload: dict[str, Any] | None = None) -> Outcome:
        """Runs the event's hooks. `target` is the tool (or the source, for SessionStart) to match against the matcher."""
        outcome = Outcome()
        names = [CLAUDE_NAMES.get(target, "")] if event in TOOL_EVENTS else []
        data = {"session_id": self.session_id, "transcript_path": "", "cwd": str(self.root),
                "hook_event_name": event, "permission_mode": self.mode, **(payload or {})}
        for hook in self.hooks:
            if hook.event == event and not hook.unsupported and hook.matches(target, *names):
                self._run_one(hook, data, outcome)
        return outcome

    def tool_payload(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        tool_input = dict(args)
        if "path" in args:  # like Claude Code: absolute path in file_path
            tool_input["file_path"] = str((self.root / str(args["path"])).resolve())
        return {"tool_name": CLAUDE_NAMES.get(tool, tool), "tool_input": tool_input}

    def _run_one(self, hook: Hook, data: dict[str, Any], outcome: Outcome) -> None:
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(self.root), "MYDEVAGENT": "1"}
        command = hook.command.replace("${CLAUDE_PROJECT_DIR}", str(self.root))
        if hook.plugin_root:
            env["CLAUDE_PLUGIN_ROOT"] = str(hook.plugin_root)
            command = command.replace("${CLAUDE_PLUGIN_ROOT}", str(hook.plugin_root))
        label = hook.command if len(hook.command) <= 60 else hook.command[:57] + "…"
        args = _shell(command)
        try:
            proc = subprocess.run(args, shell=isinstance(args, str), input=json.dumps(data), capture_output=True,
                                  text=True, timeout=hook.timeout, cwd=self.root, env=env, encoding="utf-8",
                                  errors="replace")
        except subprocess.TimeoutExpired:
            outcome.notes.append(f"hook \"{label}\" stopped after {hook.timeout}s")
            return
        except OSError as exc:
            outcome.notes.append(f"hook \"{label}\" did not start: {exc}")
            return
        if proc.returncode == 2:
            outcome.blocked = True
            outcome.add("reason", proc.stderr or f"blocked by \"{label}\"")
            return
        if proc.returncode != 0:
            outcome.notes.append(f"hook \"{label}\" failed (exit {proc.returncode}): {proc.stderr.strip()[:200]}")
            return
        out = proc.stdout.strip()
        try:
            result = json.loads(out) if out.startswith("{") else None
        except ValueError:
            result = None
        if not isinstance(result, dict):
            if data["hook_event_name"] in ("UserPromptSubmit", "SessionStart"):
                outcome.add("context", out)
            return
        specific = result.get("hookSpecificOutput") or {}
        if result.get("continue") is False:
            outcome.blocked = True
            outcome.add("reason", str(result.get("stopReason") or f"stopped by \"{label}\""))
        if result.get("decision") == "block":
            outcome.blocked = True
            outcome.add("reason", str(result.get("reason") or f"blocked by \"{label}\""))
        # ponytail: "allow"/"ask" don't override MyDevAgent's permissions; only "deny" counts
        if specific.get("permissionDecision") == "deny":
            outcome.blocked = True
            outcome.add("reason", str(specific.get("permissionDecisionReason") or f"denied by \"{label}\""))
        outcome.add("context", str(specific.get("additionalContext") or ""))
        if result.get("systemMessage"):
            outcome.notes.append(str(result["systemMessage"]))
