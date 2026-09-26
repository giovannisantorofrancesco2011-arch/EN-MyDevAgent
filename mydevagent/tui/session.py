"""Automatically saved sessions (for /resume and `mydevagent --continue`) and Markdown export."""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def state_dir() -> Path:
    base = Path(os.environ.get("MYDEVAGENT_STATE_DIR", Path.home() / ".mydevagent"))
    base.mkdir(parents=True, exist_ok=True)
    return base


@dataclass
class Session:
    id: str = field(default_factory=lambda: time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6])
    cwd: str = field(default_factory=lambda: str(Path.cwd()))
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    mode: str = "auto"
    history: list[dict[str, Any]] = field(default_factory=list)

    @property
    def path(self) -> Path:
        folder = state_dir() / "sessions"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{self.id}.json"

    @property
    def title(self) -> str:
        first = next((m["content"] for m in self.history if m["role"] == "user"), "(empty)")
        return " ".join(first.split())[:60]

    def add_turn(self, user: str, assistant: str) -> None:
        self.history += [{"role": "user", "content": user}, {"role": "assistant", "content": assistant}]
        self.save()

    def save(self) -> None:
        if not self.history:
            return
        self.updated = time.time()
        self.path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> Session:
        return cls(**json.loads(path.read_text(encoding="utf-8")))

    def to_markdown(self) -> str:
        lines = [f"# MyDevAgent — session {self.id}", "", f"_folder: {self.cwd}_", ""]
        for msg in self.history:
            who = "You" if msg["role"] == "user" else "MyDevAgent"
            lines += [f"## {who}", "", msg["content"].strip(), ""]
        return "\n".join(lines)


def list_sessions(cwd: str | None = None, limit: int = 10) -> list[Session]:
    folder = state_dir() / "sessions"
    if not folder.is_dir():
        return []
    sessions = []
    for path in folder.glob("*.json"):
        try:
            session = Session.load(path)
        except (ValueError, TypeError, OSError):
            continue
        if cwd is None or session.cwd == cwd:
            sessions.append(session)
    return sorted(sessions, key=lambda s: s.updated, reverse=True)[:limit]
