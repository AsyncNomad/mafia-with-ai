"""Connection setup and forward-only schema migrations."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path

# Append-only. Never edit a released migration; add a new one.
MIGRATIONS: Sequence[str] = (
    # 1: games and their event logs
    """
    CREATE TABLE games (
        game_id    TEXT PRIMARY KEY,
        mode       TEXT NOT NULL,
        config     TEXT NOT NULL,
        created_at REAL NOT NULL,
        last_seq   INTEGER NOT NULL DEFAULT 0,
        ended      INTEGER NOT NULL DEFAULT 0 CHECK (ended IN (0, 1))
    );
    CREATE TABLE events (
        game_id    TEXT NOT NULL REFERENCES games (game_id),
        seq        INTEGER NOT NULL,
        at         REAL NOT NULL,
        kind       TEXT NOT NULL,
        visibility TEXT NOT NULL,
        data       TEXT NOT NULL,
        PRIMARY KEY (game_id, seq)
    );
    CREATE TABLE settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
    # 2: harness bookkeeping (subagent runs per request, per-viewer render cursors)
    """
    CREATE TABLE agent_runs (
        game_id     TEXT NOT NULL REFERENCES games (game_id),
        request_id  TEXT NOT NULL,
        agent_type  TEXT NOT NULL,
        finished_at REAL NOT NULL,
        PRIMARY KEY (game_id, request_id)
    );
    CREATE TABLE cursors (
        game_id  TEXT NOT NULL REFERENCES games (game_id),
        viewer   TEXT NOT NULL,
        last_seq INTEGER NOT NULL,
        PRIMARY KEY (game_id, viewer)
    );
    """,
    # 3: murder-mystery scenario library and preparation jobs
    """
    CREATE TABLE scenarios (
        scenario_id TEXT PRIMARY KEY,
        status      TEXT NOT NULL CHECK (status IN ('verified', 'rejected', 'played')),
        created_at  REAL NOT NULL,
        title       TEXT NOT NULL,
        tone        TEXT NOT NULL,
        brief       TEXT NOT NULL,
        scenario    TEXT,
        report      TEXT NOT NULL
    );
    CREATE TABLE prep_jobs (
        job_id     TEXT PRIMARY KEY,
        active     INTEGER NOT NULL CHECK (active IN (0, 1)),
        state      TEXT NOT NULL,
        updated_at REAL NOT NULL
    );
    """,
)


class SchemaTooNew(RuntimeError):
    """The database was written by a newer version of this program."""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10.0, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA foreign_keys=ON")
    migrate(conn)
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def migrate(conn: sqlite3.Connection) -> int:
    """Apply pending migrations atomically. Safe to call concurrently from several processes."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        current = schema_version(conn)
        if current > len(MIGRATIONS):
            raise SchemaTooNew(
                f"database schema v{current} is newer than this program (v{len(MIGRATIONS)})"
            )
        for version in range(current + 1, len(MIGRATIONS) + 1):
            for statement in _statements(MIGRATIONS[version - 1]):
                conn.execute(statement)
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return len(MIGRATIONS)


def _statements(script: str) -> list[str]:
    # executescript() would commit our transaction; split instead. Migrations contain no
    # semicolons inside literals.
    return [s.strip() for s in script.split(";") if s.strip()]
