from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mafia.app.prep import (
    ARCHITECT,
    AUDITOR,
    MAX_REVISIONS,
    MAX_WAKES_PER_STAGE,
    QUICK_SOLVER,
    SOLVER,
    PrepService,
    Stage,
)
from mafia.engine.runtime import FixedClock
from mafia.modes.mystery.brief import SECTIONS
from mafia.modes.mystery.packets import SolverKind, blind_packet, solver_cards
from mafia.modes.mystery.scenario import Scenario
from mafia.store.db import connect
from mafia.store.scenarios import ScenarioStore
from tests.mystery_factory import CULPRIT, scenario_data

PASS_AUDIT = {
    "verdict": "pass",
    "findings": [{"severity": "minor", "problem": "문장"}],
    "summary": "좋음",
}


@pytest.fixture
def prep(tmp_path: Path) -> PrepService:
    return PrepService(ScenarioStore(connect(tmp_path / "game.db")), FixedClock())


def design(prep: PrepService, data: dict[str, Any] | None = None) -> str:
    data = data or scenario_data()
    for section in SECTIONS:
        assert prep.submit_section(ARCHITECT, section, data[section]).startswith("접수")
    return prep.check(ARCHITECT)


def solve_all(prep: PrepService, answers: dict[SolverKind, tuple[str, int]]) -> None:
    progress = prep.advance()
    assert progress.stage is Stage.SOLVE
    for agent in progress.agents:
        packet = prep.solver_packet(agent)
        job_id = packet.split("작업 번호: ")[1].split(" ")[0]
        state = prep.active()
        assert state is not None
        job = next(j for j in state.solvers if j.job_id == job_id)
        culprit, confidence = answers[job.kind]
        result = prep.submit_solution(agent, job_id, {"culprit": culprit, "confidence": confidence})
        assert result.startswith("접수"), result


GOOD_ANSWERS = {
    SolverKind.FULL: (CULPRIT, 90),
    SolverKind.PARTIAL: (CULPRIT, 70),
    SolverKind.ROUND1: ("undetermined", 20),
}


def test_happy_path_ends_in_the_library(prep: PrepService) -> None:
    prep.start("1930년대 경성 백화점으로", seed="happy")
    first = prep.advance()
    assert (first.stage, first.agents) == (Stage.DESIGN, (ARCHITECT,))
    assert prep.is_awaiting(ARCHITECT) and not prep.is_awaiting(AUDITOR)
    brief = prep.architect_brief(ARCHITECT)
    assert "1930년대 경성 백화점으로" in brief and '"sections"' in brief

    assert design(prep).startswith("정적 검증 통과")
    audit = prep.advance()
    assert (audit.stage, audit.agents) == (Stage.AUDIT, (AUDITOR,))
    assert prep.submit_audit(AUDITOR, PASS_AUDIT).startswith("접수")

    solve_all(prep, GOOD_ANSWERS)
    done = prep.advance()
    assert done.stage is Stage.VERIFIED and done.agents == ()
    assert prep.active() is None
    saved = prep.store.next_unplayed()
    assert saved is not None and saved.scenario is not None
    assert Scenario.model_validate(saved.scenario).truth.culprit_id == CULPRIT


def test_solver_wakes_are_parallel_and_claims_are_distinct(prep: PrepService) -> None:
    prep.start(seed="claims")
    prep.advance()
    design(prep)
    prep.advance()
    prep.submit_audit(AUDITOR, PASS_AUDIT)
    progress = prep.advance()
    assert sorted(progress.agents) == sorted([SOLVER, SOLVER, SOLVER, QUICK_SOLVER])
    packets = [prep.solver_packet(SOLVER) for _ in range(3)]
    ids = {p.split("작업 번호: ")[1].split(" ")[0] for p in packets}
    assert len(ids) == 3
    assert "남은 풀이 작업이 없습니다" in prep.solver_packet(SOLVER)
    quick = prep.solver_packet(QUICK_SOLVER)
    assert '"k21"' not in quick  # round 2 cards are not in the round-1 packet
    assert prep.submit_solution(SOLVER, "nope", {"culprit": "c1", "confidence": 1}).startswith(
        "거부됨"
    )
    quick_id = quick.split("작업 번호: ")[1].split(" ")[0]
    assert prep.submit_solution(SOLVER, quick_id, {"culprit": "c1", "confidence": 1}).startswith(
        "거부됨"
    )


def test_failed_judgement_starts_a_revision_with_feedback(prep: PrepService) -> None:
    prep.start(seed="revise")
    prep.advance()
    design(prep)
    prep.advance()
    prep.submit_audit(AUDITOR, PASS_AUDIT)
    solve_all(prep, {**GOOD_ANSWERS, SolverKind.ROUND1: (CULPRIT, 95)})  # too easy
    again = prep.advance()
    assert (again.stage, again.agents) == (Stage.DESIGN, (ARCHITECT,))
    packet = json.loads(prep.revision_packet(ARCHITECT))
    assert packet["revision"] == 2
    assert any("너무 쉬움" in f for f in packet["why_it_failed"])
    assert packet["previous_draft"]["truth"]["culprit_id"] == CULPRIT
    assert "2차 수정" in prep.architect_brief(ARCHITECT)


