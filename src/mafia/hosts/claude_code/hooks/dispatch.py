"""Turns a Claude Code hook payload into a hook response, failing closed on any error.

Claude Code treats a hook that crashes or exits non-zero (other than 2) as non-blocking, so an
uncaught exception would silently *allow* the call. Every path here therefore ends in an
explicit decision, and errors map to the most restrictive safe response for the event.
"""

from __future__ import annotations

import contextlib
import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

from mafia.harness.identity import Caller, CallerKind, classify
from mafia.harness.ports import HarnessState, HumanInputKind, HumanInputOutcome
from mafia.harness.tools import KNOWN_AGENTS
from mafia.hosts.claude_code.hooks import echo, policy
from mafia.hosts.claude_code.hooks.prompts import PromptKind, classify_prompt


class HookMode(StrEnum):
    ENFORCE = "enforce"
    OBSERVE = "observe"  # M0 only: allow everything, record raw payloads


@dataclass(frozen=True, slots=True)
class HookContext:
    mode: HookMode
    state_factory: Callable[[], HarnessState]
    log_path: Path | None


def read_mode(path: Path) -> HookMode:
    """Missing or unreadable mode file means ENFORCE: observation must be opted into."""
    try:
        return HookMode(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return HookMode.ENFORCE


def handle(payload: Mapping[str, Any], ctx: HookContext) -> dict[str, Any]:
    event = payload.get("hook_event_name")
    caller = classify(payload.get("agent_type"), KNOWN_AGENTS)

    if ctx.mode is HookMode.OBSERVE:
        return _pre_tool_use_output(policy.Allow("observe mode")) if event == "PreToolUse" else {}

    match event:
        case "PreToolUse":
            tool_name = _str(payload.get("tool_name"))
            decision = policy.decide_pre_tool_use(
                caller, tool_name, _mapping(payload.get("tool_input")), ctx.state_factory()
            )
            if isinstance(decision, policy.Allow) and _echo_gated(caller, tool_name):
                missing = echo.unechoed(_str(payload.get("transcript_path")))
                if missing:
                    decision = policy.Deny(echo.echo_reason(missing))
            return _pre_tool_use_output(decision)
        case "Stop":
            if caller.kind is not CallerKind.MAIN or payload.get("stop_hook_active"):
                return {}
            missing = echo.unechoed(_str(payload.get("transcript_path")))
            return {"decision": "block", "reason": echo.echo_reason(missing)} if missing else {}
        case "PostToolUse":
            return _post_tool_use(caller, payload)
        case "SubagentStart":
            if caller.kind in (CallerKind.SEAT, CallerKind.AGENT) and caller.agent_type:
                ctx.state_factory().record_agent_started(
                    _str(payload.get("agent_id")), caller.agent_type
                )
            return {}
        case "SubagentStop":
            state = ctx.state_factory()
            reason = policy.decide_subagent_stop(
                caller,
                bool(payload.get("stop_hook_active")),
                state,
                _str(payload.get("last_assistant_message")),
            )
            if reason:
                return {"decision": "block", "reason": reason}
            if caller.kind in (CallerKind.SEAT, CallerKind.AGENT) and caller.agent_type:
                state.record_agent_finished(_str(payload.get("agent_id")), caller.agent_type)
            return {}
        case "UserPromptSubmit":
            prompt = classify_prompt(_str(payload.get("prompt")))
            match prompt.kind:
                case PromptKind.AGENT_SEALED:
                    return _sealed_notification()
                case PromptKind.AGENT_UNSEALED:
                    return _blocked_notification()
                case PromptKind.HUMAN:
                    if caller.kind is not CallerKind.MAIN:
                        return {}
                    return _human_input_output(ctx.state_factory().handle_human_input(prompt.text))
        case _:
            return {}


def _human_input_output(outcome: HumanInputOutcome) -> dict[str, Any]:
    match outcome.kind:
        case HumanInputKind.SUBMITTED:
            return {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": outcome.message,
                }
            }
        case HumanInputKind.INFO | HumanInputKind.REJECTED:
            # Answered straight to the human; the driver model is never invoked.
            return {"decision": "block", "reason": outcome.message}
        case HumanInputKind.PASSTHROUGH:
            return {}


