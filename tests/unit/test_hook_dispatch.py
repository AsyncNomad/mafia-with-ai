from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mafia.harness.ports import HarnessState
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

    def record_human_input(self, text: str) -> None:
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
    assert state.human_inputs == [text]


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
