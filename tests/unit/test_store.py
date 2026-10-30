from __future__ import annotations

import random
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from mafia.engine.audit import audit
from mafia.engine.core_events import ContextDelivered, SeatInfo
from mafia.engine.engine import Step
from mafia.engine.requests import Action, ActionRejected
from mafia.engine.runtime import FixedClock
from mafia.service import GameService, Loaded
from mafia.store.db import MIGRATIONS, SchemaTooNew, connect, migrate, schema_version
from mafia.store.events import ConcurrencyError, EventStore, GameNotFound
from tests.sim.test_original_sim import fake_action
from tests.table import NAMES

SEATS = [SeatInfo(seat=i + 1, name=n) for i, n in enumerate(NAMES)]


def service(path: Path) -> GameService:
    return GameService(EventStore(connect(path)), FixedClock())


class TestMigrations:
    def test_fresh_database_is_current_and_idempotent(self, tmp_path: Path) -> None:
        conn = connect(tmp_path / "g.db")
        assert schema_version(conn) == len(MIGRATIONS)
        migrate(conn)
        assert schema_version(conn) == len(MIGRATIONS)

    def test_newer_schema_refused(self, tmp_path: Path) -> None:
        conn = connect(tmp_path / "g.db")
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (len(MIGRATIONS) + 1,))
        with pytest.raises(SchemaTooNew):
            migrate(conn)

    def test_failed_migration_rolls_back(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        conn = sqlite3.connect(tmp_path / "g.db", isolation_level=None)
        monkeypatch.setattr("mafia.store.db.MIGRATIONS", ("CREATE TABLE a (x INT); BROKEN SQL",))
        with pytest.raises(sqlite3.OperationalError):
            migrate(conn)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "a" not in tables
        assert schema_version(conn) == 0


class TestEventStore:
    def test_round_trip_and_tail(self, tmp_path: Path) -> None:
        svc = service(tmp_path / "g.db")
        game_id = svc.new_game(mode="original", seats=SEATS, seed="s")
        log = svc.load(game_id).log
        store = svc.store
        assert tuple(store.load(game_id)) == log
        assert tuple(store.load(game_id, after_seq=3)) == log[3:]
        assert store.game(game_id).last_seq == log[-1].seq
        assert store.active_game() == game_id

    def test_stale_append_rejected(self, tmp_path: Path) -> None:
        svc = service(tmp_path / "g.db")
        game_id = svc.new_game(mode="original", seats=SEATS, seed="s")
        log = svc.load(game_id).log
        with pytest.raises(ConcurrencyError):
            svc.store.append(game_id, log[-1].seq - 1, [log[-1]], ended=False)

    def test_gapped_append_rejected(self, tmp_path: Path) -> None:
        svc = service(tmp_path / "g.db")
        game_id = svc.new_game(mode="original", seats=SEATS, seed="s")
        last = svc.load(game_id).log[-1]
        gapped = last.model_copy(update={"seq": last.seq + 2})
        with pytest.raises(ValueError):
            svc.store.append(game_id, last.seq, [gapped], ended=False)

    def test_duplicate_game_and_missing_game(self, tmp_path: Path) -> None:
        svc = service(tmp_path / "g.db")
        game_id = svc.new_game(mode="original", seats=SEATS, seed="s", game_id="fixed")
        with pytest.raises(ConcurrencyError):
            svc.new_game(mode="original", seats=SEATS, seed="s", game_id=game_id)
        with pytest.raises(GameNotFound):
            svc.store.game("nope")

    def test_unknown_mode(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            service(tmp_path / "g.db").new_game(mode="poker", seats=SEATS)


class TestService:
    def test_two_processes_see_the_same_game(self, tmp_path: Path) -> None:
        a = service(tmp_path / "g.db")
        b = service(tmp_path / "g.db")
        game_id = a.new_game(mode="original", seats=SEATS, seed="s")
        request = b.open_requests(game_id)[0]
        b.submit(game_id, request.seat, request.request_id, Action(speech="안녕하세요"))
        assert a.load(game_id).state == b.load(game_id).state

    def test_race_between_load_and_append_is_recomputed(self, tmp_path: Path) -> None:
        a = service(tmp_path / "g.db")
        b = service(tmp_path / "g.db")
        game_id = a.new_game(mode="original", seats=SEATS, seed="s")
        calls = 0

        def command(g: Loaded) -> Step[Any]:
            nonlocal calls
            calls += 1
            if calls == 1:  # another process appends after `a` loaded, before it appends
                first = b.open_requests(game_id)[0]
                b.submit(game_id, first.seat, first.request_id, Action(speech="b가 먼저"))
            step, _ = g.engine.context_for(g.state, g.log, 1)
            return step

        a._commit(game_id, command)  # pyright: ignore[reportPrivateUsage]
        assert calls == 2
        log_a, log_b = a.load(game_id).log, b.load(game_id).log
        assert log_a == log_b
        assert log_a[-1].kind == ContextDelivered.KIND
        assert audit(a.load(game_id).engine, log_a) == []

    def test_persistent_conflicts_give_up(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        svc = service(tmp_path / "g.db")
        game_id = svc.new_game(mode="original", seats=SEATS, seed="s")

        def always_conflict(*_args: object, **_kwargs: object) -> None:
            raise ConcurrencyError("busy")

        monkeypatch.setattr(svc.store, "append", always_conflict)
        with pytest.raises(ConcurrencyError):
            svc.context(game_id, 1)

    def test_answering_a_request_closed_elsewhere_is_rejected(self, tmp_path: Path) -> None:
        a = service(tmp_path / "g.db")
        b = service(tmp_path / "g.db")
        game_id = a.new_game(mode="original", seats=SEATS, seed="s")
        request = a.open_requests(game_id)[0]
        b.submit(game_id, request.seat, request.request_id, Action(speech="b"))
        with pytest.raises(ActionRejected):
            a.submit(game_id, request.seat, request.request_id, Action(speech="a"))

    def test_full_game_through_the_store(self, tmp_path: Path) -> None:
        svc = service(tmp_path / "g.db")
        game_id = svc.new_game(mode="original", seats=SEATS, seed="full")
        rng = random.Random(1)
        for _ in range(2_000):
            loaded = svc.load(game_id)
            if loaded.state.core.ended:
                break
            for request in svc.open_requests(game_id):
                svc.context(game_id, request.seat)
                svc.submit(
                    game_id, request.seat, request.request_id, Action(**fake_action(request, rng))
                )
        final = svc.load(game_id)
        assert final.state.core.ended
        assert svc.store.game(game_id).ended
        fresh = service(tmp_path / "g.db").load(game_id)
        assert fresh.state == final.state
        assert audit(fresh.engine, fresh.log) == []
