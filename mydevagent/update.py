"""Updating MyDevAgent: `git pull` in the folder where it is installed and, only if they changed, the
dependencies. Models, settings (.env), memory and sessions are left untouched: they live elsewhere or are
not in the repository."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

HOME = Path(__file__).resolve().parent.parent  # with a `pip install -e` install, this is the repository folder
EXTRAS = "[server,search]"  # the same as scripts/install.*


@dataclass
class Result:
    ok: bool
    message: str
    changes: list[str] = field(default_factory=list)  # the titles of the new commits
    restart: bool = False  # a restart is needed to use the new version


def _git(*args: str, timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(HOME), *args], capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", errors="replace", env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


def available() -> int:
    """How many new commits are on the server (0 if offline, without git or on error). Runs a `git fetch`."""
    if os.environ.get("MYDEVAGENT_OFFLINE") or not (HOME / ".git").exists():
        return 0
    try:
        if _git("fetch", "--quiet", timeout=20).returncode:
            return 0
        count = _git("rev-list", "--count", "HEAD..@{u}")
    except (OSError, subprocess.TimeoutExpired):
        return 0
    return int(count.stdout.strip()) if count.returncode == 0 and count.stdout.strip().isdigit() else 0


def update() -> Result:
    if not (HOME / ".git").exists():
        return Result(False, f"{HOME} is not a git copy (maybe it is a zip): follow \"Updating\" in the README to "
                             "connect it to GitHub, then /update works")
    try:
        before = _git("rev-parse", "HEAD").stdout.strip()
        deps_before = _digest(HOME / "pyproject.toml")
        pulled = _git("pull", "--ff-only")
    except FileNotFoundError:
        return Result(False, "git must be installed (https://git-scm.com)")
    except subprocess.TimeoutExpired:
        return Result(False, "GitHub is not responding: try again shortly")
    if pulled.returncode:
        lines = (pulled.stderr or pulled.stdout).strip().splitlines()
        reason = next((line for line in lines if line.startswith(("fatal:", "error:"))),
                      lines[0] if lines else "unknown error")
        hint = (" (you have local changes in the MyDevAgent folder: `git stash` sets them aside)"
                if any("local changes" in line or "overwritten" in line for line in lines) else "")
        return Result(False, f"git pull failed: {reason}{hint}")
    after = _git("rev-parse", "HEAD").stdout.strip()
    if after == before:
        return Result(True, "You are already on the latest version.")
    log = _git("log", "--format=%s", f"{before}..{after}").stdout.strip().splitlines()
    message = f"Updated: {len(log)} new commit{'' if len(log) == 1 else 's'}."
    if _digest(HOME / "pyproject.toml") != deps_before:
        pip = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-e", f".{EXTRAS}"], cwd=HOME,
                             capture_output=True, text=True, encoding="utf-8", errors="replace")
        if pip.returncode:  # on Windows this can happen if the program is open
            message += (" The dependencies were not updated: close MyDevAgent and run "
                        f"`{Path(sys.executable).name} -m pip install -e \".{EXTRAS}\"` in the folder {HOME}.")
        else:
            message += " Dependencies updated."
    return Result(True, message, log[:20], restart=True)
