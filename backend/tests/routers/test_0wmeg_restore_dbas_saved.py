"""Tests for the SAVED-artifact DBAS restore endpoint (bead enhancedchannelmanager-0wmeg).

Covers ``POST /api/backup/restore-dbas-saved`` — the saved-file analogue of the
upload-based POST /restore-dbas, exposed so the MCP restore tool can restore a
SAVED on-disk DBAS artifact by filename (no file upload over MCP).

  * dry-run is the default (confirm_apply omitted -> is_dry_run True);
  * confirm_apply=True marks the run not-dry and forwards confirm_apply=True;
  * a traversal / absolute / non-matching filename is rejected (400) at the
    inlined containment guard, BEFORE any task is scheduled;
  * a well-formed name that is not on disk returns 404;
  * the saved path is forwarded to the task as artifact_path with
    cleanup_artifact=False (the operator's saved file must survive the restore);
  * a passphrase, when present, is forwarded to the task parameters but is
    NEVER logged and NEVER echoed back in the response (load-bearing security
    assertion).
"""
from __future__ import annotations

import io
import json
import logging
import zipfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from routers import backup as backup_mod


@pytest.fixture
def backups_dir(tmp_path):
    d = tmp_path / "backups"
    d.mkdir()
    return d


def _make_artifact_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"schema_version": 1, "files": []}))
    return buf.getvalue()


def _capture_create_task():
    """Return (patch_target_side_effect, captured) — observes the scheduled
    coroutine without leaving it pending."""
    captured = {}

    def _fake_create_task(coro):
        captured["coro"] = coro
        coro.close()
        return MagicMock()

    return _fake_create_task, captured


# Sentinel for "send an explicit JSON null", distinct from "omit the field".
JSON_NULL = object()


