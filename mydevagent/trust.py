"""Your OK for the project things that run commands (hooks, MCP servers): asked once per
project, and again if they change. A downloaded repository must not be able to launch programs on its own."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


def _file() -> Path:
    return Path(os.environ.get("MYDEVAGENT_STATE_DIR", Path.home() / ".mydevagent")) / "trusted.json"


def _key(root: Path, kind: str) -> str:
    return f"{Path(root).resolve()}|{kind}"


def _fingerprint(items: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(items)).encode()).hexdigest()


def _load() -> dict:
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def is_trusted(root: Path, kind: str, items: list[str]) -> bool:
    return not items or _load().get(_key(root, kind)) == _fingerprint(items)


def trust(root: Path, kind: str, items: list[str]) -> None:
    data = _load()
    data[_key(root, kind)] = _fingerprint(items)
    _file().parent.mkdir(parents=True, exist_ok=True)
    _file().write_text(json.dumps(data, indent=2), encoding="utf-8")
