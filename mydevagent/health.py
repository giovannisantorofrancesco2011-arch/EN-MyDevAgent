"""Health checks: backends and models, downloads from Ollama, explained errors, hardware and recommended profile.

Used by `mydevagent doctor`, by the UI (startup check, /doctor, /pull) and by the error messages of the
CLI and server: someone opening MyDevAgent for the first time must never see a traceback.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .config import PROJECT_DIR, TIERS, Settings

ESSENTIAL_TIERS = ("main", "fast", "reasoning")  # without these the team can't answer
OPTIONAL_TIERS = ("embed", "vision")  # without them: lexical search instead of embeddings, no images
PROFILE_ORDER = ("cpu", "gpu8", "gpu16", "gpu24")
SIZE_RE = re.compile(r":(\d+(?:\.\d+)?)b\b", re.IGNORECASE)


# --------------------------------------------------------------- backends and models
@dataclass
class TierStatus:
    tier: str
    model: str
    base_url: str
    installed: bool | None  # None = backend not reachable


@dataclass
class Health:
    reachable: dict[str, bool] = field(default_factory=dict)  # base_url → reachable
    installed: dict[str, set[str]] = field(default_factory=dict)  # base_url → models present
    tiers: list[TierStatus] = field(default_factory=list)

    @property
    def down(self) -> list[str]:
        return [url for url, ok in self.reachable.items() if not ok]

    def missing(self, essential_only: bool = False) -> list[TierStatus]:
        wanted = ESSENTIAL_TIERS if essential_only else TIERS
        return [t for t in self.tiers if t.installed is False and t.tier in wanted]

    @property
    def ok(self) -> bool:
        return not self.down and not self.missing(essential_only=True)


def is_installed(model: str, installed: set[str]) -> bool:
    return model in installed or f"{model}:latest" in installed or model.removesuffix(":latest") in installed


def list_installed(base_url: str, api_key: str = "none", timeout: float = 2.0) -> set[str] | None:
    """Models present on the backend (OpenAI API `GET /models`), None if it does not respond."""
    try:
        resp = httpx.get(base_url.rstrip("/") + "/models", headers={"Authorization": f"Bearer {api_key}"},
                         timeout=timeout)
        resp.raise_for_status()
        return {m["id"] for m in resp.json().get("data", [])}
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None


def check_backends(settings: Settings, timeout: float = 2.0) -> Health:
    health = Health()
    for tier in TIERS:
        model, backend = settings.resolve_model(tier)
        url = backend.base_url
        if url not in health.reachable:
            found = list_installed(url, backend.api_key, timeout)
            health.reachable[url] = found is not None
            health.installed[url] = found or set()
        present = is_installed(model, health.installed[url]) if health.reachable[url] else None
        health.tiers.append(TierStatus(tier, model, url, present))
    return health


def is_ollama(base_url: str) -> bool:
    return ":11434" in base_url or "ollama" in base_url.lower()


def ollama_host(base_url: str) -> str:
    """http://localhost:11434/v1 → http://localhost:11434 (Ollama's native API)."""
    return re.sub(r"/v1/?$", "", base_url.rstrip("/"))


def start_hint(base_url: str) -> str:
    if is_ollama(base_url):
        if platform.system() in ("Windows", "Darwin"):
            return "open the Ollama app (or run `ollama serve` in another terminal)"
        return "run `ollama serve` in another terminal (or `systemctl start ollama`)"
    if ":1234" in base_url:
        return "open LM Studio and start the local server (Developer tab → Start Server)"
    return f"start the model server at {base_url}"


# ---------------------------------------------------------------- substitutes
def model_size(name: str) -> float:
    match = SIZE_RE.search(name)
    return float(match.group(1)) if match else 0.0


def _is_embed(name: str) -> bool:
    return "embed" in name.lower() or "bge" in name.lower()


def suggest_substitute(tier: str, installed: set[str]) -> str | None:
    """The installed model best suited to a tier: coder models first, then size (small for `fast`)."""
    if tier == "embed":
        embeds = sorted(m for m in installed if _is_embed(m))
        return embeds[0] if embeds else None
    if tier == "vision":
        vision = sorted(m for m in installed if re.search(r"vl|vision|llava", m, re.IGNORECASE))
        return vision[0] if vision else None
    chat = [m for m in installed if not _is_embed(m)]
    if not chat:
        return None
    small_first = tier == "fast"

    def key(name: str) -> tuple[int, float, str]:
        size = model_size(name) or 7.0
        return (0 if "coder" in name.lower() else 1, size if small_first else -size, name)

    return sorted(chat, key=key)[0]


def substitutes(health: Health) -> dict[str, str]:
    """tier → installed model to use instead of the missing one."""
    out = {}
    for status in health.missing():
        choice = suggest_substitute(status.tier, health.installed.get(status.base_url, set()))
        if choice:
            out[status.tier] = choice
    return out


def apply_substitutes(settings: Settings, subs: dict[str, str]) -> None:
    """Changes the models of the active profile for this session."""
    profile = settings.active_profile
    for tier, model in subs.items():
        setattr(profile, tier, model)


def env_path() -> Path:
    return PROJECT_DIR / ".env"


