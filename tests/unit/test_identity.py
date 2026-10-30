from __future__ import annotations

import pytest

from mafia.harness.identity import Caller, CallerKind, classify

KNOWN = frozenset({"mystery-architect"})


@pytest.mark.parametrize("agent_type", [None, ""])
def test_missing_identifier_is_main(agent_type: object) -> None:
    assert classify(agent_type, KNOWN) == Caller(CallerKind.MAIN)


@pytest.mark.parametrize("seat", range(1, 9))
def test_valid_seats(seat: int) -> None:
    assert classify(f"seat-{seat}", KNOWN) == Caller(CallerKind.SEAT, f"seat-{seat}", seat)


@pytest.mark.parametrize(
    "agent_type", ["seat-0", "seat-9", "seat-01", "seat-", "Seat-1", "seat-1 ", "seat-1x"]
)
def test_malformed_seats_are_unknown_not_main(agent_type: str) -> None:
    assert classify(agent_type, KNOWN).kind is CallerKind.UNKNOWN


def test_known_agent() -> None:
    assert classify("mystery-architect", KNOWN) == Caller(CallerKind.AGENT, "mystery-architect")


@pytest.mark.parametrize("agent_type", ["general-purpose", "Explore", 42, ["seat-1"]])
def test_everything_else_is_unknown(agent_type: object) -> None:
    assert classify(agent_type, KNOWN).kind is CallerKind.UNKNOWN
