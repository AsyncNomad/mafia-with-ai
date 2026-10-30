"""Post-game review ("복기"). Shown only after the game has ended, when nothing is secret anymore."""

from __future__ import annotations

from collections.abc import Sequence

from mafia.engine.core_events import ActionSubmitted, GameEnded, SeatInfo
from mafia.engine.events import Event
from mafia.modes.original.events import NightResolved, RolesRevealed
from mafia.modes.original.roles import Role
from mafia.views.narration import TEAM_LABELS, name_lookup

_NOTE_CHARS = 140


def render_postmortem(log: Sequence[Event], seats: Sequence[SeatInfo], mode: str) -> str | None:
    if mode != "original" or not any(e.kind == GameEnded.KIND for e in log):
        return None
    names = name_lookup(seats)
    roles: dict[int, Role] = {}
    for e in log:
        if e.kind == RolesRevealed.KIND:
            roles = dict(e.body_as(RolesRevealed).roles)
    ended = next(e.body_as(GameEnded) for e in log if e.kind == GameEnded.KIND)

    def who(seat: int | None) -> str:
        if seat is None:
            return "—"
        role = roles.get(seat)
        return f"{names(seat)} {role.label}" if role else names(seat)

    out = ["## 🔍 복기", f"**{TEAM_LABELS.get(ended.winner, ended.winner)} 승리**"]

    nights = [e.body_as(NightResolved) for e in log if e.kind == NightResolved.KIND]
    if nights:
        out += [
            "",
            "### 밤마다 실제로 일어난 일",
            "| 밤 | 마피아가 노린 사람 | 의사가 지킨 사람 | 경찰이 조사한 사람 | 결과 |",
            "|---|---|---|---|---|",
        ]
        for n in nights:
            if n.killed is not None:
                result = f"{names(n.killed)} 사망"
            elif n.mafia_target is not None and n.mafia_target == n.protected:
                result = "의사가 막아냄"
            else:
                result = "아무도 죽지 않음"
            out.append(
                f"| {n.day}일차 밤 | {who(n.mafia_target)} | {who(n.protected)} | "
                f"{who(n.investigated)} | {result} |"
            )

    actions = [e.body_as(ActionSubmitted) for e in log if e.kind == ActionSubmitted.KIND]
    last_suspicion: dict[int, dict[int, int]] = {}
    last_note: dict[int, str] = {}
    for a in actions:
        if a.action.suspicion:
            last_suspicion[a.seat] = dict(a.action.suspicion)
        if a.action.private_note:
            last_note[a.seat] = a.action.private_note

    if last_suspicion:
        cols = [s.seat for s in seats]
        out += [
            "",
            "### 마지막 의심도 (0~100, 각 줄이 그 사람의 시선)",
            "| 평가자 \\ 대상 | " + " | ".join(str(c) for c in cols) + " |",
            "|---" * (len(cols) + 1) + "|",
        ]
        for rater in cols:
            row = last_suspicion.get(rater)
            if row is None:
                continue
            cells = ["·" if c == rater else str(row.get(c, "")) for c in cols]
            out.append(f"| {who(rater)} | " + " | ".join(cells) + " |")

    if last_note:
        out += ["", "### 각자의 마지막 속마음"]
        for seat in sorted(last_note):
            note = last_note[seat].replace("\n", " ")
            if len(note) > _NOTE_CHARS:
                note = note[: _NOTE_CHARS - 1] + "…"
            out.append(f'- {who(seat)}: "{note}"')

    out += ["", "전체 기록은 터미널에서 `uv run mafia replay --god`으로 다시 볼 수 있습니다."]
    return "\n".join(out)
