from __future__ import annotations

from collections import Counter

import pytest

from mafia.engine.core_events import GameEnded, RequestOpened
from mafia.engine.requests import SeatRequest
from mafia.engine.visibility import Scope, Visibility
from mafia.modes.original.config import OriginalConfig
from mafia.modes.original.events import (
    ConfirmationRevealed,
    DiscussionRoundStarted,
    InvestigationResult,
    MafiaChat,
    MafiaTargetChosen,
    NightResolved,
    NominationRevealed,
    PeacefulNight,
    PlayerDied,
    RoleAssigned,
    RolesRevealed,
    Speech,
)
from mafia.modes.original.roles import Role
from mafia.modes.original.rules import Kind
from tests.table import Table


def vote_for(target: int):
    def choose(request: SeatRequest) -> dict[str, object]:
        return {"target": target} if target in request.options else {}

    return choose


def nominate_and_execute(table: Table, target: int) -> None:
    table.pass_until("nomination")
    table.answer_all(vote_for(target))
    table.answer(target, speech="저는 아닙니다")
    table.answer_all(lambda _r: {"approve": True})


class TestSetup:
    def test_role_counts_and_private_cards(self) -> None:
        table = Table()
        assert Counter(table.mode.roles.values()) == {
            Role.MAFIA: 2,
            Role.POLICE: 1,
            Role.DOCTOR: 1,
            Role.CITIZEN: 4,
        }
        for event in table.events(RoleAssigned):
            card = event.body_as(RoleAssigned)
            assert event.visibility == Visibility.to_seats(card.seat)

    def test_mafia_know_each_other(self) -> None:
        table = Table()
        mafia = table.seats_with(Role.MAFIA)
        for card in table.bodies(RoleAssigned):
            if card.role is Role.MAFIA:
                assert set(card.teammates) == set(mafia) - {card.seat}
            else:
                assert card.teammates == ()

    def test_assignment_depends_only_on_seed(self) -> None:
        assert Table("a").mode.roles == Table("a").mode.roles
        assignments = {tuple(Table(f"s{i}").mode.roles.items()) for i in range(10)}
        assert len(assignments) > 1

    @pytest.mark.parametrize(
        "roles",
        [
            {Role.MAFIA: 4, Role.CITIZEN: 4},
            {Role.MAFIA: 0, Role.CITIZEN: 8},
            {Role.MAFIA: 2, Role.CITIZEN: 5},
            {Role.MAFIA: 2, Role.POLICE: 2, Role.CITIZEN: 4},
        ],
    )
    def test_invalid_role_configs_rejected(self, roles: dict[Role, int]) -> None:
        with pytest.raises(ValueError):
            OriginalConfig(roles=roles)


class TestDay:
    def test_discussion_is_sequential_and_covers_everyone_each_round(self) -> None:
        table = Table(config=OriginalConfig(discussion_rounds=2, start_with_night=False))
        speakers: list[int] = []
        while table.mode.phase.value == "discussion":
            (request,) = table.requests()  # exactly one speaker at a time
            assert request.kind == Kind.DISCUSSION
            speakers.append(request.seat)
            table.answer(request.seat, speech=f"{request.seat}번 발언")
        assert sorted(speakers) == sorted(list(range(1, 9)) * 2)
        rounds = table.bodies(DiscussionRoundStarted)
        assert [r.round for r in rounds] == [1, 2]
        assert speakers == list(rounds[0].order) + list(rounds[1].order)

    def test_speech_is_public_and_pass_is_recorded(self) -> None:
        table = Table()
        first = table.requests()[0].seat
        table.answer(first)  # pass
        speech = table.events(Speech)[0]
        assert speech.visibility == Visibility.public()
        assert speech.body_as(Speech).text is None

    def test_tied_nomination_goes_to_night(self) -> None:
        table = Table()
        table.pass_until("nomination")
        a, b = 1, 2
        table.answer_all(
            lambda r: {"target": a if r.seat % 2 else b} if r.seat not in (a, b) else {}
        )
        reveal = table.bodies(NominationRevealed)[-1]
        assert reveal.nominee is None
        assert table.mode.phase.value in ("mafia_chat", "night_actions")
        assert not table.events(PlayerDied)

    def test_nomination_reveal_is_simultaneous(self) -> None:
        table = Table()
        table.pass_until("nomination")
        before = len(table.events(NominationRevealed))
        requests = table.requests()
        for request in requests[:-1]:
            table.answer(request.seat, advance=False, target=request.options[0])
            assert len(table.events(NominationRevealed)) == before
        table.answer(requests[-1].seat, target=requests[-1].options[0])
        assert len(table.events(NominationRevealed)) == before + 1

    def test_execution_requires_more_yes_than_no(self) -> None:
        table = Table()
        table.pass_until("nomination")
        target = table.seats_with(Role.CITIZEN)[0]
        table.answer_all(vote_for(target))
        table.answer(target, speech="억울합니다")
        voters = [r.seat for r in table.requests()]
        assert target not in voters
        half = len(voters) // 2
        table.answer_all(lambda r: {"approve": voters.index(r.seat) < half})  # 3 yes, 4 no
        assert table.bodies(ConfirmationRevealed)[-1].approved is False
        assert target in table.mode.alive

    def test_approved_execution_kills(self) -> None:
        table = Table()
        target = table.seats_with(Role.CITIZEN)[0]
        nominate_and_execute(table, target)
        death = table.bodies(PlayerDied)[-1]
        assert (death.seat, death.cause, death.role) == (target, "execution", None)
        assert target not in table.mode.alive

    def test_role_revealed_on_death_when_configured(self) -> None:
        table = Table(config=OriginalConfig(reveal_role_on_death=True))
        target = table.seats_with(Role.POLICE)[0]
        nominate_and_execute(table, target)
        assert table.bodies(PlayerDied)[-1].role is Role.POLICE


