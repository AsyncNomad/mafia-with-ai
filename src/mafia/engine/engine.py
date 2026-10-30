"""The engine: folds events into state and turns commands into new events.

Every command returns a `Step` (new state + the events that produced it). The caller persists
the events; the state is always reproducible by folding the persisted log.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any

from mafia.engine.core_events import (
    ActionSubmitted,
    ContextDelivered,
    GameCreated,
    GameEnded,
    PhaseChanged,
    RequestOpened,
    SeatInfo,
    SeatsAnnounced,
)
from mafia.engine.events import Event, NewEvent, emit
from mafia.engine.mode import CoreState, Mode
from mafia.engine.requests import Action, ActionRejected, SeatRequest, validate_shape
from mafia.engine.runtime import Clock
from mafia.engine.visibility import Visibility, can_see


class EngineError(RuntimeError):
    """A bug or a corrupted log: never caused by a seat's input."""


@dataclass(frozen=True, slots=True)
class GameState[S]:
    core: CoreState
    mode: S


@dataclass(frozen=True, slots=True)
class Step[S]:
    state: GameState[S]
    events: tuple[Event, ...]


@dataclass(frozen=True, slots=True)
class SeatContext:
    seat: int
    brief: Mapping[str, Any]
    events: tuple[Event, ...]
    request: SeatRequest | None


def _reduce_core(core: CoreState, event: Event) -> CoreState:
    if event.seq != core.last_seq + 1:
        raise EngineError(f"event seq {event.seq} does not follow {core.last_seq}")
    core = replace(core, last_seq=event.seq)
    match event.kind:
        case GameCreated.KIND:
            b = event.body_as(GameCreated)
            return replace(
                core,
                game_id=b.game_id,
                mode=b.mode,
                seed=b.seed,
                human_seats=frozenset(b.human_seats),
            )
        case SeatsAnnounced.KIND:
            return replace(core, seats=event.body_as(SeatsAnnounced).seats)
        case PhaseChanged.KIND:
            b = event.body_as(PhaseChanged)
            return replace(core, day=b.day, phase=b.phase)
        case RequestOpened.KIND:
            request = event.body_as(RequestOpened).request
            opened = {**core.open_requests, request.request_id: request}
            return replace(
                core,
                open_requests=MappingProxyType(opened),
                requests_opened=core.requests_opened + 1,
            )
        case ActionSubmitted.KIND:
            request_id = event.body_as(ActionSubmitted).request_id
            if request_id not in core.open_requests:
                raise EngineError(
                    f"event {event.seq} answers request {request_id} that is not open"
                )
            remaining = {k: v for k, v in core.open_requests.items() if k != request_id}
            return replace(core, open_requests=MappingProxyType(remaining))
        case GameEnded.KIND:
            return replace(core, ended=True, winner=event.body_as(GameEnded).winner)
        case _:
            return core


class _Appender[S]:
    def __init__(self, engine: Engine[S], state: GameState[S]) -> None:
        self._engine = engine
        self.state = state
        self.events: list[Event] = []

    def add(self, new: NewEvent) -> Event:
        event = Event(
            seq=self.state.core.last_seq + 1,
            at=self._engine.clock.now(),
            kind=new.body.KIND,
            visibility=new.visibility,
            data=new.body.model_dump(mode="json"),
        )
        self.state = self._engine.apply(self.state, event)
        self.events.append(event)
        return event

    def add_all(self, news: Iterable[NewEvent]) -> None:
        for new in news:
            self.add(new)

    def step(self) -> Step[S]:
        return Step(self.state, tuple(self.events))


