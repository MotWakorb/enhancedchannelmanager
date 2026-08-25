"""Tests for the DBAS restore task (bead enhancedchannelmanager-o8tbv).

Covers ``tasks.dbas_restore.DbasRestoreTask`` — the async, progress-emitting
restore that ties .17 validation -> artifact decode -> .16/.18 orchestrator.

Behavioural contracts under test:
  * validate-before-mutate: a manifest that fails .17 is refused BEFORE any
    importer runs (the orchestrator is never called);
  * dry-run is default-ON: confirm_apply omitted -> run_dry_run; confirm_apply
    True -> run_restore(confirm_apply=True);
  * _set_progress fires per stage with current_item keys aligned to
    restoreStages.ts;
  * the temp artifact is cleaned up on success AND failure;
  * the RestoreReport is returned in TaskResult.details for the .20 summary.

The Dispatcharr client and the orchestrator entry points are mocked at module
level so no live upstream / no real importer runs.
"""
from __future__ import annotations

import base64
import io
import json
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from dbas.restore_contracts import (
    EntityCategoryReport,
    EntityType,
    RestoreOutcome,
    RestoreReport,
)
from tasks.dbas_restore import _STAGE_KEYS, DbasRestoreTask
from task_scheduler import ScheduleConfig, ScheduleType