class TestNight:
    def _to_night_actions(self, table: Table) -> None:
        table.pass_until("night_actions")

    def test_mafia_chat_is_team_only(self) -> None:
        table = Table()
        table.pass_until("mafia_chat")
        mafia = table.seats_with(Role.MAFIA)
        requests = table.requests()
        assert sorted(r.seat for r in requests) == mafia  # simultaneous, not one by one
        assert all(r.covert for r in requests)
        request = requests[0]
        table.answer(request.seat, advance=False, speech="오늘은 3번을 노리자")
        chat = table.events(MafiaChat)[-1]
        assert chat.visibility == Visibility.to_team("mafia")
        citizen = table.seats_with(Role.CITIZEN)[0]
        assert chat.seq not in {e.seq for e in table.context(citizen).events}
        assert chat.seq in {e.seq for e in table.context(mafia[0]).events}

    def test_night_requests_and_options(self) -> None:
        table = Table()
        self._to_night_actions(table)
        mafia = set(table.seats_with(Role.MAFIA))
        kinds = {r.seat: r.kind for r in table.requests()}
        assert kinds == {
            **{m: Kind.MAFIA_KILL for m in mafia},
            table.seats_with(Role.POLICE)[0]: Kind.INVESTIGATE,
            table.seats_with(Role.DOCTOR)[0]: Kind.PROTECT,
        }
        for request in table.requests():
            if request.kind == Kind.MAFIA_KILL:
                assert not set(request.options) & mafia
            if request.kind == Kind.INVESTIGATE:
                assert request.seat not in request.options

    def _night(
        self, table: Table, *, kill: int, protect: int | None, investigate: int | None
    ) -> None:
        def choose(request: SeatRequest) -> dict[str, object]:
            match request.kind:
                case Kind.MAFIA_KILL:
                    return {"target": kill}
                case Kind.PROTECT:
                    return {"target": protect}
                case Kind.INVESTIGATE:
                    return {"target": investigate}
                case _:
                    return {}

        table.answer_all(choose)

    def test_unprotected_target_dies(self) -> None:
        table = Table()
        self._to_night_actions(table)
        victim = table.seats_with(Role.CITIZEN)[0]
        self._night(table, kill=victim, protect=None, investigate=None)
        death = table.bodies(PlayerDied)[-1]
        assert (death.seat, death.cause) == (victim, "night")
        assert table.mode.day == 2
        assert table.mode.phase.value == "discussion"

    def test_doctor_save_gives_peaceful_night(self) -> None:
        table = Table()
        self._to_night_actions(table)
        victim = table.seats_with(Role.CITIZEN)[0]
        self._night(table, kill=victim, protect=victim, investigate=None)
        assert table.bodies(PeacefulNight)
        assert not table.events(PlayerDied)
        resolved = table.events(NightResolved)[-1]
        assert resolved.visibility.scope is Scope.GOD
        assert resolved.body_as(NightResolved).mafia_target == victim

    def test_doctor_cannot_repeat_protect(self) -> None:
        table = Table()
        self._to_night_actions(table)
        victim = table.seats_with(Role.CITIZEN)[0]
        self._night(table, kill=victim, protect=victim, investigate=None)
        table.pass_until("night_actions")
        doctor = table.seats_with(Role.DOCTOR)[0]
        assert victim not in table.request_of(doctor).options

    def test_police_result_is_private_and_correct(self) -> None:
        table = Table()
        self._to_night_actions(table)
        police = table.seats_with(Role.POLICE)[0]
        suspect = table.seats_with(Role.MAFIA)[0]
        victim = table.seats_with(Role.CITIZEN)[0]
        self._night(table, kill=victim, protect=None, investigate=suspect)
        event = table.events(InvestigationResult)[-1]
        assert event.visibility == Visibility.to_seats(police)
        assert event.body_as(InvestigationResult).is_mafia is True

    def test_mafia_split_vote_resolves_deterministically(self) -> None:
        def run(seed: str) -> int | None:
            table = Table(seed)
            table.pass_until("night_actions")
            citizens = table.seats_with(Role.CITIZEN)
            mafia = table.seats_with(Role.MAFIA)
            for request in table.requests():
                if request.kind == Kind.MAFIA_KILL:
                    table.answer(
                        request.seat, advance=False, target=citizens[mafia.index(request.seat)]
                    )
                else:
                    table.answer(request.seat, advance=False)
            table.advance()
            chosen = table.events(MafiaTargetChosen)[-1]
            assert chosen.visibility == Visibility.to_team("mafia")
            return chosen.body_as(MafiaTargetChosen).target

        assert run("x") == run("x")
        assert run("x") is not None

    def test_single_mafia_skips_chat(self) -> None:
        table = Table()
        nominate_and_execute(table, table.seats_with(Role.MAFIA)[0])
        assert table.mode.phase.value == "night_actions"


