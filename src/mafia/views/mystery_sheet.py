"""The character sheet a murder-mystery seat plays from. Built from that seat's own brief only."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_PREDICATES = {
    "hold_clue": "게임이 끝날 때 카드 {clue}를 갖고 있기",
    "clue_not_revealed": "카드 {clue}가 끝까지 공개되지 않기",
    "clue_revealed": "카드 {clue}가 공개되기",
    "accused_is": "{character}이(가) 범인으로 지목되기",
    "accused_is_not": "{character}이(가) 범인으로 지목되지 않기",
    "culprit_caught": "진범이 지목되기",
    "culprit_escaped": "진범이 지목되지 않기",
    "voted_for": "범인 지목에서 {character}에게 투표하기",
}


def _who(names: Mapping[str, str], cid: str) -> str:
    return names.get(cid, cid)


def render_sheet(brief: Mapping[str, Any], *, heading: str = "##") -> str:
    ch: Mapping[str, Any] | None = brief.get("character")
    if ch is None:
        return "(인물 배정 전)"
    names: Mapping[str, str] = brief.get("names_by_character", {})
    places: Mapping[str, str] = brief.get("location_names", {})
    sh: Mapping[str, Any] = ch["sheet"]
    h = heading
    out = [
        f"{h} 당신은 {ch['name']} ({ch['age']}세, {ch['occupation']})",
        f"공개 소개: {ch['public_profile']} / 피해자와의 관계(공개): {ch['public_relation_to_victim']}",
        "",
        f"{h}# 배경",
        sh["background"],
        f"피해자와의 진짜 관계: {sh['relation_to_victim_truth']}",
        f"동기(남들이 의심할 만한): {sh['motive']}",
        "",
        f"{h}# 당신의 비밀",
        f"{sh['secret']['what']} — 숨기는 이유: {sh['secret']['why_hide']}",
        f"이 비밀이 드러날 수 있는 카드: {', '.join(sh['secret']['exposed_by'])}",
        "",
        f"{h}# 그날 밤 당신이 실제로 한 일",
        *(
            f"- {n['slot']} {places.get(n['location_id'], n['location_id'])}: {n['action']}"
            for n in sh["night"]
        ),
    ]
    if sh.get("knowledge"):
        out += ["", f"{h}# 당신이 보고 들은 것"]
        out += [
            f"- {('[' + k['slot'] + '] ') if k.get('slot') else ''}{k['what']}"
            for k in sh["knowledge"]
        ]
    if sh.get("lies"):
        out += ["", f"{h}# 남들에게 할 거짓말"]
        out += [
            f'- {lie["about"]}: "{lie["false_version"]}" (이유: {lie["why"]})' for lie in sh["lies"]
        ]
    if sh.get("say_freely"):
        out += ["", f"{h}# 말해도 되는 것", *(f"- {x}" for x in sh["say_freely"])]
    if sh.get("hide"):
        out += ["", f"{h}# 숨길 것", *(f"- {x}" for x in sh["hide"])]
    if sh.get("relationships"):
        out += ["", f"{h}# 관계"]
        out += [f"- {_who(names, cid)}: {text}" for cid, text in sh["relationships"].items()]
    out += ["", f"{h}# 개인 목표 (게임이 끝나면 점수로 정산)"]
    for goal in sh["goals"]:
        p = goal["predicate"]
        template = _PREDICATES.get(str(p["type"])) or str(p["type"])
        rule = template.format(
            clue=p.get("clue", ""), character=_who(names, p.get("character", ""))
        )
        out.append(f"- {goal['description']} ({rule}, {goal['points']}점)")
    guide = sh["play_guide"]
    out += [
        "",
        f"{h}# 연기 지침",
        f"성향: {guide['temperament']} / 말투: {guide['speech_style']}",
        "예시 대사: " + " / ".join(f'"{s}"' for s in guide["sample_lines"]),
    ]
    kit = sh.get("culprit_kit")
    if kit:
        out += [
            "",
            f"{h}# ⚠ 당신이 범인입니다",
            f"위장 알리바이: {kit['cover_story']}",
            f"당신을 위험하게 만드는 카드: {', '.join(kit['dangerous_clues'])}",
            *(f"- 의혹을 돌릴 재료: {d}" for d in kit["deflection"]),
        ]
    hand: list[Mapping[str, Any]] = brief.get("hand", [])
    out += ["", f"{h}# 손에 든 카드"]
    if not hand:
        out.append("(없음)")
    for card in hand:
        flag = " (공개됨)" if card.get("revealed") else ""
        out.append(f'- [{card["id"]}] {card["title"]}: "{card["text"]}" — {card["where"]}{flag}')
    return "\n".join(out)
