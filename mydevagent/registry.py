"""Registry of the 15 agents: loads config/agents.yaml and the associated prompts."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .config import Settings

EXPECTED_AGENT_COUNT = 15  # core: fast / balanced / deep
EXPECTED_ULTRA_COUNT = 20  # extended: /ultra-deep only (35 total)
STAGES = ("research", "plan", "specialist", "test", "gate", "docs", "final", "judge")
SECTIONS = (
    "request",
    "history",
    "files",
    "rag",
    "research",
    "plan",
    "artifacts",
    "test_report",
    "issues",
    "image_notes",
)


@dataclass(frozen=True)
class Agent:
    id: int
    key: str
    name: str
    stage: str
    role: str
    goal: str
    prompt: str
    tier: str = "main"
    max_tokens: int = 1200
    temperature: float = 0.2
    reads: tuple[str, ...] = ("request",)
    tools: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    group: str = "core"  # core | ultra


@dataclass
class AgentRegistry:
    agents: dict[str, Agent]
    persona: str
    by_alias: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for agent in self.agents.values():
            self.by_alias[agent.key] = agent.key
            for alias in agent.aliases:
                self.by_alias[alias.lower()] = agent.key

    def __getitem__(self, key: str) -> Agent:
        return self.agents[key]

    def __iter__(self):
        return iter(sorted(self.agents.values(), key=lambda a: a.id))

    def __len__(self) -> int:
        return len(self.agents)

    def by_stage(self, stage: str) -> list[Agent]:
        return [a for a in self if a.stage == stage]

    def core(self) -> list[Agent]:
        return [a for a in self if a.group == "core"]

    def ultra(self) -> list[Agent]:
        return [a for a in self if a.group == "ultra"]

    def resolve(self, name: str) -> str | None:
        """Alias or key → agent key (None if unknown)."""
        return self.by_alias.get(name.lower().lstrip("@"))


def _load_agents(path: Path, prompts_dir: Path, group: str) -> list[Agent]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    agents = []
    for raw in data.get("agents", []):
        prompt_path = prompts_dir / "agents" / raw["prompt"]
        if not prompt_path.is_file():
            raise FileNotFoundError(f"Missing prompt for agent {raw['key']}: {prompt_path}")
        agent = Agent(
            id=int(raw["id"]),
            key=raw["key"],
            name=raw["name"],
            stage=raw["stage"],
            role=raw["role"],
            goal=raw["goal"],
            prompt=prompt_path.read_text(encoding="utf-8").strip(),
            tier=raw.get("tier", "main"),
            max_tokens=int(raw.get("max_tokens", 1200)),
            temperature=float(raw.get("temperature", 0.2)),
            reads=tuple(raw.get("reads", ["request"])),
            tools=tuple(raw.get("tools", [])),
            keywords=tuple(str(k).lower() for k in raw.get("keywords", [])),
            aliases=tuple(str(a).lower() for a in raw.get("aliases", [])),
            group=group,
        )
        _validate(agent)
        agents.append(agent)
    return agents


def load_registry(settings: Settings, *, strict: bool = True) -> AgentRegistry:
    config_dir = Path(settings.config_dir)
    prompts_dir = Path(settings.prompts_dir)
    persona = (prompts_dir / "system_persona.md").read_text(encoding="utf-8").strip()

    core = _load_agents(config_dir / "agents.yaml", prompts_dir, "core")
    ultra_file = config_dir / "agents_ultra.yaml"
    ultra = _load_agents(ultra_file, prompts_dir, "ultra") if ultra_file.is_file() else []
    agents: dict[str, Agent] = {}
    for agent in core + ultra:
        if agent.key in agents:
            raise ValueError(f"Duplicate agent key: {agent.key}")
        agents[agent.key] = agent

    if strict:
        if len(core) != EXPECTED_AGENT_COUNT:
            raise ValueError(f"MyDevAgent requires exactly {EXPECTED_AGENT_COUNT} core agents, "
                             f"found {len(core)}")
        for stage in ("plan", "specialist", "test", "gate", "final"):
            if not any(a.stage == stage for a in core):
                raise ValueError(f"No agent with stage '{stage}'")
        if sorted(a.id for a in core) != list(range(1, EXPECTED_AGENT_COUNT + 1)):
            raise ValueError("Core agent ids must be 1..15 with no gaps")
        if ultra:
            total = EXPECTED_AGENT_COUNT + EXPECTED_ULTRA_COUNT
            if len(ultra) != EXPECTED_ULTRA_COUNT or sorted(a.id for a in ultra) != list(
                    range(EXPECTED_AGENT_COUNT + 1, total + 1)):
                raise ValueError(f"agents_ultra.yaml must contain exactly {EXPECTED_ULTRA_COUNT} agents "
                                 f"with ids 16..{total}")
    return AgentRegistry(agents=agents, persona=persona)


def _validate(agent: Agent) -> None:
    if agent.stage not in STAGES:
        raise ValueError(f"{agent.key}: invalid stage '{agent.stage}' ({STAGES})")
    unknown = set(agent.reads) - set(SECTIONS)
    if unknown:
        raise ValueError(f"{agent.key}: unknown sections in 'reads': {sorted(unknown)}")
