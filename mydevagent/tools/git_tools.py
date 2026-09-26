"""Git tools: read-only by default; commits only with tools.git.allow_commit: true."""

from __future__ import annotations

import subprocess

from . import tool


def git(root, *args: str, timeout: float = 15) -> str:
    proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=timeout)
    out = (proc.stdout or "") + (proc.stderr if proc.returncode else "")
    return out.strip() or "(no output)"


@tool("git_status", "Show `git status --short --branch` of the workspace.")
def git_status(ctx) -> str:
    return git(ctx.workspace.root, "status", "--short", "--branch")


@tool("git_diff", "Show the git diff (unstaged by default, `staged=true` for staged).",
      {"type": "object", "properties": {"staged": {"type": "boolean"}, "path": {"type": "string"}}})
def git_diff(ctx, staged: bool = False, path: str | None = None) -> str:
    args = ["diff", "--stat", "--patch"] + (["--cached"] if staged else [])
    if path:
        args += ["--", str(ctx.workspace.resolve(path))]
    return git(ctx.workspace.root, *args)


@tool("git_log", "Show recent commits (oneline).",
      {"type": "object", "properties": {"n": {"type": "integer"}, "path": {"type": "string"}}})
def git_log(ctx, n: int = 15, path: str | None = None) -> str:
    args = ["log", f"-{max(1, min(n, 100))}", "--oneline", "--decorate"]
    if path:
        args += ["--", str(ctx.workspace.resolve(path))]
    return git(ctx.workspace.root, *args)


@tool("git_commit", "Stage all changes and commit with a message (disabled unless allowed in settings).",
      {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]})
def git_commit(ctx, message: str) -> str:
    if not ctx.settings.tools.git.allow_commit:
        return "ERROR: commits are disabled (tools.git.allow_commit: false)"
    git(ctx.workspace.root, "add", "-A")
    return git(ctx.workspace.root, "commit", "-m", message)
