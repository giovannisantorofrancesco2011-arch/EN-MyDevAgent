"""Autocompletion: /commands, @agents, @project files."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from prompt_toolkit.completion import Completer, Completion

from ..tools.filesystem import Workspace

MAX_FILES = 5000


class DevCompleter(Completer):
    def __init__(self, commands: dict[str, str], aliases: dict[str, str], root: Path) -> None:
        self.commands = commands
        self.aliases = aliases  # alias → agent key
        self.root = root
        self._files: list[str] | None = None
        self.arguments: dict[str, Callable[[], list[str]]] = {}  # /command → names to complete (from the UI)

    @property
    def files(self) -> list[str]:
        if self._files is None:  # lazy scan: startup stays instant even on large repos
            files = []
            for rel in Workspace(self.root).iter_files():
                files.append(rel.as_posix())
                if len(files) >= MAX_FILES:
                    break
            self._files = files
        return self._files

    def refresh(self) -> None:
        self._files = None

    def get_completions(self, document, complete_event):
        word = document.get_word_before_cursor(WORD=True)
        before = document.text_before_cursor
        parts = before.split(" ")
        if len(parts) == 2 and parts[0].lower() in self.arguments:
            for name in self.arguments[parts[0].lower()]():
                if name.startswith(parts[1].lower()):
                    yield Completion(name, -len(parts[1]), display_meta=parts[0].lower()[1:])
        elif word.startswith("/") and before.strip() == word:
            for cmd, desc in self.commands.items():
                if cmd.startswith(word.lower()):
                    yield Completion(cmd, -len(word), display_meta=desc)
        elif word.startswith("@"):
            needle = word[1:].lower()
            for alias in sorted(self.aliases):
                if alias.startswith(needle):
                    yield Completion("@" + alias, -len(word), display_meta=f"agent {self.aliases[alias]}")
            shown = 0
            for path in self.files:
                low = path.lower()
                if low.startswith(needle) or (needle and needle in low):
                    yield Completion("@" + path, -len(word), display_meta="file")
                    shown += 1
                    if shown >= 40:
                        break
