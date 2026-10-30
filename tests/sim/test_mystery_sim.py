"""Randomised murder-mystery games on the factory scenario, with invariant checks."""

from __future__ import annotations

import random
from typing import Any

import pytest

from mafia.engine.audit import audit
from mafia.engine.core_events import ActionSubmitted, GameEnded, SeatInfo
from mafia.engine.engine import Engine, GameState
from mafia.engine.events import Event
from mafia.engine.requests import Action, ActionRejected, Answer, SeatRequest
from mafia.engine.runtime import FixedClock
from mafia.engine.visibility import Scope
from mafia.modes.mystery.events import (
    AccusationRevealed,
    CardReceived,
    CardRevealed,
    CharacterAssigned,
    EndingRevealed,
    Investigated,
)
from mafia.modes.mystery.rules import (
    CULPRIT,
    INNOCENTS,
    MysteryConfig,
    MysteryMode,
    MysteryState,
    cast_seats,
)
from mafia.modes.mystery.scenario import HoldClue, Scenario
from tests.mystery_factory import CULPRIT as CULPRIT_ID
from tests.mystery_factory import scenario_data

GAMES = 150


class MysteryTable:
    def __init__(self, seed: str, data: dict[str, Any] | None = None) -> None:
        self.scenario = Scenario.model_validate(data or scenario_data())
        self.engine: Engine[MysteryState] = Engine(
            MysteryMode(MysteryConfig(scenario=self.scenario)), FixedClock()
        )
        cast = cast_seats(seed, [c.id for c in self.scenario.characters])
        seats = [
            SeatInfo(seat=s, name=self.scenario.character(cid).name)
            for s, cid in sorted(cast.items())
        ]
        step = self.engine.create("g", seed, seats)
        self.state: GameState[MysteryState] = step.state
        self.log: list[Event] = list(step.events)
        self.advance()

    def advance(self) -> None:
        step = self.engine.advance(self.state)
        self.state, self.log = step.state, self.log + list(step.events)

    def requests(self) -> list[SeatRequest]:
        return sorted(self.state.core.open_requests.values(), key=lambda r: r.seat)

    def submit(self, request: SeatRequest, action: Action) -> None:
        step = self.engine.submit(self.state, request.seat, request.request_id, action)
        self.state, self.log = step.state, self.log + list(step.events)

    def context(self, seat: int) -> None:
        step, _ = self.engine.context_for(self.state, self.log, seat)
        self.state, self.log = step.state, self.log + list(step.events)

    def bodies(self, cls: Any) -> list[Any]:
        return [e.body_as(cls) for e in self.log if e.kind == cls.KIND]


def fake(request: SeatRequest, table: MysteryTable, rng: random.Random) -> Action:
    match request.answer:
        case Answer.SPEECH:
            hand = [
                k
                for k in table.state.mode.hands.get(request.seat, ())
                if k not in table.state.mode.revealed
            ]
            reveal = tuple(rng.sample(hand, min(len(hand), rng.choice([0, 0, 1, 2]))))
            return (
                Action(speech=f"{request.seat}번 발언", reveal=reveal)
                if rng.random() < 0.9 or reveal
                else Action()
            )
        case Answer.TARGET:
            return Action(target=rng.choice([*request.options, None]))
        case Answer.APPROVAL:
            return Action(approve=rng.choice([True, False]))


def play(seed: str, data: dict[str, Any] | None = None) -> MysteryTable:
    table = MysteryTable(seed, data)
    rng = random.Random(f"policy:{seed}")
    for _ in range(500):
        if table.state.core.ended:
            return table
        for request in table.requests():
            if rng.random() < 0.3:
                table.context(request.seat)
            table.submit(request, fake(request, table, rng))
        table.advance()
    raise AssertionError("game did not end")


def check(table: MysteryTable) -> None:
    engine, log, final = table.engine, table.log, table.state
    assert engine.fold(log) == final
    assert engine.fold([Event.model_validate_json(e.model_dump_json()) for e in log]) == final
    assert audit(engine, log) == []

    for e in log:
        if e.kind in (CharacterAssigned.KIND, CardReceived.KIND):
            assert e.visibility.scope is Scope.SEATS and len(e.visibility.seats) == 1

    received = table.bodies(CardReceived)
    ids = [c.clue_id for c in received]
    assert len(ids) == len(set(ids)), "a card was dealt twice"
    per_seat = {s: [c for c in received if c.seat == s] for s in range(1, 9)}
    for cards in per_seat.values():
        assert 1 <= len(cards) <= 3  # starting hand + at most one per investigation
    for r in table.bodies(CardRevealed):
        assert r.clue_id in final.mode.hands[r.seat]

    ends = table.bodies(GameEnded)
    assert len(ends) == 1
    ending = table.bodies(EndingRevealed)[0]
    culprit_seat = next(s for s, c in final.mode.character_of.items() if c == CULPRIT_ID)
    assert ending.culprit_seat == culprit_seat
    accused = table.bodies(AccusationRevealed)[-1].accused
    assert ending.caught == (accused == culprit_seat)
    assert ends[0].winner == (INNOCENTS if ending.caught else CULPRIT)

    for seat, results in ending.goals.items():
        goals = table.scenario.character(final.mode.character_of[seat]).sheet.goals
        for goal, result in zip(goals, results, strict=True):
            if isinstance(goal.predicate, HoldClue):
                assert result.achieved == (goal.predicate.clue in final.mode.hands[seat])


