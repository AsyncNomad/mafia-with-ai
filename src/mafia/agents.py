"""Host-neutral catalog of the non-seat agents (D12).

Prompt bodies live in `prompts/agents/<name>.md`. Host adapters render this catalog into their
own agent definition format (Claude Code: `.claude/agents/<name>.md` with YAML frontmatter).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from mafia import paths
from mafia.harness.tools import AGENT_TOOLS


class ModelTier(StrEnum):
    STRONG = "strong"  # long, consistency-critical design work
    BALANCED = "balanced"
    FAST = "fast"


@dataclass(frozen=True, slots=True)
class AgentSpec:
    name: str
    description: str
    tier: ModelTier
    effort: str
    max_turns: int

    @property
    def tools(self) -> tuple[str, ...]:
        return tuple(sorted(AGENT_TOOLS[self.name]))

    def prompt(self) -> str:
        return (paths.prompts_dir() / "agents" / f"{self.name}.md").read_text(encoding="utf-8")


_ONLY_ON_REQUEST = (
    "엔진이 요청할 때만 /mafia 진행 프로토콜에 따라 호출한다. 다른 용도로 쓰지 않는다."
)

MYSTERY_AGENTS: tuple[AgentSpec, ...] = (
    AgentSpec(
        name="mystery-architect",
        description=f"머더 미스터리 모드의 시나리오 설계자. {_ONLY_ON_REQUEST}",
        tier=ModelTier.STRONG,
        effort="high",
        max_turns=60,
    ),
    AgentSpec(
        name="mystery-auditor",
        description=f"머더 미스터리 시나리오 감수자. {_ONLY_ON_REQUEST}",
        tier=ModelTier.STRONG,
        effort="high",
        max_turns=20,
    ),
    AgentSpec(
        name="mystery-solver",
        description=f"진상을 모르는 블라인드 탐정. {_ONLY_ON_REQUEST}",
        tier=ModelTier.BALANCED,
        effort="medium",
        max_turns=8,
    ),
    AgentSpec(
        name="mystery-narrator",
        description=f"머더 미스터리 모드의 내레이터. {_ONLY_ON_REQUEST}",
        tier=ModelTier.BALANCED,
        effort="low",
        max_turns=4,
    ),
)
