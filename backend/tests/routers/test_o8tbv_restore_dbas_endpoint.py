"""Tests for the async DBAS restore-trigger endpoint (bead enhancedchannelmanager-o8tbv).

Covers ``POST /api/backup/restore-dbas`` and the streaming helper
``routers.backup._stream_upload_to_temp``:

  * the upload is STREAMED to a temp file ONE chunk at a time (never read
    whole-in-RAM) and the temp file is mode 0600;
  * a hard size cap rejects an oversize upload (413) and cleans up the partial;
  * an empty upload is rejected (400);
  * admin-auth is required (403 for a non-admin);
  * a valid upload returns a task id and schedules the restore task in the
    background (the endpoint does NOT block on the restore completing);
  * dry-run is the default (confirm_apply omitted -> is_dry_run True).
"""
from __future__ import annotations

import io
import os
import stat
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from routers import backup as backup_mod
from routers.backup import _stream_upload_to_temp


class _FakeUpload:
    """Minimal UploadFile stand-in that yields its body in chunks (proves the
    streaming read path: the helper calls .read(chunk) repeatedly)."""

    def __init__(self, body: bytes, filename="artifact.zip"):
        self._buf = io.BytesIO(body)
        self.filename = filename
        self.read_calls = 0

    async def read(self, size: int = -1) -> bytes:
        self.read_calls += 1
        return self._buf.read(size)


@pytest.mark.asyncio
class TestStreamUploadToTemp:
    async def test_streams_in_chunks_not_whole_in_ram(self, tmp_path):
        # A body larger than one chunk forces multiple .read() calls.
        body = b"PK\x03\x04" + os.urandom(backup_mod._RESTORE_UPLOAD_CHUNK * 2)
        upload = _FakeUpload(body)
        out = await _stream_upload_to_temp(upload, tmp_path)
        try:
            # >1 data read + the terminating empty read == streamed, not one slurp.
            assert upload.read_calls >= 3
            assert out.read_bytes() == body
        finally:
            out.unlink(missing_ok=True)

    async def test_temp_file_is_mode_0600(self, tmp_path):
        upload = _FakeUpload(b"PK\x03\x04 small")
        out = await _stream_upload_to_temp(upload, tmp_path)
        try:
            mode = stat.S_IMODE(out.stat().st_mode)
            assert mode == 0o600
        finally:
            out.unlink(missing_ok=True)

    async def test_oversize_rejected_and_cleaned_up(self, tmp_path, monkeypatch):
        # Shrink the cap so the test stays small.
        monkeypatch.setattr(backup_mod, "_RESTORE_MAX_UPLOAD_BYTES", 1024)
        monkeypatch.setattr(backup_mod, "_RESTORE_UPLOAD_CHUNK", 256)
        upload = _FakeUpload(os.urandom(4096))
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as ei:
            await _stream_upload_to_temp(upload, tmp_path)
        assert ei.value.status_code == 413
        # No partial temp left behind.
        assert list(tmp_path.glob("ecm-restore-*")) == []

    async def test_empty_upload_rejected(self, tmp_path):
        upload = _FakeUpload(b"")
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as ei:
            await _stream_upload_to_temp(upload, tmp_path)
        assert ei.value.status_code == 400
        assert list(tmp_path.glob("ecm-restore-*")) == []


@pytest.mark.asyncio
class TestRestoreDbasEndpoint:
    async def _valid_artifact_bytes(self) -> bytes:
        import hashlib
        import json
        import zipfile
        import yaml

        cat = {"dispatcharr": {"m3u_accounts": [{"id": 1, "name": "P"}]}}
        members = {"categories/m3u_accounts.yaml": yaml.dump(cat).encode("utf-8")}
        fh = {p: hashlib.sha256(b).hexdigest() for p, b in members.items()}
        manifest = {"schema_version": 1, "files": [{"path": p, "sha256": h} for p, h in fh.items()]}
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            for p, b in members.items():
                zf.writestr(p, b)
        return buf.getvalue()

    async def test_returns_task_id_and_schedules_background(self, async_client, tmp_path, monkeypatch):
        monkeypatch.setattr(backup_mod, "_DBAS_RESTORE_TMP_DIR", tmp_path)

        engine = MagicMock()
        engine.run_task = AsyncMock(return_value=None)
        captured = {}

        # Patch asyncio.create_task so the coroutine is observed (and not left
        # pending) without actually running the restore task.
        def _fake_create_task(coro):
            captured["coro"] = coro
            coro.close()  # don't leave it un-awaited
            return MagicMock()

        with patch("task_engine.get_engine", return_value=engine), \
             patch("asyncio.create_task", side_effect=_fake_create_task):
            files = {"file": ("artifact.zip", await self._valid_artifact_bytes(), "application/zip")}
            resp = await async_client.post("/api/backup/restore-dbas", files=files)

        assert resp.status_code == 200
        body = resp.json()
        assert body["task_id"] == "dbas_restore"
        assert body["is_dry_run"] is True
        assert body["status"] == "started"
        assert "coro" in captured

    async def test_confirm_apply_flag_marks_not_dry_run(self, async_client, tmp_path, monkeypatch):
        monkeypatch.setattr(backup_mod, "_DBAS_RESTORE_TMP_DIR", tmp_path)
        engine = MagicMock()
        engine.run_task = AsyncMock(return_value=None)

        def _fake_create_task(coro):
            coro.close()
            return MagicMock()

        with patch("task_engine.get_engine", return_value=engine), \
             patch("asyncio.create_task", side_effect=_fake_create_task):
            files = {"file": ("artifact.zip", await self._valid_artifact_bytes(), "application/zip")}
            resp = await async_client.post(
                "/api/backup/restore-dbas?confirm_apply=true", files=files
            )
        assert resp.status_code == 200
        assert resp.json()["is_dry_run"] is False

    async def test_empty_upload_rejected(self, async_client, tmp_path, monkeypatch):
        monkeypatch.setattr(backup_mod, "_DBAS_RESTORE_TMP_DIR", tmp_path)
        files = {"file": ("artifact.zip", b"", "application/zip")}
        resp = await async_client.post("/api/backup/restore-dbas", files=files)
        assert resp.status_code == 400

    async def test_admin_guarded(self, async_client, tmp_path, monkeypatch):
        monkeypatch.setattr(backup_mod, "_DBAS_RESTORE_TMP_DIR", tmp_path)
        from fastapi import HTTPException, status
        from main import app
        from auth import RequireAdminIfEnabled as _prebuilt

        async def _reject() -> None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

        app.dependency_overrides[_prebuilt.dependency] = _reject
        try:
            files = {"file": ("artifact.zip", b"PK\x03\x04 x", "application/zip")}
            resp = await async_client.post("/api/backup/restore-dbas", files=files)
        finally:
            app.dependency_overrides.pop(_prebuilt.dependency, None)
        assert resp.status_code == 403