@pytest.mark.parametrize("batch", range(5))
def test_random_mystery_games_hold_invariants(batch: int) -> None:
    runoffs = 0
    for i in range(GAMES // 5):
        table = play(f"mm-{batch}-{i}")
        check(table)
        runoffs += any(a.runoff_between for a in table.bodies(AccusationRevealed))
        assert all(not inv.empty_handed for inv in table.bodies(Investigated))
    assert runoffs > 0, "no game exercised the runoff"


def test_same_seed_same_log() -> None:
    assert [e.model_dump() for e in play("same").log] == [e.model_dump() for e in play("same").log]


def test_seat_names_match_secret_cast() -> None:
    table = MysteryTable("cast")
    for seat, cid in table.state.mode.character_of.items():
        assert table.state.core.name_of(seat) == table.scenario.character(cid).name


def test_unlocked_card_appears_only_after_its_prerequisite_is_revealed() -> None:
    data = scenario_data()
    data["clues"].append(
        {
            "id": "k40",
            "title": "숨은 쪽지",
            "text": "열쇠로 연 서랍 속 쪽지",
            "kind": "문서",
            "round": 2,
            "location_id": "l1",
            "unlock_condition": "k01",
            "supports": [],
        }
    )
    dealt = 0
    for i in range(30):
        table = play(f"unlock-{i}", data)
        got = [
            e
            for e in table.log
            if e.kind == CardReceived.KIND and e.body_as(CardReceived).clue_id == "k40"
        ]
        if not got:
            continue
        dealt += 1
        revealed_at = [
            e.seq
            for e in table.log
            if e.kind == CardRevealed.KIND and e.body_as(CardRevealed).clue_id == "k01"
        ]
        assert revealed_at and revealed_at[0] < got[0].seq
    assert dealt > 0, "the unlocked card was never dealt; the test proves nothing"


class TestRevealRules:
    def _speech_request(self, table: MysteryTable) -> SeatRequest:
        (request,) = table.requests()
        assert request.answer is Answer.SPEECH
        return request

    def test_cannot_reveal_a_card_you_do_not_hold(self) -> None:
        table = MysteryTable("reveal")
        request = self._speech_request(table)
        others = [
            k for s, hand in table.state.mode.hands.items() if s != request.seat for k in hand
        ]
        with pytest.raises(ActionRejected):
            table.submit(request, Action(speech="이것 보세요", reveal=(others[0],)))

    def test_reveal_needs_words_and_no_duplicates(self) -> None:
        table = MysteryTable("reveal2")
        request = self._speech_request(table)
        own = table.state.mode.hands[request.seat][0]
        with pytest.raises(ActionRejected):
            table.submit(request, Action(reveal=(own,)))
        with pytest.raises(ActionRejected):
            table.submit(request, Action(speech="두 번", reveal=(own, own)))
        table.submit(request, Action(speech="제 카드입니다", reveal=(own,)))
        shown = table.bodies(CardRevealed)[-1]
        assert shown.clue_id == own and shown.text == table.scenario.clue(own).text

    def test_cannot_reveal_while_voting(self) -> None:
        table = MysteryTable("reveal3")
        rng = random.Random(0)
        while table.requests()[0].answer is Answer.SPEECH:
            for r in table.requests():
                table.submit(r, Action())
            table.advance()
        request = table.requests()[0]
        with pytest.raises(ActionRejected):
            table.submit(request, Action(target=request.options[0], reveal=("k01",)))
        _ = rng


def test_fallback_investigation_still_finds_a_card() -> None:
    table = MysteryTable("fallback")
    while not table.state.core.ended:
        for r in table.requests():
            step = table.engine.expire(table.state, r.request_id)
            table.state, table.log = step.state, table.log + list(step.events)
        table.advance()
    fallbacks = [b for b in table.bodies(ActionSubmitted) if b.fallback]
    assert fallbacks
    assert all(not inv.empty_handed for inv in table.bodies(Investigated))
    assert len(table.bodies(CardReceived)) == 8 * 3
