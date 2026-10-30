"""What each pipeline agent is allowed to read, as pure functions of a scenario.

Blind packets (auditor stage 1, solvers) contain exactly what players could see: public texts
and clue card *wording*. Never the truth, sheets, fact graph, or a card's design metadata
(`supports`, `points_to`, `is_red_herring`, ...) - those would hand the solver the answer.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from enum import StrEnum
from typing import Any

from mafia.engine.runtime import rng_for
from mafia.modes.mystery.scenario import Clue, Scenario

PARTIAL_DROP_PER_ROUND = 4


class SolverKind(StrEnum):
    FULL = "full"  # every card
    PARTIAL = "partial"  # 4 cards per round missing, like a real game
    ROUND1 = "round1"  # starting hands + round 1 only: must NOT already be solvable


def public_texts(s: Scenario) -> dict[str, Any]:
    return {
        "title": s.concept.title,
        "logline": s.concept.logline,
        "prologue": s.narration.prologue,
        "setting": s.world.setting_description,
        "locations": [
            {"id": loc.id, "name": loc.name, "description": loc.description}
            for loc in s.world.locations
        ],
        "victim": {"name": s.victim.name, "profile": s.victim.public_profile},
        "suspects": [
            {
                "id": c.id,
                "name": c.name,
                "age": c.age,
                "occupation": c.occupation,
                "profile": c.public_profile,
                "relation_to_victim": c.public_relation_to_victim,
                "intro": s.narration.character_intros.get(c.id, ""),
            }
            for c in s.characters
        ],
        "round_intros": list(s.narration.round_intros),
        "midgame_twist": s.narration.midgame_twist,
    }


def card(s: Scenario, k: Clue) -> dict[str, Any]:
    """A clue as a player holds it: wording and where it was found, nothing else."""
    if k.round == 0 and k.starting_holder:
        where = f"{s.character(k.starting_holder).name}의 시작 카드"
    else:
        loc = next((x.name for x in s.world.locations if x.id == k.location_id), k.location_id)
        where = f"{loc} · {k.round}라운드"
    return {"id": k.id, "title": k.title, "kind": k.kind, "where": where, "text": k.text}


def solver_cards(s: Scenario, kind: SolverKind, seed: str) -> list[Clue]:
    match kind:
        case SolverKind.FULL:
            return list(s.clues)
        case SolverKind.ROUND1:
            return [k for k in s.clues if k.round in (0, 1)]
        case SolverKind.PARTIAL:
            rng = rng_for(seed, "partial_solver")
            dropped: set[str] = set()
            for rnd in (1, 2):
                pool = sorted(k.id for k in s.clues if k.round == rnd)
                dropped |= set(rng.sample(pool, min(PARTIAL_DROP_PER_ROUND, len(pool))))
            return [k for k in s.clues if k.id not in dropped]


def _dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=1)


def blind_packet(s: Scenario, cards: Sequence[Clue] | None = None) -> str:
    chosen = list(s.clues if cards is None else cards)
    return "\n".join(
        [
            "# 공개 정보",
            _dump(public_texts(s)),
            "",
            f"# 단서 카드 ({len(chosen)}장)",
            _dump([card(s, k) for k in chosen]),
        ]
    )


def solver_packet(s: Scenario, cards: Sequence[Clue], job_id: str) -> str:
    return "\n".join(
        [
            f"작업 번호: {job_id} (mystery_submit_solution에 그대로 넣으세요)",
            "용의자 id(c1~c8)로 답하고, 근거에는 카드 id를 쓰세요.",
            "",
            blind_packet(s, cards),
        ]
    )


def full_packet(s: Scenario, warnings: Sequence[str]) -> str:
    return "\n".join(
        [
            "# 전체 시나리오 (진상 포함)",
            _dump(s.model_dump(mode="json")),
            "",
            "# 정적 검증 경고",
            *(warnings or ["(없음)"]),
        ]
    )
