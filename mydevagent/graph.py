"""The team of 15 agents, orchestrated with LangGraph.

    START ─▶ research? ─▶ architect ─▶ specialist × N (parallel) ─▶ join ─▶ test ─▶ gate × M (parallel)
                                             ▲                                              │
                                             └────────── revise (max_review_rounds) ◀── judge ─▶ docs? ─▶ END

The Formatter (agent 15) is run by the orchestrator after the graph, streaming.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from .config import Settings
from .llm import LLM, Completion
from .reasoning import effort_directive, extract_code_blocks, parse_review, scaffold, strip_thinking
from .registry import Agent, AgentRegistry
from .state import TeamState, latest_issues, render_context, truncate
from .tools import Toolbox
from .tools.web_search import format_results

EventHandler = Callable[[dict[str, Any]], None]


class Cancelled(Exception):
    """The user interrupted the request (e.g. Ctrl+C in the UI)."""


class Team:
    def __init__(self, settings: Settings, registry: AgentRegistry, llm: LLM, toolbox: Toolbox,
                 on_event: EventHandler | None = None, cancel: threading.Event | None = None) -> None:
        self.settings = settings
        self.cancel = cancel
        self.registry = registry
        self.llm = llm
        self.toolbox = toolbox
        self.on_event = on_event
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- helpers
    def emit(self, event: dict[str, Any]) -> None:
        if self.on_event:
            with self._lock:
                self.on_event(event)

    def think_for(self, mode: str, agent: Agent) -> bool:
        return self.settings.mode(mode).think and agent.tier == "reasoning"

    def system_prompt(self, agent: Agent, *, think: bool, fused_delivery: bool = False) -> str:
        """Persona (stable prefix → prefix cache) + role prompt + reasoning scaffold."""
        parts = [self.registry.persona, agent.prompt]
        if fused_delivery:
            formatter = self.registry["formatter"].prompt
            idx = formatter.find("## Output structure")
            rules = formatter[idx:] if idx >= 0 else formatter
            parts.append("# You deliver directly to the user (no other agent will edit your answer)\n" + rules)
        parts.append(scaffold(think))
        directive = effort_directive(self.llm.model_name(agent.tier), think)
        if directive:
            parts.append(directive)
        return "\n\n".join(parts)

    def messages(self, agent: Agent, state: TeamState, *, task: str = "", think: bool = False,
                 fused_delivery: bool = False, extra_reads: tuple[str, ...] = ()) -> list[dict[str, Any]]:
        context = render_context(agent, state, self.registry, self.settings.context, extra_reads)
        user = f"{context}\n\n## Your task\nAct as {agent.name}. {task}".strip()
        return [
            {"role": "system", "content": self.system_prompt(agent, think=think, fused_delivery=fused_delivery)},
            {"role": "user", "content": user},
        ]

    def run_agent(self, key: str, state: TeamState, *, task: str = "",
                  max_tokens: int | None = None) -> tuple[str, dict[str, Any]]:
        if self.cancel is not None and self.cancel.is_set():
            raise Cancelled(key)
        agent = self.registry[key]
        mode = (state.get("route") or {}).get("mode", "balanced")
        think = self.think_for(mode, agent)
        msgs = self.messages(agent, state, task=task, think=think)
        self.emit({"type": "agent_start", "agent": key, "name": agent.name})
        start = time.perf_counter()
        use_tools = self.settings.active_profile.native_tools and agent.tools
        try:
            if use_tools:
                result: Completion = self.llm.complete_with_tools(
                    msgs, tools=self.toolbox.schemas(agent.tools), executor=self._tool_executor(key),
                    tier=agent.tier, max_tokens=max_tokens or agent.max_tokens, temperature=agent.temperature)
            else:
                result = self.llm.complete(msgs, tier=agent.tier, max_tokens=max_tokens or agent.max_tokens,
                                           temperature=agent.temperature)
            text = strip_thinking(result.text)
            error = None
        except Exception as exc:  # a failing agent does not block the team
            result, text, error = Completion(text=""), "", f"{type(exc).__name__}: {exc}"
        entry = {
            "agent": key,
            "ms": int((time.perf_counter() - start) * 1000),
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "tool_calls": result.tool_calls,
            "error": error,
        }
        self.emit({"type": "agent_end", **entry, "name": agent.name})
        return text, entry

    def _tool_executor(self, agent_key: str):
        """Runs a tool and emits events (the UI shows them as ⏺ tool(args) / ⎿ result)."""

        def execute(name: str, args: dict[str, Any]) -> str:
            self.emit({"type": "tool_call", "agent": agent_key, "tool": name, "args": args})
            result = self.toolbox.execute(name, args)
            self.emit({"type": "tool_result", "agent": agent_key, "tool": name,
                       "ok": not result.startswith("ERROR"), "preview": result[:200]})
            return result

        return execute

    # ------------------------------------------------------------------ nodes
    def research_node(self, state: TeamState) -> dict[str, Any]:
        web = self.toolbox.ctx.web
        if not web.enabled():
            self.emit({"type": "info", "text": "offline: web research skipped"})
            return {"research": "OFFLINE: web research unavailable — flag versions/APIs that need verification."}
        queries = self._queries(state["request"])
        seen: set[str] = set()
        results = []
        for query in queries:
            response = web.search(query)
            for r in response.results:
                if r.url not in seen:
                    seen.add(r.url)
                    results.append(r)
        response.results = results[: self.settings.tools.web.max_results + 2]
        raw = format_results(response)
        pages = []
        for i, r in enumerate(response.results[: self.settings.tools.web.fetch_top_n], start=1):
            page = self._tool_executor("research")("web_fetch", {"url": r.url})
            if not page.startswith("ERROR"):
                pages.append(f"[{i}] extract from {r.url}:\n{truncate(page, self.settings.tools.web.max_page_chars)}")
        interim: TeamState = {**state, "research": raw + ("\n\n" + "\n\n".join(pages) if pages else "")}
        text, entry = self.run_agent("research", interim, task="Summarize the facts the team needs, with sources.")
        return {"research": text or raw, "trace": [entry]}

    def _queries(self, request: str) -> list[str]:
        prompt = ("Write 1-2 concise web search queries (one per line, no numbering, no quotes) to find "
                  "up-to-date official information for this programming request:\n" + request[:1200])
        try:
            out = self.llm.complete([{"role": "user", "content": prompt}], tier="fast", max_tokens=60,
                                    temperature=0.0).text
            lines = [re.sub(r"^[\s\-*\d.)]+", "", line).strip().strip('"') for line in strip_thinking(out).splitlines()]
            queries = [q for q in lines if 3 <= len(q) <= 200][:2]
        except Exception:
            queries = []
        return queries or [" ".join(request.split())[:200]]

    def architect_node(self, state: TeamState) -> dict[str, Any]:
        route = state["route"]
        task = ("Produce the plan. Available specialists for this request: "
                + ", ".join(route["specialists"]) + ".")
        text, entry = self.run_agent("architect", state, task=task)
        return {"plan": text, "trace": [entry]}

    def specialist_node(self, payload: dict[str, Any]) -> dict[str, Any]:
        key = payload["agent"]
        state: TeamState = payload["state"]
        round_ = state.get("round", 0)
        task = "Implement your part of the plan."
        if round_ > 0 and state.get("artifacts", {}).get(key):
            task = ("Revise your previous output to fix every BLOCKER/MAJOR issue that concerns your part. "
                    "Output the complete corrected files.\n\n## Your previous output\n"
                    + truncate(state["artifacts"][key], self.settings.context.max_section_chars * 2))
        text, entry = self.run_agent(key, state, task=task)
        return {"artifacts": {key: text}, "trace": [entry]}

    def join_node(self, state: TeamState) -> dict[str, Any]:
        return {}

    def test_node(self, state: TeamState) -> dict[str, Any]:
        artifacts = state.get("artifacts") or {}
        files = collect_files(artifacts, self.registry)
        if not files:
            return {"test_report": "NOT RUN: no code artifacts."}
        text, entry = self.run_agent("debug_test", state,
                                     task="Write tests and ONE self-check script marked `run` for the team's code.")
        run_blocks = [b for b in extract_code_blocks(text) if b.is_run]
        update: dict[str, Any] = {"artifacts": {"debug_test": text}, "trace": [entry]}
        files.update(collect_files({"debug_test": text}, self.registry))
        if run_blocks:
            code, lang = run_blocks[0].body, run_blocks[0].lang or "python"
        elif any(p.endswith(".py") and p.rsplit("/", 1)[-1].startswith("test") for p in files):
            code, lang = FALLBACK_PY_RUNNER, "python"  # no self-check: run the tests that were written
        else:
            update["test_report"] = "NOT RUN: no self-check script provided."
            return update
        self.emit({"type": "info", "text": "sandbox: running self-check"})
        result = self.toolbox.ctx.sandbox.run(code, lang, files)
        if code is FALLBACK_PY_RUNNER and result.exit_code == 3:
            update["test_report"] = "NOT RUN: no runnable stdlib tests (pytest-only or empty)."
            return update
        update["test_report"] = result.summary()
        self.emit({"type": "sandbox", "ok": result.ok, "skipped": bool(result.skipped)})
        if not result.ok and not result.skipped:
            update["issues"] = [{"agent": "debug_test", "severity": "BLOCKER", "round": state.get("round", 0),
                                 "text": "self-check failed: " + truncate(result.summary(), 1200)}]
        return update

    def gate_node(self, payload: dict[str, Any]) -> dict[str, Any]:
        key = payload["agent"]
        state: TeamState = payload["state"]
        text, entry = self.run_agent(key, state, task="Review the team artifacts now.")
        review = parse_review(text) if text else None
        if review is None:
            return {"trace": [entry]}
        issues = [{"agent": key, "severity": sev, "text": body, "round": state.get("round", 0)}
                  for sev, body in review.issues if body.lower().strip(" .") != "none"]
        return {"issues": issues, "verdicts": {key: review.verdict}, "trace": [entry]}

    def judge_node(self, state: TeamState) -> dict[str, Any]:
        round_ = state.get("round", 0)
        mode = state["route"]["mode"]
        blocking = [i for i in latest_issues(state)
                    if i.get("round", 0) == round_ and i["severity"] in ("BLOCKER", "MAJOR")]
        if blocking and round_ < self.settings.mode(mode).max_review_rounds:
            self.emit({"type": "info", "text": f"revision round {round_ + 1}: {len(blocking)} blocking issues"})
            return {"round": round_ + 1, "decision": "revise"}
        return {"decision": "done"}

    def docs_node(self, state: TeamState) -> dict[str, Any]:
        text, entry = self.run_agent("docs", state, task="Write the minimal documentation for the delivered code.")
        return {"artifacts": {"docs": text}, "trace": [entry]}

    # ---------------------------------------------------------------- routing
    def fan_out_specialists(self, state: TeamState) -> list[Send]:
        return [Send("specialist", {"agent": key, "state": state}) for key in state["route"]["specialists"]]

    def after_start(self, state: TeamState) -> str:
        return "research" if state["route"].get("research") else "architect"

    def fan_out_gate(self, state: TeamState):
        gate = state["route"].get("gate") or []
        if not gate:
            return "judge"
        return [Send("gate", {"agent": key, "state": state}) for key in gate]

    def after_judge(self, state: TeamState):
        if state.get("decision") == "revise":
            return self.fan_out_specialists(state)
        return "docs" if state["route"].get("docs") else END

    def build(self):
        graph = StateGraph(TeamState)
        graph.add_node("research", self.research_node)
        graph.add_node("architect", self.architect_node)
        graph.add_node("specialist", self.specialist_node)
        graph.add_node("join", self.join_node)
        graph.add_node("test", self.test_node)
        graph.add_node("gate", self.gate_node)
        graph.add_node("judge", self.judge_node)
        graph.add_node("docs", self.docs_node)

        graph.add_conditional_edges(START, self.after_start, ["research", "architect"])
        graph.add_edge("research", "architect")
        graph.add_conditional_edges("architect", self.fan_out_specialists, ["specialist"])
        graph.add_edge("specialist", "join")
        graph.add_edge("join", "test")
        graph.add_conditional_edges("test", self.fan_out_gate, ["gate", "judge"])
        graph.add_edge("gate", "judge")
        graph.add_conditional_edges("judge", self.after_judge, ["specialist", "docs", END])
        graph.add_edge("docs", END)
        return graph.compile()


EXTENSIONS = {"python": "py", "py": "py", "typescript": "ts", "ts": "ts", "tsx": "tsx", "javascript": "js",
              "js": "js", "jsx": "jsx", "go": "go", "rust": "rs", "java": "java", "kotlin": "kt", "csharp": "cs",
              "c#": "cs", "cpp": "cpp", "c++": "cpp", "c": "c", "ruby": "rb", "php": "php", "sql": "sql",
              "swift": "swift", "html": "html", "css": "css", "vue": "vue", "svelte": "svelte",
              "yaml": "yaml", "yml": "yml", "toml": "toml", "dockerfile": "Dockerfile"}

FALLBACK_PY_RUNNER = """\
# Auto-generated stdlib test runner (sandbox has no third-party packages such as pytest)
import importlib.util, inspect, pathlib, sys, traceback, unittest
ran = failed = skipped = 0
for path in sorted(pathlib.Path(".").rglob("test*.py")):
    if path.name == "run.py":
        continue
    source = path.read_text(encoding="utf-8")
    if "import pytest" in source or "from pytest" in source:
        skipped += 1
        print(f"skip {path} (needs pytest)")
        continue
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception:
        failed += 1
        traceback.print_exc()
        continue
    for name, obj in vars(module).items():
        if name.startswith("test") and inspect.isfunction(obj) and not inspect.signature(obj).parameters:
            ran += 1
            try:
                obj()
            except Exception:
                failed += 1
                print(f"FAIL {path}::{name}")
                traceback.print_exc()
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    if suite.countTestCases():
        result = unittest.TextTestRunner(verbosity=0).run(suite)
        ran += result.testsRun
        failed += len(result.failures) + len(result.errors)
print(f"{ran} tests run, {failed} failed, {skipped} files skipped")
if ran == 0 and failed == 0:
    sys.exit(3)
sys.exit(1 if failed else 0)
"""


def collect_files(artifacts: dict[str, str], registry: AgentRegistry) -> dict[str, str]:
    """Extracts the files from the artifacts, in agent-id order (the last one wins).

    Blocks without a path get a fallback name (`main.py`, `main_2.py`, …) so the code stays
    runnable in the sandbox even when a small model ignores the `file=` convention.
    """
    files: dict[str, str] = {}
    order = {a.key: a.id for a in registry}
    for key in sorted(artifacts, key=lambda k: order.get(k, 99)):
        unnamed = 0
        for block in extract_code_blocks(artifacts[key]):
            if block.is_run:
                continue
            path = block.path
            if path is None:
                ext = EXTENSIONS.get(block.lang)
                if ext is None:
                    continue
                unnamed += 1
                stem = "test_main" if key == "debug_test" else "main"
                path = ext if ext == "Dockerfile" else f"{stem}{'' if unnamed == 1 else f'_{unnamed}'}.{ext}"
            files[path] = block.body
    return files
