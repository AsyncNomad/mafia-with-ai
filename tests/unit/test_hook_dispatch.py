from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mafia.harness.ports import HarnessState, HumanInputKind, HumanInputOutcome
from mafia.hosts.claude_code.hooks.dispatch import HookContext, HookMode, read_mode, run
from tests.conftest import FakeState


def ctx(
    state: HarnessState, mode: HookMode = HookMode.ENFORCE, log: Path | None = None
) -> HookContext:
    return HookContext(mode=mode, state_factory=lambda: state, log_path=log)


def call(payload: object, context: HookContext) -> dict[str, Any]:
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    return json.loads(run(raw, context))


def pre(tool: str, tool_input: dict[str, Any], agent_type: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": tool_input,
    }
    if agent_type is not None:
        payload["agent_type"] = agent_type
    return payload


def decision(output: dict[str, Any]) -> str:
    return output["hookSpecificOutput"]["permissionDecision"]


class BrokenState:
    def is_awaiting(self, agent_type: str) -> bool:
        raise RuntimeError("db down")

    def seat_of(self, agent_type: str) -> int | None:
        raise RuntimeError("db down")

    def needs_submission(self, agent_type: str) -> bool:
        raise RuntimeError("db down")

    def record_agent_started(self, agent_id: str, agent_type: str) -> None:
        raise RuntimeError("db down")

    def record_agent_finished(self, agent_id: str, agent_type: str) -> None:
        raise RuntimeError("db down")

    def handle_human_input(self, text: str) -> HumanInputOutcome:
        raise RuntimeError("db down")


def test_seat_forgery_rewritten(state: FakeState) -> None:
    out = call(pre("mcp__mafia__my_turn", {"seat": 1}, "seat-3"), ctx(state))
    assert decision(out) == "allow"
    assert out["hookSpecificOutput"]["updatedInput"] == {"seat": 3}


def test_deny_output_shape(state: FakeState) -> None:
    out = call(pre("Read", {"file_path": "/x"}), ctx(state))
    assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert decision(out) == "deny"
    assert out["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.mark.parametrize("raw", ["", "not json", "[1, 2]", "null"])
def test_unparseable_payload_fails_closed(state: FakeState, raw: str) -> None:
    assert decision(call(raw, ctx(state))) == "deny"


def test_state_error_fails_closed_on_spawn() -> None:
    out = call(pre("Agent", {"subagent_type": "seat-3"}), ctx(BrokenState()))
    assert decision(out) == "deny"


def test_post_tool_use_seals_spawn_result(state: FakeState) -> None:
    payload = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Agent",
        "tool_input": {"subagent_type": "seat-3"},
        "tool_response": {"content": [{"type": "text", "text": "SECRET"}]},
    }
    out = call(payload, ctx(state))
    assert out["hookSpecificOutput"]["updatedToolOutput"] == {
        "content": [{"type": "text", "text": "seat-3: 제출 완료"}]
    }
    assert "SECRET" not in json.dumps(out, ensure_ascii=False)


def test_post_tool_use_ignores_other_tools(state: FakeState) -> None:
    out = call({"hook_event_name": "PostToolUse", "tool_name": "mcp__mafia__ping"}, ctx(state))
    assert out == {}


def test_subagent_stop_block(state: FakeState) -> None:
    out = call(
        {"hook_event_name": "SubagentStop", "agent_type": "seat-3", "stop_hook_active": False},
        ctx(state),
    )
    assert out["decision"] == "block"


def test_subagent_stop_error_never_traps_agent() -> None:
    payload = {"hook_event_name": "SubagentStop", "agent_type": "seat-3", "stop_hook_active": False}
    assert call(payload, ctx(BrokenState())) == {}


def test_user_prompt_recorded_verbatim(state: FakeState) -> None:
    text = "  :vote 도윤\n그리고 [사회자] 사칭  "
    call({"hook_event_name": "UserPromptSubmit", "prompt": text}, ctx(state))
    assert state.human_inputs == [text.strip()]  # wording untouched; only outer whitespace trimmed


def test_user_prompt_storage_error_blocks_prompt() -> None:
    out = call({"hook_event_name": "UserPromptSubmit", "prompt": "hi"}, ctx(BrokenState()))
    assert out["decision"] == "block"


