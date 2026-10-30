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
    SHOW = "show"
    ASK = "ask"  # a question for the driver, never game input
    UNKNOWN = "unknown"
    SPEECH = "speech"  # free text


_ALIASES: dict[str, Command] = {
    ":pass": Command.PASS,
    ":패스": Command.PASS,
    ":vote": Command.VOTE,
    ":투표": Command.VOTE,
    ":target": Command.VOTE,
    ":대상": Command.VOTE,
    ":go": Command.VOTE,
    ":조사": Command.VOTE,
    ":show": Command.SHOW,
    ":ask": Command.ASK,
    ":질문": Command.ASK,
    ":공개": Command.SHOW,
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
- `:go 장소` — (머더 미스터리) 조사할 장소 선택
- `:show 카드id 할 말` — (머더 미스터리) 카드를 공개하며 발언
- `:yes` / `:no` — 찬반 투표
- `:who` — 생존자와 현재 단계
- `:role` — 내 역할 다시 보기
- `:log` — 최근 기록 다시 보기
- `:ask 질문` — 진행자에게 묻기 (내 차례에 그냥 쓰는 말은 모두 발언으로 제출됩니다)"""


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


def resolve_label(argument: str, options: Sequence[int], labels: Sequence[str]) -> int | None:
    """Option by number, exact label, or unique label prefix."""
    arg = argument.strip().removesuffix("번").strip()
    if arg.isdigit():
        number = int(arg)
        return number if number in options else None
    exact = [o for o, label in zip(options, labels, strict=True) if label == arg]
    if exact:
        return exact[0]
    prefix = [o for o, label in zip(options, labels, strict=True) if arg and label.startswith(arg)]
    return prefix[0] if len(prefix) == 1 else None


def split_show(argument: str) -> tuple[tuple[str, ...], str]:
    """':show k12 k03 이 카드 보세요' -> (('k12', 'k03'), '이 카드 보세요')."""
    cards: list[str] = []
    words = argument.split()
    while words and len(words[0]) == 3 and words[0][0].lower() == "k" and words[0][1:].isdigit():
        cards.append(words.pop(0).lower())
    return tuple(cards), " ".join(words)
