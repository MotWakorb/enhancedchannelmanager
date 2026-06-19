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
