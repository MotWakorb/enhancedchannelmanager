"""TaskRegistry must persist task-specific config and rehydrate it on startup
(enhancedchannelmanager-gjb01 review blocker).

Regression: the UI save path (PATCH /api/tasks/{id} → update_task_config)
applied ``task_config`` only to the LIVE instance; ``_save_task_to_db`` never
wrote ``ScheduledTask.config`` and ``sync_from_database`` never read it, so
every task's settings-surface config silently reverted to code defaults on
container restart. Concrete harm for the Journal Noise Purge task: an
operator disables a purge bucket → restart → the toggle re-enables → the
next run deletes rows the operator explicitly chose to retain.

Contract proven here:

* Persist: ``_save_task_to_db`` writes ``instance.get_config()`` as JSON
  into ``ScheduledTask.config`` (both the update path and row creation).
* Hydrate: ``sync_from_database`` applies a stored non-null config onto the
  reconstructed instance through the SAME ``update_config`` path the live
  update uses — one apply path, merge-over-defaults semantics (each task's
  ``update_config`` only touches keys present in the dict).
* Back-compat: ``config=None`` (every pre-existing row) → pure code
  defaults, exactly the old behavior; a stored config missing a newer key
  falls back to that key's code default; malformed JSON is ignored.
* Opt-out: tasks whose config surface is per-invocation/ephemeral or
  externally stored declare ``persist_config = False`` and are neither
  persisted nor hydrated (dbas_restore/dbas_sync arming flags must re-disarm
  on restart; m3u_digest's config lives in its own settings table).
"""
import json
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from models import JournalEntry, ScheduledTask
from task_registry import TaskRegistry
from tasks.journal_noise_purge import JournalNoisePurgeTask


def _make_registry(monkeypatch, test_session) -> TaskRegistry:
    monkeypatch.setattr("task_registry.get_session", lambda: test_session)
    reg = TaskRegistry()
    reg.register(JournalNoisePurgeTask)
    return reg


def _stored_config(test_session) -> dict | None:
    row = test_session.query(ScheduledTask).filter_by(
        task_id="journal_noise_purge"
    ).first()
    assert row is not None
    return json.loads(row.config) if row.config else None


class TestPersist:
    def test_update_task_config_persists_config_json(self, test_session, monkeypatch):
        """The operator save path writes the full config surface to the DB."""
        reg = _make_registry(monkeypatch, test_session)
        reg.sync_from_database()

        reg.update_task_config(
            "journal_noise_purge",
            task_config={
                "purge_run_on_refresh_skipped": False,
                "purge_task_start_complete": False,
            },
        )

        stored = _stored_config(test_session)
        assert stored is not None
        assert stored["purge_run_on_refresh_skipped"] is False
        assert stored["purge_task_start_complete"] is False
        # Untouched keys are persisted at their current (default) values so
        # the stored surface is complete, not a delta.
        assert stored["retention_days"] == 3
        assert stored["purge_watch_events"] is True
        assert stored["purge_pipeline_rule_pairs"] is True

    def test_fresh_row_creation_persists_default_config(self, test_session, monkeypatch):
        """A task first saved to the DB (no existing row) stores its defaults."""
        reg = _make_registry(monkeypatch, test_session)

        reg.sync_from_database()  # no row yet → created with defaults

        stored = _stored_config(test_session)
        assert stored == JournalNoisePurgeTask().get_config()


