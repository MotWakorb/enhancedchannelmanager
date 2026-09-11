"""Real SQLite/task contracts for mtcs1.2's semantics-preserving reset."""
from math import ceil
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from models import StreamStats
from tasks import struck_stream_cleanup as cleanup


@pytest.fixture
def cleanup_db(tmp_path, monkeypatch):
    # A private file also permits a genuinely independent writer connection.
    engine = create_engine(f"sqlite:///{tmp_path / 'strikes.db'}", connect_args={"timeout": 0.05})
    StreamStats.__table__.create(engine)
    state = SimpleNamespace(engine=engine, sessions=[], sql=[], transactions=[], phase="fixture")

    @event.listens_for(engine, "before_cursor_execute")
    def record_sql(conn, cursor, statement, parameters, context, executemany):
        state.sql.append((state.phase, statement, parameters))

    @event.listens_for(engine, "commit")
    def record_commit(conn):
        state.transactions.append((state.phase, "commit"))

    @event.listens_for(engine, "rollback")
    def record_rollback(conn):
        state.transactions.append((state.phase, "rollback"))

    def session_factory():
        state.phase = "scan" if not state.sessions else "reset"
        session = Session(engine)
        # Wrap close without replacing real SQLAlchemy transaction cleanup.
        session.close = Mock(wraps=session.close)
        state.sessions.append(session)
        return session

    monkeypatch.setattr(cleanup, "get_session", session_factory)
    monkeypatch.setattr(cleanup, "get_settings", lambda: SimpleNamespace(strike_threshold=3))
    state.client = AsyncMock()
    monkeypatch.setattr(cleanup, "get_client", lambda: state.client)
    yield state
    for session in state.sessions:
        session.close()
    engine.dispose()


def seed(db, ids):
    with Session(db.engine) as session:
        session.add_all([
            StreamStats(stream_id=sid, stream_name=f"stream-{sid}",
                        probe_status="failed", consecutive_failures=3, bitrate=1700000)
            for sid in ids
        ])
        session.add(StreamStats(stream_id=99999, stream_name="unrelated", consecutive_failures=1))
        session.commit()


def snapshot(db):
    db.phase = "observe"
    with db.engine.connect() as conn:
        return {row.stream_id: dict(row._mapping) for row in conn.execute(select(StreamStats.__table__))}


def assert_only_reset(before, after, reset_ids):
    expected = {sid: dict(row) for sid, row in before.items()}
    for sid in reset_ids:
        if sid in expected:
            expected[sid]["consecutive_failures"] = 0
    assert after == expected


def assert_sql_budget(db, count):
    scan = [sql for phase, sql, _ in db.sql if phase == "scan" and sql.startswith("SELECT")]
    reset = [(sql, params) for phase, sql, params in db.sql if phase == "reset"]
    assert len(scan) == 1
    assert not [sql for sql, _ in reset if sql.startswith("SELECT")]
    updates = [(sql, params) for sql, params in reset if sql.startswith("UPDATE")]
    assert len(updates) <= ceil(count / 500)
    assert bool(updates) == bool(count)
    assert all(len(params) <= 501 for _, params in updates)  # IDs plus SET value
    assert db.transactions.count(("reset", "commit")) == bool(count)
    assert len(db.sessions) == (2 if count else 1)
    assert len({id(session) for session in db.sessions}) == len(db.sessions)
    for session in db.sessions:
        session.close.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 499, 500, 501])
