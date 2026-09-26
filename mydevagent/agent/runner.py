"""Agent mode + team: the Architect plans, the agent edits the files, the quality gates
review the real diff and the agent fixes it.

    fast      → AgentLoop with the specialist picked by the router
    balanced  → Architect → AgentLoop → Reviewer on the diff → fixes (1 round)
    deep      → Architect → AgentLoop → Security/Performance/Edge/Reviewer on the diff → fixes (2 rounds)
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Any

import httpx

from ..graph import Cancelled, Team
from ..hooks import Hooks
from ..mcp import McpManager
from ..skills import load_skills, skills_prompt
from ..state import TeamState, render_files, render_history, truncate
from ..subagents import SubAgent, load_subagents, subagents_prompt
from ..tools import preview
from ..tools.web_fetch import UnsafeURLError, fetch_text
from ..tools.web_search import format_results
from .checkpoints import CheckpointStore
from .context import extra_dirs_context, project_context, read_memory
from .loop import AgentLoop
from .permissions import Approver, PermissionPolicy
from .tools import AgentTools

MAX_STEPS = {"fast": 15, "balanced": 25, "deep": 35, "ultra-deep": 45}
CHANGE_INTENT_RE = re.compile(
    r"\b(add\w*|creat\w*|implement\w*|fix\w*|remov\w*|delet\w*|renam\w*|refactor\w*|writ\w*|updat\w*|"
    r"migrat\w*|chang\w*|build\w*|modif\w*|edit\w*|replac\w*|convert\w*|mov\w*|improv\w*|rewrit\w*|"
    r"generat\w*|install\w*|set\s+up|setup|make|clean\s+up|optimi[sz]\w*|"
    r"aggiung\w*|crea\w*|modific\w*|sistem\w*|correggi\w*|rimuov\w*|elimin\w*|rinomin\w*|"
    r"sposta\w*|scriv\w*|migra\w*|aggiorn\w*)\b", re.IGNORECASE)


QUESTION_RE = re.compile(
    r"^\s*(what|how|why|which|when|where|who|explain|describe|show|is|are|does|do|can|should|"
    r"cosa|che cosa|come|perch[eé]|quale|quali|quando|dove|chi|quanto|spiega\w*|descrivi\w*|mostra\w*)\b",
    re.IGNORECASE)


def wants_changes(request: str) -> bool:
    """Does the request ask to change the project (and not just for an explanation or an answer)?"""
    text = re.sub(r"^\s*/[\w-]+\s*", "", request)  # strips /fast, /deep, …
    if QUESTION_RE.match(text) or (text.rstrip().endswith("?") and not re.match(
            r"\s*(can you|could you|would you|will you|please|puoi|potresti)\b", text, re.IGNORECASE)):
        return False
    return bool(CHANGE_INTENT_RE.search(text))
EventHandler = Callable[[dict[str, Any]], None]
# Claude Code's permission names, for the hooks' permission_mode field
PERMISSION_MODES = {"ask": "default", "auto-edit": "acceptEdits", "plan": "plan", "auto": "bypassPermissions"}
LEARN_PROMPT = """# Learning mode: the user is learning to program
- Before changing code, say in one or two simple sentences what you are about to do and why.
- Leave ONE small, meaningful piece for the user to write (a condition, a loop or a function body of 3-10 lines):
  write everything else, and where their code goes put a `TODO(you):` comment with a hint of what to write (not
  the solution). Then tell them the file and what to write. Tests that exercise their part may fail until they
  write it: that is expected, say so instead of filling it in. Skip the exercise for urgent bug fixes or when
  the user asks you to write everything.
