"""MCP tools. Which caller may use which tool is enforced by the host adapter's hooks
(`mafia.harness.tools`); every tool here also returns only what its caller may see."""

from __future__ import annotations

import functools
from collections.abc import Callable

from mcp.server.mcpserver import MCPServer

from mafia.app.table import NoActiveGame, TableApp
from mafia.engine.requests import Answer
from mafia.harness.tools import MCP_SERVER_NAME

server = MCPServer(
    MCP_SERVER_NAME,
    instructions="mafia-with-ai 게임 엔진. 진행자는 new_game/advance/status만, 좌석 에이전트는 my_turn/submit_*만 쓴다.",
)


@functools.cache
def app() -> TableApp:
    return TableApp.open_default()


def _guard(fn: Callable[[], str]) -> str:
    try:
        return fn()
    except NoActiveGame as exc:
        return str(exc)


# --- driver -------------------------------------------------------------------------------
@server.tool()
def new_game(human_name: str = "나", watch: bool = False, mode: str = "original") -> str:
    """새 게임을 시작한다. human_name: 사람 플레이어 이름. watch=true면 AI 8명끼리 하는 관전 모드.
    결과의 <display>는 그대로 출력하고 <next>에 따라 진행한다."""
    return app().new_game(mode=mode, human_name=human_name, watch=watch).to_text()


@server.tool()
def advance() -> str:
    """게임을 진행하고, 사람에게 보여 줄 새 내용(<display>)과 다음 할 일(<next>)을 돌려준다."""
    return _guard(lambda: app().advance().to_text())


@server.tool()
def status() -> str:
    """현재 일차, 단계, 생존자(공개 정보)."""
    return _guard(app().status)


# --- seats --------------------------------------------------------------------------------
@server.tool()
def my_turn(seat: int) -> str:
    """당신(좌석)이 지금 알 수 있는 모든 것과 지금 할 일. 매 차례 가장 먼저 호출한다."""
    return _guard(lambda: app().seat_turn(seat))


@server.tool()
def submit_speech(
    seat: int,
    speech: str | None = None,
    private_note: str | None = None,
    suspicion: dict[str, int] | None = None,
) -> str:
    """발언 제출. speech를 생략하면 패스. private_note는 나만 보는 메모, suspicion은 {좌석 번호: 0~100}."""
    return _guard(
        lambda: app().seat_submit(
            seat, Answer.SPEECH, speech=speech, private_note=private_note, suspicion=suspicion
        )
    )


@server.tool()
def submit_target(
    seat: int,
    target: int | None = None,
    private_note: str | None = None,
    suspicion: dict[str, int] | None = None,
) -> str:
    """대상 선택(투표·밤 행동). target은 선택지의 좌석 번호, 생략하면 기권."""
    return _guard(
        lambda: app().seat_submit(
            seat, Answer.TARGET, target=target, private_note=private_note, suspicion=suspicion
        )
    )


@server.tool()
def submit_approval(
    seat: int,
    approve: bool | None = None,
    private_note: str | None = None,
    suspicion: dict[str, int] | None = None,
) -> str:
    """찬반 투표. approve=true 찬성, false 반대, 생략하면 기권."""
    return _guard(
        lambda: app().seat_submit(
            seat, Answer.APPROVAL, approve=approve, private_note=private_note, suspicion=suspicion
        )
    )


def main() -> None:
    server.run()


if __name__ == "__main__":
    main()
