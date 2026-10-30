"""`mafia tail`: follow a game's log in the terminal, from a chosen point of view.

Reads the database only; never writes. Safe to run while a game is being played.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from rich.console import Console
from rich.table import Table
from rich.text import Text

from mafia import paths
from mafia.engine.core_events import PhaseChanged, SeatInfo, SeatsAnnounced
from mafia.engine.engine import Engine
from mafia.engine.events import Event
from mafia.engine.runtime import SystemClock
from mafia.engine.visibility import Viewer, can_see
from mafia.modes.original.events import MafiaChat, Speech
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

    def switch(self, game_id: str) -> None:
        self.game_id = game_id
        self.events = []
        if self.view.god:
            label = "신의 시점"
        elif self.view.seat:
            label = f"{self.view.seat}번 좌석 시점"
        else:
            label = "공개 시점"
        self.console.rule(Text(f"게임 {game_id} · {label}", style="bold"))

    def feed(self, new: Sequence[Event], mode: str) -> None:
        """Append events to the known log and print those this view may see."""
        if not new:
            return
        self.events.extend(new)
        seats = _seats(self.events)
        names = name_lookup(seats)
        viewer = self._viewer()
        for event in new:
            if not can_see(viewer, event.visibility):
                continue
            line = narrate_terminal(event, mode, names, god=self.view.god)
            if line is None:
                continue
            if event.kind == PhaseChanged.KIND:
                self.console.rule(Text(line.strip("─ "), style="bold cyan"))
            else:
                self.console.print(colorize(line, seats), highlight=False)

    def poll(self, game_id: str | None) -> None:
        if game_id is None:
            return
        if game_id != self.game_id:
            self.switch(game_id)
        try:
            record = self.store.game(game_id)
        except GameNotFound:
            return
        after = self.events[-1].seq if self.events else 0
        self.feed(self.store.load(game_id, after_seq=after), record.mode)


def pause_for(event: Event) -> float:
    """Seconds a human needs to read this event at speed 1."""
    if event.kind in (Speech.KIND, MafiaChat.KIND):
        text = str(event.data.get("text") or "")
        return 0.8 + len(text) / 35
    if event.kind == PhaseChanged.KIND:
        return 1.5
    return 0.3


def replay(view: View, *, game_id: str | None, speed: float) -> int:
    """Print a stored game from the start, paced for reading. `speed=0` prints instantly."""
    store = EventStore(connect(paths.game_db_path()))
    game_id = game_id or store.active_game()
    if game_id is None:
        Console().print("저장된 게임이 없습니다.")
        return 1
    record = store.game(game_id)
    tail = Tail(store, view, Console())
    tail.switch(game_id)
    try:
        for event in store.load(game_id):
            tail.feed([event], record.mode)
            if speed > 0 and can_see(tail._viewer(), event.visibility):  # pyright: ignore[reportPrivateUsage]
                time.sleep(pause_for(event) / speed)
    except KeyboardInterrupt:
        pass
    return 0


def list_games() -> int:
    store = EventStore(connect(paths.game_db_path()))
    active = store.active_game()
    table = Table("", "게임", "모드", "시작", "이벤트", "상태")
    for g in store.list_games():
        started = datetime.fromtimestamp(g.created_at).strftime("%m-%d %H:%M")
        table.add_row(
            "▶" if g.game_id == active else "",
            g.game_id,
            g.mode,
            started,
            str(g.last_seq),
            "종료" if g.ended else "진행 중",
        )
    Console().print(table)
    return 0


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


def list_scenarios(*, spoil: bool) -> int:
    from mafia.modes.mystery.scenario import Scenario
    from mafia.store.scenarios import ScenarioStore

    store = ScenarioStore(connect(paths.game_db_path()))
    console = Console()
    table = Table("시나리오", "제목", "톤", "상태", "수정 차수")
    records = store.list()
    for r in records:
        status = {"verified": "준비 완료", "played": "플레이함", "rejected": "폐기"}.get(
            r.status, r.status
        )
        table.add_row(r.scenario_id, r.title, r.tone, status, str(r.report.get("revisions", "")))
    console.print(table)
    if spoil:
        for r in records:
            if r.scenario is None:
                continue
            s = Scenario.model_validate(r.scenario)
            culprit = s.character(s.truth.culprit_id)
            console.rule(Text(f"{r.title} · 진상", style="bold red"))
            console.print(Text(f"범인: {culprit.name} ({culprit.occupation})\n{s.truth.summary}"))
    return 0
