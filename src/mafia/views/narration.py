"""Event → one Korean line, dispatching core events here and mode events to the mode."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from mafia.engine.core_events import (
    ActionSubmitted,
    ContextDelivered,
    GameCreated,
    GameEnded,
    PhaseChanged,
    RequestOpened,
    SeatInfo,
    SeatProfileAssigned,
    SeatsAnnounced,
)
from mafia.engine.events import Body, Event
from mafia.modes.mystery import narration as mystery
from mafia.modes.original import narration as original

Names = Callable[[int | None], str]

_MODE_NARRATORS: Mapping[str, Callable[[Body, Names], str | None]] = {
    "original": original.narrate,
    "mystery": mystery.narrate,
}
_PHASE_LABELS: Mapping[str, Mapping[str, str]] = {
    "original": original.PHASE_LABELS,
    "mystery": mystery.PHASE_LABELS,
}
_TEAM_LABELS: Mapping[str, Mapping[str, str]] = {
    "original": original.TEAM_LABELS,
    "mystery": mystery.TEAM_LABELS,
}


def phase_label(mode: str, phase: str) -> str:
    return _PHASE_LABELS.get(mode, {}).get(phase, phase)


def team_label(mode: str, team: str) -> str:
    return _TEAM_LABELS.get(mode, {}).get(team, team)


def name_lookup(seats: Sequence[SeatInfo]) -> Names:
    by_seat = {s.seat: s.name for s in seats}

    def names(seat: int | None) -> str:
        if seat is None:
            return "없음"
        return f"{by_seat.get(seat, '?')}({seat}번)"

    return names


def _own_action(b: ActionSubmitted, n: Names) -> str | None:
    a = b.action
    parts: list[str] = []
    if a.target is not None:
        parts.append(f"선택: {n(a.target)}")
    elif a.approve is not None:
        parts.append("찬성" if a.approve else "반대")
    elif a.speech is None and b.kind not in ("discussion", "defense", "mafia_chat"):
        parts.append("기권")
    if b.fallback:
        parts.append("시간 초과로 자동 처리")
    if a.private_note:
        parts.append(f'메모: "{a.private_note}"')
    return f"[내 기록] {', '.join(parts)}" if parts else None


def narrate(event: Event, mode: str, names: Names) -> str | None:
    match event.kind:
        case SeatsAnnounced.KIND:
            seats = event.body_as(SeatsAnnounced).seats
            return "참가자 — " + ", ".join(names(s.seat) for s in seats)
        case PhaseChanged.KIND:
            b = event.body_as(PhaseChanged)
            if mode == "mystery":
                return f"── {phase_label(mode, b.phase)} ──"
            return f"── {b.day}일차 · {phase_label(mode, b.phase)} ──"
        case ActionSubmitted.KIND:
            return _own_action(event.body_as(ActionSubmitted), names)
        case GameEnded.KIND:
            b = event.body_as(GameEnded)
            return f"🏁 게임 종료 — {team_label(mode, b.winner)} 승리"
        case (
            GameCreated.KIND | SeatProfileAssigned.KIND | RequestOpened.KIND | ContextDelivered.KIND
        ):
            return None
        case _:
            narrator = _MODE_NARRATORS.get(mode)
            return narrator(event.body(), names) if narrator else None
