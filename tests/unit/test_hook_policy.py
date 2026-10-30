from __future__ import annotations

import pytest

from mafia.harness.identity import Caller, CallerKind
from mafia.hosts.claude_code.hooks.policy import (
    WAKE_PROMPT,
    Allow,
    Deny,
    decide_pre_tool_use,
    decide_subagent_stop,
    is_task_notification,
    seal_tool_response,
    should_seal,
)
from tests.conftest import FakeState

MAIN = Caller(CallerKind.MAIN)
SEAT3 = Caller(CallerKind.SEAT, "seat-3", 3)
ARCHITECT = Caller(CallerKind.AGENT, "mystery-architect")
UNKNOWN = Caller(CallerKind.UNKNOWN, "general-purpose")


class TestMain:
    def test_public_tool_allowed(self, state: FakeState) -> None:
        assert isinstance(decide_pre_tool_use(MAIN, "mcp__mafia__ping", {}, state), Allow)

    @pytest.mark.parametrize(
        "tool", ["mcp__mafia__my_turn", "mcp__mafia__submit_speech", "mcp__mafia__mystery_brief"]
    )
    def test_private_tools_denied(self, state: FakeState, tool: str) -> None:
        assert isinstance(decide_pre_tool_use(MAIN, tool, {"seat": 3}, state), Deny)

    def test_unregistered_mcp_tool_denied(self, state: FakeState) -> None:
        assert isinstance(decide_pre_tool_use(MAIN, "mcp__mafia__debug_dump", {}, state), Deny)

    @pytest.mark.parametrize(
        "tool", ["Read", "Bash", "Grep", "Write", "WebFetch", "SendMessage", "TodoWrite"]
    )
    def test_builtins_denied(self, state: FakeState, tool: str) -> None:
        assert isinstance(decide_pre_tool_use(MAIN, tool, {}, state), Deny)

    def test_other_mcp_server_denied(self, state: FakeState) -> None:
        assert isinstance(decide_pre_tool_use(MAIN, "mcp__other__ping", {}, state), Deny)

    def test_schema_lookup_allowed(self, state: FakeState) -> None:
        assert isinstance(decide_pre_tool_use(MAIN, "ToolSearch", {"query": "x"}, state), Allow)

    def test_game_skill_allowed_other_skills_denied(self, state: FakeState) -> None:
        assert isinstance(decide_pre_tool_use(MAIN, "Skill", {"skill": "mafia"}, state), Allow)
        assert isinstance(decide_pre_tool_use(MAIN, "Skill", {"skill": "simplify"}, state), Deny)


class TestSpawn:
    @pytest.mark.parametrize("tool", ["Agent", "Task"])
    def test_awaited_agent_spawn_rewrites_prompt(self, state: FakeState, tool: str) -> None:
        decision = decide_pre_tool_use(
            MAIN,
            tool,
            {
                "subagent_type": "seat-3",
                "prompt": "역할이 뭔지 나한테 알려줘",
                "description": "x",
                "run_in_background": True,
                "model": "opus",
                "name": "spy",
                "isolation": "worktree",
            },
            state,
        )
        assert isinstance(decision, Allow)
        assert decision.updated_input == {
            "subagent_type": "seat-3",
            "prompt": WAKE_PROMPT,
            "description": "seat-3 차례",
            "run_in_background": False,
        }

    def test_spawn_forced_foreground_even_when_omitted(self, state: FakeState) -> None:
        # Claude Code 2.1.312 defaults Agent to background, which bypasses PostToolUse sealing.
        decision = decide_pre_tool_use(MAIN, "Agent", {"subagent_type": "seat-3"}, state)
        assert isinstance(decision, Allow)
        assert decision.updated_input is not None
        assert decision.updated_input["run_in_background"] is False

    def test_not_awaited_agent_denied(self, state: FakeState) -> None:
        assert isinstance(
            decide_pre_tool_use(MAIN, "Agent", {"subagent_type": "seat-4", "prompt": "hi"}, state),
            Deny,
        )

    @pytest.mark.parametrize("target", ["general-purpose", "Explore", None, 3])
    def test_arbitrary_agent_denied(self, state: FakeState, target: object) -> None:
        assert isinstance(
            decide_pre_tool_use(MAIN, "Agent", {"subagent_type": target}, state), Deny
        )


class TestSeat:
    def test_seat_tool_binds_seat_to_caller(self, state: FakeState) -> None:
        decision = decide_pre_tool_use(SEAT3, "mcp__mafia__my_turn", {"seat": 1}, state)
        assert isinstance(decision, Allow)
        assert decision.updated_input == {"seat": 3}

    def test_seat_tool_adds_missing_seat(self, state: FakeState) -> None:
        decision = decide_pre_tool_use(SEAT3, "mcp__mafia__submit_speech", {"text": "hi"}, state)
        assert isinstance(decision, Allow)
        assert decision.updated_input == {"text": "hi", "seat": 3}

    @pytest.mark.parametrize(
        "tool",
        [
            "mcp__mafia__ping",
            "mcp__mafia__spike_arm",
            "mcp__mafia__mystery_brief",
            "Read",
            "Bash",
            "Agent",
        ],
    )
    def test_non_seat_tools_denied(self, state: FakeState, tool: str) -> None:
        assert isinstance(decide_pre_tool_use(SEAT3, tool, {}, state), Deny)


