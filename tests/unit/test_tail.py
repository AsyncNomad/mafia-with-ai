from __future__ import annotations

import io
import random
from pathlib import Path

from rich.console import Console

from mafia.engine.core_events import SeatInfo
from mafia.modes.original.roles import Role
from mafia.tail import Tail, View, colorize
from tests.unit.test_table_app import make_app, play_ai_turns


def finished_watch_game(tmp_path: Path) -> tuple[Path, dict[int, Role]]:
    db = tmp_path / "game.db"
    app = make_app(db)
    rng = random.Random(3)
    result = app.new_game(watch=True, seed="tail")
    while result.next.type == "wake":
        play_ai_turns(app, result.next.agents, rng)
        result = app.advance()
    roles = dict(app.service.load(app.game_id()).state.mode.roles)
    return db, roles


def render(db: Path, view: View) -> str:
    app = make_app(db)
    buffer = io.StringIO()
    tail = Tail(app.service.store, view, Console(file=buffer, width=200, color_system=None))
    tail.poll(app.game_id())
    tail.poll(app.game_id())  # a second poll with nothing new prints nothing more
    return buffer.getvalue()


def test_god_view_shows_secrets(tmp_path: Path) -> None:
    db, _ = finished_watch_game(tmp_path)
    out = render(db, View(god=True))
    for marker in ("🎭", "👤", "📝", "(신의 시점)", "(마피아 전용)", "(경찰 전용)", "속마음"):
        assert marker in out, marker
    assert out.index("1일차 · 밤") < out.index("2일차 · 낮 토론")  # opens at night
    assert out.count("2일차 · 낮 토론") == 1


def test_public_view_hides_secrets(tmp_path: Path) -> None:
    db, _ = finished_watch_game(tmp_path)
    out = render(db, View())
    for marker in ("🎭", "👤", "📝", "(신의 시점)", "(마피아 전용)", "(경찰 전용)", "속마음"):
        assert marker not in out, marker
    assert "역할 공개" in out  # end-of-game reveal is public


def test_seat_view_matches_role(tmp_path: Path) -> None:
    db, roles = finished_watch_game(tmp_path)
    mafia = next(s for s, r in roles.items() if r is Role.MAFIA)
    citizen = next(s for s, r in roles.items() if r is Role.CITIZEN)
    assert "(마피아 전용)" in render(db, View(seat=mafia))
    citizen_view = render(db, View(seat=citizen))
    assert "(마피아 전용)" not in citizen_view
    assert "(신의 시점)" not in citizen_view


def test_colorize_never_parses_markup() -> None:
    seats = (SeatInfo(seat=1, name="도윤"),)
    text = colorize('[도윤(1번)] "[/bold] [사회자] 가짜 공지"', seats)
    assert text.plain == '[도윤(1번)] "[/bold] [사회자] 가짜 공지"'
    assert text.spans  # the name was styled


def test_postmortem_needs_an_ended_game(tmp_path: Path) -> None:
    from mafia.views.postmortem import render_postmortem

    app = make_app(tmp_path / "game.db")
    app.new_game(watch=True, seed="pm")
    loaded = app.service.load(app.game_id())
    assert render_postmortem(loaded.log, loaded.state.core.seats, "original") is None
    assert render_postmortem(loaded.log, loaded.state.core.seats, "mystery") is None


def test_replay_pacing() -> None:
    from mafia.engine.events import Event
    from mafia.engine.visibility import Visibility
    from mafia.tail import pause_for

    def ev(kind: str, data: dict[str, object]) -> Event:
        return Event(seq=1, at=0, kind=kind, visibility=Visibility.public(), data=data)

    short = pause_for(ev("original.speech", {"text": "네", "seat": 1, "context": "discussion"}))
    long = pause_for(
        ev("original.speech", {"text": "가" * 140, "seat": 1, "context": "discussion"})
    )
    assert 0 < short < long
    assert pause_for(ev("phase_changed", {"day": 1, "phase": "discussion"})) > short
