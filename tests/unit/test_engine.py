from __future__ import annotations

import pytest

from mafia.engine.audit import audit
from mafia.engine.core_events import ActionSubmitted, GameCreated, SeatInfo, SeatsAnnounced
from mafia.engine.engine import Engine, EngineError
from mafia.engine.events import Event
from mafia.engine.requests import Action, ActionRejected, Answer, SeatRequest, validate_shape
from mafia.engine.runtime import FixedClock, rng_for
from mafia.engine.visibility import Scope, Visibility
from mafia.modes.original.rules import OriginalMode, OriginalState
from tests.table import Table


def test_create_rejects_bad_seat_numbers() -> None:
    engine: Engine[OriginalState] = Engine(OriginalMode(), FixedClock())
    seats = [SeatInfo(seat=i, name=f"p{i}") for i in (1, 2, 4, 5, 6, 7, 8, 9)]
    with pytest.raises(EngineError):
        engine.create("g", "s", seats)


def test_seed_and_human_seat_are_god_only() -> None:
    table = Table()
    created = table.events(GameCreated)[0]
    assert created.visibility.scope is Scope.GOD
    roster = table.events(SeatsAnnounced)[0]
    assert roster.visibility == Visibility.public()
    assert "human" not in str(roster.data)


def test_fold_reproduces_state() -> None:
    table = Table()
    table.pass_until("night_actions")
    assert table.engine.fold(table.log) == table.state


def test_fold_rejects_gaps() -> None:
    table = Table()
    broken = [*table.log[:3], *table.log[4:]]
    with pytest.raises(EngineError):
        table.engine.fold(broken)


def test_events_round_trip_through_json() -> None:
    table = Table()
    table.pass_until("night_actions")
    restored = [Event.model_validate_json(e.model_dump_json()) for e in table.log]
    assert table.engine.fold(restored) == table.state


class TestSubmit:
    def test_wrong_seat_rejected(self) -> None:
        table = Table()
        request = table.requests()[0]
        other = request.seat % 8 + 1
        with pytest.raises(ActionRejected):
            table.engine.submit(table.state, other, request.request_id, Action())

    def test_closed_request_rejected(self) -> None:
        table = Table()
        request = table.requests()[0]
        table.answer(request.seat, speech="안녕하세요")
        with pytest.raises(ActionRejected):
            table.engine.submit(table.state, request.seat, request.request_id, Action())

    def test_target_outside_options_rejected(self) -> None:
        table = Table()
        table.pass_until("nomination")
        request = table.requests()[0]
        with pytest.raises(ActionRejected):
            table.engine.submit(
                table.state, request.seat, request.request_id, Action(target=request.seat)
            )

    def test_rejection_leaves_state_unchanged(self) -> None:
        table = Table()
        before = table.state
        request = table.requests()[0]
        with pytest.raises(ActionRejected):
            table.engine.submit(table.state, request.seat, request.request_id, Action(target=1))
        assert table.state == before

    def test_own_action_visible_only_to_that_seat(self) -> None:
        table = Table()
        request = table.requests()[0]
        table.answer(request.seat, speech="hi", private_note="민지가 수상하다")
        submitted = table.events(ActionSubmitted)[-1]
        assert submitted.visibility == Visibility.to_seats(request.seat)


def _request(
    answer: Answer, *, options: tuple[int, ...] = (), allow_pass: bool = False
) -> SeatRequest:
    return SeatRequest(
        request_id="q1",
        seat=1,
        kind="k",
        answer=answer,
        instruction="",
        options=options,
        allow_pass=allow_pass,
    )


@pytest.mark.parametrize(
    ("request_", "action"),
    [
        (_request(Answer.SPEECH), Action()),
        (_request(Answer.SPEECH, allow_pass=True), Action(speech="   ")),
        (_request(Answer.SPEECH, allow_pass=True), Action(speech="hi", target=2)),
        (_request(Answer.TARGET, options=(2, 3)), Action()),
        (_request(Answer.TARGET, options=(2, 3), allow_pass=True), Action(target=4)),
        (_request(Answer.TARGET, options=(2, 3)), Action(target=2, approve=True)),
        (_request(Answer.APPROVAL), Action()),
        (_request(Answer.APPROVAL, allow_pass=True), Action(approve=True, speech="x")),
        (_request(Answer.SPEECH, allow_pass=True), Action(suspicion={9: 50})),
        (_request(Answer.SPEECH, allow_pass=True), Action(suspicion={2: 101})),
    ],
)
def test_validate_shape_rejects(request_: SeatRequest, action: Action) -> None:
    with pytest.raises(ActionRejected):
        validate_shape(request_, action, seat_count=8)


@pytest.mark.parametrize(
    ("request_", "action"),
    [
        (_request(Answer.SPEECH), Action(speech="안녕")),
        (_request(Answer.SPEECH, allow_pass=True), Action()),
        (_request(Answer.TARGET, options=(2, 3)), Action(target=3)),
        (_request(Answer.TARGET, options=(2, 3), allow_pass=True), Action()),
        (_request(Answer.APPROVAL), Action(approve=False)),
        (_request(Answer.SPEECH, allow_pass=True), Action(suspicion={2: 0, 8: 100})),
    ],
)
def test_validate_shape_accepts(request_: SeatRequest, action: Action) -> None:
    validate_shape(request_, action, seat_count=8)


def test_action_rejects_unknown_fields_and_long_speech() -> None:
    with pytest.raises(ValueError):
        Action.model_validate({"speech": "x", "seat": 3})
    with pytest.raises(ValueError):
        Action(speech="가" * 601)


def test_rng_for_is_deterministic_and_scoped() -> None:
    assert rng_for("s", "a", 1).random() == rng_for("s", "a", 1).random()
    assert rng_for("s", "a", 1).random() != rng_for("s", "a", 2).random()
    assert rng_for("s", "a", 1).random() != rng_for("t", "a", 1).random()


def test_audit_detects_forged_delivery() -> None:
    table = Table()
    table.context(1)
    assert audit(table.engine, table.log) == []
    # Forge a delivery that shows seat 1 another seat's role card.
    from mafia.engine.core_events import ContextDelivered
    from mafia.modes.original.events import RoleAssigned

    other_card = next(e for e in table.events(RoleAssigned) if 1 not in e.visibility.seats)
    forged = Event(
        seq=table.state.core.last_seq + 1,
        at=0.0,
        kind=ContextDelivered.KIND,
        visibility=Visibility.god(),
        data=ContextDelivered(seat=1, request_id=None, event_seqs=(other_card.seq,)).model_dump(
            mode="json"
        ),
    )
    violations = audit(table.engine, [*table.log, forged])
    assert len(violations) == 1
    assert violations[0].leaked_seq == other_card.seq