def test_rejected_after_max_revisions(prep: PrepService) -> None:
    prep.start(seed="reject")
    for revision in range(1, MAX_REVISIONS + 1):
        progress = prep.advance()
        assert progress.stage is Stage.DESIGN
        design(prep)
        prep.advance()
        blocker = {
            "verdict": "revise",
            "findings": [{"severity": "blocker", "problem": "정답이 둘"}],
        }
        prep.submit_audit(AUDITOR, blocker)
        solve_all(prep, GOOD_ANSWERS)
        if revision < MAX_REVISIONS:
            assert prep.active() is not None
    final = prep.advance()
    assert final.stage is Stage.REJECTED
    assert prep.active() is None and prep.store.next_unplayed() is None


def test_architect_that_gives_up_uses_up_wakes(prep: PrepService) -> None:
    prep.start(seed="giveup")
    for _ in range(MAX_WAKES_PER_STAGE):
        assert prep.advance().agents == (ARCHITECT,)
    nxt = prep.advance()  # still nothing submitted: revision 2 begins
    assert nxt.stage is Stage.DESIGN
    state = prep.active()
    assert state is not None and state.revision == 2


def test_abandoned_solver_job_is_freed(prep: PrepService) -> None:
    prep.start(seed="abandon")
    prep.advance()
    design(prep)
    prep.advance()
    prep.submit_audit(AUDITOR, PASS_AUDIT)
    prep.advance()
    prep.solver_packet(SOLVER)  # claimed, never submitted
    progress = prep.advance()
    assert progress.agents.count(SOLVER) == 3  # the abandoned job is offered again


def test_schema_errors_are_reported_and_not_stored(prep: PrepService) -> None:
    prep.start(seed="schema")
    prep.advance()
    result = prep.submit_section(ARCHITECT, "concept", {"title": "제목"})
    assert result.startswith("거부됨")
    assert "logline" in result
    assert prep.submit_section(ARCHITECT, "plot", {}).startswith("거부됨")
    assert "아직 제출하지 않은 섹션" in prep.check(ARCHITECT)


def test_static_errors_block_the_audit(prep: PrepService) -> None:
    prep.start(seed="static")
    prep.advance()
    data = scenario_data()
    data["facts"] = [f for f in data["facts"] if f["id"] != "f3"]  # c2 never excluded
    report = design(prep, data)
    assert "not_excluded" in report
    assert prep.advance().stage is Stage.DESIGN  # not promoted to audit


def test_tools_refuse_other_agents_and_wrong_stages(prep: PrepService) -> None:
    prep.start(seed="perm")
    prep.advance()
    with pytest.raises(PermissionError):
        prep.architect_brief(AUDITOR)
    with pytest.raises(PermissionError):
        prep.submit_section(SOLVER, "concept", {})
    with pytest.raises(PermissionError):
        prep.review_packet(AUDITOR, "blind")  # audit stage not reached yet
    assert "풀이할 사건이 없습니다" in prep.solver_packet(SOLVER)


def test_blind_packets_carry_no_design_secrets() -> None:
    data = scenario_data()
    data["victim"]["true_background"] = "TOKEN_VICTIM_SECRET"
    data["truth"]["motive"] = "TOKEN_MOTIVE"
    data["characters"][2]["sheet"]["secret"]["what"] = "TOKEN_SHEET_SECRET"
    data["facts"][0]["statement"] = "TOKEN_FACT"
    data["narration"]["truth_reveal"] = "TOKEN_REVEAL " * 100
    data["narration"]["ending_caught"] = "TOKEN_ENDING"
    scenario = Scenario.model_validate(data)
    for cards in (None, solver_cards(scenario, SolverKind.PARTIAL, "x")):
        packet = blind_packet(scenario, cards)
        for token in (
            "TOKEN_",
            "is_red_herring",
            "points_to",
            "supports",
            "culprit",
            "red_herring_resolution",
            "unlock_condition",
        ):
            assert token not in packet, token


def test_partial_and_round1_card_sets() -> None:
    scenario = Scenario.model_validate(scenario_data())
    partial = solver_cards(scenario, SolverKind.PARTIAL, "seed")
    assert len(partial) == len(scenario.clues) - 8
    assert partial == solver_cards(scenario, SolverKind.PARTIAL, "seed")  # deterministic
    round1 = solver_cards(scenario, SolverKind.ROUND1, "seed")
    assert {k.round for k in round1} == {0, 1}
