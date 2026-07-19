"""Tests for the automated-noise journal purge (enhancedchannelmanager-uliyr).

PO policy decision 2026-07-17: auto-purge journal entries older than 3 DAYS
for exactly TWO automated-noise buckets:

(a) Watch events — ``category="watch"``, ``action_type in ("start", "stop")``,
    written ONLY by ``bandwidth_tracker`` with ``user_initiated=False``.
(b) Channel Pipeline rule create/delete pairs — ``category="auto_creation"``,
    ``action_type in ("create", "delete")``, written ONLY by the rule
    create/delete endpoints in ``routers/channel_pipeline.py`` (dominated in
    practice by E2E test-rule churn like 'E2E event_sync happy path').

PO follow-up decision 2026-07-19: operator-initiated rule create/delete rows
are KEPT. The ``automated_client`` marker (nullable Boolean, stamped by the
actor-source middleware from the ``X-ECM-Automated-Client`` request header)
distinguishes them:

* ``True``  — self-declared automated client (the E2E harness) → purged
* ``False`` — a request WITHOUT the header (real UI/MCP operator)  → kept
* ``NULL``  — pre-marker legacy rows (the PO's original measured
  population; shrinks to zero) and non-HTTP internal writers → purged

so bucket (b)'s predicate is ``automated_client IS NOT FALSE``.

ALL other categories and all other ``auto_creation`` action types (update,
import, bulk_update, run_on_refresh_skipped, rollback, restore_snapshot,
circuit_breaker_reset) must be untouched — the existing manual Purge control
(``journal.purge_old_entries``) remains the tool for everything else.

The purge runs as a new daily scheduled task ``JournalNoisePurgeTask``
(task_scheduler pattern) with settings-visible config:
``retention_days`` (PO-decided default 3), ``purge_watch_events`` and
``purge_pipeline_rule_pairs`` toggles.
"""
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from models import JournalEntry
from task_scheduler import ScheduleConfig, ScheduleType


def _add_entry(
    session,
    category: str,
    action_type: str,
    age: timedelta,
    entity_name: str = "entity",
    user_initiated: bool = True,
    mutation_source: str | None = None,
    automated_client: bool | None = None,
) -> int:
    """Seed one journal row with a timestamp ``age`` in the past. Returns id."""
    entry = JournalEntry(
        timestamp=datetime.utcnow() - age,
        category=category,
        action_type=action_type,
        entity_name=entity_name,
        description=f"{category}/{action_type} {entity_name}",
        user_initiated=user_initiated,
        mutation_source=mutation_source,
        automated_client=automated_client,
    )
    session.add(entry)
    session.commit()
    return entry.id


OLD = timedelta(days=3, minutes=1)     # just past the 3-day window
FRESH = timedelta(days=2, hours=23)    # just inside the 3-day window


