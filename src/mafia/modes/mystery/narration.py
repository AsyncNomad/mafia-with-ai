"""One Korean line per murder-mystery event (multi-line for narration and the ending)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from mafia.engine.events import Body
from mafia.modes.mystery.events import (
    AccusationRevealed,
    CardReceived,
    CardRevealed,
    CharacterAssigned,
    EndingRevealed,
    Investigated,
    Narrated,
    SpeakingRoundStarted,
    Speech,
)

Names = Callable[[int | None], str]

PHASE_LABELS: Mapping[str, str] = {
    "setup": "준비",
    "statement": "첫 진술",
    "investigation": "조사",
    "discussion": "토론",
    "final": "최종 변론",
    "accusation": "범인 지목",
    "runoff": "결선 투표",
    "ended": "게임 종료",
}

TEAM_LABELS: Mapping[str, str] = {"innocents": "무고한 사람들", "culprit": "범인", "draw": "무승부"}

_STAGES = {"statement": "첫 진술", "discussion": "토론", "final": "최종 변론"}


def _speech(b: Speech, n: Names) -> str:
    tag = n(b.seat) if b.stage == "discussion" else f"{n(b.seat)} · {_STAGES.get(b.stage, b.stage)}"
    return f'[{tag}] "{b.text}"' if b.text else f"[{tag}] (패스)"


def _accusation(b: AccusationRevealed, n: Names) -> str:
    votes = ", ".join(
        f"{n(s)}→{n(t) if t is not None else '기권'}" for s, t in sorted(b.votes.items())
    )
    if b.runoff_between:
        result = "동률 — 결선: " + ", ".join(n(s) for s in b.runoff_between)
    elif b.accused is not None:
        result = f"지목된 사람: {n(b.accused)}"
    else:
        result = "아무도 지목되지 않았습니다"
    return f"범인 지목 결과 — {votes} / {result}"


def _ending(b: EndingRevealed, n: Names) -> str:
    verdict = "범인이 잡혔습니다" if b.caught else "범인이 빠져나갔습니다"
    lines = [
        f"⚖ {verdict}. 진범은 {n(b.culprit_seat)}였습니다.",
        "",
        b.ending,
        "",
        "📖 진상",
        b.truth,
        "",
        "🎯 개인 목표",
    ]
    for seat, goals in sorted(b.goals.items()):
        total = sum(g.points for g in goals)
        detail = " · ".join(f"{'✔' if g.achieved else '✘'} {g.description}" for g in goals)
        lines.append(f"- {n(seat)}: {total}점 ({detail})")
    epilogues = [f"- {n(s)}: {t}" for s, t in sorted(b.epilogues.items()) if t]
    if epilogues:
        lines += ["", "🌙 그 후", *epilogues]
    return "\n".join(lines)


NARRATORS: Mapping[str, Callable[[Any, Names], str | None]] = {
    CharacterAssigned.KIND: lambda _b, _n: None,  # shown as the character sheet instead
    Narrated.KIND: lambda b, _n: f"📜 {b.text}",
    SpeakingRoundStarted.KIND: lambda b, n: (
        f"{_STAGES.get(b.stage, b.stage)} {b.round}라운드 — 순서: {' → '.join(n(s) for s in b.order)}"
    ),
    Speech.KIND: _speech,
    CardReceived.KIND: lambda b, _n: (
        f'🃏 (내 카드) [{b.clue_id}] {b.title}: "{b.text}" — {b.where}'
    ),
    Investigated.KIND: lambda b, n: (
        f"{b.investigation}차 조사 — "
        + ", ".join(f"{n(s)}→{loc}" for s, loc in sorted(b.visits.items()))
        + (f" (빈손: {', '.join(n(s) for s in b.empty_handed)})" if b.empty_handed else "")
    ),
    CardRevealed.KIND: lambda b, n: (
        f'🔎 {n(b.seat)}이(가) 카드를 공개했습니다 — [{b.clue_id}] {b.title}: "{b.text}"'
    ),
    AccusationRevealed.KIND: _accusation,
    EndingRevealed.KIND: _ending,
}


def narrate(body: Body, names: Names) -> str | None:
    fn = NARRATORS.get(body.KIND)
    return fn(body, names) if fn else None
