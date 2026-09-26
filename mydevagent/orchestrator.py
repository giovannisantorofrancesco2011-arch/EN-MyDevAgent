"""Entry point: route → (fast: 1 call) | (balanced/deep: team graph) → streaming Formatter."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from .config import Settings, get_settings
from .graph import Cancelled, EventHandler, Team
from .llm import LLM, build_llm
from .reasoning import ThinkFilter
from .registry import AgentRegistry, load_registry
from .router import Route, Router
from .state import TeamState, render_files, render_history
from .tools import Toolbox
from .tools.vision import describe_images
from .tools.web_search import format_results


@dataclass
class RunInfo:
    route: Route | None = None
    trace: list[dict[str, Any]] = field(default_factory=list)
    started: float = field(default_factory=time.perf_counter)
    elapsed_ms: int = 0
    final_chars: int = 0
    test_report: str = ""
    artifacts: dict[str, str] = field(default_factory=dict)

    @property
    def tokens(self) -> int:
        used = sum(t.get("prompt_tokens", 0) + t.get("completion_tokens", 0) for t in self.trace)
        return used + self.final_chars // 4

    def summary(self) -> str:
        if not self.route:
            return ""
        agents = len({t["agent"] for t in self.trace}) + 1  # +1: final streaming agent
        return f"{self.route.mode} · {agents} agents · {self.elapsed_ms / 1000:.1f}s · ~{self.tokens:,} tokens"


def sandbox_footer(report: str) -> str:
    first = (report or "NOT RUN").split("\n", 1)[0].strip()
    if first.startswith("PASS"):
        icon = "✅"
    elif first.startswith(("FAIL", "TIMEOUT")):
        icon = "❌"
    else:
        icon = "⚠️"
    return f"\n\n---\n{icon} Sandbox: {first}\n"


class Orchestrator:
    def __init__(self, settings: Settings | None = None, llm: LLM | None = None,
                 registry: AgentRegistry | None = None) -> None:
        self.settings = settings or get_settings()
        self.registry = registry or load_registry(self.settings)
        self.llm = llm or build_llm(self.settings)
        self.toolbox = Toolbox(self.settings, self.llm)
        self.router = Router(self.settings, self.registry, self.llm)
        self.last_run = RunInfo()

    # ---------------------------------------------------------------- public
    def route(self, request: str, mode: str | None = None, has_images: bool = False) -> Route:
        return self.router.route(request, mode=mode, has_images=has_images)

    def run(
        self,
        request: str,
        *,
        history: list[dict[str, Any]] | None = None,
        files: dict[str, str] | None = None,
        images: list[str] | None = None,
        mode: str | None = None,
        on_event: EventHandler | None = None,
        show_thinking: bool = False,
        cancel: threading.Event | None = None,
    ) -> Iterator[str]:
        """Runs the request and streams back the final answer (text chunks).

        `cancel` (threading.Event) stops the work: before the next agent or at the next chunk.
        """
        info = RunInfo()
        self.last_run = info
        route = self.route(request, mode=mode, has_images=bool(images))
        info.route = route
        emit = on_event or (lambda _e: None)
        emit({"type": "route", "mode": route.mode, "agents": route.agents, "reasons": route.reasons,
              "suggest_ultra": route.suggest_ultra})

        team = Team(self.settings, self.registry, self.llm, self.toolbox, on_event=on_event, cancel=cancel)
        state = self._initial_state(route, history or [], files or {}, images or [], emit)

        if route.mode == "fast":
            agent = self.registry[route.primary]
            if route.research:
                web = self.toolbox.ctx.web
                state["research"] = (format_results(web.search(route.request)) if web.enabled()
                                     else "OFFLINE: web search unavailable — mention what needs verification.")
            msgs = team.messages(agent, state, think=team.think_for("fast", agent), fused_delivery=True,
                                 extra_reads=("history", "files", "rag", "research", "image_notes"),
                                 task="Answer the request directly.")
            tier, max_tokens, temperature = agent.tier, max(agent.max_tokens, 1500), agent.temperature
            emit({"type": "agent_start", "agent": agent.key, "name": agent.name})
        else:
            try:
                if route.mode == "ultra-deep":
                    from .ultra import ChatImplementer, UltraPipeline

                    web = self.toolbox.ctx.web
                    pipeline = UltraPipeline(team, route, ChatImplementer(team, state), online=web.enabled(), web=web)
                    final_state = pipeline.run(state)
                else:
                    final_state = team.build().invoke(state, {"recursion_limit": 60})
            except Cancelled:
                emit({"type": "cancelled"})
                return
            info.trace = list(final_state.get("trace", []))
            info.test_report = final_state.get("test_report", "")
            info.artifacts = dict(final_state.get("artifacts", {}))
            formatter = self.registry["formatter"]
            msgs = team.messages(formatter, final_state, task="Deliver the final answer to the user now.")
            tier, max_tokens, temperature = formatter.tier, formatter.max_tokens, formatter.temperature
            emit({"type": "agent_start", "agent": "formatter", "name": formatter.name})

        think_filter = ThinkFilter(show=show_thinking)
        final_start = time.perf_counter()
        chars = 0
        stream = self.llm.stream(msgs, tier=tier, max_tokens=max_tokens, temperature=temperature)
        for chunk in stream:
            if cancel is not None and cancel.is_set():
                close = getattr(stream, "close", None)
                if close:
                    close()  # closes the connection: the model stops generating
                emit({"type": "cancelled"})
                return
            out = think_filter.feed(chunk)
            if out:
                chars += len(out)
                yield out
        tail = think_filter.flush()
        if tail:
            chars += len(tail)
            yield tail
        if route.mode != "fast":
            yield sandbox_footer(info.test_report)  # real outcome, never generated by the model
        info.final_chars = chars + sum(len(m["content"]) for m in msgs if isinstance(m["content"], str))
        info.elapsed_ms = int((time.perf_counter() - info.started) * 1000)
        emit({"type": "agent_end", "agent": "final", "ms": int((time.perf_counter() - final_start) * 1000)})
        emit({"type": "done", "summary": info.summary()})

    def ask(self, request: str, **kwargs: Any) -> str:
        return "".join(self.run(request, **kwargs))

    # --------------------------------------------------------------- helpers
    def _initial_state(self, route: Route, history: list[dict[str, Any]], files: dict[str, str],
                       images: list[str], emit: EventHandler) -> TeamState:
        cfg = self.settings.context
        state: TeamState = {
            "request": route.request,
            "history": render_history(history, cfg),
            "files": render_files(files, cfg) if files else "",
            "route": {"mode": route.mode, "specialists": route.specialists, "gate": route.gate,
                      "research": route.research, "docs": route.docs},
            "artifacts": {},
            "issues": [],
            "verdicts": {},
            "round": 0,
            "trace": [],
        }
        rag_cfg = self.settings.tools.rag
        if rag_cfg.enabled:
            index = self.toolbox.ctx.index
            if index.exists():
                state["rag"] = index.context(route.request, rag_cfg.top_k, cfg.max_section_chars)
                if state["rag"]:
                    emit({"type": "info", "text": "RAG: codebase context added"})
        if images:
            emit({"type": "info", "text": f"vision: describing {len(images)} image(s)"})
            state["image_notes"] = describe_images(self.llm, images, route.request)
        return state
