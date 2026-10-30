"""Spike MCP server. Records what each tool actually received so the report can compare it
with what the hooks saw (e.g. whether a seat's forged `seat` argument was overwritten)."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from mafia.harness.tools import MCP_SERVER_NAME
from mafia.hosts.claude_code.spike.state import SpikeState

server = MCPServer(MCP_SERVER_NAME, instructions="mafia-with-ai M0 harness spike")


@server.tool()
def ping() -> str:
    """Public liveness check."""
    return "pong"


@server.tool()
def spike_arm(agent_types: list[str]) -> str:
    """Public. Mark these agents as awaited by the game (spike stand-in for the engine)."""
    SpikeState.open_default().arm(agent_types)
    return f"armed: {', '.join(agent_types)}"


@server.tool()
def my_turn(seat: int) -> str:
    """Seat only. Returns this seat's private context."""
    state = SpikeState.open_default()
    state.record_delivery(seat)
    secret = state.secret_for(f"seat-{seat}") or "(이 좌석은 대기 중이 아님)"
    return f"당신은 {seat}번 좌석입니다. 비밀 코드: {secret}. submit_speech로 발언을 제출하세요."


@server.tool()
def submit_speech(seat: int, text: str) -> str:
    """Seat only. Submit this seat's speech."""
    SpikeState.open_default().record_submission(seat, text)
    return "접수되었습니다."


def main() -> None:
    server.run()


if __name__ == "__main__":
    main()
