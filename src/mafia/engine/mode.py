"""The contract between the engine and a game mode (D10, design §10.2)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol

from mafia.engine.core_events import SeatInfo
from mafia.engine.events import Event, NewEvent
from mafia.engine.requests import Action, RequestSpec, SeatRequest
from mafia.engine.visibility import Viewer


@dataclass(frozen=True, slots=True)
class CoreState:
    """Engine-owned state, identical for every mode."""

    game_id: str = ""
    mode: str = ""
    seed: str = ""
    seats: tuple[SeatInfo, ...] = ()
    human_seats: frozenset[int] = frozenset()
    last_seq: int = 0
    day: int = 0
    phase: str = "setup"
    open_requests: Mapping[str, SeatRequest] = field(default_factory=lambda: MappingProxyType({}))
    requests_opened: int = 0
    ended: bool = False
    winner: str | None = None

    @property
    def seat_numbers(self) -> tuple[int, ...]:
        return tuple(s.seat for s in self.seats)

    def name_of(self, seat: int) -> str:
        for info in self.seats:
            if info.seat == seat:
                return info.name
        raise KeyError(seat)


@dataclass(frozen=True, slots=True)
class Progress:
    """What a mode does when no request is open: emit events and/or open new requests."""

    events: Sequence[NewEvent] = ()
    requests: Sequence[RequestSpec] = ()


class Mode[S](Protocol):
    name: str

    def initial_state(self) -> S: ...

    def setup(self, state: S, core: CoreState) -> Sequence[NewEvent]:
        """Events right after the table is seated (e.g. secret role assignment)."""
        ...

    def reduce(self, state: S, core: CoreState, event: Event) -> S:
        """Fold one event into mode state. `core` already reflects the event."""
        ...

    def progress(self, state: S, core: CoreState) -> Progress:
        """Called only when no request is open and the game has not ended."""
        ...

    def validate(self, state: S, core: CoreState, request: SeatRequest, action: Action) -> None:
        """Rule-specific checks beyond the generic shape. Raise `ActionRejected`."""
        ...

    def on_action(
        self, state: S, core: CoreState, request: SeatRequest, action: Action
    ) -> Sequence[NewEvent]:
        """Events caused immediately by an accepted action (e.g. a public speech)."""
        ...

    def fallback(self, state: S, core: CoreState, request: SeatRequest) -> Action:
        """The action applied when a seat fails to answer in time. Must always be valid."""
        ...

    def viewer(self, state: S, core: CoreState, seat: int) -> Viewer:
        """The point of view of a seat, including its team memberships."""
        ...

    def seat_brief(self, state: S, core: CoreState, seat: int) -> Mapping[str, Any]:
        """The seat's own standing information (role card etc.) for its context."""
        ...
