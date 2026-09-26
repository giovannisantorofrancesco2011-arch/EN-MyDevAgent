"""Agent tools: read, search, edit, run. Every action goes through permissions and checkpoints."""

from __future__ import annotations

import difflib
import json
import os
import re
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..graph import Cancelled
from ..hooks import Hooks
from ..mcp import McpManager
from ..skills import Skill
from ..subagents import SubAgent
from ..tools.filesystem import IGNORED_DIRS, Workspace, WorkspaceError, display_path
from ..tools.preview import is_local
from .checkpoints import CheckpointStore
from .permissions import EDIT_TOOLS, ApprovalRequest, Approver, PermissionPolicy, is_dangerous

MAX_RESULT_CHARS = 8000
WEB_PAGE_CHARS = 6000  # one chunk of a web page: below MAX_RESULT_CHARS, so it is not cut in half
DEFAULT_READ_LINES = 250
EventHandler = Callable[[dict[str, Any]], None]
Spawn = Callable[["SubAgent", str], "tuple[str, AgentTools]"]

SPECS: list[dict[str, Any]] = [
    {"name": "read_file", "description": "Read a text file with line numbers. Use offset/limit for big files.",
     "parameters": {"type": "object", "properties": {
         "path": {"type": "string"}, "offset": {"type": "integer", "description": "first line (1-based)"},
         "limit": {"type": "integer"}}, "required": ["path"]}},
    {"name": "list_files", "description": "List project files (recursive). Optional glob pattern like '*.py'.",
     "parameters": {"type": "object", "properties": {
         "path": {"type": "string"}, "pattern": {"type": "string"}}}},
    {"name": "grep", "description": "Search a regex in project files. Returns path:line: text.",
     "parameters": {"type": "object", "properties": {
         "pattern": {"type": "string"}, "glob": {"type": "string"}}, "required": ["pattern"]}},
    {"name": "edit_file", "description": "Replace an exact snippet in a file. old_string must match the file "
     "exactly (copy it from read_file without line numbers) and be unique unless replace_all is true.",
     "parameters": {"type": "object", "properties": {
         "path": {"type": "string"}, "old_string": {"type": "string"}, "new_string": {"type": "string"},
         "replace_all": {"type": "boolean"}}, "required": ["path", "old_string", "new_string"]}},
    {"name": "write_file", "description": "Create a new file or fully overwrite a small file.",
     "parameters": {"type": "object", "properties": {
         "path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
    {"name": "bash", "description": "Run a shell command in the project directory (build, tests, git, tools).",
     "parameters": {"type": "object", "properties": {
         "command": {"type": "string"}, "timeout": {"type": "integer"}}, "required": ["command"]}},
    {"name": "run_tests", "description": "Run the project's test suite (auto-detected) and return the result.",
     "parameters": {"type": "object", "properties": {"command": {"type": "string",
                    "description": "optional explicit test command"}}}},
    {"name": "todo_write", "description": "Write the task checklist. Use it for multi-step tasks and keep it "
     "updated: status is pending, in_progress or completed.",
     "parameters": {"type": "object", "properties": {"todos": {"type": "array", "items": {
         "type": "object", "properties": {"content": {"type": "string"}, "status": {"type": "string"}}}}},
         "required": ["todos"]}},
    {"name": "web_search", "description": "Search the web for up-to-date docs, versions, errors (only online).",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "web_fetch", "description": "Read a web page (docs, issues, changelogs) as text. Long pages come in "
     "parts: call again with the given offset to read more.",
     "parameters": {"type": "object", "properties": {
         "url": {"type": "string"}, "offset": {"type": "integer", "description": "character to start from"}},
         "required": ["url"]}},
    {"name": "preview", "description": "See a web page of the project like the user would: opens it on localhost "
     "in a headless browser, takes a screenshot (described by the vision model) and reports console errors, "
     "missing files and the visible text. Use it after changing a web page to check the result. `path`: an HTML "
     "file of the project (default index.html). For a site with its own server give `url` (localhost) and "
     "`start`, the command that starts it (e.g. `npm run dev`): it keeps running for later previews.",
     "parameters": {"type": "object", "properties": {
         "path": {"type": "string"}, "url": {"type": "string"},
         "start": {"type": "string", "description": "command that starts the local server"},
         "look": {"type": "string", "description": "what to check on the page"}}}},
    {"name": "mcp", "description": "Use an external MCP server (see the MCP servers list). With only `server`, "
     "list its tools and their arguments; with `tool` and `arguments`, call one.",
     "parameters": {"type": "object", "properties": {
         "server": {"type": "string"}, "tool": {"type": "string"}, "arguments": {"type": "object"}},
         "required": ["server"]}},
    {"name": "task", "description": "Delegate a self-contained job to a sub-agent (see the Sub-agents list). It "
     "works in its own context with its own tools and returns only its final report. The prompt must contain "
     "everything it needs: it cannot see this conversation.",
     "parameters": {"type": "object", "properties": {
         "agent": {"type": "string"}, "prompt": {"type": "string"}}, "required": ["agent", "prompt"]}},
    {"name": "skill", "description": "Load a skill's instructions by name (see the Skills list). With `file`, "
     "read one of the skill's supporting files.",
     "parameters": {"type": "object", "properties": {
         "name": {"type": "string"}, "file": {"type": "string"}}, "required": ["name"]}},
]
SPEC_BY_NAME = {s["name"]: s for s in SPECS}


def truncate_middle(text: str, limit: int = MAX_RESULT_CHARS) -> str:
    if len(text) <= limit:
        return text
    head = int(limit * 0.6)
    tail = limit - head
    omitted = text[head:-tail].count("\n")
    return f"{text[:head]}\n…[{omitted} lines omitted]…\n{text[-tail:]}"


def unified_diff(old: str, new: str, path: str) -> str:
    return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), f"a/{path}", f"b/{path}"))


