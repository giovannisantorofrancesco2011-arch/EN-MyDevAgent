"""Step-by-step reasoning: per-mode scaffolds, <think> block handling and parsing."""

from __future__ import annotations

import re
from dataclasses import dataclass

THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)
CODE_BLOCK_RE = re.compile(r"```([^\n`]*)\n(.*?)(?:```|\Z)", re.DOTALL)  # \Z: truncated block
ISSUE_RE = re.compile(r"^\s*[-*]\s*\[(BLOCKER|MAJOR|MINOR)\]\s*(.+)$", re.IGNORECASE | re.MULTILINE)
VERDICT_RE = re.compile(r"VERDICT\s*:\s*(APPROVE|REVISE)", re.IGNORECASE)

SCAFFOLD_FAST = (
    "Reasoning: think briefly and silently (plan → solve → quick check), then answer directly."
)
SCAFFOLD_DEEP = (
    "Reasoning: before answering, reason step by step privately: 1) restate the exact goal and constraints, "
    "2) consider 2 approaches and pick the best with a one-line justification, 3) solve, 4) verify the result "
    "against every requirement and at least two edge cases, fixing anything wrong. Then output only the final "
    "result in the required format."
)


def effort_directive(model: str, think: bool) -> str:
    """Directives for model families whose reasoning can be toggled.

    - Qwen3 (non-coder): `/think` and `/no_think` turn thinking on/off.
    - gpt-oss: "Reasoning: low|high" in the system prompt.
    """
    name = model.lower()
    if name.startswith("qwen3") and "coder" not in name:
        return "/think" if think else "/no_think"
    if "gpt-oss" in name:
        return "Reasoning: high" if think else "Reasoning: low"
    return ""


def scaffold(think: bool) -> str:
    return SCAFFOLD_DEEP if think else SCAFFOLD_FAST


def strip_thinking(text: str) -> str:
    """Removes <think>…</think> blocks (even unclosed ones) from the output."""
    text = THINK_RE.sub("", text)
    lower = text.lower()
    if "<think>" in lower:  # unclosed block (truncated output): drop everything from the tag on
        text = text[: lower.index("<think>")]
    if "</think>" in text.lower():  # some templates omit the opening tag
        text = text[text.lower().rindex("</think>") + len("</think>") :]
    return text.strip()


class ThinkFilter:
    """Streaming filter that hides the content between <think> and </think>."""

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self, show: bool = False) -> None:
        self.show = show
        self.inside = False
        self.buffer = ""

    def feed(self, chunk: str) -> str:
        if self.show:
            return chunk
        self.buffer += chunk
        out: list[str] = []
        while self.buffer:
            tag = self.CLOSE if self.inside else self.OPEN
            idx = self.buffer.lower().find(tag)
            if idx >= 0:
                if not self.inside:
                    out.append(self.buffer[:idx])
                self.buffer = self.buffer[idx + len(tag) :]
                self.inside = not self.inside
                if not self.inside:
                    self.buffer = self.buffer.lstrip()
                continue
            # keep in the buffer a tag that may be split across two chunks
            keep = _partial_suffix(self.buffer.lower(), tag)
            emit, self.buffer = self.buffer[: len(self.buffer) - keep], self.buffer[len(self.buffer) - keep :]
            if not self.inside:
                out.append(emit)
            break
        return "".join(out)

    def flush(self) -> str:
        rest, self.buffer = self.buffer, ""
        return "" if self.inside else rest


def _partial_suffix(text: str, tag: str) -> int:
    for size in range(min(len(tag) - 1, len(text)), 0, -1):
        if tag.startswith(text[-size:]):
            return size
    return 0


PATH_TOKEN_RE = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)*[\w.-]+\.(?:py|ts|tsx|js|jsx|mjs|go|rs|java|kt|cs|cpp|c|h|"
                           r"rb|php|sql|sh|yaml|yml|toml|json|vue|svelte|html|css|md|tf|swift|scala|prisma))\b")
