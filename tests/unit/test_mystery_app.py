from __future__ import annotations

import random
from pathlib import Path
from typing import Any

from mafia.app.prep import ARCHITECT, AUDITOR, PrepService, Stage
from mafia.app.table import TableApp
from mafia.engine.requests import Answer
from mafia.engine.runtime import FixedClock
from mafia.harness.ports import HumanInputKind
from mafia.modes.mystery.brief import SECTIONS
from mafia.modes.mystery.events import CardRevealed, Speech
from mafia.personas import load_personas
from mafia.service import GameService
from mafia.store.db import connect
from mafia.store.events import EventStore
from mafia.store.harness import HarnessStore
from mafia.store.scenarios import ScenarioRecord, ScenarioStore
from tests.mystery_factory import CHARS, scenario_data
from tests.unit.test_prep import GOOD_ANSWERS, PASS_AUDIT, solve_all


def secret_data() -> dict[str, Any]:
    """Factory scenario with a unique token in every secret, to detect leaks by search."""
    data = scenario_data()
    for ch in data["characters"]:
        ch["sheet"]["secret"]["what"] = f"SECRET_OF_{ch['id']}"
    data["narration"]["truth_reveal"] = "TRUTH_TOKEN " * 80
    data["narration"]["ending_caught"] = "ENDING_TOKEN"
    data["narration"]["ending_escaped"] = "ENDING_TOKEN"
    return data


def make_app(path: Path, *, library: dict[str, Any] | None = None) -> TableApp:
    conn = connect(path)
    clock = FixedClock()
    store = ScenarioStore(conn)
    if library is not None:
        store.save(
            ScenarioRecord(
                scenario_id="s-test",
                status="verified",
                created_at=0,
                title=library["concept"]["title"],
                tone="본격",
                brief={},
                scenario=library,
                report={},
            )
        )
    return TableApp(
        GameService(EventStore(conn), clock),
        HarnessStore(conn),
        clock,
        load_personas(),
        PrepService(store, clock),
    )


def mode_state(app: TableApp) -> Any:
    return app.service.load(app.game_id()).state.mode


def human_seat(app: TableApp) -> int:
    return next(iter(app.service.load(app.game_id()).state.core.human_seats))


def ai_turn(app: TableApp, seat: int, rng: random.Random) -> None:
    text = app.seat_turn(seat)
    own = mode_state(app).character_of[seat]
    for cid in CHARS:
        if cid != own:
            assert f"SECRET_OF_{cid}" not in text, f"seat {seat} saw {cid}'s secret"
    assert f"SECRET_OF_{own}" in text
    request = next(r for r in app.service.open_requests(app.game_id()) if r.seat == seat)
    match request.answer:
        case Answer.SPEECH:
            hand = [
                k for k in mode_state(app).hands.get(seat, ()) if k not in mode_state(app).revealed
            ]
            reveal = hand[:1] if hand and rng.random() < 0.3 else []
            result = app.seat_submit(
                seat, Answer.SPEECH, speech="제 생각은 이렇습니다", reveal=reveal
            )
        case Answer.TARGET:
            result = app.seat_submit(seat, Answer.TARGET, target=rng.choice(request.options))
        case Answer.APPROVAL:
            result = app.seat_submit(seat, Answer.APPROVAL, approve=True)
    assert result.startswith("접수"), result


def test_library_scenario_game_runs_without_leaks(tmp_path: Path) -> None:
    app = make_app(tmp_path / "g.db", library=secret_data())
    rng = random.Random(5)
    result = app.new_game(mode="mystery", seed="mm-app")
    me = human_seat(app)
    mine = mode_state(app).character_of[me]
    assert "머더 미스터리" in result.display and f"SECRET_OF_{mine}" in result.display
    displays = [result.display]
    for _ in range(400):
        if result.next.type == "ended":
            break
        if result.next.type == "wake":
            for agent in result.next.agents:
                ai_turn(app, int(agent.removeprefix("seat-")), rng)
        else:
            request = next(r for r in app.service.open_requests(app.game_id()) if r.seat == me)
            if request.answer is Answer.TARGET and request.option_labels:
                command = f":go {request.option_labels[0]}"
            elif request.answer is Answer.TARGET:
                command = f":vote {request.options[0]}"
            else:
                hand = [
                    k
                    for k in mode_state(app).hands.get(me, ())
                    if k not in mode_state(app).revealed
                ]
                command = f":show {hand[0]} 제 카드입니다" if hand else "저는 아무것도 몰라요"
            assert app.handle_human_input(command).kind is HumanInputKind.SUBMITTED, command
        result = app.advance()
        displays.append(result.display)
    assert result.next.type == "ended"
    ended_at = next(i for i, d in enumerate(displays) if "TRUTH_TOKEN" in d)
    for display in displays[:ended_at]:
        for cid in CHARS:
            if cid != mine:
                assert f"SECRET_OF_{cid}" not in display
        assert "ENDING_TOKEN" not in display
    assert "진범은" in displays[ended_at] and "개인 목표" in displays[ended_at]
    log = app.service.load(app.game_id()).log
    assert any(e.kind == CardRevealed.KIND and e.body_as(CardRevealed).seat == me for e in log)
    assert any(e.kind == Speech.KIND for e in log)
    assert app.prep().store.next_unplayed() is None  # the scenario is marked played


def test_human_commands_for_mystery(tmp_path: Path) -> None:
    app = make_app(tmp_path / "g.db", library=scenario_data())
    app.new_game(mode="mystery", seed="cmd")
    assert app.handle_human_input(":role").kind is HumanInputKind.INFO
    assert "손에 든 카드" in app.handle_human_input(":role").message


def test_empty_library_prepares_then_starts_the_game(tmp_path: Path) -> None:
    app = make_app(tmp_path / "g.db")
    result = app.new_game(mode="mystery", human_name="상현")
    assert result.next.agents == (ARCHITECT,) and "새로 만듭니다" in result.display
    prep = app.prep()
    data = scenario_data()
    for section in SECTIONS:
        prep.submit_section(ARCHITECT, section, data[section])
    assert prep.check(ARCHITECT).startswith("정적 검증 통과")
    assert app.advance().next.agents == (AUDITOR,)
    prep.submit_audit(AUDITOR, PASS_AUDIT)
    solve_all(prep, GOOD_ANSWERS)
    started = app.advance()
    assert "시나리오 완성" in started.display and "머더 미스터리를 시작합니다" in started.display
    assert started.next.type in ("wake", "human")
    assert prep.active() is None
    assert app.service.load(app.game_id()).state.core.mode == "mystery"
    assert prep.store.get(next(iter([r.scenario_id for r in prep.store.list()]))) is not None
    _ = Stage
