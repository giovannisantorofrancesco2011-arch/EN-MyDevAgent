"""Claude Code-style terminal UI for MyDevAgent.

Architecture:
  - main thread: input (prompt_toolkit), drawing (rich.Live), permission confirmations
  - worker thread: AgentRunner.run() (agent mode) or Orchestrator.run() (chat mode);
    communicates only through a queue.Queue of events, chunks and confirmation requests
  - Esc / Ctrl+C while working set a threading.Event → the team stops
"""

from __future__ import annotations

import contextlib
import getpass
import json
import queue
import re
import subprocess
import sys
import threading
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory, InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from rich.console import Console
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from .. import health, plugins, templates
from .. import hooks as hooks_mod
from .. import mcp as mcp_mod
from .. import stats as stats_mod
from .. import update as update_mod
from ..agent import CheckpointStore, PermissionPolicy
from ..agent.context import append_memory, collect_attachments, read_memory
from ..agent.permissions import MODE_LABELS, ApprovalRequest
from ..agent.permissions import MODES as PERMISSION_MODES
from ..agent.runner import LEARN_PROMPT, AgentRunner
from ..config import load_settings
from ..hooks import Hooks
from ..mcp import McpManager
from ..orchestrator import Orchestrator
from ..skills import load_skills
from ..subagents import load_subagents
from ..tools import preview as preview_mod
from ..tools.filesystem import Workspace, WorkspaceError, display_path
from . import extras, mascot, multi, statsview
from .apply import apply_answer
from .completion import DevCompleter
from .keys import EscWatcher
from .render import ACCENT, THEME, TurnRenderer, markdown, render_diff, set_theme
from .session import Session, list_sessions, state_dir

COMMANDS = {
    "/help": "show commands and shortcuts",
    "/fast": "fast mode (1 agent)",
    "/balanced": "standard team: plan, edits, tests and review",
    "/deep": "full team: security, performance, edge cases",
    "/ultra-deep": "35 agents: plan debate, mega review, web search if needed (slow)",
    "/auto": "mode chosen by the router (default)",
    "/plan": "plan mode: the agent reads and proposes, changes nothing",
    "/permissions": "permission mode (ask · auto-edit · plan · auto) and saved rules",
    "/undo": "undo the file changes of the last turn",
    "/rewind": "go back to an earlier point (undo several turns)",
    "/diff": "all the changes made in this session",
    "/chat": "chat mode: answers without touching files (use /apply to save them)",
    "/agent": "agent mode: works directly on the files (default)",
    "/apply": "write the files of the last answer to disk (chat mode)",
    "/learn": "learn mode: Vio explains what it does and lets you write a piece of the code · /learn off",
    "/add-dir": "also work on another folder (e.g. the backend) · /add-dir <folder> · /add-dir remove <folder>",
    "/preview": "open the project's site in the browser (on localhost) · /preview <file.html | url>",
    "/new": "create a ready-made project: website, game, bot-discord, api, python · /new <template> [name]",
    "/multi": "multiplayer: friends on your network follow the session from the browser and write to the agent "
              "· /multi stop",
    "/init": "create MYDEVAGENT.md with the project's commands and conventions",
    "/memory": "show the project memory · /memory <text> adds a note",
    "/compact": "summarize the conversation to free up context",
    "/context": "how much of the model's context you are using (instructions, summary, messages, free)",
    "/model": "change the main model · /model <name> [--save]",
    "/models": "installed models and models in use",
    "/pull": "download a model from Ollama · /pull <name>",
    "/agents": "list the team agents and the subagents (Claude Code format)",
    "/files": "files attached to the last message",
    "/cost": "session tokens and time",
    "/stats": "stats: requests, tokens, files, day streaks and activity graph · /stats 7 · /stats 30 · /stats all",
    "/think": "show/hide the model's reasoning",
    "/index": "index the project for semantic search",
    "/doctor": "check backend, models, network, sandbox",
    "/theme": "dark / light theme",
    "/vio": "say hi to Vio, the mascot (and pet it)",
    "/skill": "available skills · /skill <name> [request] to use one",
    "/plugin": "plugins (Claude Code format) · /plugin install <user/repo> · update · remove",
    "/hooks": "active hooks (automatic commands) · /hooks trust enables the project's",
    "/mcp": "MCP servers (external tools) · /mcp reload · /mcp trust",
    "/update": "update MyDevAgent to the latest version (models and settings are kept)",
    "/resume": "resume a previous session in this folder",
    "/export": "save the conversation as Markdown",
    "/clear": "new conversation",
    "/exit": "quit",
}
MODES = ("auto", "fast", "balanced", "deep", "ultra-deep")
SHELL_TIMEOUT = 120
MAX_SHELL_OUTPUT = 8000
NOTIFY_AFTER_S = 20
AUTO_COMPACT_MESSAGES = 20
GUEST_TURN = object()  # the input closes by itself: a friend connected via /multi wrote something


