"""Parsing the human's chat messages into game commands.

Commands start with ':' so they never collide with Claude Code's '/' commands or '@' mentions
(design D8). Anything else is free text: a speech when it is the human's turn to speak,
otherwise ordinary chat with the driver.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from mafia.engine.core_events import SeatInfo


class Command(StrEnum):
    PASS = "pass"
    VOTE = "vote"
    YES = "yes"
    NO = "no"
    WHO = "who"
    ROLE = "role"
    LOG = "log"
    HELP = "help"
    UNKNOWN = "unknown"
    SPEECH = "speech"  # free text


_ALIASES: dict[str, Command] = {
    ":pass": Command.PASS,
    ":패스": Command.PASS,
    ":vote": Command.VOTE,
    ":투표": Command.VOTE,
    ":target": Command.VOTE,
    ":대상": Command.VOTE,
    ":yes": Command.YES,
    ":찬성": Command.YES,
    ":no": Command.NO,
    ":반대": Command.NO,
    ":who": Command.WHO,
    ":생존": Command.WHO,
    ":role": Command.ROLE,
    ":역할": Command.ROLE,
    ":log": Command.LOG,
    ":기록": Command.LOG,
    ":help": Command.HELP,
    ":도움말": Command.HELP,
}

HELP_TEXT = """\
**게임 명령**
- 발언 차례: 채팅창에 그냥 말하면 발언이 됩니다
- `:pass` — 패스 / 기권
- `:vote 이름` 또는 `:vote 번호` — 지목·밤 행동 대상 선택
- `:yes` / `:no` — 찬반 투표
- `:who` — 생존자와 현재 단계
- `:role` — 내 역할 다시 보기
- `:log` — 최근 기록 다시 보기"""


@dataclass(frozen=True, slots=True)
class ParsedInput:
    command: Command
    argument: str = ""


def parse(text: str) -> ParsedInput | None:
    """None means "not game input" (empty, or a Claude Code slash command)."""
    stripped = text.strip()
    if not stripped or stripped.startswith("/"):
        return None
    if not stripped.startswith(":"):
        return ParsedInput(Command.SPEECH, stripped)
    head, _, rest = stripped.partition(" ")
    return ParsedInput(_ALIASES.get(head.lower(), Command.UNKNOWN), rest.strip())


def resolve_seat(argument: str, seats: Sequence[SeatInfo]) -> int | None:
    """Seat by number, exact name, or unique name prefix. None if missing or ambiguous."""
    arg = argument.strip().removesuffix("번").strip()
    if not arg:
        return None
    if arg.isdigit():
        number = int(arg)
        return number if any(s.seat == number for s in seats) else None
    exact = [s.seat for s in seats if s.name == arg]
    if exact:
        return exact[0]
    prefix = [s.seat for s in seats if s.name.startswith(arg)]
    return prefix[0] if len(prefix) == 1 else None
