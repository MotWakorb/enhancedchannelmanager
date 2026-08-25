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
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.orm import sessionmaker

import database
import observability
from dbas.restore_contracts import RestoreOutcome, RestoreReport
from export_models import SyncTarget
from models import ScheduledTask, TaskExecution, TaskSchedule
from task_registry import get_registry
from tasks import dbas_sync


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


def _sync_counter_value(result_label: str, sync_target_id: int) -> float:
    """Read ecm_sync_runs_total for one result label on ONE target's series."""
    counter = observability.get_metric("sync_runs_total")
    return counter.labels(
        result=result_label, sync_target_id=str(sync_target_id)
    )._value.get()


# ---------------------------------------------------------------------------
# 1. Per-target task identity
# ---------------------------------------------------------------------------


def test_sync_task_id_scheme():

    assert dbas_sync.SYNC_TASK_ID_PREFIX == "dbas_sync_"
    assert dbas_sync.sync_task_id_for(7) == "dbas_sync_7"


def test_make_sync_task_class_binds_target():
    from task_scheduler import ScheduleType

    cls = dbas_sync.make_sync_task_class(7, "replica-b")
    assert issubclass(cls, dbas_sync.DbasSyncTask)
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

    session = _wire_db()
    target = _make_target(session, name="replica-b")
    target_id = target.id
    session.close()

    task_id = dbas_sync.sync_task_id_for(target_id)
    dbas_sync.ensure_sync_target_task(target_id, "replica-b")

    registry = get_registry()
    assert registry.is_registered(task_id)
    session = _wire_db()
    row = session.query(ScheduledTask).filter(ScheduledTask.task_id == task_id).first()
    assert row is not None
    assert "replica-b" in row.task_name
    session.close()

    # Rename flows through ensure (same id, refreshed display name).
    dbas_sync.ensure_sync_target_task(target_id, "replica-b-renamed")
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

    dbas_sync.remove_sync_target_task(target_id)
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
    session.add(ScheduledTask(task_id=dbas_sync.sync_task_id_for(999999),
                              task_name="Cross-Instance Sync: ghost",
                              enabled=False, schedule_type="manual"))
    session.commit()
    session.close()

    dbas_sync.register_sync_target_tasks()

    registry = get_registry()
    assert registry.is_registered(dbas_sync.sync_task_id_for(t1_id))
    assert registry.is_registered(dbas_sync.sync_task_id_for(t2_id))
    assert not registry.is_registered("dbas_sync")

    session = _wire_db()
    # Legacy schedule re-keyed to its per-target id, parameters intact.
    migrated = session.query(TaskSchedule).filter(
        TaskSchedule.task_id == dbas_sync.sync_task_id_for(t1_id)
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
        ScheduledTask.task_id == dbas_sync.sync_task_id_for(999999)
    ).first() is None
    session.close()


# ---------------------------------------------------------------------------
# 3. Engine-level concurrency semantics
# ---------------------------------------------------------------------------


def _register_bound_task(target_id: int, name: str):

    registry = get_registry()
    registry.register(dbas_sync.make_sync_task_class(target_id, name))
    return dbas_sync.sync_task_id_for(target_id)


@pytest.mark.asyncio
async def test_concurrent_runs_of_different_targets_proceed(
    _wire_db, _clean_registry, _fresh_semaphore
):
    """While target 1's run is mid-flight (blocked inside run_sync), target 2's
    run must start AND complete — the exact starvation v1 exhibited."""
    from task_engine import TaskEngine

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

    session = _wire_db()
    t1 = _make_target(session, name="replica-1")
    t2 = _make_target(session, name="replica-2")
    t1_id, t2_id = t1.id, t2.id
    session.close()

    inst1 = dbas_sync.make_sync_task_class(t1_id, "replica-1")()
    inst2 = dbas_sync.make_sync_task_class(t2_id, "replica-2")()

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


    session = _wire_db()
    t1 = _make_target(session, name="replica-1")
    t2 = _make_target(session, name="replica-2")
    t1_id, t2_id = t1.id, t2.id
    session.close()

    inst = dbas_sync.make_sync_task_class(t1_id, "replica-1")()

    with patch.object(dbas_sync, "run_sync", new=AsyncMock()) as mock_run:
        inst.update_config({"sync_target_id": t2_id, "confirm_apply": True})
        result = await inst.execute()

    assert mock_run.await_count == 0
    assert result.success is False
    assert str(t2_id) in (result.message or "")
    assert _sync_counter_value("failed", t1_id) == 1.0

    # Disarmed back to the bound target — the conflict does not stick.
    assert inst.sync_target_id == t1_id
    assert inst.confirm_apply is False


