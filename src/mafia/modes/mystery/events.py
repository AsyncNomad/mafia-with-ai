"""Event bodies of the murder-mystery mode. Visibility is decided where they are emitted."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from mafia.engine.events import Body


class CharacterAssigned(Body):
    """Seat-only: which scenario character this seat plays. The sheet comes from the scenario."""

    KIND: ClassVar[str] = "mystery.character_assigned"
    seat: int
    character_id: str


class Narrated(Body):
    """Public narration from the scenario (prologue, round intros, twist, accusation intro)."""

    KIND: ClassVar[str] = "mystery.narrated"
    beat: str
    text: str


class SpeakingRoundStarted(Body):
    KIND: ClassVar[str] = "mystery.speaking_round_started"
    stage: str  # "statement" | "discussion" | "final"
    round: int
    order: tuple[int, ...]


class Speech(Body):
    KIND: ClassVar[str] = "mystery.speech"
    seat: int
    text: str | None
    stage: str


class CardReceived(Body):
    """Seat-only: a clue card now in this seat's hand (starting hand or investigation)."""

    KIND: ClassVar[str] = "mystery.card_received"
    seat: int
    clue_id: str
    title: str
    text: str
    where: str


class Investigated(Body):
    """Public: who searched where. What they found stays private."""

    KIND: ClassVar[str] = "mystery.investigated"
    investigation: int
    visits: dict[int, str]  # seat -> location name
    empty_handed: tuple[int, ...] = ()


class CardRevealed(Body):
    """Public: the engine shows the real card text, so reveals cannot be forged."""

    KIND: ClassVar[str] = "mystery.card_revealed"
    seat: int
    clue_id: str
    title: str
    text: str


class AccusationRevealed(Body):
    KIND: ClassVar[str] = "mystery.accusation_revealed"
    votes: dict[int, int | None]
    accused: int | None
    runoff_between: tuple[int, ...] = ()  # set when a tie leads to a runoff


class GoalResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    description: str
    achieved: bool
    points: int


class EndingRevealed(Body):
    """Public, after the accusation: the ending, the full truth and everyone's goals."""

    KIND: ClassVar[str] = "mystery.ending_revealed"
    caught: bool
    culprit_seat: int
    ending: str
    truth: str
    characters: dict[int, str]  # seat -> character name (also the human's)
    epilogues: dict[int, str]
    goals: dict[int, tuple[GoalResult, ...]]