@pytest.mark.parametrize("missing", [False, True])
async def test_reset_sql_budget_and_all_column_equivalence(cleanup_db, count, missing):
    db = cleanup_db
    ids = list(range(1, count + 1))
    seed(db, ids)
    before = snapshot(db)

    async def channels(**kwargs):
        if missing:
            db.phase = "fixture"
            with Session(db.engine) as session:
                session.query(StreamStats).filter(StreamStats.stream_id.in_(ids[::10])).delete()
                session.commit()
            for sid in ids[::10]:
                before.pop(sid)
        return {"results": [{"id": 1, "streams": [99999, *ids, 88888]}], "count": 1}

    db.client.get_channels.side_effect = channels
    result = await cleanup.StruckStreamCleanupTask().execute()
    assert result.success
    assert result.total_items == count
    assert result.success_count == count
    assert result.failed_count == 0
    if count:
        db.client.update_channel.assert_awaited_once_with(1, {"streams": [99999, 88888]})
        assert result.details == {"struck_stream_ids": ids[:50], "removed_from_channels": count, "threshold": 3}
    else:
        db.client.get_channels.assert_not_awaited()
        db.client.update_channel.assert_not_awaited()
    assert_sql_budget(db, count)
    assert_only_reset(before, snapshot(db), ids)


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario,removed,failed,reset", [
    ("success", 3, 0, True), ("partial", 2, 1, True),
    ("all_failed", 0, 2, False), ("cancel_after", 2, 0, True),
    ("cancel_before", 0, 0, False), ("unassigned", 0, 0, False),
])
async def test_captured_population_order_and_task_reporting(cleanup_db, scenario, removed, failed, reset):
    db = cleanup_db
    seed(db, [10, 20, 30])  # 20 is shared; 30 has no channel assignment.
    before = snapshot(db)
    task = cleanup.StruckStreamCleanupTask()
    channels = [{"id": 1, "name": "one", "streams": [90, 10, 80, 20, 90]},
                {"id": 2, "name": "two", "streams": [20, 70]}]
    if scenario == "unassigned":
        channels = [{"id": 1, "streams": [90, 80]}]

    async def get_channels(**kwargs):
        if scenario == "cancel_before":
            task._cancel_requested = True
        # A changed captured row must still reset; a newly struck row must not.
        db.phase = "fixture"
        with Session(db.engine) as session:
            session.query(StreamStats).filter_by(stream_id=30).update({"consecutive_failures": 1})
            session.query(StreamStats).filter_by(stream_id=99999).update({"consecutive_failures": 8})
            session.commit()
        before[30]["consecutive_failures"] = 1
        before[99999]["consecutive_failures"] = 8
        return {"results": channels, "count": len(channels)}

    async def update_channel(channel_id, payload):
        if scenario == "all_failed" or (scenario == "partial" and channel_id == 2):
            raise RuntimeError("offline failure")
        if scenario == "cancel_after":
            task._cancel_requested = True

    db.client.get_channels.side_effect = get_channels
    db.client.update_channel.side_effect = update_channel
    result = await task.execute()
    expected_calls = [] if scenario in {"cancel_before", "unassigned"} else [call(1, {"streams": [90, 80, 90]})]
    if scenario in {"success", "partial", "all_failed"}:
        expected_calls.append(call(2, {"streams": [70]}))
    assert db.client.update_channel.await_args_list == expected_calls
    assert result.success == (scenario in {"success", "partial", "unassigned"})
    assert result.error == ("CANCELLED" if scenario.startswith("cancel") else None)
    assert (result.total_items, result.success_count, result.failed_count) == (3, removed, failed)
    assert result.details["struck_stream_ids"] == [10, 20, 30]
    assert result.details["removed_from_channels"] == removed
    assert result.details["threshold"] == 3
    assert result.details.get("errors", []) == (
        ["Channel one: offline failure", "Channel two: offline failure"] if scenario == "all_failed"
        else ["Channel two: offline failure"] if scenario == "partial" else []
    )
    assert (task._progress.total, task._progress.success_count, task._progress.failed_count) == (3, removed, failed)
    assert task._progress.status == ("cancelled" if scenario.startswith("cancel") else "completed")
    assert len(db.sessions) == (2 if reset else 1)
    assert_only_reset(before, snapshot(db), [10, 20, 30] if reset else [])
    with Session(db.engine) as session:
        eligible = {row.stream_id for row in session.query(StreamStats).filter(StreamStats.consecutive_failures >= 3)}
    assert eligible == ({99999} if reset else {10, 20, 99999})


@pytest.mark.asyncio
async def test_duplicate_captured_ids_use_real_reset_sql(cleanup_db, monkeypatch):
    db = cleanup_db
    seed(db, [10, 20])
    before = snapshot(db)
    # The unique DB column prevents natural scan duplicates. Inject only the
    # scan result; the production reset still executes against real SQLite.
    from sqlalchemy.orm import Query
    original_all = Query.all

    def duplicate_scan(query):
        rows = original_all(query)
        return rows * 251

    monkeypatch.setattr(Query, "all", duplicate_scan)
    db.client.get_channels.return_value = {"results": [{"id": 1, "streams": [20, 10, 20, 90]}], "count": 1}
    result = await cleanup.StruckStreamCleanupTask().execute()
    assert result.success
    assert (result.total_items, result.success_count) == (502, 3)
    assert result.details["struck_stream_ids"] == [10, 20] * 25
    db.client.update_channel.assert_awaited_once_with(1, {"streams": [90]})
    assert_sql_budget(db, 502)
    assert_only_reset(before, snapshot(db), [10, 20])


@pytest.mark.asyncio
async def test_late_chunk_failure_rolls_back_all_reset_rows(cleanup_db):
    db = cleanup_db
    seed(db, range(1, 502))
    before = snapshot(db)
    with db.engine.begin() as conn:
        conn.exec_driver_sql("CREATE TRIGGER fail_late BEFORE UPDATE ON stream_stats "
                             "WHEN OLD.stream_id = 501 BEGIN SELECT RAISE(ABORT, 'late chunk'); END")
    db.client.get_channels.return_value = {"results": [{"id": 1, "streams": [1]}], "count": 1}
    result = await cleanup.StruckStreamCleanupTask().execute()
    assert not result.success
    assert "late chunk" in result.error
    assert snapshot(db) == before
    assert db.transactions.count(("reset", "commit")) == 0
    assert db.transactions.count(("reset", "rollback")) == 1
    updates = [sql for phase, sql, _ in db.sql if phase == "reset" and sql.startswith("UPDATE")]
    assert len(updates) == 2
    db.client.update_channel.assert_awaited_once_with(1, {"streams": []})


@pytest.mark.asyncio
async def test_file_backed_writer_contention_then_recovery(cleanup_db):
    db = cleanup_db
    seed(db, range(1, 502))
    before = snapshot(db)
    db.client.get_channels.return_value = {"results": [{"id": 1, "streams": [1]}], "count": 1}
    with db.engine.connect() as writer:
        async def acquire_write_lock(*args):
            writer.exec_driver_sql("BEGIN IMMEDIATE")
            writer.exec_driver_sql("UPDATE stream_stats SET consecutive_failures=7 WHERE stream_id=99999")

        db.client.update_channel.side_effect = acquire_write_lock
        result = await cleanup.StruckStreamCleanupTask().execute()
        assert not result.success
        assert "database is locked" in result.error
        assert db.transactions.count(("reset", "commit")) == 0
        assert db.transactions.count(("reset", "rollback")) == 1
        assert snapshot(db) == before  # no dirty read of competing transaction
        writer.rollback()
    assert snapshot(db) == before
    db.client.update_channel.side_effect = None
    result = await cleanup.StruckStreamCleanupTask().execute()
    assert result.success
    assert_only_reset(before, snapshot(db), range(1, 502))