@pytest.mark.asyncio
async def test_matching_sync_target_id_parameter_is_accepted(
    _wire_db, _clean_registry, _fresh_semaphore
):
    """The frontend keeps sending sync_target_id (self-documenting payload);
    a value MATCHING the bound target must run normally."""

    session = _wire_db()
    t1 = _make_target(session, name="replica-1")
    t1_id = t1.id
    session.close()

    inst = dbas_sync.make_sync_task_class(t1_id, "replica-1")()
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

    monkeypatch.setenv("ECM_SYNC_MAX_CONCURRENT", "1")
    dbas_sync.reset_sync_concurrency_for_tests()

    session = _wire_db()
    t1 = _make_target(session, name="replica-1")
    t2 = _make_target(session, name="replica-2")
    t1_id, t2_id = t1.id, t2.id
    session.close()

    inst1 = dbas_sync.make_sync_task_class(t1_id, "replica-1")()
    inst2 = dbas_sync.make_sync_task_class(t2_id, "replica-2")()

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

    session = _wire_db()
    t1 = _make_target(session, name="replica-1")
    t2 = _make_target(session, name="replica-2")
    t1_id, t2_id = t1.id, t2.id
    session.close()

    inst1 = dbas_sync.make_sync_task_class(t1_id, "replica-1")()
    inst2 = dbas_sync.make_sync_task_class(t2_id, "replica-2")()

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
    assert _sync_counter_value("success", t1_id) == 1.0
    assert _sync_counter_value("failed", t2_id) == 1.0
    assert _sync_counter_value("partial", t1_id) == 0.0
    assert _sync_counter_value("partial", t2_id) == 0.0


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


# ---------------------------------------------------------------------------
# 7. Migration preserves EFFECTIVE enabled state (PR #752 review, Block 1).
#
# Firing needs BOTH the parent ``scheduled_tasks.enabled`` gate AND an enabled
# child ``task_schedules`` row (task_engine._check_and_run_due_tasks). The
# migration re-keys enabled child rows and deletes the legacy parent, but the
# registry creates each new per-target parent from ``default_enabled=False``
# — so an operator upgrading with a WORKING hourly schedule would land on a
# silently non-firing one. The effective pre-upgrade state (legacy parent
# enabled AND child enabled) must survive the migration.
# ---------------------------------------------------------------------------


def _seed_legacy_schedule(session, target_id, *, parent_enabled=True,
                          child_enabled=True, next_run_at=None):
    """Seed the pre-7ipq2.3 row shape: one shared parent + a parameterized child."""
    session.add(ScheduledTask(
        task_id="dbas_sync", task_name="Cross-Instance Sync",
        enabled=parent_enabled, schedule_type="manual",
    ))
    sched = TaskSchedule(
        task_id="dbas_sync", name="hourly", enabled=child_enabled,
        schedule_type="interval", interval_seconds=3600,
        parameters=json.dumps({"sync_target_id": target_id, "confirm_apply": True}),
        next_run_at=next_run_at,
    )
    session.add(sched)
    session.commit()
    return sched


def test_migration_preserves_enabled_parent_gate(_wire_db, _clean_registry):
    """A working legacy schedule (parent ON + child ON) migrates to a per-target
    parent that is ALSO on — otherwise the child is re-keyed onto a disabled
    parent and never fires again."""
    session = _wire_db()
    target = _make_target(session, name="replica-1")
    target_id = target.id
    _seed_legacy_schedule(session, target_id)
    session.close()

    dbas_sync.register_sync_target_tasks()

    session = _wire_db()
    parent = session.query(ScheduledTask).filter(
        ScheduledTask.task_id == dbas_sync.sync_task_id_for(target_id)
    ).first()
    assert parent is not None, "per-target parent row was not created"
    assert parent.enabled is True, "effective enabled state lost in migration"
    child = session.query(TaskSchedule).filter(
        TaskSchedule.task_id == dbas_sync.sync_task_id_for(target_id)
    ).one()
    assert child.enabled is True
    session.close()

    # The registry instance must agree — sync_from_database reads the row, but
    # the in-memory instance is what the engine's legacy fallback path checks.
    instance = get_registry().get_task_instance(dbas_sync.sync_task_id_for(target_id))
    assert instance._enabled is True


