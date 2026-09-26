"""Single OpenAI-compatible LLM client (Ollama, LM Studio, vLLM, llama.cpp, any /v1)."""

from __future__ import annotations

import json
import re
import time
import zlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol

from .config import Settings

Message = dict[str, Any]


@dataclass
class Completion:
    text: str
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    ms: int = 0
    tool_calls: int = 0
    calls: list[dict[str, Any]] = field(default_factory=list)  # native tool calls: {id, name, arguments}


class LLM(Protocol):
    def complete(
        self,
        messages: list[Message],
        *,
        tier: str = "main",
        max_tokens: int = 1024,
        temperature: float = 0.2,
        tools: list[dict[str, Any]] | None = None,
    ) -> Completion: ...

    def stream(
        self,
        messages: list[Message],
        *,
        tier: str = "main",
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> Iterator[str]: ...

    def complete_with_tools(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]],
        executor: Callable[[str, dict[str, Any]], str],
        tier: str = "main",
        max_tokens: int = 1024,
        temperature: float = 0.2,
        max_steps: int = 4,
    ) -> Completion: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...

    def model_name(self, tier: str) -> str: ...


class OpenAICompatLLM:
    """One client per backend; the model is picked from the active profile's tier."""

    def __init__(self, settings: Settings, timeout: float = 600.0) -> None:
        from openai import OpenAI  # lazy import: speeds up the CLI

        self._openai_cls = OpenAI
        self.settings = settings
        self.timeout = timeout
        self._clients: dict[str, Any] = {}

    def _client_for(self, tier: str) -> tuple[Any, str]:
        model, backend = self.settings.resolve_model(tier)
        client = self._clients.get(backend.base_url)
        if client is None:
            client = self._openai_cls(
                base_url=backend.base_url, api_key=backend.api_key or "none", timeout=self.timeout, max_retries=2
            )
            self._clients[backend.base_url] = client
        return client, model

    def model_name(self, tier: str) -> str:
        return self.settings.resolve_model(tier)[0]

    def complete(self, messages, *, tier="main", max_tokens=1024, temperature=0.2, tools=None) -> Completion:
        client, model = self._client_for(tier)
        start = time.perf_counter()
        kwargs: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": max_tokens,
                                  "temperature": temperature}
        if tools:
            kwargs["tools"] = tools
        resp = client.chat.completions.create(**kwargs)
        usage = getattr(resp, "usage", None)
        msg = resp.choices[0].message
        calls = [{"id": c.id, "name": c.function.name, "arguments": c.function.arguments or "{}"}
                 for c in (getattr(msg, "tool_calls", None) or [])]
        return Completion(
            text=msg.content or "",
            model=model,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            ms=int((time.perf_counter() - start) * 1000),
            calls=calls,
        )

    def stream(self, messages, *, tier="main", max_tokens=1024, temperature=0.2) -> Iterator[str]:
        client, model = self._client_for(tier)
        stream = client.chat.completions.create(
            model=model, messages=messages, max_tokens=max_tokens, temperature=temperature, stream=True
        )
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            # some servers (vLLM, llama.cpp) expose thinking in reasoning_content: we ignore it
            if delta and delta.content:
                yield delta.content

    def complete_with_tools(
        self, messages, *, tools, executor, tier="main", max_tokens=1024, temperature=0.2, max_steps=4
    ) -> Completion:
        """Native function-calling loop (ReAct) with at most `max_steps` tool rounds."""
        client, model = self._client_for(tier)
        convo = list(messages)
        total = Completion(text="", model=model)
        start = time.perf_counter()
        for step in range(max_steps + 1):
            kwargs: dict[str, Any] = {"model": model, "messages": convo, "max_tokens": max_tokens,
                                      "temperature": temperature}
            if step < max_steps:
                kwargs["tools"] = tools
            resp = client.chat.completions.create(**kwargs)
            usage = getattr(resp, "usage", None)
            total.prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
            total.completion_tokens += getattr(usage, "completion_tokens", 0) or 0
            msg = resp.choices[0].message
            calls = getattr(msg, "tool_calls", None) or []
            if not calls:
                total.text = msg.content or ""
                break
            convo.append(
                {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {"id": c.id, "type": "function",
                         "function": {"name": c.function.name, "arguments": c.function.arguments}}
                        for c in calls
                    ],
                }
            )
            for call in calls:
                total.tool_calls += 1
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = executor(call.function.name, args)
                convo.append({"role": "tool", "tool_call_id": call.id, "content": result})
        total.ms = int((time.perf_counter() - start) * 1000)
        return total

    def embed(self, texts: list[str]) -> list[list[float]]:
        client, model = self._client_for("embed")
        resp = client.embeddings.create(model=model, input=texts)
        return [item.embedding for item in resp.data]


