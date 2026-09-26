"""Project context for the agent: memory (MYDEVAGENT.md) + compact repo map (an idea from Aider)."""

from __future__ import annotations

import ast
import json
import os
import re
from pathlib import Path

from ..tools.filesystem import IGNORED_DIRS, Workspace, display_path

MEMORY_FILES = ("MYDEVAGENT.md", "AGENTS.md", "CLAUDE.md")
MAX_MEMORY_CHARS = 6000
MAX_MAP_CHARS = 7000
CODE_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".go", ".rs", ".java", ".kt", ".cs", ".rb", ".php",
            ".swift", ".scala", ".c", ".h", ".cpp", ".hpp", ".vue", ".svelte"}
OTHER_EXT = {".md", ".toml", ".json", ".yaml", ".yml", ".sql", ".sh", ".tf", ".html", ".css", ".prisma"}
MAX_FILE_BYTES = 400_000

SYMBOL_PATTERNS = {
    "js": [r"^\s*export\s+(?:default\s+)?(?:async\s+)?function\s*\*?\s*(\w+\s*\([^)]*\))",
           r"^\s*(?:async\s+)?function\s*\*?\s*(\w+\s*\([^)]*\))",
           r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+(\w+[^{]*)",
           r"^\s*(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*(?:async\s+)?\([^)]*\)\s*(?::[^=]+)?=>",
           r"^\s*(?:export\s+)?(?:interface|type|enum)\s+(\w+)",
           r"^\s{2,4}(?:public\s+|private\s+|protected\s+|static\s+|async\s+)*(\w+\s*\([^)]*\))\s*(?::[^{]+)?\{"],
    "go": [r"^func\s+(\([^)]*\)\s*\w+\s*\([^)]*\)[^{]*)", r"^func\s+(\w+\s*\([^)]*\)[^{]*)",
           r"^type\s+(\w+\s+(?:struct|interface))"],
    "rs": [r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+(\w+[^{;]*)", r"^\s*(?:pub\s+)?(?:struct|enum|trait)\s+(\w+)",
           r"^\s*impl(?:<[^>]*>)?\s+([^{]+)"],
    "java": [r"^\s*(?:public|private|protected)?\s*(?:abstract\s+|final\s+)?(?:class|interface|enum|record)\s+(\w+)",
             r"^\s+(?:public|protected|private)\s+(?:static\s+)?(?:[\w<>\[\],\s]+)\s+(\w+\s*\([^)]*\))"],
    "rb": [r"^\s*(?:class|module)\s+([\w:]+)", r"^\s*def\s+([\w.?!]+(?:\([^)]*\))?)"],
    "php": [r"^\s*(?:abstract\s+|final\s+)?class\s+(\w+)", r"^\s*(?:public|private|protected)?\s*(?:static\s+)?function\s+(\w+\s*\([^)]*\))"],
}
EXT_LANG = {".js": "js", ".jsx": "js", ".ts": "js", ".tsx": "js", ".mjs": "js", ".vue": "js", ".svelte": "js",
            ".go": "go", ".rs": "rs", ".java": "java", ".kt": "java", ".cs": "java", ".scala": "java",
            ".swift": "java", ".rb": "rb", ".php": "php", ".c": "rs", ".h": "rs", ".cpp": "rs", ".hpp": "rs"}


def read_memory(root: Path) -> str:
    """The project's MYDEVAGENT.md / AGENTS.md / CLAUDE.md + global user memory."""
    parts = []
    user = Path(os.environ.get("MYDEVAGENT_STATE_DIR", Path.home() / ".mydevagent")) / "MYDEVAGENT.md"
    for path in [user] + [root / name for name in MEMORY_FILES]:
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace").strip()
            if text:
                parts.append(f"## {path.name}{' (user)' if path == user else ''}\n{text}")
    return "\n\n".join(parts)[:MAX_MEMORY_CHARS]


def collect_attachments(root: Path, text: str, extra_dirs: list[Path] | tuple[Path, ...] = ()) -> dict[str, str]:
    """Files mentioned as @path in the message (only inside the project or the extra folders, no secrets)."""
    files: dict[str, str] = {}
    for token in text.split():
        if not token.startswith("@") or len(token) < 2:
            continue
        candidate = token[1:].rstrip(",.;:")
        try:
            path = (root / candidate).resolve()
        except OSError:
            continue
        inside = any(path.is_relative_to(d) for d in (root, *extra_dirs))
        if path.is_file() and inside and not Workspace.is_secret(path):
            files[candidate] = path.read_text(encoding="utf-8", errors="replace")
    return files


def append_memory(root: Path, note: str) -> Path:
    path = root / "MYDEVAGENT.md"
    existing = path.read_text(encoding="utf-8") if path.is_file() else "# MYDEVAGENT.md\n\n## Notes\n"
    if not existing.endswith("\n"):
        existing += "\n"
    path.write_text(existing + f"- {note.strip()}\n", encoding="utf-8")
    return path


def python_symbols(source: str) -> list[str]:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    out = []

    def sig(fn) -> str:
        args = [a.arg for a in fn.args.posonlyargs + fn.args.args]
        if fn.args.vararg:
            args.append("*" + fn.args.vararg.arg)
        args += [a.arg for a in fn.args.kwonlyargs]
        if fn.args.kwarg:
            args.append("**" + fn.args.kwarg.arg)
        prefix = "async def" if isinstance(fn, ast.AsyncFunctionDef) else "def"
        return f"{prefix} {fn.name}({', '.join(args)})"

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append(sig(node))
        elif isinstance(node, ast.ClassDef):
            out.append(f"class {node.name}")
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                        not item.name.startswith("_") or item.name == "__init__"):
                    out.append("  " + sig(item))
    return out


def regex_symbols(source: str, lang: str) -> list[str]:
    out = []
    patterns = [re.compile(p) for p in SYMBOL_PATTERNS.get(lang, [])]
    for line in source.splitlines():
        for pattern in patterns:
            match = pattern.match(line)
            if match:
                symbol = " ".join(match.group(1).split())[:100]
                if symbol.split("(")[0].strip() not in ("if", "for", "while", "switch", "catch", "return"):
                    out.append(("  " if line[:1].isspace() else "") + symbol)
                break
    return out[:60]


class RepoMap:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.cache_path = self.root / ".mydevagent" / "repomap.json"
        self._cache: dict[str, dict] = {}
        if self.cache_path.is_file():
            try:
                self._cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                self._cache = {}

    def _symbols(self, rel: str, path: Path) -> list[str]:
        stat = path.stat()
        key = f"{stat.st_mtime_ns}:{stat.st_size}"
        cached = self._cache.get(rel)
        if cached and cached.get("key") == key:
            return cached["symbols"]
        symbols: list[str] = []
        if path.suffix in CODE_EXT and stat.st_size < MAX_FILE_BYTES:
            source = path.read_text(encoding="utf-8", errors="replace")
            symbols = python_symbols(source) if path.suffix == ".py" else regex_symbols(source, EXT_LANG.get(path.suffix, ""))
        self._cache[rel] = {"key": key, "symbols": symbols}
        return symbols

    def build(self, query: str = "", max_chars: int = MAX_MAP_CHARS) -> str:
        files = []
        for rel in Workspace(self.root).iter_files():
            if rel.suffix in CODE_EXT or rel.suffix in OTHER_EXT or rel.name in ("Dockerfile", "Makefile"):
                files.append(rel)
            if len(files) >= 3000:
                break
        words = {w.lower() for w in re.findall(r"[A-Za-z_]{3,}", query)}

        def score(rel: Path) -> tuple[int, int, str]:
            text = rel.as_posix().lower()
            hits = sum(1 for w in words if w in text)
            return (-hits, len(rel.parts), text)

        blocks, used = [], 0
        entries = []
        for rel in files:
            try:
                symbols = self._symbols(rel.as_posix(), self.root / rel)
            except OSError:
                continue
            sym_hits = sum(1 for s in symbols for w in words if w in s.lower()) if words else 0
            entries.append((rel, symbols, sym_hits))
        entries.sort(key=lambda e: (-e[2],) + score(e[0]))
        omitted = 0
        for rel, symbols, _ in entries:
            block = rel.as_posix() + ("\n" + "\n".join("  " + s for s in symbols[:25]) if symbols else "")
            if used + len(block) > max_chars:
                omitted += 1
                continue
            blocks.append(block)
            used += len(block) + 1
        self._save()
        tail = f"\n… {omitted} more files (use list_files)" if omitted else ""
        return "\n".join(blocks) + tail

    def _save(self) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(self._cache), encoding="utf-8")
        except OSError:
            pass