class TestEnding:
    def test_citizens_win_when_all_mafia_die(self) -> None:
        table = Table(config=OriginalConfig(start_with_night=False))
        first, second = table.seats_with(Role.MAFIA)
        nominate_and_execute(table, first)
        table.pass_until("discussion")
        nominate_and_execute(table, second)
        ended = table.bodies(GameEnded)[-1]
        assert ended.winner == "citizens"
        reveal = table.events(RolesRevealed)[-1]
        assert reveal.visibility == Visibility.public()
        assert table.state.core.ended
        assert not table.requests()

    def test_mafia_win_at_parity(self) -> None:
        table = Table()
        citizens = [s for s in range(1, 9) if table.mode.roles[s] is not Role.MAFIA]
        for _ in range(5):
            if table.state.core.ended:
                break
            nominate_and_execute(table, next(c for c in citizens if c in table.mode.alive))
            if table.state.core.ended:
                break
            table.pass_until("night_actions")
            target = next(c for c in citizens if c in table.mode.alive)
            table.answer_all(lambda r, t=target: {"target": t} if r.kind == Kind.MAFIA_KILL else {})
        assert table.bodies(GameEnded)[-1].winner == "mafia"

    def test_max_days_is_a_draw(self) -> None:
        table = Table(config=OriginalConfig(max_days=2))
        table.pass_until("ended")
        assert table.bodies(GameEnded)[-1].winner == "draw"
        assert table.mode.day == 3

    def test_dead_seats_never_get_requests(self) -> None:
        table = Table()
        victim = table.seats_with(Role.CITIZEN)[0]
        nominate_and_execute(table, victim)
        died_at = table.events(PlayerDied)[-1].seq
        table.pass_until("ended")
        later = [
            e.body_as(RequestOpened).request for e in table.events(RequestOpened) if e.seq > died_at
        ]
        assert later
        assert all(r.seat != victim for r in later)


class TestNightStart:
    """Default table flow: night 1 first, the morning announces it, then day 2 discussion."""

    def test_game_opens_at_night_and_day_breaks_with_the_news(self) -> None:
        table = Table(config=OriginalConfig())
        assert table.mode.phase.value in ("mafia_chat", "night_actions")
        assert table.mode.day == 1
        table.pass_until("night_actions")
        victim = table.seats_with(Role.CITIZEN)[0]
        table.answer_all(lambda r: {"target": victim} if r.kind == Kind.MAFIA_KILL else {})
        death = table.bodies(PlayerDied)[-1]
        assert (death.seat, death.cause) == (victim, "night")
        assert (table.mode.day, table.mode.phase.value) == (2, "discussion")
        assert victim not in table.mode.alive
        assert all(r.seat != victim for r in table.requests())
