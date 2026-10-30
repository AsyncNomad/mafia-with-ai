"""Event bodies owned by the engine, shared by every mode."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from mafia.engine.events import Body
from mafia.engine.requests import Action, SeatRequest


class SeatInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    seat: int
    name: str


class GameCreated(Body):
    """God-only: carries the seed and which seat is the human."""

    KIND: ClassVar[str] = "game_created"
    game_id: str
    mode: str
    seed: str
    human_seats: tuple[int, ...]


class SeatsAnnounced(Body):
    """Public table roster. Deliberately says nothing about who is human."""

    KIND: ClassVar[str] = "seats_announced"
    seats: tuple[SeatInfo, ...]


class PhaseChanged(Body):
    KIND: ClassVar[str] = "phase_changed"
    day: int
    phase: str


class RequestOpened(Body):
    KIND: ClassVar[str] = "request_opened"
    request: SeatRequest


class ActionSubmitted(Body):
    """Closes the request. `fallback` marks an action the engine applied on the seat's behalf."""

    KIND: ClassVar[str] = "action_submitted"
    request_id: str
    kind: str  # copied from the request so modes can fold without looking it up
    seat: int
    action: Action
    fallback: bool = False


class ContextDelivered(Body):
    """God-only audit trail: exactly which events were shown to a seat."""

    KIND: ClassVar[str] = "context_delivered"
    seat: int
    request_id: str | None
    event_seqs: tuple[int, ...]


class GameEnded(Body):
    KIND: ClassVar[str] = "game_ended"
    winner: str  # mode-defined team name, or "draw"
    reason: str
