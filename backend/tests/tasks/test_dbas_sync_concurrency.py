"""Per-target sync concurrency (bead ``enhancedchannelmanager-7ipq2.3``, epic i39wu).

ADR-013 S6 mandates ONE ``task_id`` per ``SyncTarget`` so distinct targets run
concurrently while the task engine's ``ALREADY_RUNNING`` guard (keyed on
``task_id``) excludes a second run of the SAME target. v1 (bead 5gzg5)
deliberately shipped a single parameterized ``task_id="dbas_sync"`` — safe but
serializing: one slow/unreachable B starved every other target, and two
same-tick due schedules under the shared id silently swallowed the second
target's run (the engine groups due schedules by task_id, runs the FIRST
schedule's parameters, and advances next_run_at for ALL of them).

This suite covers the follow-up, red-first:

1. **Per-target task identity** — ``sync_task_id_for`` / ``make_sync_task_class``
   produce a bound subclass per target (``dbas_sync_<target_id>``); the base
   class is no longer statically registered.
2. **Registration lifecycle** — ``ensure_sync_target_task`` /
   ``remove_sync_target_task`` keep the registry + ``scheduled_tasks`` /
   ``task_schedules`` rows in step with SyncTarget CRUD, and
   ``register_sync_target_tasks`` (startup) registers every existing target,
   migrates legacy ``dbas_sync`` schedule rows to their per-target id, and
   prunes rows for deleted targets.
3. **Engine-level concurrency semantics** — concurrent runs of DIFFERENT
   targets proceed; a second run of the SAME target is refused non-silently
   (``ALREADY_RUNNING``).
4. **One-shot isolation under concurrency** (extends the 7ipq2.2 arming fix) —
   concurrent runs read only their own per-target instance state; disarm
   resets to the BOUND target id, never leaking parameters across targets OR
   runs. A foreign ``sync_target_id`` parameter on a bound task hard-fails
   without ever reaching ``run_sync``.
5. **Bounded concurrency** — a module-level semaphore caps simultaneous sync
   runs (``ECM_SYNC_MAX_CONCURRENT``, default 3); excess runs queue, they are
   not dropped.
6. **Metric attribution** — concurrent runs bump their own
   ``ecm_sync_runs_total{result}`` labels exactly once each.

All DB access goes through an in-memory SQLite engine wired into the
``database`` module (same harness as ``test_dbas_sync_task.py``).
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker

import database
import observability
from dbas.restore_contracts import RestoreOutcome, RestoreReport
from export_models import SyncTarget
from models import ScheduledTask, TaskSchedule
from task_registry import get_registry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def _wire_db(test_engine, monkeypatch):
    """Point database._SessionLocal at the in-memory test engine."""
    TestSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine, expire_on_commit=False
    )
    monkeypatch.setattr(database, "_SessionLocal", TestSessionLocal)
    return TestSessionLocal


@pytest.fixture
def _reset_metrics():
    observability.reset_for_tests()
    observability.install_metrics()
    yield
    observability.reset_for_tests()


@pytest.fixture
def _clean_registry():
    """Snapshot the global task registry and restore it after the test.

    Per-target sync task classes are registered into the GLOBAL registry;
    without this, one test's dynamic registrations leak into every later test
    in the session.
    """
    registry = get_registry()
    tasks_before = dict(registry._tasks)
    instances_before = dict(registry._instances)
    yield registry
    registry._tasks.clear()
    registry._tasks.update(tasks_before)
    registry._instances.clear()
    registry._instances.update(instances_before)


@pytest.fixture
def _fresh_semaphore():
    """Reset the module-level sync concurrency semaphore around the test."""
    from tasks import dbas_sync

    dbas_sync.reset_sync_concurrency_for_tests()
    yield
    dbas_sync.reset_sync_concurrency_for_tests()


def _make_target(session, name="dispatcharr-b", **overrides) -> SyncTarget:
    fields = dict(
        name=name,
        base_url="https://%s.example.com" % name,
        credentials="{}",
        enabled=True,
        credential_version=1,
        token_revoked_at=None,
        insecure=False,
    )
    fields.update(overrides)
    target = SyncTarget(**fields)
    session.add(target)
    session.commit()
    session.refresh(target)
    return target


def _success_report() -> RestoreReport:
    report = RestoreReport(is_dry_run=False)
    report.outcome = RestoreOutcome.SUCCESS
    return report


def _sync_counter_value(result_label: str) -> float:
    counter = observability.get_metric("sync_runs_total")
    return counter.labels(result=result_label)._value.get()


# ---------------------------------------------------------------------------
# 1. Per-target task identity
# ---------------------------------------------------------------------------


def test_sync_task_id_scheme():
    from tasks.dbas_sync import SYNC_TASK_ID_PREFIX, sync_task_id_for

    assert SYNC_TASK_ID_PREFIX == "dbas_sync_"
    assert sync_task_id_for(7) == "dbas_sync_7"


def test_make_sync_task_class_binds_target():
    from task_scheduler import ScheduleType
    from tasks.dbas_sync import DbasSyncTask, make_sync_task_class

    cls = make_sync_task_class(7, "replica-b")
    assert issubclass(cls, DbasSyncTask)
    assert cls.task_id == "dbas_sync_7"
    assert cls.bound_sync_target_id == 7
    assert "replica-b" in cls.task_name
    # Posture inherited from the base: opt-in, per-invocation config only.
    assert cls.default_enabled is False
    assert cls.persist_config is False

    inst = cls()
    # A bound instance arms its own target by construction — a schedule needs
    # no sync_target_id parameter at all.
    assert inst.sync_target_id == 7
    assert inst.schedule_config.schedule_type == ScheduleType.MANUAL


def test_base_class_is_not_statically_registered():
    """ADR-013 S6: one task_id per SyncTarget. The shared parameterized
    ``dbas_sync`` id would bypass per-target locking (two targets serialized,
    or a same-target run hidden from the guard under the legacy id), so the
    base class must no longer self-register."""
    import tasks  # noqa: F401 — triggers @register_task side effects

    registry = get_registry()
    assert not registry.is_registered("dbas_sync")


# ---------------------------------------------------------------------------
# 2. Registration lifecycle
# ---------------------------------------------------------------------------


def test_ensure_and_remove_sync_target_task(_wire_db, _clean_registry):
    from tasks.dbas_sync import (
        ensure_sync_target_task,
        remove_sync_target_task,
        sync_task_id_for,
    )

    session = _wire_db()
    target = _make_target(session, name="replica-b")
    target_id = target.id
    session.close()

    task_id = sync_task_id_for(target_id)
    ensure_sync_target_task(target_id, "replica-b")

    registry = get_registry()
    assert registry.is_registered(task_id)
    session = _wire_db()
    row = session.query(ScheduledTask).filter(ScheduledTask.task_id == task_id).first()
    assert row is not None
    assert "replica-b" in row.task_name
    session.close()

    # Rename flows through ensure (same id, refreshed display name).
    ensure_sync_target_task(target_id, "replica-b-renamed")
    session = _wire_db()
    row = session.query(ScheduledTask).filter(ScheduledTask.task_id == task_id).first()
    assert "replica-b-renamed" in row.task_name
    session.close()

    # Removal unregisters and prunes BOTH row kinds.
    session = _wire_db()
    session.add(
        TaskSchedule(task_id=task_id, name="s", enabled=True, schedule_type="interval",
                     interval_seconds=3600)
    )
    session.commit()
    session.close()

    remove_sync_target_task(target_id)
    assert not registry.is_registered(task_id)
    session = _wire_db()
    assert session.query(ScheduledTask).filter(ScheduledTask.task_id == task_id).first() is None
    assert session.query(TaskSchedule).filter(TaskSchedule.task_id == task_id).first() is None
    session.close()


def test_register_sync_target_tasks_migrates_legacy_rows(_wire_db, _clean_registry):
    """Startup reconcile: every existing target gets its per-target task; a
    legacy ``dbas_sync`` schedule row is re-keyed to the per-target id carried
    in its parameters; a legacy row pointing at a DELETED target is disabled
    (non-silently, never re-keyed to a dead id); the legacy parent row and
    stale per-target rows are pruned."""
    from tasks.dbas_sync import register_sync_target_tasks, sync_task_id_for

    session = _wire_db()
    t1 = _make_target(session, name="replica-1")
    t2 = _make_target(session, name="replica-2")
    t1_id, t2_id = t1.id, t2.id

    # Legacy v1 rows: shared parent + one schedule per target, parameterized.
    session.add(ScheduledTask(task_id="dbas_sync", task_name="Cross-Instance Sync",
                              enabled=True, schedule_type="manual"))
    session.add(TaskSchedule(
        task_id="dbas_sync", name="hourly replica-1", enabled=True,
        schedule_type="interval", interval_seconds=3600,
        parameters=json.dumps({"sync_target_id": t1_id, "confirm_apply": True}),
    ))
    session.add(TaskSchedule(
        task_id="dbas_sync", name="orphan", enabled=True,
        schedule_type="interval", interval_seconds=3600,
        parameters=json.dumps({"sync_target_id": 999999}),
    ))
    # Stale per-target rows for a target deleted while the container was down.
    session.add(ScheduledTask(task_id=sync_task_id_for(999999),
                              task_name="Cross-Instance Sync: ghost",
                              enabled=False, schedule_type="manual"))
    session.commit()
    session.close()

    register_sync_target_tasks()

    registry = get_registry()
    assert registry.is_registered(sync_task_id_for(t1_id))
    assert registry.is_registered(sync_task_id_for(t2_id))
    assert not registry.is_registered("dbas_sync")

    session = _wire_db()
    # Legacy schedule re-keyed to its per-target id, parameters intact.
    migrated = session.query(TaskSchedule).filter(
        TaskSchedule.task_id == sync_task_id_for(t1_id)
    ).all()
    assert len(migrated) == 1
    assert migrated[0].enabled is True
    assert json.loads(migrated[0].parameters)["confirm_apply"] is True
    # Orphaned legacy schedule disabled, left under an id that can never fire.
    leftovers = session.query(TaskSchedule).filter(
        TaskSchedule.task_id == "dbas_sync"
    ).all()
    assert len(leftovers) == 1
    assert leftovers[0].enabled is False
    # Legacy parent row and stale per-target parent row pruned.
    assert session.query(ScheduledTask).filter(
        ScheduledTask.task_id == "dbas_sync"
    ).first() is None
    assert session.query(ScheduledTask).filter(
        ScheduledTask.task_id == sync_task_id_for(999999)
    ).first() is None
    session.close()


# ---------------------------------------------------------------------------
# 3. Engine-level concurrency semantics
# ---------------------------------------------------------------------------


def _register_bound_task(target_id: int, name: str):
    from tasks.dbas_sync import make_sync_task_class, sync_task_id_for

    registry = get_registry()
    registry.register(make_sync_task_class(target_id, name))
    return sync_task_id_for(target_id)


@pytest.mark.asyncio
async def test_concurrent_runs_of_different_targets_proceed(
    _wire_db, _clean_registry, _fresh_semaphore
):
    """While target 1's run is mid-flight (blocked inside run_sync), target 2's
    run must start AND complete — the exact starvation v1 exhibited."""
    from task_engine import TaskEngine
    from tasks import dbas_sync

    session = _wire_db()
    t1 = _make_target(session, name="replica-1")
    t2 = _make_target(session, name="replica-2")
    t1_id, t2_id = t1.id, t2.id
    session.close()

    task_id_1 = _register_bound_task(t1_id, "replica-1")
    task_id_2 = _register_bound_task(t2_id, "replica-2")

    t1_entered = asyncio.Event()
    t1_release = asyncio.Event()

    async def _fake_run_sync(sync_target, **_kw):
        if sync_target.id == t1_id:
            t1_entered.set()
            await t1_release.wait()
        return _success_report()

    engine = TaskEngine()
    with patch.object(dbas_sync, "run_sync", side_effect=_fake_run_sync):
        run1 = asyncio.create_task(engine._execute_task(task_id_1))
        await asyncio.wait_for(t1_entered.wait(), timeout=5)

        # Target 1 is mid-flight; target 2 must run to completion regardless.
        result2 = await asyncio.wait_for(engine._execute_task(task_id_2), timeout=5)
        assert result2 is not None
        assert result2.success is True
        assert not run1.done(), "target-1 run finished early — overlap not proven"

        t1_release.set()
        result1 = await asyncio.wait_for(run1, timeout=5)

    assert result1.success is True
    assert task_id_1 not in engine._active_tasks
    assert task_id_2 not in engine._active_tasks


@pytest.mark.asyncio
async def test_same_target_second_run_refused_non_silently(
    _wire_db, _clean_registry, _fresh_semaphore
):
    """Never two concurrent runs against the same B: the engine's guard refuses
    the second run of the SAME per-target task id with an explicit
    ALREADY_RUNNING failure (non-silent), and the target stays runnable after
    the first run finishes."""
    from task_engine import TaskEngine
    from tasks import dbas_sync

    session = _wire_db()
    t1 = _make_target(session, name="replica-1")
    t1_id = t1.id
    session.close()

    task_id_1 = _register_bound_task(t1_id, "replica-1")

    entered = asyncio.Event()
    release = asyncio.Event()

    async def _fake_run_sync(sync_target, **_kw):
        entered.set()
        await release.wait()
        return _success_report()

    engine = TaskEngine()
    with patch.object(dbas_sync, "run_sync", side_effect=_fake_run_sync):
        run1 = asyncio.create_task(engine._execute_task(task_id_1))
        await asyncio.wait_for(entered.wait(), timeout=5)

        refused = await engine._execute_task(task_id_1)
        assert refused is not None
        assert refused.success is False
        assert refused.error == "ALREADY_RUNNING"

        release.set()
        result1 = await asyncio.wait_for(run1, timeout=5)

    assert result1.success is True
    # The refusal must not have evicted the finished run's slot bookkeeping.
    assert task_id_1 not in engine._active_tasks


# ---------------------------------------------------------------------------
# 4. One-shot isolation under concurrency (extends 7ipq2.2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_shot_isolation_across_concurrent_targets(
    _wire_db, _clean_registry, _fresh_semaphore
):
    """Two targets in flight simultaneously: each run reads ONLY its own bound
    target and its own confirm_apply, and both instances disarm back to their
    bound id afterwards — no parameter leakage across targets or runs."""
    from tasks import dbas_sync
    from tasks.dbas_sync import make_sync_task_class

    session = _wire_db()
    t1 = _make_target(session, name="replica-1")
    t2 = _make_target(session, name="replica-2")
    t1_id, t2_id = t1.id, t2.id
    session.close()

    inst1 = make_sync_task_class(t1_id, "replica-1")()
    inst2 = make_sync_task_class(t2_id, "replica-2")()

    both_in_flight = asyncio.Barrier(2)
    captured: dict[int, bool] = {}

    async def _fake_run_sync(sync_target, *, confirm_apply=False, **_kw):
        await asyncio.wait_for(both_in_flight.wait(), timeout=5)
        captured[sync_target.id] = confirm_apply
        return _success_report()

    with patch.object(dbas_sync, "run_sync", side_effect=_fake_run_sync):
        # Target 1 armed for APPLY; target 2 left at the dry-run default.
        inst1.update_config({"confirm_apply": True})
        r1, r2 = await asyncio.gather(inst1.execute(), inst2.execute())

    assert r1.success is True and r2.success is True
    assert captured == {t1_id: True, t2_id: False}

    # Disarm resets to the BOUND id (not None) — the next bare run of this
    # target still syncs this target, but never replays confirm_apply.
    assert inst1.sync_target_id == t1_id
    assert inst2.sync_target_id == t2_id
    assert inst1.confirm_apply is False
    assert inst2.confirm_apply is False
    assert inst1.cloud_credential_version is None
    assert inst2.cloud_credential_version is None


@pytest.mark.asyncio
async def test_foreign_sync_target_id_parameter_hard_fails(
    _wire_db, _clean_registry, _reset_metrics, _fresh_semaphore
):
    """A bound task handed a DIFFERENT target's id must fail fast and
    non-silently WITHOUT running: silently syncing target B under target A's
    task id would run B outside B's own lock (two concurrent runs against the
    same B via two ids) and misattribute the run history."""
    from unittest.mock import AsyncMock

    from tasks import dbas_sync
    from tasks.dbas_sync import make_sync_task_class

    session = _wire_db()
    t1 = _make_target(session, name="replica-1")
    t2 = _make_target(session, name="replica-2")
    t1_id, t2_id = t1.id, t2.id
    session.close()

    inst = make_sync_task_class(t1_id, "replica-1")()

    with patch.object(dbas_sync, "run_sync", new=AsyncMock()) as mock_run:
        inst.update_config({"sync_target_id": t2_id, "confirm_apply": True})
        result = await inst.execute()

    assert mock_run.await_count == 0
    assert result.success is False
    assert str(t2_id) in (result.message or "")
    assert _sync_counter_value("failed") == 1.0

    # Disarmed back to the bound target — the conflict does not stick.
    assert inst.sync_target_id == t1_id
    assert inst.confirm_apply is False


@pytest.mark.asyncio
async def test_matching_sync_target_id_parameter_is_accepted(
    _wire_db, _clean_registry, _fresh_semaphore
):
    """The frontend keeps sending sync_target_id (self-documenting payload);
    a value MATCHING the bound target must run normally."""
    from tasks import dbas_sync
    from tasks.dbas_sync import make_sync_task_class

    session = _wire_db()
    t1 = _make_target(session, name="replica-1")
    t1_id = t1.id
    session.close()

    inst = make_sync_task_class(t1_id, "replica-1")()
    seen = {}

    async def _fake_run_sync(sync_target, *, confirm_apply=False, **_kw):
        seen["target_id"] = sync_target.id
        return _success_report()

    with patch.object(dbas_sync, "run_sync", side_effect=_fake_run_sync):
        inst.update_config({"sync_target_id": t1_id})
        result = await inst.execute()

    assert result.success is True
    assert seen["target_id"] == t1_id


# ---------------------------------------------------------------------------
# 5. Bounded concurrency
# ---------------------------------------------------------------------------


def test_sync_max_concurrent_default_and_env(monkeypatch):
    from tasks import dbas_sync

    monkeypatch.delenv("ECM_SYNC_MAX_CONCURRENT", raising=False)
    assert dbas_sync._sync_max_concurrent() == 3

    monkeypatch.setenv("ECM_SYNC_MAX_CONCURRENT", "5")
    assert dbas_sync._sync_max_concurrent() == 5

    # Invalid / out-of-range values fall back to the safe default, never 0.
    monkeypatch.setenv("ECM_SYNC_MAX_CONCURRENT", "0")
    assert dbas_sync._sync_max_concurrent() == 3
    monkeypatch.setenv("ECM_SYNC_MAX_CONCURRENT", "not-a-number")
    assert dbas_sync._sync_max_concurrent() == 3


@pytest.mark.asyncio
async def test_semaphore_queues_excess_runs_instead_of_dropping(
    _wire_db, _clean_registry, _fresh_semaphore, monkeypatch
):
    """With the cap forced to 1, two different-target runs serialize: the
    second waits for the first slot and still completes — queued, not
    refused."""
    from tasks import dbas_sync
    from tasks.dbas_sync import make_sync_task_class

    monkeypatch.setenv("ECM_SYNC_MAX_CONCURRENT", "1")
    dbas_sync.reset_sync_concurrency_for_tests()

    session = _wire_db()
    t1 = _make_target(session, name="replica-1")
    t2 = _make_target(session, name="replica-2")
    t1_id, t2_id = t1.id, t2.id
    session.close()

    inst1 = make_sync_task_class(t1_id, "replica-1")()
    inst2 = make_sync_task_class(t2_id, "replica-2")()

    entered: list[int] = []
    t1_release = asyncio.Event()

    async def _fake_run_sync(sync_target, **_kw):
        entered.append(sync_target.id)
        if sync_target.id == t1_id:
            await t1_release.wait()
        return _success_report()

    with patch.object(dbas_sync, "run_sync", side_effect=_fake_run_sync):
        run1 = asyncio.create_task(inst1.execute())
        while not entered:  # wait until run 1 holds the only slot
            await asyncio.sleep(0.01)

        run2 = asyncio.create_task(inst2.execute())
        # Give run 2 a real chance to (incorrectly) enter run_sync.
        await asyncio.sleep(0.05)
        assert entered == [t1_id], "cap=1 must hold run 2 out of run_sync"

        t1_release.set()
        r1, r2 = await asyncio.gather(run1, run2)

    assert r1.success is True and r2.success is True
    assert entered == [t1_id, t2_id]


# ---------------------------------------------------------------------------
# 6. Metric attribution under concurrency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_runs_attribute_metrics_per_result(
    _wire_db, _clean_registry, _reset_metrics, _fresh_semaphore
):
    """Concurrent success + failure runs each bump their OWN result label
    exactly once — attribution survives interleaving."""
    from tasks import dbas_sync
    from tasks.dbas_sync import make_sync_task_class

    session = _wire_db()
    t1 = _make_target(session, name="replica-1")
    t2 = _make_target(session, name="replica-2")
    t1_id, t2_id = t1.id, t2.id
    session.close()

    inst1 = make_sync_task_class(t1_id, "replica-1")()
    inst2 = make_sync_task_class(t2_id, "replica-2")()

    both_in_flight = asyncio.Barrier(2)

    async def _fake_run_sync(sync_target, **_kw):
        await asyncio.wait_for(both_in_flight.wait(), timeout=5)
        if sync_target.id == t2_id:
            raise RuntimeError("B unreachable")
        return _success_report()

    with patch.object(dbas_sync, "run_sync", side_effect=_fake_run_sync):
        r1, r2 = await asyncio.gather(inst1.execute(), inst2.execute())

    assert r1.success is True
    assert r2.success is False
    assert _sync_counter_value("success") == 1.0
    assert _sync_counter_value("failed") == 1.0
    assert _sync_counter_value("partial") == 0.0


# ---------------------------------------------------------------------------
# Privileged-task gating for per-target ids (O8TBV-1 continuity)
# ---------------------------------------------------------------------------


def test_per_target_sync_ids_are_privileged():
    """Every per-target sync id must stay admin-gated exactly like the ids in
    PRIVILEGED_TASK_IDS — the outbound-write surface did not stop being
    privileged because the id grew a suffix."""
    from routers.tasks import PRIVILEGED_TASK_IDS, is_privileged_task_id

    for legacy_id in PRIVILEGED_TASK_IDS:
        assert is_privileged_task_id(legacy_id)
    assert is_privileged_task_id("dbas_sync_1")
    assert is_privileged_task_id("dbas_sync_12345")
    assert not is_privileged_task_id("cleanup")
    assert not is_privileged_task_id("stream_probe")