_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def _write_artifact(tmp_path: Path, *, schema_version=1, newer=False) -> Path:
    """Write a real, integrity-valid new-format artifact to a temp file."""
    import hashlib
    import yaml

    cat = {
        "ecm_export": {"version": "0.17.6-test", "sections_included": ["m3u_accounts"]},
        "dispatcharr": {"m3u_accounts": [{"id": 1, "name": "Provider A"}]},
    }
    members = {
        "categories/m3u_accounts.yaml": yaml.dump(cat).encode("utf-8"),
        "binary/logos/espn.png": _PNG_BYTES,
    }
    file_hashes = {p: hashlib.sha256(b).hexdigest() for p, b in members.items()}
    sv = schema_version + 1 if newer else schema_version
    manifest = {
        "schema_version": sv,
        "app_version": "0.17.6-test",
        "files": [{"path": p, "sha256": h} for p, h in sorted(file_hashes.items())],
    }
    art = tmp_path / "artifact.zip"
    with zipfile.ZipFile(art, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for path, blob in members.items():
            zf.writestr(path, blob)
    return art


def _make_task(artifact_path: Path, *, confirm_apply=False) -> DbasRestoreTask:
    task = DbasRestoreTask(ScheduleConfig(schedule_type=ScheduleType.MANUAL))
    task.update_config(
        {"artifact_path": str(artifact_path), "confirm_apply": confirm_apply}
    )
    return task


def _dry_run_report() -> RestoreReport:
    report = RestoreReport(is_dry_run=True)
    cat = report.category(EntityType.M3U_ACCOUNT)
    cat.would_create = 1
    return report


def _apply_report(outcome=RestoreOutcome.SUCCESS) -> RestoreReport:
    report = RestoreReport(is_dry_run=False, outcome=outcome)
    cat = report.category(EntityType.M3U_ACCOUNT)
    cat.created = 1
    return report


@pytest.mark.asyncio
class TestDryRunDefault:
    async def test_dry_run_is_default(self, tmp_path):
        art = _write_artifact(tmp_path)
        task = _make_task(art, confirm_apply=False)

        dry = AsyncMock(return_value=_dry_run_report())
        apply = AsyncMock(return_value=_apply_report())
        with patch("dbas.restore_orchestrator.run_dry_run", dry), \
             patch("dbas.restore_orchestrator.run_restore", apply), \
             patch("dispatcharr_client.get_client", return_value=AsyncMock()):
            result = await task.execute()

        dry.assert_awaited_once()
        apply.assert_not_awaited()
        assert result.success is True
        assert result.details["is_dry_run"] is True
        assert "restore_report" in result.details

    async def test_confirm_apply_runs_apply(self, tmp_path):
        art = _write_artifact(tmp_path)
        task = _make_task(art, confirm_apply=True)

        dry = AsyncMock(return_value=_dry_run_report())
        apply = AsyncMock(return_value=_apply_report())
        with patch("dbas.restore_orchestrator.run_dry_run", dry), \
             patch("dbas.restore_orchestrator.run_restore", apply), \
             patch("dispatcharr_client.get_client", return_value=AsyncMock()):
            result = await task.execute()

        apply.assert_awaited_once()
        dry.assert_not_awaited()
        # confirm_apply=True is threaded into run_restore.
        assert apply.await_args.kwargs["confirm_apply"] is True
        assert result.details["is_dry_run"] is False

    async def test_logo_payload_is_loaded_lazily_while_archive_is_open(self, tmp_path):
        art = _write_artifact(tmp_path)
        task = _make_task(art, confirm_apply=False)
        loaded = []
        captured = []

        async def dry_run(*, plan, logo_content_provider, **_kwargs):
            record = plan.category(EntityType.LOGO).entities[0]
            assert "content_b64" not in record
            loaded.append(base64.b64decode(await logo_content_provider(record)))
            captured.append((logo_content_provider, record))
            return _dry_run_report()

        with patch(
            "dbas.restore_orchestrator.run_dry_run",
            AsyncMock(side_effect=dry_run),
        ), patch("dispatcharr_client.get_client", return_value=AsyncMock()):
            result = await task.execute()

        assert result.success is True
        assert loaded == [_PNG_BYTES]
        with pytest.raises(ValueError, match="already closed"):
            await captured[0][0](captured[0][1])

    async def test_archive_closes_when_orchestration_raises(self, tmp_path):
        art = _write_artifact(tmp_path)
        task = _make_task(art, confirm_apply=False)
        captured = []

        async def failing_dry_run(*, plan, logo_content_provider, **_kwargs):
            captured.append(
                (logo_content_provider, plan.category(EntityType.LOGO).entities[0])
            )
            raise RuntimeError("boom")

        with patch(
            "dbas.restore_orchestrator.run_dry_run",
            AsyncMock(side_effect=failing_dry_run),
        ), patch("dispatcharr_client.get_client", return_value=AsyncMock()):
            result = await task.execute()

        assert result.success is False
        with pytest.raises(ValueError, match="already closed"):
            await captured[0][0](captured[0][1])

    async def test_apply_states_the_archive_restores_full_ladder_policy(self, tmp_path):
        """The archive restore STATES ``allow_fuzzy_stream_match=True``.

        Bead ``…-efvyg``. Restore and cross-instance sync share this
        orchestrator, and they do NOT share a stream-matching policy: sync is
        floored at Tier-3 by ruling 1b, while a restore's post-create rebind is
        where essentially all of its matching happens (the destination has no
        provider streams at channel-import time), so flooring it would strand
        restored channels on placeholders — the ``…-2o0cz`` P0. This pins the
        value at the CALL SITE, because a value equal to the signature default
        is exactly the kind that gets quietly dropped and then quietly changed.
        """
        art = _write_artifact(tmp_path)
        task = _make_task(art, confirm_apply=True)

        apply = AsyncMock(return_value=_apply_report())
        with patch("dbas.restore_orchestrator.run_restore", apply), \
             patch("dispatcharr_client.get_client", return_value=AsyncMock()):
            await task.execute()

        assert apply.await_args.kwargs["allow_fuzzy_stream_match"] is True

    async def test_apply_failure_outcome_marks_task_failed(self, tmp_path):
        art = _write_artifact(tmp_path)
        task = _make_task(art, confirm_apply=True)
        apply = AsyncMock(return_value=_apply_report(RestoreOutcome.FAILED_ROLLBACK_INCOMPLETE))
        with patch("dbas.restore_orchestrator.run_restore", apply), \
             patch("dispatcharr_client.get_client", return_value=AsyncMock()):
            result = await task.execute()
        # A rolled-back / incomplete apply is NOT a success.
        assert result.success is False


@pytest.mark.asyncio
class TestValidateBeforeMutate:
    async def test_newer_schema_refused_before_orchestrator(self, tmp_path):
        art = _write_artifact(tmp_path, newer=True)  # schema_version too new
        task = _make_task(art, confirm_apply=True)

        dry = AsyncMock(return_value=_dry_run_report())
        apply = AsyncMock(return_value=_apply_report())
        with patch("dbas.restore_orchestrator.run_dry_run", dry), \
             patch("dbas.restore_orchestrator.run_restore", apply), \
             patch("dispatcharr_client.get_client", return_value=AsyncMock()):
            result = await task.execute()

        # Refused by .17 BEFORE any importer — neither orchestrator entry ran.
        dry.assert_not_awaited()
        apply.assert_not_awaited()
        assert result.success is False

    async def test_missing_artifact_fails_cleanly(self, tmp_path):
        task = _make_task(tmp_path / "does-not-exist.zip")
        with patch("dispatcharr_client.get_client", return_value=AsyncMock()):
            result = await task.execute()
        assert result.success is False
        assert "missing" in result.message.lower()

    async def test_no_artifact_path_fails(self):
        task = DbasRestoreTask(ScheduleConfig(schedule_type=ScheduleType.MANUAL))
        result = await task.execute()
        assert result.success is False


@pytest.mark.asyncio
class TestProgressStages:
    async def test_emits_stage_keys_aligned_to_restore_stages(self, tmp_path):
        art = _write_artifact(tmp_path)
        task = _make_task(art, confirm_apply=False)

        emitted: list[str] = []
        orig = task._set_progress

        def _spy(*args, **kwargs):
            ci = kwargs.get("current_item")
            if ci:
                emitted.append(ci)
            return orig(*args, **kwargs)

        with patch.object(task, "_set_progress", side_effect=_spy), \
             patch("dbas.restore_orchestrator.run_dry_run", AsyncMock(return_value=_dry_run_report())), \
             patch("dispatcharr_client.get_client", return_value=AsyncMock()):
            await task.execute()

        # preflight first, finalize last, every emitted key is a known stage key.
        assert emitted[0] == "preflight"
        assert emitted[-1] == "finalize"
        assert set(emitted).issubset(set(_STAGE_KEYS))
        # the category stages fired in order
        assert "m3u_account" in emitted


@pytest.mark.asyncio
class TestTempCleanup:
    async def test_artifact_removed_on_success(self, tmp_path):
        art = _write_artifact(tmp_path)
        task = _make_task(art, confirm_apply=False)
        with patch("dbas.restore_orchestrator.run_dry_run", AsyncMock(return_value=_dry_run_report())), \
             patch("dispatcharr_client.get_client", return_value=AsyncMock()):
            await task.execute()
        assert not art.exists()

    async def test_artifact_removed_on_failure(self, tmp_path):
        art = _write_artifact(tmp_path)
        task = _make_task(art, confirm_apply=False)
        with patch("dbas.restore_orchestrator.run_dry_run",
                   AsyncMock(side_effect=RuntimeError("boom"))), \
             patch("dispatcharr_client.get_client", return_value=AsyncMock()):
            result = await task.execute()
        assert result.success is False
        assert not art.exists()

    async def test_cleanup_disabled_keeps_artifact(self, tmp_path):
        art = _write_artifact(tmp_path)
        task = _make_task(art, confirm_apply=False)
        task.update_config({"cleanup_artifact": False})
        with patch("dbas.restore_orchestrator.run_dry_run", AsyncMock(return_value=_dry_run_report())), \
             patch("dispatcharr_client.get_client", return_value=AsyncMock()):
            await task.execute()
        assert art.exists()


# ---------------------------------------------------------------------------
# enhancedchannelmanager-tyei5 — dry-run failed/conflict counts must be
# visible in BOTH the summary message and the numeric TaskResult badges,
# mirroring the pattern dbas_sync.py already carries (SyncCounts /
# _counts_from_report / the "N conflict(s)" summary phrase).
# ---------------------------------------------------------------------------


def _dry_run_report_with_categories() -> RestoreReport:
    """A dry-run plan across 2 categories with a mix of would_create/
    would_update/would_skip — the real per-entity scope a preview should
    surface, NOT the stage-count ('13 of 13') task-level placeholder."""
    report = RestoreReport(is_dry_run=True)
    report.categories = [
        EntityCategoryReport(
            entity_type=EntityType.CHANNEL,
            would_create=10, would_update=5, would_skip=2,
        ),
        EntityCategoryReport(
            entity_type=EntityType.CHANNEL_GROUP,
            would_create=3, would_update=1, would_skip=1,
        ),
    ]
    return report


def _dry_run_report_with_conflict() -> RestoreReport:
    """A dry-run plan where ONE category surfaces a per-item CONFLICT.

    Mirrors ``EntityCategoryReport.failed``'s docstring: it is NOT
    exclusively an apply-time signal — an importer MAY populate it on a
    dry-run too when the failure is a fact about the source data (not about
    whether the run applied). This fixture proves a dry-run report CAN carry
    a nonzero ``failed`` alongside normal ``would_create`` counts elsewhere.
    """
    report = RestoreReport(is_dry_run=True)
    report.categories = [
        EntityCategoryReport(
            entity_type=EntityType.CHANNEL_GROUP,
            would_create=1, would_update=0, would_skip=0, failed=1,
        ),
        EntityCategoryReport(
            entity_type=EntityType.M3U_ACCOUNT,
            would_create=1, would_update=0, would_skip=0,
        ),
    ]
    return report


def _apply_success_report_with_categories() -> RestoreReport:
    """A clean SUCCESS apply across 2 categories with created/updated/skipped
    (no failures — a clean success has zero failed entities)."""
    report = RestoreReport(is_dry_run=False, outcome=RestoreOutcome.SUCCESS)
    report.categories = [
        EntityCategoryReport(
            entity_type=EntityType.CHANNEL,
            created=8, updated=4, skipped=1, failed=0,
        ),
        EntityCategoryReport(
            entity_type=EntityType.M3U_ACCOUNT,
            created=2, updated=0, skipped=0, failed=0,
        ),
    ]
    return report


def _apply_partial_report_with_categories() -> RestoreReport:
    """A mixed/rolled-back apply with real failures across categories."""
    report = RestoreReport(
        is_dry_run=False, outcome=RestoreOutcome.PARTIAL_FAILED_ROLLED_BACK
    )
    report.categories = [
        EntityCategoryReport(
            entity_type=EntityType.CHANNEL,
            created=5, updated=2, skipped=1, failed=3,
        ),
        EntityCategoryReport(
            entity_type=EntityType.EPG_SOURCE,
            created=1, updated=1, skipped=0, failed=0,
        ),
    ]
    return report


@pytest.mark.asyncio
class TestSummaryMessageFailedVisibility:
    """``_summary_message``'s dry-run branch must surface a failed/conflict
    count the same way the apply branch (and dbas_sync's dry-run branch)
    already do."""

    async def test_dry_run_summary_unchanged_when_no_failures(self, tmp_path):
        art = _write_artifact(tmp_path)
        task = _make_task(art, confirm_apply=False)
        with patch(
            "dbas.restore_orchestrator.run_dry_run",
            AsyncMock(return_value=_dry_run_report_with_categories()),
        ), patch("dispatcharr_client.get_client", return_value=AsyncMock()):
            result = await task.execute()

        assert "would create 13, update 6, skip 3" in result.message
        # No categories failed — the conflict phrase reports 0, matching
        # dbas_sync's unconditional "N conflict(s)" phrasing exactly.
        assert "0 conflict(s)" in result.message

    async def test_dry_run_summary_mentions_conflict_count_when_failed(self, tmp_path):
        art = _write_artifact(tmp_path)
        task = _make_task(art, confirm_apply=False)
        with patch(
            "dbas.restore_orchestrator.run_dry_run",
            AsyncMock(return_value=_dry_run_report_with_conflict()),
        ), patch("dispatcharr_client.get_client", return_value=AsyncMock()):
            result = await task.execute()

        # Mirrors dbas_sync's "N conflict(s)" phrasing so a WOULD-FAIL dry-run
        # is visible in the one-line summary, not just the rich report UI.
        assert "1 conflict(s)" in result.message


@pytest.mark.asyncio
class TestTaskResultCountsWiring:
    """``TaskResult.failed_count``/``skipped_count`` must reflect the REAL
    per-category sums so ``task_engine.py``'s ``elif result.failed_count > 0``
    ("Completed with Warnings") branch is reachable for DBAS restores."""

    async def test_dry_run_counts_reflect_real_category_sums(self, tmp_path):
        art = _write_artifact(tmp_path)
        task = _make_task(art, confirm_apply=False)
        with patch(
            "dbas.restore_orchestrator.run_dry_run",
            AsyncMock(return_value=_dry_run_report_with_categories()),
        ), patch("dispatcharr_client.get_client", return_value=AsyncMock()):
            result = await task.execute()

        assert result.success is True
        # would_create (10+3) + would_update (5+1) = 19
        assert result.success_count == 19
        # would_skip (2+1) = 3
        assert result.skipped_count == 3
        assert result.failed_count == 0
        assert result.total_items == 22

    async def test_dry_run_counts_surface_conflict_failed_count(self, tmp_path):
        art = _write_artifact(tmp_path)
        task = _make_task(art, confirm_apply=False)
        with patch(
            "dbas.restore_orchestrator.run_dry_run",
            AsyncMock(return_value=_dry_run_report_with_conflict()),
        ), patch("dispatcharr_client.get_client", return_value=AsyncMock()):
            result = await task.execute()

        assert result.failed_count == 1
        assert result.success_count == 2
        assert result.skipped_count == 0
        assert result.total_items == 3

    async def test_apply_success_counts_reflect_real_category_sums(self, tmp_path):
        art = _write_artifact(tmp_path)
        task = _make_task(art, confirm_apply=True)
        with patch(
            "dbas.restore_orchestrator.run_restore",
            AsyncMock(return_value=_apply_success_report_with_categories()),
        ), patch("dispatcharr_client.get_client", return_value=AsyncMock()):
            result = await task.execute()

        assert result.success is True
        # created (8+2) + updated (4+0) = 14
        assert result.success_count == 14
        assert result.skipped_count == 1
        assert result.failed_count == 0
        assert result.total_items == 15

    async def test_apply_partial_counts_reflect_real_failures(self, tmp_path):
        art = _write_artifact(tmp_path)
        task = _make_task(art, confirm_apply=True)
        with patch(
            "dbas.restore_orchestrator.run_restore",
            AsyncMock(return_value=_apply_partial_report_with_categories()),
        ), patch("dispatcharr_client.get_client", return_value=AsyncMock()):
            result = await task.execute()

        assert result.success is False
        # created (5+1) + updated (2+1) = 9
        assert result.success_count == 9
        assert result.skipped_count == 1
        # failed (3+0) = 3 — the REAL failure count, not the old stage-count.
        assert result.failed_count == 3
        assert result.total_items == 13


@pytest.mark.asyncio
class TestSummaryMessageCredentialReentry:
    """The one-line task message is the ONLY surface an operator who restored
    via MCP or reads task history ever sees. A restore from a redacted artifact
    reports a clean SUCCESS with perfect counts while every restored account is
    unauthenticated, so the action item has to be in the message too (…-6pilh).
    """

    async def test_message_names_the_accounts_needing_credentials(self, tmp_path):
        from dbas.restore_contracts import EntityType

        report = _dry_run_report_with_categories()
        report.record_credential_reentry(
            EntityType.M3U_ACCOUNT, "Infinity", ["password"], source_export_id=5
        )

        art = _write_artifact(tmp_path)
        task = _make_task(art, confirm_apply=False)
        with patch(
            "dbas.restore_orchestrator.run_dry_run", AsyncMock(return_value=report)
        ), patch("dispatcharr_client.get_client", return_value=AsyncMock()):
            result = await task.execute()

        assert "1 account(s) need credentials re-entered" in result.message

    async def test_message_is_unchanged_when_credentials_came_through(self, tmp_path):
        art = _write_artifact(tmp_path)
        task = _make_task(art, confirm_apply=False)
        with patch(
            "dbas.restore_orchestrator.run_dry_run",
            AsyncMock(return_value=_dry_run_report_with_categories()),
        ), patch("dispatcharr_client.get_client", return_value=AsyncMock()):
            result = await task.execute()

        assert "credentials re-entered" not in result.message


# ---------------------------------------------------------------------------
# channel_reattach_mode threading (bead dfkbn, PR review W1)
#
# The mode has to reach BOTH orchestrator entry points. A preview run under a
# different mode from the apply that follows it mispredicts exactly the number
# the mode exists to control, which is the dgnms failure shape.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestChannelReattachMode:
    async def test_default_is_preserve_when_the_config_omits_it(self, tmp_path):
        from dbas.restore_contracts import ChannelReattachMode

        art = _write_artifact(tmp_path)
        task = _make_task(art, confirm_apply=True)

        apply = AsyncMock(return_value=_apply_report())
        with patch("dbas.restore_orchestrator.run_dry_run", AsyncMock()), \
             patch("dbas.restore_orchestrator.run_restore", apply), \
             patch("dispatcharr_client.get_client", return_value=AsyncMock()):
            await task.execute()

        assert (
            apply.await_args.kwargs["channel_reattach_mode"]
            is ChannelReattachMode.PRESERVE
        )

    async def test_the_dry_run_gets_the_SAME_mode_the_apply_would(self, tmp_path):
        from dbas.restore_contracts import ChannelReattachMode

        art = _write_artifact(tmp_path)
        task = _make_task(art, confirm_apply=False)
        task.update_config({"channel_reattach_mode": "overwrite"})

        dry = AsyncMock(return_value=_dry_run_report())
        with patch("dbas.restore_orchestrator.run_dry_run", dry), \
             patch("dbas.restore_orchestrator.run_restore", AsyncMock()), \
             patch("dispatcharr_client.get_client", return_value=AsyncMock()):
            await task.execute()

        assert (
            dry.await_args.kwargs["channel_reattach_mode"]
            is ChannelReattachMode.OVERWRITE
        )

    async def test_an_unrecognised_mode_falls_back_to_preserve(self, tmp_path):
        from dbas.restore_contracts import ChannelReattachMode

        art = _write_artifact(tmp_path)
        task = _make_task(art, confirm_apply=True)
        task.update_config({"channel_reattach_mode": "obliterate"})

        apply = AsyncMock(return_value=_apply_report())
        with patch("dbas.restore_orchestrator.run_dry_run", AsyncMock()), \
             patch("dbas.restore_orchestrator.run_restore", apply), \
             patch("dispatcharr_client.get_client", return_value=AsyncMock()):
            await task.execute()

        assert (
            apply.await_args.kwargs["channel_reattach_mode"]
            is ChannelReattachMode.PRESERVE
        )

    async def test_the_mode_is_visible_in_get_config(self, tmp_path):
        art = _write_artifact(tmp_path)
        task = _make_task(art, confirm_apply=False)
        task.update_config({"channel_reattach_mode": "overwrite"})
        assert task.get_config()["channel_reattach_mode"] == "overwrite"
        # ...and the passphrase still is NOT (it must never be persisted).
        assert "passphrase" not in task.get_config()


# ---------------------------------------------------------------------------
# Per-run transients on a SINGLETON task (PR review round 2, finding 5)
#
# The cytzj shape, one bead old. get_task_instance() returns a singleton and
# update_config only assigns when a key is PRESENT, so without a reset a bare
# re-run inherits the previous run's mode — a path where the option is absent
# and does NOT resolve to preserve.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestPerRunTransientsAreReset:
    async def test_a_bare_rerun_does_not_inherit_the_previous_mode(self, tmp_path):
        from dbas.restore_contracts import ChannelReattachMode

        art = _write_artifact(tmp_path)
        task = _make_task(art, confirm_apply=True)
        task.update_config({"channel_reattach_mode": "overwrite"})

        apply = AsyncMock(return_value=_apply_report())
        with patch("dbas.restore_orchestrator.run_dry_run", AsyncMock()), \
             patch("dbas.restore_orchestrator.run_restore", apply), \
             patch("dispatcharr_client.get_client", return_value=AsyncMock()):
            await task.execute()
        assert (
            apply.await_args.kwargs["channel_reattach_mode"]
            is ChannelReattachMode.OVERWRITE
        )

        # A SECOND run configured with only an artifact path — the shape a bare
        # POST /api/tasks/dbas_restore/run produces against the same singleton.
        art2 = _write_artifact(tmp_path)
        task.update_config({"artifact_path": str(art2)})
        apply2 = AsyncMock(return_value=_apply_report())
        dry2 = AsyncMock(return_value=_dry_run_report())
        with patch("dbas.restore_orchestrator.run_dry_run", dry2), \
             patch("dbas.restore_orchestrator.run_restore", apply2), \
             patch("dispatcharr_client.get_client", return_value=AsyncMock()):
            await task.execute()

        # It must NOT still be overwriting, and it must not still be applying.
        assert apply2.await_count == 0
        assert (
            dry2.await_args.kwargs["channel_reattach_mode"]
            is ChannelReattachMode.PRESERVE
        )
        assert task.channel_reattach_mode is ChannelReattachMode.PRESERVE
        assert task.confirm_apply is False

    async def test_the_transients_reset_even_when_the_run_fails(self, tmp_path):
        from dbas.restore_contracts import ChannelReattachMode

        art = _write_artifact(tmp_path)
        task = _make_task(art, confirm_apply=True)
        task.update_config({"channel_reattach_mode": "overwrite"})

        boom = AsyncMock(side_effect=RuntimeError("upstream exploded"))
        with patch("dbas.restore_orchestrator.run_dry_run", AsyncMock()), \
             patch("dbas.restore_orchestrator.run_restore", boom), \
             patch("dispatcharr_client.get_client", return_value=AsyncMock()):
            result = await task.execute()

        assert result.success is False
        # The finally ran: a failed destructive run does not leave itself armed.
        assert task.channel_reattach_mode is ChannelReattachMode.PRESERVE
        assert task.confirm_apply is False
        assert task.passphrase is None

    async def test_the_transients_reset_when_the_run_returns_early(self, tmp_path):
        """The no-artifact early return is an exit path like any other."""
        from dbas.restore_contracts import ChannelReattachMode

        task = DbasRestoreTask(ScheduleConfig(schedule_type=ScheduleType.MANUAL))
        task.update_config(
            {"artifact_path": "", "confirm_apply": True,
             "channel_reattach_mode": "overwrite"}
        )
        result = await task.execute()

        assert result.success is False
        assert task.channel_reattach_mode is ChannelReattachMode.PRESERVE
        assert task.confirm_apply is False

    async def test_cleanup_artifact_does_not_stay_disarmed(self, tmp_path):
        """The fifth transient. /restore-dbas-saved sets it False DELIBERATELY,
        because the artifact there is the operator's own saved backup and must
        survive the restore. Left sticky on the singleton, that False governs
        whether a LATER run deletes the file it was handed."""
        art = _write_artifact(tmp_path)
        task = _make_task(art, confirm_apply=False)
        task.update_config({"cleanup_artifact": False})
        assert task.cleanup_artifact is False

        with patch("dbas.restore_orchestrator.run_dry_run",
                   AsyncMock(return_value=_dry_run_report())), \
             patch("dbas.restore_orchestrator.run_restore", AsyncMock()), \
             patch("dispatcharr_client.get_client", return_value=AsyncMock()):
            await task.execute()

        assert task.cleanup_artifact is True