def _sealed_notification() -> dict[str, Any]:
    """Let a sealed completion notice wake the driver (hosts that force background agents)."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "에이전트 차례가 끝났습니다. 이 알림에 답하지 말고 곧바로 mcp__mafia__advance를 호출해 진행하세요.",
        }
    }


def _blocked_notification() -> dict[str, Any]:
    """Subagent results must never reach the main session; it learns progress via `advance`."""
    return {
        "decision": "block",
        "reason": "(AI 차례 진행 중)",
        "hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "suppressOriginalPrompt": True},
    }


def fail_closed(payload: Mapping[str, Any] | None, error: BaseException) -> dict[str, Any]:
    event = payload.get("hook_event_name") if payload else None
    match event:
        case "PreToolUse" | None:
            # None: we could not even parse the event, and only PreToolUse can stop harm.
            return _pre_tool_use_output(
                policy.Deny(f"게임 하네스 내부 오류로 차단했습니다: {error!r}")
            )
        case "PostToolUse":
            caller = classify(payload.get("agent_type") if payload else None, KNOWN_AGENTS)
            tool = _str(payload.get("tool_name")) if payload else ""
            if policy.should_seal(caller, tool):
                return _sealed_output("subagent", payload.get("tool_response") if payload else None)
            return {}
        case "UserPromptSubmit":
            if (
                payload
                and classify_prompt(_str(payload.get("prompt"))).kind is not PromptKind.HUMAN
            ):
                return _blocked_notification()
            return {
                "decision": "block",
                "reason": "게임 엔진 오류로 입력을 기록하지 못했습니다. 다시 보내 주세요.",
            }
        case _:
            # SubagentStop and others: never trap an agent in a loop because of our own bug.
            return {}


def _post_tool_use(caller: Caller, payload: Mapping[str, Any]) -> dict[str, Any]:
    tool_name = _str(payload.get("tool_name"))
    if caller.kind is CallerKind.MAIN and policy.mcp_short_name(tool_name) in DISPLAY_TOOLS:
        if _has_display(payload.get("tool_response")):
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": ECHO_REMINDER,
                }
            }
        return {}
    if not policy.should_seal(caller, tool_name):
        return {}
    target = _mapping(payload.get("tool_input")).get("subagent_type")
    return _sealed_output(
        target if isinstance(target, str) else "subagent", payload.get("tool_response")
    )


def _echo_gated(caller: Caller, tool_name: str) -> bool:
    """Driver actions that must wait until the latest display has been written out."""
    if caller.kind is not CallerKind.MAIN:
        return False
    return tool_name in policy.SPAWN_TOOLS or policy.mcp_short_name(tool_name) in DISPLAY_TOOLS


# Driver tools whose <display> the human must read in the chat, verbatim.
DISPLAY_TOOLS = frozenset({"advance", "new_game", "prepare_mystery"})
ECHO_REMINDER = (
    "방금 받은 <display> 안의 내용을 지금 당신의 답변으로 한 글자도 빼거나 바꾸지 말고 그대로 출력하세요. "
    "사람은 도구 결과를 볼 수 없고 당신이 쓴 글만 봅니다. 요약, 해설, '~를 작성했어요' 같은 설명은 쓰지 마세요. "
    "출력한 뒤 <next>에 따라 진행하세요."
)
_DISPLAY = re.compile(r"<display>(.*?)</display>", re.DOTALL)


def _has_display(tool_response: object) -> bool:
    text = (
        tool_response
        if isinstance(tool_response, str)
        else json.dumps(tool_response, ensure_ascii=False)
    )
    match = _DISPLAY.search(text)
    return match is not None and bool(match.group(1).replace("\\n", "").strip())


def _sealed_output(agent_type: str, tool_response: object) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "updatedToolOutput": policy.seal_tool_response(tool_response, agent_type),
        }
    }


def _pre_tool_use_output(decision: policy.Decision) -> dict[str, Any]:
    out: dict[str, Any] = {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow" if isinstance(decision, policy.Allow) else "deny",
        "permissionDecisionReason": decision.reason,
    }
    if isinstance(decision, policy.Allow) and decision.updated_input is not None:
        out["updatedInput"] = decision.updated_input
    return {"hookSpecificOutput": out}


def append_log(
    log_path: Path,
    payload: Mapping[str, Any] | None,
    output: Mapping[str, Any],
    mode: HookMode,
    elapsed_ms: float,
    error: str | None,
) -> None:
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "mode": mode.value,
        "event": payload.get("hook_event_name") if payload else None,
        "agent_type": payload.get("agent_type") if payload else None,
        "tool_name": payload.get("tool_name") if payload else None,
        "elapsed_ms": round(elapsed_ms, 1),
        "error": error,
        "input": payload,
        "output": output,
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def run(raw: str, ctx: HookContext) -> str:
    started = time.monotonic()
    payload: Mapping[str, Any] | None = None
    error: str | None = None
    try:
        payload = _parse_payload(raw)
        output = handle(payload, ctx)
    except Exception as exc:  # fail closed on anything, including bugs in policy code
        error = repr(exc)
        output = fail_closed(payload, exc)
    if ctx.log_path is not None:
        elapsed_ms = (time.monotonic() - started) * 1000
        # logging must never change the decision
        with contextlib.suppress(OSError):
            append_log(ctx.log_path, payload, output, ctx.mode, elapsed_ms, error)
    return json.dumps(output, ensure_ascii=False)


def _parse_payload(raw: str) -> Mapping[str, Any]:
    parsed: object = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("hook payload is not a JSON object")
    return cast(dict[str, Any], parsed)


def _str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}  # type: ignore[return-value]
