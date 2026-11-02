"""The driver must write each display into the chat before acting again (observed M2)."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from mafia.hosts.claude_code.hooks import echo
from mafia.hosts.claude_code.hooks.dispatch import HookContext, HookMode, run
from mafia.hosts.claude_code.hooks.echo import missing_lines, unechoed
from tests.conftest import FakeState

DISPLAY = '- [🟦준호(5번)] "허허, 아직 첫날이라 나도 잘 모르겠구먼."\n\n**🙋 당신의 차례입니다.** 발언하세요.'


def tool_result(display: str) -> dict[str, Any]:
    text = f'<display>\n{display}\n</display>\n<next>{{"type": "human"}}</next>'
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "content": [{"type": "text", "text": text}]}],
        },
    }


def assistant(text: str) -> dict[str, Any]:
    return {
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }


def tool_use(name: str) -> dict[str, Any]:
    return {
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "tool_use", "name": name}]},
    }


def transcript(tmp_path: Path, *entries: dict[str, Any]) -> str:
    path = tmp_path / "t.jsonl"
    path.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n", encoding="utf-8"
    )
    return str(path)


def test_summary_instead_of_echo_is_caught(tmp_path: Path) -> None:
    path = transcript(
        tmp_path, tool_result(DISPLAY), tool_use("Agent"), assistant("준호가 발언을 마쳤어요.")
    )
    assert len(unechoed(path)) == 2


def test_verbatim_echo_passes_even_with_formatting_changes(tmp_path: Path) -> None:
    reformatted = "[🟦준호(5번)] “허허, 아직 첫날이라 나도 잘 모르겠구먼.”\n\n🙋 **당신의 차례입니다.**  발언하세요."
    path = transcript(tmp_path, tool_result(DISPLAY), assistant(reformatted))
    assert unechoed(path) == []


def test_echo_split_over_several_messages_passes(tmp_path: Path) -> None:
    first, second = DISPLAY.split("\n\n")
    path = transcript(
        tmp_path, tool_result(DISPLAY), assistant(first), tool_use("Agent"), assistant(second)
    )
    assert unechoed(path) == []


def test_only_the_latest_display_matters(tmp_path: Path) -> None:
    path = transcript(tmp_path, tool_result("- 옛날 줄"), tool_result(DISPLAY), assistant(DISPLAY))
    assert unechoed(path) == []


def test_empty_display_and_missing_transcript_pass(tmp_path: Path) -> None:
    assert unechoed(transcript(tmp_path, tool_result(""))) == []
    assert unechoed(str(tmp_path / "missing.jsonl")) == []
    assert missing_lines("\n\n", "") == []


def _ctx() -> HookContext:
    state = FakeState(awaiting={"seat-3"})
    return HookContext(mode=HookMode.ENFORCE, state_factory=lambda: state, log_path=None)


def test_next_spawn_is_refused_until_the_display_is_shown(tmp_path: Path) -> None:
    path = transcript(tmp_path, tool_result(DISPLAY), assistant("준호가 말했어요."))
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Agent",
        "tool_input": {"subagent_type": "seat-3"},
        "transcript_path": path,
    }
    out = json.loads(run(json.dumps(payload), _ctx()))["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"
    assert "허허" in out["permissionDecisionReason"]

    shown = transcript(tmp_path, tool_result(DISPLAY), assistant(DISPLAY))
    out = json.loads(run(json.dumps({**payload, "transcript_path": shown}), _ctx()))[
        "hookSpecificOutput"
    ]
    assert out["permissionDecision"] == "allow"


def test_turn_cannot_end_before_the_display_is_shown(tmp_path: Path) -> None:
    path = transcript(tmp_path, tool_result(DISPLAY), assistant("Jane 차례예요."))
    payload = {"hook_event_name": "Stop", "transcript_path": path, "stop_hook_active": False}
    assert json.loads(run(json.dumps(payload), _ctx()))["decision"] == "block"
    assert json.loads(run(json.dumps({**payload, "stop_hook_active": True}), _ctx())) == {}


def test_seats_are_not_echo_gated(tmp_path: Path) -> None:
    path = transcript(tmp_path, tool_result(DISPLAY))
    payload: dict[str, Any] = {
        "hook_event_name": "PreToolUse",
        "tool_name": "mcp__mafia__my_turn",
        "tool_input": {},
        "agent_type": "seat-3",
        "transcript_path": path,
    }
    assert (
        json.loads(run(json.dumps(payload), _ctx()))["hookSpecificOutput"]["permissionDecision"]
        == "allow"
    )


def test_stop_counts_the_message_the_host_hands_over(tmp_path: Path) -> None:
    # Regression: the Stop hook ran before the reply reached the transcript and blocked a
    # driver that had echoed correctly, which then gave up and ended its turn.
    path = transcript(tmp_path, tool_result(DISPLAY))
    payload = {
        "hook_event_name": "Stop",
        "transcript_path": path,
        "stop_hook_active": False,
        "last_assistant_message": DISPLAY,
    }
    assert json.loads(run(json.dumps(payload), _ctx())) == {}


def test_tool_call_waits_for_the_transcript_to_catch_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(echo, "SETTLE_SECONDS", 2.0)
    path = transcript(tmp_path, tool_result(DISPLAY))

    def flush_late() -> None:
        time.sleep(0.3)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(assistant(DISPLAY), ensure_ascii=False) + "\n")

    writer = threading.Thread(target=flush_late)
    writer.start()
    try:
        assert unechoed(path, settle=echo.SETTLE_SECONDS) == []
    finally:
        writer.join()