def extra_dirs_context(root: Path, dirs: list[Path], limit: int = 60) -> str:
    """The extra folders (/add-dir): where they are, their first files and their memory."""
    blocks = []
    for folder in dirs:
        files: list[str] = []
        for current, subdirs, names in os.walk(folder):
            subdirs[:] = sorted(d for d in subdirs if d not in IGNORED_DIRS and not d.startswith("."))
            base = Path(current).relative_to(folder)
            files += [(base / name).as_posix() for name in sorted(names) if not Workspace.is_secret(Path(name))]
            if len(files) >= limit:
                files = files[:limit] + ["…"]
                break
        memory = "\n".join(p.read_text(encoding="utf-8", errors="replace").strip()
                           for p in (folder / name for name in MEMORY_FILES) if p.is_file())
        label = display_path(root, folder)
        blocks.append(f"## {label}\n" + ("\n".join(files) or "(empty)")
                      + (f"\n### Project memory of {label}\n{memory[:2000]}" if memory else ""))
    if not blocks:
        return ""
    return ("# Additional working directories\nBesides the project you can read, search and edit files in these "
            "folders. Use their paths as written below (e.g. `" + display_path(root, dirs[0]) + "/file`), or "
            "absolute paths with forward slashes.\n\n" + "\n\n".join(blocks))


def project_context(root: Path, query: str = "", map_chars: int = MAX_MAP_CHARS) -> str:
    parts = []
    memory = read_memory(root)
    if memory:
        parts.append(f"# Project memory (follow these instructions)\n{memory}")
    repo_map = RepoMap(root).build(query, map_chars)
    if repo_map.strip():
        parts.append(f"# Repository map (files and main symbols)\n{repo_map}")
    return "\n\n".join(parts)
