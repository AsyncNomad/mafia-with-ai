"""Markdown for the human's chat. Same visibility as any seat: the human sees only what their
own seat may see (or only public events when no human plays)."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from mafia.engine.core_events import PhaseChanged, SeatInfo
from mafia.engine.events import Event
from mafia.engine.requests import Answer, SeatRequest
from mafia.views.mystery_sheet import render_sheet
from mafia.views.narration import name_lookup, narrate

SEAT_MARKS = ("🟥", "🟧", "🟨", "🟩", "🟦", "🟪", "🟫", "⬜")

HUMAN_HOW_TO: Mapping[Answer, str] = {
    Answer.SPEECH: "채팅창에 그대로 말하면 발언이 됩니다. 패스는 `:pass`",
    Answer.TARGET: "`:vote 이름` 또는 `:vote 번호`로 고르세요. 기권은 `:pass`",
    Answer.APPROVAL: "`:yes`(찬성) / `:no`(반대) / `:pass`(기권)",
}


def _marked_names(seats: Sequence[SeatInfo]) -> Callable[[int | None], str]:
    plain = name_lookup(seats)

    def names(seat: int | None) -> str:
        if seat is None:
            return plain(None)
        return f"{SEAT_MARKS[(seat - 1) % len(SEAT_MARKS)]}{plain(seat)}"

    return names


def render_events(events: Sequence[Event], seats: Sequence[SeatInfo], mode: str) -> str:
    names = _marked_names(seats)
    lines: list[str] = []
    for event in events:
        line = narrate(event, mode, names)
        if line is None:
            continue
        if event.kind == PhaseChanged.KIND:
            lines += ["", f"### {line.strip('─ ')}"]
        else:
            lines.append(f"- {line}")
    return "\n".join(lines).strip()


def render_role_card(brief: Mapping[str, Any], seats: Sequence[SeatInfo]) -> str:
    if brief.get("mode") == "mystery":
        return render_sheet(brief, heading="###")
    names = _marked_names(seats)
    lines = [
        f"> **당신의 역할: {brief['role']}** — {brief['role_description']}",
    ]
    teammates: list[Mapping[str, Any]] = brief.get("mafia_teammates") or []
    if teammates:
        lines.append("> 동료 마피아: " + ", ".join(names(t["seat"]) for t in teammates))
    return "\n".join(lines)


def render_human_request(request: SeatRequest, seats: Sequence[SeatInfo]) -> str:
    names = _marked_names(seats)
    lines = [f"**🙋 당신의 차례입니다.** {request.instruction}"]
    if request.options:
        if request.option_labels:
            listed = ", ".join(
                f"{o}. {label}"
                for o, label in zip(request.options, request.option_labels, strict=True)
            )
            lines.append(f"선택지: {listed}")
            lines.append("`:go 장소이름` 또는 `:go 번호`로 고르세요. 기권(무작위 장소)은 `:pass`")
            return "\n".join(lines)
        lines.append("선택지: " + ", ".join(names(o) for o in request.options))
    lines.append(HUMAN_HOW_TO[request.answer])
    if request.answer is Answer.SPEECH and "reveal" in request.instruction:
        lines.append(
            "카드를 공개하며 말하려면 `:show 카드id 할 말` (예: `:show k12 이 영수증을 보세요`)"
        )
    return "\n".join(lines)
