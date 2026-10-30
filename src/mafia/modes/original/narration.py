"""One Korean line per original-mode event. Pure formatting; callers have already applied
visibility, so these functions never decide who may read what."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from mafia.engine.events import Body
from mafia.modes.original.events import (
    ConfirmationRevealed,
    DiscussionRoundStarted,
    InvestigationResult,
    MafiaChat,
    MafiaChatRoundStarted,
    MafiaTargetChosen,
    NightResolved,
    NominationRevealed,
    PeacefulNight,
    PlayerDied,
    RoleAssigned,
    RolesRevealed,
    Speech,
)
from mafia.modes.original.roles import Team

Names = Callable[[int | None], str]

PHASE_LABELS: Mapping[str, str] = {
    "setup": "준비",
    "discussion": "낮 토론",
    "nomination": "지목 투표",
    "defense": "최후 변론",
    "confirmation": "찬반 투표",
    "mafia_chat": "밤 · 마피아 대화",
    "night_actions": "밤",
    "ended": "게임 종료",
}

TEAM_LABELS: Mapping[str, str] = {
    Team.MAFIA.value: "마피아",
    Team.CITIZENS.value: "시민",
    "draw": "무승부",
}


def _speech(b: Speech, n: Names) -> str:
    tag = f"{n(b.seat)} · 최후 변론" if b.context == "defense" else n(b.seat)
    return f'[{tag}] "{b.text}"' if b.text else f"[{tag}] (패스)"


def _nomination(b: NominationRevealed, n: Names) -> str:
    votes = ", ".join(
        f"{n(s)}→{n(t) if t is not None else '기권'}" for s, t in sorted(b.votes.items())
    )
    result = f"후보: {n(b.nominee)}" if b.nominee is not None else "후보 없음(동률 또는 기권)"
    return f"지목 투표 결과 — {votes} / {result}"


def _confirmation(b: ConfirmationRevealed, n: Names) -> str:
    yes = [n(s) for s, v in sorted(b.votes.items()) if v is True]
    no = [n(s) for s, v in sorted(b.votes.items()) if v is False]
    abstain = [n(s) for s, v in sorted(b.votes.items()) if v is None]
    verdict = "가결" if b.approved else "부결"
    return (
        f"찬반 투표 결과 — 찬성 {len(yes)}({', '.join(yes) or '-'}), "
        f"반대 {len(no)}({', '.join(no) or '-'}), 기권 {len(abstain)} → {verdict}"
    )


def _died(b: PlayerDied, n: Names) -> str:
    how = "처형되었습니다" if b.cause == "execution" else "밤사이 살해당했습니다"
    role = f" (역할: {b.role.label})" if b.role is not None else ""
    return f"☠ {n(b.seat)}이(가) {how}{role}"


def _night(b: NightResolved, n: Names) -> str:
    return (
        f"(신의 시점) {b.day}일차 밤 — 마피아 대상 {n(b.mafia_target)}, 의사 보호 {n(b.protected)}, "
        f"경찰 조사 {n(b.investigated)}, 사망 {n(b.killed)}"
    )


def _roles(b: RolesRevealed, n: Names) -> str:
    roles = ", ".join(f"{n(s)} {r.label}" for s, r in sorted(b.roles.items()))
    return f"역할 공개 — {roles}"


NARRATORS: Mapping[str, Callable[[Any, Names], str | None]] = {
    RoleAssigned.KIND: lambda _b, _n: None,  # shown in the role card instead
    DiscussionRoundStarted.KIND: lambda b, n: (
        f"토론 {b.round}라운드 — 발언 순서: {' → '.join(n(s) for s in b.order)}"
    ),
    Speech.KIND: _speech,
    MafiaChatRoundStarted.KIND: lambda b, _n: f"(마피아 전용) 밤 대화 {b.round}라운드",
    MafiaChat.KIND: lambda b, n: (
        f'(마피아 전용) [{n(b.seat)}] "{b.text}"'
        if b.text
        else f"(마피아 전용) [{n(b.seat)}] (패스)"
    ),
    NominationRevealed.KIND: _nomination,
    ConfirmationRevealed.KIND: _confirmation,
    PlayerDied.KIND: _died,
    PeacefulNight.KIND: lambda _b, _n: "평화로운 밤이었습니다. 아무도 죽지 않았습니다.",
    MafiaTargetChosen.KIND: lambda b, n: (
        f"(마피아 전용) 오늘 밤 대상: {n(b.target) if b.target else '없음'}"
    ),
    InvestigationResult.KIND: lambda b, n: (
        f"(경찰 전용) 조사 결과: {n(b.target)}은(는) 마피아{'입니다' if b.is_mafia else '가 아닙니다'}"
    ),
    NightResolved.KIND: _night,
    RolesRevealed.KIND: _roles,
}


def narrate(body: Body, names: Names) -> str | None:
    fn = NARRATORS.get(body.KIND)
    return fn(body, names) if fn else None