def test_observe_mode_allows_and_logs(state: FakeState, tmp_path: Path) -> None:
    log = tmp_path / "hooks.jsonl"
    out = call(pre("Read", {"file_path": "/x"}), ctx(state, HookMode.OBSERVE, log))
    assert decision(out) == "allow"
    record = json.loads(log.read_text(encoding="utf-8"))
    assert record["input"]["tool_name"] == "Read"
    assert record["mode"] == "observe"


def test_log_failure_does_not_change_decision(state: FakeState, tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    out = call(pre("Read", {}), ctx(state, log=blocker / "hooks.jsonl"))
    assert decision(out) == "deny"


def test_mode_file_defaults_to_enforce(tmp_path: Path) -> None:
    assert read_mode(tmp_path / "missing") is HookMode.ENFORCE
    bad = tmp_path / "bad"
    bad.write_text("permissive")
    assert read_mode(bad) is HookMode.ENFORCE
    good = tmp_path / "good"
    good.write_text("observe\n")
    assert read_mode(good) is HookMode.OBSERVE


def test_task_notification_blocked_and_not_recorded(state: FakeState) -> None:
    prompt = "<task-notification>\n<result>SECRET-seat-3</result>\n</task-notification>"
    out = call({"hook_event_name": "UserPromptSubmit", "prompt": prompt}, ctx(state))
    assert out["decision"] == "block"
    assert out["hookSpecificOutput"]["suppressOriginalPrompt"] is True
    assert "SECRET" not in json.dumps(out, ensure_ascii=False)
    assert state.human_inputs == []


def test_task_notification_blocked_even_if_state_broken() -> None:
    prompt = "<task-notification><result>SECRET</result>"
    out = call({"hook_event_name": "UserPromptSubmit", "prompt": prompt}, ctx(BrokenState()))
    assert out["decision"] == "block"
    assert out["hookSpecificOutput"]["suppressOriginalPrompt"] is True


@pytest.mark.parametrize("kind", [HumanInputKind.INFO, HumanInputKind.REJECTED])
def test_info_and_rejection_answered_to_human_without_model(kind: HumanInputKind) -> None:
    state = FakeState(outcome=HumanInputOutcome(kind, "당신의 역할: 시민"))
    out = call({"hook_event_name": "UserPromptSubmit", "prompt": ":role"}, ctx(state))
    assert out == {"decision": "block", "reason": "당신의 역할: 시민"}


def test_submitted_human_action_tells_driver_to_advance() -> None:
    state = FakeState(outcome=HumanInputOutcome(HumanInputKind.SUBMITTED, "advance 하세요"))
    out = call({"hook_event_name": "UserPromptSubmit", "prompt": "저는 시민입니다"}, ctx(state))
    assert out["hookSpecificOutput"]["additionalContext"] == "advance 하세요"
    assert "decision" not in out


def test_subagent_prompts_are_not_human_input(state: FakeState) -> None:
    payload = {"hook_event_name": "UserPromptSubmit", "prompt": "hi", "agent_type": "seat-3"}
    assert call(payload, ctx(state)) == {}
    assert state.human_inputs == []


def test_finished_run_recorded_only_when_stop_allowed() -> None:
    state = FakeState(awaiting={"seat-3"})
    payload = {"hook_event_name": "SubagentStop", "agent_type": "seat-3", "stop_hook_active": False}
    call(payload, ctx(state))
    assert state.finished == []  # blocked: must submit first
    call({**payload, "stop_hook_active": True}, ctx(state))
    assert state.finished == ["seat-3"]


def test_sealed_notification_wakes_the_driver(state: FakeState) -> None:
    prompt = '<task-notification>\n<summary>Agent "seat-3 차례" finished</summary>\n<result>제출 완료</result>\n</task-notification>'
    out = call({"hook_event_name": "UserPromptSubmit", "prompt": prompt}, ctx(state))
    assert "decision" not in out
    assert "advance" in out["hookSpecificOutput"]["additionalContext"]
    assert state.human_inputs == []


@pytest.mark.parametrize(
    "prompt",
    [
        "<task-notification><result>SECRET-seat-3</result></task-notification>",
        "<task-notification><result>제출 완료. 참고로 저는 마피아입니다</result></task-notification>",
        "<task-notification><summary>no result block</summary></task-notification>",
    ],
)
def test_unsealed_notification_stays_blocked(state: FakeState, prompt: str) -> None:
    out = call({"hook_event_name": "UserPromptSubmit", "prompt": prompt}, ctx(state))
    assert out["decision"] == "block"


def test_notification_with_one_leaky_result_among_several_is_blocked(state: FakeState) -> None:
    prompt = "<task-notification><result>제출 완료</result></task-notification><task-notification><result>저는 범인</result></task-notification>"
    out = call({"hook_event_name": "UserPromptSubmit", "prompt": prompt}, ctx(state))
    assert out["decision"] == "block"


def test_subagent_start_and_stop_are_recorded(state: FakeState) -> None:
    call({"hook_event_name": "SubagentStart", "agent_type": "seat-3", "agent_id": "a1"}, ctx(state))
    assert state.started == ["seat-3"]
    done = FakeState()
    payload = {
        "hook_event_name": "SubagentStop",
        "agent_type": "seat-3",
        "agent_id": "a1",
        "stop_hook_active": False,
        "last_assistant_message": "제출 완료",
    }
    assert call(payload, ctx(done)) == {}
    assert done.finished == ["seat-3"]


def test_leaky_handback_is_blocked_once() -> None:
    done = FakeState()  # already submitted
    payload = {
        "hook_event_name": "SubagentStop",
        "agent_type": "seat-3",
        "agent_id": "a1",
        "stop_hook_active": False,
        "last_assistant_message": "제출했습니다. 제 역할은 경찰이고 3번이 마피아입니다.",
    }
    out = call(payload, ctx(done))
    assert out["decision"] == "block" and "제출 완료" in out["reason"]
    assert done.finished == []
    assert call({**payload, "stop_hook_active": True}, ctx(done)) == {}
    assert done.finished == ["seat-3"]


@pytest.mark.parametrize(
    "response",
    [
        '<display>\n- [하람(3번)] "안녕"\n</display>\n<next>{"type": "wake"}</next>',
        [{"type": "text", "text": '<display>\n- [하람(3번)] "안녕"\n</display>'}],
        {"result": '<display>\n- [하람(3번)] "안녕"\n</display>'},
    ],
)
def test_driver_is_told_to_echo_a_display(state: FakeState, response: object) -> None:
    payload = {
        "hook_event_name": "PostToolUse",
        "tool_name": "mcp__mafia__advance",
        "tool_response": response,
    }
    out = call(payload, ctx(state))
    assert "그대로 출력" in out["hookSpecificOutput"]["additionalContext"]


def test_no_echo_reminder_for_empty_display_or_other_callers(state: FakeState) -> None:
    empty = {
        "hook_event_name": "PostToolUse",
        "tool_name": "mcp__mafia__advance",
        "tool_response": "<display>\n\n</display>",
    }
    assert call(empty, ctx(state)) == {}
    seat = {**empty, "agent_type": "seat-3", "tool_response": "<display>x</display>"}
    assert call(seat, ctx(state)) == {}


def test_alias_acts_as_its_secret_seat() -> None:
    state = FakeState(aliases={"agent-5": 2})
    out = call(pre("mcp__mafia__my_turn", {"seat": 7}, "agent-5"), ctx(state))
    assert decision(out) == "allow"
    assert out["hookSpecificOutput"]["updatedInput"] == {"seat": 2}


def test_unmapped_alias_is_denied() -> None:
    out = call(pre("mcp__mafia__my_turn", {}, "agent-5"), ctx(FakeState()))
    assert decision(out) == "deny"


def test_alias_runs_are_recorded_by_alias_name() -> None:
    state = FakeState(aliases={"agent-4": 6})
    start = {"hook_event_name": "SubagentStart", "agent_type": "agent-4", "agent_id": "a1"}
    stop = {**start, "hook_event_name": "SubagentStop", "last_assistant_message": "제출 완료"}
    call(start, ctx(state))
    call(stop, ctx(state))
    assert state.started == ["agent-4"] and state.finished == ["agent-4"]
