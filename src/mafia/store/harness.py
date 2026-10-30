"""Harness bookkeeping that is not part of the game log: finished agent runs, render cursors."""

from __future__ import annotations

import sqlite3


class HarnessStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def record_agent_finished(
        self, game_id: str, request_id: str, agent_type: str, at: float
    ) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO agent_runs (game_id, request_id, agent_type, finished_at) "
            "VALUES (?, ?, ?, ?)",
            (game_id, request_id, agent_type, at),
        )

    def finished_requests(self, game_id: str) -> set[str]:
        rows = self._conn.execute("SELECT request_id FROM agent_runs WHERE game_id = ?", (game_id,))
        return {str(r[0]) for r in rows}

    def cursor(self, game_id: str, viewer: str) -> int:
        row = self._conn.execute(
            "SELECT last_seq FROM cursors WHERE game_id = ? AND viewer = ?", (game_id, viewer)
        ).fetchone()
        return 0 if row is None else int(row[0])

    def set_cursor(self, game_id: str, viewer: str, last_seq: int) -> None:
        self._conn.execute(
            "INSERT INTO cursors (game_id, viewer, last_seq) VALUES (?, ?, ?) "
            "ON CONFLICT (game_id, viewer) DO UPDATE SET last_seq = excluded.last_seq",
            (game_id, viewer, last_seq),
        )
