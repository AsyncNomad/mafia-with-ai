"""Randomised whole-game simulation with invariant checks. No network, no models."""

from __future__ import annotations

import random
from typing import Any

import pytest

from mafia.engine.audit import audit
from mafia.engine.core_events import ContextDelivered, GameEnded, RequestOpened
from mafia.engine.events import Event
from mafia.engine.requests import Answer, SeatRequest
from mafia.engine.visibility import Scope
from mafia.modes.original.config import OriginalConfig
from mafia.modes.original.events import (
    InvestigationResult,
    MafiaChat,
    MafiaTargetChosen,
    NightResolved,
    PlayerDied,
    RoleAssigned,
)
from mafia.modes.original.roles import Role, Team
from mafia.modes.original.rules import Phase, winner_of
from tests.table import Table

GAMES = 300
MAX_ACTIONS = 2_000

# Events that must never be public, with the only scope they may have.
EXPECTED_SCOPE = {
    RoleAssigned.KIND: Scope.SEATS,
    InvestigationResult.KIND: Scope.SEATS,
    MafiaChat.KIND: Scope.TEAM,
    MafiaTargetChosen.KIND: Scope.TEAM,
    NightResolved.KIND: Scope.GOD,
    ContextDelivered.KIND: Scope.GOD,
}


def fake_action(request: SeatRequest, rng: random.Random) -> dict[str, Any]:
    action: dict[str, Any] = {}
    match request.answer:
        case Answer.SPEECH:
            if rng.random() < 0.8:
                action["speech"] = f"{request.seat}번의 발언 {rng.randint(0, 999)}"
        case Answer.TARGET:
            choices: list[int | None] = [*request.options, None]
            action["target"] = rng.choice(choices)
        case Answer.APPROVAL:
            action["approve"] = rng.choice([True, False, None])
    if rng.random() < 0.3:
        action["private_note"] = "관찰 메모"
    if rng.random() < 0.2:
        action["suspicion"] = {s: rng.randint(0, 100) for s in rng.sample(range(1, 9), 3)}
    return action


def play(seed: str, config: OriginalConfig | None = None) -> Table:
    table = Table(seed, config)
    rng = random.Random(f"policy:{seed}")
    for _ in range(MAX_ACTIONS):
        if table.state.core.ended:
            return table
        requests = table.requests()
        assert requests, f"stuck without requests in phase {table.mode.phase}"
        for request in requests:
            if rng.random() < 0.5:
                table.context(request.seat)
            if rng.random() < 0.05:
                step = table.engine.expire(table.state, request.request_id)
                table.state = step.state
                table.log.extend(step.events)
            else:
                table.answer(request.seat, advance=False, **fake_action(request, rng))
        table.advance()
    raise AssertionError(f"game {seed} did not end within {MAX_ACTIONS} rounds of requests")


def check_invariants(table: Table) -> None:
    log = table.log
    engine = table.engine
    final = table.state

    # 1. The log alone reproduces the final state, also after a JSON round trip.
    assert engine.fold(log) == final
    restored = [Event.model_validate_json(e.model_dump_json()) for e in log]
    assert engine.fold(restored) == final

    # 2. No context ever shown to a seat contained something it was not entitled to.
    assert audit(engine, log) == []

    # 3. Secret events always carry their restricted scope.
    for event in log:
        expected = EXPECTED_SCOPE.get(event.kind)
        if expected is not None:
            assert event.visibility.scope is expected, (event.kind, event.visibility)

    # 4. Requests only ever go to living seats.
    state = engine.empty()
    for event in log:
        if event.kind == RequestOpened.KIND:
            seat = event.body_as(RequestOpened).request.seat
            assert seat in state.mode.alive, f"request to dead seat {seat} at {event.seq}"
        state = engine.apply(state, event)

    # 5. The game ended exactly once, with a winner consistent with the rules.
    ends = [e.body_as(GameEnded) for e in log if e.kind == GameEnded.KIND]
    assert len(ends) == 1
    rule_winner = winner_of(final.mode.roles, final.mode.alive)
    if ends[0].winner == "draw":
        assert rule_winner is None
        assert final.mode.day > table.rules.config.max_days
    else:
        assert rule_winner is Team(ends[0].winner)
    assert final.mode.phase is Phase.ENDED
    assert not final.core.open_requests

    # 6. Deaths are unique and only of living seats; nobody revives.
    dead = [e.body_as(PlayerDied).seat for e in log if e.kind == PlayerDied.KIND]
    assert len(dead) == len(set(dead))
    assert set(dead) == set(range(1, 9)) - final.mode.alive

    # 7. Role distribution never changes.
    assert sorted(final.mode.roles.values()) == sorted(
        [Role.MAFIA] * 2 + [Role.POLICE, Role.DOCTOR] + [Role.CITIZEN] * 4
    )


@pytest.mark.parametrize("batch", range(10))
def test_random_games_hold_invariants(batch: int) -> None:
    winners: dict[str, int] = {}
    for i in range(GAMES // 10):
        table = play(f"sim-{batch}-{i}")
        check_invariants(table)
        winner = table.bodies(GameEnded)[0].winner
        winners[winner] = winners.get(winner, 0) + 1
    assert winners  # every game produced a result


@pytest.mark.parametrize(
    "config",
    [
        OriginalConfig(start_with_night=True),
        OriginalConfig(discussion_rounds=1, mafia_chat_rounds=0),
        OriginalConfig(doctor_self_protect=False, doctor_repeat_protect=True),
        OriginalConfig(reveal_role_on_death=True, max_days=3),
    ],
)
def test_config_variants_hold_invariants(config: OriginalConfig) -> None:
    for i in range(20):
        check_invariants(play(f"variant-{i}", config))


def test_same_seed_same_policy_same_log() -> None:
    first = play("determinism")
    second = play("determinism")
    assert [e.model_dump() for e in first.log] == [e.model_dump() for e in second.log]


def test_different_seeds_diverge() -> None:
    a = play("diverge-a")
    b = play("diverge-b")
    assert [e.model_dump() for e in a.log] != [e.model_dump() for e in b.log]
