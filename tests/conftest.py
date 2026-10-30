from __future__ import annotations

from dataclasses import dataclass, field

import pytest


@dataclass
class FakeState:
    awaiting: set[str] = field(default_factory=set[str])
    human_inputs: list[str] = field(default_factory=list[str])

    def is_awaiting(self, agent_type: str) -> bool:
        return agent_type in self.awaiting

    def record_human_input(self, text: str) -> None:
        self.human_inputs.append(text)


@pytest.fixture
def state() -> FakeState:
    return FakeState(awaiting={"seat-3"})