def diff_stats(diff: str) -> tuple[int, int]:
    added = sum(1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---"))
    return added, removed


def detect_test_command(root: Path, memory: str = "") -> str | None:
    match = re.search(r"^\s*[-*]?\s*(?:test|tests)\s*:\s*`?([^`\n]+)`?", memory, re.IGNORECASE | re.MULTILINE)
    if match:
        return match.group(1).strip()
    if (root / "package.json").is_file():
        try:
            scripts = json.loads((root / "package.json").read_text(encoding="utf-8")).get("scripts", {})
            if "test" in scripts:
                return "npm test --silent"
        except ValueError:
            pass
    if (root / "go.mod").is_file():
        return "go test ./..."
    if (root / "Cargo.toml").is_file():
        return "cargo test -q"
    py_markers = ("pyproject.toml", "setup.cfg", "pytest.ini", "tox.ini", "setup.py")
    if any((root / m).is_file() for m in py_markers) or list(root.glob("test*.py")) or (root / "tests").is_dir():
        return "python -m pytest -q"
    return None


class AgentTools:
    def __init__(self, root: Path, policy: PermissionPolicy, checkpoints: CheckpointStore,
                 approver: Approver | None = None, emit: EventHandler | None = None,
                 web_search: Callable[[str], str] | None = None, memory: str = "",
                 web_fetch: Callable[[str], str] | None = None, preview: Callable[..., str] | None = None,
                 bash_timeout: int = 120, skills: dict[str, Skill] | None = None,
                 hooks: Hooks | None = None, mcp: McpManager | None = None,
                 subagents: dict[str, SubAgent] | None = None, spawn: Spawn | None = None,
                 allowed: set[str] | None = None, extra_dirs: list[Path] | None = None) -> None:
        self.root = Path(root).resolve()
        self.extra_dirs = [Path(d).resolve() for d in extra_dirs or []]  # extra folders (/add-dir)
        self.workspace = Workspace(self.root, allow_write=True, extra=self.extra_dirs)
        self.policy = policy
        self.checkpoints = checkpoints
        self.approver = approver
        self.emit = emit or (lambda _e: None)
        self.web_search_fn = web_search
        self.web_fetch_fn = web_fetch
        self.preview_fn = preview  # site preview: only available if Chrome, Edge or Chromium is installed
        self.memory = memory
        self.bash_timeout = bash_timeout
        self.skills = skills or {}
        self.hooks = hooks
        self.mcp = mcp
        self.subagents = subagents or {}
        self.spawn = spawn  # runs a subagent: provided by the runner, which owns the model
        self.allowed = allowed  # None = all tools (subagents may have fewer)
        self.todos: list[dict[str, str]] = []
        self._page = ("", "")  # (url, text) of the last page read with web_fetch
        self.changed: list[str] = []
        self.read_paths: set[str] = set()
        self.last_test: tuple[str, bool, str] | None = None  # (command, ok, output)
        self.user_denied = False  # the user denied something this turn
        self._lock = threading.Lock()

    # ----------------------------------------------------------------- spec
    def specs(self) -> list[dict[str, Any]]:
        specs = [s for s in SPECS if (s["name"] != "web_search" or self.web_search_fn)
                 and (s["name"] != "web_fetch" or self.web_fetch_fn) and (s["name"] != "preview" or self.preview_fn)
                 and (s["name"] != "skill" or self.skills) and (s["name"] != "mcp" or self.mcp)
                 and (s["name"] != "task" or (self.subagents and self.spawn))
                 and (self.allowed is None or s["name"] in self.allowed)]
        if self.policy.mode == "plan":
            specs = [s for s in specs if s["name"] not in EDIT_TOOLS]
        return specs

    def openai_schemas(self) -> list[dict[str, Any]]:
        return [{"type": "function", "function": s} for s in self.specs()]

    # -------------------------------------------------------------- dispatch
    def execute(self, name: str, args: dict[str, Any]) -> str:
        handler = getattr(self, f"_t_{name}", None)
        if handler is None or name not in SPEC_BY_NAME or (self.allowed is not None and name not in self.allowed):
            return f"ERROR: unknown tool '{name}'. Available: {', '.join(s['name'] for s in self.specs())}"
        self.emit({"type": "tool_call", "agent": "agent", "tool": name, "args": _display_args(name, args)})
        blocked, reason = self._hook("PreToolUse", name, args)
        if blocked:
            result = f"DENIED by a hook: {reason}"
        else:
            try:
                result = handler(**args)
            except TypeError as exc:
                result = f"ERROR: bad arguments for {name}: {exc}"
            except (WorkspaceError, FileNotFoundError, IsADirectoryError, UnicodeDecodeError) as exc:
                result = f"ERROR: {type(exc).__name__}: {exc}"
            except Cancelled:  # Esc during a subagent: stop the main agent too
                raise
            except Exception as exc:  # a tool must never crash the loop
                result = f"ERROR: {name} failed: {type(exc).__name__}: {exc}"
            _, feedback = self._hook("PostToolUse", name, args, result)
            if feedback:
                result += f"\n\n[hook] {feedback}"
        ok = not result.startswith(("ERROR", "DENIED"))
        self.emit({"type": "tool_result", "agent": "agent", "tool": name, "ok": ok,
                   "preview": _preview(name, result)})
        return truncate_middle(result)

    def _hook(self, event: str, name: str, args: dict[str, Any], result: str | None = None) -> tuple[bool, str]:
        """Runs the tool's hooks: (blocked?, message for the model)."""
        if not self.hooks:
            return False, ""
        if name == "mcp" and args.get("tool"):  # for hooks it is mcp__server__tool, as in Claude Code
            name, args = f"mcp__{args.get('server')}__{args['tool']}", dict(args.get("arguments") or {})
        payload = self.hooks.tool_payload(name, args)
        if result is not None:
            payload["tool_response"] = {"output": result, "success": not result.startswith(("ERROR", "DENIED"))}
        outcome = self.hooks.run(event, target=name, payload=payload)
        for note in outcome.notes:
            self.emit({"type": "info", "text": note})
        if outcome.blocked:
            self.emit({"type": "info", "text": f"a hook {'blocked' if event == 'PreToolUse' else 'flagged'} "
                                               f"{name}: {outcome.reason[:120]}"})
        return outcome.blocked, (outcome.reason if outcome.blocked else outcome.context)

    # ---------------------------------------------------------- permissions
    def _authorize(self, tool: str, args: dict[str, Any], summary: str, diff: str = "",
                   before: str | None = None, after: str | None = None) -> str | None:
        """None = allowed; otherwise the message to return to the model."""
        decision = self.policy.decide(tool, args)
        if decision.action == "allow":
            return None
        if decision.action == "deny":
            return f"DENIED: {decision.reason}"
        if self.approver is None:
            return "DENIED: this action needs user approval, which is not available in this context."
        command = str(args.get("command", ""))
        request = ApprovalRequest(tool=tool, args=args, summary=summary, diff=diff,
                                  dangerous=bool(command) and is_dangerous(command), before=before, after=after)
        with self._lock:
            answer, feedback = self.approver(request)
        if answer == "always":
            self.policy.remember(self.policy.rule_for(tool, args))
            return None
        if answer == "yes":
            return None
        self.user_denied = True
        return "DENIED by the user." + (f" User says: {feedback}" if feedback else
                                         " Ask what to do differently or try another approach.")

    # ------------------------------------------------------------------ tool
    def _rel(self, path: str) -> tuple[str, Path]:
        target = self.workspace.resolve(path)
        return display_path(self.root, target), target

    def _t_read_file(self, path: str, offset: int = 1, limit: int = DEFAULT_READ_LINES) -> str:
        rel, target = self._rel(path)
        if self.workspace.is_secret(target):
            return f"ERROR: {rel} looks like a secrets file; not reading it."
        if target.is_dir():
            return f"ERROR: {rel} is a directory; use list_files."
        if not target.is_file():
            return f"ERROR: file not found: {rel}"
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        self.read_paths.add(rel)
        start = max(1, int(offset))
        end = min(len(lines), start + max(1, int(limit)) - 1)
        body = "\n".join(f"{n:>5}→{lines[n - 1]}" for n in range(start, end + 1))
        more = f"\n… {len(lines) - end} more lines (use offset={end + 1})" if end < len(lines) else ""
        return f"{rel} ({len(lines)} lines)\n{body}{more}" if lines else f"{rel} (empty file)"

    def _t_list_files(self, path: str = ".", pattern: str = "*") -> str:
        base = self.workspace.resolve(path)
        out = []
        for item in sorted(base.rglob(pattern)):
            rel = item.relative_to(self.root if item.is_relative_to(self.root) else base)
            if any(part in IGNORED_DIRS for part in rel.parts) or not item.is_file():
                continue
            out.append(display_path(self.root, item))
            if len(out) >= 400:
                out.append("… (truncated, use a narrower path or pattern)")
                break
        return "\n".join(out) or "no files"

    def _t_skill(self, name: str, file: str | None = None) -> str:
        skill = self.skills.get(name.strip().lstrip("/").lower())
        if skill is None:
            return f"ERROR: unknown skill '{name}'. Available: {', '.join(self.skills) or 'none'}"
        self.emit({"type": "info", "text": f"skill {skill.name}" + (f" · {file}" if file else "")})
        return skill.read(file)

    def _t_mcp(self, server: str, tool: str | None = None, arguments: dict[str, Any] | str | None = None) -> str:
        srv = self.mcp.servers.get(server) if self.mcp else None
        if srv is None:
            names = ", ".join(self.mcp.servers) if self.mcp else ""
            return f"ERROR: unknown MCP server '{server}'. Available: {names or 'none'}"
        srv.connect()
        if srv.error:
            return f"ERROR: MCP server '{server}' is not available: {srv.error}"
        if not tool:
            return self.mcp.list_tools(srv)
        spec = srv.tool(tool)
        if spec is None:
            return f"ERROR: '{server}' has no tool '{tool}'.\n{self.mcp.list_tools(srv)}"
        if isinstance(arguments, str):  # small models sometimes send the JSON as a string
            try:
                arguments = json.loads(arguments)
            except ValueError:
                return "ERROR: `arguments` must be a JSON object"
        arguments = arguments if isinstance(arguments, dict) else {}
        read_only = bool((spec.get("annotations") or {}).get("readOnlyHint"))
        denied = self._authorize("mcp", {"server": server, "tool": tool, "arguments": arguments,
                                         "read_only": read_only},
                                 f"MCP({server}.{tool} {json.dumps(arguments, ensure_ascii=False)[:200]})")
        if denied:
            return denied
        return srv.call(tool, arguments)

    def _t_task(self, agent: str = "", prompt: str = "", subagent_type: str = "", description: str = "") -> str:
        name = (agent or subagent_type).strip().lower()  # subagent_type: the field name in Claude Code
        sub = self.subagents.get(name)
        if sub is None or not self.spawn:
            return f"ERROR: unknown sub-agent '{name}'. Available: {', '.join(self.subagents) or 'none'}"
        if not prompt.strip():
            return "ERROR: give the sub-agent a complete `prompt`."
        report, child = self.spawn(sub, prompt)
        for path in child.changed:  # its changes count as ours (footer, review, /undo)
            if path not in self.changed:
                self.changed.append(path)
        self.last_test = child.last_test or self.last_test
        self.user_denied = self.user_denied or child.user_denied
        changed = f"\n\n(files changed by {name}: {', '.join(child.changed)})" if child.changed else ""
        return f"Report from sub-agent {name}:\n{report}{changed}"

    def _t_grep(self, pattern: str, glob: str = "*") -> str:
        try:
            re.compile(pattern)
        except re.error as exc:
            return f"ERROR: invalid regex: {exc}"
        found = []
        for base in (self.root, *self.extra_dirs):  # in the extra folders too
            hits = Workspace(base).grep(pattern, glob, limit=80)
            if hits != "no matches":
                prefix = "" if base == self.root else display_path(self.root, base) + "/"
                found += [line if line == "…" else prefix + line for line in hits.splitlines()]
        return "\n".join(found) or "no matches"

    def _t_edit_file(self, path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
        rel, target = self._rel(path)
        if self.workspace.is_secret(target):
            return f"ERROR: refusing to edit secrets file {rel}"
        if not target.is_file():
            return f"ERROR: file not found: {rel}. Use write_file to create it."
        old_text = target.read_text(encoding="utf-8", errors="replace")
        if old_string == new_string:
            return "ERROR: old_string and new_string are identical."
        new_text, error = apply_edit(old_text, old_string, new_string, replace_all)
        if error:
            return f"ERROR: {error}"
        return self._commit_write("edit_file", rel, target, old_text, new_text,
                                  {"path": rel, "old_string": old_string, "new_string": new_string})

    def _t_write_file(self, path: str, content: str) -> str:
        rel, target = self._rel(path)
        if self.workspace.is_secret(target):
            return f"ERROR: refusing to write secrets file {rel}"
        old_text = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else ""
        if target.is_file() and old_text == content:
            return f"{rel} already has this content."
        return self._commit_write("write_file", rel, target, old_text, content, {"path": rel, "content": content},
                                  created=not target.exists())

    def _commit_write(self, tool: str, rel: str, target: Path, old: str, new: str, args: dict[str, Any],
                      created: bool = False) -> str:
        diff = unified_diff(old, new, rel)
        action = "Create" if created else "Update"
        denied = self._authorize(tool, args, f"{action}({rel})", diff, before=None if created else old, after=new)
        if denied:
            return denied
        self.checkpoints.before_write(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(new, encoding="utf-8")
        if rel not in self.changed:
            self.changed.append(rel)
        added, removed = diff_stats(diff)
        self.emit({"type": "diff", "path": rel, "action": action, "diff": diff, "added": added, "removed": removed})
        return f"{action.lower()}d {rel} (+{added} -{removed})"

    def _t_bash(self, command: str, timeout: int | None = None) -> str:
        denied = self._authorize("bash", {"command": command}, f"Bash({command})")
        if denied:
            return denied
        return self._run(command, timeout or self.bash_timeout)

    def _t_run_tests(self, command: str | None = None) -> str:
        command = command or detect_test_command(self.root, self.memory)
        if not command:
            return "NO TESTS: no test suite detected. Tell the user how to verify manually."
        denied = self._authorize("run_tests", {"command": command}, f"Test({command})")
        if denied:
            return denied
        result = self._run(command, max(self.bash_timeout, 300))
        ok = result.startswith("exit code 0")
        self.last_test = (command, ok, result)
        self.emit({"type": "tests", "command": command, "ok": ok})
        if ok and self.changed:  # helps small models understand when to stop
            result += ("\n\nAll tests pass. If the requested change is complete, STOP calling tools and give "
                       "your short final answer now.")
        return result

    def _run(self, command: str, timeout: int) -> str:
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "CI": "1", "NO_COLOR": "1"}
        try:
            proc = subprocess.run(command, shell=True, cwd=self.root, capture_output=True, text=True,
                                  timeout=timeout, env=env, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired as exc:
            out = (exc.stdout or b"").decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            return f"TIMEOUT after {timeout}s\n{out[-3000:]}"
        output = (proc.stdout + ("\n" + proc.stderr if proc.stderr else "")).strip()
        return f"exit code {proc.returncode}\n{output}"

    def _t_todo_write(self, todos: list[dict[str, Any]]) -> str:
        clean = []
        for item in todos:
            status = str(item.get("status", "pending")).lower()
            clean.append({"content": str(item.get("content", "")).strip(),
                          "status": status if status in ("pending", "in_progress", "completed") else "pending"})
        self.todos = [t for t in clean if t["content"]]
        self.emit({"type": "todo", "todos": self.todos})
        done = sum(t["status"] == "completed" for t in self.todos)
        return f"todo list updated ({done}/{len(self.todos)} completed)"

    def _t_web_fetch(self, url: str, offset: int = 0, prompt: str = "") -> str:
        # `prompt` is WebFetch's field in Claude Code: here the whole page arrives, in chunks
        if not self.web_fetch_fn:
            return "ERROR: web fetch not available (offline mode?)."
        url = url.strip() if "://" in url else "https://" + url.strip()  # "docs.python.org/3" is fine
        host = (urlparse(url).hostname or "").lower()
        if not re.fullmatch(r"[\w.-]+", host):  # no "*": the "always" permission applies to that site
            return f"ERROR: not a valid web address: {url}"
        denied = self._authorize("web_fetch", {"url": url, "host": host}, f"Fetch({url})")
        if denied:
            return denied
        start = max(0, int(offset or 0))
        if start == 0 or self._page[0] != url:  # later chunks come from the same copy of the page
            self._page = (url, self.web_fetch_fn(url))
        text = self._page[1]
        if text.startswith(("OFFLINE:", "ERROR:")):  # page not read: no "N characters"
            self._page = ("", "")
            return text
        part = text[start:start + WEB_PAGE_CHARS]
        rest = len(text) - start - len(part)
        more = f"\n\n… {rest} more characters: call web_fetch again with offset={start + len(part)}" if rest > 0 else ""
        return f"{url} ({len(text)} characters)\n{part}{more}" if part else f"{url}: no more text (offset {start})"

    def _t_preview(self, path: str = "", url: str = "", start: str = "", look: str = "") -> str:
        if not self.preview_fn:
            return "ERROR: preview not available (no Chrome, Edge or Chromium on this computer)."
        if url:
            url = url.strip() if "://" in url else "http://" + url.strip()
            if not is_local(url):
                return "ERROR: preview is for sites on this computer (localhost); use web_fetch for the others."
        elif start:
            return "ERROR: with `start`, also give the `url` where the server answers (e.g. http://localhost:5173)."
        base = self.root
        if path and not url:  # a file of the project or of an extra folder: serve its folder
            target = self.workspace.resolve(path)
            base = next(b for b in (self.root, *self.extra_dirs) if target.is_relative_to(b))
            path = target.relative_to(base).as_posix()
        if start:  # starting a server is a command like any other: same permissions as bash
            denied = self._authorize("bash", {"command": start}, f"Bash({start})")
            if denied:
                return denied
        return self.preview_fn(base, url=url, path=path, start=start, look=look)

    def _t_web_search(self, query: str) -> str:
        if not self.web_search_fn:
            return "ERROR: web search not available."
        return self.web_search_fn(query)


def apply_edit(text: str, old: str, new: str, replace_all: bool = False) -> tuple[str, str | None]:
    """Exact replacement, then lenient on trailing whitespace, then on indentation."""
    if not old:
        return text, "old_string is empty; use write_file to create a file."
    count = text.count(old)
    if count == 1 or (count > 1 and replace_all):
        return text.replace(old, new) if replace_all else text.replace(old, new, 1), None
    if count > 1:
        return text, (f"old_string matches {count} places. Add surrounding lines to make it unique, "
                      "or set replace_all=true.")
    # leniency: compare line by line ignoring trailing whitespace, then indentation
    lines = text.splitlines(keepends=True)
    old_lines = old.strip("\n").splitlines()
    for normalize in (str.rstrip, str.strip):
        target = [normalize(line) for line in old_lines]
        hits = [i for i in range(len(lines) - len(target) + 1)
                if [normalize(line.rstrip("\n")) for line in lines[i:i + len(target)]] == target]
        if len(hits) == 1:
            i = hits[0]
            original = "".join(lines[i:i + len(target)])
            replacement = new.strip("\n")
            if normalize is str.strip:  # reindent the new text like the original
                replacement = _reindent(replacement, _indent(lines[i]), _indent(old_lines[0]))
            if original.endswith("\n") and not replacement.endswith("\n"):
                replacement += "\n"
            return "".join(lines[:i]) + replacement + "".join(lines[i + len(target):]), None
        if len(hits) > 1:
            return text, f"old_string (ignoring whitespace) matches {len(hits)} places; add more context."
    close = difflib.get_close_matches(old_lines[0].strip() if old_lines else "",
                                      [line.strip() for line in lines], n=3, cutoff=0.6)
    hint = ("\nSimilar lines in the file:\n" + "\n".join(f"  {c}" for c in close)) if close else ""
    return text, f"old_string not found. Re-read the file with read_file and copy the text exactly.{hint}"


def _indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def _reindent(text: str, file_indent: str, given_indent: str) -> str:
    out = []
    for line in text.splitlines():
        if line.startswith(given_indent):
            line = file_indent + line[len(given_indent):]
        elif line.strip():
            line = file_indent + line.lstrip()
        out.append(line)
    return "\n".join(out)


def _display_args(name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name in ("edit_file", "write_file"):
        return {"path": args.get("path", "")}
    if name == "todo_write":
        return {"items": len(args.get("todos", []) or [])}
    return args


def _preview(name: str, result: str) -> str:
    if name == "read_file" and not result.startswith("ERROR"):
        return result.split("\n", 1)[0]
    if name in ("bash", "run_tests"):
        lines = [line for line in result.splitlines() if line.strip() and not line.startswith("All tests pass")]
        picked = lines[:1] + (lines[-1:] if len(lines) > 1 else [])
        return " · ".join(picked)[:200]
    return result[:200]
