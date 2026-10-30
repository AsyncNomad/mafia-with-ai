"""What harness adapters may ask the game. Implemented by the application layer (and the M0 spike)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class HumanInputKind(StrEnum):
    SUBMITTED = "submitted"  # recorded as the human's action; the driver should advance
    INFO = "info"  # answered directly to the human (e.g. ":role"); the model is not involved
    REJECTED = "rejected"  # not a valid action now; shown to the human only
    PASSTHROUGH = "passthrough"  # not game input; let the prompt reach the driver unchanged


@dataclass(frozen=True, slots=True)
class HumanInputOutcome:
    kind: HumanInputKind
    message: str = ""


class HarnessState(Protocol):
    def is_awaiting(self, agent_type: str) -> bool:
        """True while the game has an open, unfulfilled request for this agent to act."""
        ...

    def record_agent_finished(self, agent_type: str) -> None:
        """The agent's run ended. If it did not submit, the game applies its fallback later."""
        ...

    def handle_human_input(self, text: str) -> HumanInputOutcome:
        """Apply the human's message verbatim, bypassing any model paraphrase."""
        ...
