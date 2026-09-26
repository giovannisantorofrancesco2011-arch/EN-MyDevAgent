"""Local RAG index of the codebase: embeddings via /v1/embeddings (e.g. nomic-embed-text on Ollama).

Pure-Python store (JSON) → zero extra dependencies. If embeddings are not available a lexical
fallback (simplified BM25) is used, so RAG always works, even without an embedding model.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from . import tool
from .filesystem import Workspace

CODE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".go", ".rs", ".java", ".kt", ".cs", ".cpp", ".cc", ".c",
    ".h", ".hpp", ".swift", ".rb", ".php", ".scala", ".sql", ".sh", ".vue", ".svelte", ".css", ".scss",
    ".html", ".md", ".yaml", ".yml", ".toml", ".json", ".tf", ".dockerfile", ".gradle", ".prisma",
}
MAX_FILE_BYTES = 300_000
TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}")


@dataclass
class Chunk:
    path: str
    start: int
    end: int
    text: str

    def header(self) -> str:
        return f"{self.path}:{self.start}-{self.end}"


def _tokens(text: str) -> list[str]:
    out: list[str] = []
    for tok in TOKEN_RE.findall(text):
        low = tok.lower()
        out.append(low)
        # split camelCase and snake_case to improve matching
        parts = re.findall(r"[a-z]+|[A-Z][a-z]*|\d+", tok)
        if len(parts) > 1:
            out.extend(p.lower() for p in parts if len(p) > 1)
    return out


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


class CodeIndex:
    def __init__(self, workspace: Workspace, index_dir: Path, llm=None, chunk_lines: int = 60,
                 overlap: int = 10) -> None:
        self.workspace = workspace
        self.path = index_dir / "index.json"
        self.llm = llm
        self.chunk_lines = chunk_lines
        self.overlap = overlap
        self.chunks: list[Chunk] = []
        self.vectors: list[list[float]] = []
        self._loaded = False

    @classmethod
    def for_workspace(cls, settings, llm=None) -> CodeIndex:
        ws = Workspace(settings.tools.filesystem.root)
        rag = settings.tools.rag
        return cls(ws, ws.root / rag.index_dir, llm, rag.chunk_lines, rag.chunk_overlap)

    # ------------------------------------------------------------------ build
    def _chunk_file(self, rel: Path) -> list[Chunk]:
        full = self.workspace.root / rel
        if full.stat().st_size > MAX_FILE_BYTES:
            return []
        try:
            lines = full.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            return []
        chunks = []
        step = max(1, self.chunk_lines - self.overlap)
        for start in range(0, max(len(lines), 1), step):
            window = lines[start : start + self.chunk_lines]
            if not "".join(window).strip():
                continue
            chunks.append(Chunk(str(rel), start + 1, start + len(window), "\n".join(window)))
            if start + self.chunk_lines >= len(lines):
                break
        return chunks

    def build(self, use_embeddings: bool = True, batch: int = 32) -> dict[str, int]:
        self.chunks = []
        for rel in self.workspace.iter_files():
            name = rel.name.lower()
            if rel.suffix.lower() in CODE_EXTENSIONS or name in ("dockerfile", "makefile"):
                self.chunks.extend(self._chunk_file(rel))
        self.vectors = []
        if use_embeddings and self.llm is not None:
            try:
                for i in range(0, len(self.chunks), batch):
                    texts = [f"{c.header()}\n{c.text}" for c in self.chunks[i : i + batch]]
                    self.vectors.extend(self.llm.embed(texts))
            except Exception:
                self.vectors = []  # lexical fallback
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"chunks": [asdict(c) for c in self.chunks], "vectors": self.vectors}),
                             encoding="utf-8")
        self._loaded = True
        return {"chunks": len(self.chunks), "embedded": len(self.vectors)}

    # ------------------------------------------------------------------ query
    def load(self) -> bool:
        if self._loaded:
            return bool(self.chunks)
        self._loaded = True
        if not self.path.is_file():
            return False
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.chunks = [Chunk(**c) for c in data.get("chunks", [])]
        self.vectors = data.get("vectors", [])
        return bool(self.chunks)

    def exists(self) -> bool:
        return self.path.is_file()

    def search(self, query: str, k: int = 6) -> list[tuple[float, Chunk]]:
        if not self.load():
            return []
        if self.vectors and len(self.vectors) == len(self.chunks) and self.llm is not None:
            try:
                qvec = self.llm.embed([query])[0]
                scored = [(_cosine(qvec, v), c) for v, c in zip(self.vectors, self.chunks, strict=False)]
                return sorted(scored, key=lambda x: x[0], reverse=True)[:k]
            except Exception:
                pass
        return self._lexical(query, k)

    def _lexical(self, query: str, k: int) -> list[tuple[float, Chunk]]:
        q = Counter(_tokens(query))
        if not q:
            return []
        docs = [Counter(_tokens(c.header() + " " + c.text)) for c in self.chunks]
        n = len(docs)
        avg = sum(sum(d.values()) for d in docs) / max(n, 1)
        df = Counter(t for d in docs for t in set(d) if t in q)
        scored = []
        for doc, chunk in zip(docs, self.chunks, strict=False):
            length = sum(doc.values()) or 1
            score = 0.0
            for term in q:
                if term not in doc:
                    continue
                idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
                tf = doc[term]
                score += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * length / avg))
            if score > 0:
                scored.append((score, chunk))
        return sorted(scored, key=lambda x: x[0], reverse=True)[:k]

    def context(self, query: str, k: int = 6, max_chars: int = 6000) -> str:
        parts, used = [], 0
        for _, chunk in self.search(query, k):
            block = f"--- {chunk.header()}\n{chunk.text}"
            if used + len(block) > max_chars:
                break
            parts.append(block)
            used += len(block)
        return "\n".join(parts)


@tool("rag_search", "Semantic search over the indexed local codebase; returns relevant code chunks with path:lines.",
      {"type": "object", "properties": {"query": {"type": "string"}, "k": {"type": "integer"}},
       "required": ["query"]})
def rag_search(ctx, query: str, k: int = 6) -> str:
    index = ctx.index
    if not index.exists():
        return "NO INDEX: run `mydevagent index` in the project root."
    return index.context(query, k) or "no relevant code found"
