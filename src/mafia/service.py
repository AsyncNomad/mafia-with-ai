"""Application layer: runs engine commands against the event store, safely across processes.

Every command is "load → compute with the pure engine → append if nobody else appended". On a
concurrent append the command is recomputed from the fresh log, which is safe because the
engine has no side effects.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from mafia.engine.core_events import SeatInfo
from mafia.engine.engine import Engine, GameState, SeatContext, Step
from mafia.engine.events import Event
from mafia.engine.requests import Action, SeatRequest
from mafia.engine.runtime import Clock
from mafia.modes.registry import build_mode, default_config
from mafia.store.events import ConcurrencyError, EventStore, GameRecord


@dataclass(frozen=True, slots=True)
class Loaded:
    record: GameRecord
    engine: Engine[Any]
    state: GameState[Any]
    log: tuple[Event, ...]


class GameService:
    def __init__(self, store: EventStore, clock: Clock, *, max_retries: int = 8) -> None:
        self.store = store
        self.clock = clock
        self._max_retries = max_retries
        self._cache: dict[str, Loaded] = {}

    # --- lifecycle ----------------------------------------------------------------------
    def new_game(
        self,
        *,
        mode: str,
        seats: Sequence[SeatInfo],
        human_seats: Sequence[int] = (),
        seed: str | None = None,
        config: Mapping[str, Any] | None = None,
        game_id: str | None = None,
    ) -> str:
        game_id = game_id or f"g-{secrets.token_hex(6)}"
        seed = seed or secrets.token_hex(16)
        resolved = dict(config) if config is not None else default_config(mode)
        engine: Engine[Any] = Engine(build_mode(mode, resolved), self.clock)
        created = engine.create(game_id, seed, seats, human_seats)
        advanced = engine.advance(created.state)
        events = created.events + advanced.events
        self.store.create_game(game_id, mode, resolved, self.clock.now())
        self.store.append(game_id, 0, events, ended=advanced.state.core.ended)
        self.store.set_active_game(game_id)
        return game_id

    def load(self, game_id: str) -> Loaded:
        """Current state, folding only events appended since the last load."""
        cached = self._cache.get(game_id)
        if cached is None:
            record = self.store.game(game_id)
            engine: Engine[Any] = Engine(build_mode(record.mode, record.config), self.clock)
            cached = Loaded(record, engine, engine.empty(), ())
        new = self.store.load(game_id, after_seq=cached.state.core.last_seq)
        if new:
            state = cached.state
            for event in new:
                state = cached.engine.apply(state, event)
            cached = Loaded(self.store.game(game_id), cached.engine, state, cached.log + tuple(new))
        self._cache[game_id] = cached
        return cached

    # --- commands -----------------------------------------------------------------------
    def advance(self, game_id: str) -> tuple[Event, ...]:
        return self._commit(game_id, lambda g: g.engine.advance(g.state))

    def submit(self, game_id: str, seat: int, request_id: str, action: Action) -> tuple[Event, ...]:
        """Record an answer and run the game forward. Raises `ActionRejected` on bad input."""

        def command(g: Loaded) -> Step[Any]:
            submitted = g.engine.submit(g.state, seat, request_id, action)
            advanced = g.engine.advance(submitted.state)
            return Step(advanced.state, submitted.events + advanced.events)

        return self._commit(game_id, command)

    def expire(self, game_id: str, request_id: str) -> tuple[Event, ...]:
        def command(g: Loaded) -> Step[Any]:
            expired = g.engine.expire(g.state, request_id)
            advanced = g.engine.advance(expired.state)
            return Step(advanced.state, expired.events + advanced.events)

        return self._commit(game_id, command)

    def context(self, game_id: str, seat: int) -> SeatContext:
        """What `seat` may know now. The delivery itself is recorded for audit."""
        holder: list[SeatContext] = []

        def command(g: Loaded) -> Step[Any]:
            step, ctx = g.engine.context_for(g.state, g.log, seat)
            holder[:] = [ctx]
            return step

        self._commit(game_id, command)
        return holder[0]

    # --- queries ------------------------------------------------------------------------
    def open_requests(self, game_id: str) -> list[SeatRequest]:
        state = self.load(game_id).state
        return sorted(state.core.open_requests.values(), key=lambda r: (r.seat, r.request_id))

    def _commit(self, game_id: str, command: Callable[[Loaded], Step[Any]]) -> tuple[Event, ...]:
        for _ in range(self._max_retries):
            loaded = self.load(game_id)
            step = command(loaded)
            try:
                self.store.append(
                    game_id, loaded.state.core.last_seq, step.events, ended=step.state.core.ended
                )
            except ConcurrencyError:
                continue  # someone else appended; recompute from the fresh log
            self._cache[game_id] = Loaded(
                self.store.game(game_id) if step.events else loaded.record,
                loaded.engine,
                step.state,
                loaded.log + step.events,
            )
            return step.events
        raise ConcurrencyError(f"gave up on game {game_id} after {self._max_retries} conflicts")