class TuiApp:
    def __init__(
        self,
        orchestrator: Orchestrator | None = None,
        *,
        profile: str | None = None,
        console: Console | None = None,
        prompt_input=None,
        prompt_output=None,
        ask: Callable[[str], str] | None = None,
        session: Session | None = None,
        root: Path | None = None,
        permission_mode: str = "ask",
        agent_mode: bool = True,
        background: bool = True,
        startup_check: bool | None = None,
        extra_dirs: list[Path] | None = None,
    ) -> None:
        self.orch = orchestrator or Orchestrator(load_settings(overrides={"profile": profile} if profile else None))
        self.console = console or Console()
        self.root = (root or Path.cwd()).resolve()
        self.session = session or Session(cwd=str(self.root))
        self.mode = self.session.mode
        self.agent_mode = agent_mode
        self.policy = PermissionPolicy(mode=permission_mode, root=self.root)
        self.checkpoints = CheckpointStore(self.root)
        existing = self.checkpoints.list()
        self.session_start_cp = (existing[-1].id + 1) if existing else 1
        self.show_thinking = False
        self.last_answer = next((m["content"] for m in reversed(self.session.history)
                                 if m["role"] == "assistant"), "")
        self.last_files: dict[str, str] = {}
        self.pending_context: dict[str, str] = {}  # output of `!` commands to attach to the next turn
        self.stats = {"tokens": 0, "turns": 0, "seconds": 0.0}
        self.ctx_percent: int | None = None  # for the bottom bar: refreshed after every turn and with /context
        self.turn_log: list[dict[str, Any]] = []  # this session's requests, for /stats
        self.names = {a.key: a.name for a in self.orch.registry}
        self.model = self.orch.settings.resolve_model("main")[0]
        self.online: bool | None = None
        self.branch = self._git_branch()
        self._ask = ask
        self._startup_check = background if startup_check is None else startup_check
        self._ctrl_c_at = 0.0
        self.custom = extras.custom_commands(self.root)
        self.hooks = Hooks(self.root)
        self.mcp = McpManager(self.root, configs={})  # started in start_project, after your yes
        self._load_prefs()
        self.learn = bool(self._prefs().get("learn"))  # learn mode
        self.extra_dirs = self._saved_dirs()  # extra folders: the ones remembered for the project + --add-dir
        self.extra_dirs += [d for d in (Path(p).expanduser().resolve() for p in extra_dirs or [])
                            if d.is_dir() and d != self.root and d not in self.extra_dirs]
        all_commands = {**COMMANDS, **{k: v[0] for k, v in self.custom.items()}}
        self.completer = DevCompleter(all_commands, dict(self.orch.registry.by_alias), self.root)
        names = {"/skill": lambda: list(load_skills(self.root)), "/new": lambda: list(templates.TEMPLATES)}
        self.completer.arguments = {**names, "/skills": names["/skill"], "/stats": lambda: ["7", "30", "all"]}
        self._vio_event: tuple[str | None, str, tuple] | None = None
        self.room: multi.Room | None = None  # multiplayer (/multi)
        self._at_prompt = False
        self._draft = ""  # what you were typing when a friend's message arrived
        self.say("Hi, I'm Vio! Type below what you want to do.")
        self.prompt = self._build_prompt(prompt_input, prompt_output, animate=background)
        if background:
            threading.Thread(target=self._check_online, daemon=True).start()
            extras.warmup(self.orch.llm, self.orch.settings)
            threading.Thread(target=self._auto_index, daemon=True).start()

    # ------------------------------------------------------------ setup
    def _build_prompt(self, prompt_input, prompt_output, animate: bool = True) -> PromptSession:
        keys = KeyBindings()

        @keys.add("escape", "enter")  # Alt+Enter → new line
        def _newline(event) -> None:
            event.current_buffer.insert_text("\n")

        @keys.add("c-j")  # Ctrl+J → new line (works in every terminal)
        def _newline2(event) -> None:
            event.current_buffer.insert_text("\n")

        @keys.add("s-tab")  # Shift+Tab → switch permission mode, like Claude Code
        def _cycle(event) -> None:
            self.policy.next_mode()
            event.app.invalidate()

        @keys.add("escape", "escape")  # Esc Esc → /rewind
        def _rewind(event) -> None:
            buffer = event.current_buffer
            buffer.text = "/rewind"
            buffer.validate_and_handle()

        try:
            history = FileHistory(str(state_dir() / "history"))
        except OSError:
            history = InMemoryHistory()
        return PromptSession(
            history=history,
            completer=self.completer,
            complete_while_typing=True,
            key_bindings=keys,
            bottom_toolbar=self.toolbar,
            erase_when_done=True,  # Vio stays only in the input area: we reprint the question in the history
            refresh_interval=0.5 if animate else None,  # tentacles and eyelids
            reserve_space_for_menu=4,
            color_depth=_color_depth(),
            placeholder=[("class:placeholder", "Ask something… / commands · @ files · ! shell · # memory")],
            style=_style(),
            input=prompt_input,
            output=prompt_output,
        )

    def _prefs_path(self) -> Path:
        return state_dir() / "config.json"

    def _prefs(self) -> dict[str, Any]:
        try:
            return json.loads(self._prefs_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _load_prefs(self) -> None:
        set_theme(self._prefs().get("theme", "dark"))

    def _save_pref(self, key: str, value: Any) -> None:
        path = self._prefs_path()
        try:
            prefs = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        except ValueError:
            prefs = {}
        prefs[key] = value
        path.write_text(json.dumps(prefs, indent=1), encoding="utf-8")

    def _saved_dirs(self) -> list[Path]:
        saved = self._prefs().get("dirs", {}).get(str(self.root), [])
        return [Path(d) for d in saved if Path(d).is_dir()]

    def _add_dir(self, arg: str) -> None:
        """/add-dir: other folders where the agent reads, searches and edits (remembered for this project)."""
        c = self.console
        action, _, rest = arg.partition(" ")
        remove = action.lower() in ("remove", "rm")
        target = (rest if remove else arg).strip().strip('"')
        if target:
            folder = (self.root / Path(target).expanduser()).resolve()
            if remove:
                self.extra_dirs = [d for d in self.extra_dirs if d != folder]
            elif not folder.is_dir():
                c.print(f"[red]⎿  can't find the folder {escape(str(folder))}[/]", highlight=False)
                return
            elif folder == self.root or folder in self.extra_dirs:
                c.print("[dim]⎿  this folder is already there[/]")
                return
            else:
                self.extra_dirs.append(folder)
            dirs = self._prefs().get("dirs", {})
            dirs[str(self.root)] = [str(d) for d in self.extra_dirs]
            self._save_pref("dirs", dirs)
        if not self.extra_dirs:
            c.print("[dim]⎿  The agent works only in this folder. /add-dir <folder> adds another one, "
                    "for example /add-dir ../backend[/]", highlight=False)
            return
        if target and not remove:
            c.print(f"[green]⏺[/] Added {escape(display_path(self.root, folder))}: the agent can read, search "
                    "and edit there too (with the usual permissions)", highlight=False)
            self.say("Now I'm working on several folders at once!", "love")
        c.print("[dim]⎿  folders: this project · "
                + " · ".join(escape(display_path(self.root, d)) for d in self.extra_dirs)
                + " · /add-dir remove <folder> to drop one[/]", highlight=False)

    def _check_online(self) -> None:
        try:
            self.online = self.orch.toolbox.ctx.connectivity.online()
        except Exception:
            self.online = False

    def _auto_index(self) -> None:
        """Builds the RAG index in the background the first time a project is opened (if small enough)."""
        rag = self.orch.settings.tools.rag
        index_file = self.root / rag.index_dir / "index.json"
        if not rag.enabled or index_file.exists() or not (self.root / ".git").exists():
            return
        try:
            count = sum(1 for _ in Workspace(self.root).iter_files())
            if count > 2000:
                return
            from ..tools.rag import CodeIndex

            settings = self.orch.settings.model_copy(deep=True)
            settings.tools.filesystem.root = str(self.root)
            extras_private_dir(self.root)
            CodeIndex.for_workspace(settings, self.orch.llm).build()
        except Exception:
            pass

    def _git_branch(self) -> str:
        try:
            out = subprocess.run(["git", "-C", str(self.root), "rev-parse", "--abbrev-ref", "HEAD"],
                                 capture_output=True, text=True, timeout=3)
            return out.stdout.strip() if out.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            return ""

    # -------------------------------------------------------------- view
    def expression(self) -> str:
        """Vio's expression: the permission mode, or chat."""
        return self.policy.mode if self.agent_mode else "chat"

    def _width(self) -> int:
        try:
            from prompt_toolkit.application import get_app

            return max(20, get_app().output.get_size().columns)
        except Exception:
            return self.console.width

    # ------------------------------------------------------------------ Vio
    def _mode_key(self) -> tuple:
        return (self.policy.mode, self.agent_mode, self.mode)

    def say(self, text: str, expression: str | None = None) -> None:
        """What Vio says above the input. Lasts until you change mode."""
        self._vio_event = (expression, text, self._mode_key())
        if self.room:
            self.room.publish({"kind": "vio", "text": text})

    def vio_state(self) -> tuple[str, str]:
        """Vio's (expression, line) right now."""
        if self._vio_event and self._vio_event[2] == self._mode_key():
            expression, text, _ = self._vio_event
            return expression or self.expression(), text
        return self.expression(), mascot.SAYS[self.expression()]

    def prompt_message(self):
        """Vio with its speech bubble, a rule, then the input: like Claude Code and BluAgent."""
        now = time.monotonic()
        expression, text = self.vio_state()
        if expression in ("ask", "chat", "auto-edit", "done") and int(now * 2) % 12 == 0:
            expression = "blink"
        width = self._width()
        label = (f"{self.policy.mode} mode" if self.agent_mode else "chat mode") + (" · learn" if self.learn else "")
        room = max(10, width - mascot.WIDTH - 4)
        speech = [
            [],
            [("class:vio.name", mascot.NAME), ("class:tb.dim", f" · {label} · team {self.mode}"[:room])],
            [("class:vio.say", text if len(text) <= room else text[: room - 1] + "…")],
            [],
        ]
        out: list[tuple[str, str]] = []
        for line, bubble in zip(mascot.fragments(expression, int(now) % 2), speech, strict=True):
            out += [("", " ")] + line + [("", "  ")] + bubble + [("", "\n")]
        return out + [("class:rule", "─" * width + "\n"), ("class:prompt", "› ")]

    def toolbar(self):
        net = {True: ("class:tb.ok", "✓ online"), False: ("class:tb.warn", "○ offline"),
               None: ("class:tb.dim", "… network")}[self.online]
        perm = self.policy.mode
        perm_style = {"ask": "class:tb.key", "auto-edit": "class:tb.ok", "plan": "class:tb.plan",
                      "auto": "class:tb.warn"}[perm]
        arrows = {"ask": "⏵", "auto-edit": "⏵⏵", "plan": "⏸", "auto": "⏵⏵⏵"}[perm]
        parts = [("class:rule", "─" * self._width() + "\n"), ("", " ")]
        if self.agent_mode:
            parts += [(perm_style, f"{arrows} {perm} mode"), ("class:tb.dim", " (shift+tab to switch)")]
        else:
            parts += [("class:tb.key", "⏵ chat mode"), ("class:tb.dim", " (/agent to edit files)")]
        parts += [
            ("class:tb.dim", " · "), ("class:tb", self.model),
            ("class:tb.dim", " · team "), ("class:tb.key", self.mode), ("class:tb.dim", " · "), net,
            ("class:tb.dim", f" · ~{self.stats['tokens']:,} tok"),
            *([("class:tb.dim", " · ctx "), ("class:tb.warn" if self.ctx_percent >= 70 else "class:tb.dim",
                                                f"{self.ctx_percent}%")] if self.ctx_percent is not None else []),
            ("class:tb.dim", " · / for commands"),
        ]
        if self.branch:
            parts.append(("class:tb.dim", f" ·  {self.branch}"))
        return parts

    def banner(self) -> None:
        from .. import __version__

        settings = self.orch.settings
        memory = "MYDEVAGENT.md ✓" if read_memory(self.root) else "no memory (/init to create it)"
        skills = load_skills(self.root)
        skill_line = (f"{len(skills)} loaded · /skill to see them" if skills
                      else "none (.mydevagent/skills/<name>/SKILL.md)")
        found = plugins.load_plugins(self.root)
        plugin_line = (f"{len(found)} active · /plugin to see them" if found
                       else "none (/plugin install <user/repo>)")
        tiers = " · ".join(f"{t} {settings.resolve_model(t)[0]}" for t in ("fast", "reasoning"))
        body = (
            f"[bold {ACCENT}]✻[/] [bold]Welcome to MyDevAgent[/]  [dim]v{__version__} · "
            f"{len(self.orch.registry)} agents · local-first[/]\n\n"
            f"[dim]cwd:[/]      {escape(str(self.root))}\n"
            f"[dim]profile:[/]  {settings.profile} · [dim]model:[/] {escape(self.model)} [dim]· {escape(tiers)}[/]\n"
            f"[dim]hardware:[/] {self._hardware}\n"
            f"[dim]memory:[/]   {memory}\n"
            f"[dim]skills:[/]   {skill_line}\n"
            f"[dim]plugins:[/]  {plugin_line}\n"
            + (f"[dim]folders:[/]  {escape(' · '.join(display_path(self.root, d) for d in self.extra_dirs))} "
               "[dim](/add-dir)[/]\n" if self.extra_dirs else "") + "\n"
            "[dim]Tips:[/]\n"
            f"  [{ACCENT}]•[/] ask to change the code: the agent reads, edits, runs the tests and shows you the diffs\n"
            f"  [{ACCENT}]•[/] [bold]/[/] commands · [bold]@file[/] attach · [bold]![/]shell · [bold]#[/]memory note\n"
            f"  [{ACCENT}]•[/] [bold]Shift+Tab[/] mode · [bold]Esc[/] interrupt · [bold]/undo[/] undo · "
            "[bold]Esc Esc[/] go back · [bold]/vio[/] say hi to the mascot"
        )
        self.console.print(Panel(body, border_style=ACCENT, padding=(1, 2)))
        if self.session.history:
            turns = len(self.session.history) // 2
            self.console.print(f"[dim]⎿  Resumed session '{escape(self.session.title)}' ({turns} turns)[/]")

    @property
    def _hardware(self) -> str:
        if not hasattr(self, "_hw_text"):
            hw = health.detect_hardware()
            self._hw_text = (f"GPU {hw.gpu_gb:.0f} GB" if hw.gpu_gb else
                             f"Apple {hw.apple_gb:.0f} GB" if hw.apple_gb else f"RAM {hw.ram_gb:.0f} GB")
        return self._hw_text

    # --------------------------------------------------------- user questions
    def _input(self, message: str) -> str:
        answer = self.prompt.prompt(message, bottom_toolbar=None, completer=None, placeholder="").strip()
        self.console.print(f"[dim]{escape(message)}[/]{escape(answer)}")  # the input gets erased: reprint it
        return answer

    def ask(self, question: str, allow_always: bool = True) -> str:
        if self._ask:
            return self._ask(question)
        options = "  [dim]1[/] Yes" + ("  [dim]2[/] Yes, and don't ask again" if allow_always else "") + "  [dim]3[/] No"
        self.console.print(f"[bold]{escape(question)}[/]{options}")
        answer = self._input("  › ")
        valid = ("1", "2", "3") if allow_always else ("1", "3")
        return answer if answer in valid else "3"

    def ask_approval(self, req: ApprovalRequest) -> tuple[str, str]:
        """Confirmation of a change or a command (called from the UI thread)."""
        c = self.console
        if req.tool in ("edit_file", "write_file"):
            path = req.args.get("path", "")
            c.print(f"\n[{ACCENT}]⏺[/] [bold]{escape(req.summary)}[/]")
            if req.diff:
                c.print(render_diff(req.diff, max_lines=80))
            question = f"Apply the change to {path}?"
        elif req.tool == "web_fetch":
            question = f"Read pages from {req.args.get('host', 'this site')}?"
        elif req.tool == "mcp":
            question = f"Use the MCP tool {req.args.get('server')}.{req.args.get('tool')}?"
        else:  # command already shown by the tool's ⏺ Bash(...)/Test(...) line
            if req.dangerous:
                c.print("  [red]⚠ potentially destructive command: check carefully[/]")
            question = "Run this command?"
        choice = self.ask(question, allow_always=not req.dangerous)
        if choice == "1":
            return "yes", ""
        if choice == "2":
            return "always", ""
        feedback = "" if self._ask else self._input("  what should I do instead? (Enter to skip) › ")
        return "no", feedback

    # ---------------------------------------------------------- commands
    def handle_command(self, text: str) -> bool:
        """Runs a `/` command. Returns False to quit."""
        cmd, _, arg = text.partition(" ")
        cmd, arg = cmd.lower(), arg.strip()
        if ":" in cmd and cmd not in self.custom:  # /plugin:command, like in Claude Code
            cmd = "/" + cmd.rsplit(":", 1)[1]
        c = self.console
        if cmd in ("/exit", "/quit"):
            return False
        if cmd == "/help":
            table = Table(show_header=False, box=None, padding=(0, 2))
            for name, desc in {**COMMANDS, **{k: v[0] for k, v in self.custom.items()}}.items():
                table.add_row(f"[bold]{name}[/]", f"[dim]{escape(desc)}[/]")
            c.print(table)
            c.print("[dim]Shortcuts: Tab completes · ↑/↓ history · Alt+Enter/Ctrl+J new line · Shift+Tab "
                    "permissions · Esc or Ctrl+C interrupts · Esc Esc goes back · Ctrl+D quits[/]")
        elif cmd[1:] in MODES and not arg:
            self.mode = self.session.mode = cmd[1:]
            key = "auto-team" if self.mode == "auto" else self.mode
            self.say(mascot.SAYS[key], key)
            c.print(f"[dim]⎿  team: {self.mode}[/]")
        elif cmd in ("/fast", "/balanced", "/deep", "/ultra-deep") and arg:
            self.submit(text)  # "/deep build an API" → the router handles the inline command
        elif cmd == "/plan":
            self.policy.mode = "ask" if self.policy.mode == "plan" else "plan"
            c.print(f"[dim]⎿  {self.policy.mode} mode: {mascot.SAYS[self.policy.mode]}[/]")
            if arg:
                self.submit(arg)
        elif cmd == "/permissions":
            if arg in PERMISSION_MODES:
                self.policy.mode = arg
            c.print(f"[dim]⎿  mode: [bold]{self.policy.mode}[/] ({MODE_LABELS[self.policy.mode]}) · "
                    f"available: {', '.join(PERMISSION_MODES)}[/]")
            rules = self.policy.allow_rules
            c.print("[dim]   'always allow' rules: " + (", ".join(rules) if rules else "none") + "[/]")
        elif cmd == "/undo":
            undone = self.checkpoints.undo()
            if not undone:
                c.print("[dim]⎿  Nothing to undo.[/]")
            else:
                cp, files = undone
                c.print(f"[green]⏺[/] Undid '{escape(cp.label)}'\n  [dim]⎿  restored: {', '.join(files)}[/]")
                self.completer.refresh()
        elif cmd == "/rewind":
            self._rewind()
        elif cmd == "/diff":
            diff = self.checkpoints.session_diff(self.session_start_cp)
            if diff.strip():
                c.print(Syntax(diff, "diff", theme=THEME["diff"], word_wrap=True, background_color="default"))
            else:
                c.print("[dim]⎿  No changes in this session.[/]")
        elif cmd in ("/chat", "/agent"):
            self.agent_mode = cmd == "/agent"
            c.print("[dim]⎿  " + ("agent mode: I work directly on the files" if self.agent_mode
                                   else "chat mode: I answer without changing files") + "[/]")
        elif cmd == "/apply":
            if not self.last_answer:
                c.print("[dim]⎿  No answer to apply.[/]")
            else:
                written = apply_answer(self.last_answer, self.orch.registry, self.root, c, self.ask)
                if written:
                    self.completer.refresh()
        elif cmd == "/new":
            self._new(arg)
        elif cmd == "/add-dir":
            self._add_dir(arg)
        elif cmd == "/multi":
            self._multi(arg)
        elif cmd == "/preview":
            self._preview(arg)
        elif cmd == "/learn":
            self.learn = arg.lower() in ("on", "yes") or (arg.lower() not in ("off", "no") and not self.learn)
            self._save_pref("learn", self.learn)
            if self.learn:
                c.print("[dim]⎿  learn mode: I explain what I do and leave you a piece to write (look for "
                        "TODO(you)) · /learn off to turn it off[/]", highlight=False)
                self.say("Let's learn together! You write some of the pieces.", "love")
            else:
                c.print("[dim]⎿  learn mode off: I write all the code[/]")
                self.say("OK, I'm back to writing all the code.", "done")
        elif cmd == "/init":
            was_agent, self.agent_mode = self.agent_mode, True
            self.submit(extras.INIT_TASK, display="/init")
            self.agent_mode = was_agent
        elif cmd == "/memory":
            if arg:
                path = append_memory(self.root, arg)
                c.print(f"[dim]⎿  note added to {path.name}[/]")
            else:
                memory = read_memory(self.root)
                c.print(Panel(escape(memory) if memory else "[dim]No memory. Use /init or #note.[/]",
                              title="project memory", border_style="grey50", expand=False))
        elif cmd == "/compact":
            self._compact(manual=True)
        elif cmd == "/context":
            self._show_context()
        elif cmd == "/model":
            if not arg:
                c.print(f"[dim]⎿  main model: {self.model} · /models for the list[/]")
            else:
                save = "--save" in arg.split()
                name = " ".join(w for w in arg.split() if w != "--save")
                profile = self.orch.settings.active_profile
                profile.main = name
                if isinstance(profile.reasoning, str) and profile.reasoning == self.model:
                    profile.reasoning = name
                self.model = name
                if save:
                    path = health.save_env({"MYDEVAGENT_MODEL_MAIN": name})
                    c.print(f"[dim]⎿  main model: {escape(name)} (saved in {escape(str(path))})[/]")
                else:
                    c.print(f"[dim]⎿  main model: {escape(name)} (this session only · "
                            "--save to remember it)[/]")
                extras.warmup(self.orch.llm, self.orch.settings)
        elif cmd == "/models":
            self._models()
        elif cmd == "/pull":
            if not arg:
                c.print("[dim]⎿  usage: /pull <name>, e.g. /pull qwen2.5-coder:7b[/]")
            else:
                self.pull_models(arg.split())
        elif cmd == "/agents":
            table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
            for col in ("#", "agent", "group", "stage", "aliases"):
                table.add_column(col)
            for a in self.orch.registry:
                group = "core" if a.group == "core" else "ultra"
                table.add_row(str(a.id), a.name, group, a.stage, " ".join("@" + x for x in a.aliases))
            c.print(table)
            subs = load_subagents(self.root)
            if subs:
                table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2), expand=True)
                for col in ("subagent", "from"):
                    table.add_column(col, no_wrap=True)
                table.add_column("tools", no_wrap=True, overflow="ellipsis", max_width=28)
                table.add_column("description", no_wrap=True, overflow="ellipsis", ratio=1)
                for sub in subs.values():
                    table.add_row(f"[{ACCENT}]{escape(sub.name)}[/]", escape(sub.source),
                                  escape(", ".join(sub.tools) if sub.tools else "all"), escape(sub.description))
                c.print()
                c.print(table)
                c.print("[dim]The agent calls them on its own when needed: they work in a separate context and "
                        "report the result back to it[/]")
        elif cmd == "/files":
            c.print("[dim]⎿  " + (", ".join(self.last_files) or "no attached files") + "[/]")
        elif cmd == "/stats":
            self._stats(arg)
        elif cmd == "/cost":
            c.print(f"[dim]⎿  {self.stats['turns']} turns · ~{self.stats['tokens']:,} tokens · "
                    f"{self.stats['seconds']:.0f}s of work[/]")
        elif cmd == "/think":
            self.show_thinking = not self.show_thinking
            c.print(f"[dim]⎿  show reasoning: {'yes' if self.show_thinking else 'no'}[/]")
        elif cmd == "/index":
            self._index()
        elif cmd == "/doctor":
            from ..cli import doctor

            doctor(profile=None)
        elif cmd in ("/skill", "/skills"):
            self._skill(arg)
        elif cmd in ("/plugin", "/plugins"):
            self._plugin(arg)
        elif cmd in ("/hooks", "/hook"):
            self._hooks(arg)
        elif cmd == "/mcp":
            self._mcp(arg)
        elif cmd == "/update":
            self._update()
        elif cmd == "/vio":
            self._pats = getattr(self, "_pats", 0) + 1
            self.say(mascot.PATS[(self._pats - 1) % len(mascot.PATS)], "love")
        elif cmd == "/theme":
            name = arg or ("light" if THEME["diff"] == "ansi_dark" else "dark")
            set_theme(name)
            self._save_pref("theme", name)
            c.print(f"[dim]⎿  theme: {name}[/]")
        elif cmd == "/resume":
            self._resume()
        elif cmd == "/export":
            path = self.root / f"mydevagent-{self.session.id}.md"
            path.write_text(self.session.to_markdown(), encoding="utf-8")
            c.print(f"[dim]⎿  saved to {escape(str(path))}[/]")
        elif cmd == "/clear":
            self.session = Session(cwd=str(self.root), mode=self.mode)
            self.last_answer, self.last_files, self.pending_context = "", {}, {}
            self.ctx_percent = None
            c.clear()
            self.banner()
        elif cmd in self.custom:
            self.submit(extras.expand_command(self.custom[cmd][1], arg), display=text)
        else:
            c.print(f"[red]⎿  unknown command: {escape(cmd)}[/] [dim](/help)[/]")
        return True

    def _stats(self, arg: str) -> None:
        choice = arg.lower() or "all"
        if choice not in statsview.RANGES:
            self.console.print("[red]⎿  usage: /stats · /stats 7 (last 7 days) · /stats 30 · /stats all[/]")
            return
        days = statsview.RANGES[choice]
        history = stats_mod.summarize(stats_mod.load())
        total = stats_mod.summarize(stats_mod.load(days)) if days else history
        self.console.print(statsview.render(stats_mod.summarize(self.turn_log), total, history,
                                            label=f"last {days} days" if days else "all time",
                                            width=self.console.width))
        streak, _ = history.streaks()
        self.say(f"{streak} days in a row together! Let's keep it up." if streak > 1
                 else "Here's what we've done together!", "love")

    def _skill(self, arg: str) -> None:
        skills = load_skills(self.root)
        name, _, request = arg.partition(" ")
        if not name or name == "list":
            if not skills:
                self.console.print("[dim]⎿  No skills. Create one in .mydevagent/skills/<name>/SKILL.md "
                                   "(guide: docs/TUI.md)[/]")
                return
            table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2), expand=True)
            for col in ("skill", "from"):
                table.add_column(col, no_wrap=True)
            table.add_column("description", no_wrap=True, overflow="ellipsis", ratio=1)
            for skill in skills.values():
                table.add_row(f"[{ACCENT}]{escape(skill.name)}[/]", skill.source, escape(skill.description))
            self.console.print(table)
            self.console.print("[dim]The agent uses them on its own when needed · /skill <name> <request> to "
                               "force one[/]", highlight=False)
            return
        skill = skills.get(name.lower())
        if skill is None:
            self.console.print(f"[red]⎿  unknown skill: {escape(name)}[/] [dim](/skill for the list)[/]")
            return
        task = request.strip() or "Apply this skill to the current project."
        self.say(f"Using the skill {skill.name}.", "think")
        self.submit(f"Follow the skill `{skill.name}` for this request.\n\n{skill.read()}\n\n# Request\n{task}",
                    display=f"/skill {arg}")

    def _new(self, arg: str) -> None:
        kind, _, name = arg.partition(" ")
        if kind.lower() not in templates.TEMPLATES:
            if kind:
                self.console.print(f"[red]⎿  unknown template: {escape(kind)}[/]")
            table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
            table.add_column("template")
            table.add_column("what you get")
            for key, description in templates.TEMPLATES.items():
                table.add_row(f"[{ACCENT}]{key}[/]", description)
            self.console.print(table)
            self.console.print("[dim]/new <template> \\[name] · created in a new folder here (or in this one, if it's "
                               "empty), then I work inside it[/]", highlight=False)
            return
        try:
            dest = templates.target_for(self.root, kind.lower(), name.strip())
            created = templates.create(kind.lower(), dest)
        except (ValueError, OSError) as exc:
            self.console.print(f"[red]⎿  {escape(str(exc))}[/]", highlight=False)
            return
        self.console.print(f"[green]⏺[/] Created the '{kind.lower()}' project in {escape(str(dest))}", highlight=False)
        self.console.print(f"  [dim]⎿  {escape(', '.join(created))}[/]", highlight=False)
        if dest != self.root:
            self.switch_root(dest)
        self.say("Project ready! Tell me how you want to customize it.", "love")

    def _preview(self, arg: str) -> None:
        """The project's site in the browser: an HTML file served on localhost, or the url of a running server."""
        target = arg.strip()
        if "://" in target or re.match(r"(localhost|127\.0\.0\.1)(:\d+)?(/|$)", target):
            url = target if "://" in target else f"http://{target}"
        else:
            page = target or "index.html"
            try:
                path = Workspace(self.root).resolve(page)
            except WorkspaceError:
                path = None
            if path is None or not path.is_file():
                self.console.print(f"[red]⎿  can't find {escape(page)}[/] [dim]· /preview <file.html> or "
                                   "/preview <url>, e.g. /preview localhost:5173[/]", highlight=False)
                return
            url = f"{preview_mod.serve(self.root)}/{quote(path.relative_to(self.root).as_posix())}"
        opened = webbrowser.open(url)
        self.console.print(f"[green]⏺[/] Preview: {escape(url)}", highlight=False)
        self.console.print("  [dim]⎿  " + ("opened in the browser" if opened else "open it in the browser")
                           + " · stays up while MyDevAgent is open · the agent looks at it on its own when "
                             "needed[/]", highlight=False)

    def _multi(self, arg: str) -> None:
        """/multi: friends on your network follow the session from the browser and write to the agent."""
        c = self.console
        if arg.lower() in ("stop", "off", "close"):
            if self.room:
                self.room.system(f"{self.room.host} closed the session")
                self.room.close()
                self.room = None
                self.say("Session closed: it's just the two of us again.", "done")
            c.print("[dim]⎿  multiplayer off[/]")
            return
        opened = self.room is None
        if opened:
            try:
                host = getpass.getuser()
            except Exception:
                host = "host"
            try:
                self.room = multi.Room(host)
            except OSError as exc:
                c.print(f"[red]⎿  can't open the session: {escape(str(exc))}[/]", highlight=False)
                return
            self.room.on_message = self._wake
            self.room.on_join = lambda name: self.say(f"{name} joined! Now we work together.", "love")
            for m in self.session.history[-4:]:  # whoever joins sees where you left off
                if m["role"] == "user":
                    self.room.publish({"kind": "message", "who": host, "text": m["content"]})
                else:
                    self.room.console.print(markdown(m["content"]))
                    self.room.flush()
            self.say("Multiplayer on! Send the link to your friends.", "love")
        link = self.room.link()
        c.print("[green]⏺[/] Multiplayer: anyone on your same network (Wi-Fi) opens this link", highlight=False)
        c.print(f"  [dim]⎿[/]  [bold {ACCENT}]{link}[/]", highlight=False)
        c.print(f"     [dim]code {self.room.code} · /multi stop to close[/]", highlight=False)
        c.print("     [dim]they see what the agent does and write to it; you always confirm changes and commands[/]")
        if self.room.guests:
            c.print(f"     [dim]joined: {escape(', '.join(self.room.guests))}[/]", highlight=False)
        if "//127." in link:
            c.print("     [yellow]I don't see any network: connect to Wi-Fi and run /multi again[/]")
        elif opened and sys.platform == "win32":
            c.print("     [dim]if Windows asks for permission for Python, allow it on private networks[/]")

    def _wake(self) -> None:
        """A friend wrote: if you're idle at the input, let it through (what you were typing stays there)."""
        loop = self.prompt.app.loop
        if self._at_prompt and loop:
            with contextlib.suppress(RuntimeError):  # the input just closed
                loop.call_soon_threadsafe(self._leave_prompt)

    def _leave_prompt(self) -> None:
        app = self.prompt.app
        if self._at_prompt and app.future and not app.future.done():
            self._draft = app.current_buffer.text
            app.exit(result=GUEST_TURN)

    def _guests_waiting(self) -> None:
        if self.room and not self.room.inbox.empty():
            self._leave_prompt()

    def _handle_guests(self) -> None:
        """Friends' messages: the agent runs them like yours."""
        while self.room and (message := self.room.next_message()):
            name, text = message
            self.console.print(f"[{ACCENT}]›[/] [bold]{escape(name)}:[/] {escape(text)}", highlight=False)
            self.submit(f"{name}: {text}", guest=True)
            self.console.print()

    def switch_root(self, path: Path) -> None:
        """Works in another folder (after /new): that folder's conversation, checkpoints, permissions and plugins."""
        self.session.save()
        self.root = Path(path).resolve()
        self.session = Session(cwd=str(self.root), mode=self.mode)
        self.policy = PermissionPolicy(mode=self.policy.mode, root=self.root)
        self.checkpoints = CheckpointStore(self.root)
        existing = self.checkpoints.list()
        self.session_start_cp = (existing[-1].id + 1) if existing else 1
        self.last_answer, self.last_files, self.pending_context = "", {}, {}
        self.extra_dirs = self._saved_dirs()
        self.branch = self._git_branch()
        self.custom = extras.custom_commands(self.root)
        self.completer.commands = {**COMMANDS, **{k: v[0] for k, v in self.custom.items()}}
        self.completer.root = self.root
        self.completer.refresh()
        self.start_project()
        self.console.print(f"  [dim]⎿  now working in {escape(str(self.root))}[/]", highlight=False)

    def _update(self) -> None:
        with self.console.status(f"[{ACCENT}]Updating MyDevAgent…[/]"):
            result = update_mod.update()
        color = "green" if result.ok else "red"
        self.console.print(f"[{color}]⏺[/] {escape(result.message)}", highlight=False)
        for change in result.changes:
            self.console.print(f"  [dim]• {escape(change)}[/]", highlight=False)
        if result.restart:
            self.console.print(f"[{ACCENT}]⎿  Restart MyDevAgent to use the new version (/exit and then open it again)[/]")
            self.say("I've updated myself! Restart me to see what's new.", "love")
        elif result.ok:
            self.say("I'm already up to date!", "done")
        else:
            self.say("I couldn't update myself: read above.", "error")

    def _check_updates(self) -> None:
        """In the background at startup: if there is something new on GitHub, Vio says so."""
        count = update_mod.available()
        if count:
            self.say(f"There are {count} MyDevAgent updates: type /update to get them.", "love")

    def start_project(self) -> None:
        """Asks for a yes for the project's hooks and MCP servers (once, or when they change), then starts them."""
        hooks_pending, mcp_pending = hooks_mod.untrusted(self.root), mcp_mod.untrusted(self.root)
        if hooks_pending or mcp_pending:
            what = " and ".join(filter(None, [f"{len(hooks_pending)} hook(s)" if hooks_pending else "",
                                              f"{len(mcp_pending)} MCP server(s)" if mcp_pending else ""]))
            self.console.print(f"[bold]This project has {what}: programs that start on their own.[/]")
            for hook in hooks_pending[:8]:
                self.console.print(f"  [dim]{hook.event}[/] {escape(hook.command[:90])} [dim]({hook.source})[/]",
                                   highlight=False)
            for server in mcp_pending[:8]:
                self.console.print(f"  [dim]MCP {escape(server.name)}[/] {escape(server.describe()[:90])}",
                                   highlight=False)
            answer = self._reply("  Enable them? Only if you trust this project [y/N] › ")
            if answer.strip().lower() in ("y", "yes"):
                hooks_mod.allow(self.root)
                mcp_mod.allow(self.root)
        self.hooks = Hooks(self.root)
        if any(h.event == "SessionStart" and not h.unsupported for h in self.hooks.hooks):
            # in the background: some plugins install packages at startup and must not block you
            threading.Thread(target=self.hooks.start, args=("startup",), daemon=True).start()
        self._start_mcp()

    def _start_mcp(self) -> None:
        self.mcp.close()
        self.mcp = McpManager(self.root)
        if self.mcp:  # they connect in the background: npx can take a while the first time
            threading.Thread(target=self.mcp.connect_all, daemon=True).start()

    def _mcp(self, arg: str) -> None:
        c = self.console
        if arg.lower() in ("reload", "trust"):
            if arg.lower() == "trust":
                mcp_mod.allow(self.root)
            self._start_mcp()
            self.mcp.connect_all()
        pending = mcp_mod.untrusted(self.root)
        if not self.mcp and not pending:
            c.print("[dim]⎿  No MCP servers. They are configured like in Claude Code: .mcp.json in the project or "
                    "~/.mydevagent/mcp.json (guide: docs/TUI.md)[/]", highlight=False)
            return
        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2), expand=True)
        for col in ("server", "from", "status"):
            table.add_column(col, no_wrap=True)
        table.add_column("command / url", no_wrap=True, overflow="ellipsis", ratio=1)
        for server in self.mcp.servers.values():
            if server.error:
                state = "[red]error[/]"
            elif server.transport:
                state = f"[green]✓[/] {len(server.tools)} tools"
            else:
                state = "[dim]not started[/]"
            table.add_row(f"[{ACCENT}]{escape(server.name)}[/]", escape(server.config.source), state,
                          escape(server.config.describe()))
        c.print(table)
        for server in self.mcp.servers.values():
            if server.error:
                c.print(f"[red]⎿  {escape(server.name)}: {escape(server.error[:200])}[/]", highlight=False)
        if pending:
            c.print(f"[yellow]⎿  {len(pending)} project server(s) are off: /mcp trust to enable them[/]")
        c.print("[dim]The agent uses them on its own when needed · /mcp reload restarts them[/]", highlight=False)

    def _hooks(self, arg: str) -> None:
        c = self.console
        if arg.lower() == "trust":
            hooks_mod.allow(self.root)
            self.hooks = Hooks(self.root)
            c.print(f"[green]⏺[/] Project hooks enabled ({len(self.hooks.hooks)} active hooks)")
            return
        pending = hooks_mod.untrusted(self.root)
        if not self.hooks.hooks and not pending:
            c.print("[dim]⎿  No hooks. They are written like in Claude Code, in .mydevagent/settings.json or "
                    ".claude/settings.json (guide: docs/TUI.md)[/]", highlight=False)
            return
        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2), expand=True)
        for col in ("event", "matcher", "from"):
            table.add_column(col, no_wrap=True)
        table.add_column("command", no_wrap=True, overflow="ellipsis", ratio=1)
        for hook in self.hooks.hooks:
            event = f"[dim]{hook.event}[/]" if hook.unsupported else f"[{ACCENT}]{hook.event}[/]"
            command = escape(hook.command) + (f" [dim]({hook.unsupported})[/]" if hook.unsupported else "")
            table.add_row(event, escape(hook.matcher or "*"), escape(hook.source), command)
        c.print(table)
        skipped = sum(1 for h in self.hooks.hooks if h.unsupported)
        if skipped:
            c.print(f"[dim]{skipped} grayed-out hook(s) are not run: they use Claude Code features that "
                    "MyDevAgent does not have yet[/]")
        if pending:
            c.print(f"[yellow]⎿  {len(pending)} project hook(s) are off: /hooks trust to enable them[/]")

    def _plugin(self, arg: str) -> None:
        c = self.console
        action, _, target = arg.partition(" ")
        action, target = action.lower(), target.strip()
        found = plugins.load_plugins(self.root)
        if action in ("install", "update", "remove") and target:
            if action == "install":
                c.print(f"[dim]⎿  Installing {escape(target)}…[/]")
            try:
                if action == "install":
                    done = [f"Installed [bold]{escape(p.name)}[/] [dim]{self._plugin_summary(p)}[/]"
                            for p in plugins.install(target)]
                elif action == "update":
                    done = [f"Updated [bold]{escape(target)}[/] [dim]{escape(plugins.update(target, self.root))}[/]"]
                else:
                    folder = plugins.remove(target, self.root)
                    done = [f"Removed [bold]{escape(target)}[/] [dim](folder {escape(str(folder))})[/]"]
            except (ValueError, OSError) as exc:
                c.print(f"[red]⎿  {escape(str(exc))}[/]")
                return
            self.custom = extras.custom_commands(self.root)
            self.completer.commands = {**COMMANDS, **{k: v[0] for k, v in self.custom.items()}}
            for line in done:
                c.print(f"[green]⏺[/] {line}", highlight=False)
            if action == "install":
                self.say("New plugin! Find its commands with /, its skills with /skill.", "love")
            return
        if action not in ("", "list"):
            c.print("[red]⎿  usage: /plugin [list] · install <user/repo | git url | folder> · update <name> · "
                    "remove <name>[/]", highlight=False)
            return
        if not found:
            c.print("[dim]⎿  No plugins. Install one with /plugin install <user/repo | git url | folder>, "
                    "Claude Code ones too (guide: docs/TUI.md)[/]", highlight=False)
            return
        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2), expand=True)
        for col in ("plugin", "from", "contents"):
            table.add_column(col, no_wrap=True)
        table.add_column("description", no_wrap=True, overflow="ellipsis", ratio=1)  # one row per plugin
        for p in found.values():
            folder = plugins.folder_of(p)
            source = p.source + (f" · {folder.name}" if folder and folder.name != p.name else "")
            table.add_row(f"[{ACCENT}]{escape(p.name)}[/]", escape(source), self._plugin_summary(p),
                          escape(p.description))
        c.print(table)
        c.print("[dim]/plugin install <user/repo | url | folder> · /plugin update <name> · "
                "/plugin remove <name>[/]", highlight=False)

    @staticmethod
    def _plugin_summary(plugin: plugins.Plugin) -> str:
        names = (("commands", "command", "commands"), ("skills", "skill", "skills"), ("agents", "agent", "agents"))
        parts = [f"{n} {one if n == 1 else many}" for kind, one, many in names if (n := plugin.count(kind))]
        return " · ".join(parts + plugin.features()) or "empty"

    def _rewind(self) -> None:
        checkpoints = self.checkpoints.list()
        if not checkpoints:
            self.console.print("[dim]⎿  No point to go back to.[/]")
            return
        recent = checkpoints[-10:]
        for i, cp in enumerate(reversed(recent), start=1):
            when = time.strftime("%H:%M", time.localtime(cp.created))
            self.console.print(f"  [bold]{i}[/] [dim]{when}[/] {escape(cp.label[:60])} [dim]({len(cp.files)} files)[/]")
        choice = self._input("  go back to before (number) › ") if not self._ask else self._ask("rewind")
        if choice.isdigit() and 1 <= int(choice) <= len(recent):
            target = list(reversed(recent))[int(choice) - 1]
            files = self.checkpoints.rewind(target.id)
            self.console.print(f"[green]⏺[/] Went back to before '{escape(target.label[:60])}'\n"
                               f"  [dim]⎿  restored: {', '.join(files)}[/]")
            self.completer.refresh()

    def _models(self) -> None:
        installed, url = extras.list_models(self.orch.settings)
        if installed is None:
            self.console.print(f"[red]⎿  backend unreachable: {url}[/] [dim](start Ollama: `ollama serve`)[/]")
            return
        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
        table.add_column("tier")
        table.add_column("model")
        table.add_column("")
        for tier in ("main", "fast", "reasoning", "embed", "vision"):
            model, _ = self.orch.settings.resolve_model(tier)
            ok = model in installed or f"{model}:latest" in installed
            table.add_row(tier, model, "[green]✓[/]" if ok else f"[red]✗ ollama pull {model}[/]")
        self.console.print(table)
        self.console.print("[dim]Installed: " + ", ".join(sorted(installed)) + "[/]")

    # --------------------------------------------------------- startup check
    def startup_check(self) -> health.Health:
        """Backend down or models missing → explains what to do and offers to fix it. Silent if all is well."""
        settings = self.orch.settings
        status = health.check_backends(settings)
        while status.down:
            urls = ", ".join(status.down)
            hint = health.start_hint(status.down[0])
            self.console.print(Panel(
                f"[bold]The model server is not responding[/] at {escape(urls)}\n\n"
                f"To start it: {escape(hint)}.\n"
                "If you haven't installed it yet: https://ollama.com/download",
                title="MyDevAgent can't start", border_style="red", expand=False, padding=(0, 2)))
            answer = self._reply("  Enter to retry · c to continue anyway › ")
            if answer.lower().startswith(("c", "q", "3")):
                return status
            status = health.check_backends(settings)
        missing = status.missing()
        if missing:
            self._offer_missing(status, missing)
        self._hardware_hint()
        return status

    def _offer_missing(self, status: health.Health, missing: list[health.TierStatus]) -> None:
        settings = self.orch.settings
        essential = [m for m in missing if m.tier in health.ESSENTIAL_TIERS]
        tiers_by_model: dict[str, list[str]] = {}
        for m in missing:
            tiers_by_model.setdefault(m.model, []).append(m.tier)
        lines = []
        for model, tiers in tiers_by_model.items():
            size = health.model_size(model)
            weight = f" · about {size * 0.6 + 0.4:.1f} GB" if size else ""
            note = "" if set(tiers) & set(health.ESSENTIAL_TIERS) else " [dim](optional)[/]"
            lines.append(f"  [red]✗[/] {escape(model)} [dim]({', '.join(tiers)}{weight})[/]{note}")
        to_pull = list(dict.fromkeys(m.model for m in (essential or missing)))
        subs = health.substitutes(status)
        can_pull = health.is_ollama(missing[0].base_url)
        options = []
        if can_pull:
            options.append(("1", "Download them now" + (" (only the required ones)" if len(to_pull) < len(tiers_by_model)
                                                    else "")))
        if subs:
            used = ", ".join(f"{t}: {escape(m)}" for t, m in subs.items())
            options.append(("2", f"Use the models I already have ({used})"))
        options.append(("3", "Continue anyway"))
        title = "Some models are missing" if essential else "Some optional models are missing"
        body = (f"[bold]The {settings.profile} profile uses models that are not installed:[/]\n"
                + "\n".join(lines) + "\n\n" + "\n".join(f"  [bold]{k}[/] {v}" for k, v in options))
        if not essential:
            body += "\n\n[dim]MyDevAgent works without these anyway (code search without embeddings).[/]"
        self.console.print(Panel(body, title=title, border_style="yellow", expand=False, padding=(0, 2)))
        choice = self._reply("  choice › ")
        if choice == "1" and can_pull:
            if self.pull_models(to_pull):
                extras.warmup(self.orch.llm, settings)
        elif choice == "2" and subs:
            health.apply_substitutes(settings, subs)
            self.model = settings.resolve_model("main")[0]
            self.console.print(f"[green]⏺[/] Using the installed models · main model: {escape(self.model)}")
            save = self._reply("  Remember this choice for next time? (y/N) › ")
            if save.lower().startswith(("y", "1")):
                path = health.save_env({f"MYDEVAGENT_MODEL_{t.upper()}": m for t, m in subs.items()})
                self.console.print(f"  [dim]⎿  saved to {escape(str(path))}[/]")
            extras.warmup(self.orch.llm, settings)
        elif essential:
            self.console.print("[dim]⎿  OK. Whenever you like: /pull <name> downloads a model, /model <name> switches to it.[/]")

    def _hardware_hint(self) -> None:
        """Only once: if the profile does not suit the hardware, says so."""
        prefs = self._prefs()
        if prefs.get("hardware_hint"):
            return
        hw = health.detect_hardware()
        best = health.recommend_profile(hw)
        current = self.orch.settings.profile
        if best != current and current in health.PROFILE_ORDER:
            self.console.print(f"[yellow]⏺[/] On this PC ({escape(hw.describe())}) I recommend the "
                               f"[bold]{best}[/] profile (you are using {escape(current)}).\n  [dim]⎿  try `mydevagent -p {best}` or "
                               f"MYDEVAGENT_PROFILE={best} in the .env file[/]")
        self._save_pref("hardware_hint", True)

    def pull_models(self, models: list[str]) -> bool:
        """Downloads the models from Ollama with a progress bar. True if all were downloaded."""
        from rich.progress import (
            BarColumn,
            DownloadColumn,
            Progress,
            TextColumn,
            TimeRemainingColumn,
            TransferSpeedColumn,
        )

        base_url = self.orch.settings.resolve_model("main")[1].base_url
        if not health.is_ollama(base_url):
            self.console.print(f"[red]⎿  /pull only works with Ollama (current backend: {escape(base_url)})[/]")
            return False
        all_ok = True
        for model in dict.fromkeys(models):
            columns = (TextColumn("  [bold]{task.description}[/]"), BarColumn(), DownloadColumn(),
                       TransferSpeedColumn(), TimeRemainingColumn())
            try:
                with Progress(*columns, console=self.console, transient=True) as progress:
                    task = progress.add_task(model, total=None)

                    def update(status: str, done: int, total: int, task=task, progress=progress, model=model) -> None:
                        label = model if status.startswith("pulling") else f"{model} · {status}"
                        progress.update(task, description=label, completed=done, total=total or None)

                    health.pull_model(base_url, model, update)
            except KeyboardInterrupt:
                self.console.print(f"[yellow]⎿  download of {escape(model)} interrupted[/]")
                return False
            except Exception as exc:
                _, hint = health.explain_error(exc, self.orch.settings)
                self.console.print(f"[red]⏺ Download of {escape(model)} failed: {escape(str(exc))}[/]\n"
                                   f"  [dim]⎿  check the name on https://ollama.com/library · {escape(hint)}[/]")
                all_ok = False
                continue
            self.console.print(f"[green]⏺[/] Downloaded {escape(model)}")
        return all_ok

    def _reply(self, message: str) -> str:
        return self._ask(message) if self._ask else self._input(message)

    def _index(self) -> None:
        from ..tools.rag import CodeIndex

        settings = self.orch.settings.model_copy(deep=True)
        settings.tools.filesystem.root = str(self.root)
        index = CodeIndex.for_workspace(settings, self.orch.llm)
        extras_private_dir(self.root)
        with self.console.status(f"[{ACCENT}]✻ Indexing the project…[/]"):
            stats = index.build()
        self.orch.toolbox.ctx.cache.pop("index", None)
        kind = "Semantic" if stats["embedded"] else "Lexical"
        self.console.print(f"[green]⏺[/] {kind} index: {stats['chunks']} chunks\n  [dim]⎿  {index.path}[/]")

    def _resume(self) -> None:
        sessions = [s for s in list_sessions(str(self.root)) if s.id != self.session.id]
        if not sessions:
            self.console.print("[dim]⎿  No previous sessions in this folder.[/]")
            return
        for i, s in enumerate(sessions, start=1):
            when = time.strftime("%m/%d %H:%M", time.localtime(s.updated))
            self.console.print(f"  [bold]{i}[/] [dim]{when}[/] {escape(s.title)} [dim]({len(s.history) // 2} turns)[/]")
        choice = self._input("  number › ")
        if choice.isdigit() and 1 <= int(choice) <= len(sessions):
            self.session = sessions[int(choice) - 1]
            self.mode = self.session.mode
            self.last_answer = next((m["content"] for m in reversed(self.session.history)
                                     if m["role"] == "assistant"), "")
            self.console.print(f"[dim]⎿  Resumed '{escape(self.session.title)}'[/]")

    def _usage(self) -> extras.ContextUsage:
        usage = extras.context_usage(self.orch.settings, self.orch.registry.persona, self.root, self.session.history)
        self.ctx_percent = usage.percent
        return usage

    def _show_context(self) -> None:
        u = self._usage()
        width = 40
        parts = [(u.instructions, "#f0a8e0"), (u.summary, "yellow"), (u.messages, "cyan")]
        cells = [max(1, round(width * n / u.window)) if n else 0 for n, _ in parts]
        bar = "".join(f"[{color}]{'█' * c}[/]" for c, (_, color) in zip(cells, parts))
        bar += f"[grey37]{'░' * max(0, width - sum(cells))}[/]"
        n = lambda v: f"{v:,}"  # noqa: E731
        rows = [("#f0a8e0", "Instructions & memory", u.instructions), ("yellow", "Summary", u.summary),
                ("cyan", f"Messages ({u.count})", u.messages), ("grey50", "Free", u.free)]
        c = self.console
        c.print(f"\n [bold {ACCENT}]Context[/]\n  {bar}   [bold]{u.percent}%[/] of {n(u.window)} tokens\n")
        for color, label, value in rows:
            c.print(f"  [{color}]■[/] {label:<22} {n(value)} tokens")
        c.print(f"\n  [dim]Estimate: about 4 characters per token. Auto-compacts at {round(extras.AUTO_COMPACT_AT * 100)}% "
                "(or with /compact).[/]\n")

    def _compact(self, manual: bool = False) -> None:
        if len(self.session.history) < 4:
            if manual:
                self.console.print("[dim]⎿  Conversation still short, nothing to compact.[/]")
            return
        before = self._usage().used
        with self.console.status(f"[{ACCENT}]✻ Compacting the conversation…[/]"):
            try:
                compacted = extras.compact_history(self.orch.llm, self.session.history)
            except Exception as exc:
                self.console.print(f"[red]⎿  compaction failed: {escape(str(exc))}[/]")
                return
        turns = len(self.session.history) // 2
        self.session.history = compacted
        self.session.save()
        freed = max(0, before - self._usage().used)
        self.console.print(f"  ⎿  [green]✓[/] Conversation compacted ({turns} turns) · freed about "
                           f"{freed:,} tokens · [dim]/context[/]")
        for line in extras.summary_of(compacted).splitlines()[:12]:
            if line.strip():
                self.console.print(f"     [dim]{escape(line.strip().replace('**', ''))}[/]")

    # ------------------------------------------------------------- shell
    def run_shell(self, command: str) -> None:
        """`!command`: runs in the project folder and attaches the output to the next message."""
        self.console.print(f"[{ACCENT}]⏺[/] Bash([bold]{escape(command)}[/])")
        try:
            proc = subprocess.run(command, shell=True, cwd=self.root, capture_output=True, text=True,
                                  timeout=SHELL_TIMEOUT)
            output, code = (proc.stdout + proc.stderr).strip(), proc.returncode
        except subprocess.TimeoutExpired:
            output, code = f"timeout after {SHELL_TIMEOUT}s", -1
        lines = output.splitlines()
        shown = lines[:15]
        for i, line in enumerate(shown):
            prefix = "  ⎿  " if i == 0 else "     "
            self.console.print(f"[dim]{prefix}{escape(line[:200])}[/]")
        if len(lines) > len(shown):
            self.console.print(f"[dim]     … {len(lines) - len(shown)} more lines[/]")
        status = "[green]exit 0[/]" if code == 0 else f"[red]exit {code}[/]"
        self.console.print(f"     {status} [dim]· output attached to the next message[/]")
        self.pending_context[f"$ {command}"] = f"exit code {code}\n{output[-MAX_SHELL_OUTPUT:]}"

    # -------------------------------------------------------------- turns
    def collect_attachments(self, text: str) -> dict[str, str]:
        return collect_attachments(self.root, text, self.extra_dirs)

    def submit(self, text: str, display: str | None = None, guest: bool = False) -> str:
        if extras.needs_compact(self._usage(), self.session.history, AUTO_COMPACT_MESSAGES):
            self._compact()
        files = self.collect_attachments(text)
        for name in files:
            self.console.print(f"  [dim]⎿  attached {escape(name)}[/]")
        files.update(self.pending_context)
        self.pending_context = {}
        self.last_files = files
        if self.room and not guest:
            self.room.publish({"kind": "message", "who": self.room.host, "text": display or text})
        mode = self.policy.mode
        if guest and mode in ("auto", "auto-edit"):
            self.policy.mode = "ask"  # you always confirm what a friend asks for
        try:
            answer = self.run_turn(text, files)
        finally:
            self.policy.mode = mode
        self.session.add_turn(display or text, answer)
        with contextlib.suppress(Exception):
            self._usage()  # the percentage in the bottom bar
        return answer

    def run_turn(self, text: str, files: dict[str, str]) -> str:
        events: queue.Queue = queue.Queue()
        cancel = threading.Event()
        room = self.room
        out = multi.Tee(self.console, room.console) if room else self.console  # with /multi friends see it too
        renderer = TurnRenderer(out, self.names)
        if room:
            room.publish({"kind": "busy", "on": True})
        started = time.monotonic()
        turn = stats_mod.Turn(project=str(self.root), session=self.session.id, model=self.model)
        pending_replies: list[queue.Queue] = []
        mode = None if self.mode == "auto" else self.mode

        def approver(req: ApprovalRequest) -> tuple[str, str]:
            reply: queue.Queue = queue.Queue(maxsize=1)
            pending_replies.append(reply)
            events.put(("approval", (req, reply)))
            return reply.get()

        def worker() -> None:
            try:
                if self.agent_mode:
                    extras_private_dir(self.root)
                    runner = AgentRunner(self.orch, self.root, self.policy, approver=approver,
                                         checkpoints=self.checkpoints, hooks=self.hooks, mcp=self.mcp,
                                         learn=self.learn, extra_dirs=self.extra_dirs)
                    stream = runner.run(text, history=self.session.history, files=files, mode=mode,
                                        on_event=lambda e: events.put(("event", e)), cancel=cancel)
                else:
                    learn = {"Learning mode (follow these instructions)": LEARN_PROMPT} if self.learn else {}
                    stream = self.orch.run(text, history=self.session.history, files={**files, **learn}, mode=mode,
                                           on_event=lambda e: events.put(("event", e)),
                                           show_thinking=self.show_thinking, cancel=cancel)
                for chunk in stream:
                    events.put(("chunk", chunk))
            except Exception as exc:
                events.put(("error", exc))
            finally:
                events.put(("done", None))

        threading.Thread(target=worker, daemon=True).start()
        abandoned = False
        failed = ""
        watcher = EscWatcher()

        def interrupt() -> bool:
            """First Esc/Ctrl+C: stops the team. Second: abandons. Returns True to leave the loop."""
            if cancel.is_set():
                return True
            cancel.set()
            renderer.cancelled = True
            renderer.status = "Interrupting (again to force)"
            for reply in pending_replies:
                if reply.empty():
                    reply.put(("no", "interrupted by the user"))
            return False

        with watcher, Live(renderer.view(), console=self.console, refresh_per_second=12, transient=True) as live:
            while True:
                try:
                    if watcher.pressed() and interrupt():
                        abandoned = True
                        break
                    try:
                        kind, value = events.get(timeout=0.08)
                    except queue.Empty:
                        live.update(renderer.view())
                        continue
                    if kind == "done":
                        break
                    if kind == "approval":
                        req, reply = value
                        if cancel.is_set():
                            reply.put(("no", "interrupted by the user"))
                            continue
                        live.stop()
                        watcher.__exit__(None, None, None)
                        if room:
                            if req.diff:
                                room.console.print(f"[{ACCENT}]⏺[/] [bold]{escape(req.summary)}[/]")
                                room.console.print(render_diff(req.diff, max_lines=80))
                            room.console.print(f"  [dim]⎿  {escape(room.host)} needs to confirm…[/]")
                            room.flush()
                        try:
                            answer = self.ask_approval(req)
                        except (KeyboardInterrupt, EOFError):
                            answer = ("no", "")
                            interrupt()
                        if room:
                            room.console.print("  [dim]⎿  confirmed[/]" if answer[0] != "no"
                                               else "  [red]⎿  not confirmed[/]")
                        if answer[0] != "no" and req.tool in ("edit_file", "write_file"):
                            renderer.shown_diffs.add(req.args.get("path", ""))
                        reply.put(answer)
                        watcher.__enter__()
                        live.start()
                    elif kind == "error":
                        title, hint = health.explain_error(value, self.orch.settings)
                        failed = title
                        out.print(f"[red]⏺ {escape(title)}[/]\n  [dim]⎿  {escape(hint)}[/]")
                    elif kind == "chunk":
                        renderer.on_chunk(value)
                    else:
                        renderer.on_event(value)
                        turn.on_event(value)
                    live.update(renderer.view())
                    if room:
                        room.flush()
                except KeyboardInterrupt:
                    if interrupt():
                        abandoned = True
                        break
            live.update("")
        renderer.finish()
        if abandoned:
            self.console.print("[dim]  ⎿  the running agent will finish in the background[/]")
        if room:
            room.flush()
            room.publish({"kind": "busy", "on": False})
        elapsed = time.monotonic() - started
        self.stats["turns"] += 1
        self.stats["seconds"] += elapsed
        self.stats["tokens"] += renderer.tokens + len(renderer.answer) // 4
        turn.cancelled = turn.cancelled or renderer.cancelled
        self.turn_log.append(turn.finish(renderer.answer, estimate=not self.agent_mode, failed=bool(failed)))
        if elapsed > NOTIFY_AFTER_S and not self._ask:
            extras.notify("MyDevAgent", "Done" if not renderer.cancelled else "Interrupted")
        answer = renderer.answer + ("\n\n[interrupted]" if renderer.cancelled else "")
        self.last_answer = renderer.answer
        self.completer.refresh()
        if failed:
            self.say(f"Ops: {failed}", "error")
        elif renderer.cancelled:
            self.say("I stopped. Tell me how to continue.", "think")
        else:
            self.say(f"Done in {elapsed:.0f}s! What do we do next?", "done")
        return answer

    # --------------------------------------------------------------- loop
    def loop(self) -> None:
        self.banner()
        if self._startup_check:
            self.startup_check()
        self.start_project()
        if self._startup_check:
            threading.Thread(target=self._check_updates, daemon=True).start()
        while True:
            self.console.print()
            self._handle_guests()
            try:
                self._at_prompt = True
                draft, self._draft = self._draft, ""
                text = self.prompt.prompt(self.prompt_message, default=draft, pre_run=self._guests_waiting)
                if text is GUEST_TURN:
                    continue
                text = text.strip()
                if text:
                    self.console.print(f"[{ACCENT}]›[/] {escape(text)}", highlight=False)
            except KeyboardInterrupt:
                now = time.monotonic()
                if now - self._ctrl_c_at < 1.5:
                    break
                self._ctrl_c_at = now
                self.console.print("[dim]⎿  Ctrl+C again to quit[/]")
                continue
            except EOFError:
                break
            finally:
                self._at_prompt = False
            if not text:
                continue
            if text.startswith("!"):
                if text[1:].strip():
                    self.run_shell(text[1:].strip())
                continue
            if text.startswith("#") and not text.startswith("##") and len(text) > 1:
                path = append_memory(self.root, text[1:])
                self.console.print(f"[dim]⎿  note saved in {path.name}[/]")
                continue
            if text.startswith("/") and not text.startswith("//"):
                if not self.handle_command(text):
                    break
                continue
            self.submit(text)
        self.mcp.close()
        if self.room:
            self.room.system(f"{self.room.host} closed MyDevAgent")
            self.room.close()
        self.session.save()
        self.console.print(f"[dim]Session saved: {self.session.id} · resume with `mydevagent --continue`[/]")


