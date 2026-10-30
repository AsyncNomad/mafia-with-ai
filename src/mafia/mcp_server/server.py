"""MCP tools. Which caller may use which tool is enforced by the host adapter's hooks
(`mafia.harness.tools`); every tool here also returns only what its caller may see."""

from __future__ import annotations

import contextlib
import threading
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from mcp.server.mcpserver import MCPServer

from mafia import paths
from mafia.app.table import NoActiveGame, TableApp
from mafia.engine.requests import Answer
from mafia.harness.tools import MCP_SERVER_NAME

server = MCPServer(
    MCP_SERVER_NAME,
    instructions="mafia-with-ai 게임 엔진. 진행자는 new_game/advance/status만, 좌석 에이전트는 my_turn/submit_*만 쓴다.",
)


# The SDK runs synchronous tools on a pool of worker threads, and a sqlite3 connection may only
# be used by the thread that created it. Each worker thread therefore gets its own table (and
# connection); concurrent writers are serialised by the database (see mafia.service).
_local = threading.local()
_generation = 0


def app() -> TableApp:
    if getattr(_local, "generation", None) != _generation:
        _local.app = TableApp.open_default()
        _local.generation = _generation
    return _local.app


def reset_for_tests() -> None:
    global _generation
    _generation += 1


def _guard(fn: Callable[[], str]) -> str:
    try:
        return fn()
    except (NoActiveGame, PermissionError, LookupError) as exc:
        return str(exc)
    except Exception as exc:
        # Never let the SDK swallow a failure into a bare "Error executing tool".
        log = paths.logs_dir() / "mcp-server.log"
        with contextlib.suppress(OSError):
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a", encoding="utf-8") as f:
                f.write(f"--- {datetime.now(UTC).isoformat()}\n{traceback.format_exc()}\n")
        return f"엔진 오류: {exc!r}. 자세한 내용은 runs/logs/mcp-server.log에 기록했습니다. `/mafia continue`로 다시 시도하세요."


# --- driver -------------------------------------------------------------------------------
@server.tool()
def new_game(human_name: str = "나", watch: bool = False, mode: str = "original") -> str:
    """새 게임을 시작한다. mode: "original"(마피아) 또는 "mystery"(머더 미스터리).
    human_name: 사람 플레이어 이름. watch=true면 AI 8명끼리 하는 관전 모드.
    결과의 <display>는 그대로 출력하고 <next>에 따라 진행한다."""
    return _guard(lambda: app().new_game(mode=mode, human_name=human_name, watch=watch).to_text())


@server.tool()
def advance() -> str:
    """게임을 진행하고, 사람에게 보여 줄 새 내용(<display>)과 다음 할 일(<next>)을 돌려준다."""
    return _guard(lambda: app().advance().to_text())


@server.tool()
def status() -> str:
    """현재 일차, 단계, 생존자(공개 정보)."""
    return _guard(lambda: app().status())


@server.tool()
def prepare_mystery(request: str = "") -> str:
    """머더 미스터리 시나리오를 새로 준비한다(설계 → 감수 → 블라인드 풀이 → 판정).
    request: 플레이어가 원하는 배경·분위기(없으면 무작위). 이후 advance 루프를 그대로 따른다."""
    return _guard(lambda: app().prepare_mystery(request).to_text())


# --- mystery pipeline agents (caller is injected by the harness) ---------------------------
@server.tool()
def mystery_brief(caller: str = "") -> str:
    """설계 브리프와 섹션별 JSON 스키마."""
    return _guard(lambda: app().prep().architect_brief(caller))


@server.tool()
def mystery_revision_packet(caller: str = "") -> str:
    """수정 차수일 때: 이전 초안, 불합격 사유, 감수 보고서, 블라인드 풀이 결과."""
    return _guard(lambda: app().prep().revision_packet(caller))


@server.tool()
def mystery_submit_section(
    section: str, payload: dict[str, Any] | list[Any], caller: str = ""
) -> str:
    """시나리오 섹션 하나를 제출한다. facts/characters/clues는 배열, 나머지는 객체."""
    return _guard(lambda: app().prep().submit_section(caller, section, payload))


@server.tool()
def mystery_check(caller: str = "") -> str:
    """제출된 초안 전체를 조립해 정적 검증을 돌린다. 오류가 0이어야 다음 단계로 간다."""
    return _guard(lambda: app().prep().check(caller))


@server.tool()
def mystery_review_packet(stage: str = "blind", caller: str = "") -> str:
    """감수 자료. stage=blind: 플레이어가 보는 것만, stage=full: 진상 포함 전체."""
    return _guard(lambda: app().prep().review_packet(caller, stage))


@server.tool()
def mystery_submit_audit(audit: dict[str, Any], caller: str = "") -> str:
    """감수 보고서 제출: {verdict, findings[], blind_attempt, summary}."""
    return _guard(lambda: app().prep().submit_audit(caller, audit))


@server.tool()
def mystery_solver_packet(caller: str = "") -> str:
    """풀이 작업 하나를 맡고, 그 작업의 공개 정보와 카드를 받는다."""
    return _guard(lambda: app().prep().solver_packet(caller))


@server.tool()
def mystery_submit_solution(job_id: str, solution: dict[str, Any], caller: str = "") -> str:
    """풀이 제출: {culprit, confidence, reasoning, suspects, confusions}."""
    return _guard(lambda: app().prep().submit_solution(caller, job_id, solution))


# --- seats --------------------------------------------------------------------------------
@server.tool()
def my_turn(seat: int) -> str:
    """당신(좌석)이 지금 알 수 있는 모든 것과 지금 할 일. 매 차례 가장 먼저 호출한다."""
    return _guard(lambda: app().seat_turn(seat))


@server.tool()
def submit_speech(
    seat: int,
    speech: str | None = None,
    reveal: list[str] | None = None,
    private_note: str | None = None,
    suspicion: dict[str, int] | None = None,
) -> str:
    """발언 제출. speech를 생략하면 패스. reveal: 함께 공개할 손패 카드 id(머더 미스터리, 최대 3장).
    private_note는 나만 보는 메모, suspicion은 {좌석 번호: 0~100}."""
    return _guard(
        lambda: app().seat_submit(
            seat,
            Answer.SPEECH,
            speech=speech,
            reveal=reveal,
            private_note=private_note,
            suspicion=suspicion,
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