def test_migration_does_not_enable_a_disabled_legacy_setup(_wire_db, _clean_registry):
    """The migration PRESERVES effective state — it never turns a deliberately
    stopped sync back on. Parent off (the documented kill switch) stays off."""
    session = _wire_db()
    target = _make_target(session, name="replica-1")
    target_id = target.id
    _seed_legacy_schedule(session, target_id, parent_enabled=False)
    session.close()

    dbas_sync.register_sync_target_tasks()

    session = _wire_db()
    parent = session.query(ScheduledTask).filter(
        ScheduledTask.task_id == dbas_sync.sync_task_id_for(target_id)
    ).first()
    assert parent is not None
    assert parent.enabled is False
    session.close()


def test_migration_leaves_parent_off_when_child_was_disabled(_wire_db, _clean_registry):
    """Parent ON but child OFF was NOT firing before — it must not start now."""
    session = _wire_db()
    target = _make_target(session, name="replica-1")
    target_id = target.id
    _seed_legacy_schedule(session, target_id, child_enabled=False)
    session.close()

    dbas_sync.register_sync_target_tasks()

    session = _wire_db()
    parent = session.query(ScheduledTask).filter(
        ScheduledTask.task_id == dbas_sync.sync_task_id_for(target_id)
    ).first()
    assert parent.enabled is False
    session.close()


@pytest.mark.asyncio
async def test_migrated_schedule_fires_on_first_due_tick(
    _wire_db, _clean_registry, _fresh_semaphore
):
    """END-TO-END through the scheduler, not just registration state: seed the
    legacy working schedule with a DUE next_run_at, run the real startup
    sequence (register_sync_target_tasks -> sync_from_database), then let the
    engine's due-task scan run. The migrated schedule must actually FIRE.

    This is the regression the review demanded: asserting on row/registry state
    alone passed while the run silently never happened (parent gate off)."""
    from task_engine import TaskEngine

    session = _wire_db()
    target = _make_target(session, name="replica-1")
    target_id = target.id
    _seed_legacy_schedule(
        session, target_id,
        next_run_at=datetime.utcnow() - timedelta(minutes=5),  # already due
    )
    session.close()

    # --- real startup order (main.py): register, THEN engine start/sync ---
    dbas_sync.register_sync_target_tasks()
    registry = get_registry()
    registry.sync_from_database()

    fired = asyncio.Event()
    seen = {}

    async def _fake_run_sync(sync_target, *, confirm_apply=False, **_kw):
        seen["target_id"] = sync_target.id
        seen["confirm_apply"] = confirm_apply
        fired.set()
        return _success_report()

    engine = TaskEngine()
    with patch.object(dbas_sync, "run_sync", side_effect=_fake_run_sync):
        await engine._check_and_run_due_tasks()
        await asyncio.wait_for(fired.wait(), timeout=5)
        # Let the spawned run finish before the patch is torn down.
        for _ in range(200):
            if not engine._active_tasks:
                break
            await asyncio.sleep(0.01)

    assert seen["target_id"] == target_id
    # The migrated schedule's own parameters still drive the run.
    assert seen["confirm_apply"] is True


# ---------------------------------------------------------------------------
# 8. Drift signal is FULL-APPLY-ONLY (PR #752 review, Block 2).
#
# The generic task engine stamps ecm_task_schedule_last_success_timestamp on
# ANY TaskResult.success, and a dry-run PREVIEW legitimately reports success
# (it produced a plan). The staleness alert defines its gauge as "last FULL
# success" freshness, so a recurring preview would reset the drift clock
# without ever writing B — masking real divergence. The alert therefore keys
# on a dedicated apply-only gauge stamped by this task.
# ---------------------------------------------------------------------------


