"""Harness bookkeeping that is not part of the game log: finished agent runs, render cursors."""

from __future__ import annotations

import sqlite3

_DRIVER_NUDGES_KEY = "driver_nudges"


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

    # --- driver nudges (Stop hook) --------------------------------------------------------
    def driver_nudges(self) -> int:
        row = self._conn.execute(
            "SELECT value FROM settings WHERE key = ?", (_DRIVER_NUDGES_KEY,)
        ).fetchone()
        return 0 if row is None else int(row[0])

    def set_driver_nudges(self, count: int) -> None:
        self._conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (_DRIVER_NUDGES_KEY, str(count)),
        )

    # --- live runs ----------------------------------------------------------------------
    def agent_started(
        self,
        agent_id: str,
        agent_type: str,
        at: float,
        *,
        seat: int | None = None,
        batch: str | None = None,
    ) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO agent_live (agent_id, agent_type, started_at, seat, batch) "
            "VALUES (?, ?, ?, ?, ?)",
            (agent_id, agent_type, at, seat, batch),
        )

    def run_binding(self, agent_id: str) -> tuple[int | None, str | None] | None:
        """(seat, batch) an alias run was bound to when it started; None for an unknown run."""
        row = self._conn.execute(
            "SELECT seat, batch FROM agent_live WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        if row is None:
            return None
        return (None if row[0] is None else int(row[0]), None if row[1] is None else str(row[1]))

    def running_types(self, now: float, stale_after: float) -> set[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT agent_type FROM agent_live WHERE finished_at IS NULL "
            "AND started_at > ?",
            (now - stale_after,),
        )
        return {str(r[0]) for r in rows}

    def agent_stopped(self, agent_id: str, at: float) -> None:
        self._conn.execute(
            "UPDATE agent_live SET finished_at = ? WHERE agent_id = ? AND finished_at IS NULL",
            (at, agent_id),
        )

    def running(self, agent_type: str, now: float, stale_after: float) -> int:
        """Runs of this agent type that started and have not stopped. Runs older than
        `stale_after` seconds are presumed dead (e.g. the host was closed mid-run)."""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM agent_live WHERE agent_type = ? AND finished_at IS NULL "
            "AND started_at > ?",
            (agent_type, now - stale_after),
        ).fetchone()
        return int(row[0])

    # --- covert aliases -----------------------------------------------------------------
    def aliases(self, game_id: str) -> list[tuple[str, int, str, bool]]:
        rows = self._conn.execute(
            "SELECT alias, seat, batch, done FROM aliases WHERE game_id = ? ORDER BY alias",
            (game_id,),
        )
        return [(str(a), int(s), str(b), bool(d)) for a, s, b, d in rows]

    def replace_aliases(self, game_id: str, batch: str, mapping: dict[str, int]) -> None:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._conn.execute("DELETE FROM aliases WHERE game_id = ?", (game_id,))
            self._conn.executemany(
                "INSERT INTO aliases (game_id, alias, seat, batch) VALUES (?, ?, ?, ?)",
                [(game_id, alias, seat, batch) for alias, seat in mapping.items()],
            )
            self._conn.execute("COMMIT")
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise

    def mark_alias_done(self, game_id: str, alias: str, batch: str) -> None:
        """Only within the batch the run was woken for: a late run from an earlier night step
        must not use up the alias's turn in the current one."""
        self._conn.execute(
            "UPDATE aliases SET done = 1 WHERE game_id = ? AND alias = ? AND batch = ?",
            (game_id, alias, batch),
        )
