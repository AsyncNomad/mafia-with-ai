"""SQLite state shared by the spike MCP server, the hooks and the contract report."""

from __future__ import annotations

import re
import secrets
import sqlite3
import time
from collections.abc import Iterable
from pathlib import Path

from mafia import paths
from mafia.harness.ports import HumanInputKind, HumanInputOutcome

_SCHEMA = """
CREATE TABLE IF NOT EXISTS awaiting (
    agent_type TEXT PRIMARY KEY,
    status     TEXT NOT NULL CHECK (status IN ('awaiting', 'submitted')),
    secret     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deliveries (
    id   INTEGER PRIMARY KEY,
    seat INTEGER NOT NULL,
    at   REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS submissions (
    id   INTEGER PRIMARY KEY,
    seat INTEGER NOT NULL,
    text TEXT NOT NULL,
    at   REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS human_inputs (
    id   INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    at   REAL NOT NULL
);
"""


def default_db_path() -> Path:
    return paths.runs_dir() / "spike" / "harness.db"


class SpikeState:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, timeout=5.0, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)

    @classmethod
    def open_default(cls) -> SpikeState:
        return cls(default_db_path())

    # --- HarnessState -------------------------------------------------------------------
    def is_awaiting(self, agent_type: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM awaiting WHERE agent_type = ? AND status = 'awaiting'", (agent_type,)
        ).fetchone()
        return row is not None

    def handle_human_input(self, text: str) -> HumanInputOutcome:
        self._conn.execute("INSERT INTO human_inputs (text, at) VALUES (?, ?)", (text, time.time()))
        return HumanInputOutcome(HumanInputKind.PASSTHROUGH)

    def driver_must_continue(self) -> bool:
        return False  # the spike has no game loop to keep going

    def spend_driver_nudge(self) -> bool:
        return False

    def seat_of(self, agent_type: str) -> int | None:
        match = re.match(r"^seat-(\d+)$", agent_type)
        return int(match.group(1)) if match else None

    def needs_submission(self, agent_type: str) -> bool:
        return self.is_awaiting(agent_type)

    def record_agent_started(self, agent_id: str, agent_type: str) -> None:
        return None

    def record_agent_finished(self, agent_id: str, agent_type: str) -> None:
        return None

    # --- spike engine -------------------------------------------------------------------
    def arm(self, agent_types: Iterable[str]) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM awaiting")
            for agent_type in agent_types:
                self._conn.execute(
                    "INSERT INTO awaiting (agent_type, status, secret) VALUES (?, 'awaiting', ?)",
                    (agent_type, f"SECRET-{agent_type}-{secrets.token_hex(4)}"),
                )

    def secret_for(self, agent_type: str) -> str | None:
        row = self._conn.execute(
            "SELECT secret FROM awaiting WHERE agent_type = ?", (agent_type,)
        ).fetchone()
        return None if row is None else str(row[0])

    def record_delivery(self, seat: int) -> None:
        self._conn.execute("INSERT INTO deliveries (seat, at) VALUES (?, ?)", (seat, time.time()))

    def record_submission(self, seat: int, text: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO submissions (seat, text, at) VALUES (?, ?, ?)",
                (seat, text, time.time()),
            )
            self._conn.execute(
                "UPDATE awaiting SET status = 'submitted' WHERE agent_type = ?", (f"seat-{seat}",)
            )

    def rows(self, table: str) -> list[tuple[object, ...]]:
        if table not in {"awaiting", "deliveries", "submissions", "human_inputs"}:
            raise ValueError(table)
        return list(self._conn.execute(f"SELECT * FROM {table} ORDER BY rowid"))

    def reset(self) -> None:
        with self._conn:
            for table in ("awaiting", "deliveries", "submissions", "human_inputs"):
                self._conn.execute(f"DELETE FROM {table}")