class TestPurgeNoiseEntriesPredicates:
    """journal.purge_noise_entries deletes ONLY the two noise buckets."""

    def test_deletes_old_watch_start_and_stop_events(self, test_session):
        from journal import purge_noise_entries

        with patch("journal.get_session", return_value=test_session):
            start_id = _add_entry(
                test_session, "watch", "start", OLD,
                entity_name="BBC Two", user_initiated=False,
            )
            stop_id = _add_entry(
                test_session, "watch", "stop", OLD,
                entity_name="BBC Two", user_initiated=False,
            )

            counts = purge_noise_entries(days=3)

        assert counts["watch_events"] == 2
        remaining = test_session.query(JournalEntry.id).all()
        remaining_ids = {r.id for r in remaining}
        assert start_id not in remaining_ids
        assert stop_id not in remaining_ids

    def test_deletes_old_unmarked_legacy_rule_pairs_any_mutation_source(self, test_session):
        """Pre-marker legacy rows (automated_client NULL) age out regardless
        of mutation_source — they are the PO's original measured population
        and the unmarked set naturally shrinks to zero."""
        from journal import purge_noise_entries

        with patch("journal.get_session", return_value=test_session):
            for source in ("ui", "mcp_ai", None):
                _add_entry(
                    test_session, "auto_creation", "create", OLD,
                    entity_name="E2E event_sync happy path (ti939.2.4)",
                    user_initiated=True, mutation_source=source,
                )
                _add_entry(
                    test_session, "auto_creation", "delete", OLD,
                    entity_name="E2E event_sync happy path (ti939.2.4)",
                    user_initiated=True, mutation_source=source,
                )

            counts = purge_noise_entries(days=3)

        assert counts["pipeline_rule_pairs"] == 6
        assert test_session.query(JournalEntry).count() == 0

    def test_deletes_old_automated_marked_rule_pairs(self, test_session):
        """Rows self-declared automated (X-ECM-Automated-Client harness
        traffic) purge once past the window."""
        from journal import purge_noise_entries

        with patch("journal.get_session", return_value=test_session):
            _add_entry(
                test_session, "auto_creation", "create", OLD,
                entity_name="E2E churn rule", automated_client=True,
            )
            _add_entry(
                test_session, "auto_creation", "delete", OLD,
                entity_name="E2E churn rule", automated_client=True,
            )

            counts = purge_noise_entries(days=3)

        assert counts["pipeline_rule_pairs"] == 2
        assert test_session.query(JournalEntry).count() == 0

    def test_keeps_operator_marked_rule_pairs(self, test_session):
        """PO follow-up decision: operator-initiated rule create/delete rows
        (automated_client=False — a request WITHOUT the automation header)
        survive no matter how old. Same shape, same age as automated rows —
        only the marker differs."""
        from journal import purge_noise_entries

        with patch("journal.get_session", return_value=test_session):
            kept_create = _add_entry(
                test_session, "auto_creation", "create", timedelta(days=400),
                entity_name="Operator rule", automated_client=False,
            )
            kept_delete = _add_entry(
                test_session, "auto_creation", "delete", timedelta(days=400),
                entity_name="Operator rule", automated_client=False,
            )
            purged = _add_entry(
                test_session, "auto_creation", "create", timedelta(days=400),
                entity_name="Automated rule", automated_client=True,
            )

            counts = purge_noise_entries(days=3)

        assert counts["pipeline_rule_pairs"] == 1
        assert test_session.get(JournalEntry, kept_create) is not None
        assert test_session.get(JournalEntry, kept_delete) is not None
        assert test_session.get(JournalEntry, purged) is None

    def test_keeps_old_rows_in_all_other_categories(self, test_session):
        """Category isolation: every non-noise category survives no matter
        how old — the manual Purge control owns those."""
        from journal import purge_noise_entries

        other_categories = [
            ("channel", "create"),
            ("channel", "delete"),
            ("epg", "delete"),
            ("m3u", "refresh"),
            ("event_sync", "merge_stream"),
            ("task", "start"),
            ("task", "complete"),
            ("settings", "update"),
            ("sync", "create"),
            ("backup", "create"),
        ]
        with patch("journal.get_session", return_value=test_session):
            kept_ids = [
                _add_entry(test_session, cat, action, timedelta(days=400))
                for cat, action in other_categories
            ]

            counts = purge_noise_entries(days=3)

        assert counts == {"watch_events": 0, "pipeline_rule_pairs": 0}
        remaining_ids = {r.id for r in test_session.query(JournalEntry.id).all()}
        assert set(kept_ids) <= remaining_ids

    def test_keeps_other_auto_creation_action_types(self, test_session):
        """Only the create/delete PAIR ages out — updates, imports, the
        run_on_refresh_skipped noise, and snapshot/rollback audit rows all
        survive (they were NOT part of the PO decision)."""
        from journal import purge_noise_entries

        preserved_actions = [
            "update", "bulk_update", "import", "run_on_refresh_skipped",
            "rollback", "restore_snapshot", "circuit_breaker_reset",
        ]
        with patch("journal.get_session", return_value=test_session):
            kept_ids = [
                _add_entry(test_session, "auto_creation", action, timedelta(days=400))
                for action in preserved_actions
            ]

            counts = purge_noise_entries(days=3)

        assert counts["pipeline_rule_pairs"] == 0
        remaining_ids = {r.id for r in test_session.query(JournalEntry.id).all()}
        assert set(kept_ids) <= remaining_ids

    def test_keeps_user_initiated_watch_rows(self, test_session):
        """Defensive guard: the bandwidth tracker always writes
        user_initiated=False. Any hypothetical operator-initiated watch row
        is NOT provably automated noise and must survive."""
        from journal import purge_noise_entries

        with patch("journal.get_session", return_value=test_session):
            kept = _add_entry(
                test_session, "watch", "start", OLD, user_initiated=True,
            )

            counts = purge_noise_entries(days=3)

        assert counts["watch_events"] == 0
        assert test_session.get(JournalEntry, kept) is not None

    def test_keeps_watch_rows_with_unknown_action_types(self, test_session):
        """A future watch action type outside start/stop is not swept."""
        from journal import purge_noise_entries

        with patch("journal.get_session", return_value=test_session):
            kept = _add_entry(
                test_session, "watch", "annotate", OLD, user_initiated=False,
            )

            purge_noise_entries(days=3)

        assert test_session.get(JournalEntry, kept) is not None

    def test_boundary_exactly_three_days(self, test_session):
        """'Older than 3 days' is strict: just past the window deletes,
        just inside the window survives — same ``timestamp < cutoff``
        semantics as the manual purge (journal.purge_old_entries)."""
        from journal import purge_noise_entries

        with patch("journal.get_session", return_value=test_session):
            past_window = _add_entry(
                test_session, "watch", "start", OLD, user_initiated=False,
            )
            inside_window = _add_entry(
                test_session, "watch", "stop", FRESH, user_initiated=False,
            )

            counts = purge_noise_entries(days=3)

        assert counts["watch_events"] == 1
        assert test_session.get(JournalEntry, past_window) is None
        assert test_session.get(JournalEntry, inside_window) is not None

    def test_watch_toggle_off_leaves_watch_untouched(self, test_session):
        from journal import purge_noise_entries

        with patch("journal.get_session", return_value=test_session):
            watch_id = _add_entry(
                test_session, "watch", "start", OLD, user_initiated=False,
            )
            pair_id = _add_entry(test_session, "auto_creation", "create", OLD)

            counts = purge_noise_entries(days=3, purge_watch_events=False)

        assert counts == {"watch_events": 0, "pipeline_rule_pairs": 1}
        assert test_session.get(JournalEntry, watch_id) is not None
        assert test_session.get(JournalEntry, pair_id) is None

    def test_pipeline_toggle_off_leaves_pairs_untouched(self, test_session):
        from journal import purge_noise_entries

        with patch("journal.get_session", return_value=test_session):
            watch_id = _add_entry(
                test_session, "watch", "start", OLD, user_initiated=False,
            )
            pair_id = _add_entry(test_session, "auto_creation", "delete", OLD)

            counts = purge_noise_entries(days=3, purge_pipeline_rule_pairs=False)

        assert counts == {"watch_events": 1, "pipeline_rule_pairs": 0}
        assert test_session.get(JournalEntry, watch_id) is None
        assert test_session.get(JournalEntry, pair_id) is not None

    def test_days_below_one_raises_value_error(self, test_session):
        """A scheduled deleter must never run with a window that would sweep
        fresh rows — days < 1 is a config error, not a bigger purge."""
        from journal import purge_noise_entries

        with patch("journal.get_session", return_value=test_session):
            _add_entry(test_session, "watch", "start", OLD, user_initiated=False)

            with pytest.raises(ValueError):
                purge_noise_entries(days=0)

        assert test_session.query(JournalEntry).count() == 1