class TestRestartRoundtrip:
    @pytest.mark.asyncio
    async def test_disabled_toggles_survive_restart_and_gate_the_purge(
        self, test_session, monkeypatch
    ):
        """The review's required regression: toggles OFF → sync → simulated
        restart (fresh registry reconstructed from the DB) → toggles STILL
        OFF on the reconstructed instance AND a purge run with that instance
        does not touch the disabled buckets."""
        reg_a = _make_registry(monkeypatch, test_session)
        reg_a.sync_from_database()
        reg_a.update_task_config(
            "journal_noise_purge",
            task_config={
                "purge_run_on_refresh_skipped": False,
                "purge_task_start_complete": False,
            },
        )

        # Simulated restart: a brand-new registry, same DB.
        reg_b = _make_registry(monkeypatch, test_session)
        reg_b.sync_from_database()
        inst = reg_b.get_task_instance("journal_noise_purge")

        assert inst is not reg_a.get_task_instance("journal_noise_purge")
        assert inst.purge_run_on_refresh_skipped is False
        assert inst.purge_task_start_complete is False
        # The other surface keys still carry their defaults.
        assert inst.retention_days == 3
        assert inst.purge_watch_events is True
        assert inst.purge_pipeline_rule_pairs is True

        # And the reconstructed config actually gates the delete: seed rows
        # in both disabled buckets (old enough to purge if the toggles had
        # reset) plus one enabled-bucket row proving the purge itself ran.
        old = timedelta(days=30)
        kept_skip = JournalEntry(
            timestamp=datetime.utcnow() - old,
            category="auto_creation", action_type="run_on_refresh_skipped",
            entity_name="Auto-Creation", description="d", user_initiated=False,
        )
        kept_task = JournalEntry(
            timestamp=datetime.utcnow() - old,
            category="task", action_type="complete",
            entity_name="M3U Refresh", description="d", user_initiated=False,
        )
        purged_watch = JournalEntry(
            timestamp=datetime.utcnow() - old,
            category="watch", action_type="start",
            entity_name="BBC Two", description="d", user_initiated=False,
        )
        test_session.add_all([kept_skip, kept_task, purged_watch])
        test_session.commit()
        kept_ids = {kept_skip.id, kept_task.id}
        purged_id = purged_watch.id

        with patch("journal.get_session", return_value=test_session):
            result = await inst.execute()

        assert result.success is True
        assert result.details["deleted"]["run_on_refresh_skipped"] == 0
        assert result.details["deleted"]["task_start_complete"] == 0
        assert result.details["deleted"]["watch_events"] == 1
        remaining = {r.id for r in test_session.query(JournalEntry.id).all()}
        assert kept_ids <= remaining
        assert purged_id not in remaining

    def test_legacy_row_with_null_config_gets_pure_defaults(
        self, test_session, monkeypatch
    ):
        """Back-compat: every pre-existing row has config=None — the
        reconstructed instance must carry pure code defaults (today's exact
        behavior), with no error and no write requirement."""
        from tests.fixtures.factories import create_scheduled_task

        create_scheduled_task(
            test_session,
            task_id="journal_noise_purge",
            task_name="Journal Noise Purge",
            description="x",
            enabled=True,
            schedule_type="cron",
            cron_expression="45 3 * * *",
            config=None,
        )
        reg = _make_registry(monkeypatch, test_session)

        reg.sync_from_database()
        inst = reg.get_task_instance("journal_noise_purge")

        assert inst.get_config() == JournalNoisePurgeTask().get_config()

    def test_stored_config_missing_keys_merges_over_defaults(
        self, test_session, monkeypatch
    ):
        """Merge semantics: a stored config written before a newer key
        existed applies the keys it has and falls back to the code default
        for the rest — never a wholesale replace."""
        from tests.fixtures.factories import create_scheduled_task

        create_scheduled_task(
            test_session,
            task_id="journal_noise_purge",
            task_name="Journal Noise Purge",
            description="x",
            enabled=True,
            schedule_type="cron",
            cron_expression="45 3 * * *",
            config={"retention_days": 7, "purge_watch_events": False},
        )
        reg = _make_registry(monkeypatch, test_session)

        reg.sync_from_database()
        inst = reg.get_task_instance("journal_noise_purge")

        assert inst.retention_days == 7
        assert inst.purge_watch_events is False
        # Keys absent from the stored config keep their code defaults.
        assert inst.purge_pipeline_rule_pairs is True
        assert inst.purge_run_on_refresh_skipped is True
        assert inst.purge_task_start_complete is True

    def test_malformed_stored_config_is_ignored(self, test_session, monkeypatch):
        """A corrupt config blob must not break startup — the task comes up
        on code defaults."""
        from tests.fixtures.factories import create_scheduled_task

        row = create_scheduled_task(
            test_session,
            task_id="journal_noise_purge",
            task_name="Journal Noise Purge",
            description="x",
            enabled=True,
            schedule_type="cron",
            cron_expression="45 3 * * *",
        )
        row.config = "{not valid json"
        test_session.commit()
        reg = _make_registry(monkeypatch, test_session)

        reg.sync_from_database()
        inst = reg.get_task_instance("journal_noise_purge")

        assert inst is not None
        assert inst.get_config() == JournalNoisePurgeTask().get_config()


class TestPersistConfigOptOut:
    """Tasks with ``persist_config = False`` are neither persisted nor
    hydrated: their config surface is per-invocation state (dbas_restore /
    dbas_sync destructive arming flags must re-disarm on restart) or lives
    in its own store (m3u_digest settings table)."""

    def test_opted_out_tasks_declare_persist_config_false(self):
        from tasks.dbas_restore import DbasRestoreTask
        from tasks.dbas_sync import DbasSyncTask
        from tasks.m3u_digest import M3UDigestTask

        assert DbasRestoreTask.persist_config is False
        assert DbasSyncTask.persist_config is False
        assert M3UDigestTask.persist_config is False
        # The default on the base class (and thus on every other task) is
        # to persist — durable operator settings.
        assert JournalNoisePurgeTask.persist_config is True

    def test_opted_out_task_config_never_written(self, test_session, monkeypatch):
        from tasks.dbas_sync import DbasSyncTask

        monkeypatch.setattr("task_registry.get_session", lambda: test_session)
        reg = TaskRegistry()
        reg.register(DbasSyncTask)
        reg.sync_from_database()

        reg.update_task_config("dbas_sync", task_config={"confirm_apply": True})

        row = test_session.query(ScheduledTask).filter_by(task_id="dbas_sync").first()
        assert row is not None
        assert row.config is None
        # The live instance still took the update (behavior unchanged).
        assert reg.get_task_instance("dbas_sync").confirm_apply is True

    def test_opted_out_task_config_never_hydrated(self, test_session, monkeypatch):
        """Even a hand-planted stored config must not re-arm the flag on
        restart — the fail-safe direction is disarmed."""
        from tests.fixtures.factories import create_scheduled_task
        from tasks.dbas_sync import DbasSyncTask

        create_scheduled_task(
            test_session,
            task_id="dbas_sync",
            task_name="DBAS Sync",
            description="x",
            enabled=True,
            schedule_type="manual",
            config={"confirm_apply": True, "sync_target_id": 1},
        )
        monkeypatch.setattr("task_registry.get_session", lambda: test_session)
        reg = TaskRegistry()
        reg.register(DbasSyncTask)

        reg.sync_from_database()
        inst = reg.get_task_instance("dbas_sync")

        assert inst.confirm_apply is False
        assert inst.sync_target_id is None