class Engine[S]:
    def __init__(self, mode: Mode[S], clock: Clock, *, max_progress_steps: int = 10_000) -> None:
        self.mode = mode
        self.clock = clock
        self._max_progress_steps = max_progress_steps

    # --- folding ------------------------------------------------------------------------
    def empty(self) -> GameState[S]:
        return GameState(CoreState(), self.mode.initial_state())

    def apply(self, state: GameState[S], event: Event) -> GameState[S]:
        core = _reduce_core(state.core, event)
        return GameState(core, self.mode.reduce(state.mode, core, event))

    def fold(self, events: Iterable[Event]) -> GameState[S]:
        state = self.empty()
        for event in events:
            state = self.apply(state, event)
        return state

    # --- commands -----------------------------------------------------------------------
    def create(
        self,
        game_id: str,
        seed: str,
        seats: Sequence[SeatInfo],
        human_seats: Iterable[int] = (),
    ) -> Step[S]:
        numbers = [s.seat for s in seats]
        if sorted(numbers) != list(range(1, len(seats) + 1)):
            raise EngineError(f"seats must be numbered 1..{len(seats)}, got {numbers}")
        humans = tuple(sorted(set(human_seats)))
        if not set(humans) <= set(numbers):
            raise EngineError(f"human seats {humans} are not at the table")
        out = _Appender(self, self.empty())
        out.add(
            emit(
                GameCreated(game_id=game_id, mode=self.mode.name, seed=seed, human_seats=humans),
                Visibility.god(),
            )
        )
        out.add(emit(SeatsAnnounced(seats=tuple(seats)), Visibility.public()))
        out.add_all(self.mode.setup(out.state.mode, out.state.core))
        return out.step()

    def submit(self, state: GameState[S], seat: int, request_id: str, action: Action) -> Step[S]:
        """Validate and record a seat's answer. Raises `ActionRejected` for bad input."""
        if state.core.ended:
            raise ActionRejected("게임이 이미 끝났습니다.")
        request = state.core.open_requests.get(request_id)
        if request is None:
            raise ActionRejected("이미 처리됐거나 존재하지 않는 요청입니다.")
        if request.seat != seat:
            raise ActionRejected("이 요청은 당신의 것이 아닙니다.")
        validate_shape(request, action, len(state.core.seats))
        self.mode.validate(state.mode, state.core, request, action)
        return self._record(state, request, action, fallback=False)

    def expire(self, state: GameState[S], request_id: str) -> Step[S]:
        """Apply the mode's fallback action to an unanswered request."""
        request = state.core.open_requests.get(request_id)
        if request is None:
            raise EngineError(f"request {request_id} is not open")
        action = self.mode.fallback(state.mode, state.core, request)
        try:
            validate_shape(request, action, len(state.core.seats))
            self.mode.validate(state.mode, state.core, request, action)
        except ActionRejected as exc:
            raise EngineError(f"mode fallback for {request.kind} is invalid: {exc}") from exc
        return self._record(state, request, action, fallback=True)

    def _record(
        self, state: GameState[S], request: SeatRequest, action: Action, *, fallback: bool
    ) -> Step[S]:
        immediate = self.mode.on_action(state.mode, state.core, request, action)
        out = _Appender(self, state)
        out.add(
            emit(
                ActionSubmitted(
                    request_id=request.request_id,
                    kind=request.kind,
                    seat=request.seat,
                    action=action,
                    fallback=fallback,
                ),
                Visibility.to_seats(request.seat),
            )
        )
        out.add_all(immediate)
        return out.step()

    def advance(self, state: GameState[S]) -> Step[S]:
        """Run the mode until it opens requests or the game ends."""
        out = _Appender(self, state)
        for _ in range(self._max_progress_steps):
            core = out.state.core
            if core.ended or core.open_requests or not core.seats:
                return out.step()
            progress = self.mode.progress(out.state.mode, core)
            if not progress.events and not progress.requests:
                raise EngineError(f"mode made no progress in phase {core.phase!r}")
            out.add_all(progress.events)
            if out.state.core.ended:
                if progress.requests:
                    raise EngineError("mode opened requests after ending the game")
                return out.step()
            busy = {r.seat for r in out.state.core.open_requests.values()}
            for spec in progress.requests:
                if spec.seat not in out.state.core.seat_numbers:
                    raise EngineError(f"request for unknown seat {spec.seat}")
                if spec.seat in busy:
                    raise EngineError(f"seat {spec.seat} already has an open request")
                busy.add(spec.seat)
                request = SeatRequest(
                    request_id=f"q{out.state.core.requests_opened + 1}",
                    **spec.model_dump(),
                )
                out.add(emit(RequestOpened(request=request), Visibility.to_seats(spec.seat)))
        raise EngineError("mode did not settle; possible infinite loop")

    def context_for(
        self, state: GameState[S], log: Sequence[Event], seat: int
    ) -> tuple[Step[S], SeatContext]:
        """Everything this seat may know right now, recorded for audit."""
        if not log or log[-1].seq != state.core.last_seq:
            raise EngineError("log does not match state")
        if seat not in state.core.seat_numbers:
            raise EngineError(f"unknown seat {seat}")
        viewer = self.mode.viewer(state.mode, state.core, seat)
        visible = tuple(
            e for e in log if e.kind != ContextDelivered.KIND and can_see(viewer, e.visibility)
        )
        request = next((r for r in state.core.open_requests.values() if r.seat == seat), None)
        brief = self.mode.seat_brief(state.mode, state.core, seat)
        out = _Appender(self, state)
        out.add(
            emit(
                ContextDelivered(
                    seat=seat,
                    request_id=request.request_id if request else None,
                    event_seqs=tuple(e.seq for e in visible),
                ),
                Visibility.god(),
            )
        )
        return out.step(), SeatContext(seat=seat, brief=brief, events=visible, request=request)
