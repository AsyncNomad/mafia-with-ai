"""`mafia tail`: follow a game's log in the terminal, from a chosen point of view.

Reads the database only; never writes. Safe to run while a game is being played.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from rich.console import Console
from rich.text import Text

from mafia import paths
from mafia.engine.core_events import PhaseChanged, SeatInfo, SeatsAnnounced
from mafia.engine.engine import Engine
from mafia.engine.events import Event
from mafia.engine.runtime import SystemClock
from mafia.engine.visibility import Viewer, can_see
from mafia.modes.registry import build_mode
from mafia.store.db import connect
from mafia.store.events import EventStore, GameNotFound
from mafia.views.god import narrate_terminal
from mafia.views.narration import name_lookup

SEAT_COLORS = ("red", "dark_orange", "yellow", "green", "blue", "magenta", "bright_cyan", "white")


@dataclass(frozen=True, slots=True)
class View:
    god: bool = False
    seat: int | None = None  # None and not god: public view


def _seats(events: list[Event]) -> tuple[SeatInfo, ...]:
    return next(
        (e.body_as(SeatsAnnounced).seats for e in events if e.kind == SeatsAnnounced.KIND), ()
    )


def colorize(line: str, seats: tuple[SeatInfo, ...]) -> Text:
    """Plain text in, names coloured out. Never parses markup: speeches may contain '['."""
    text = Text(line)
    plain = name_lookup(seats)
    for info in seats:
        color = SEAT_COLORS[(info.seat - 1) % len(SEAT_COLORS)]
        text.highlight_words([plain(info.seat)], style=f"bold {color}")
    return text


class Tail:
    def __init__(self, store: EventStore, view: View, console: Console) -> None:
        self.store = store
        self.view = view
        self.console = console
        self.game_id: str | None = None
        self.events: list[Event] = []

    def _viewer(self) -> Viewer:
        if self.view.god:
            return Viewer.god()
        if self.view.seat is None or self.game_id is None:
            return Viewer()
        record = self.store.game(self.game_id)
        engine: Engine[object] = Engine(build_mode(record.mode, record.config), SystemClock())
        state = engine.fold(self.events)
        return engine.mode.viewer(state.mode, state.core, self.view.seat)

    def poll(self, game_id: str | None) -> None:
        if game_id is None:
            return
        if game_id != self.game_id:
            self.game_id = game_id
            self.events = []
            label = (
                "신의 시점"
                if self.view.god
                else f"{self.view.seat}번 좌석 시점"
                if self.view.seat
                else "공개 시점"
            )
            self.console.rule(f"[bold]게임 {game_id} · {label}[/]")
        try:
            record = self.store.game(game_id)
        except GameNotFound:
            return
        new = self.store.load(game_id, after_seq=self.events[-1].seq if self.events else 0)
        if not new:
            return
        self.events.extend(new)
        seats = _seats(self.events)
        names = name_lookup(seats)
        viewer = self._viewer()
        for event in new:
            if not can_see(viewer, event.visibility):
                continue
            line = narrate_terminal(event, record.mode, names, god=self.view.god)
            if line is None:
                continue
            if event.kind == PhaseChanged.KIND:
                self.console.rule(Text(line.strip("─ "), style="bold cyan"))
            else:
                self.console.print(colorize(line, seats), highlight=False)


def run(view: View, *, game_id: str | None, follow: bool, interval: float = 0.5) -> int:
    store = EventStore(connect(paths.game_db_path()))
    tail = Tail(store, view, Console())
    try:
        while True:
            tail.poll(game_id or store.active_game())
            if not follow:
                return 0
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0
