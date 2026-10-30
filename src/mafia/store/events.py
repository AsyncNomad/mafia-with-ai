"""Append-only event store with optimistic concurrency."""

from __future__ import annotations

import itertools
import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from mafia.engine.events import Event
from mafia.engine.visibility import Visibility

ACTIVE_GAME_KEY = "active_game"


class ConcurrencyError(RuntimeError):
    """Another process appended to the game first. Reload and retry."""


class GameNotFound(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class GameRecord:
    game_id: str
    mode: str
    config: dict[str, Any]
    created_at: float
    last_seq: int
    ended: bool


class EventStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # --- games --------------------------------------------------------------------------
    def create_game(
        self, game_id: str, mode: str, config: dict[str, Any], created_at: float
    ) -> None:
        try:
            self._conn.execute(
                "INSERT INTO games (game_id, mode, config, created_at) VALUES (?, ?, ?, ?)",
                (game_id, mode, json.dumps(config, ensure_ascii=False, sort_keys=True), created_at),
            )
        except sqlite3.IntegrityError as exc:
            raise ConcurrencyError(f"game {game_id} already exists") from exc

    def game(self, game_id: str) -> GameRecord:
        row = self._conn.execute(
            "SELECT game_id, mode, config, created_at, last_seq, ended FROM games WHERE game_id = ?",
            (game_id,),
        ).fetchone()
        if row is None:
            raise GameNotFound(game_id)
        return GameRecord(
            game_id=row[0],
            mode=row[1],
            config=json.loads(row[2]),
            created_at=row[3],
            last_seq=row[4],
            ended=bool(row[5]),
        )

    def list_games(self) -> list[GameRecord]:
        ids = [r[0] for r in self._conn.execute("SELECT game_id FROM games ORDER BY created_at")]
        return [self.game(i) for i in ids]

    # --- events -------------------------------------------------------------------------
    def append(
        self, game_id: str, expected_last_seq: int, events: Sequence[Event], *, ended: bool
    ) -> None:
        """Atomically append `events` if nobody appended since `expected_last_seq`."""
        if not events:
            return
        if events[0].seq != expected_last_seq + 1 or any(
            b.seq != a.seq + 1 for a, b in itertools.pairwise(events)
        ):
            raise ValueError("events must continue the log without gaps")
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._conn.execute(
                "SELECT last_seq FROM games WHERE game_id = ?", (game_id,)
            ).fetchone()
            if row is None:
                raise GameNotFound(game_id)
            if row[0] != expected_last_seq:
                raise ConcurrencyError(
                    f"game {game_id} is at seq {row[0]}, expected {expected_last_seq}"
                )
            self._conn.executemany(
                "INSERT INTO events (game_id, seq, at, kind, visibility, data) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        game_id,
                        e.seq,
                        e.at,
                        e.kind,
                        e.visibility.model_dump_json(),
                        json.dumps(e.data, ensure_ascii=False),
                    )
                    for e in events
                ],
            )
            self._conn.execute(
                "UPDATE games SET last_seq = ?, ended = ? WHERE game_id = ?",
                (events[-1].seq, int(ended), game_id),
            )
            self._conn.execute("COMMIT")
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise

    def load(self, game_id: str, after_seq: int = 0) -> list[Event]:
        rows = self._conn.execute(
            "SELECT seq, at, kind, visibility, data FROM events "
            "WHERE game_id = ? AND seq > ? ORDER BY seq",
            (game_id, after_seq),
        )
        return [
            Event(
                seq=seq,
                at=at,
                kind=kind,
                visibility=Visibility.model_validate_json(visibility),
                data=json.loads(data),
            )
            for seq, at, kind, visibility, data in rows
        ]

    # --- settings -----------------------------------------------------------------------
    def set_active_game(self, game_id: str) -> None:
        self._conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (ACTIVE_GAME_KEY, game_id),
        )

    def active_game(self) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM settings WHERE key = ?", (ACTIVE_GAME_KEY,)
        ).fetchone()
        return None if row is None else str(row[0])
