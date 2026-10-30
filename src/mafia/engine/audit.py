"""Post-hoc isolation audit: replays a log and checks every context ever shown to a seat."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from mafia.engine.core_events import ContextDelivered
from mafia.engine.engine import Engine
from mafia.engine.events import Event
from mafia.engine.visibility import can_see


@dataclass(frozen=True, slots=True)
class Violation:
    delivery_seq: int
    seat: int
    leaked_seq: int
    reason: str


def audit[S](engine: Engine[S], log: Sequence[Event]) -> list[Violation]:
    """Return every event a seat was shown but was not entitled to see at that moment."""
    by_seq = {e.seq: e for e in log}
    violations: list[Violation] = []
    state = engine.empty()
    for event in log:
        if event.kind == ContextDelivered.KIND:
            delivery = event.body_as(ContextDelivered)
            viewer = engine.mode.viewer(state.mode, state.core, delivery.seat)
            for seq in delivery.event_seqs:
                shown = by_seq.get(seq)
                if shown is None or seq >= event.seq:
                    violations.append(Violation(event.seq, delivery.seat, seq, "not yet existing"))
                elif shown.kind == ContextDelivered.KIND:
                    violations.append(
                        Violation(event.seq, delivery.seat, seq, "audit record shown")
                    )
                elif not can_see(viewer, shown.visibility):
                    violations.append(
                        Violation(event.seq, delivery.seat, seq, f"{shown.kind} not visible")
                    )
        state = engine.apply(state, event)
    return violations
