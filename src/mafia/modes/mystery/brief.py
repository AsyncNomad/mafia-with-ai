"""The architect's brief: seeded choices that force variety, plus the user's own request."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from mafia.engine.runtime import rng_for
from mafia.modes.mystery.scenario import (
    Character,
    Clue,
    Concept,
    Fact,
    Narration,
    Scenario,
    Truth,
    Victim,
    World,
)

# Submission order: truth first, because everything else is derived from it.
SECTIONS: tuple[str, ...] = (
    "concept",
    "world",
    "victim",
    "truth",
    "facts",
    "characters",
    "clues",
    "narration",
)
SECTION_TYPES: dict[str, Any] = {
    "concept": Concept,
    "world": World,
    "victim": Victim,
    "truth": Truth,
    "facts": tuple[Fact, ...],
    "characters": tuple[Character, ...],
    "clues": tuple[Clue, ...],
    "narration": Narration,
}


class BriefPools(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    settings: tuple[str, ...] = Field(min_length=1)
    tones: tuple[str, ...] = Field(min_length=1)
    twist_types: tuple[str, ...] = Field(min_length=1)
    banned_cliches: tuple[str, ...] = ()
    content_rules: tuple[str, ...] = ()


class Brief(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    seed: str
    setting: str
    tone: str
    twist_type: str
    banned_cliches: tuple[str, ...]
    content_rules: tuple[str, ...]
    user_request: str = ""


def load_pools(path: Path) -> BriefPools:
    with path.open("rb") as f:
        return BriefPools.model_validate(tomllib.load(f))


def make_brief(seed: str, pools: BriefPools, user_request: str = "") -> Brief:
    rng = rng_for(seed, "mystery_brief")
    return Brief(
        seed=seed,
        setting=rng.choice(pools.settings),
        tone=rng.choice(pools.tones),
        twist_type=rng.choice(pools.twist_types),
        banned_cliches=pools.banned_cliches,
        content_rules=pools.content_rules,
        user_request=user_request.strip(),
    )


def section_schemas() -> dict[str, Any]:
    """JSON schema of each submittable section, sharing one set of definitions."""
    full = Scenario.model_json_schema()
    props = full["properties"]
    return {
        "sections": {name: props[name] for name in SECTIONS},
        "$defs": full.get("$defs", {}),
    }


def render_brief(brief: Brief) -> str:
    lines = ["# 시나리오 브리프", ""]
    if brief.user_request:
        lines += [
            "## 플레이어의 요청 (가장 우선)",
            brief.user_request,
            "요청이 아래 배경·톤·반전과 충돌하면 요청을 따르세요.",
            "",
        ]
    lines += [
        "## 배경",
        brief.setting,
        "",
        "## 톤",
        brief.tone,
        "",
        "## 반전 유형",
        brief.twist_type,
        "",
        "## 쓰지 말 것 (요청이 명시하지 않는 한)",
        *(f"- {c}" for c in brief.banned_cliches),
        "",
        "## 수위 규칙",
        *(f"- {r}" for r in brief.content_rules),
        "",
        "## 고정 규격",
        "- 인물 8명(c1~c8), 장소 6곳(l1~l6), 피해자는 NPC, 범인 1명",
        "- 시작 카드: 인물마다 1장(round 0, starting_holder)",
        "- 조사 카드: 장소마다 라운드(1, 2)마다 2장 = 24장 (location_id). unlock_condition이 있는 카드는 이 수에 포함하지 않는 추가 카드",
        "- 핵심 시간대 슬롯 6~12칸, 모든 슬롯마다 8명 전원의 위치",
        "",
        "## 제출",
        f"섹션 순서: {' → '.join(SECTIONS)}. 각 섹션은 `mystery_submit_section(section, payload)`로 냅니다.",
        "facts, characters, clues의 payload는 배열이고, 나머지는 객체입니다. 아래 JSON 스키마를 정확히 따르세요.",
        "",
        "## 섹션별 JSON 스키마",
        "```json",
        json.dumps(section_schemas(), ensure_ascii=False, indent=1),
        "```",
    ]
    return "\n".join(lines)
