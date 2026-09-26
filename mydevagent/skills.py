"""Skills: reusable instructions in folders, loaded only when needed (like Claude Code skills).

A skill is a folder with a `SKILL.md` (plus optional supporting files: examples, scripts, templates) or
a single `<name>.md` file. At the top of the file, optionally:

    ---
    name: release-notes
    description: Writes release notes from the git log. Use it when the user asks for a changelog.
    ---

The agent only receives the list of names + descriptions; it reads the content with the `skill` tool when a
request matches. Folders read, from the most specific: the project's `.mydevagent/skills` and
`.claude/skills`, `~/.mydevagent/skills`, `~/.claude/skills`, plus those in MYDEVAGENT_SKILLS_DIRS (separated
by `;` on Windows and `:` elsewhere), for example another agent's skills folder. Then the plugins' skills
(see plugins.py).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from .plugins import load_plugins

SKILL_FILES = ("SKILL.md", "skill.md", "Skill.md")
MAX_SKILL_CHARS = 12_000
MAX_DESCRIPTION = 300
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


@dataclass
class Skill:
    name: str
    description: str
    path: Path  # the SKILL.md (or the single .md file)
    source: str  # project · user · extra folder

    @property
    def folder(self) -> Path | None:
        return self.path.parent if self.path.name in SKILL_FILES else None

    def body(self) -> str:
        return split_frontmatter(self.path.read_text(encoding="utf-8", errors="replace"))[1].strip()[:MAX_SKILL_CHARS]

    def files(self) -> list[str]:
        if not self.folder:
            return []
        return sorted(p.relative_to(self.folder).as_posix() for p in self.folder.rglob("*")
                      if p.is_file() and p != self.path and not p.name.startswith("."))[:50]

    def read(self, file: str | None = None) -> str:
        """The skill's content, or one of its supporting files (only inside its folder)."""
        if not file:
            extra = self.files()
            listing = ("\n\nSupporting files (read them with the skill tool and `file`):\n"
                       + "\n".join(f"- {f}" for f in extra)) if extra else ""
            return f"# Skill: {self.name}\n{self.body()}{listing}"
        if not self.folder:
            return f"ERROR: the skill '{self.name}' has no supporting files"
        target = (self.folder / file).resolve()
        if not target.is_relative_to(self.folder.resolve()) or not target.is_file():
            return f"ERROR: file not found in skill '{self.name}': {file}"
        return target.read_text(encoding="utf-8", errors="replace")[:MAX_SKILL_CHARS]


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Simple `key: value` frontmatter → (metadata, body)."""
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    meta = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() and not key.startswith((" ", "\t")):
            meta[key.strip().lower()] = value.strip().strip("\"'")
    return meta, text[match.end():]


def _describe(body: str) -> str:
    for line in body.splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line
    return ""


def skill_dirs(root: Path) -> list[tuple[Path, str]]:
    home = Path.home()
    state = Path(os.environ.get("MYDEVAGENT_STATE_DIR", home / ".mydevagent"))
    dirs = [(root / ".mydevagent" / "skills", "project"), (root / ".claude" / "skills", "project"),
            (state / "skills", "user"), (home / ".claude" / "skills", "user")]
    for plugin in load_plugins(root).values():  # plugins' skills (agents are in subagents.py)
        dirs += [(d, f"plugin {plugin.name}") for d in plugin.dirs("skills")]
    for raw in os.environ.get("MYDEVAGENT_SKILLS_DIRS", "").split(os.pathsep):
        if raw.strip():
            dirs.append((Path(raw.strip()).expanduser(), "extra"))
    return dirs


def load_skills(root: Path) -> dict[str, Skill]:
    """name → skill. On a name clash the most specific folder wins (the project)."""
    found: dict[str, Skill] = {}
    for folder, source in skill_dirs(Path(root)):
        if not folder.is_dir():
            continue
        candidates = [next((d / f for f in SKILL_FILES if (d / f).is_file()), None)
                      for d in sorted(folder.iterdir()) if d.is_dir()]
        candidates += sorted(p for p in folder.glob("*.md") if p.name.lower() not in ("readme.md",))
        for path in candidates:
            if path is None:
                continue
            try:
                meta, body = split_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            default = path.parent.name if path.name in SKILL_FILES else path.stem
            name = re.sub(r"[^\w-]+", "-", meta.get("name") or default).strip("-").lower()
            if name and name not in found:
                description = (meta.get("description") or _describe(body))[:MAX_DESCRIPTION]
                found[name] = Skill(name, description, path, source)
    return found


def skills_prompt(skills: dict[str, Skill]) -> str:
    if not skills:
        return ""
    lines = "\n".join(f"- {s.name}: {short(s.description)}" for s in skills.values())
    return ("# Skills\nThese skills contain expert instructions. When the request matches a skill, FIRST call "
            "the `skill` tool with its name, then follow its instructions.\n" + lines)


def short(text: str, limit: int = 160) -> str:
    """The beginning is enough for the prompt: with many plugins the list stays small even for local models."""
    text = re.split(r"\s*(?:<example>|Examples?:)", text)[0]
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"
