"""Plugins in the Claude Code format: a folder with `.claude-plugin/plugin.json` and, inside, `commands/`
(`/name` commands), `skills/` (skills) and `agents/` (specialized agents, which are subagents in MyDevAgent),
`hooks/` (see hooks.py) and `.mcp.json`.

Where they come from: the project's `.mydevagent/plugins/`, `~/.mydevagent/plugins/` (where
`/plugin install` puts them), the plugins installed in Claude Code (`~/.claude/plugins/installed_plugins.json`)
and the folders in MYDEVAGENT_PLUGINS_DIRS. A marketplace repository (`.claude-plugin/marketplace.json`) can
contain several plugins.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

KINDS = ("commands", "skills", "agents")
PATTERNS = {"commands": "**/*.md", "skills": "*/SKILL.md", "agents": "*.md"}


@dataclass
class Plugin:
    name: str
    path: Path
    source: str  # project · user · claude code · extra
    manifest: dict = field(default_factory=dict)

    @property
    def description(self) -> str:
        return str(self.manifest.get("description") or "")

    @property
    def version(self) -> str:
        return str(self.manifest.get("version") or "")

    def dirs(self, kind: str) -> list[Path]:
        """The standard folder plus those listed in plugin.json (they add up, as in Claude Code)."""
        extra = self.manifest.get(kind) or []
        extra = [extra] if isinstance(extra, str) else extra
        paths = [self.path / kind] + [(self.path / e).resolve() for e in extra if isinstance(e, str)]
        return [p for p in dict.fromkeys(paths) if p.is_dir()]

    def count(self, kind: str) -> int:
        return sum(1 for d in self.dirs(kind) for _ in d.glob(PATTERNS[kind]))

    def features(self) -> list[str]:
        out = []
        if (self.path / "hooks").is_dir() or self.manifest.get("hooks"):
            out.append("hook")
        if (self.path / ".mcp.json").is_file() or self.manifest.get("mcpServers"):
            out.append("MCP")
        return out


def _json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def state_plugins() -> Path:
    return Path(os.environ.get("MYDEVAGENT_STATE_DIR", Path.home() / ".mydevagent")) / "plugins"


def is_plugin(folder: Path) -> bool:
    return (folder / ".claude-plugin" / "plugin.json").is_file() or any((folder / k).is_dir() for k in KINDS)


def find_plugins(folder: Path, depth: int = 1) -> list[Path]:
    """A plugin, a marketplace with its local plugins, or a folder that contains plugins."""
    if not folder.is_dir():
        return []
    if is_plugin(folder):
        return [folder]
    market = _json(folder / ".claude-plugin" / "marketplace.json")
    if market:
        sources = [p.get("source") for p in market.get("plugins", []) if isinstance(p, dict)]
        # ponytail: local sources only ("./plugins/x"); github/url ones are installed separately with /plugin install
        return [p for s in sources if isinstance(s, str) for p in [(folder / s).resolve()] if is_plugin(p)]
    if depth == 0:
        return []
    return [p for d in sorted(folder.iterdir()) if d.is_dir() and not d.name.startswith(".")
            for p in find_plugins(d, depth - 1)]


def claude_code_plugins() -> list[Path]:
    """The enabled plugins installed in Claude Code (v1 and v2 format of installed_plugins.json)."""
    base = Path.home() / ".claude"
    enabled = _json(base / "settings.json").get("enabledPlugins") or {}
    out = []
    for key, entries in (_json(base / "plugins" / "installed_plugins.json").get("plugins") or {}).items():
        if enabled.get(key) is False:
            continue
        for entry in entries if isinstance(entries, list) else [entries]:
            if isinstance(entry, dict) and entry.get("installPath"):
                out.append(Path(entry["installPath"]))
    return out


def load_plugins(root: Path) -> dict[str, Plugin]:
    """name → plugin. On a name clash the first wins: project, user, Claude Code, extra folders."""
    places = [(p, "project") for p in find_plugins(Path(root) / ".mydevagent" / "plugins")]
    places += [(p, "user") for p in find_plugins(state_plugins())]
    places += [(p, "claude code") for p in claude_code_plugins() if p.is_dir()]
    for raw in os.environ.get("MYDEVAGENT_PLUGINS_DIRS", "").split(os.pathsep):
        if raw.strip():
            places += [(p, "extra") for p in find_plugins(Path(raw.strip()).expanduser())]
    found: dict[str, Plugin] = {}
    for path, source in places:
        plugin = _plugin(path, source)
        found.setdefault(plugin.name, plugin)
    return found


def _plugin(path: Path, source: str) -> Plugin:
    manifest = _json(path / ".claude-plugin" / "plugin.json")
    return Plugin(str(manifest.get("name") or path.name), path, source, manifest)


# ------------------------------------------------------------ installation
def install(spec: str) -> list[Plugin]:
    """From a local folder, a git URL or a GitHub `user/repo` (like `/plugin marketplace add`)."""
    base = state_plugins()
    base.mkdir(parents=True, exist_ok=True)
    local = Path(spec).expanduser()
    if local.is_dir():
        dest = base / local.resolve().name
    else:
        url = spec if ("://" in spec or spec.startswith("git@")) else f"https://github.com/{spec.strip('/')}"
        dest = base / url.rstrip("/").removesuffix(".git").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    if dest.exists():
        raise ValueError(f"{dest.name} is already installed (/plugin update {dest.name})")
    if local.is_dir():
        shutil.copytree(local, dest, ignore=shutil.ignore_patterns(".git"))
    else:
        _git(["clone", "--depth", "1", url, str(dest)])
    found = find_plugins(dest)
    if not found:
        remove_folder(dest)
        raise ValueError("no plugin found: it needs .claude-plugin/plugin.json or a "
                         "commands/, skills/ or agents/ folder")
    return [_plugin(p, "user") for p in found]


def folder_of(plugin: Plugin) -> Path | None:
    """The folder downloaded with /plugin install that contains the plugin (a marketplace contains more than one)."""
    base = state_plugins().resolve()
    path = plugin.path.resolve()
    if plugin.source != "user" or not path.is_relative_to(base):
        return None
    return base / path.relative_to(base).parts[0]


def installed_folder(name: str, root: Path) -> Path:
    """By plugin name or by downloaded folder name (for example `claude-code`)."""
    plugin = load_plugins(root).get(name)
    if plugin is None:
        folder = state_plugins() / name
        if Path(name).name != name or not folder.is_dir():
            raise ValueError(f"unknown plugin: {name} (/plugin for the list)")
        return folder.resolve()
    folder = folder_of(plugin)
    if folder is None:
        raise ValueError(f"{name} was not installed with /plugin install (it comes from: {plugin.source})")
    return folder


def update(name: str, root: Path) -> str:
    folder = installed_folder(name, root)
    if not (folder / ".git").exists():
        raise ValueError(f"{name} was copied from a folder: reinstall it to update it")
    return _git(["-C", str(folder), "pull", "--ff-only"]).strip()


def remove(name: str, root: Path) -> Path:
    folder = installed_folder(name, root)
    others = [p.name for p in load_plugins(root).values() if p.name != name and folder_of(p) == folder]
    if others and name != folder.name:
        raise ValueError(f"{name} came with {len(others)} other plugin(s) in the folder {folder.name}: "
                         f"/plugin remove {folder.name} removes them all")
    remove_folder(folder)
    return folder


def remove_folder(folder: Path) -> None:
    def force(func, path, _exc):  # on Windows the .git files are read-only
        os.chmod(path, stat.S_IWRITE)
        func(path)

    shutil.rmtree(folder, **({"onexc": force} if sys.version_info >= (3, 12) else {"onerror": force}))


def _git(args: list[str]) -> str:
    try:
        done = subprocess.run(["git", *args], capture_output=True, text=True, timeout=300,
                              env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})  # never wait for a password
    except FileNotFoundError as exc:
        raise ValueError("git must be installed (https://git-scm.com)") from exc
    if done.returncode:
        out = (done.stderr or done.stdout).strip()
        raise ValueError(out.splitlines()[-1] if out else f"git {args[0]} failed")
    return done.stdout
