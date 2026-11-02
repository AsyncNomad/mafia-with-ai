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
    def seat_of(self, agent_type: str, run_id: str = "") -> int | None:
        """The seat an agent plays: from `seat-N`, or from the game's secret mapping for an
        anonymous alias. A known run keeps the seat it started with, even if the aliases have
        been reshuffled since. None when the alias is not in use."""
        ...

    def needs_submission(self, agent_type: str, run_id: str = "") -> bool:
        """True while the game has an open, unfulfilled request for this agent (run)."""
        ...

    def is_awaiting(self, agent_type: str) -> bool:
        """True when the agent should be woken now: it has work and no run is in progress."""
        ...

    def record_agent_started(self, agent_id: str, agent_type: str) -> None:
        """A run of this agent began (hosts may run subagents in the background)."""
        ...

    def record_agent_finished(self, agent_id: str, agent_type: str) -> None:
        """The run ended. If it did not submit, the game applies its fallback later."""
        ...

    def handle_human_input(self, text: str) -> HumanInputOutcome:
        """Apply the human's message verbatim, bypassing any model paraphrase."""
        ...

    def driver_must_continue(self) -> bool:
        """True while the game waits on the driver rather than on the human."""
        ...

    def spend_driver_nudge(self) -> bool:
        """Use one 'keep going' push on a driver that tries to stop. False once the budget is
        spent without progress (the game resets it on `advance`): looping forever is worse."""
        ...