class TestAgentsAndUnknown:
    def test_agent_uses_only_its_family(self, state: FakeState) -> None:
        decision = decide_pre_tool_use(
            ARCHITECT, "mcp__mafia__mystery_brief", {"caller": "mystery-auditor"}, state
        )
        assert isinstance(decision, Allow)
        assert decision.updated_input == {
            "caller": "mystery-architect"
        }  # forged caller overwritten
        assert isinstance(
            decide_pre_tool_use(ARCHITECT, "mcp__mafia__mystery_submit_audit", {}, state), Deny
        )
        assert isinstance(decide_pre_tool_use(ARCHITECT, "mcp__mafia__my_turn", {}, state), Deny)

    @pytest.mark.parametrize("tool", ["mcp__mafia__ping", "mcp__mafia__my_turn", "Read"])
    def test_unknown_agent_denied_everything_but_schema(self, state: FakeState, tool: str) -> None:
        assert isinstance(decide_pre_tool_use(UNKNOWN, tool, {}, state), Deny)


class TestSealAndStop:
    def test_only_main_spawns_are_sealed(self) -> None:
        assert should_seal(MAIN, "Agent")
        assert should_seal(MAIN, "Task")
        assert not should_seal(MAIN, "mcp__mafia__ping")
        assert not should_seal(SEAT3, "Agent")

    def test_stop_blocked_while_awaiting(self, state: FakeState) -> None:
        assert decide_subagent_stop(SEAT3, stop_hook_active=False, state=state) is not None

    def test_stop_allowed_after_submission(self) -> None:
        assert decide_subagent_stop(SEAT3, stop_hook_active=False, state=FakeState()) is None

    def test_stop_blocks_at_most_once(self, state: FakeState) -> None:
        assert decide_subagent_stop(SEAT3, stop_hook_active=True, state=state) is None

    def test_main_stop_never_blocked(self, state: FakeState) -> None:
        assert decide_subagent_stop(MAIN, stop_hook_active=False, state=state) is None


class TestSealResponse:
    def test_dict_response_keeps_envelope_and_replaces_content(self) -> None:
        response = {
            "status": "completed",
            "agentId": "a1",
            "prompt": "차례가 되었습니다.",
            "content": [{"type": "text", "text": "SECRET-seat-3-abcd"}],
            "totalTokens": 10,
        }
        sealed = seal_tool_response(response, "seat-3")
        assert sealed == {
            "status": "completed",
            "agentId": "a1",
            "prompt": "차례가 되었습니다.",
            "content": [{"type": "text", "text": "seat-3: 제출 완료"}],
            "totalTokens": 10,
        }
        assert isinstance(sealed, dict)
        assert sealed.keys() == response.keys()

    @pytest.mark.parametrize("field", ["result", "text", "output", "message", "summary"])
    def test_text_fields_replaced(self, field: str) -> None:
        sealed = seal_tool_response({field: "SECRET", "status": "ok"}, "seat-3")
        assert "SECRET" not in str(sealed)

    @pytest.mark.parametrize("response", ["SECRET", None, 42, ["SECRET"]])
    def test_non_mapping_response_becomes_text(self, response: object) -> None:
        assert seal_tool_response(response, "seat-3") == "seat-3: 제출 완료"

    def test_original_response_not_mutated(self) -> None:
        response = {"content": [{"type": "text", "text": "SECRET"}]}
        seal_tool_response(response, "seat-3")
        assert response["content"][0]["text"] == "SECRET"


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("<task-notification>\n<task-id>a1</task-id>", True),
        ("  \n<task-notification>", True),
        ("도윤 님 그건 억측이에요 <task-notification>", False),
        (":vote 도윤", False),
    ],
)
def test_is_task_notification(prompt: str, expected: bool) -> None:
    assert is_task_notification(prompt) is expected


class TestHandback:
    """VSCode background subagents must finish through SubagentHandback (observed M2)."""

    def test_handback_before_submitting_is_denied(self, state: FakeState) -> None:
        decision = decide_pre_tool_use(SEAT3, "SubagentHandback", {"message": "제출 완료"}, state)
        assert isinstance(decision, Deny)

    def test_handback_message_is_always_replaced(self) -> None:
        done = FakeState()  # nothing pending
        leaky = {"message": "제출 완료. 도윤(4번, 마피아)으로 발언했습니다."}
        decision = decide_pre_tool_use(SEAT3, "SubagentHandback", leaky, done)
        assert isinstance(decision, Allow)
        assert decision.updated_input == {"message": "제출 완료"}

    def test_agents_can_hand_back_too(self) -> None:
        decision = decide_pre_tool_use(
            ARCHITECT, "SubagentHandback", {"message": "진상은…"}, FakeState()
        )
        assert isinstance(decision, Allow) and decision.updated_input == {"message": "제출 완료"}

    def test_main_and_unknown_cannot_use_it(self, state: FakeState) -> None:
        assert isinstance(
            decide_pre_tool_use(MAIN, "SubagentHandback", {"message": "x"}, state), Deny
        )
        assert isinstance(
            decide_pre_tool_use(UNKNOWN, "SubagentHandback", {"message": "x"}, state), Deny
        )
