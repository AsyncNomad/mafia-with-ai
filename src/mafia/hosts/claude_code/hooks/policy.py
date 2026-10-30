"""Isolation policy for Claude Code, as pure functions of (caller, tool call, game state).

Nothing here performs I/O, so every rule is unit-testable from JSON fixtures. The dispatcher
in `__main__` turns these decisions into Claude Code's hook output format.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from mafia.harness.identity import Caller, CallerKind
from mafia.harness.ports import HarnessState
from mafia.harness.tools import AGENT_TOOLS, PUBLIC_TOOLS, SEAT_ARGUMENT, SEAT_TOOLS
from mafia.hosts.claude_code import MCP_TOOL_PREFIX

# Claude Code's subagent tool. "Task" is the older name; both are treated identically.
SPAWN_TOOLS = frozenset({"Agent", "Task"})
# Loads deferred tool schemas. Reveals nothing about the game, and MCP tools may be deferred.
SCHEMA_TOOLS = frozenset({"ToolSearch"})
MAIN_SKILLS = frozenset({"mafia"})

# Fields of the spawn call the main session must not control.
_SPAWN_FIELDS_STRIPPED = ("isolation", "name", "model")

WAKE_PROMPT = "차례가 되었습니다. 당신의 지침에 따라 진행하세요."

# Observed in Claude Code 2.1.312 (M0): Agent runs in the background *by default*, and a
# background result reaches the main session as a `<task-notification>` user prompt instead of
# passing through PostToolUse. Spawns are therefore forced to the foreground so the result is
# sealed in PostToolUse, and notifications are blocked in UserPromptSubmit as a second layer.
TASK_NOTIFICATION_TAG = "<task-notification>"
_RESULT_TEXT_FIELDS = ("result", "text", "output", "message", "summary")


@dataclass(frozen=True, slots=True)
class Allow:
    reason: str
    updated_input: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class Deny:
    reason: str


Decision = Allow | Deny


def mcp_short_name(tool_name: str) -> str | None:
    if tool_name.startswith(MCP_TOOL_PREFIX):
        return tool_name.removeprefix(MCP_TOOL_PREFIX)
    return None


def sealed_text(agent_type: str) -> str:
    return f"{agent_type}: 제출 완료"


def decide_pre_tool_use(
    caller: Caller,
    tool_name: str,
    tool_input: Mapping[str, Any],
    state: HarnessState,
) -> Decision:
    if tool_name in SCHEMA_TOOLS:
        return Allow("tool schema lookup")
    match caller.kind:
        case CallerKind.MAIN:
            return _decide_main(tool_name, tool_input, state)
        case CallerKind.SEAT:
            return _decide_seat(caller, tool_name, tool_input)
        case CallerKind.AGENT:
            return _decide_agent(caller, tool_name)
        case CallerKind.UNKNOWN:
            return Deny(
                f"알 수 없는 에이전트({caller.agent_type})는 이 워크스페이스에서 도구를 쓸 수 없습니다."
            )


def _decide_main(tool_name: str, tool_input: Mapping[str, Any], state: HarnessState) -> Decision:
    short = mcp_short_name(tool_name)
    if short is not None:
        if short in PUBLIC_TOOLS:
            return Allow("public game tool")
        return Deny("진행자는 공개 게임 도구만 쓸 수 있습니다.")

    if tool_name in SPAWN_TOOLS:
        target = tool_input.get("subagent_type")
        if not isinstance(target, str) or not state.is_awaiting(target):
            return Deny(
                "게임 엔진이 지금 이 에이전트를 기다리고 있지 않습니다. advance가 지시한 에이전트만 깨울 수 있습니다."
            )
        updated = {k: v for k, v in tool_input.items() if k not in _SPAWN_FIELDS_STRIPPED}
        updated["prompt"] = WAKE_PROMPT
        updated["description"] = f"{target} 차례"
        updated["run_in_background"] = False
        return Allow("awaited agent; prompt replaced; forced foreground", updated)

    if tool_name == "Skill" and tool_input.get("skill") in MAIN_SKILLS:
        return Allow("game skill")

    return Deny("이 워크스페이스에서 진행자는 게임 진행 도구만 쓸 수 있습니다.")


def _decide_seat(caller: Caller, tool_name: str, tool_input: Mapping[str, Any]) -> Decision:
    short = mcp_short_name(tool_name)
    if short is not None and short in SEAT_TOOLS:
        updated = dict(tool_input)
        updated[SEAT_ARGUMENT] = caller.seat  # identity comes from the harness, never the model
        return Allow("seat tool; seat bound to caller", updated)
    return Deny("플레이어는 자기 게임 도구만 쓸 수 있습니다.")


def _decide_agent(caller: Caller, tool_name: str) -> Decision:
    short = mcp_short_name(tool_name)
    allowed = AGENT_TOOLS.get(caller.agent_type or "", frozenset())
    if short is not None and short in allowed:
        return Allow("agent tool family")
    return Deny(f"{caller.agent_type}에게 허용되지 않은 도구입니다.")


def should_seal(caller: Caller, tool_name: str) -> bool:
    """Every subagent result is replaced before the main session sees it."""
    return caller.kind is CallerKind.MAIN and tool_name in SPAWN_TOOLS


def seal_tool_response(response: object, agent_type: str) -> object:
    """Replace every result-bearing part of an Agent tool response, keeping its shape.

    Claude Code validates `updatedToolOutput` against the original response shape, so the
    envelope (status, ids, usage) is kept and only content is overwritten.
    """
    text = sealed_text(agent_type)
    if not isinstance(response, Mapping):
        return text
    sealed: dict[str, Any] = dict(cast(Mapping[str, Any], response))
    if "content" in sealed:
        sealed["content"] = [{"type": "text", "text": text}]
    for field in _RESULT_TEXT_FIELDS:
        if field in sealed:
            sealed[field] = text
    # Keys are never removed: a replacement whose shape differs from the original is silently
    # ignored by Claude Code (observed in M0). `prompt` is kept; it is already WAKE_PROMPT.
    return sealed


def is_task_notification(prompt: str) -> bool:
    return prompt.lstrip().startswith(TASK_NOTIFICATION_TAG)


def decide_subagent_stop(caller: Caller, stop_hook_active: bool, state: HarnessState) -> str | None:
    """Return a block reason if the agent tries to stop before fulfilling its request.

    Blocks at most once: if the agent already got a block and still did not submit, it is
    allowed to stop and the engine applies its fallback action. Looping forever is worse.
    """
    if caller.kind not in (CallerKind.SEAT, CallerKind.AGENT) or caller.agent_type is None:
        return None
    if stop_hook_active:
        return None
    if state.is_awaiting(caller.agent_type):
        return "아직 행동을 제출하지 않았습니다. 지침에서 요구한 제출 도구를 호출한 뒤 끝내세요."
    return None
