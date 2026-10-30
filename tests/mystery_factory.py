"""Builds a minimal scenario that satisfies every static rule, as plain data for mutation."""

from __future__ import annotations

from typing import Any

from mafia.modes.mystery.scenario import Scenario

NAMES = ("가람", "나연", "다온", "라희", "마루", "바다", "사랑", "아린")
SLOTS = ("21:00", "21:20", "21:40", "22:00", "22:20", "22:40")
CHARS = [f"c{i}" for i in range(1, 9)]
LOCS = [f"l{i}" for i in range(1, 7)]
CULPRIT = "c1"


def location_of(char_index: int, slot_index: int) -> str:
    return LOCS[(char_index + slot_index) % 6]


def clue_id(n: int) -> str:
    return f"k{n:02d}"


def scenario_data() -> dict[str, Any]:
    timeline: list[dict[str, Any]] = [
        {
            "slot": slot,
            "character_id": ch,
            "location_id": location_of(ci, si),
            "action": f"{NAMES[ci]}의 행동",
            "witnessed_by": [],
        }
        for si, slot in enumerate(SLOTS)
        for ci, ch in enumerate(CHARS)
    ]
    # c1 and c7 share a location at 21:00 (index 0 and 6 → same modulo 6): a valid witness
    timeline[0]["witnessed_by"] = ["c7"]

    clues: list[dict[str, Any]] = []
    for i, ch in enumerate(CHARS):
        clues.append(
            {
                "id": clue_id(i + 1),
                "title": f"{NAMES[i]}의 소지품",
                "text": "주머니 속 메모",
                "kind": "물건",
                "round": 0,
                "starting_holder": ch,
            }
        )
    n = 9
    for rnd in (1, 2):
        for loc in LOCS:
            for _ in range(2):
                clues.append(
                    {
                        "id": clue_id(n),
                        "title": f"카드 {n}",
                        "text": f"{loc}에서 발견된 흔적 {n}",
                        "kind": "흔적",
                        "round": rnd,
                        "location_id": loc,
                    }
                )
                n += 1
    by_id = {k["id"]: k for k in clues}
    # red herrings: k10 → c2 (resolved by k12), k14 → c3 (k16), k18 → c4 (k20)
    for herring, target, resolution in (
        ("k10", "c2", "k12"),
        ("k14", "c3", "k16"),
        ("k18", "c4", "k20"),
    ):
        by_id[herring].update(
            is_red_herring=True, points_to=[target], red_herring_resolution=[resolution]
        )
    by_id["k09"]["points_to"] = [CULPRIT]

    facts: list[dict[str, Any]] = [
        # decisive facts, each proven by cards from two different locations
        {
            "id": "f1",
            "statement": "범행 도구는 가람의 것",
            "decisive": True,
            "proven_by": ["k09", "k11"],
        },  # l1 r1, l2 r1
        {
            "id": "f2",
            "statement": "가람의 알리바이는 거짓",
            "decisive": True,
            "proven_by": ["k13", "k21"],
        },  # l3 r1, l1 r2
    ]
    for i, ch in enumerate(CHARS[1:], start=3):
        facts.append(
            {
                "id": f"f{i}",
                "statement": f"{ch}는 그 시각 다른 곳에 있었다",
                "proven_by": [clue_id(20 + i)],
                "excludes": ch,
            }
        )
    for k in clues:
        k.setdefault("supports", [])
    by_id["k09"]["supports"] = ["f1"]

    characters: list[dict[str, Any]] = []
    for ci, ch in enumerate(CHARS):
        sheet: dict[str, Any] = {
            "background": "배경",
            "relation_to_victim_truth": "관계",
            "secret": {"what": "비밀", "why_hide": "이유", "exposed_by": [clue_id(9 + ci)]},
            "motive": "동기",
            "night": [
                {"slot": slot, "location_id": location_of(ci, si), "action": "행동"}
                for si, slot in enumerate(SLOTS)
            ],
            "knowledge": [{"what": "들은 것", "slot": "21:00"}],
            "lies": [
                {
                    "about": "비밀",
                    "false_version": "거짓말",
                    "why": "이유",
                    "broken_by": [clue_id(9 + ci)],
                }
            ],
            "say_freely": ["말해도 되는 것"],
            "hide": ["숨길 것"],
            "relationships": {CHARS[(ci + 1) % 8]: "오랜 친구"},
            "goals": [
                {
                    "description": "증거를 지킨다",
                    "predicate": {"type": "hold_clue", "clue": clue_id(ci + 1)},
                    "points": 2,
                }
            ],
            "play_guide": {
                "temperament": "차분함",
                "speech_style": "존댓말",
                "sample_lines": ["안녕하세요", "그건 아니에요"],
            },
            "culprit_kit": None,
        }
        if ch == CULPRIT:
            sheet["culprit_kit"] = {
                "cover_story": "서재에 있었다",
                "dangerous_clues": ["k09"],
                "deflection": ["나연을 의심하게 만든다"],
            }
        characters.append(
            {
                "id": ch,
                "name": NAMES[ci],
                "age": 30 + ci,
                "occupation": "직업",
                "public_profile": "공개 소개",
                "public_relation_to_victim": "피해자와의 관계",
                "sheet": sheet,
            }
        )

    long_text = "안개 낀 밤, 오래된 건물의 복도에 발소리가 울렸다. " * 20
    return {
        "schema_version": 1,
        "concept": {
            "title": "제목",
            "logline": "한 줄 소개",
            "tone": "본격",
            "era": "현대",
            "setting": "연구소",
            "twist_type": "시간 착각",
        },
        "world": {
            "setting_description": "배경 설명",
            "locations": [{"id": loc, "name": f"장소{loc}", "description": "묘사"} for loc in LOCS],
        },
        "victim": {"name": "피해자", "public_profile": "공개", "true_background": "진짜 배경"},
        "truth": {
            "culprit_id": CULPRIT,
            "motive": "동기",
            "means": "수단",
            "opportunity": "기회",
            "summary": long_text,
            "time_slots": list(SLOTS),
            "timeline": timeline,
            "key_deductions": [
                {"step": 1, "claim": "도구의 주인", "evidence": ["f1", "k09"]},
                {"step": 2, "claim": "알리바이 붕괴", "evidence": ["f2"]},
                {"step": 3, "claim": "다른 사람은 불가능", "evidence": ["f3", "f4"]},
            ],
        },
        "facts": facts,
        "characters": characters,
        "clues": clues,
        "narration": {
            "prologue": long_text,
            "character_intros": {ch: "소개" for ch in CHARS},
            "round_intros": ["조사를 시작합니다", "두 번째 조사입니다"],
            "midgame_twist": "새로운 사실이 드러났다",
            "accusation_intro": "이제 범인을 지목할 시간",
            "ending_caught": "범인이 잡혔다",
            "ending_escaped": "범인이 빠져나갔다",
            "truth_reveal": long_text * 2,
            "epilogues": {ch: "그 후" for ch in CHARS},
        },
    }


def valid_scenario() -> Scenario:
    return Scenario.model_validate(scenario_data())
