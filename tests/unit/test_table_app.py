from __future__ import annotations

import random
from pathlib import Path

import pytest

from mafia.app.table import NoActiveGame, TableApp
from mafia.engine.core_events import ActionSubmitted
from mafia.engine.requests import Answer
from mafia.engine.runtime import FixedClock
from mafia.harness.ports import HumanInputKind
from mafia.modes.original.events import Speech
from mafia.modes.original.roles import Role
from mafia.personas import load_personas
from mafia.service import GameService
from mafia.store.db import connect
from mafia.store.events import EventStore
from mafia.store.harness import HarnessStore

SECRET_MARKERS = {
    "(신의 시점)": None,  # never, for anyone
    "(마피아 전용)": Role.MAFIA,
    "(경찰 전용)": Role.POLICE,
}


def make_app(path: Path) -> TableApp:
    conn = connect(path)
    clock = FixedClock()
    return TableApp(
        GameService(EventStore(conn), clock), HarnessStore(conn), clock, load_personas()
    )


@pytest.fixture
def app(tmp_path: Path) -> TableApp:
    return make_app(tmp_path / "game.db")


def human_seat(app: TableApp) -> int:
    loaded = app.service.load(app.service.store.active_game() or "")
    return next(iter(loaded.state.core.human_seats))


def human_role(app: TableApp) -> Role:
    loaded = app.service.load(app.service.store.active_game() or "")
    return loaded.state.mode.roles[human_seat(app)]


def play_ai_turns(app: TableApp, agents: tuple[str, ...], rng: random.Random) -> None:
    for agent in agents:
        seat = int(agent.removeprefix("seat-"))
        text = app.seat_turn(seat)
        assert "## 지금 할 일" in text
        request = next(r for r in app.service.open_requests(app.game_id()) if r.seat == seat)
        match request.answer:
            case Answer.SPEECH:
                result = app.seat_submit(
                    seat, Answer.SPEECH, speech=f"{seat}번 생각입니다", private_note="메모"
                )
            case Answer.TARGET:
                result = app.seat_submit(
                    seat, Answer.TARGET, target=rng.choice([*request.options, None])
                )
            case Answer.APPROVAL:
                result = app.seat_submit(seat, Answer.APPROVAL, approve=rng.choice([True, False]))
        assert result.startswith("접수"), result


def assert_display_is_clean(display: str, role: Role | None) -> None:
    for marker, allowed_for in SECRET_MARKERS.items():
        if allowed_for is None or role is not allowed_for:
            assert marker not in display, f"{marker} leaked to a {role} viewer:\n{display}"


class TestNewGame:
    def test_human_game_intro(self, app: TableApp) -> None:
        result = app.new_game(human_name="상현", seed="intro")
        seat = human_seat(app)
        assert "상현" in result.display
        assert "당신의 역할" in result.display
        assert result.next.type in ("wake", "human")
        loaded = app.service.load(app.game_id())
        names = [s.name for s in loaded.state.core.seats]
        assert len(set(names)) == 8 and names[seat - 1] == "상현"
        assert not any(n.startswith("상") for i, n in enumerate(names) if i != seat - 1)

    def test_human_seat_varies_with_seed(self, tmp_path: Path) -> None:
        seats: set[int] = set()
        for i in range(12):
            app = make_app(tmp_path / f"g{i}.db")
            app.new_game(seed=f"seat-{i}")
            seats.add(human_seat(app))
        assert len(seats) > 1

    def test_no_game_yet(self, app: TableApp) -> None:
        with pytest.raises(NoActiveGame):
            app.advance()
        assert app.handle_human_input("안녕").kind is HumanInputKind.PASSTHROUGH
        assert not app.is_awaiting("seat-1")


class TestSeats:
    def test_seat_sees_only_its_own_persona(self, app: TableApp) -> None:
        app.new_game(watch=True, seed="persona")
        loaded = app.service.load(app.game_id())
        names = {s.seat: s.name for s in loaded.state.core.seats}
        pool = {p.name: p for p in load_personas()}
        text = app.seat_turn(1)
        assert pool[names[1]].personality in text
        for seat in range(2, 9):
            assert pool[names[seat]].personality not in text

    def test_wrong_submit_tool_rejected(self, app: TableApp) -> None:
        result = app.new_game(watch=True, seed="tool")
        seat = int(result.next.agents[0].removeprefix("seat-"))
        assert app.seat_submit(seat, Answer.TARGET, target=1).startswith("거부됨")
        assert app.seat_submit(seat, Answer.SPEECH, speech="안녕하세요").startswith("접수")

    def test_not_your_turn(self, app: TableApp) -> None:
        result = app.new_game(watch=True, seed="turn")
        idle = next(s for s in range(1, 9) if f"seat-{s}" not in result.next.agents)
        assert "차례가 아닙니다" in app.seat_submit(idle, Answer.SPEECH, speech="끼어들기")
        assert not app.is_awaiting(f"seat-{idle}")

    def test_human_seat_is_never_an_agent(self, app: TableApp) -> None:
        app.new_game(seed="human-agent")
        seat = human_seat(app)
        assert not app.is_awaiting(f"seat-{seat}")
        assert "사람 플레이어" in app.seat_turn(seat)
        assert "사람 플레이어" in app.seat_submit(seat, Answer.SPEECH, speech="x")

    def test_abandoned_turn_gets_fallback(self, app: TableApp) -> None:
        result = app.new_game(watch=True, seed="abandon")
        agent = result.next.agents[0]
        assert app.is_awaiting(agent)
        app.record_agent_started("a1", agent)
        app.record_agent_finished("a1", agent)  # the subagent stopped without submitting
        app.advance()
        loaded = app.service.load(app.game_id())
        fallbacks = [
            e.body_as(ActionSubmitted) for e in loaded.log if e.kind == ActionSubmitted.KIND
        ]
        assert fallbacks and fallbacks[0].fallback and fallbacks[0].seat == int(agent[5:])