class TestAutomatedClientCapture:
    """The actor-source middleware stamps ``automated_client`` on every
    journal row written while handling an /api/* request:

    * header ``X-ECM-Automated-Client`` present → True (self-declared)
    * no header → False (an operator client — real UI/MCP)
    * outside any request (internal writers) → NULL

    Driven through the REAL rule create endpoint — the exact write path
    bucket (b) purges — so the contextvar plumbing is exercised end-to-end.
    """

    _RULE_PAYLOAD = {
        "name": "marker capture probe",
        "description": "written by test_journal_noise_purge",
        "enabled": False,
        "conditions": [{"type": "always"}],
        "actions": [{"type": "skip"}],
    }

    @pytest.mark.asyncio
    async def test_header_marks_row_automated(self, async_client, test_session):
        resp = await async_client.post(
            "/api/channel-pipeline/rules",
            json=self._RULE_PAYLOAD,
            headers={"X-ECM-Automated-Client": "e2e"},
        )
        assert resp.status_code == 200

        row = test_session.query(JournalEntry).filter_by(
            category="auto_creation", action_type="create",
            entity_name="marker capture probe",
        ).one()
        assert row.automated_client is True

    @pytest.mark.asyncio
    async def test_no_header_marks_row_operator(self, async_client, test_session):
        """A request without the header can NEVER be classified automated —
        classification requires the client itself to self-declare."""
        resp = await async_client.post(
            "/api/channel-pipeline/rules", json=self._RULE_PAYLOAD,
        )
        assert resp.status_code == 200

        row = test_session.query(JournalEntry).filter_by(
            category="auto_creation", action_type="create",
            entity_name="marker capture probe",
        ).one()
        assert row.automated_client is False

    def test_outside_request_context_row_is_unmarked(self, test_session):
        """Internal (non-HTTP) writers leave the marker NULL."""
        with patch("journal.get_session", return_value=test_session):
            from journal import log_entry

            entry = log_entry(
                category="auto_creation",
                action_type="create",
                entity_name="internal write",
                description="no request context",
            )

        assert entry is not None
        assert entry.automated_client is None


