"""Who is making a call.

Host adapters translate their own notion of "which agent is this" (Claude Code: the
`agent_type` hook field) into a `Caller`. Policy decisions are made on `Caller` only, so
they stay identical across hosts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

SEAT_COUNT = 8
_SEAT_RE = re.compile(r"^seat-([1-9][0-9]*)$")
_ALIAS_RE = re.compile(r"^agent-([1-9][0-9]*)$")
ALIAS_COUNT = 8


class CallerKind(StrEnum):
    MAIN = "main"  # the chat session driving the game; must only ever see public information
    SEAT = "seat"  # an AI player
    AGENT = "agent"  # a named non-seat agent (scenario architect, auditor, ...)
    ALIAS = "alias"  # an anonymous seat slot for covert turns; the seat is known only to the game
    UNKNOWN = "unknown"  # anything else; always denied


@dataclass(frozen=True, slots=True)
class Caller:
    kind: CallerKind
    agent_type: str | None = None
    seat: int | None = None


def seat_agent_name(seat: int) -> str:
    return f"seat-{seat}"


def classify(agent_type: object, known_agents: frozenset[str]) -> Caller:
    """Map a host-reported agent identifier to a caller.

    A missing or empty identifier means the main session. Anything malformed is UNKNOWN
    rather than MAIN: an unrecognised identifier must never inherit main-session rights.
    """
    if agent_type is None or agent_type == "":
        return Caller(CallerKind.MAIN)
    if not isinstance(agent_type, str):
        return Caller(CallerKind.UNKNOWN)
    match = _SEAT_RE.match(agent_type)
    if match:
        seat = int(match.group(1))
        if 1 <= seat <= SEAT_COUNT:
            return Caller(CallerKind.SEAT, agent_type, seat)
        return Caller(CallerKind.UNKNOWN, agent_type)
    alias = _ALIAS_RE.match(agent_type)
    if alias and 1 <= int(alias.group(1)) <= ALIAS_COUNT:
        return Caller(CallerKind.ALIAS, agent_type)
    if agent_type in known_agents:
        return Caller(CallerKind.AGENT, agent_type)
    return Caller(CallerKind.UNKNOWN, agent_type)


def alias_names() -> tuple[str, ...]:
    return tuple(f"agent-{i}" for i in range(1, ALIAS_COUNT + 1))
