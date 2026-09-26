"""Editor "Tab" completion (fill-in-the-middle): the model writes what is missing at the cursor.

The prompt uses the model family's FIM tokens in raw mode: `/api/generate` with Ollama, `/v1/completions`
with other OpenAI-compatible servers. "Base" models work best (e.g. qwen2.5-coder:1.5b-base),
but instruct models of the same family work too.
"""

from __future__ import annotations

import re

import httpx

from .config import Settings
from .health import is_ollama, ollama_host

QWEN = ("<|fim_prefix|>", "<|fim_suffix|>", "<|fim_middle|>",
        ["<|endoftext|>", "<|fim_pad|>", "<|fim_prefix|>", "<|fim_suffix|>", "<|fim_middle|>", "<|file_sep|>",
         "<|repo_name|>", "<|im_start|>", "<|im_end|>"])
TEMPLATES = [  # (families, before cursor, after cursor, where to write, stop)
    (("deepseek",), "<｜fim▁begin｜>", "<｜fim▁hole｜>", "<｜fim▁end｜>", ["<｜end▁of▁sentence｜>", "<｜fim▁begin｜>"]),
    (("codellama",), "<PRE> ", " <SUF>", " <MID>", ["<EOT>", "<PRE>", "<SUF>", "<MID>"]),
    (("starcoder", "stable-code"), "<fim_prefix>", "<fim_suffix>", "<fim_middle>",
     ["<|endoftext|>", "<file_sep>", "<fim_prefix>", "<fim_suffix>", "<fim_middle>"]),
    (("codegemma",), "<|fim_prefix|>", "<|fim_suffix|>", "<|fim_middle|>",
     ["<|file_separator|>", "<end_of_turn>", "<eos>", "<|fim_prefix|>"]),
]
CLOSERS = re.compile(r"[)\]}>'\"`;,:]+")
MAX_PREFIX = 4000
MAX_SUFFIX = 1500


def template(model: str) -> tuple[str, str, str, list[str]]:
    name = model.lower()
    for families, *rest in TEMPLATES:
        if any(f in name for f in families):
            return rest[0], rest[1], rest[2], list(rest[3])
    return QWEN[0], QWEN[1], QWEN[2], list(QWEN[3])  # qwen2.5-coder, qwen3-coder and unknown models


def build_prompt(model: str, prefix: str, suffix: str) -> tuple[str, list[str]]:
    before, after, middle, stop = template(model)
    return f"{before}{prefix[-MAX_PREFIX:]}{after}{suffix[:MAX_SUFFIX]}{middle}", stop


def clean(text: str, suffix: str, stop: list[str]) -> str:
    """Trims leaked special tokens, trailing whitespace and text repeating what is already after the cursor."""
    for token in stop:
        if token in text:
            text = text[: text.index(token)]
    text = text.rstrip()
    ahead = suffix.split("\n", 1)[0].strip()
    if ahead and text.endswith(ahead):  # it rewrote the rest of the line
        return text[: -len(ahead)].rstrip()
    if CLOSERS.fullmatch(ahead):  # e.g. closes a ")" that is already after the cursor
        for size in range(min(len(ahead), len(text)), 0, -1):
            if text.endswith(ahead[:size]):
                return text[:-size]
    return text


def complete(settings: Settings, prefix: str, suffix: str, *, model: str | None = None, max_tokens: int = 64,
             multiline: bool = True, timeout: float = 15.0, client: httpx.Client | None = None) -> str:
    fast, backend = settings.resolve_model("fast")
    model = model or fast
    prompt, stop = build_prompt(model, prefix, suffix)
    if not multiline:
        stop = [*stop, "\n"]
    http = client or httpx.Client(timeout=timeout)
    try:
        if is_ollama(backend.base_url):
            resp = http.post(ollama_host(backend.base_url) + "/api/generate", json={
                "model": model, "prompt": prompt, "raw": True, "stream": False, "keep_alive": "30m",
                "options": {"num_predict": max_tokens, "temperature": 0.1, "top_p": 0.9, "stop": stop}})
            resp.raise_for_status()
            text = resp.json().get("response", "")
        else:
            resp = http.post(backend.base_url.rstrip("/") + "/completions",
                             headers={"Authorization": f"Bearer {backend.api_key}"},
                             json={"model": model, "prompt": prompt, "max_tokens": max_tokens, "temperature": 0.1,
                                   "stop": stop[:4]})  # OpenAI APIs accept at most 4 stop sequences
            resp.raise_for_status()
            text = resp.json()["choices"][0].get("text", "")
    finally:
        if client is None:
            http.close()
    return clean(text, suffix, stop)
