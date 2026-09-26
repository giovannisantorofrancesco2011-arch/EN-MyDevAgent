"""Ready-made projects for /new: each folder next to this file is a template, copied as is.

Files that start with a dot (.gitignore, .env.example) are stored here without the dot, so they also end up
in the pip package: /new renames them."""

from __future__ import annotations

import contextlib
import re
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATES = {
    "website": "website with HTML, CSS and JavaScript (nothing to install)",
    "game": "2D game with Pygame: catch the stars",
    "bot-discord": "Discord bot with the !hello and !dice commands",
    "api": "web API with FastAPI, with tests",
    "python": "Python program with tests, to get started",
}
DOTFILES = {"gitignore": ".gitignore", "env.example": ".env.example"}


def _is_empty(folder: Path) -> bool:
    return not any(not p.name.startswith(".") for p in folder.iterdir())


def target_for(root: Path, template: str, name: str = "") -> Path:
    """Where to create the project: in the open folder if it is empty, otherwise in a new subfolder.
    Never inside the MyDevAgent folder: there the project goes next to it."""
    from ..update import HOME

    if name and not re.fullmatch(r"[\w][\w.-]*", name):
        raise ValueError(f"invalid name: {name} (use letters, digits, - and _)")
    root = root.resolve()
    if root == HOME:
        root = root.parent
    elif not name and _is_empty(root):
        return root
    if name:
        dest = root / name
        if dest.exists() and not _is_empty(dest):
            raise ValueError(f"the folder {dest} already exists and is not empty: pick another name")
        return dest
    dest, n = root / template, 2
    while dest.exists() and not _is_empty(dest):
        dest, n = root / f"{template}-{n}", n + 1
    return dest


def create(template: str, dest: Path) -> list[str]:
    """Copies the template into `dest` (and runs `git init` if git is available). Returns the files created."""
    source = HERE / template
    if template not in TEMPLATES or not source.is_dir():
        raise ValueError(f"unknown template: {template}")
    dest.mkdir(parents=True, exist_ok=True)
    created = []
    for path in sorted(source.rglob("*")):
        if path.is_dir() or "__pycache__" in path.parts:
            continue
        rel = path.relative_to(source)
        rel = rel.with_name(DOTFILES.get(rel.name, rel.name))
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest / rel)
        created.append(rel.as_posix())
    if not (dest / ".git").exists():
        with contextlib.suppress(OSError, subprocess.TimeoutExpired):  # works without git too
            subprocess.run(["git", "init", "-q"], cwd=dest, capture_output=True, timeout=20)
    return created
