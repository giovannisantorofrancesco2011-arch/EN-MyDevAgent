"""Bridge for MyDevAgent Studio: `mydevagent bridge` speaks JSON over stdin/stdout, one line per message.

The editor sends requests {"id": 1, "method": "prompt", "params": {...}} and receives {"id": 1, "result": ...}
or {"id": 1, "error": {"message": ..., "hint": ...}}. The bridge also sends notifications, without an id:

    event     {"turn", "event"}         the same events the terminal draws (tools, diffs, todos, tests…)
    chunk     {"turn", "text"}          a piece of the answer
    approval  {"request", "turn", …}    a yes is needed: the editor replies with the approval_reply method
    turn_end  {"turn", "answer", "cancelled", "error", "files"}
    pull      {"model", "status", "completed", "total"}   progress of a download (pull method)

The agent, permissions, checkpoints (/undo), sessions and stats are the terminal's own:
only who draws changes. stdout is reserved for the protocol; everything else (print, logs) goes to stderr.
"""

from __future__ import annotations

import contextlib
import itertools
import json
import queue
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import IO, Any

from . import __version__, fim, plus, stats
from . import hooks as hooks_mod
from . import license as license_mod
from . import mcp as mcp_mod
from . import update as update_mod
from .agent import CheckpointStore, PermissionPolicy
from .agent.context import collect_attachments
from .agent.permissions import MODES as PERMISSION_MODES
from .agent.permissions import ApprovalRequest
from .agent.runner import LEARN_PROMPT, AgentRunner
from .health import check_backends, explain_error, is_installed, is_ollama, pull_model
from .hooks import Hooks
from .mcp import McpManager
from .orchestrator import Orchestrator
from .reasoning import strip_thinking
from .skills import load_skills
from .tui import extras
from .tui.session import Session, list_sessions

PROTOCOL = 1
TEAMS = ("auto", "fast", "balanced", "deep", "ultra-deep")
AUTO_COMPACT_MESSAGES = 20
MAX_FILE_CHARS = 1_500_000  # beyond this, the editor shows only the diff (no whole files side by side)
BACKGROUND = {"complete", "inline_edit", "pull", "health", "updates", "update", "compact"}  # these don't block reading messages
INLINE_SYSTEM = """You are an expert programmer editing code inside a code editor.
Rewrite ONLY the selected code so that it follows the user's instruction. If the selection is empty, write the
new code to insert at the cursor. Reply with the code only: no explanations, no Markdown fences. Keep the
language, style, naming and indentation of the file. Keep comments in the language they are written in."""


