"""Narration for the terminal log. In god view it also shows what no seat sees in chat:
role cards, every seat's private notes and suspicion, whose turn it is."""

from __future__ import annotations

from collections.abc import Callable

from mafia.engine.core_events import (
    ActionSubmitted,
    ContextDelivered,
    GameCreated,
    RequestOpened,
    SeatProfileAssigned,
)
from mafia.engine.events import Event
from mafia.modes.original.events import RoleAssigned
from mafia.views.narration import narrate

Names = Callable[[int | None], str]


def _action(b: ActionSubmitted, n: Names) -> str:
    a = b.action
    if a.target is not None:
        what = f"→ {n(a.target)}"
    elif a.approve is not None:
        what = "찬성" if a.approve else "반대"
    elif a.speech is not None:
        what = "발언"
    else:
        what = "패스/기권"
    parts = [f"📝 {n(b.seat)} [{b.kind}] {what}"]
    if b.fallback:
        parts.append("(시간 초과 자동 처리)")
    if a.private_note:
        parts.append(f'속마음: "{a.private_note}"')
    if a.suspicion:
        ranked = sorted(a.suspicion.items(), key=lambda kv: -kv[1])
        parts.append("의심도: " + ", ".join(f"{n(s)} {v}" for s, v in ranked))
    return " · ".join(parts)


def narrate_terminal(event: Event, mode: str, names: Names, *, god: bool) -> str | None:
    if god:
        match event.kind:
            case GameCreated.KIND:
                b = event.body_as(GameCreated)
                return f"🎲 게임 {b.game_id} · 모드 {b.mode} · 사람 좌석 {list(b.human_seats) or '없음(관전)'}"
            case RoleAssigned.KIND:
                b = event.body_as(RoleAssigned)
                return f"🎭 {names(b.seat)} = {b.role.label}"
            case SeatProfileAssigned.KIND:
                b = event.body_as(SeatProfileAssigned)
                p = b.profile
                return f"👤 {names(b.seat)} 인격: {p.get('occupation')}, {p.get('personality')}"
            case RequestOpened.KIND:
                r = event.body_as(RequestOpened).request
                return f"⏳ {names(r.seat)} 차례 ({r.kind})"
            case ActionSubmitted.KIND:
                return _action(event.body_as(ActionSubmitted), names)
            case ContextDelivered.KIND:
                return None
            case _:
                pass
    return narrate(event, mode, names)