- End with a short "💡 Good to know" section: 2-3 bullet points that explain the concepts you used, in simple words.
- When the user says they wrote their part, read it, say what is right and explain gently what to fix, without
  rewriting it for them unless they ask."""
NO_CHANGES = ("The request asks to change the project, but you have not modified any file. Apply the changes now "
              "with edit_file / write_file, run the tests, then give your final answer. If you believe no change is "
              "needed, explain why in one line.")


class AgentRunner:
    def __init__(self, orchestrator, root: Path, policy: PermissionPolicy, *, approver: Approver | None = None,
                 checkpoints: CheckpointStore | None = None, hooks: Hooks | None = None,
                 mcp: McpManager | None = None, learn: bool = False,
                 extra_dirs: list[Path] | None = None) -> None:
        self.orch = orchestrator
        self.root = Path(root).resolve()
        self.policy = policy
        self.approver = approver
        self.checkpoints = checkpoints or CheckpointStore(self.root)
        self.hooks = hooks if hooks is not None else Hooks(self.root)
        self.mcp = mcp  # None: servers are created for this request and closed at the end
        self.learn = learn  # learn mode: explain and leave the user a piece to write
        self.extra_dirs = [Path(d).resolve() for d in extra_dirs or []]  # extra folders (/add-dir)

    def run(self, request: str, **kwargs) -> Iterator[str]:
        if self.mcp is not None:
            yield from self._run(request, self.mcp, **kwargs)
            return
        mcp = McpManager(self.root)
        try:
            yield from self._run(request, mcp, **kwargs)
        finally:
            mcp.close()

    def _run(self, request: str, mcp: McpManager, *, history: list[dict[str, Any]] | None = None,
            files: dict[str, str] | None = None, mode: str | None = None, on_event: EventHandler | None = None,
            cancel: threading.Event | None = None) -> Iterator[str]:
        emit = on_event or (lambda _e: None)
        self.hooks.mode = PERMISSION_MODES.get(self.policy.mode, "default")
        submitted = self.hooks.run("UserPromptSubmit", payload={"prompt": request})
        for note in submitted.notes:
            emit({"type": "info", "text": note})
        if submitted.blocked:
            yield f"⛔ Request blocked by a hook: {submitted.reason}"
            return
        orch = self.orch
        route = orch.route(request, mode=mode)
        settings = orch.settings
        registry = orch.registry
        team = Team(settings, registry, orch.llm, orch.toolbox, on_event=on_event, cancel=cancel)
        lead = registry[route.primary if route.mode == "fast" else route.specialists[0]]
        gates = [] if route.mode == "fast" else route.gate
        agents = ([] if route.mode == "fast" else ["architect"]) + [lead.key] + gates
        emit({"type": "route", "mode": route.mode, "agents": agents + ["formatter"], "reasons": route.reasons,
              "agentic": True, "suggest_ultra": route.suggest_ultra})

        web = orch.toolbox.ctx.web
        web_fn = (lambda q: format_results(web.search(q))) if settings.tools.web.enabled else None
        fetch_fn = partial(self._fetch, orch.toolbox.ctx) if settings.tools.web.enabled else None
        preview_fn = partial(preview.preview, llm=orch.llm) if preview.find_browser() else None
        memory = read_memory(self.root)
        skills = load_skills(self.root)
        subagents = load_subagents(self.root)
        hook_context = "\n".join(filter(None, (self.hooks.session_context, submitted.context)))
        base_context = "\n\n".join(p for p in (skills_prompt(skills), mcp.prompt(),
                                                project_context(self.root, route.request),
                                                extra_dirs_context(self.root, self.extra_dirs),
                                                f"# Context from hooks\n{hook_context}" if hook_context else "")
                                     if p)
        context = "\n\n".join(p for p in (subagents_prompt(subagents), base_context,
                                            LEARN_PROMPT if self.learn else "") if p)

        def tools_for(allowed: set[str] | None = None, **extra) -> AgentTools:
            return AgentTools(self.root, self.policy, self.checkpoints, approver=self.approver, emit=emit,
                              web_search=web_fn, web_fetch=fetch_fn, preview=preview_fn, memory=memory, skills=skills,
                              hooks=self.hooks, mcp=mcp, allowed=allowed, extra_dirs=self.extra_dirs, **extra)

        def spawn(agent: SubAgent, prompt: str) -> tuple[str, AgentTools]:
            """A subagent: its own context, its own tools (no `task`: no sub-subagents)."""
            child = tools_for(agent.allowed())
            loop = AgentLoop(orch.llm, child, system=f"{agent.prompt}\n\n{base_context}".strip(), tier=agent.tier,
                             max_steps=MAX_STEPS["fast"], native=settings.active_profile.native_tools, emit=emit,
                             cancel=cancel, context_chars=settings.active_profile.num_ctx * 3,
                             stop_event="SubagentStop")
            emit({"type": "agent_start", "agent": agent.name, "name": f"Agent {agent.name}"})
            started = time.perf_counter()
            result = loop.run(prompt)
            emit({"type": "agent_end", "agent": agent.name, "name": f"Agent {agent.name}",
                  "ms": int((time.perf_counter() - started) * 1000), "prompt_tokens": result.prompt_tokens,
                  "completion_tokens": result.completion_tokens, "tool_calls": result.tool_calls, "error": None,
                  "counted": True})
            return result.text or "(no report)", child

        tools = tools_for(subagents=subagents, spawn=spawn)
        self.checkpoints.begin(route.request)

        state: TeamState = {
            "request": route.request,
            "history": render_history(history or [], settings.context),
            "files": render_files(files, settings.context) if files else "",
            "rag": context,
            "route": {"mode": route.mode, "specialists": route.specialists, "gate": gates,
                      "research": route.research, "docs": False},
            "artifacts": {}, "issues": [], "verdicts": {}, "round": 0, "trace": [],
        }
        if route.research and web.enabled():
            state["research"] = format_results(web.search(route.request))

        if route.mode == "ultra-deep":
            yield from self._ultra(route, team, state, tools, context, emit, cancel)
            return

        try:
            plan = ""
            if route.mode != "fast":
                plan, _ = team.run_agent("architect", state, task=(
                    "Produce the plan for an agent that will edit the project directly. Specialists: "
                    + ", ".join(route.specialists) + "."))
                state["plan"] = plan

            helpers = [registry[k] for k in route.specialists if k != lead.key]
            also = "".join(f"\n- {a.name}: {a.role}" for a in helpers)
            # compact role prompt: chat-mode output instructions confuse small models
            system = "\n\n".join(filter(None, [
                registry.persona,
                f"# Your role: {lead.name}\n{lead.role}\nGoal: {lead.goal}",
                f"# Also apply the expertise of:{also}" if also else "",
                context,
            ]))
            native = settings.active_profile.native_tools
            loop = AgentLoop(orch.llm, tools, system=system, tier=lead.tier if lead.tier != "reasoning" else "main",
                             max_steps=MAX_STEPS.get(route.mode, 25), native=native, emit=emit, cancel=cancel,
                             context_chars=settings.active_profile.num_ctx * 3)
            emit({"type": "agent_start", "agent": lead.key, "name": lead.name})
            started = time.perf_counter()
            result = loop.run(self._task(route.request, state, plan, self._rag(route.request)))
            emit({"type": "agent_end", "agent": lead.key, "name": lead.name,
                  "ms": int((time.perf_counter() - started) * 1000),
                  "prompt_tokens": result.prompt_tokens, "completion_tokens": result.completion_tokens,
                  "tool_calls": result.tool_calls, "error": None, "quiet": True, "counted": True})

            if (not tools.changed and not tools.user_denied and wants_changes(route.request)
                    and self.policy.mode != "plan"):
                emit({"type": "info", "text": "no files changed: asking the agent to apply the changes"})
                result = loop.follow_up(NO_CHANGES)

            review_note = ""
            rounds = settings.mode(route.mode).max_review_rounds if route.mode != "fast" else 0
            round_ = 0
            while gates and tools.changed:
                diff = self.checkpoints.session_diff(self.checkpoints.current.id if self.checkpoints.current else 0)
                blocking = self._review(team, state, gates, diff, tools, result.text, round_)
                if not blocking:
                    review_note = "✅ Review: approved"
                    break
                if round_ >= rounds:
                    review_note = f"⚠️ Review: {len(blocking)} unresolved issues (see above)"
                    break
                round_ += 1
                emit({"type": "info", "text": f"review {round_}: {len(blocking)} issues to fix"})
                issues = "\n".join(f"- [{i['severity']}] ({i['agent']}) {i['text']}" for i in blocking)
                result = loop.follow_up(
                    "The reviewers found these problems in your changes:\n" + issues +
                    "\nFix them with the tools, re-run the tests, then give your final answer."
                    + (" Leave the TODO(you) parts to the user." if self.learn else ""))
        except Cancelled:
            emit({"type": "cancelled"})
            return

        yield result.text or "(no answer)"
        yield self._footer(tools, review_note, result, wants_changes(route.request) and self.policy.mode != "plan")
        emit({"type": "done", "summary": f"{route.mode} · agent · {result.steps} steps · "
                                         f"{result.tool_calls} tools · ~{result.prompt_tokens + result.completion_tokens:,} tokens"})

    # ------------------------------------------------------------ ultra-deep
    def _ultra(self, route, team: Team, state: TeamState, tools: AgentTools, context: str, emit, cancel):
        from ..ultra import UltraPipeline, issues_summary

        orch = self.orch
        registry = orch.registry
        settings = orch.settings
        runner = self
        emit({"type": "info", "text": "ultra-deep: 35 agents at work (this can take several minutes)"})

        class AgentImplementer:
            def __init__(self) -> None:
                self.loop: AgentLoop | None = None
                self.summary = ""

            def implement(self, task: str, specialists: list[str]) -> str:
                lead = registry[specialists[0]]
                helpers = "".join(f"\n- {registry[k].name}: {registry[k].role}" for k in specialists[1:])
                system = "\n\n".join(filter(None, [
                    registry.persona, f"# Your role: {lead.name}\n{lead.role}\nGoal: {lead.goal}",
                    f"# Also apply the expertise of:{helpers}" if helpers else "", context]))
                self.loop = AgentLoop(orch.llm, tools, system=system, tier="main", max_steps=MAX_STEPS["ultra-deep"],
                                      native=settings.active_profile.native_tools, emit=emit, cancel=cancel,
                                      context_chars=settings.active_profile.num_ctx * 3)
                emit({"type": "agent_start", "agent": lead.key, "name": lead.name})
                started = time.perf_counter()
                rag = runner._rag(route.request)
                result = self.loop.run(task + (f"\n\n# Possibly relevant code\n{rag}" if rag else ""))
                emit({"type": "agent_end", "agent": lead.key, "name": lead.name,
                      "ms": int((time.perf_counter() - started) * 1000), "prompt_tokens": result.prompt_tokens,
                      "completion_tokens": result.completion_tokens, "tool_calls": result.tool_calls, "error": None,
                      "counted": True})
                self.summary = result.text
                return result.text

            def expects_changes(self) -> bool:
                return wants_changes(route.request) and runner.policy.mode != "plan"

            def subject(self) -> str:
                cp = runner.checkpoints.current
                diff = runner.checkpoints.session_diff(cp.id if cp else 0)
                return f"```diff\n{diff}\n```" if diff.strip() else "(no file changes)"

            def run_tests(self) -> str:
                return tools.execute("run_tests", {})

            def fix(self, fixes: str) -> str:
                result = self.loop.follow_up("The review board (35 agents) asks you to fix these problems:\n"
                                             + fixes + "\nFix them with the tools, re-run the tests, then give "
                                             "your final answer.")
                self.summary = result.text
                return result.text

            def apply_docs(self, notes: str) -> str:
                if not tools.changed:
                    return ""
                result = self.loop.follow_up(
                    "Documentation and release notes from the team are below. If the project has a README or "
                    "CHANGELOG, update them briefly with tools (skip if not useful). Then give your final "
                    "answer.\n\n" + truncate(notes, 4000))
                self.summary = result.text
                return result.text

        implementer = AgentImplementer()
        web = orch.toolbox.ctx.web
        try:
            pipeline = UltraPipeline(team, route, implementer, online=web.enabled(), web=web)
            final_state = pipeline.run(state)
            formatter = registry["formatter"]
            final_state["artifacts"] = {**final_state.get("artifacts", {}),
                                        "implementation": f"Agent summary: {implementer.summary}\n\n"
                                                          f"{implementer.subject()}"}
            emit({"type": "agent_start", "agent": "formatter", "name": formatter.name})
            text, entry = team.run_agent("formatter", final_state, task=(
                "Changes are ALREADY applied to the project files (see the diff). Do not repeat whole files: "
                "summarise what was built/changed and why, list the files, how to run/test, and residual risks."))
        except Cancelled:
            emit({"type": "cancelled"})
            return
        yield text or implementer.summary
        loop_result = implementer.loop.result if implementer.loop else None
        footer = self._footer(tools, issues_summary(final_state), loop_result,
                              wants_changes(route.request)) if loop_result else ""
        yield footer
        agents_used = len(pipeline.participants | {"formatter"})
        emit({"type": "done", "summary": f"ultra-deep · {agents_used} agents · "
                                         f"{loop_result.steps if loop_result else 0} agent steps"})

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _fetch(ctx, url: str) -> str:
        if not ctx.connectivity.online():
            return "OFFLINE: cannot fetch pages."
        try:
            return fetch_text(url, ctx.settings.tools.web)
        except (UnsafeURLError, httpx.HTTPError) as exc:
            return f"ERROR: {type(exc).__name__}: {exc}"

    def _rag(self, query: str) -> str:
        """Relevant code snippets from the project's semantic index, if there is one."""
        from ..tools.rag import CodeIndex

        settings = self.orch.settings
        if not settings.tools.rag.enabled:
            return ""
        index_settings = settings.model_copy(deep=True)
        index_settings.tools.filesystem.root = str(self.root)
        index = CodeIndex.for_workspace(index_settings, self.orch.llm)
        if not index.exists():
            return ""
        try:
            return index.context(query, k=4, max_chars=4000)
        except Exception:
            return ""

    @staticmethod
    def _task(request: str, state: TeamState, plan: str, rag: str = "") -> str:
        parts = [f"# Task\n{request}"]
        if state.get("history"):
            parts.append(f"# Conversation so far\n{state['history']}")
        if state.get("files"):
            parts.append(f"# Attached files / command output\n{state['files']}")
        if state.get("research"):
            parts.append(f"# Web research\n{state['research']}")
        if rag:
            parts.append(f"# Possibly relevant code (semantic search, verify with read_file)\n{rag}")
        if plan:
            parts.append(f"# Plan from the Architect (follow it, adapt if the code says otherwise)\n{plan}")
        return "\n\n".join(parts)

    def _review(self, team: Team, state: TeamState, gates: list[str], diff: str, tools: AgentTools,
                summary: str, round_: int) -> list[dict[str, Any]]:
        test = tools.last_test
        test_report = "NOT RUN" if test is None else f"{'PASS' if test[1] else 'FAIL'} ({test[0]})\n{test[2][-1500:]}"
        review_state: TeamState = {**state, "round": round_, "test_report": test_report,
                                   "artifacts": {"language": f"Agent summary: {summary}\n\n```diff\n{diff}\n```"}}
        with ThreadPoolExecutor(max_workers=max(1, len(gates))) as pool:
            outputs = list(pool.map(lambda key: team.gate_node({"agent": key, "state": review_state}), gates))
        issues = [i for out in outputs for i in out.get("issues", [])]
        blocking = [i for i in issues if i["severity"] in ("BLOCKER", "MAJOR")]
        # in learn mode the TODO(you) parts are the user's exercise, not an issue to fix
        return [i for i in blocking if "todo" not in i["text"].lower()] if self.learn else blocking

    @staticmethod
    def _footer(tools: AgentTools, review_note: str, result, request_wants_changes: bool = False) -> str:
        lines = []
        if tools.changed:
            lines.append("📝 Files changed: " + ", ".join(tools.changed) + "  (/undo to revert)")
        if tools.last_test:
            command, ok, _ = tools.last_test
            lines.append(f"{'✅' if ok else '❌'} Tests: {'passed' if ok else 'failed'} ({command})")
        elif tools.changed:
            lines.append("⚠️ Tests: not run")
        if not tools.changed and request_wants_changes and not tools.user_denied:
            lines.insert(0, "⚠️ No files changed: the agent did not apply any changes (try again, or use a "
                            "bigger model)")
        if review_note:
            lines.append(review_note)
        if result.stopped == "max_steps":
            lines.append('⚠️ Step limit reached: type "continue" to keep going')
        return ("\n\n---\n" + "\n".join(lines) + "\n") if lines else ""
