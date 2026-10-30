"""Scenario library and murder-mystery preparation jobs."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ScenarioRecord:
    scenario_id: str
    status: str
    created_at: float
    title: str
    tone: str
    brief: dict[str, Any]
    scenario: dict[str, Any] | None
    report: dict[str, Any]


class ScenarioStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # --- library ------------------------------------------------------------------------
    def save(self, record: ScenarioRecord) -> None:
        self._conn.execute(
            "INSERT INTO scenarios (scenario_id, status, created_at, title, tone, brief, scenario, report) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.scenario_id,
                record.status,
                record.created_at,
                record.title,
                record.tone,
                json.dumps(record.brief, ensure_ascii=False),
                None
                if record.scenario is None
                else json.dumps(record.scenario, ensure_ascii=False),
                json.dumps(record.report, ensure_ascii=False),
            ),
        )

    def get(self, scenario_id: str) -> ScenarioRecord | None:
        row = self._conn.execute(
            "SELECT scenario_id, status, created_at, title, tone, brief, scenario, report "
            "FROM scenarios WHERE scenario_id = ?",
            (scenario_id,),
        ).fetchone()
        return None if row is None else _record(row)

    def list(self) -> list[ScenarioRecord]:
        rows = self._conn.execute(
            "SELECT scenario_id, status, created_at, title, tone, brief, scenario, report "
            "FROM scenarios ORDER BY created_at"
        )
        return [_record(r) for r in rows]

    def next_unplayed(self) -> ScenarioRecord | None:
        row = self._conn.execute(
            "SELECT scenario_id, status, created_at, title, tone, brief, scenario, report "
            "FROM scenarios WHERE status = 'verified' ORDER BY created_at LIMIT 1"
        ).fetchone()
        return None if row is None else _record(row)

    def mark_played(self, scenario_id: str) -> None:
        self._conn.execute(
            "UPDATE scenarios SET status = 'played' WHERE scenario_id = ?", (scenario_id,)
        )

    # --- preparation jobs ---------------------------------------------------------------
    def start_job(self, job_id: str, state: str, at: float) -> None:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._conn.execute("UPDATE prep_jobs SET active = 0 WHERE active = 1")
            self._conn.execute(
                "INSERT INTO prep_jobs (job_id, active, state, updated_at) VALUES (?, 1, ?, ?)",
                (job_id, state, at),
            )
            self._conn.execute("COMMIT")
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise

    def active_job(self) -> tuple[str, str] | None:
        row = self._conn.execute(
            "SELECT job_id, state FROM prep_jobs WHERE active = 1 LIMIT 1"
        ).fetchone()
        return None if row is None else (str(row[0]), str(row[1]))

    def update_job[T](
        self,
        job_id: str,
        change: Callable[[str], tuple[str, T]],
        at: float,
        *,
        finish: bool = False,
    ) -> T:
        """Atomically read the job state, apply `change`, write the result back.

        `change` returns (new state, value to hand back). Parallel agents (e.g. solvers
        claiming work) serialise here.
        """
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._conn.execute(
                "SELECT state FROM prep_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise LookupError(job_id)
            new_state, value = change(str(row[0]))
            self._conn.execute(
                "UPDATE prep_jobs SET state = ?, updated_at = ?, active = ? WHERE job_id = ?",
                (new_state, at, 0 if finish else 1, job_id),
            )
            self._conn.execute("COMMIT")
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        return value

    def finish_job(self, job_id: str) -> None:
        self._conn.execute("UPDATE prep_jobs SET active = 0 WHERE job_id = ?", (job_id,))


def _record(row: tuple[Any, ...]) -> ScenarioRecord:
    return ScenarioRecord(
        scenario_id=row[0],
        status=row[1],
        created_at=row[2],
        title=row[3],
        tone=row[4],
        brief=json.loads(row[5]),
        scenario=None if row[6] is None else json.loads(row[6]),
        report=json.loads(row[7]),
    )
