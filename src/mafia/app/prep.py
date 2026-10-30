"""Murder-mystery scenario preparation pipeline (docs/modes/murder-mystery.md §3).

design (architect) → static validation → audit (auditor) → blind solves (4 solvers) → judge
→ verified into the library, or back to design with a revision packet (at most 3 revisions).

Stage progress is decided when the driver calls `advance`. Agents run in the foreground, so at
that moment none is running: unfinished work means the agent gave up, which costs one of the
stage's limited wakes. No hook bookkeeping is needed.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from mafia import paths
from mafia.engine.runtime import Clock
from mafia.modes.mystery.brief import (
    SECTION_TYPES,
    SECTIONS,
    Brief,
    load_pools,
    make_brief,
    render_brief,
)
from mafia.modes.mystery.packets import (
    SolverKind,
    blind_packet,
    full_packet,
    solver_cards,
    solver_packet,
)
from mafia.modes.mystery.scenario import Scenario
from mafia.modes.mystery.validate import Severity, validate
from mafia.store.scenarios import ScenarioRecord, ScenarioStore

MAX_REVISIONS = 3
MAX_WAKES_PER_STAGE = 3
ARCHITECT = "mystery-architect"
AUDITOR = "mystery-auditor"
SOLVER = "mystery-solver"
QUICK_SOLVER = "mystery-solver-quick"
FULL_SOLVER_MIN_CONFIDENCE = 60
ROUND1_TOO_EASY_CONFIDENCE = 80
_ERROR_PREVIEW = 40


class Stage(StrEnum):
    DESIGN = "design"
    AUDIT = "audit"
    SOLVE = "solve"
    VERIFIED = "verified"
    REJECTED = "rejected"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Finding(_Model):
    severity: Literal["blocker", "major", "minor"]
    path: str = ""
    problem: str
    play_impact: str = ""
    fix: str = ""


class Audit(_Model):
    verdict: Literal["pass", "revise"]
    findings: list[Finding] = Field(default_factory=list[Finding])
    blind_attempt: str = ""
    summary: str = ""

    def counts(self) -> tuple[int, int]:
        blockers = sum(1 for f in self.findings if f.severity == "blocker")
        majors = sum(1 for f in self.findings if f.severity == "major")
        return blockers, majors


class Solution(_Model):
    culprit: str  # character id, or "undetermined"
    confidence: int = Field(ge=0, le=100)
    reasoning: str | list[str] = ""
    suspects: dict[str, Any] | list[Any] = Field(default_factory=dict[str, Any])
    confusions: str | list[str] = ""


class SolverJob(_Model):
    job_id: str
    kind: SolverKind
    agent: str
    claimed: bool = False
    solution: Solution | None = None


class RevisionRecord(_Model):
    revision: int
    failures: list[str]
    static_issues: list[str] = Field(default_factory=list[str])
    audit: Audit | None = None
    solvers: list[SolverJob] = Field(default_factory=list[SolverJob])


class StartGame(_Model):
    human_name: str = "나"
    watch: bool = False


class PrepState(_Model):
    job_id: str
    scenario_id: str
    brief: Brief
    stage: Stage = Stage.DESIGN
    revision: int = 1
    wakes: int = 0
    draft: dict[str, Any] = Field(default_factory=dict[str, Any])
    checked: bool = False
    last_issues: list[str] = Field(default_factory=list[str])
    audit: Audit | None = None
    solvers: list[SolverJob] = Field(default_factory=list[SolverJob])
    history: list[RevisionRecord] = Field(default_factory=list[RevisionRecord])
    start_game: StartGame | None = None
    reason: str = ""

    def scenario(self) -> Scenario:
        return Scenario.model_validate(self.draft)


@dataclass(frozen=True, slots=True)
class PrepProgress:
    display: str
    agents: tuple[str, ...]
    stage: Stage
    scenario_id: str
    start_game: StartGame | None


def _short(exc: ValidationError) -> list[str]:
    out: list[str] = []
    for err in exc.errors()[:_ERROR_PREVIEW]:
        loc = ".".join(str(x) for x in err["loc"])
        out.append(f"{loc}: {err['msg']}")
    if len(exc.errors()) > _ERROR_PREVIEW:
        out.append(f"… 외 {len(exc.errors()) - _ERROR_PREVIEW}건")
    return out


class PrepService:
    def __init__(self, store: ScenarioStore, clock: Clock) -> None:
        self.store = store
        self.clock = clock

    # --- lifecycle ----------------------------------------------------------------------
    def start(
        self, user_request: str = "", start_game: StartGame | None = None, seed: str | None = None
    ) -> PrepState:
        seed = seed or secrets.token_hex(16)
        pools = load_pools(paths.config_dir() / "mystery" / "brief_pools.toml")
        state = PrepState(
            job_id=f"p-{secrets.token_hex(6)}",
            scenario_id=f"s-{secrets.token_hex(6)}",
            brief=make_brief(seed, pools, user_request),
            start_game=start_game,
        )
        self.store.start_job(state.job_id, state.model_dump_json(), self.clock.now())
        return state

    def active(self) -> PrepState | None:
        found = self.store.active_job()
        return None if found is None else PrepState.model_validate_json(found[1])

    def _update[T](self, change: Callable[[PrepState], T]) -> T:
        found = self.store.active_job()
        if found is None:
            raise LookupError("준비 중인 시나리오가 없습니다.")
        job_id = found[0]

        def apply(raw: str) -> tuple[str, T]:
            state = PrepState.model_validate_json(raw)
            value = change(state)
            return state.model_dump_json(), value

        return self.store.update_job(job_id, apply, self.clock.now())

    # --- driver -------------------------------------------------------------------------
    def advance(self) -> PrepProgress:
        progress = self._update(self._step)
        if progress.stage in (Stage.VERIFIED, Stage.REJECTED):
            found = self.store.active_job()
            if found is not None:
                self.store.finish_job(found[0])
        return progress

    def _step(self, s: PrepState) -> PrepProgress:
        for _ in range(16):
            match s.stage:
                case Stage.DESIGN:
                    if s.checked:
                        s.stage, s.wakes = Stage.AUDIT, 0
                        continue
                    if s.wakes >= MAX_WAKES_PER_STAGE:
                        self._fail(s, ["설계자가 검증을 통과하는 시나리오를 완성하지 못했습니다"])
                        continue
                    s.wakes += 1
                    return self._progress(
                        s,
                        f"🖋 시나리오를 설계하고 있습니다 ({s.revision}/{MAX_REVISIONS}차)",
                        (ARCHITECT,),
                    )
                case Stage.AUDIT:
                    if s.audit is not None:
                        s.stage, s.wakes = Stage.SOLVE, 0
                        s.solvers = self._new_solver_jobs(s)
                        continue
                    if s.wakes >= MAX_WAKES_PER_STAGE:
                        self._fail(s, ["감수 보고서를 받지 못했습니다"])
                        continue
                    s.wakes += 1
                    return self._progress(
                        s, "🔎 감수자가 시나리오의 허점을 찾고 있습니다", (AUDITOR,)
                    )
                case Stage.SOLVE:
                    for job in s.solvers:
                        if job.claimed and job.solution is None:
                            job.claimed = False  # the solver gave up; free the job
                    pending = [j for j in s.solvers if j.solution is None]
                    if not pending:
                        self._judge(s)
                        continue
                    if s.wakes >= MAX_WAKES_PER_STAGE:
                        self._fail(s, ["블라인드 풀이 결과를 모두 받지 못했습니다"])
                        continue
                    s.wakes += 1
                    return self._progress(
                        s,
                        "🕵 진상을 모르는 탐정들이 단서만으로 사건을 풀어 보고 있습니다",
                        tuple(j.agent for j in pending),
                    )
                case Stage.VERIFIED:
                    concept = s.scenario().concept
                    return self._progress(
                        s, f"✅ 시나리오 완성: **{concept.title}** — {concept.logline}", ()
                    )
                case Stage.REJECTED:
                    return self._progress(
                        s, f"❌ 시나리오를 완성하지 못해 폐기했습니다. ({s.reason})", ()
                    )
        raise RuntimeError("preparation did not settle")

    @staticmethod
    def _progress(s: PrepState, display: str, agents: tuple[str, ...]) -> PrepProgress:
        return PrepProgress(display, agents, s.stage, s.scenario_id, s.start_game)

    def _new_solver_jobs(self, s: PrepState) -> list[SolverJob]:
        plan = [
            (SolverKind.FULL, SOLVER),
            (SolverKind.FULL, SOLVER),
            (SolverKind.PARTIAL, SOLVER),
            (SolverKind.ROUND1, QUICK_SOLVER),
        ]
        return [
            SolverJob(job_id=f"{s.scenario_id}-r{s.revision}-j{i + 1}", kind=kind, agent=agent)
            for i, (kind, agent) in enumerate(plan)
        ]

    def _judge(self, s: PrepState) -> None:
        culprit = s.scenario().truth.culprit_id
        failures: list[str] = []
        assert s.audit is not None
        blockers, majors = s.audit.counts()
        if blockers or majors > 2:
            failures.append(f"감수 불합격 (blocker {blockers}, major {majors})")
        for job in s.solvers:
            sol = job.solution
            assert sol is not None
            correct = sol.culprit == culprit
            match job.kind:
                case SolverKind.FULL:
                    if not correct or sol.confidence < FULL_SOLVER_MIN_CONFIDENCE:
                        failures.append(
                            "모든 카드를 본 탐정이 범인을 확신하지 못했습니다 (공정성·유일성 부족)"
                        )
                case SolverKind.PARTIAL:
                    if not correct:
                        failures.append(
                            "카드 일부가 빠지자 범인을 특정하지 못했습니다 (견고성 부족)"
                        )
                case SolverKind.ROUND1:
                    if correct and sol.confidence >= ROUND1_TOO_EASY_CONFIDENCE:
                        failures.append("1라운드 카드만으로 범인이 확정됩니다 (너무 쉬움)")
        if failures:
            self._fail(s, failures)
            return
        s.stage = Stage.VERIFIED
        scenario = s.scenario()
        self.store.save(
            ScenarioRecord(
                scenario_id=s.scenario_id,
                status="verified",
                created_at=self.clock.now(),
                title=scenario.concept.title,
                tone=scenario.concept.tone,
                brief=s.brief.model_dump(mode="json"),
                scenario=scenario.model_dump(mode="json"),
                report=self._report(s, []),
            )
        )

    def _fail(self, s: PrepState, failures: list[str]) -> None:
        s.history.append(
            RevisionRecord(
                revision=s.revision,
                failures=failures,
                static_issues=list(s.last_issues),
                audit=s.audit,
                solvers=list(s.solvers),
            )
        )
        if s.revision >= MAX_REVISIONS:
            s.stage = Stage.REJECTED
            s.reason = f"{MAX_REVISIONS}차 수정까지 검증을 통과하지 못했습니다"
            return
        s.revision += 1
        s.stage, s.wakes = Stage.DESIGN, 0
        s.checked, s.audit, s.solvers = False, None, []

    @staticmethod
    def _report(s: PrepState, failures: list[str]) -> dict[str, Any]:
        return {
            "revisions": s.revision,
            "history": [h.model_dump(mode="json") for h in s.history],
            "final_audit": None if s.audit is None else s.audit.model_dump(mode="json"),
            "final_solvers": [j.model_dump(mode="json") for j in s.solvers],
            "failures": failures,
        }

    def is_awaiting(self, agent_type: str) -> bool:
        s = self.active()
        if s is None:
            return False
        match s.stage:
            case Stage.DESIGN:
                return agent_type == ARCHITECT and not s.checked
            case Stage.AUDIT:
                return agent_type == AUDITOR and s.audit is None
            case Stage.SOLVE:
                return any(j.agent == agent_type and j.solution is None for j in s.solvers)
            case _:
                return False

    # --- architect tools ----------------------------------------------------------------
    def architect_brief(self, caller: str) -> str:
        s = self._require(caller, ARCHITECT, Stage.DESIGN)
        note = ""
        if s.revision > 1:
            note = (
                f"\n\n> 이번은 {s.revision}차 수정입니다. 먼저 `mystery_revision_packet`으로 이전 초안과 "
                "감수·풀이 결과를 받으세요. 이전 초안은 그대로 남아 있으니 고칠 섹션만 다시 제출해도 됩니다."
            )
        return render_brief(s.brief) + note

    def revision_packet(self, caller: str) -> str:
        s = self._require(caller, ARCHITECT, Stage.DESIGN)
        if not s.history:
            return "첫 설계입니다. 수정 자료가 없습니다."
        last = s.history[-1]
        packet = {
            "revision": s.revision,
            "why_it_failed": last.failures,
            "static_issues": last.static_issues,
            "audit": None if last.audit is None else last.audit.model_dump(mode="json"),
            "blind_solvers": [
                {
                    "kind": j.kind.value,
                    "solution": None if j.solution is None else j.solution.model_dump(mode="json"),
                }
                for j in last.solvers
            ],
            "previous_draft": s.draft,
        }
        return json.dumps(packet, ensure_ascii=False, indent=1)

    def submit_section(self, caller: str, section: str, payload: Any) -> str:
        if section not in SECTION_TYPES:
            return f"거부됨: section은 {', '.join(SECTIONS)} 중 하나여야 합니다."

        def change(s: PrepState) -> str:
            self._check_caller(s, caller, ARCHITECT, Stage.DESIGN)
            try:
                value = TypeAdapter(SECTION_TYPES[section]).validate_python(payload)
            except ValidationError as exc:
                return "거부됨 — 스키마 오류:\n" + "\n".join(_short(exc))
            s.draft[section] = TypeAdapter(SECTION_TYPES[section]).dump_python(value, mode="json")
            s.checked = False
            missing = [x for x in SECTIONS if x not in s.draft]
            nxt = (
                f"남은 섹션: {', '.join(missing)}"
                if missing
                else "모든 섹션이 제출되었습니다. mystery_check로 전체 검증을 돌리세요."
            )
            return f"접수: {section}. {nxt}"

        return self._update(change)

    def check(self, caller: str) -> str:
        def change(s: PrepState) -> str:
            self._check_caller(s, caller, ARCHITECT, Stage.DESIGN)
            missing = [x for x in SECTIONS if x not in s.draft]
            if missing:
                s.last_issues = [f"missing_section: {m}" for m in missing]
                return "아직 제출하지 않은 섹션: " + ", ".join(missing)
            try:
                scenario = s.scenario()
            except ValidationError as exc:
                s.last_issues = _short(exc)
                return "전체 조립 실패 — 스키마 오류:\n" + "\n".join(s.last_issues)
            issues = validate(scenario)
            s.last_issues = [str(i) for i in issues]
            errs = [i for i in issues if i.severity is Severity.ERROR]
            warns = [i for i in issues if i.severity is Severity.WARNING]
            if errs:
                return (
                    f"오류 {len(errs)}건 (모두 고쳐야 합니다):\n"
                    + "\n".join(map(str, errs))
                    + ("\n경고:\n" + "\n".join(map(str, warns)) if warns else "")
                )
            s.checked = True
            tail = (
                ("\n경고 (의도한 것인지 확인하세요):\n" + "\n".join(map(str, warns)))
                if warns
                else ""
            )
            return "정적 검증 통과. 자기 점검표를 확인한 뒤 '제출 완료'로 끝내세요." + tail

        return self._update(change)

    # --- auditor tools ------------------------------------------------------------------
    def review_packet(self, caller: str, stage: str) -> str:
        s = self._require(caller, AUDITOR, Stage.AUDIT)
        scenario = s.scenario()
        if stage == "blind":
            return blind_packet(scenario)
        if stage == "full":
            warnings = [i for i in s.last_issues if i.startswith("[warning]")]
            return full_packet(scenario, warnings)
        return "거부됨: stage는 blind 또는 full입니다."

    def submit_audit(self, caller: str, audit: Any) -> str:
        def change(s: PrepState) -> str:
            self._check_caller(s, caller, AUDITOR, Stage.AUDIT)
            try:
                s.audit = Audit.model_validate(audit)
            except ValidationError as exc:
                return "거부됨 — 형식 오류:\n" + "\n".join(_short(exc))
            return "접수되었습니다. 최종 응답으로 '제출 완료'라고만 쓰고 끝내세요."

        return self._update(change)

    # --- solver tools -------------------------------------------------------------------
    def solver_packet(self, caller: str) -> str:
        def change(s: PrepState) -> str:
            if caller not in (SOLVER, QUICK_SOLVER) or s.stage is not Stage.SOLVE:
                return "지금은 풀이할 사건이 없습니다. '제출 완료'로 끝내세요."
            job = next((j for j in s.solvers if j.agent == caller and not j.claimed), None)
            if job is None:
                return "남은 풀이 작업이 없습니다. '제출 완료'로 끝내세요."
            job.claimed = True
            scenario = s.scenario()
            return solver_packet(scenario, solver_cards(scenario, job.kind, job.job_id), job.job_id)

        return self._update(change)

    def submit_solution(self, caller: str, job_id: str, solution: Any) -> str:
        def change(s: PrepState) -> str:
            job = next((j for j in s.solvers if j.job_id == job_id), None)
            if job is None or job.agent != caller or not job.claimed:
                return "거부됨: 당신이 받은 작업 번호가 아닙니다."
            if job.solution is not None:
                return "이미 제출된 작업입니다. '제출 완료'로 끝내세요."
            try:
                job.solution = Solution.model_validate(solution)
            except ValidationError as exc:
                return "거부됨 — 형식 오류:\n" + "\n".join(_short(exc))
            return "접수되었습니다. 최종 응답으로 '제출 완료'라고만 쓰고 끝내세요."

        return self._update(change)

    # --- guards -------------------------------------------------------------------------
    def _require(self, caller: str, agent: str, stage: Stage) -> PrepState:
        s = self.active()
        if s is None:
            raise PermissionError("준비 중인 시나리오가 없습니다.")
        self._check_caller(s, caller, agent, stage)
        return s

    @staticmethod
    def _check_caller(s: PrepState, caller: str, agent: str, stage: Stage) -> None:
        if caller != agent:
            raise PermissionError(f"이 도구는 {agent} 전용입니다.")
        if s.stage is not stage:
            raise PermissionError("지금 단계에서는 쓸 수 없는 도구입니다. '제출 완료'로 끝내세요.")
