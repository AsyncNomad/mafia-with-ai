from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from mafia.harness.ports import HumanInputKind, HumanInputOutcome


@dataclass
class FakeState:
    awaiting: set[str] = field(default_factory=set[str])
    human_inputs: list[str] = field(default_factory=list[str])

    def is_awaiting(self, agent_type: str) -> bool:
        return agent_type in self.awaiting

    finished: list[str] = field(default_factory=list[str])
    outcome: HumanInputOutcome = field(
        default_factory=lambda: HumanInputOutcome(HumanInputKind.PASSTHROUGH)
    )

    def handle_human_input(self, text: str) -> HumanInputOutcome:
        self.human_inputs.append(text)
        return self.outcome

    started: list[str] = field(default_factory=list[str])

    def needs_submission(self, agent_type: str) -> bool:
        return agent_type in self.awaiting

    def record_agent_started(self, agent_id: str, agent_type: str) -> None:
        self.started.append(agent_type)

    def record_agent_finished(self, agent_id: str, agent_type: str) -> None:
        self.finished.append(agent_type)


@pytest.fixture
def state() -> FakeState:
    return FakeState(awaiting={"seat-3"})