FIRST_LINE_PATH_RE = re.compile(r"^\s*(?:#|//|--|/\*|<!--)\s*(?:file(?:name)?\s*[:=]\s*)?(\S+\.\w{1,6})\s*(?:\*/|-->)?\s*$",
                                re.IGNORECASE)
NON_FILE_LANGS = {"", "bash", "sh", "shell", "console", "text", "txt", "plaintext", "diff", "output", "log"}


@dataclass
class CodeBlock:
    lang: str
    info: str
    body: str
    before: str = ""

    @property
    def is_run(self) -> bool:
        return bool(re.search(r"(^|\s)run(\s|$)", self.info))

    @property
    def path(self) -> str | None:
        """File path: info string → first-line comment → path mentioned right before the block."""
        match = re.search(r"(?:file|path|title)\s*=\s*[\"']?([^\s\"']+)", self.info)
        if match:
            return match.group(1)
        if self.is_run or self.lang in NON_FILE_LANGS:
            return None
        first = self.body.split("\n", 1)[0]
        match = FIRST_LINE_PATH_RE.match(first)
        if match:
            return match.group(1).removeprefix("./")
        tail = self.before.strip().splitlines()[-1:] if self.before.strip() else []
        if tail:
            candidates = PATH_TOKEN_RE.findall(tail[0])
            if candidates:
                return candidates[-1].removeprefix("./")
        return None


def _split_multi_file(block: CodeBlock) -> list[CodeBlock]:
    """Splits a block holding several files marked by comments (`# app/a.py` … `# tests/test_a.py`)."""
    if re.search(r"(?:file|path|title)\s*=", block.info) or block.is_run:
        return [block]
    lines = block.body.split("\n")
    starts = [i for i, line in enumerate(lines) if FIRST_LINE_PATH_RE.match(line)
              and (i == 0 or not lines[i - 1].strip())]
    if len(starts) < 2:
        return [block]
    parts = []
    if starts[0] > 0 and "\n".join(lines[: starts[0]]).strip():
        parts.append(CodeBlock(block.lang, block.info, "\n".join(lines[: starts[0]]) + "\n", block.before))
    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(lines)
        body = "\n".join(lines[start:end]).rstrip() + "\n"
        path = FIRST_LINE_PATH_RE.match(lines[start]).group(1).removeprefix("./")
        parts.append(CodeBlock(block.lang, f"{block.info} file={path}".strip(), body, block.before))
    return parts


def extract_code_blocks(text: str) -> list[CodeBlock]:
    blocks = []
    for match in CODE_BLOCK_RE.finditer(text):
        info = match.group(1).strip()
        lang = info.split()[0].lower() if info else ""
        before = text[max(0, match.start() - 300) : match.start()]
        blocks.extend(_split_multi_file(CodeBlock(lang=lang, info=info, body=match.group(2), before=before)))
    return blocks


@dataclass
class Review:
    verdict: str
    issues: list[tuple[str, str]]

    @property
    def blocking(self) -> list[tuple[str, str]]:
        return [(sev, text) for sev, text in self.issues if sev in ("BLOCKER", "MAJOR")]


def parse_review(text: str) -> Review:
    """Parses the quality gates' output (VERDICT + list of issues with severity)."""
    text = strip_thinking(text)
    issues = [(sev.upper(), body.strip()) for sev, body in ISSUE_RE.findall(text)]
    match = VERDICT_RE.search(text)
    if match:
        verdict = match.group(1).upper()
    else:
        verdict = "REVISE" if any(sev == "BLOCKER" for sev, _ in issues) else "APPROVE"
    # An APPROVE with a BLOCKER is inconsistent: severity wins.
    if any(sev == "BLOCKER" for sev, _ in issues):
        verdict = "REVISE"
    return Review(verdict=verdict, issues=issues)
