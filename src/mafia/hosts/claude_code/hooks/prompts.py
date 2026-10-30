"""What a UserPromptSubmit payload actually is.

Claude Code feeds more than human typing through this hook (observed in the VSCode extension):
- background subagent results, as `<task-notification>` or `<agent-message>` envelopes,
- human messages prefixed with IDE context blocks such as `<ide_opened_file>`.
Neither envelope may ever be read as the human's game input.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from mafia.hosts.claude_code.hooks.policy import is_sealed_handback


class PromptKind(StrEnum):
    HUMAN = "human"
    AGENT_SEALED = "agent_sealed"  # a subagent result carrying only the fixed handback
    AGENT_UNSEALED = "agent_unsealed"  # anything else from a subagent: must not reach the driver


@dataclass(frozen=True, slots=True)
class Prompt:
    kind: PromptKind
    text: str = ""  # the human's own words, IDE context removed


_IDE_BLOCK = re.compile(r"^\s*<(ide_[a-z_]+)>.*?</\1>\s*", re.DOTALL)
_RESULT = re.compile(r"<result>(.*?)</result>", re.DOTALL)
_AGENT_MESSAGE = re.compile(r"^\s*<agent-message\b[^>]*>(.*)</agent-message>\s*$", re.DOTALL)
_FRAME_START = "[Subagent hand-back]"
_FRAME_END = "The report follows:"
# Harness notes allowed above the hand-back frame; they list tool names, never model text.
_ALLOWED_NOTE = re.compile(
    r"^\s*(<tools-used-before-summary>[^<]*</tools-used-before-summary>\s*)*$"
)


def strip_ide_context(text: str) -> str:
    while True:
        match = _IDE_BLOCK.match(text)
        if match is None:
            return text.strip()
        text = text[match.end() :]


def _sealed_task_notification(prompt: str) -> bool:
    results = _RESULT.findall(prompt)
    return bool(results) and all(is_sealed_handback(r) for r in results)


def _sealed_agent_message(prompt: str) -> bool:
    match = _AGENT_MESSAGE.match(prompt)
    if match is None:
        return False
    body = match.group(1)
    start = body.find(_FRAME_START)
    end = body.find(_FRAME_END, start)
    if start < 0 or end < 0:
        return False
    if not _ALLOWED_NOTE.match(body[:start]):
        return False
    report = body[end + len(_FRAME_END) :]
    lines = [line for line in report.split("\n") if line.strip()]
    # The harness indents every report line; a column-zero line would be forged framing.
    if any(not line.startswith(" ") for line in lines):
        return False
    return is_sealed_handback(" ".join(line.strip() for line in lines))


def classify_prompt(prompt: str) -> Prompt:
    head = prompt.lstrip()
    if head.startswith("<task-notification"):
        sealed = _sealed_task_notification(prompt)
        return Prompt(PromptKind.AGENT_SEALED if sealed else PromptKind.AGENT_UNSEALED)
    if head.startswith("<agent-message"):
        sealed = _sealed_agent_message(prompt)
        return Prompt(PromptKind.AGENT_SEALED if sealed else PromptKind.AGENT_UNSEALED)
    return Prompt(PromptKind.HUMAN, strip_ide_context(prompt))
