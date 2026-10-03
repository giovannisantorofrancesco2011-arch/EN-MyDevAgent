"""The Plus features: review before commit, guided debugging, tests written by Vio, background jobs and
automatic project memory. They need a Plus code (see license.plus_needed); they work the same way in the
terminal and in Studio.
"""

from __future__ import annotations

import itertools
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .agent import PermissionPolicy
from .agent.context import append_memory, read_memory
from .agent.permissions import ApprovalRequest
from .agent.runner import AgentRunner

COMMANDS = {
    "/review-changes": "Plus · Vio checks your changes before the commit: bugs, unclear parts, passwords in the code",
    "/debug": "Plus · /debug <command>: Vio runs it, finds the cause of the error and fixes it",
    "/test": "Plus · /test <file>: Vio writes the tests for that file and makes them pass",
    "/background": "Plus · /background <task>: Vio works in the background while you go on · alone: the jobs",
}
REVIEW_TASK = (
    "Review the uncommitted changes of this project before the user commits them. Use git_diff (and read_file for "
    "context). Look for: bugs and edge cases, unclear or duplicated code, missing error handling, missing tests, "
    "and secrets (passwords, API keys, tokens) left in the code. Do NOT change any file. Answer with a short list "
    "grouped as 🔴 must fix / 🟡 could improve / 🟢 fine, each item with file:line and a one-line fix. "
    "End with one line: 'You can commit' or 'Fix the red items first'."
)
DEBUG_TASK = (
    "Guided debugging. Run this command: `{command}`. If it fails, read the error and the code it points to, find "
    "the root cause (not just the symptom), fix it with edit_file, and run the command again until it works (max 3 "
    "attempts). Then explain in simple words: what was wrong, where (file:line) and what you changed."
)
TEST_TASK = (
    "Write automated tests for {target}. Use the test framework this project already uses (look for existing tests "
    "and config); if there is none, use the standard one for the language (pytest for Python). Cover the normal "
    "cases, the edge cases and the errors. Put the tests where this project keeps them, run them, and fix the tests "
    "until they pass. If a test reveals a real bug in the code, do not hide it: tell the user."
)
MEMORY_PROMPT = (
    "You maintain a project's memory file. From the exchange below, extract at most 2 durable facts worth remembering "
    "in future sessions: project conventions, decisions, exact commands, user preferences. Skip anything temporary, "
    "obvious or already in the memory. Reply with one fact per line starting with '- ', in the user's language, "
    "or exactly NONE.\n\n# Current memory\n{memory}"
)


def expand(name: str, arg: str) -> str | None:
    """The agent request for /review-changes, /debug and /test (None for the other commands)."""
    if name == "/review-changes":
        return REVIEW_TASK + (f"\nFocus also on: {arg}" if arg else "")
    if name == "/debug":
        if not arg:
            raise ValueError("usage: /debug <command>, for example /debug python main.py")
        return DEBUG_TASK.format(command=arg)
    if name == "/test":
        return TEST_TASK.format(target=f"`{arg}`" if arg else "the files changed most recently (see git_diff)")
    return None


def remember(llm, root: Path, request: str, answer: str) -> list[str]:
    """Automatic memory: saves in MYDEVAGENT.md what is worth remembering from this request."""
    if not answer.strip():
        return []
    reply = llm.complete([{"role": "system", "content": MEMORY_PROMPT.format(memory=read_memory(root)[-4000:])},
                          {"role": "user", "content": f"User: {request[:2000]}\n\nAssistant: {answer[-4000:]}"}],
                         tier="fast", max_tokens=150, temperature=0.1).text.strip()
    facts = [line[2:].strip() for line in reply.splitlines() if line.startswith("- ") and len(line) > 4][:2]
    for fact in facts:
        append_memory(root, fact)
    return facts


@dataclass
class Job:
    id: int
    task: str
    status: str = "running"  # "running", "done", "error"
    answer: str = ""
    started: float = field(default_factory=time.time)


class Background:
    """Background jobs: each one is a separate agent that edits files on its own (like "auto edit") and runs
    the safe commands; it skips the dangerous ones and says so at the end."""

    def __init__(self, orch, root: Path, on_done: Callable[[Job], Any]) -> None:
        self.orch, self.root, self.on_done = orch, Path(root), on_done
        self.jobs: list[Job] = []
        self._ids = itertools.count(1)

    def start(self, task: str) -> Job:
        job = Job(next(self._ids), task)
        self.jobs.append(job)
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    @staticmethod
    def _approve(request: ApprovalRequest) -> tuple[str, str]:
        if request.dangerous:
            return "no", "Running in the background: dangerous commands are not allowed. Tell the user to run it."
        return "yes", ""

    def _run(self, job: Job) -> None:
        # ponytail: if the user edits the same files at the same time, the last write wins
        policy = PermissionPolicy(mode="auto-edit", root=self.root)
        try:
            runner = AgentRunner(self.orch, self.root, policy, approver=self._approve)
            job.answer = "".join(runner.run(job.task))
            job.status = "done"
        except Exception as exc:  # a background job must never crash the app
            job.answer, job.status = str(exc), "error"
        self.on_done(job)
