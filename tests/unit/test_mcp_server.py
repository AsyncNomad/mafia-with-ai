"""The MCP SDK runs synchronous tools on a thread pool. Every tool must work from any thread."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from mafia.mcp_server import server


@pytest.fixture(autouse=True)
def isolated_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAFIA_RUNS_DIR", str(tmp_path))
    server.reset_for_tests()


def test_tools_work_while_the_creating_thread_is_still_alive() -> None:
    # The SDK's worker threads are long-lived; a connection made in one of them must not be
    # reused from another. Keep the creating thread alive so thread ids cannot be recycled.
    created: list[str] = []
    ready, release = threading.Event(), threading.Event()

    def owner() -> None:
        created.append(server.new_game(human_name="Jane"))
        ready.set()
        release.wait()

    worker = threading.Thread(target=owner)
    worker.start()
    ready.wait()
    try:
        assert "<display>" in created[0]
        assert "일차" in server.status()  # called from another live thread
        assert "<next>" in server.advance()
    finally:
        release.set()
        worker.join()


def test_unexpected_errors_are_reported_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> object:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(server, "app", boom)
    message = server.status()
    assert "엔진 오류" in message and "disk on fire" in message


def test_every_tool_body_is_guarded() -> None:
    import inspect
    import re

    source = inspect.getsource(server)
    bodies = re.findall(r"@server\.tool\(\)\ndef (\w+)\((?:.|\n)*?\n    return (.*)", source)
    assert len(bodies) >= 16
    for name, body in bodies:
        assert body.startswith("_guard("), f"{name} is not guarded: {body}"
