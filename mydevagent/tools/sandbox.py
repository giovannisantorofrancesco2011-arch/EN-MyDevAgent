"""Isolated code execution.

`docker` backend (default): no network, read-only filesystem, unprivileged user, CPU/RAM/process
limits, timeout. `local` backend: subprocess with rlimit — NOT a real sandbox, so it can only be
used with `allow_unsafe_local: true`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ..config import SandboxConfig
from . import tool

LANG_ALIASES = {
    "python": "python", "py": "python", "python3": "python",
    "javascript": "javascript", "js": "javascript", "node": "javascript", "mjs": "javascript",
    "bash": "bash", "sh": "bash", "shell": "bash",
}
RUN_FILES = {"python": ("run.py", ["python", "run.py"]),
             "javascript": ("run.mjs", ["node", "run.mjs"]),
             "bash": ("run.sh", ["sh", "run.sh"])}
MAX_OUTPUT = 4000


@dataclass
class RunResult:
    ok: bool
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    backend: str = ""
    skipped: str = ""

    def summary(self) -> str:
        if self.skipped:
            return f"NOT RUN: {self.skipped}"
        status = "TIMEOUT" if self.timed_out else ("PASS" if self.ok else f"FAIL (exit {self.exit_code})")
        parts = [f"{status} [{self.backend}]"]
        if self.stdout.strip():
            parts.append("stdout:\n" + self.stdout.strip()[-MAX_OUTPUT:])
        if self.stderr.strip():
            parts.append("stderr:\n" + self.stderr.strip()[-MAX_OUTPUT:])
        return "\n".join(parts)


def _safe_relpath(path: str) -> PurePosixPath | None:
    rel = PurePosixPath(path.replace("\\", "/").lstrip("/"))
    if not rel.parts or ".." in rel.parts:
        return None
    return rel


class Sandbox:
    def __init__(self, cfg: SandboxConfig) -> None:
        self.cfg = cfg
        self._docker_ok: bool | None = None

    def docker_available(self) -> bool:
        """Binary present AND daemon reachable (checked only once)."""
        if self._docker_ok is None:
            if not shutil.which("docker"):
                self._docker_ok = False
            else:
                try:
                    proc = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                                          capture_output=True, text=True, timeout=8)
                    self._docker_ok = proc.returncode == 0
                except (subprocess.TimeoutExpired, OSError):
                    self._docker_ok = False
        return self._docker_ok

    def backend(self) -> str | None:
        if not self.cfg.enabled:
            return None
        if self.cfg.backend == "docker" and self.docker_available():
            return "docker"
        if self.cfg.backend == "local" and self.cfg.allow_unsafe_local:
            return "local"
        return None

    def run(self, code: str, lang: str = "python", files: dict[str, str] | None = None) -> RunResult:
        lang = LANG_ALIASES.get(lang.lower(), "")
        if not lang:
            return RunResult(False, None, "", "", skipped="unsupported language (python, javascript, bash)")
        backend = self.backend()
        if backend is None:
            return RunResult(False, None, "", "", skipped="sandbox disabled or docker daemon not available")
        with tempfile.TemporaryDirectory(prefix="mydevagent-") as tmp:
            workdir = Path(tmp)
            for path, content in (files or {}).items():
                rel = _safe_relpath(path)
                if rel is None:
                    continue
                target = workdir / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            # make generated Python packages importable (app/ → app/__init__.py)
            for directory in {p.parent for p in workdir.rglob("*.py")}:
                if directory != workdir and not (directory / "__init__.py").exists():
                    (directory / "__init__.py").write_text("", encoding="utf-8")
            filename, command = RUN_FILES[lang]
            (workdir / filename).write_text(code, encoding="utf-8")
            os.chmod(workdir, 0o755)
            for p in workdir.rglob("*"):
                os.chmod(p, 0o755 if p.is_dir() else 0o644)
            if backend == "docker":
                return self._run_docker(workdir, lang, command)
            return self._run_local(workdir, lang, command)

    def ensure_image(self, image: str) -> bool:
        """Pull the image once, outside the execution timeout."""
        if subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode == 0:
            return True
        try:
            return subprocess.run(["docker", "pull", "-q", image], capture_output=True, timeout=600).returncode == 0
        except subprocess.TimeoutExpired:
            return False

    def _run_docker(self, workdir: Path, lang: str, command: list[str]) -> RunResult:
        if not self.ensure_image(self.cfg.images[lang]):
            return RunResult(False, None, "", "", backend="docker",
                             skipped=f"cannot pull image {self.cfg.images[lang]}")
        name = f"mydevagent-sbx-{uuid.uuid4().hex[:12]}"
        docker_cmd = [
            "docker", "run", "--rm", "--name", name,
            "--network", "none",
            "--memory", self.cfg.memory, "--memory-swap", self.cfg.memory,
            "--cpus", str(self.cfg.cpus),
            "--pids-limit", "128",
            "--read-only", "--tmpfs", "/tmp:rw,size=64m",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--user", "65534:65534", "--pull", "never",
            "-e", "HOME=/tmp", "-e", "PYTHONDONTWRITEBYTECODE=1",
            "-v", f"{workdir}:/work:ro", "-w", "/work",
            self.cfg.images[lang], *command,
        ]
        try:
            proc = subprocess.run(docker_cmd, capture_output=True, text=True, timeout=self.cfg.timeout_s)
        except subprocess.TimeoutExpired as exc:
            subprocess.run(["docker", "kill", name], capture_output=True)
            return RunResult(False, None, _text(exc.stdout), _text(exc.stderr), timed_out=True, backend="docker")
        if proc.returncode == 125:  # an error from docker itself (image, daemon), not from the code
            return RunResult(False, 125, "", "", backend="docker",
                             skipped="docker error: " + proc.stderr.strip()[-300:])
        return RunResult(proc.returncode == 0, proc.returncode, proc.stdout[-MAX_OUTPUT:],
                         proc.stderr[-MAX_OUTPUT:], backend="docker")

    def _run_local(self, workdir: Path, lang: str, command: list[str]) -> RunResult:
        if lang == "python":
            command = [sys.executable, *command[1:]]

        def limits() -> None:  # POSIX only
            import resource

            if lang == "python":  # V8 (node) reserves lots of virtual memory: no RLIMIT_AS
                mem = 512 * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
            cpu = int(self.cfg.timeout_s) + 1
            resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))

        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(workdir), "PYTHONDONTWRITEBYTECODE": "1"}
        try:
            proc = subprocess.run(command, cwd=workdir, capture_output=True, text=True, env=env,
                                  timeout=self.cfg.timeout_s,
                                  preexec_fn=limits if os.name == "posix" else None)
        except subprocess.TimeoutExpired as exc:
            return RunResult(False, None, _text(exc.stdout), _text(exc.stderr), timed_out=True, backend="local")
        return RunResult(proc.returncode == 0, proc.returncode, proc.stdout[-MAX_OUTPUT:],
                         proc.stderr[-MAX_OUTPUT:], backend="local")


def _text(value) -> str:
    if value is None:
        return ""
    return value.decode(errors="replace") if isinstance(value, bytes) else value


@tool(
    "run_code",
    "Run a short self-contained script in an isolated sandbox (no network, no third-party packages). "
    "Returns PASS/FAIL with stdout/stderr.",
    {"type": "object",
     "properties": {"code": {"type": "string"},
                    "language": {"type": "string", "enum": ["python", "javascript", "bash"]}},
     "required": ["code"]},
)
def run_code(ctx, code: str, language: str = "python") -> str:
    return ctx.sandbox.run(code, language).summary()