def _full_success_gauge(target_id: int) -> float:
    gauge = observability.get_metric("sync_last_full_success_timestamp")
    return gauge.labels(sync_target_id=str(target_id))._value.get()


@pytest.mark.asyncio
async def test_dry_run_preview_does_not_stamp_full_success_gauge(
    _wire_db, _clean_registry, _reset_metrics, _fresh_semaphore
):
    """A preview writes nothing to B — it must NOT reset the drift clock."""
    session = _wire_db()
    target = _make_target(session, name="replica-1")
    target_id = target.id
    session.close()

    inst = dbas_sync.make_sync_task_class(target_id, "replica-1")()

    async def _fake_run_sync(sync_target, **_kw):
        return RestoreReport(is_dry_run=True)

    with patch.object(dbas_sync, "run_sync", side_effect=_fake_run_sync):
        result = await inst.execute()

    # The RUN succeeded (a preview that produced a plan is a success) ...
    assert result.success is True
    assert result.details["is_dry_run"] is True
    # ... but the apply-only drift gauge stays untouched.
    assert _full_success_gauge(target_id) == 0.0


@pytest.mark.asyncio
async def test_successful_apply_stamps_full_success_gauge(
    _wire_db, _clean_registry, _reset_metrics, _fresh_semaphore
):
    """A clean APPLY is the only thing that resets the drift clock."""
    session = _wire_db()
    target = _make_target(session, name="replica-1")
    target_id = target.id
    session.close()

    inst = dbas_sync.make_sync_task_class(target_id, "replica-1")()

    async def _fake_run_sync(sync_target, **_kw):
        return _success_report()

    with patch.object(dbas_sync, "run_sync", side_effect=_fake_run_sync):
        inst.update_config({"confirm_apply": True})
        result = await inst.execute()

    assert result.success is True
    assert _full_success_gauge(target_id) > 0.0


@pytest.mark.asyncio
async def test_partial_apply_does_not_stamp_full_success_gauge(
    _wire_db, _clean_registry, _reset_metrics, _fresh_semaphore
):
    """Tri-state discipline: a mixed/rolled-back apply leaves B drifting, so it
    must not reset the clock either (the sustained-partial-loop case the
    runbook calls out)."""
    session = _wire_db()
    target = _make_target(session, name="replica-1")
    target_id = target.id
    session.close()

    inst = dbas_sync.make_sync_task_class(target_id, "replica-1")()

    async def _fake_run_sync(sync_target, **_kw):
        report = RestoreReport(is_dry_run=False)
        report.outcome = RestoreOutcome.PARTIAL_FAILED_ROLLED_BACK
        return report

    with patch.object(dbas_sync, "run_sync", side_effect=_fake_run_sync):
        inst.update_config({"confirm_apply": True})
        result = await inst.execute()

    assert result.success is False
    assert _full_success_gauge(target_id) == 0.0


@pytest.mark.asyncio
async def test_full_success_gauge_is_attributed_per_target(
    _wire_db, _clean_registry, _reset_metrics, _fresh_semaphore
):
    """Target 1 applying cleanly must NOT reset target 2's drift clock — the
    per-target attribution the whole alert story depends on."""
    session = _wire_db()
    t1 = _make_target(session, name="replica-1")
    t2 = _make_target(session, name="replica-2")
    t1_id, t2_id = t1.id, t2.id
    session.close()

    inst1 = dbas_sync.make_sync_task_class(t1_id, "replica-1")()

    async def _fake_run_sync(sync_target, **_kw):
        return _success_report()

    with patch.object(dbas_sync, "run_sync", side_effect=_fake_run_sync):
        inst1.update_config({"confirm_apply": True})
        await inst1.execute()

    assert _full_success_gauge(t1_id) > 0.0
    assert _full_success_gauge(t2_id) == 0.0


# ---------------------------------------------------------------------------
# 9. Per-target attribution on ecm_sync_runs_total (PO-authorized scope).
#
# With per-target concurrency the runbook's triage step ("compare
# ecm_sync_runs_total{result=failed} vs {result=partial}") could no longer say
# WHICH target was failing, while the freshness gauge had already become
# per-target — the two signals disagreed in granularity. The counter now
# carries the same target key the gauge uses.
#
# Label key: sync_target_id (the SyncTarget row pk), NOT the target name —
# a rename would fork the series and break rate()/increase() continuity, and
# the pk is the same key the freshness gauge and the task id are derived from.
# ---------------------------------------------------------------------------