def save_env(values: dict[str, str], path: Path | None = None) -> Path:
    """Writes/updates keys in MyDevAgent's .env (the one created by the installer)."""
    path = path or env_path()
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    pending = dict(values)
    for i, line in enumerate(lines):
        key = line.lstrip("# ").split("=", 1)[0].strip()
        if key in pending and "=" in line:
            lines[i] = f"{key}={pending.pop(key)}"
    lines += [f"{k}={v}" for k, v in pending.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for key, value in values.items():
        os.environ[key] = value
    return path


# ------------------------------------------------------------------- downloads
def pull_model(base_url: str, model: str, on_progress: Callable[[str, int, int], None] | None = None,
               client: httpx.Client | None = None) -> None:
    """Downloads a model with Ollama's `POST /api/pull`, calling on_progress(status, completed, total)."""
    client = client or httpx.Client(timeout=httpx.Timeout(10.0, read=None))
    with client.stream("POST", ollama_host(base_url) + "/api/pull", json={"model": model, "stream": True}) as resp:
        if resp.status_code >= 400:
            resp.read()
            raise RuntimeError(_pull_error(resp.text) or f"HTTP {resp.status_code}")
        for line in resp.iter_lines():
            if not line.strip():
                continue
            data = json.loads(line)
            if data.get("error"):
                raise RuntimeError(data["error"])
            if on_progress:
                on_progress(data.get("status", ""), int(data.get("completed") or 0), int(data.get("total") or 0))


def _pull_error(text: str) -> str:
    try:
        return json.loads(text).get("error", "")
    except ValueError:
        return text.strip()[:200]


# ---------------------------------------------------------------------- errors
def explain_error(exc: BaseException, settings: Settings | None = None) -> tuple[str, str]:
    """→ (title, hint) in plain English for the most common errors of local backends."""
    name = type(exc).__name__
    text = str(exc)
    low = text.lower()
    url = ""
    if settings is not None:
        try:
            url = settings.resolve_model("main")[1].base_url
        except ValueError:
            url = ""
    model = _model_from_error(text)
    if (name == "NotFoundError" or "404" in text) and "model" in low and ("not found" in low or "pull" in low):
        what = f"The model {model} is not installed" if model else "The requested model is not installed"
        hint = (f"download it with `/pull {model}` (or `ollama pull {model}`)" if model else "check with /models")
        return what, hint + ", or use a model you already have with `/model <name>`"
    if name in ("APIConnectionError", "ConnectError", "ConnectionError", "ConnectionRefusedError") or \
            "connection refused" in low or "connection error" in low:
        where = f" at {url}" if url else ""
        return f"I can't reach the model server{where}", start_hint(url)
    if name in ("APITimeoutError", "ReadTimeout", "TimeoutException", "Timeout") or "timed out" in low:
        return ("The model is too slow for this PC",
                "use /fast for simple requests or a lighter profile (`mydevagent -p cpu`); "
                "`mydevagent bench` measures the speed")
    if "out of memory" in low or "requires more system memory" in low or "insufficient memory" in low:
        return ("The model does not fit in memory",
                "close other programs or use a smaller profile (`mydevagent -p cpu` / `-p gpu8`)")
    if name in ("AuthenticationError", "PermissionDeniedError"):
        return "The model server rejected the key", "check LLM_API_KEY in the .env file"
    return f"{name}: {text}", "try /doctor"


def _model_from_error(text: str) -> str:
    match = re.search(r"model ['\"]([^'\"]+)['\"]", text) or re.search(r"model ([\w.:/-]+) not found", text)
    return match.group(1) if match else ""


# -------------------------------------------------------------------- hardware
@dataclass
class Hardware:
    gpu_gb: float = 0.0  # NVIDIA VRAM (the largest card)
    gpu_name: str = ""
    apple_gb: float = 0.0  # Apple Silicon unified memory
    ram_gb: float = 0.0

    def describe(self) -> str:
        if self.gpu_gb:
            return f"GPU {self.gpu_name or 'NVIDIA'} with {self.gpu_gb:.0f} GB"
        if self.apple_gb:
            return f"Apple Silicon with {self.apple_gb:.0f} GB of unified memory"
        return f"no GPU detected, {self.ram_gb:.0f} GB of RAM" if self.ram_gb else "no GPU detected"


def _run(cmd: list[str]) -> str:
    if not shutil.which(cmd[0]):
        return ""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def parse_nvidia_smi(output: str) -> tuple[float, str]:
    best = (0.0, "")
    for line in output.strip().splitlines():
        name, _, mem = line.rpartition(",")
        try:
            gb = float(mem.strip()) / 1024
        except ValueError:
            continue
        if gb > best[0]:
            best = (gb, name.strip())
    return best


def detect_hardware(run: Callable[[list[str]], str] = _run) -> Hardware:
    hw = Hardware()
    hw.gpu_gb, hw.gpu_name = parse_nvidia_smi(
        run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"]))
    if platform.system() == "Darwin":
        out = run(["sysctl", "-n", "hw.memsize"]).strip()
        total = int(out) / 1024**3 if out.isdigit() else 0.0
        hw.ram_gb = total
        if platform.machine() == "arm64":
            hw.apple_gb = total
    else:
        try:
            hw.ram_gb = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024**3
        except (ValueError, OSError, AttributeError):
            hw.ram_gb = 0.0
    return hw


def recommend_profile(hw: Hardware) -> str:
    usable = hw.gpu_gb or hw.apple_gb * 0.7  # on a Mac part of the memory stays with the system
    if usable >= 22:
        return "gpu24"
    if usable >= 14:
        return "gpu16"
    if usable >= 7:
        return "gpu8"
    return "cpu"