def extras_private_dir(root: Path) -> None:
    """`.mydevagent/` in the project with its own .gitignore: checkpoints and caches stay out of git."""
    folder = root / ".mydevagent"
    try:
        folder.mkdir(exist_ok=True)
        ignore = folder / ".gitignore"
        if not ignore.exists():
            ignore.write_text("*\n", encoding="utf-8")
    except OSError:
        pass


def _color_depth():
    """Full colors where the terminal supports them (Windows Terminal, iTerm, VS Code…): Vio stays truly purple."""
    import os

    from prompt_toolkit.output import ColorDepth

    if os.environ.get("COLORTERM") in ("truecolor", "24bit") or "WT_SESSION" in os.environ:
        return ColorDepth.TRUE_COLOR
    return None


def _style():
    from prompt_toolkit.styles import Style

    return Style.from_dict({
        "prompt": f"bold {ACCENT}",
        "placeholder": "#7a7a7a italic",
        "bottom-toolbar": "noreverse bg:default #9a9a9a",
        "tb": "#c0c0c0",
        "tb.key": f"bold {ACCENT}",
        "tb.dim": "#7a7a7a",
        "tb.ok": "#6fbf73",
        "tb.warn": "#e0b04a",
        "tb.plan": "#67e8f9",
        "vio.name": f"bold {ACCENT}",
        "vio.say": "#e9d5ff",
        "rule": "#7e22ce",
        "completion-menu.completion": "bg:#2b2b2b #d0d0d0",
        "completion-menu.completion.current": f"bg:{ACCENT} #ffffff",
        "completion-menu.meta.completion": "bg:#2b2b2b #8a8a8a",
        "completion-menu.meta.completion.current": f"bg:{ACCENT} #ffffff",
    })


def run_tui(profile: str | None = None, continue_last: bool = False, permission_mode: str = "ask",
            add_dirs: list[Path] | None = None) -> None:
    session = None
    if continue_last:
        previous = list_sessions(str(Path.cwd().resolve()), limit=1)
        session = previous[0] if previous else None
    TuiApp(profile=profile, session=session, permission_mode=permission_mode, extra_dirs=add_dirs).loop()
