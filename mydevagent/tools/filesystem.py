"""Filesystem access confined to a workspace root (no path traversal/symlink escape)."""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path

from . import tool

IGNORED_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next",
                ".mydevagent", ".mypy_cache", ".pytest_cache", ".ruff_cache", "target", ".idea", ".vscode"}
SECRET_FILES = ("*.pem", "*.key", "id_rsa*", "id_ed25519*", ".env", ".env.*", "*.p12", "*.pfx")
MAX_READ_BYTES = 200_000


class WorkspaceError(PermissionError):
    pass


def display_path(root: Path, target: Path) -> str:
    """A path as the agent writes it: relative to the project (even ../other-folder/…), or absolute."""
    try:
        return os.path.relpath(target, root).replace(os.sep, "/")
    except ValueError:  # Windows: a different drive
        return Path(target).as_posix()


class Workspace:
    def __init__(self, root: str | Path, allow_write: bool = False, extra: list[Path] | tuple = ()) -> None:
        self.root = Path(root).expanduser().resolve()
        self.allow_write = allow_write
        self.extra = [Path(p).expanduser().resolve() for p in extra]  # extra folders (/add-dir)

    def resolve(self, path: str | Path) -> Path:
        candidate = (self.root / path).resolve()
        if any(candidate == base or candidate.is_relative_to(base) for base in (self.root, *self.extra)):
            return candidate
        raise WorkspaceError(f"path outside workspace: {path}")

    @staticmethod
    def is_secret(path: Path) -> bool:
        return any(fnmatch.fnmatch(path.name, pattern) for pattern in SECRET_FILES) and path.name != ".env.example"

    def read(self, path: str, max_chars: int = 20_000) -> str:
        target = self.resolve(path)
        if self.is_secret(target):
            raise WorkspaceError(f"refusing to read secret-like file: {path}")
        if not target.is_file():
            raise FileNotFoundError(path)
        data = target.read_bytes()[:MAX_READ_BYTES]
        if b"\0" in data[:4096]:
            return f"[binary file: {path}, {target.stat().st_size} bytes]"
        text = data.decode("utf-8", errors="replace")
        return text if len(text) <= max_chars else text[:max_chars] + "\n…[truncated]"

    def write(self, path: str, content: str) -> str:
        if not self.allow_write:
            raise WorkspaceError("writing is disabled (tools.filesystem.allow_write: false)")
        target = self.resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"wrote {len(content)} chars to {path}"

    def iter_files(self, pattern: str = "*"):
        for path in sorted(self.root.rglob("*")):
            rel = path.relative_to(self.root)
            if any(part in IGNORED_DIRS for part in rel.parts):
                continue
            if path.is_file() and fnmatch.fnmatch(path.name, pattern) and not self.is_secret(path):
                yield rel

    def list(self, path: str = ".", depth: int = 2, limit: int = 300) -> str:
        base = self.resolve(path)
        lines: list[str] = []
        for item in sorted(base.rglob("*")):
            rel = item.relative_to(base)
            if len(rel.parts) > depth or any(part in IGNORED_DIRS for part in rel.parts):
                continue
            lines.append(f"{rel}{'/' if item.is_dir() else ''}")
            if len(lines) >= limit:
                lines.append("…")
                break
        return "\n".join(lines) or "(empty)"

    def grep(self, pattern: str, glob: str = "*", limit: int = 50) -> str:
        regex = re.compile(pattern)
        hits: list[str] = []
        for rel in self.iter_files(glob):
            try:
                text = (self.root / rel).read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    hits.append(f"{rel}:{number}: {line.strip()[:200]}")
                    if len(hits) >= limit:
                        return "\n".join(hits) + "\n…"
        return "\n".join(hits) or "no matches"


@tool("read_file", "Read a text file from the workspace.",
      {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]})
def read_file(ctx, path: str) -> str:
    return ctx.workspace.read(path)


@tool("list_dir", "List files in a workspace directory (default depth 2).",
      {"type": "object", "properties": {"path": {"type": "string"}, "depth": {"type": "integer"}}})
def list_dir(ctx, path: str = ".", depth: int = 2) -> str:
    return ctx.workspace.list(path, depth)


@tool("grep", "Search a regex in workspace files; returns path:line: text.",
      {"type": "object", "properties": {"pattern": {"type": "string"}, "glob": {"type": "string"}},
       "required": ["pattern"]})
def grep(ctx, pattern: str, glob: str = "*") -> str:
    return ctx.workspace.grep(pattern, glob)


@tool("write_file", "Write a file in the workspace (only if enabled in settings).",
      {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
       "required": ["path", "content"]})
def write_file(ctx, path: str, content: str) -> str:
    return ctx.workspace.write(path, content)
