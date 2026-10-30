"""Was the last game display actually written into the chat?

The driver model tends to summarise ("하람이 발언했어요") instead of copying the display, and
then the human never reads other players' words. Instructions alone did not fix this, so the
hooks check the driver's transcript: the next driver tool call (and the end of its turn) is
refused until every line of the latest display appears in its replies.

This is a presentation guarantee, not a security boundary: if the transcript cannot be read,
the check passes rather than stalling the game.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast

_DISPLAY = re.compile(r"<display>(.*?)</display>", re.DOTALL)
# Formatting the driver may legitimately change; content may not change.
_IGNORED = re.compile("[\\s*_`>#|\\-\"'\u201c\u201d\u2018\u2019]")
KEY_CHARS = 30


def _normalise(text: str) -> str:
    return _IGNORED.sub("", text)


def _as_dict(value: object) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _as_list(value: object) -> list[Any]:
    return cast(list[Any], value) if isinstance(value, list) else []


def _texts(blocks: Iterable[Any]) -> list[str]:
    out: list[str] = []
    for block in blocks:
        b = _as_dict(block)
        if b.get("type") == "text":
            out.append(str(b.get("text", "")))
    return out


def _display_in(entry: dict[str, Any]) -> str | None:
    if entry.get("type") != "user":
        return None
    for block in _as_list(_as_dict(entry.get("message")).get("content")):
        b = _as_dict(block)
        if b.get("type") == "tool_result":
            raw: object = b.get("content")
            items = _as_list(raw)
            text = "\n".join(_texts(items)) if items else str(raw or "")
            match = _DISPLAY.search(text)
            if match is not None:
                return match.group(1)
    return None


def latest_display_and_replies(entries: list[dict[str, Any]]) -> tuple[str, str] | None:
    """The newest display shown to the driver, and everything the driver wrote after it."""
    replies: list[str] = []
    for entry in reversed(entries):
        display = _display_in(entry)
        if display is not None:
            return display, "\n".join(reversed(replies))
        if entry.get("type") == "assistant":
            content = _as_list(_as_dict(entry.get("message")).get("content"))
            replies.extend(reversed(_texts(content)))
    return None


def missing_lines(display: str, replies: str) -> list[str]:
    written = _normalise(replies)
    missing: list[str] = []
    for line in display.splitlines():
        key = _normalise(line)[:KEY_CHARS]
        if key and key not in written:
            missing.append(line.strip())
    return missing


def unechoed(transcript_path: str) -> list[str]:
    """Lines of the latest display that the driver has not written yet ([] when all shown)."""
    try:
        raw = Path(transcript_path).read_text(encoding="utf-8")
    except OSError:
        return []
    entries: list[dict[str, Any]] = []
    for line in raw.splitlines():
        try:
            parsed: object = json.loads(line)
        except json.JSONDecodeError:
            continue
        entry = _as_dict(parsed)
        if entry:
            entries.append(entry)
    found = latest_display_and_replies(entries)
    return [] if found is None else missing_lines(*found)


def echo_reason(missing: list[str]) -> str:
    preview = "\n".join(missing[:5])
    more = f"\n… 외 {len(missing) - 5}줄" if len(missing) > 5 else ""
    return (
        "직전 진행 결과(<display>)를 아직 사람에게 보여 주지 않았습니다. 사람은 도구 결과를 볼 수 없습니다. "
        "다음 줄들을 포함해 <display> 내용 전체를 한 글자도 바꾸지 말고 먼저 답변으로 출력한 뒤 다시 진행하세요.\n"
        f"{preview}{more}"
    )
