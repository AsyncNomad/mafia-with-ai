"""The text an AI seat receives from `my_turn`. Built only from an engine `SeatContext`, which
already contains nothing but what that seat may know."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mafia.engine.core_events import SeatProfileAssigned, SeatsAnnounced
from mafia.engine.engine import SeatContext
from mafia.engine.requests import Answer
from mafia.views.narration import PHASE_LABELS, name_lookup, narrate

SUBMIT_TOOL = {
    Answer.SPEECH: "submit_speech (speech에 발언, 패스하려면 speech 생략)",
    Answer.TARGET: "submit_target (target에 좌석 번호, 기권하려면 target 생략)",
    Answer.APPROVAL: "submit_approval (approve에 true=찬성 / false=반대, 기권하려면 생략)",
}


def _persona(profile: Mapping[str, Any]) -> list[str]:
    lines = [
        f"- 이름: {profile.get('name')} ({profile.get('age')}세, {profile.get('occupation')})",
        f"- 성격: {profile.get('personality')}",
        f"- 말투: {profile.get('speech_style')}",
        f"- 남을 믿는 정도: {profile.get('trust')} · 거짓말 실력: {profile.get('deception')} · "
        f"말수: {profile.get('talkativeness')}",
    ]
    quirks: list[str] = profile.get("quirks") or []
    if quirks:
        lines.append("- 버릇: " + " / ".join(quirks))
    samples: list[str] = profile.get("sample_lines") or []
    if samples:
        lines.append("- 예시 대사: " + " / ".join(f'"{s}"' for s in samples))
    return lines


def render_seat_context(ctx: SeatContext, mode: str) -> str:
    seats = next(
        (e.body_as(SeatsAnnounced).seats for e in ctx.events if e.kind == SeatsAnnounced.KIND), ()
    )
    names = name_lookup(seats)
    brief = ctx.brief
    profile = next(
        (
            e.body_as(SeatProfileAssigned).profile
            for e in ctx.events
            if e.kind == SeatProfileAssigned.KIND
        ),
        None,
    )

    out = [f"# 당신은 {names(ctx.seat)} 입니다"]
    if profile:
        out += ["", "## 당신의 인격", *_persona(profile)]

    out += ["", "## 당신의 역할", f"**{brief['role']}** — {brief['role_description']}"]
    teammates: list[Mapping[str, Any]] = brief.get("mafia_teammates") or []
    if teammates:
        out.append("동료 마피아: " + ", ".join(names(t["seat"]) for t in teammates))

    alive = ", ".join(names(s) for s in brief.get("alive_seats", []))
    phase = PHASE_LABELS.get(str(brief.get("phase")), str(brief.get("phase")))
    status = "생존" if brief.get("alive") else "사망"
    out += [
        "",
        "## 현재 상황",
        f"{brief.get('day')}일차 · {phase} · 당신은 {status}",
        f"생존자: {alive}",
    ]

    history = [line for e in ctx.events if (line := narrate(e, mode, names))]
    out += ["", "## 당신이 아는 기록 (시간순)", *(history or ["(아직 없음)"])]

    out += ["", "## 지금 할 일"]
    if ctx.request is None:
        out.append("지금은 당신의 차례가 아닙니다. 아무것도 제출하지 말고 끝내세요.")
    else:
        r = ctx.request
        out.append(r.instruction)
        if r.options:
            out.append("선택지: " + ", ".join(names(o) for o in r.options))
        out.append(f"제출 도구: {SUBMIT_TOOL[r.answer]}")
        out.append(
            "모든 제출 도구에 private_note(나만 보는 메모, 다음 차례에 다시 보여 드립니다)와 "
            "suspicion({좌석 번호: 0~100 의심도})를 함께 넣을 수 있습니다."
        )
    return "\n".join(out)
