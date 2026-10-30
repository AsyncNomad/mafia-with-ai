from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from mafia.modes.mystery.scenario import Scenario
from mafia.modes.mystery.validate import Severity, validate
from tests.mystery_factory import scenario_data, valid_scenario

Mutation = Callable[[dict[str, Any]], object]


def codes(data: dict[str, Any]) -> set[str]:
    return {i.code for i in validate(Scenario.model_validate(data)) if i.severity is Severity.ERROR}


def test_factory_scenario_is_valid() -> None:
    issues = validate(valid_scenario())
    assert [i for i in issues if i.severity is Severity.ERROR] == []
    assert [i for i in issues if i.severity is Severity.WARNING] == []


def _clue(d: dict[str, Any], clue_id: str) -> dict[str, Any]:
    return next(k for k in d["clues"] if k["id"] == clue_id)


def _char(d: dict[str, Any], char_id: str) -> dict[str, Any]:
    return next(c for c in d["characters"] if c["id"] == char_id)


def _fact(d: dict[str, Any], fact_id: str) -> dict[str, Any]:
    return next(f for f in d["facts"] if f["id"] == fact_id)


MUTATIONS: dict[str, tuple[Mutation, str]] = {
    "drop a character": (lambda d: d["characters"].pop(), "character_count"),
    "duplicate clue id": (lambda d: _clue(d, "k10").update(id="k09"), "duplicate_id"),
    "duplicate name": (lambda d: _char(d, "c2").update(name="가람"), "duplicate_name"),
    "too many clues in one place": (
        lambda d: _clue(d, "k11").update(location_id="l1"),
        "clues_per_location",
    ),
    "starting card with a location": (
        lambda d: _clue(d, "k01").update(location_id="l1"),
        "clue_placement",
    ),
    "two starting cards": (lambda d: _clue(d, "k02").update(starting_holder="c1"), "starting_hand"),
    "goal on missing clue": (
        lambda d: _char(d, "c3")["sheet"]["goals"][0]["predicate"].update(clue="k99"),
        "dangling_reference",
    ),
    "relationship with self": (
        lambda d: _char(d, "c2")["sheet"].update(relationships={"c2": "나"}),
        "self_relationship",
    ),
    "culprit without kit": (
        lambda d: _char(d, "c1")["sheet"].update(culprit_kit=None),
        "culprit_kit",
    ),
    "innocent with kit": (
        lambda d: _char(d, "c2")["sheet"].update(
            culprit_kit=_char(d, "c1")["sheet"]["culprit_kit"]
        ),
        "culprit_kit",
    ),
    "timeline gap": (lambda d: d["truth"]["timeline"].pop(5), "timeline_gap"),
    "timeline conflict": (
        lambda d: d["truth"]["timeline"].append({**d["truth"]["timeline"][0], "location_id": "l6"}),
        "timeline_conflict",
    ),
    "impossible witness": (
        lambda d: d["truth"]["timeline"][0].update(witnessed_by=["c2"]),
        "impossible_witness",
    ),
    "sheet contradicts truth": (
        lambda d: _char(d, "c4")["sheet"]["night"][2].update(
            location_id="l1" if _char(d, "c4")["sheet"]["night"][2]["location_id"] != "l1" else "l2"
        ),
        "sheet_contradicts_truth",
    ),
    "sheet missing a slot": (lambda d: _char(d, "c5")["sheet"]["night"].pop(), "sheet_gap"),
    "decisive fact from one place": (
        lambda d: _fact(d, "f1").update(proven_by=["k09", "k10"]),
        "fragile_decisive_fact",
    ),
    "no decisive fact": (
        lambda d: [f.update(decisive=False) for f in d["facts"]],
        "no_decisive_fact",
    ),
    "innocent never excluded": (lambda d: d["facts"].remove(_fact(d, "f3")), "not_excluded"),
    "culprit excluded": (lambda d: _fact(d, "f3").update(excludes="c1"), "culprit_excluded"),
    "unresolved red herring": (
        lambda d: _clue(d, "k10").update(red_herring_resolution=[]),
        "unresolved_red_herring",
    ),
    "red herring at the culprit": (
        lambda d: _clue(d, "k10").update(points_to=["c1"]),
        "red_herring_target",
    ),
    "too few red herrings": (
        lambda d: _clue(d, "k14").update(is_red_herring=False, red_herring_resolution=[]),
        "too_few_red_herrings",
    ),
    "unlock cycle": (
        lambda d: (
            _clue(d, "k25").update(unlock_condition="k26"),
            _clue(d, "k26").update(unlock_condition="k25"),
        ),
        "unlock_cycle",
    ),
    "unlock from a later round": (
        lambda d: _clue(d, "k09").update(unlock_condition="k21"),
        "unlock_order",
    ),
    "missing epilogue": (lambda d: d["narration"]["epilogues"].pop("c8"), "narration_missing"),
    "deduction steps out of order": (
        lambda d: d["truth"]["key_deductions"][1].update(step=5),
        "deduction_steps",
    ),
    "deduction cites nothing real": (
        lambda d: d["truth"]["key_deductions"][0].update(evidence=["f99"]),
        "dangling_reference",
    ),
}


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_each_defect_is_caught_by_its_rule(name: str) -> None:
    mutate, expected = MUTATIONS[name]
    data = scenario_data()
    mutate(data)
    assert expected in codes(data), f"{name}: expected {expected}, got {codes(data)}"


def test_warnings_do_not_block() -> None:
    data = scenario_data()
    data["narration"]["prologue"] = "짧은 프롤로그"
    data["characters"][1]["name"] = "가온"  # shares the first syllable with 가람
    issues = validate(Scenario.model_validate(data))
    assert {i.code for i in issues} == {"text_length", "similar_names"}
    assert all(i.severity is Severity.WARNING for i in issues)


def test_leak_warning_for_culprit_name_in_public_text() -> None:
    data = scenario_data()
    data["narration"]["midgame_twist"] = "범인은 가람일지도 모른다는 소문이 돌았다"
    assert "possible_leak" in {i.code for i in validate(Scenario.model_validate(data))}


def test_schema_rejects_malformed_ids_and_unknown_fields() -> None:
    data = scenario_data()
    data["clues"][0]["id"] = "clue-1"
    with pytest.raises(ValueError):
        Scenario.model_validate(data)
    data = scenario_data()
    data["concept"]["mood"] = "extra"
    with pytest.raises(ValueError):
        Scenario.model_validate(data)


def test_schema_rejects_unknown_goal_predicate() -> None:
    data = scenario_data()
    data["characters"][0]["sheet"]["goals"][0]["predicate"] = {"type": "said_word", "word": "x"}
    with pytest.raises(ValueError):
        Scenario.model_validate(data)
