"""Test driver: seats a table, advances the engine and answers requests like players would."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mafia.engine.core_events import SeatInfo
from mafia.engine.engine import Engine, GameState, SeatContext
from mafia.engine.events import Body, Event
from mafia.engine.requests import Action, SeatRequest
from mafia.engine.runtime import FixedClock
from mafia.modes.original.config import OriginalConfig
from mafia.modes.original.roles import Role
from mafia.modes.original.rules import OriginalMode, OriginalState

NAMES = ("도윤", "민지", "서연", "하람", "준호", "예린", "태오", "수아")


class Table:
    def __init__(self, seed: str = "seed-1", config: OriginalConfig | None = None) -> None:
        # Rule tests were written against a day start; pass a config to test the night start.
        self.rules = OriginalMode(config or OriginalConfig(start_with_night=False))
        self.engine: Engine[OriginalState] = Engine(self.rules, FixedClock())
        seats = [SeatInfo(seat=i + 1, name=n) for i, n in enumerate(NAMES)]
        step = self.engine.create("game-1", seed, seats, human_seats=[1])
        self.state: GameState[OriginalState] = step.state
        self.log: list[Event] = list(step.events)
        self.advance()

    # --- driving ------------------------------------------------------------------------
    def advance(self) -> None:
        step = self.engine.advance(self.state)
        self.state = step.state
        self.log.extend(step.events)

    def requests(self) -> list[SeatRequest]:
        return sorted(self.state.core.open_requests.values(), key=lambda r: r.seat)

    def request_of(self, seat: int) -> SeatRequest:
        for request in self.requests():
            if request.seat == seat:
                return request
        raise AssertionError(f"seat {seat} has no open request; open: {self.requests()}")

    def answer(self, seat: int, *, advance: bool = True, **fields: Any) -> None:
        request = self.request_of(seat)
        step = self.engine.submit(self.state, seat, request.request_id, Action(**fields))
        self.state = step.state
        self.log.extend(step.events)
        if advance:
            self.advance()

    def answer_all(self, choose: Callable[[SeatRequest], dict[str, Any]]) -> None:
        """Answer every currently open request, then advance once (a parallel phase)."""
        for request in self.requests():
            self.answer(request.seat, advance=False, **choose(request))
        self.advance()

    def pass_until(self, phase: str) -> None:
        """Pass/abstain on everything until the given phase is reached."""
        for _ in range(500):
            if self.mode.phase.value == phase or self.state.core.ended:
                return
            self.answer_all(lambda _r: {})
        raise AssertionError(f"never reached {phase}")

    def context(self, seat: int) -> SeatContext:
        step, ctx = self.engine.context_for(self.state, self.log, seat)
        self.state = step.state
        self.log.extend(step.events)
        return ctx

    # --- inspection ---------------------------------------------------------------------
    @property
    def mode(self) -> OriginalState:
        return self.state.mode

    def seats_with(self, role: Role) -> list[int]:
        return sorted(s for s, r in self.mode.roles.items() if r is role)

    def bodies[B: Body](self, cls: type[B]) -> list[B]:
        return [e.body_as(cls) for e in self.log if e.kind == cls.KIND]

    def events(self, cls: type[Body]) -> list[Event]:
        return [e for e in self.log if e.kind == cls.KIND]