class TestHumanInput:
    def _until_human_turn(self, app: TableApp, rng: random.Random) -> None:
        for _ in range(200):
            result = app.advance()
            if result.next.type != "wake":
                return
            play_ai_turns(app, result.next.agents, rng)
        raise AssertionError("human never got a turn")

    def test_info_commands_do_not_need_a_turn(self, app: TableApp) -> None:
        app.new_game(seed="info")
        assert app.handle_human_input(":help").kind is HumanInputKind.INFO
        role = app.handle_human_input(":role")
        assert role.kind is HumanInputKind.INFO and human_role(app).label in role.message
        assert app.handle_human_input(":who").kind is HumanInputKind.INFO
        assert app.handle_human_input(":nonsense").kind is HumanInputKind.REJECTED
        assert app.handle_human_input("/mafia status").kind is HumanInputKind.PASSTHROUGH

    def test_free_text_is_the_humans_speech_verbatim(self, app: TableApp) -> None:
        app.new_game(seed="speech")
        self._until_human_turn(app, random.Random(0))
        text = '  근거를 보면… 민지 님이 "이상"해요  '
        outcome = app.handle_human_input(text)
        assert outcome.kind is HumanInputKind.SUBMITTED
        loaded = app.service.load(app.game_id())
        speeches = [e.body_as(Speech) for e in loaded.log if e.kind == Speech.KIND]
        mine = [s for s in speeches if s.seat == human_seat(app)]
        assert mine[-1].text == text.strip()

    def test_vote_by_name_and_wrong_command_for_phase(self, app: TableApp) -> None:
        app.new_game(seed="vote")
        rng = random.Random(1)
        for _ in range(500):
            result = app.advance()
            if result.next.type == "wake":
                play_ai_turns(app, result.next.agents, rng)
                continue
            assert result.next.type == "human"
            request = next(
                r for r in app.service.open_requests(app.game_id()) if r.seat == human_seat(app)
            )
            if request.answer is Answer.TARGET:
                assert app.handle_human_input(":yes").kind is HumanInputKind.REJECTED
                loaded = app.service.load(app.game_id())
                name = loaded.state.core.name_of(request.options[0])
                assert app.handle_human_input(f":vote {name}").kind is HumanInputKind.SUBMITTED
                return
            app.handle_human_input(":pass")
        raise AssertionError("no target request for the human")

    def test_ask_is_never_a_speech(self, app: TableApp) -> None:
        app.new_game(seed="ask")
        self._until_human_turn(app, random.Random(2))
        before = len(app.service.load(app.game_id()).log)
        outcome = app.handle_human_input(":ask 지금 게임 진행 중인 거 맞아?")
        assert outcome.kind is HumanInputKind.PASSTHROUGH
        assert len(app.service.load(app.game_id()).log) == before  # nothing was submitted

    def test_chat_outside_the_humans_turn_passes_through(self, app: TableApp) -> None:
        result = app.new_game(seed="chat")
        if result.next.type == "wake":
            assert app.handle_human_input("다음은 누구 차례야?").kind is HumanInputKind.PASSTHROUGH
            assert app.handle_human_input(":pass").kind is HumanInputKind.REJECTED