class TestJournalNoisePurgeTaskConfig:
    """Config schema round-trips (the Settings UI reads/writes these keys)."""

    def test_default_config_matches_po_policy(self):
        from tasks.journal_noise_purge import JournalNoisePurgeTask

        config = JournalNoisePurgeTask().get_config()

        assert config == {
            "retention_days": 3,
            "purge_watch_events": True,
            "purge_pipeline_rule_pairs": True,
        }

    def test_update_config_round_trip(self):
        from tasks.journal_noise_purge import JournalNoisePurgeTask

        task = JournalNoisePurgeTask()
        task.update_config({
            "retention_days": 7,
            "purge_watch_events": False,
            "purge_pipeline_rule_pairs": False,
        })

        assert task.retention_days == 7
        assert task.purge_watch_events is False
        assert task.purge_pipeline_rule_pairs is False

    def test_partial_update_leaves_other_keys(self):
        from tasks.journal_noise_purge import JournalNoisePurgeTask

        task = JournalNoisePurgeTask()
        task.update_config({"retention_days": 14})

        assert task.retention_days == 14
        assert task.purge_watch_events is True
        assert task.purge_pipeline_rule_pairs is True

    def test_update_config_rejects_retention_below_one(self):
        from tasks.journal_noise_purge import JournalNoisePurgeTask

        task = JournalNoisePurgeTask()
        task.update_config({"retention_days": 0})

        assert task.retention_days == 3

    def test_default_schedule_is_daily_cron_0345_utc(self):
        """Daily cadence keeps worst-case entry age ~4 days against the
        3-day retention. 03:45 UTC is offset from both CleanupTask (Sunday
        02:00) and StatsV2RollupTask (daily 03:30) so the maintenance
        passes don't contend."""
        from tasks.journal_noise_purge import JournalNoisePurgeTask

        task = JournalNoisePurgeTask()

        assert task.schedule_config.schedule_type == ScheduleType.CRON
        assert task.schedule_config.cron_expression == "45 3 * * *"

    def test_explicit_schedule_config_preserved(self):
        """An operator-persisted schedule must not be clobbered by the
        default (same contract as CleanupTask / bd-ygoqr)."""
        from tasks.journal_noise_purge import JournalNoisePurgeTask

        operator_config = ScheduleConfig(
            schedule_type=ScheduleType.MANUAL, cron_expression="",
        )
        task = JournalNoisePurgeTask(operator_config)

        assert task.schedule_config.schedule_type == ScheduleType.MANUAL

    def test_task_is_registered(self):
        """Importing the tasks package must register the task so it shows
        up in /api/tasks and the Scheduled Tasks settings UI."""
        import tasks  # noqa: F401 — import side effect registers tasks
        from tasks.journal_noise_purge import JournalNoisePurgeTask
        from task_registry import get_registry

        assert JournalNoisePurgeTask.task_id == "journal_noise_purge"
        assert get_registry().is_registered("journal_noise_purge")


class TestJournalNoisePurgeTaskExecute:
    @pytest.mark.asyncio
    async def test_execute_purges_only_noise(self, test_session):
        from tasks.journal_noise_purge import JournalNoisePurgeTask

        with patch("journal.get_session", return_value=test_session):
            _add_entry(test_session, "watch", "start", OLD, user_initiated=False)
            _add_entry(test_session, "watch", "stop", OLD, user_initiated=False)
            _add_entry(test_session, "auto_creation", "create", OLD)
            _add_entry(test_session, "auto_creation", "delete", OLD)
            fresh_watch = _add_entry(
                test_session, "watch", "start", FRESH, user_initiated=False,
            )
            other_category = _add_entry(
                test_session, "channel", "delete", timedelta(days=400),
            )

            result = await JournalNoisePurgeTask().execute()

        assert result.success is True
        assert result.details["deleted"] == {
            "watch_events": 2,
            "pipeline_rule_pairs": 2,
        }
        assert result.details["retention_days"] == 3
        remaining_ids = {r.id for r in test_session.query(JournalEntry.id).all()}
        assert remaining_ids == {fresh_watch, other_category}

    @pytest.mark.asyncio
    async def test_execute_reports_failure_on_purge_error(self, test_session):
        from tasks.journal_noise_purge import JournalNoisePurgeTask

        with patch(
            "tasks.journal_noise_purge.journal.purge_noise_entries",
            side_effect=RuntimeError("boom"),
        ):
            result = await JournalNoisePurgeTask().execute()

        assert result.success is False
        assert "boom" in (result.error or "")