class Bridge:
    def __init__(self, orchestrator: Orchestrator, root: Path, out: IO[bytes], *, permission: str = "ask",
                 auto_memory: bool = False) -> None:
        self.orch = orchestrator
        self.root = Path(root).resolve()
        self.out = out
        self.policy = PermissionPolicy(mode=permission, root=self.root)
        self.checkpoints = CheckpointStore(self.root)
        existing = self.checkpoints.list()
        self.session_start_cp = (existing[-1].id + 1) if existing else 1
        self.session = Session(cwd=str(self.root))
        self.team = "auto"
        self.learn = False
        self.agent = True
        self.hooks = Hooks(self.root, hooks=[])  # the real ones start with hello (after the OK for the project)
        self.mcp = McpManager(self.root, configs={})
        self.turn_log: list[dict[str, Any]] = []
        self.pending: dict[str, queue.Queue] = {}
        self.cancel = threading.Event()
        self.busy: int | None = None  # number of the turn in progress
        self.ids = itertools.count(1)
        self.write_lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=4)
        self.running = True
        self.auto_memory = auto_memory  # automatic memory (Plus); off in tests
        self.jobs = plus.Background(self.orch, self.root, self._background_done)

    # ------------------------------------------------------------- protocol
    def send(self, message: dict[str, Any]) -> None:
        line = json.dumps(message, ensure_ascii=True, default=str) + "\n"  # ASCII: no encoding issues
        with self.write_lock:
            self.out.write(line.encode("ascii"))
            self.out.flush()

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self.send({"method": method, "params": params})

    def serve(self, stdin: IO[bytes]) -> None:
        for raw in stdin:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                self.send({"id": None, "error": {"message": "invalid JSON"}})
                continue
            self.handle(message)
            if not self.running:
                break
        self.close()

    def handle(self, message: dict[str, Any]) -> None:
        method = str(message.get("method", ""))
        params = message.get("params") or {}
        mid = message.get("id")
        handler = getattr(self, f"m_{method}", None) if re.fullmatch(r"[a-z_]+", method) else None
        if handler is None or not isinstance(params, dict):
            self.send({"id": mid, "error": {"message": f"unknown method: {method}"}})
        elif method in BACKGROUND:
            self.pool.submit(self._call, handler, mid, params)
        else:
            self._call(handler, mid, params)

    def _call(self, handler, mid: Any, params: dict[str, Any]) -> None:
        try:
            result = handler(params)
        except Exception as exc:  # an error in a method must never close the bridge
            title, hint = explain_error(exc, self.orch.settings)
            self.send({"id": mid, "error": {"message": title, "hint": hint}})
        else:
            self.send({"id": mid, "result": result})

    def close(self) -> None:
        self.running = False
        self.m_cancel({})
        self.mcp.close()
        self.session.save()
        self.pool.shutdown(wait=False, cancel_futures=True)

    # -------------------------------------------------------------- methods
    def m_hello(self, p: dict[str, Any]) -> dict[str, Any]:
        """First message from the editor: who I am, which models I use, the conversation to resume."""
        if p.get("resume"):
            previous = list_sessions(str(self.root), limit=1)
            if previous:
                self.session = previous[0]
        if p.get("permission") in PERMISSION_MODES:
            self.policy.mode = p["permission"]
        self._start_project()
        extras.warmup(self.orch.llm, self.orch.settings)
        settings = self.orch.settings
        models = {tier: settings.resolve_model(tier)[0] for tier in ("main", "fast", "reasoning", "vision", "embed")}
        backend = settings.resolve_model("main")[1].base_url
        return {
            "protocol": PROTOCOL, "version": __version__, "root": str(self.root), "profile": settings.profile,
            "models": models, "backend": backend, "ollama": is_ollama(backend),
            "permission": self.policy.mode, "permissions": list(PERMISSION_MODES), "team": self.team,
            "teams": list(TEAMS), "learn": self.learn, "session": self.session.id,
            "history": [{"role": m["role"], "content": m["content"]} for m in self.session.history],
            "untrusted": {"hooks": [f"{h.event}: {h.command}" for h in hooks_mod.untrusted(self.root)],
                          "mcp": [f"{s.name}: {s.describe()}" for s in mcp_mod.untrusted(self.root)]},
            "commands": self._commands(), "agents": {a.key: a.name for a in self.orch.registry},
            "home": str(update_mod.HOME), "installed": update_mod.current(), "license": self.m_license({}),
        }

    def m_set(self, p: dict[str, Any]) -> dict[str, Any]:
        """Changes permission mode, team, learn mode or chat/agent (they apply from the next request)."""
        if "permission" in p:
            if p["permission"] not in PERMISSION_MODES:
                raise ValueError(f"unknown mode: {p['permission']}")
            self.policy.mode = p["permission"]
        if "team" in p:
            if p["team"] not in TEAMS:
                raise ValueError(f"unknown team: {p['team']}")
            self.team = p["team"]
        if "learn" in p:
            self.learn = bool(p["learn"])
        if "agent" in p:
            self.agent = bool(p["agent"])
        return {"permission": self.policy.mode, "team": self.team, "learn": self.learn, "agent": self.agent}

    def m_prompt(self, p: dict[str, Any]) -> dict[str, Any]:
        text = str(p.get("text", "")).strip()
        if not text:
            raise ValueError("empty message")
        name, _, arg = text.partition(" ")
        if name.lower() == "/license":
            state = self.m_license({"remove": True} if arg.strip().lower() == "remove" else
                                   {"key": arg.strip()} if arg.strip() else {})
            return self._instant(state["message"] + ("" if state["ok"] else f"\n\n{state['buy']}"))
        state = license_mod.status()
        if not state.ok:
            raise RuntimeError(state.message)
        if name.lower() == "/background":
            return self._instant(self._background(arg.strip()))
        if self.busy is not None:
            raise RuntimeError("I'm already working: wait for me to finish or press Stop")
        display = text
        task = self._expand(text)
        files = collect_attachments(self.root, text)
        files.update({str(k): str(v) for k, v in (p.get("files") or {}).items()})
        files.update(self._editor_context(p.get("context") or {}))
        turn = next(self.ids)
        self.busy = turn
        self.cancel = threading.Event()
        agent = self.agent or task != text  # /init, /skill and commands always work on files
        threading.Thread(target=self._turn, args=(turn, task, files, agent, display), daemon=True).start()
        return {"turn": turn}

    def m_license(self, p: dict[str, Any]) -> dict[str, Any]:
        """License status; with `key` it activates it, with `remove` it removes it from this computer."""
        removed = license_mod.deactivate() if p.get("remove") else ""
        state = license_mod.activate(str(p["key"])) if p.get("key") else license_mod.status()
        return {"ok": state.ok, "kind": state.kind, "message": removed or state.message,
                "days_left": state.days_left, "plus": state.plus, "buy": license_mod.BUY_URL}

    def _instant(self, answer: str) -> dict[str, Any]:
        """A command that answers right away, without the model: the editor sees it as a normal turn."""
        turn = next(self.ids)
        self.notify("chunk", {"turn": turn, "text": answer})
        self.notify("turn_end", {"turn": turn, "answer": answer, "cancelled": False, "error": None, "files": []})
        return {"turn": turn}

    def _background(self, task: str) -> str:
        why = license_mod.plus_needed()
        if why:
            return why
        if task:
            job = self.jobs.start(task)
            return f"Job no. {job.id} started in the background: keep going, I'll tell you when it's done."
        if not self.jobs.jobs:
            return "No background jobs. Type /background <task> to start one."
        return "\n\n".join(f"**no. {j.id} · {j.status}** · {j.task}" + (f"\n\n{j.answer}" if j.answer else "")
                             for j in self.jobs.jobs)

    def _background_done(self, job: plus.Job) -> None:
        text = (f"Background job no. {job.id} is done: type /background to see how it went."
                if job.status == "done" else f"Background job no. {job.id} stopped: {job.answer[:120]}")
        self.notify("event", {"turn": self.busy or 0, "event": {"type": "info", "text": text}})
        self.notify("background", {"id": job.id, "status": job.status, "task": job.task, "answer": job.answer,
                                   "text": text})

    def m_cancel(self, p: dict[str, Any]) -> dict[str, Any]:
        self.cancel.set()
        for request in list(self.pending):
            box = self.pending.pop(request, None)
            if box is not None:
                box.put(("no", "interrupted by the user"))
        return {"cancelled": self.busy is not None}

    def m_approval_reply(self, p: dict[str, Any]) -> dict[str, Any]:
        box = self.pending.pop(str(p.get("request")), None)
        if box is None:
            return {"ok": False}
        answer = p.get("answer") if p.get("answer") in ("yes", "always", "no") else "no"
        box.put((answer, str(p.get("feedback") or "")))
        return {"ok": True}

    def m_undo(self, p: dict[str, Any]) -> dict[str, Any] | None:
        undone = self.checkpoints.undo()
        if not undone:
            return None
        checkpoint, files = undone
        return {"label": checkpoint.label, "files": files}

    def m_diff(self, p: dict[str, Any]) -> dict[str, Any]:
        return {"diff": self.checkpoints.session_diff(self.session_start_cp)}

    def m_clear(self, p: dict[str, Any]) -> dict[str, Any]:
        self.session.save()
        self.session = Session(cwd=str(self.root))
        return {"session": self.session.id}

    def m_stats(self, p: dict[str, Any]) -> dict[str, Any]:
        days = p.get("days")
        history = stats.summarize(stats.load())
        total = stats.summarize(stats.load(int(days))) if days else history
        return {"session": stats.summarize(self.turn_log).to_dict(), "total": total.to_dict(),
                "history": history.to_dict()}

    def m_context(self, p: dict[str, Any]) -> dict[str, Any]:
        """/context: how much of the model's context the next request uses."""
        return self._usage().to_dict()

    def m_compact(self, p: dict[str, Any]) -> dict[str, Any]:
        """/compact: summarizes the conversation into one message."""
        if self.busy is not None:
            raise RuntimeError("I'm already working: wait for me to finish or press Stop")
        history = self.session.history
        if len(history) < 4:
            return {"compacted": False, "usage": self._usage().to_dict()}
        before = self._usage().used
        self.session.history = extras.compact_history(self.orch.llm, history)
        self.session.save()
        usage = self._usage()
        return {"compacted": True, "turns": len(history) // 2, "freed": max(0, before - usage.used),
                "summary": extras.summary_of(self.session.history), "usage": usage.to_dict()}

    def m_updates(self, p: dict[str, Any]) -> dict[str, Any]:
        """How many MyDevAgent updates are on GitHub (runs git fetch)."""
        return {"available": update_mod.available(), "installed": update_mod.current(), "home": str(update_mod.HOME)}

    def m_update(self, p: dict[str, Any]) -> dict[str, Any]:
        """Like /update in the terminal: git pull (and pip if new dependencies are needed). Then the editor restarts me."""
        if self.busy is not None:
            raise RuntimeError("I'm already working: wait for me to finish or press Stop")
        result = update_mod.update()
        return {"ok": result.ok, "message": result.message, "changes": result.changes, "restart": result.restart,
                "installed": update_mod.current()}

    def _usage(self) -> extras.ContextUsage:
        return extras.context_usage(self.orch.settings, self.orch.registry.persona, self.root, self.session.history)

    def m_trust(self, p: dict[str, Any]) -> dict[str, Any]:
        """The user trusts the project: turns on its hooks and MCP servers."""
        hooks_mod.allow(self.root)
        mcp_mod.allow(self.root)
        self._start_project()
        return {"hooks": len(self.hooks.hooks), "mcp": len(self.mcp.servers)}

    def m_health(self, p: dict[str, Any]) -> dict[str, Any]:
        status = check_backends(self.orch.settings)
        installed = sorted(set().union(*status.installed.values())) if status.installed else []
        extra = [m for m in p.get("models") or [] if isinstance(m, str)]  # e.g. the model for Tab
        return {"down": status.down,
                "missing": [{"tier": t.tier, "model": t.model} for t in status.missing()],
                "installed": installed,
                "has": {m: is_installed(m, set(installed)) for m in extra}}

    def m_pull(self, p: dict[str, Any]) -> dict[str, Any]:
        base_url = self.orch.settings.resolve_model("main")[1].base_url
        if not is_ollama(base_url):
            raise RuntimeError("Models can only be downloaded from here with Ollama")
        for model in dict.fromkeys(str(m) for m in p.get("models") or []):
            def progress(status: str, done: int, total: int, model: str = model) -> None:
                self.notify("pull", {"model": model, "status": status, "completed": done, "total": total})

            pull_model(base_url, model, progress)
        return {"ok": True}

    def m_complete(self, p: dict[str, Any]) -> dict[str, Any]:
        """Tab: completes the code at the cursor with the fast model (or the one chosen in the editor)."""
        text = fim.complete(self.orch.settings, str(p.get("prefix", "")), str(p.get("suffix", "")),
                            model=p.get("model") or None, max_tokens=int(p.get("max_tokens", 64)),
                            multiline=bool(p.get("multiline", True)))
        return {"text": text}

    def m_inline_edit(self, p: dict[str, Any]) -> dict[str, Any]:
        """Ctrl+K: rewrites the selection as the user asks and returns only the new code."""
        selection = str(p.get("selection", ""))
        user = (f"File: {p.get('path', '?')} ({p.get('language', '')})\n\n"
                f"# Code before the selection\n{str(p.get('before', ''))[-2500:]}\n\n"
                f"# Selected code (rewrite this)\n{selection or '(empty: write new code for the cursor position)'}\n"
                f"\n# Code after the selection\n{str(p.get('after', ''))[:1500]}\n\n"
                f"# Instruction\n{p.get('instruction', '')}")
        completion = self.orch.llm.complete([{"role": "system", "content": INLINE_SYSTEM},
                                             {"role": "user", "content": user}],
                                            tier="main", max_tokens=2048, temperature=0.1)
        return {"text": inline_code(completion.text, selection)}

    def m_shutdown(self, p: dict[str, Any]) -> dict[str, Any]:
        self.running = False
        return {"ok": True}

    # ------------------------------------------------------------------ turns
    def _turn(self, turn: int, text: str, files: dict[str, str], agent: bool, display: str) -> None:
        cancel = self.cancel
        record = stats.Turn(project=str(self.root), session=self.session.id, source="studio",
                            model=self.orch.settings.resolve_model("main")[0])
        answer, error = "", None

        def on_event(event: dict[str, Any]) -> None:
            if event.get("type") == "model_reply":  # only in the log (View > Output > MyDevAgent)
                print(f"[model reply without tools]\n{event['text']}\n", file=sys.stderr, flush=True)
                return
            record.on_event(event)
            self.notify("event", {"turn": turn, "event": event})

        try:
            if extras.needs_compact(self._usage(), self.session.history, AUTO_COMPACT_MESSAGES):
                on_event({"type": "info", "text": "summarizing the conversation to make room"})
                self.session.history = extras.compact_history(self.orch.llm, self.session.history)
            mode = None if self.team == "auto" else self.team
            if agent:
                runner = AgentRunner(self.orch, self.root, self.policy, approver=self._approver(turn),
                                     checkpoints=self.checkpoints, hooks=self.hooks, mcp=self.mcp, learn=self.learn)
                stream = runner.run(text, history=self.session.history, files=files, mode=mode, on_event=on_event,
                                    cancel=cancel)
            else:
                learn = {"Learning mode (follow these instructions)": LEARN_PROMPT} if self.learn else {}
                stream = self.orch.run(text, history=self.session.history, files={**files, **learn}, mode=mode,
                                       on_event=on_event, cancel=cancel)
            for chunk in stream:
                answer += chunk
                self.notify("chunk", {"turn": turn, "text": chunk})
        except Exception as exc:
            title, hint = explain_error(exc, self.orch.settings)
            error = {"message": title, "hint": hint}
        cancelled = cancel.is_set() or record.cancelled
        record.cancelled = cancelled
        self.turn_log.append(record.finish(answer, estimate=not agent, failed=bool(error)))
        if answer or not error:
            self.session.add_turn(display, answer + ("\n\n[interrupted]" if cancelled else ""))
        self.busy = None
        if self.auto_memory and answer and not error and license_mod.status().plus:
            self.pool.submit(self._remember, display, answer)
        self.notify("turn_end", {"turn": turn, "answer": answer, "cancelled": cancelled, "error": error,
                                 "files": record.files})

    def _remember(self, request: str, answer: str) -> None:
        with contextlib.suppress(Exception):  # automatic memory must never get in the way
            for fact in plus.remember(self.orch.llm, self.root, request, answer):
                self.notify("event", {"turn": 0, "event": {"type": "info", "text": f"remembered in MYDEVAGENT.md: {fact}"}})

    def _approver(self, turn: int):
        def approve(req: ApprovalRequest) -> tuple[str, str]:
            if self.cancel.is_set():
                return "no", "interrupted by the user"
            request = f"r{next(self.ids)}"
            box: queue.Queue = queue.Queue(maxsize=1)
            self.pending[request] = box
            whole = all(len(t or "") <= MAX_FILE_CHARS for t in (req.before, req.after))
            self.notify("approval", {
                "request": request, "turn": turn, "tool": req.tool, "summary": req.summary, "diff": req.diff,
                "dangerous": req.dangerous, "path": req.args.get("path"), "command": req.args.get("command"),
                "host": req.args.get("host"), "server": req.args.get("server"), "mcp_tool": req.args.get("tool"),
                "before": req.before if whole else None, "after": req.after if whole else None})
            return box.get()

        return approve

    # -------------------------------------------------------------- helpers
    def _start_project(self) -> None:
        self.hooks = Hooks(self.root)
        if any(h.event == "SessionStart" and not h.unsupported for h in self.hooks.hooks):
            threading.Thread(target=self.hooks.start, args=("startup",), daemon=True).start()
        self.mcp.close()
        self.mcp = McpManager(self.root)
        if self.mcp:
            threading.Thread(target=self.mcp.connect_all, daemon=True).start()

    def _commands(self) -> list[dict[str, str]]:
        """The / commands the editor completes: custom ones and skills."""
        out = [{"name": name, "description": desc} for name, (desc, _) in extras.custom_commands(self.root).items()]
        out += [{"name": f"/skill {s.name}", "description": s.description} for s in load_skills(self.root).values()]
        out += [{"name": name, "description": desc} for name, desc in plus.COMMANDS.items()]
        out.append({"name": "/license", "description": "your license · /license <code> activates it"})
        return out

    def _expand(self, text: str) -> str:
        """/init, /skill <name> and custom commands become the request for the agent."""
        name, _, arg = text.partition(" ")
        name, arg = name.lower(), arg.strip()
        if name == "/init":
            return extras.INIT_TASK
        if name in plus.COMMANDS:
            why = license_mod.plus_needed()
            if why:
                raise ValueError(why)
            return plus.expand(name, arg)
        if name in ("/skill", "/skills"):
            skill_name, _, request = arg.partition(" ")
            skill = load_skills(self.root).get(skill_name.lower())
            if skill is None:
                raise ValueError(f"unknown skill: {skill_name or '(none)'}")
            task = request.strip() or "Apply this skill to the current project."
            return f"Follow the skill `{skill.name}` for this request.\n\n{skill.read()}\n\n# Request\n{task}"
        custom = extras.custom_commands(self.root)
        if name in custom:
            return extras.expand_command(custom[name][1], arg)
        return text

    def _editor_context(self, context: dict[str, Any]) -> dict[str, str]:
        """What the user is looking at in the editor: the open file and, if there is one, the selection."""
        path = str(context.get("file") or "")
        if not path:
            return {}
        selection = context.get("selection") or {}
        if selection.get("text"):
            where = f"{path} (lines {selection.get('start')}-{selection.get('end')} selected in the editor)"
            return {where: str(selection["text"])}
        line = f", line {context['line']}" if context.get("line") else ""
        return {"editor": f"The user has {path}{line} open in the editor."}


def inline_code(text: str, selection: str) -> str:
    """Only the code: strips the reasoning and the ``` fences that models add even when they shouldn't."""
    text = strip_thinking(text or "")
    fence = re.search(r"```[\w+#.-]*[^\S\n]*\n(.*?)\n?```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    text = text.strip("\n")
    if selection.endswith("\n") and not text.endswith("\n"):
        text += "\n"
    return text


def main(profile: str | None = None, root: Path | None = None, permission: str = "ask") -> None:
    from .config import load_settings

    out = sys.stdout.buffer
    sys.stdout = sys.stderr  # print and rich go to stderr: stdout belongs to the protocol only
    orch = Orchestrator(load_settings(overrides={"profile": profile} if profile else None))
    bridge = Bridge(orch, root or Path.cwd(), out, permission=permission, auto_memory=True)
    bridge.notify("ready", {"protocol": PROTOCOL, "version": __version__})
    bridge.serve(sys.stdin.buffer)