@pytest.mark.parametrize("seed", [f"full-{i}" for i in range(12)])
def test_full_human_game_never_leaks_into_the_chat(tmp_path: Path, seed: str) -> None:
    app = make_app(tmp_path / "game.db")
    rng = random.Random(seed)
    result = app.new_game(seed=seed)
    role = human_role(app)
    displays = [result.display]
    for _ in range(1_000):
        if result.next.type == "ended":
            break
        if result.next.type == "wake":
            play_ai_turns(app, result.next.agents, rng)
        else:
            request = next(
                r for r in app.service.open_requests(app.game_id()) if r.seat == human_seat(app)
            )
            command = {
                Answer.SPEECH: "저는 시민입니다. 믿어 주세요.",
                Answer.TARGET: f":vote {rng.choice(request.options)}"
                if request.options
                else ":pass",
                Answer.APPROVAL: rng.choice([":yes", ":no", ":pass"]),
            }[request.answer]
            assert app.handle_human_input(command).kind is HumanInputKind.SUBMITTED
        result = app.advance()
        displays.append(result.display)
    assert result.next.type == "ended"
    assert "게임 종료" in displays[-1] and "역할 공개" in displays[-1]
    for display in displays:
        assert_display_is_clean(display, role)


def test_watch_game_shows_only_public_events(tmp_path: Path) -> None:
    app = make_app(tmp_path / "game.db")
    rng = random.Random(7)
    result = app.new_game(watch=True, seed="watch")
    displays = [result.display]
    while result.next.type == "wake":
        play_ai_turns(app, result.next.agents, rng)
        result = app.advance()
        displays.append(result.display)
    assert result.next.type == "ended"
    for display in displays:
        assert_display_is_clean(display, None)
        assert "당신의 역할" not in display


def test_postmortem_appears_once_when_the_game_ends(tmp_path: Path) -> None:
    app = make_app(tmp_path / "game.db")
    rng = random.Random(11)
    result = app.new_game(watch=True, seed="review")
    displays = [result.display]
    while result.next.type == "wake":
        assert "## 🔍 복기" not in result.display
        play_ai_turns(app, result.next.agents, rng)
        result = app.advance()
        displays.append(result.display)
    final = displays[-1]
    assert final.count("## 🔍 복기") == 1
    assert "밤마다 실제로 일어난 일" in final
    assert "각자의 마지막 속마음" in final
    assert "## 🔍 복기" not in app.advance().display  # not repeated


def test_preparation_takes_over_advance_until_done(tmp_path: Path) -> None:
    from mafia.app.prep import PrepService
    from mafia.store.scenarios import ScenarioStore

    conn = connect(tmp_path / "game.db")
    clock = FixedClock()
    app = TableApp(
        GameService(EventStore(conn), clock),
        HarnessStore(conn),
        clock,
        load_personas(),
        preparation=PrepService(ScenarioStore(conn), clock),
    )
    result = app.prepare_mystery("남극 기지")
    assert result.next.type == "wake" and result.next.agents == ("mystery-architect",)
    assert "설계" in result.display
    assert app.is_awaiting("mystery-architect")
    assert not app.is_awaiting("mystery-auditor")
    assert not app.is_awaiting("seat-1")  # no game is running
    assert app.advance().next.agents == ("mystery-architect",)  # still designing


class TestBackgroundAgents:
    """VSCode runs subagents in the background: the driver may call advance while they run."""

    def test_running_seat_is_not_woken_twice(self, app: TableApp) -> None:
        result = app.new_game(watch=True, seed="bg")
        agent = result.next.agents[0]
        app.record_agent_started("run-1", agent)
        assert not app.is_awaiting(agent)  # a duplicate spawn would be denied
        assert app.needs_submission(agent)  # but it still owes an answer
        again = app.advance()
        assert again.next.type == "wait"

    def test_finished_run_without_submission_falls_back(self, app: TableApp) -> None:
        result = app.new_game(watch=True, seed="bg2")
        agent = result.next.agents[0]
        app.record_agent_started("run-1", agent)
        app.record_agent_finished("run-1", agent)
        nxt = app.advance()
        assert nxt.next.type in ("wake", "human")
        assert agent not in nxt.next.agents or nxt.next.agents.count(agent) == 1

    def test_stale_runs_are_ignored(self, tmp_path: Path) -> None:
        app = make_app(tmp_path / "g.db")
        result = app.new_game(watch=True, seed="stale")
        agent = result.next.agents[0]
        app.record_agent_started("ghost", agent)
        assert not app.is_awaiting(agent)
        app.clock = FixedClock(start=10_000_000)  # far in the future: the run is presumed dead
        assert app.is_awaiting(agent)


def test_game_in_progress_takes_priority_over_preparation(tmp_path: Path) -> None:
    from mafia.app.prep import PrepService
    from mafia.store.scenarios import ScenarioStore

    conn = connect(tmp_path / "game.db")
    clock = FixedClock()
    app = TableApp(
        GameService(EventStore(conn), clock),
        HarnessStore(conn),
        clock,
        load_personas(),
        preparation=PrepService(ScenarioStore(conn), clock),
    )
    prepared = app.prepare_mystery("남극 기지")
    assert prepared.next.agents == ("mystery-architect",)
    game = app.new_game(watch=True, seed="priority")
    assert all(a.startswith("seat-") for a in game.next.agents)  # the game, not the architect
    assert all(a.startswith("seat-") for a in app.advance().next.agents)
