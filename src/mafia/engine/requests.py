"""What the game asks a seat to do, and the shape of the answer.

Every seat action, human or AI, is an `Action` answering one open `SeatRequest`. The engine
validates the shape generically; modes add rule-specific checks on top.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

MAX_SPEECH_CHARS = 600
MAX_NOTE_CHARS = 1000


class Answer(StrEnum):
    SPEECH = "speech"  # free text; `speech=None` means pass when allowed
    TARGET = "target"  # one seat from `options`; `target=None` means abstain when allowed
    APPROVAL = "approval"  # yes / no


class RequestSpec(BaseModel):
    """A request as produced by a mode, before the engine assigns its id."""

    model_config = ConfigDict(frozen=True)

    seat: int
    kind: str  # mode-defined, e.g. "discussion", "nomination_vote"
    answer: Answer
    instruction: str  # shown to the seat, in Korean
    options: tuple[int, ...] = ()
    # Display names for `options` when they are not seats (e.g. locations), same order.
    option_labels: tuple[str, ...] = ()
    allow_pass: bool = False


class SeatRequest(RequestSpec):
    request_id: str


class Action(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    speech: str | None = Field(default=None, max_length=MAX_SPEECH_CHARS)
    target: int | None = None
    approve: bool | None = None
    # Card ids to reveal publicly together with a speech (murder mystery). The engine shows the
    # card's real text, so a revealed card cannot be forged.
    reveal: tuple[str, ...] = Field(default=(), max_length=3)
    # Never shown to other seats. Returned to the same seat as its own memory.
    private_note: str | None = Field(default=None, max_length=MAX_NOTE_CHARS)
    # 0-100 per seat, god-only; feeds the post-game suspicion matrix.
    suspicion: dict[int, int] | None = None


class ActionRejected(ValueError):
    """The action does not answer the request. The message is safe to show to that seat."""


def validate_shape(request: SeatRequest, action: Action, seat_count: int) -> None:
    match request.answer:
        case Answer.SPEECH:
            if action.target is not None or action.approve is not None:
                raise ActionRejected("발언 요청에는 speech만 채워야 합니다.")
            if action.speech is None and not request.allow_pass:
                raise ActionRejected("이 요청은 패스할 수 없습니다. speech를 채워 주세요.")
            if action.speech is not None and not action.speech.strip():
                raise ActionRejected("빈 발언은 낼 수 없습니다. 패스하려면 speech를 비워 두세요.")
            if action.reveal and action.speech is None:
                raise ActionRejected("카드를 공개할 때는 한마디라도 발언을 함께 해야 합니다.")
        case Answer.TARGET:
            if action.speech is not None or action.approve is not None or action.reveal:
                raise ActionRejected("대상 선택 요청에는 target만 채워야 합니다.")
            if action.target is None:
                if not request.allow_pass:
                    raise ActionRejected("이 요청은 기권할 수 없습니다. target을 골라 주세요.")
            elif action.target not in request.options:
                raise ActionRejected(f"고를 수 있는 대상은 {list(request.options)}입니다.")
        case Answer.APPROVAL:
            if action.speech is not None or action.target is not None or action.reveal:
                raise ActionRejected("찬반 요청에는 approve만 채워야 합니다.")
            if action.approve is None and not request.allow_pass:
                raise ActionRejected("approve에 찬성(true) 또는 반대(false)를 넣어 주세요.")
    if action.suspicion is not None:
        for seat, score in action.suspicion.items():
            if not 1 <= seat <= seat_count or not 0 <= score <= 100:
                raise ActionRejected("suspicion은 {좌석 번호: 0~100} 형식이어야 합니다.")
