"""The text an AI seat receives from `my_turn`. Built only from an engine `SeatContext`, which
already contains nothing but what that seat may know."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from mafia.engine.core_events import SeatProfileAssigned, SeatsAnnounced
from mafia.engine.engine import SeatContext
from mafia.engine.requests import Answer
from mafia.views.mystery_sheet import render_sheet
from mafia.views.narration import name_lookup, narrate, phase_label

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


ORIGINAL_TIPS = {
    "마피아": "시민인 척하세요. 동료를 노골적으로 감싸면 함께 의심받습니다. 표가 시민에게 몰리도록 그럴듯한 근거로 의심을 돌리고, 밤 대화에서는 경찰이나 의사로 보이는 사람을 우선 노리세요.",
    "경찰": "조사 결과는 당신만 압니다. 너무 일찍 밝히면 밤에 표적이 됩니다. 마피아를 찾았거나 판이 기울 때 밝혀 표를 모으세요. 마피아가 경찰을 사칭할 수도 있습니다.",
    "의사": "정체를 숨기고, 마피아가 노릴 만한 사람(활약하는 사람, 경찰로 보이는 사람)을 지키세요.",
    "시민": "발언과 투표 패턴에서 모순을 찾으세요. 근거 없는 처형에는 반대할 수 있습니다.",
}

# The table's shared register. Without it models write Korean like a speech or a court ruling.
# Original mode only: mystery characters speak in their scenario's era instead.
ORIGINAL_VOICE = """\
처음 만난 20~30대 또래끼리 하는 온라인 마피아 채팅입니다. 당신의 인격 말투는 이 위에 얹습니다.
- 기본은 가벼운 해요체이고 상대는 "○○ 님"으로 부릅니다. 흥분하거나 맞장구칠 땐 반말이 섞여도 됩니다.
- 손으로 빠르게 치는 채팅입니다. 짧게 끊어 말하고, 문장을 글처럼 다듬지 않습니다.
- "아 근데", "솔직히", "~인 듯", "~각", "ㄹㅇ", "ㅋㅋ" 같은 말은 **가끔** 씁니다. 매 문장에 넣지는 않습니다.
- 쓰지 않는 말: 하십시오체("~습니다", "~하십시오"), "~바랍니다", "판단하겠습니다", "양측을 정리하면", "양반", "허허", "~구먼", 속담, 연설이나 공문 같은 문장, 이모지
- 고쳐 쓰는 예
  - ✗ "민지 님과 서연 님께서 각자 누구를 의심하는지 먼저 답해 주시기 바랍니다." → ○ "민지 님 서연 님, 둘 다 누구 찍을 건지부터 말해 봐요"
  - ✗ "도윤 님을 노린 건 분석이 날카로워 위협이 된다고 본 쪽일 겁니다." → ○ "도윤 님 죽은 거 보면 마피아가 분석하는 사람 싫어하는 듯. 민지 님 두 번 연속 패스한 거 좀 걸려요"
  - ✗ "확증 없이 사람을 처형할 수는 없습니다." → ○ "아니 증거도 없는데 보내는 건 좀 아닌 거 같아요\""""

MYSTERY_TIPS_INNOCENT = (
    "범인을 찾되 당신의 비밀도 지키세요. 시트의 '거짓말'과 '숨길 것'을 지키되, 범행과 직결된 사실은 숨기지 마세요. "
    "결정적인 카드는 아껴 두었다가 공개하면 토론을 뒤집을 수 있습니다. 공개는 엔진이 원문을 보여 주므로 위조할 수 없고, "
    "말로만 하는 주장은 거짓일 수 있다는 점을 모두가 압니다. 개인 목표도 점수가 됩니다."
)
MYSTERY_TIPS_CULPRIT = (
    "당신은 범인입니다. 위장 알리바이를 일관되게 유지하고, 위험한 카드가 공개되면 시트의 대응 논리로 둘러대세요. "
    "다른 사람의 수상한 점(그들의 비밀)으로 의혹을 돌리되, 기록에 남은 공개 사실과 모순되는 말은 하지 마세요. "
    "최종 지목에서 당신이 아닌 사람이 지목되면 당신의 승리입니다."
)


def _original_head(ctx: SeatContext, mode: str, names: Callable[[int | None], str]) -> list[str]:
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
        out += ["", "## 말투", ORIGINAL_VOICE]
    out += ["", "## 당신의 역할", f"**{brief['role']}** — {brief['role_description']}"]
    teammates: list[Mapping[str, Any]] = brief.get("mafia_teammates") or []
    if teammates:
        out.append("동료 마피아: " + ", ".join(names(t["seat"]) for t in teammates))
    tip = ORIGINAL_TIPS.get(str(brief.get("role")))
    if tip:
        out.append(f"요령: {tip}")
    alive = ", ".join(names(s) for s in brief.get("alive_seats", []))
    status = "생존" if brief.get("alive") else "사망"
    out += [
        "",
        "## 현재 상황",
        f"{brief.get('day')}일차 · {phase_label(mode, str(brief.get('phase')))} · 당신은 {status}",
        f"생존자: {alive}",
    ]
    return out


def _mystery_head(ctx: SeatContext, mode: str) -> list[str]:
    brief = ctx.brief
    character: Mapping[str, Any] | None = brief.get("character")
    culprit = bool(character and character["sheet"].get("culprit_kit"))
    phase = phase_label(mode, str(brief.get("phase")))
    return [
        f"# 머더 미스터리 「{brief.get('title')}」",
        "",
        render_sheet(brief, heading="##"),
        "",
        "## 요령",
        MYSTERY_TIPS_CULPRIT if culprit else MYSTERY_TIPS_INNOCENT,
        "",
        "## 현재 상황",
        f"{phase}"
        + (f" · {brief.get('investigation')}차 조사 이후" if brief.get("investigation") else ""),
        "조사할 수 있는 곳: "
        + ", ".join(f"{i}. {n}" for i, n in enumerate(brief.get("locations", []), start=1)),
    ]


def render_seat_context(ctx: SeatContext, mode: str) -> str:
    seats = next(
        (e.body_as(SeatsAnnounced).seats for e in ctx.events if e.kind == SeatsAnnounced.KIND), ()
    )
    names = name_lookup(seats)
    out = (
        _mystery_head(ctx, mode)
        if ctx.brief.get("mode") == "mystery"
        else _original_head(ctx, mode, names)
    )

    history = [line for e in ctx.events if (line := narrate(e, mode, names))]
    out += ["", "## 당신이 아는 기록 (시간순)", *(history or ["(아직 없음)"])]

    out += ["", "## 지금 할 일"]
    if ctx.request is None:
        out.append("지금은 당신의 차례가 아닙니다. 아무것도 제출하지 말고 끝내세요.")
    else:
        r = ctx.request
        out.append(r.instruction)
        if r.options:
            if r.option_labels:
                listed = ", ".join(
                    f"{o}. {label}" for o, label in zip(r.options, r.option_labels, strict=True)
                )
            else:
                listed = ", ".join(names(o) for o in r.options)
            out.append(f"선택지 (target에 번호): {listed}")
        out.append(f"제출 도구: {SUBMIT_TOOL[r.answer]}")
        out.append(
            "모든 제출 도구에 private_note(나만 보는 메모, 다음 차례에 다시 보여 드립니다)와 "
            "suspicion({좌석 번호: 0~100 의심도})를 함께 넣을 수 있습니다."
        )
    return "\n".join(out)