def test_sync_runs_total_is_labeled_by_result_and_target():
    """Label shape is exactly {result, sync_target_id} — the counter must be
    filterable per target, keyed on the immutable pk."""
    observability.reset_for_tests()
    observability.install_metrics()
    try:
        counter = observability.get_metric("sync_runs_total")
        assert set(counter._labelnames) == {"result", "sync_target_id"}
    finally:
        observability.reset_for_tests()


@pytest.mark.asyncio
async def test_run_outcomes_are_attributed_to_their_own_target(
    _wire_db, _clean_registry, _reset_metrics, _fresh_semaphore
):
    """Two targets, opposite outcomes, run concurrently: each result lands on
    its OWN target's series. Under the aggregate counter a responder could see
    one success and one failure but not which replica was broken."""
    session = _wire_db()
    t1 = _make_target(session, name="replica-1")
    t2 = _make_target(session, name="replica-2")
    t1_id, t2_id = t1.id, t2.id
    session.close()

    inst1 = dbas_sync.make_sync_task_class(t1_id, "replica-1")()
    inst2 = dbas_sync.make_sync_task_class(t2_id, "replica-2")()

    both_in_flight = asyncio.Barrier(2)

    async def _fake_run_sync(sync_target, **_kw):
        await asyncio.wait_for(both_in_flight.wait(), timeout=5)
        if sync_target.id == t2_id:
            raise RuntimeError("B unreachable")
        return _success_report()

    with patch.object(dbas_sync, "run_sync", side_effect=_fake_run_sync):
        inst1.update_config({"confirm_apply": True})
        inst2.update_config({"confirm_apply": True})
        await asyncio.gather(inst1.execute(), inst2.execute())

    # Target 1 succeeded; target 2 failed. Neither leaks onto the other.
    assert _sync_counter_value("success", t1_id) == 1.0
    assert _sync_counter_value("failed", t1_id) == 0.0
    assert _sync_counter_value("failed", t2_id) == 1.0
    assert _sync_counter_value("success", t2_id) == 0.0


@pytest.mark.asyncio
async def test_freshness_abort_is_attributed_to_its_target(
    _wire_db, _clean_registry, _reset_metrics, _fresh_semaphore
):
    """The credential-freshness abort path carries the target label too — it is
    the single most common 'why is this target failing' cause in the runbook."""
    session = _wire_db()
    t1 = _make_target(session, name="replica-1")
    t2 = _make_target(session, name="replica-2", enabled=False)
    t1_id, t2_id = t1.id, t2.id
    session.close()

    inst2 = dbas_sync.make_sync_task_class(t2_id, "replica-2")()
    result = await inst2.execute()

    assert result.error == "CREDENTIAL_FRESHNESS_ABORT"
    assert _sync_counter_value("failed", t2_id) == 1.0
    assert _sync_counter_value("failed", t1_id) == 0.0


# ---------------------------------------------------------------------------
# 10. Registration NEVER downgrades an existing per-target parent gate
#     (PR #752 delta review — regression introduced BY the Block 1 fix).
#
# ``register_sync_target_tasks`` persists every registered target's parent row
# at startup. Per-target task classes are created dynamically, so on EVERY
# boot the registry hands back a freshly constructed instance still carrying
# ``default_enabled = False``; ``task_registry._save_task_to_db`` writes that
# straight over the existing row, and ``sync_from_database`` only hydrates the
# instance afterwards. Net effect for an operator: the upgrade works, sync
# runs, and then the next ordinary container restart silently turns it off.
#
# Section 7's tests all observe the FIRST startup, which is exactly why they
# missed this — every case below drives a genuine SECOND process startup
# (registry memory dropped, database kept) and then a real engine due-tick.
# ---------------------------------------------------------------------------


def _simulate_process_restart(registry) -> None:
    """Drop ALL in-memory registry state while keeping the database.

    A container restart re-imports the task modules into an EMPTY registry and
    re-runs ``register_sync_target_tasks`` against the SAME rows. Per-target
    sync classes are not statically registered, so nothing survives in memory
    — reproducing that (rather than re-invoking registration over already-
    hydrated instances) is what makes these second-startup regressions real.
    """
    registry._tasks.clear()
    registry._instances.clear()
    registry._initialized = False