@pytest.mark.asyncio
class TestRestoreDbasSavedEndpoint:
    async def test_dry_run_is_default(self, async_client, backups_dir):
        fname = "ecm-backup-2026-01-01_000000.zip"
        (backups_dir / fname).write_bytes(_make_artifact_zip())
        engine = MagicMock()
        engine.run_task = AsyncMock(return_value=None)
        fake_ct, _ = _capture_create_task()

        with patch("routers.backup.BACKUPS_DIR", backups_dir), patch(
            "task_engine.get_engine", return_value=engine
        ), patch("asyncio.create_task", side_effect=fake_ct):
            resp = await async_client.post(
                "/api/backup/restore-dbas-saved", json={"filename": fname}
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["task_id"] == "dbas_restore"
        assert body["status"] == "started"
        assert body["is_dry_run"] is True

    async def test_confirm_apply_marks_not_dry_run_and_forwards_param(
        self, async_client, backups_dir
    ):
        fname = "ecm-backup-2026-01-01_000000.zip"
        saved = backups_dir / fname
        saved.write_bytes(_make_artifact_zip())
        engine = MagicMock()
        engine.run_task = AsyncMock(return_value=None)
        fake_ct, _ = _capture_create_task()

        with patch("routers.backup.BACKUPS_DIR", backups_dir), patch(
            "task_engine.get_engine", return_value=engine
        ), patch("asyncio.create_task", side_effect=fake_ct):
            resp = await async_client.post(
                "/api/backup/restore-dbas-saved",
                json={"filename": fname, "confirm_apply": True},
            )

        assert resp.status_code == 200
        assert resp.json()["is_dry_run"] is False
        # The task was kicked with the SAVED path, confirm_apply True,
        # and cleanup_artifact False (do not delete the operator's saved backup).
        engine.run_task.assert_called_once()
        _args, kwargs = engine.run_task.call_args
        params = kwargs["parameters"]
        assert params["artifact_path"] == str(saved.resolve())
        assert params["confirm_apply"] is True
        assert params["cleanup_artifact"] is False

    # --- channel_reattach_mode (bead dfkbn, PR review W1) -------------------

    @pytest.mark.parametrize(
        "sent,expected",
        [
            (None, "preserve"),        # OLD client: field absent entirely
            ("preserve", "preserve"),
            ("overwrite", "overwrite"),
            ("OverWrite", "overwrite"),  # case-insensitive
            ("  overwrite  ", "overwrite"),
            ("nonsense", "preserve"),  # unrecognised falls back SAFE, never 422
            ("", "preserve"),
            (JSON_NULL, "preserve"),   # explicit null coerces, never 422
        ],
    )
    async def test_reattach_mode_is_forwarded_and_defaults_safe(
        self, async_client, backups_dir, sent, expected
    ):
        """An absent or unparseable mode must never mean "overwrite".

        The unsafe direction is not a fallback: a restore from an older client
        that has never heard of this field must keep the behaviour it was written
        against, which is leaving the operator's live channels alone.
        """
        fname = "ecm-backup-2026-01-01_000000.zip"
        (backups_dir / fname).write_bytes(_make_artifact_zip())
        engine = MagicMock()
        engine.run_task = AsyncMock(return_value=None)
        fake_ct, _ = _capture_create_task()

        body = {"filename": fname}
        if sent is JSON_NULL:
            body["channel_reattach_mode"] = None
        elif sent is not None:
            body["channel_reattach_mode"] = sent

        with patch("routers.backup.BACKUPS_DIR", backups_dir), patch(
            "task_engine.get_engine", return_value=engine
        ), patch("asyncio.create_task", side_effect=fake_ct):
            resp = await async_client.post(
                "/api/backup/restore-dbas-saved", json=body
            )

        assert resp.status_code == 200
        assert resp.json()["channel_reattach_mode"] == expected
        _args, kwargs = engine.run_task.call_args
        assert kwargs["parameters"]["channel_reattach_mode"] == expected

    async def test_missing_file_returns_404(self, async_client, backups_dir):
        with patch("routers.backup.BACKUPS_DIR", backups_dir):
            resp = await async_client.post(
                "/api/backup/restore-dbas-saved",
                json={"filename": "ecm-backup-2099-12-31_235959.zip"},
            )
        assert resp.status_code == 404

    @pytest.mark.parametrize(
        "payload",
        [
            "../etc/passwd",
            "../../etc/passwd",
            "../ecm-backup-2026-01-01_000000.zip",
            "/etc/passwd",
            "/tmp/ecm-backup-2026-01-01_000000.zip",
            "ecm-backup-2026-01-01_000000.zip\x00.evil",
            "..\\ecm-backup-2026-01-01_000000.zip",
            "evil-file.txt",
            "ecm-backup-bad.zip",
            "ecm-backup-2026-01-01_000000.yaml",
            "ecm-backup-2026-01-01_000000.tar",
        ],
    )
    async def test_rejects_bad_filename_before_scheduling(
        self, async_client, backups_dir, payload
    ):
        """Traversal / absolute / non-matching names are rejected (400) at the
        containment guard, and NO restore task is scheduled."""
        with patch("routers.backup.BACKUPS_DIR", backups_dir), patch(
            "asyncio.create_task"
        ) as mock_ct, patch("task_engine.get_engine") as mock_engine:
            resp = await async_client.post(
                "/api/backup/restore-dbas-saved", json={"filename": payload}
            )
        assert resp.status_code == 400
        mock_ct.assert_not_called()
        mock_engine.assert_not_called()

    async def test_admin_guarded(self, async_client, backups_dir):
        from fastapi import HTTPException, status
        from main import app
        from auth import RequireAdminIfEnabled as _prebuilt

        async def _reject() -> None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required"
            )

        app.dependency_overrides[_prebuilt.dependency] = _reject
        try:
            with patch("routers.backup.BACKUPS_DIR", backups_dir):
                resp = await async_client.post(
                    "/api/backup/restore-dbas-saved",
                    json={"filename": "ecm-backup-2026-01-01_000000.zip"},
                )
        finally:
            app.dependency_overrides.pop(_prebuilt.dependency, None)
        assert resp.status_code == 403

    async def test_passphrase_forwarded_to_task(self, async_client, backups_dir):
        fname = "ecm-backup-2026-01-01_000000.zip"
        (backups_dir / fname).write_bytes(_make_artifact_zip())
        engine = MagicMock()
        engine.run_task = AsyncMock(return_value=None)
        fake_ct, _ = _capture_create_task()
        secret = "correct horse battery staple"

        with patch("routers.backup.BACKUPS_DIR", backups_dir), patch(
            "task_engine.get_engine", return_value=engine
        ), patch("asyncio.create_task", side_effect=fake_ct):
            resp = await async_client.post(
                "/api/backup/restore-dbas-saved",
                json={"filename": fname, "passphrase": secret},
            )

        assert resp.status_code == 200
        # The passphrase reaches the task parameters (so an encrypted artifact
        # can be decrypted)...
        _args, kwargs = engine.run_task.call_args
        assert kwargs["parameters"]["passphrase"] == secret

    async def test_passphrase_never_in_response_or_logs(
        self, async_client, backups_dir, caplog
    ):
        """LOAD-BEARING SECURITY ASSERTION: the passphrase must NEVER appear in
        the HTTP response body NOR in any log line emitted by the endpoint."""
        fname = "ecm-backup-2026-01-01_000000.zip"
        (backups_dir / fname).write_bytes(_make_artifact_zip())
        engine = MagicMock()
        engine.run_task = AsyncMock(return_value=None)
        fake_ct, _ = _capture_create_task()
        secret = "S3cr3t-Passphrase-Do-Not-Leak"

        with caplog.at_level(logging.DEBUG, logger="routers.backup"):
            with patch("routers.backup.BACKUPS_DIR", backups_dir), patch(
                "task_engine.get_engine", return_value=engine
            ), patch("asyncio.create_task", side_effect=fake_ct):
                resp = await async_client.post(
                    "/api/backup/restore-dbas-saved",
                    json={"filename": fname, "passphrase": secret},
                )

        assert resp.status_code == 200
        # Not in the response body.
        assert secret not in resp.text
        # Not in any captured log record (message or rendered).
        for rec in caplog.records:
            assert secret not in rec.getMessage()
            assert secret not in str(rec.args or "")
