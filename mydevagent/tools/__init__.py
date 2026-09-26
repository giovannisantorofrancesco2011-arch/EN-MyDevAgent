"""MyDevAgent tools + registry for custom tools (`@tool` decorator)."""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..config import Settings
    from ..llm import LLM

MAX_TOOL_OUTPUT = 8000


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    func: Callable[..., str]

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }


_REGISTRY: dict[str, Tool] = {}


def tool(name: str, description: str, parameters: dict[str, Any] | None = None):
    """Register a function as a tool. The function receives `ctx: ToolContext` + keyword arguments.

    Example:
        @tool("jira_issue", "Read a Jira issue", {"type": "object",
              "properties": {"key": {"type": "string"}}, "required": ["key"]})
        def jira_issue(ctx, key: str) -> str: ...
    """
    params = parameters or {"type": "object", "properties": {}}

    def decorator(func: Callable[..., str]) -> Callable[..., str]:
        _REGISTRY[name] = Tool(name=name, description=description, parameters=params, func=func)
        return func

    return decorator


def registered_tools() -> dict[str, Tool]:
    return dict(_REGISTRY)


@dataclass
class ToolContext:
    settings: Settings
    llm: LLM | None = None
    cache: dict[str, Any] = field(default_factory=dict)

    # services are created on demand (lazily) so startup stays fast
    def service(self, name: str, factory: Callable[[], Any]) -> Any:
        if name not in self.cache:
            self.cache[name] = factory()
        return self.cache[name]

    @property
    def connectivity(self):
        from .connectivity import Connectivity

        return self.service("connectivity", lambda: Connectivity(self.settings.tools.connectivity))

    @property
    def workspace(self):
        from .filesystem import Workspace

        cfg = self.settings.tools.filesystem
        return self.service("workspace", lambda: Workspace(cfg.root, allow_write=cfg.allow_write))

    @property
    def web(self):
        from .web_search import WebSearch

        return self.service("web", lambda: WebSearch(self.settings.tools.web, self.connectivity))

    @property
    def sandbox(self):
        from .sandbox import Sandbox

        return self.service("sandbox", lambda: Sandbox(self.settings.tools.sandbox))

    @property
    def index(self):
        from .rag import CodeIndex

        return self.service("index", lambda: CodeIndex.for_workspace(self.settings, self.llm))


BUILTIN_MODULES = ("web_search", "web_fetch", "filesystem", "git_tools", "sandbox", "rag")


class Toolbox:
    """Exposes tools to agents (OpenAI schemas) and executes calls safely."""

    def __init__(self, settings: Settings, llm: LLM | None = None) -> None:
        self.ctx = ToolContext(settings=settings, llm=llm)
        for module in BUILTIN_MODULES:
            importlib.import_module(f"{__name__}.{module}")
        if settings.tools.custom:
            import os
            import sys

            if os.getcwd() not in sys.path:  # custom modules usually live in the current project
                sys.path.insert(0, os.getcwd())
            for module in settings.tools.custom:
                importlib.import_module(module)

    def available(self) -> dict[str, Tool]:
        return registered_tools()

    def schemas(self, names: list[str] | tuple[str, ...]) -> list[dict[str, Any]]:
        tools = registered_tools()
        return [tools[n].schema() for n in names if n in tools]

    def execute(self, name: str, args: dict[str, Any]) -> str:
        tools = registered_tools()
        if name not in tools:
            return f"ERROR: unknown tool '{name}'"
        try:
            result = tools[name].func(self.ctx, **args)
        except TypeError as exc:
            return f"ERROR: bad arguments for {name}: {exc}"
        except Exception as exc:  # tools must never crash the pipeline
            return f"ERROR: {name} failed: {type(exc).__name__}: {exc}"
        if not isinstance(result, str):
            result = json.dumps(result, ensure_ascii=False, default=str)
        if len(result) > MAX_TOOL_OUTPUT:
            result = result[:MAX_TOOL_OUTPUT] + "\n…[truncated]"
        return result