# --------------------------------------------------------------------------- fake
@dataclass
class FakeLLM:
    """Deterministic LLM for tests and offline demos (`MYDEVAGENT_FAKE_LLM=1`).

    Recognizes the agent's role from the system prompt and returns plausible output in the expected format.
    """

    responses: dict[str, str] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)
    gate_verdicts: list[str] = field(default_factory=list)

    def model_name(self, tier: str) -> str:
        return f"fake-{tier}"

    def _role(self, messages: list[Message]) -> str:
        system = " ".join(m.get("content", "") for m in messages if m.get("role") == "system")
        match = re.search(r"# Role: ([^\n(]+)", system)
        return match.group(1).strip() if match else "unknown"

    def _answer(self, role: str, messages: list[Message]) -> str:
        for key, value in self.responses.items():
            if key.lower() in role.lower():
                return value
        lowered = role.lower()
        if "architect" in lowered:
            return ("**Goal**: implement the request.\n**Files**: app/main.py — logic\n"
                    "**Assignments**: language: implementation\n**Acceptance criteria**:\n- add(2, 3) == 5")
        if any(k in lowered for k in ("security", "performance", "edge cases", "reviewer")):
            verdict = self.gate_verdicts.pop(0) if self.gate_verdicts else "APPROVE"
            if verdict == "REVISE":
                return "VERDICT: REVISE\n- [BLOCKER] app/main.py: missing input validation → validate types"
            return "VERDICT: APPROVE\n- none"
        if "debugging" in lowered:
            return ("**Test plan**\n- check add\n```python run\nfrom app.main import add\n"
                    "assert add(2, 3) == 5\nprint('SELF-CHECK OK')\n```")
        if "research" in lowered:
            return "**Findings**:\n- fake fact — [1]\n**Sources**: [1] Example — https://example.com"
        if "formatter" in lowered:
            return "**Summary**: done.\n```python file=app/main.py\ndef add(a: int, b: int) -> int:\n    return a + b\n```"
        if "documentation" in lowered:
            return "```markdown file=README.md\n# App\nUsage: `python -m app.main`\n```"
        return "**Approach**\n- simple\n```python file=app/main.py\ndef add(a: int, b: int) -> int:\n    return a + b\n```"

    def complete(self, messages, *, tier="main", max_tokens=1024, temperature=0.2, tools=None) -> Completion:
        role = self._role(messages)
        self.calls.append({"role": role, "tier": tier, "messages": messages})
        text = self._answer(role, messages)
        prompt_tokens = sum(len(str(m.get("content", ""))) for m in messages) // 4
        return Completion(text=text, model=self.model_name(tier), prompt_tokens=prompt_tokens,
                          completion_tokens=len(text) // 4, ms=1)

    def stream(self, messages, *, tier="main", max_tokens=1024, temperature=0.2) -> Iterator[str]:
        text = self.complete(messages, tier=tier, max_tokens=max_tokens, temperature=temperature).text
        for i in range(0, len(text), 16):
            yield text[i : i + 16]

    def complete_with_tools(self, messages, *, tools, executor, tier="main", max_tokens=1024,
                            temperature=0.2, max_steps=4) -> Completion:
        return self.complete(messages, tier=tier, max_tokens=max_tokens, temperature=temperature)

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            vec = [0.0] * 256
            for token in re.findall(r"\w+", text.lower()):
                vec[zlib.crc32(token.encode()) % 256] += 1.0
            vectors.append(vec)
        return vectors


def build_llm(settings: Settings) -> LLM:
    import os

    if os.environ.get("MYDEVAGENT_FAKE_LLM") == "1":
        return FakeLLM()
    return OpenAICompatLLM(settings)
