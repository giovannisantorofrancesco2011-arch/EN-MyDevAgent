#!/usr/bin/env python3
"""Builds a fine-tuning dataset from YOUR git repositories.

Generates three kinds of examples (chat JSONL format compatible with TRL/Unsloth):
  1. commit  → "apply this change" : before/after of a file for each small, well-described commit
  2. fim     → fill-in-the-middle (autocomplete) with Qwen2.5-Coder's FIM tokens
  3. extra   → your own question/answer pairs in a JSONL file ({"prompt": ..., "response": ...})

Files with secrets (.env, keys, tokens) and suspicious lines are discarded.

Usage:
  python finetune/build_dataset.py ~/code/project1 ~/code/project2 --out finetune/data
  python finetune/build_dataset.py ~/code/app --extra my_qa.jsonl --max-commits 3000
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import random
import re
import subprocess
from pathlib import Path

SYSTEM = ("You are MyDevAgent, a principal-level software engineer. Write correct, complete, idiomatic code "
          "that follows the conventions of the user's projects.")
CODE_EXT = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".kt", ".cs", ".cpp", ".c", ".h",
            ".swift", ".rb", ".php", ".sql", ".vue", ".svelte", ".sh", ".yaml", ".yml", ".tf", ".scala"}
SKIP_FILES = ["*.lock", "package-lock.json", "*.min.js", "*.map", ".env*", "*.pem", "*.key", "*secret*",
              "*credential*", "*.pb.go", "*_pb2.py", "*.generated.*", "*.snap"]
SECRET_RE = re.compile(
    r"(AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|ghp_[A-Za-z0-9]{30,}|sk-[A-Za-z0-9_-]{20,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}|(?i:(api[_-]?key|secret|password|token)\s*[:=]\s*['\"][^'\"]{8,}['\"]))"
)
FIM_PREFIX, FIM_SUFFIX, FIM_MIDDLE = "<|fim_prefix|>", "<|fim_suffix|>", "<|fim_middle|>"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          errors="replace").stdout


def usable(path: str) -> bool:
    name = Path(path).name
    return Path(path).suffix in CODE_EXT and not any(fnmatch.fnmatch(name, p) for p in SKIP_FILES)


def clean(text: str) -> str | None:
    return None if SECRET_RE.search(text) else text


def lang_of(path: str) -> str:
    return {".py": "python", ".ts": "typescript", ".tsx": "tsx", ".js": "javascript", ".go": "go", ".rs": "rust",
            ".java": "java", ".cs": "csharp", ".sql": "sql", ".sh": "bash"}.get(Path(path).suffix, "")


def commit_examples(repo: Path, max_commits: int, max_chars: int) -> list[dict]:
    examples = []
    log = git(repo, "log", "--no-merges", f"-{max_commits}", "--pretty=format:%H%x1f%s%x1f%b%x1e")
    for record in log.split("\x1e"):
        parts = record.strip().split("\x1f")
        if len(parts) < 2:
            continue
        sha, subject, body = parts[0], parts[1], (parts[2] if len(parts) > 2 else "")
        if len(subject) < 12 or subject.lower().startswith(("merge", "wip", "bump", "update readme")):
            continue
        files = [f for f in git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", sha).split() if usable(f)]
        if not 1 <= len(files) <= 2:
            continue
        for path in files:
            before = git(repo, "show", f"{sha}^:{path}")
            after = git(repo, "show", f"{sha}:{path}")
            if not after or before == after or len(before) + len(after) > max_chars:
                continue
            before, after = clean(before), clean(after)
            if before is None or after is None:
                continue
            task = subject + (f"\n\n{body.strip()}" if body.strip() else "")
            lang = lang_of(path)
            user = (f"Task: {task}\n\nFile `{path}` (current version):\n```{lang}\n{before}\n```"
                    if before else f"Task: {task}\n\nCreate the file `{path}`.")
            examples.append({"messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": user},
                {"role": "assistant", "content": f"```{lang} file={path}\n{after}\n```"},
            ], "source": f"{repo.name}@{sha[:8]}"})
    return examples


def fim_examples(repo: Path, per_repo: int, rng: random.Random) -> list[dict]:
    files = [f for f in git(repo, "ls-files").splitlines() if usable(f)]
    rng.shuffle(files)
    examples = []
    for path in files[: per_repo * 2]:
        try:
            text = (repo / path).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if not 200 < len(text) < 12000 or clean(text) is None:
            continue
        lines = text.splitlines(keepends=True)
        if len(lines) < 10:
            continue
        start = rng.randint(2, len(lines) - 6)
        end = min(len(lines) - 1, start + rng.randint(1, 8))
        prefix, middle, suffix = "".join(lines[:start]), "".join(lines[start:end]), "".join(lines[end:])
        examples.append({"text": f"{FIM_PREFIX}{prefix}{FIM_SUFFIX}{suffix}{FIM_MIDDLE}{middle}<|endoftext|>",
                         "source": f"{repo.name}:{path}"})
        if len(examples) >= per_repo:
            break
    return examples


def extra_examples(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            out.append({"messages": [{"role": "system", "content": SYSTEM},
                                     {"role": "user", "content": row["prompt"]},
                                     {"role": "assistant", "content": row["response"]}], "source": "extra"})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repos", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, default=Path("finetune/data"))
    ap.add_argument("--max-commits", type=int, default=2000)
    ap.add_argument("--max-chars", type=int, default=16000, help="maximum before+after size of a file")
    ap.add_argument("--fim-per-repo", type=int, default=300)
    ap.add_argument("--extra", type=Path, help="JSONL with {prompt, response} pairs")
    ap.add_argument("--val-ratio", type=float, default=0.05)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    chat, fim = [], []
    for repo in args.repos:
        repo = repo.expanduser().resolve()
        if not (repo / ".git").exists():
            print(f"skip (not a git repo): {repo}")
            continue
        c, f = commit_examples(repo, args.max_commits, args.max_chars), fim_examples(repo, args.fim_per_repo, rng)
        print(f"{repo.name}: {len(c)} examples from commits, {len(f)} FIM")
        chat += c
        fim += f
    if args.extra:
        chat += extra_examples(args.extra)

    args.out.mkdir(parents=True, exist_ok=True)
    rng.shuffle(chat)
    n_val = max(1, int(len(chat) * args.val_ratio)) if len(chat) > 20 else 0
    splits = {"train.jsonl": chat[n_val:], "val.jsonl": chat[:n_val], "fim.jsonl": fim}
    for name, rows in splits.items():
        with (args.out / name).open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"→ {args.out / name}: {len(rows)} examples")


if __name__ == "__main__":
    main()
