"""What harness adapters may ask the game. Implemented by the engine (and by the M0 spike)."""

from __future__ import annotations

from typing import Protocol


class HarnessState(Protocol):
    def is_awaiting(self, agent_type: str) -> bool:
        """True while the game has an open, unfulfilled request for this agent to act."""
        ...

    def record_human_input(self, text: str) -> None:
        """Store the human's message verbatim, bypassing any model paraphrase."""
        ...
