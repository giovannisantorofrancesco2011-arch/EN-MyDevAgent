"""/apply: from the code blocks of the last answer to files on disk, with diff and confirmation."""

from __future__ import annotations

import difflib
from collections.abc import Callable
from pathlib import Path

from rich.console import Console
from rich.syntax import Syntax

from ..graph import collect_files
from ..registry import AgentRegistry
from ..tools.filesystem import Workspace, WorkspaceError

Ask = Callable[[str], str]  # takes the question, returns "1" | "2" | "3"


def unified_diff(old: str, new: str, path: str) -> str:
    return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), f"a/{path}", f"b/{path}"))


def apply_answer(answer: str, registry: AgentRegistry, root: Path, console: Console, ask: Ask) -> list[str]:
    """Shows the diff of each file and writes it only after confirmation. Returns the files written."""
    files = {p: c for p, c in collect_files({"formatter": answer}, registry).items()
             if not p.startswith(("main.", "main_", "test_main"))}  # fallback names: no "made-up" files
    if not files:
        console.print("[dim]  ⎿  No files with a path in the last answer (needs ```lang file=path blocks).[/]")
        return []
    workspace = Workspace(root, allow_write=True)
    written: list[str] = []
    approve_all = False
    for path, content in files.items():
        try:
            target = workspace.resolve(path)
        except WorkspaceError as exc:
            console.print(f"[red]⏺ Blocked {path}[/]\n  [dim]⎿  {exc}[/]")
            continue
        if workspace.is_secret(target):
            console.print(f"[red]⏺ Blocked {path}[/]\n  [dim]⎿  sensitive file, not touching it[/]")
            continue
        old = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else ""
        if old == content:
            console.print(f"[dim]⏺ {path}  ⎿  no changes[/]")
            continue
        action = "Update" if target.exists() else "Create"
        added = sum(1 for line in content.splitlines() if line) if not old else None
        console.print(f"\n[bold]⏺ {action}([cyan]{path}[/])[/]")
        if old:
            console.print(Syntax(unified_diff(old, content, path), "diff", theme="ansi_dark", word_wrap=True))
        else:
            console.print(f"  [dim]⎿  new file, {added} lines[/]")
            console.print(Syntax(content, Path(path).suffix.lstrip(".") or "text", theme="ansi_dark",
                                 line_numbers=True, word_wrap=True))
        choice = "1" if approve_all else ask(f"Apply the change to {path}?")
        if choice == "2":
            approve_all, choice = True, "1"
        if choice == "1":
            workspace.write(path, content)
            written.append(path)
            console.print(f"  [green]⎿  wrote {path}[/]")
        else:
            console.print("  [dim]⎿  skipped[/]")
    return written
