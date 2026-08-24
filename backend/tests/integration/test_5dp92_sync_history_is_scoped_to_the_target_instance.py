"""A sync target never displays a run it did not perform (bead …-5dp92).

Layer: API integration — real router → real ``TaskEngine.get_task_history`` →
real ``task_executions`` rows in the in-memory DB. Nothing is mocked; the seam
these tests cross is the one the operator's browser crosses.

THE DEFECT
----------
Execution history is keyed on ``task_executions.task_id``, which for a sync
target is ``dbas_sync_<target_id>`` (``tasks/dbas_sync.py`` →
:func:`sync_task_id_for`). ``SyncTarget.id`` is a SQLite autoincrement primary
key, so it is REUSED after a delete, and
:func:`tasks.dbas_sync.remove_sync_target_task` prunes the ``scheduled_tasks``
and ``task_schedules`` rows but not the ``task_executions`` rows. Delete target
1 and create another, the new one is also assigned id 1, and it opens carrying
the deleted target's runs — including failures that were not its own, on the
surface an operator uses to decide whether the target is healthy.

THE INVARIANT (this is the specification; delete-then-recreate is ONE example
of it, not the definition)
--------------------------------------------------------------------------
A ``task_executions`` row is attributed to a per-target sync task only when the
currently-live ``SyncTarget`` behind that id existed when the run STARTED.
Equivalently: no run that began before a target instance came into existence is
ever shown as that instance's history, on any surface, however the id came to
be reused — and an id with no live target behind it has no history at all.

``started_at`` is the column that carries this, not ``completed_at``: a run
in flight when its target is deleted is deliberately not interrupted
(``remove_sync_target_task``), so it can finish AFTER the replacement is
created. It still started before the replacement existed, and it is still not
the replacement's run.

WHY IT IS ENFORCED AT THE READ AND NOT ON DELETE
------------------------------------------------
Purging the rows in ``remove_sync_target_task`` would be a cleanup step, not a
guarantee: that function's documented contract is best-effort, it runs after
the delete transaction has already committed, and it does nothing for the
installs that are carrying orphaned rows today. Scoping at the read holds
whether or not any cleanup ever ran.

WHY a3lby DID NOT FIX THIS
--------------------------
Bead …-a3lby made a sync target correctable in place, so the common route into
this defect — rebuild the target to fix a typo — is no longer necessary. That
makes the bug HARDER TO REPRODUCE by walking the UI while leaving the
id/history keying untouched. Do not read a failed manual reproduction as
absence of the defect; read these tests.

Conventions: ``docs/pytest_conventions.md``.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from export_models import SyncTarget
from models import TaskExecution


def _execution(task_id: str, started_at: datetime, *, status: str = "completed") -> TaskExecution:
    return TaskExecution(
        task_id=task_id,
        started_at=started_at,
        completed_at=started_at + timedelta(seconds=5),
        duration_seconds=5.0,
        status=status,
        success=status == "completed",
        message="%s run at %s" % (task_id, started_at.isoformat()),
        triggered_by="manual",
    )


def _target(target_id: int, name: str, created_at: datetime) -> SyncTarget:
    return SyncTarget(
        id=target_id,
        name=name,
        base_url="https://b.example.com",
        credentials="{}",
        created_at=created_at,
        updated_at=created_at,
    )


# The predecessor's timeline, then the replacement's — the ids collide.
T_PREDECESSOR_RUN = datetime(2026, 8, 1, 9, 0, 0)
T_REPLACEMENT_CREATED = datetime(2026, 8, 2, 9, 0, 0)
T_REPLACEMENT_RUN = datetime(2026, 8, 3, 9, 0, 0)


async def _history(async_client, task_id: str) -> list[dict]:
    resp = await async_client.get("/api/tasks/%s/history" % task_id)
    assert resp.status_code == 200, resp.text
    return resp.json()["history"]


async def _all_history(async_client) -> list[dict]:
    resp = await async_client.get("/api/tasks/history/all")
    assert resp.status_code == 200, resp.text
    return resp.json()["history"]


class TestSyncHistoryIsScopedToTheTargetInstance:
    """The reused-id case, and the property it is an example of."""

    @pytest.mark.asyncio
    async def test_a_replacement_target_does_not_inherit_its_predecessors_runs(
        self, async_client, test_session
    ):
        """The bead's observation: id 1 deleted and reissued, history follows.

        The predecessor's FAILED run is the one that matters — it is what makes
        a clean new target look like it has a history of problems.
        """
        test_session.add(_execution("dbas_sync_1", T_PREDECESSOR_RUN, status="failed"))
        test_session.add(_target(1, "Replacement", T_REPLACEMENT_CREATED))
        test_session.add(_execution("dbas_sync_1", T_REPLACEMENT_RUN))
        test_session.commit()

        history = await _history(async_client, "dbas_sync_1")

        assert [row["started_at"] for row in history] == [
            T_REPLACEMENT_RUN.isoformat() + "Z"
        ]
        assert all(row["status"] != "failed" for row in history)

    @pytest.mark.asyncio
    async def test_a_run_starting_before_the_target_existed_is_never_attributed(
        self, async_client, test_session
    ):
        """The invariant stated directly — no delete involved at all.

        Any row older than the live target's ``created_at`` is unattributable,
        whatever produced it: a reused id, a restored backup, a hand-edited
        row. The delete-then-recreate sequence is one way to get here.
        """
        test_session.add(_target(4, "Only Ever Target", T_REPLACEMENT_CREATED))
        test_session.add(_execution("dbas_sync_4", T_REPLACEMENT_CREATED - timedelta(microseconds=1)))
        test_session.commit()

        assert await _history(async_client, "dbas_sync_4") == []

    @pytest.mark.asyncio
    async def test_a_run_starting_at_the_moment_of_creation_is_attributed(
        self, async_client, test_session
    ):
        """The boundary is inclusive — a run cannot start before its own target.

        Without this, a target created and run in the same tick would lose its
        first run, which is the same class of wrong answer in the other
        direction.
        """
        test_session.add(_target(5, "Immediate", T_REPLACEMENT_CREATED))
        test_session.add(_execution("dbas_sync_5", T_REPLACEMENT_CREATED))
        test_session.commit()

        assert len(await _history(async_client, "dbas_sync_5")) == 1

    @pytest.mark.asyncio
    async def test_an_in_flight_predecessor_run_is_not_claimed_by_the_replacement(
        self, async_client, test_session
    ):
        """``remove_sync_target_task`` does not interrupt a run in progress.

        So a predecessor's run can COMPLETE after the replacement is created.
        Scoping on ``started_at`` is what keeps it out; scoping on
        ``completed_at`` would hand it to the replacement.
        """
        straddling = _execution("dbas_sync_6", T_REPLACEMENT_CREATED - timedelta(minutes=5))
        straddling.completed_at = T_REPLACEMENT_CREATED + timedelta(minutes=5)
        test_session.add(straddling)
        test_session.add(_target(6, "Replacement Six", T_REPLACEMENT_CREATED))
        test_session.commit()

        assert await _history(async_client, "dbas_sync_6") == []

    @pytest.mark.asyncio
    async def test_a_sync_id_with_no_live_target_has_no_history(
        self, async_client, test_session
    ):
        """Nothing owns these rows, so nothing may display them.

        Without this clause the deleted target's runs stay visible under a task
        id that the next created target will be handed.
        """
        test_session.add(_execution("dbas_sync_9", T_PREDECESSOR_RUN))
        test_session.commit()

        assert await _history(async_client, "dbas_sync_9") == []

    @pytest.mark.asyncio
    async def test_the_all_tasks_feed_applies_the_same_scope(
        self, async_client, test_session
    ):
        """The chronological feed resolves the same ids to the same names.

        Filtering only the per-task read would leave the violation reachable
        one screen over.
        """
        test_session.add(_execution("dbas_sync_1", T_PREDECESSOR_RUN, status="failed"))
        test_session.add(_target(1, "Replacement", T_REPLACEMENT_CREATED))
        test_session.add(_execution("dbas_sync_1", T_REPLACEMENT_RUN))
        test_session.add(_execution("epg_refresh", T_PREDECESSOR_RUN))
        test_session.commit()

        rows = await _all_history(async_client)

        assert sorted((r["task_id"], r["started_at"]) for r in rows) == [
            ("dbas_sync_1", T_REPLACEMENT_RUN.isoformat() + "Z"),
            ("epg_refresh", T_PREDECESSOR_RUN.isoformat() + "Z"),
        ]


class TestOrdinaryTaskHistoryIsUntouched:
    """The scope must not reach tasks whose ids are release constants."""

    @pytest.mark.asyncio
    async def test_a_normal_task_keeps_its_whole_history(
        self, async_client, test_session
    ):
        """``epg_refresh`` has no target row, and must not be emptied by that."""
        test_session.add(_execution("epg_refresh", T_PREDECESSOR_RUN))
        test_session.add(_execution("epg_refresh", T_REPLACEMENT_RUN))
        test_session.commit()

        assert len(await _history(async_client, "epg_refresh")) == 2

    @pytest.mark.asyncio
    async def test_the_legacy_shared_dbas_sync_id_is_not_swept_up(
        self, async_client, test_session
    ):
        """``dbas_sync`` (no suffix) is the pre-per-target id from bead …-5gzg5.

        It is not keyed to any target, so it is not subject to the scope. This
        is also the guard against a careless ``LIKE 'dbas_sync_%'`` — in SQL
        ``_`` is a single-character wildcard, so an unescaped pattern would
        also swallow ids this rule has no business touching.
        """
        test_session.add(_execution("dbas_sync", T_PREDECESSOR_RUN))
        test_session.commit()

        assert len(await _history(async_client, "dbas_sync")) == 1

    @pytest.mark.asyncio
    async def test_an_id_that_merely_looks_like_a_sync_id_is_not_scoped(
        self, async_client, test_session
    ):
        """``dbas_syncX`` matches an unescaped ``LIKE 'dbas_sync_%'`` and must not.

        The suffix of a real per-target id is an INTEGER; anything else is a
        different task and keeps its history.
        """
        test_session.add(_execution("dbas_syncx_report", T_PREDECESSOR_RUN))
        test_session.commit()

        assert len(await _history(async_client, "dbas_syncx_report")) == 1
