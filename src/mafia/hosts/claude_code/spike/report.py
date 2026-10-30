# pyright: basic
# M0 throwaway: parses untyped JSON evidence; strict typing would add noise, not safety.
"""Evaluates the M0 harness contracts from evidence left on disk.

Evidence sources: the hook log (what Claude Code sent to hooks and what they answered), the
spike database (what the MCP server actually received), subagent transcripts (what the
subagent was actually told), and optionally the main session's final output.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from mafia import paths
from mafia.hosts.claude_code.hooks.policy import SPAWN_TOOLS, WAKE_PROMPT, sealed_text
from mafia.hosts.claude_code.spike.state import SpikeState

Record = Mapping[str, Any]


class Verdict(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NA = "N/A"


@dataclass(frozen=True, slots=True)
class Result:
    contract: str
    verdict: Verdict
    detail: str


@dataclass(frozen=True, slots=True)
class Evidence:
    hooks: list[Record]
    awaiting: list[tuple[object, ...]]
    deliveries: list[tuple[object, ...]]
    submissions: list[tuple[object, ...]]
    human_inputs: list[tuple[object, ...]]
    main_output: str | None

    def events(self, name: str) -> list[Record]:
        return [h for h in self.hooks if h.get("event") == name]

    def secrets(self) -> list[str]:
        return [str(row[2]) for row in self.awaiting]


def load_evidence(main_output: Path | None) -> Evidence:
    log = paths.logs_dir() / "hooks.jsonl"
    hooks: list[Record] = []
    if log.exists():
        hooks = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]
    state = SpikeState.open_default()
    return Evidence(
        hooks=hooks,
        awaiting=state.rows("awaiting"),
        deliveries=state.rows("deliveries"),
        submissions=state.rows("submissions"),
        human_inputs=state.rows("human_inputs"),
        main_output=main_output.read_text(encoding="utf-8") if main_output else None,
    )


def _decision(record: Record) -> str | None:
    out = record.get("output") or {}
    return (out.get("hookSpecificOutput") or {}).get("permissionDecision")


def _tool_input(record: Record) -> Mapping[str, Any]:
    return (record.get("input") or {}).get("tool_input") or {}


def _pre(ev: Evidence, tool: str | None = None, agent_prefix: str | None = None) -> list[Record]:
    out = ev.events("PreToolUse")
    if tool is not None:
        out = [r for r in out if r.get("tool_name") == tool]
    if agent_prefix is not None:
        out = [r for r in out if str(r.get("agent_type") or "").startswith(agent_prefix)]
    return out


def c1_main_has_no_agent_type(ev: Evidence) -> Result:
    spawns = [r for r in ev.events("PreToolUse") if r.get("tool_name") in SPAWN_TOOLS]
    if not spawns:
        return Result("C1 메인 세션 훅 입력에 agent_type 없음", Verdict.NA, "Agent 호출 기록 없음")
    leaked = [r for r in spawns if "agent_type" in (r.get("input") or {})]
    if leaked:
        return Result(
            "C1 메인 세션 훅 입력에 agent_type 없음",
            Verdict.FAIL,
            f"{len(leaked)}건에 agent_type 존재",
        )
    return Result(
        "C1 메인 세션 훅 입력에 agent_type 없음",
        Verdict.PASS,
        f"Agent 호출 {len(spawns)}건 모두 없음",
    )


def c2_mcp_tool_naming(ev: Evidence) -> Result:
    names = sorted(
        {
            str(r.get("tool_name"))
            for r in ev.events("PreToolUse")
            if "mafia" in str(r.get("tool_name"))
        }
    )
    ok = any(n == "mcp__mafia__my_turn" for n in names)
    return Result(
        "C2 MCP 도구 이름 mcp__mafia__<tool>",
        Verdict.PASS if ok else Verdict.FAIL,
        ", ".join(names) or "없음",
    )


def c3_subagent_identity(ev: Evidence) -> Result:
    calls = _pre(ev, "mcp__mafia__my_turn")
    if not calls:
        return Result("C3 서브에이전트 도구 호출에 agent_type", Verdict.NA, "my_turn 호출 없음")
    types = sorted({str(r.get("agent_type")) for r in calls})
    ok = all(t.startswith("seat-") for t in types)
    return Result(
        "C3 서브에이전트 도구 호출에 agent_type",
        Verdict.PASS if ok else Verdict.FAIL,
        f"agent_type={types}",
    )


def c4_impersonation_overwritten(ev: Evidence) -> Result:
    calls = _pre(ev, "mcp__mafia__my_turn")
    forged = [
        r for r in calls if r.get("agent_type") == "seat-2" and _tool_input(r).get("seat") != 2
    ]
    if not forged:
        return Result(
            "C4 좌석 사칭 시 seat 인자 덮어쓰기", Verdict.NA, "seat-2의 사칭 시도 기록 없음"
        )
    rewritten = [
        r
        for r in forged
        if ((r.get("output") or {}).get("hookSpecificOutput") or {})
        .get("updatedInput", {})
        .get("seat")
        == 2
    ]
    delivered_to_1 = sum(1 for d in ev.deliveries if d[1] == 1)
    seat1_calls = len(_pre(ev, "mcp__mafia__my_turn", "seat-1"))
    ok = len(rewritten) == len(forged) and delivered_to_1 <= seat1_calls
    return Result(
        "C4 좌석 사칭 시 seat 인자 덮어쓰기",
        Verdict.PASS if ok else Verdict.FAIL,
        f"사칭 {len(forged)}건, 덮어씀 {len(rewritten)}건, 서버가 seat=1로 받은 횟수 {delivered_to_1} (seat-1 정상 호출 {seat1_calls})",
    )


def c5_seat_builtins_unavailable(ev: Evidence) -> Result:
    forbidden = {"Read", "Bash", "mcp__mafia__ping", "Agent", "Task"}
    attempts = [r for r in _pre(ev, agent_prefix="seat-") if r.get("tool_name") in forbidden]
    allowed = [r for r in attempts if _decision(r) == "allow"]
    if allowed:
        return Result("C5 좌석의 금지 도구 차단", Verdict.FAIL, f"허용된 시도 {len(allowed)}건")
    detail = (
        "도구 자체가 노출되지 않음(시도 0건)"
        if not attempts
        else f"시도 {len(attempts)}건 모두 훅이 거부"
    )
    return Result("C5 좌석의 금지 도구 차단", Verdict.PASS, detail)


def _first_user_text(transcript: Path) -> str | None:
    try:
        lines = transcript.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = entry.get("message") if isinstance(entry, dict) else None
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts = [
                b.get("text", "")
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            if texts:
                return "\n".join(texts)
    return None


def c6_spawn_prompt_replaced(ev: Evidence) -> Result:
    stops = [
        r for r in ev.events("SubagentStop") if str(r.get("agent_type") or "").startswith("seat-")
    ]
    prompts: list[str] = []
    for r in stops:
        path = (r.get("input") or {}).get("agent_transcript_path")
        if path:
            text = _first_user_text(Path(path))
            if text is not None:
                prompts.append(text)
    if not prompts:
        return Result(
            "C6 서브에이전트 프롬프트 고정 문구로 교체",
            Verdict.NA,
            "전사본에서 첫 메시지를 찾지 못함",
        )
    ok = all(WAKE_PROMPT in p and "비밀" not in p for p in prompts)
    sample = prompts[0][:80].replace("\n", " ")
    return Result(
        "C6 서브에이전트 프롬프트 고정 문구로 교체",
        Verdict.PASS if ok else Verdict.FAIL,
        f"첫 메시지 예: {sample!r}",
    )


def c7_result_sealed(ev: Evidence) -> Result:
    secrets = ev.secrets()
    posts = [r for r in ev.events("PostToolUse") if r.get("tool_name") in SPAWN_TOOLS]
    if not posts:
        return Result("C7 서브에이전트 결과 봉인", Verdict.NA, "Agent PostToolUse 기록 없음")
    leaks: list[str] = []
    if ev.main_output is not None:
        leaks += [s for s in secrets if s in ev.main_output]
    for transcript in _main_transcripts(ev):
        text = transcript.read_text(encoding="utf-8") if transcript.exists() else ""
        sealed_seen = any(sealed_text(t) in text for t in ("seat-1", "seat-2"))
        if not sealed_seen:
            leaks.append(f"{transcript.name}: 봉인 문구가 메인 전사본에 없음")
    if ev.main_output is None:
        return Result(
            "C7 서브에이전트 결과 봉인",
            Verdict.NA if not leaks else Verdict.FAIL,
            "메인 출력 미제공. " + "; ".join(leaks),
        )
    return Result(
        "C7 서브에이전트 결과 봉인",
        Verdict.FAIL if leaks else Verdict.PASS,
        "; ".join(leaks) if leaks else "메인 출력에 비밀 코드 없음",
    )


def _main_transcripts(ev: Evidence) -> list[Path]:
    paths_seen = {
        str((r.get("input") or {}).get("transcript_path"))
        for r in ev.events("PreToolUse")
        if r.get("tool_name") in SPAWN_TOOLS and (r.get("input") or {}).get("transcript_path")
    }
    return [Path(p) for p in sorted(paths_seen)]


def c8_unawaited_spawn_denied(ev: Evidence) -> Result:
    spawns = [r for r in ev.events("PreToolUse") if r.get("tool_name") in SPAWN_TOOLS]
    others = [
        r for r in spawns if not str(_tool_input(r).get("subagent_type", "")).startswith("seat-")
    ]
    if not others:
        return Result("C8 대기 중이 아닌 에이전트 생성 거부", Verdict.NA, "시도 없음")
    bad = [r for r in others if _decision(r) != "deny"]
    started = [
        r
        for r in ev.events("SubagentStart")
        if not str(r.get("agent_type") or "").startswith("seat-")
    ]
    ok = not bad and not started
    return Result(
        "C8 대기 중이 아닌 에이전트 생성 거부",
        Verdict.PASS if ok else Verdict.FAIL,
        f"시도 {len(others)}건, 거부 안 됨 {len(bad)}건, 실제 시작 {len(started)}건",
    )


def c9_main_builtins_denied(ev: Evidence) -> Result:
    attempts = [
        r
        for r in ev.events("PreToolUse")
        if not r.get("agent_type") and r.get("tool_name") in {"Read", "Bash"}
    ]
    if not attempts:
        return Result(
            "C9 메인의 Read/Bash 차단",
            Verdict.NA,
            "훅에 도달한 시도 없음(권한 deny가 먼저 막았을 수 있음)",
        )
    bad = [r for r in attempts if _decision(r) != "deny"]
    return Result(
        "C9 메인의 Read/Bash 차단",
        Verdict.FAIL if bad else Verdict.PASS,
        f"시도 {len(attempts)}건, 허용 {len(bad)}건",
    )


def c10_subagent_stop_blocks_until_submitted(ev: Evidence) -> Result:
    stops = [r for r in ev.events("SubagentStop") if r.get("agent_type") == "seat-2"]
    blocked = [r for r in stops if (r.get("output") or {}).get("decision") == "block"]
    submitted = any(row[1] == 2 for row in ev.submissions)
    if not stops:
        return Result("C10 미제출 종료 차단 후 제출", Verdict.NA, "seat-2 종료 기록 없음")
    if not blocked:
        detail = "차단 없이 종료" + (
            " (이미 제출한 뒤라 정상)" if submitted else " — 미제출인데 막지 못함"
        )
        return Result(
            "C10 미제출 종료 차단 후 제출", Verdict.PASS if submitted else Verdict.FAIL, detail
        )
    return Result(
        "C10 미제출 종료 차단 후 제출",
        Verdict.PASS if submitted else Verdict.FAIL,
        f"차단 {len(blocked)}회, 이후 제출 {'있음' if submitted else '없음'}",
    )


def c11_human_input_captured(ev: Evidence) -> Result:
    prompts = ev.events("UserPromptSubmit")
    if not prompts:
        return Result("C11 사람 입력 원문 기록", Verdict.NA, "UserPromptSubmit 기록 없음")
    raw = [str((r.get("input") or {}).get("prompt", "")) for r in prompts]
    stored = [str(row[1]) for row in ev.human_inputs]
    ok = all(p in stored for p in raw)
    return Result(
        "C11 사람 입력 원문 기록",
        Verdict.PASS if ok else Verdict.FAIL,
        f"훅 {len(raw)}건, 저장 {len(stored)}건",
    )


def c12_hook_latency(ev: Evidence) -> Result:
    times = [float(h.get("elapsed_ms") or 0) for h in ev.hooks]
    if not times:
        return Result("C12 훅 처리 시간", Verdict.NA, "기록 없음")
    worst = max(times)
    return Result(
        "C12 훅 처리 시간",
        Verdict.PASS if worst < 500 else Verdict.FAIL,
        f"최대 {worst:.0f}ms, {len(times)}건",
    )


def c13_hooks_never_errored(ev: Evidence) -> Result:
    errors = [h for h in ev.hooks if h.get("error")]
    return Result(
        "C13 훅 내부 오류 없음",
        Verdict.FAIL if errors else Verdict.PASS,
        "; ".join(str(h.get("error")) for h in errors[:3]) or f"{len(ev.hooks)}건 정상",
    )


CONTRACTS: tuple[Callable[[Evidence], Result], ...] = (
    c1_main_has_no_agent_type,
    c2_mcp_tool_naming,
    c3_subagent_identity,
    c4_impersonation_overwritten,
    c5_seat_builtins_unavailable,
    c6_spawn_prompt_replaced,
    c7_result_sealed,
    c8_unawaited_spawn_denied,
    c9_main_builtins_denied,
    c10_subagent_stop_blocks_until_submitted,
    c11_human_input_captured,
    c12_hook_latency,
    c13_hooks_never_errored,
)


def evaluate(ev: Evidence) -> list[Result]:
    return [contract(ev) for contract in CONTRACTS]


def format_results(results: Iterable[Result]) -> str:
    lines = ["| 계약 | 판정 | 근거 |", "|---|---|---|"]
    lines += [f"| {r.contract} | {r.verdict} | {r.detail} |" for r in results]
    return "\n".join(lines)


def main(main_output: str | None) -> int:
    ev = load_evidence(Path(main_output) if main_output else None)
    results = evaluate(ev)
    print(format_results(results))
    return 1 if any(r.verdict is Verdict.FAIL for r in results) else 0
