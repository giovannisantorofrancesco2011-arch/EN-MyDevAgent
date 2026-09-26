"""Configuration loading (YAML + environment variables)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent
TIERS = ("main", "fast", "reasoning", "vision", "embed")


class Backend(BaseModel):
    base_url: str
    api_key: str = "none"


class ModelRef(BaseModel):
    model: str
    backend: str | None = None


class Profile(BaseModel):
    main: str | ModelRef
    fast: str | ModelRef
    reasoning: str | ModelRef
    vision: str | ModelRef | None = None
    embed: str | ModelRef | None = None
    num_ctx: int = 16384
    native_tools: bool = False


class ModeConfig(BaseModel):
    gate: list[str] = Field(default_factory=list)
    max_review_rounds: int = 0
    think: bool = False
    docs: bool = False


class RouterConfig(BaseModel):
    default_mode: str = "auto"
    use_llm: bool = False
    fast_max_chars: int = 320
    deep_min_chars: int = 1500
    deep_min_domains: int = 4


class ContextConfig(BaseModel):
    history_turns: int = 4
    max_section_chars: int = 6000
    max_file_chars: int = 12000
    max_history_chars: int = 1500


class WebConfig(BaseModel):
    enabled: bool = True
    providers: list[str] = Field(default_factory=lambda: ["tavily", "firecrawl", "searxng", "duckduckgo"])
    max_results: int = 5
    fetch_top_n: int = 1
    max_page_chars: int = 6000
    allow_private_urls: bool = False
    timeout_s: float = 12


class ConnectivityConfig(BaseModel):
    check_url: str = "https://www.gstatic.com/generate_204"
    timeout_s: float = 2.0
    cache_s: float = 60


class SandboxConfig(BaseModel):
    enabled: bool = True
    backend: str = "docker"
    allow_unsafe_local: bool = False
    timeout_s: float = 20
    memory: str = "512m"
    cpus: float = 1.0
    images: dict[str, str] = Field(
        default_factory=lambda: {
            "python": "python:3.12-slim",
            "javascript": "node:22-slim",
            "bash": "alpine:3.20",
        }
    )


class FilesystemConfig(BaseModel):
    root: str = "."
    allow_write: bool = False


class GitConfig(BaseModel):
    allow_commit: bool = False


class RagConfig(BaseModel):
    enabled: bool = True
    index_dir: str = ".mydevagent"
    top_k: int = 6
    chunk_lines: int = 60
    chunk_overlap: int = 10


class ToolsConfig(BaseModel):
    web: WebConfig = Field(default_factory=WebConfig)
    connectivity: ConnectivityConfig = Field(default_factory=ConnectivityConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    filesystem: FilesystemConfig = Field(default_factory=FilesystemConfig)
    git: GitConfig = Field(default_factory=GitConfig)
    rag: RagConfig = Field(default_factory=RagConfig)
    custom: list[str] = Field(default_factory=list)


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000
    show_progress: bool = True


class Settings(BaseModel):
    profile: str = "gpu8"
    backends: dict[str, Backend]
    default_backend: str = "ollama"
    profiles: dict[str, Profile]
    modes: dict[str, ModeConfig] = Field(default_factory=dict)
    router: RouterConfig = Field(default_factory=RouterConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    config_dir: Path = PROJECT_DIR / "config"
    prompts_dir: Path = PROJECT_DIR / "prompts"

    # ------------------------------------------------------------------ helpers
    @property
    def active_profile(self) -> Profile:
        if self.profile not in self.profiles:
            raise ValueError(f"Profile '{self.profile}' is not defined. Available: {list(self.profiles)}")
        return self.profiles[self.profile]

    def mode(self, name: str) -> ModeConfig:
        return self.modes.get(name, ModeConfig())

    def resolve_model(self, tier: str) -> tuple[str, Backend]:
        """Returns (model_name, backend) for a tier of the active profile."""
        if tier not in TIERS:
            raise ValueError(f"Unknown tier: {tier}")
        ref = getattr(self.active_profile, tier) or self.active_profile.main
        if isinstance(ref, str):
            ref = ModelRef(model=ref)
        backend_name = ref.backend or self.default_backend
        if backend_name not in self.backends:
            raise ValueError(f"Backend '{backend_name}' is not defined in settings.yaml")
        return ref.model, self.backends[backend_name]


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (does not override variables that are already set)."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        if value:
            os.environ.setdefault(key.strip(), value)


def _apply_env(data: dict[str, Any]) -> dict[str, Any]:
    env = os.environ
    if env.get("MYDEVAGENT_PROFILE"):
        data["profile"] = env["MYDEVAGENT_PROFILE"]
    default_backend = data.get("default_backend", "ollama")
    backend = data.setdefault("backends", {}).setdefault(default_backend, {})
    if env.get("LLM_BASE_URL"):
        backend["base_url"] = env["LLM_BASE_URL"]
    if env.get("LLM_API_KEY"):
        backend["api_key"] = env["LLM_API_KEY"]
    profile = data.get("profiles", {}).get(data.get("profile", "gpu8"))
    if profile is not None:
        for tier in TIERS:
            value = env.get(f"MYDEVAGENT_MODEL_{tier.upper()}")
            if value:
                profile[tier] = value
    return data


def _find_config() -> Path:
    """MYDEVAGENT_CONFIG → ./config/settings.yaml → $MYDEVAGENT_HOME/config → project folder."""
    candidates = [os.environ.get("MYDEVAGENT_CONFIG"), Path.cwd() / "config" / "settings.yaml"]
    if os.environ.get("MYDEVAGENT_HOME"):
        candidates.append(Path(os.environ["MYDEVAGENT_HOME"]) / "config" / "settings.yaml")
    candidates.append(PROJECT_DIR / "config" / "settings.yaml")
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise FileNotFoundError("config/settings.yaml not found: run `pip install -e .` from the project "
                            "folder or set MYDEVAGENT_HOME / MYDEVAGENT_CONFIG")


def load_settings(path: str | Path | None = None, overrides: dict[str, Any] | None = None) -> Settings:
    """Loads config/settings.yaml (or `path`), applies .env, env vars and explicit overrides."""
    config_path = Path(path) if path else _find_config()
    _load_dotenv(Path.cwd() / ".env")
    _load_dotenv(PROJECT_DIR / ".env")
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    data = _apply_env(data)
    if overrides:
        data = _deep_merge(data, overrides)
    data.setdefault("config_dir", config_path.parent)
    data.setdefault("prompts_dir", config_path.parent.parent / "prompts")
    return Settings.model_validate(data)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