def _startup(registry) -> None:
    """The real main.py order: register per-target tasks, then hydrate."""
    dbas_sync.register_sync_target_tasks()
    registry.sync_from_database()


def _seed_per_target_schedule(session, task_id, *, enabled=True, next_run_at=None,
                              parameters=None):
    session.add(TaskSchedule(
        task_id=task_id, name="hourly", enabled=enabled,
        schedule_type="interval", interval_seconds=3600,
        parameters=json.dumps(parameters or {"confirm_apply": True}),
        next_run_at=next_run_at,
    ))
    session.commit()


def _parent_enabled(session, task_id) -> bool:
    row = session.query(ScheduledTask).filter(
        ScheduledTask.task_id == task_id
    ).first()
    assert row is not None, "per-target parent row %s is missing" % task_id
    return bool(row.enabled)


async def _tick_and_wait(engine, fired, *, timeout=5) -> bool:
    """Run one due-task scan; report whether the sync actually fired."""
    await engine._check_and_run_due_tasks()
    try:
        await asyncio.wait_for(fired.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        return False
    finally:
        for _ in range(200):
            if not engine._active_tasks:
                break
            await asyncio.sleep(0.01)
    return True


@pytest.mark.asyncio
async def test_due_schedule_without_apply_confirmation_fails_instead_of_previewing(
    _wire_db, _clean_registry, _fresh_semaphore
):
    """The real scheduler seam must carry trigger context into DbasSyncTask."""
    from task_engine import TaskEngine

    registry = _clean_registry
    session = _wire_db()
    target = _make_target(session, name="replica-1")
    target_id = target.id
    session.close()
    task_id = dbas_sync.sync_task_id_for(target_id)
    _startup(registry)

    session = _wire_db()
    session.query(ScheduledTask).filter(
        ScheduledTask.task_id == task_id
    ).update({"enabled": True})
    session.add(TaskSchedule(
        task_id=task_id,
        name="legacy preview schedule",
        enabled=True,
        schedule_type="interval",
        interval_seconds=3600,
        parameters=None,
        next_run_at=datetime.utcnow() - timedelta(minutes=5),
    ))
    session.commit()
    session.close()

    engine = TaskEngine()
    with patch.object(dbas_sync, "run_sync", new=AsyncMock()) as mock_run:
        await engine._check_and_run_due_tasks()
        execution = None
        for _ in range(200):
            session = _wire_db()
            execution = session.query(TaskExecution).filter(
                TaskExecution.task_id == task_id,
                TaskExecution.status != "running",
            ).first()
            session.close()
            if execution is not None:
                break
            await asyncio.sleep(0.01)

    assert mock_run.await_count == 0
    assert execution is not None
    assert execution.error == "SCHEDULE_APPLY_NOT_CONFIRMED"


@pytest.mark.asyncio
async def test_enabled_parent_survives_a_normal_restart_and_fires(
    _wire_db, _clean_registry, _fresh_semaphore
):
    """No legacy rows anywhere — just an operator who enabled per-target sync.
    The SECOND startup must leave the parent gate on and the due child must
    still fire."""
    from task_engine import TaskEngine

    registry = _clean_registry
    session = _wire_db()
    target = _make_target(session, name="replica-1")
    target_id = target.id
    session.close()
    task_id = dbas_sync.sync_task_id_for(target_id)

    # --- first startup creates the (disabled) parent row -------------------
    _startup(registry)

    # --- operator enables the task and gives it a due hourly schedule ------
    session = _wire_db()
    session.query(ScheduledTask).filter(
        ScheduledTask.task_id == task_id
    ).update({"enabled": True})
    _seed_per_target_schedule(
        session, task_id, next_run_at=datetime.utcnow() - timedelta(minutes=5)
    )
    session.close()

    # --- ordinary container restart ---------------------------------------
    _simulate_process_restart(registry)
    _startup(registry)

    fired = asyncio.Event()
    seen = {}

    async def _fake_run_sync(sync_target, *, confirm_apply=False, **_kw):
        seen["target_id"] = sync_target.id
        fired.set()
        return _success_report()

    # Behaviour first — the row assertion below only localises the cause.
    engine = TaskEngine()
    with patch.object(dbas_sync, "run_sync", side_effect=_fake_run_sync):
        assert await _tick_and_wait(engine, fired), (
            "enabled schedule did not fire after a normal restart"
        )
    assert seen["target_id"] == target_id

    session = _wire_db()
    assert _parent_enabled(session, task_id) is True, (
        "registration downgraded an ENABLED per-target parent on restart"
    )
    session.close()


@pytest.mark.asyncio
async def test_disabled_parent_stays_disabled_across_restart(
    _wire_db, _clean_registry, _fresh_semaphore
):
    """The other half of the invariant: registration must not ACCIDENTALLY
    re-enable a parent the operator deliberately switched off (the documented
    kill switch), even with an enabled, due child schedule sitting there."""
    from task_engine import TaskEngine

    registry = _clean_registry
    session = _wire_db()
    target = _make_target(session, name="replica-1")
    target_id = target.id
    session.close()
    task_id = dbas_sync.sync_task_id_for(target_id)

    _startup(registry)

    session = _wire_db()
    session.query(ScheduledTask).filter(
        ScheduledTask.task_id == task_id
    ).update({"enabled": False})
    _seed_per_target_schedule(
        session, task_id, next_run_at=datetime.utcnow() - timedelta(minutes=5)
    )
    session.close()

    _simulate_process_restart(registry)
    _startup(registry)

    session = _wire_db()
    assert _parent_enabled(session, task_id) is False
    session.close()

    fired = asyncio.Event()
    engine = TaskEngine()
    with patch.object(dbas_sync, "run_sync", side_effect=AssertionError(
        "a disabled parent must never fire"
    )):
        assert not await _tick_and_wait(engine, fired, timeout=0.5)


@pytest.mark.asyncio
async def test_migrated_schedule_still_fires_on_the_second_startup(
    _wire_db, _clean_registry, _fresh_semaphore
):
    """The upgrade path end to end: legacy migration on startup #1 (fires), then
    a plain restart on startup #2 with NO legacy rows left. The second boot is
    where the freshly materialized default-disabled instance used to overwrite
    the parent the migration had just enabled."""
    from task_engine import TaskEngine

    registry = _clean_registry
    session = _wire_db()
    target = _make_target(session, name="replica-1")
    target_id = target.id
    _seed_legacy_schedule(
        session, target_id,
        next_run_at=datetime.utcnow() - timedelta(minutes=5),
    )
    session.close()
    task_id = dbas_sync.sync_task_id_for(target_id)

    fired = asyncio.Event()
    seen = {}

    async def _fake_run_sync(sync_target, *, confirm_apply=False, **_kw):
        seen["target_id"] = sync_target.id
        seen["confirm_apply"] = confirm_apply
        fired.set()
        return _success_report()

    # --- startup #1: migration + first due run ----------------------------
    _startup(registry)
    engine = TaskEngine()
    with patch.object(dbas_sync, "run_sync", side_effect=_fake_run_sync):
        assert await _tick_and_wait(engine, fired), "migrated schedule never fired"

    session = _wire_db()
    assert session.query(TaskSchedule).filter(
        TaskSchedule.task_id == dbas_sync.LEGACY_SYNC_TASK_ID
    ).first() is None, "legacy rows should be gone after the migration"
    # Make it due again for the next boot's tick.
    session.query(TaskSchedule).filter(
        TaskSchedule.task_id == task_id
    ).update({"next_run_at": datetime.utcnow() - timedelta(minutes=5)})
    session.commit()
    session.close()

    # --- startup #2: ordinary restart, nothing left to migrate ------------
    _simulate_process_restart(registry)
    _startup(registry)

    fired.clear()
    seen.clear()
    engine = TaskEngine()
    with patch.object(dbas_sync, "run_sync", side_effect=_fake_run_sync):
        assert await _tick_and_wait(engine, fired), (
            "migrated schedule stopped firing after a restart"
        )
    assert seen["target_id"] == target_id
    assert seen["confirm_apply"] is True

    session = _wire_db()
    assert _parent_enabled(session, task_id) is True, (
        "the migrated parent was disabled by the second startup"
    )
    session.close()
