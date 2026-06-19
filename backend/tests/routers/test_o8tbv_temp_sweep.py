"""Stale restore-temp sweep (O8TBV-4).

The restore-trigger endpoint schedules ``DbasRestoreTask`` fire-and-forget via
``asyncio.create_task``. If that coroutine returns BEFORE ``execute()`` runs —
``run_task`` returns ``None`` (task not registered) or an ``ALREADY_RUNNING``
result for a concurrent run — the task's ``finally`` cleanup never fires and the
0600 temp ZIP is orphaned. ``routers.backup._sweep_stale_restore_temps`` runs at
the start of every restore trigger and removes temps older than the age floor.

These tests prove the sweep removes a stale temp, never deletes a fresh one (a
live run still owns it), and is wired into the endpoint.
"""
from __future__ import annotations

import os
import time

import pytest

from routers import backup as backup_mod
from routers.backup import _sweep_stale_restore_temps


def _make_temp(dest_dir, age_seconds):
    dest_dir.mkdir(parents=True, exist_ok=True)
    p = dest_dir / "ecm-restore-abc123.zip"
    p.write_bytes(b"PK\x03\x04 stale")
    past = time.time() - age_seconds
    os.utime(p, (past, past))
    return p


class TestSweepStaleRestoreTemps:
    def test_removes_stale_temp(self, tmp_path):
        stale = _make_temp(
            tmp_path, backup_mod._DBAS_RESTORE_TMP_MAX_AGE_SECONDS + 60
        )
        assert stale.exists()
        _sweep_stale_restore_temps(tmp_path)
        assert not stale.exists()

    def test_keeps_fresh_temp(self, tmp_path):
        # A temp younger than the floor belongs to a possibly-live run.
        fresh = _make_temp(tmp_path, 10)
        _sweep_stale_restore_temps(tmp_path)
        assert fresh.exists()

    def test_no_dir_is_noop(self, tmp_path):
        missing = tmp_path / "does-not-exist"
        # Must not raise when the dir has never been created.
        _sweep_stale_restore_temps(missing)

    def test_only_touches_restore_temps(self, tmp_path):
        tmp_path.mkdir(parents=True, exist_ok=True)
        other = tmp_path / "unrelated.txt"
        other.write_text("keep me")
        past = time.time() - (backup_mod._DBAS_RESTORE_TMP_MAX_AGE_SECONDS + 60)
        os.utime(other, (past, past))
        _sweep_stale_restore_temps(tmp_path)
        assert other.exists()


@pytest.mark.asyncio
class TestEndpointSweepsOnTrigger:
    async def test_trigger_sweeps_stale_temp(self, async_client, tmp_path, monkeypatch):
        """An early scheduling/fire-and-forget failure that orphaned a previous
        temp is cleaned up on the next restore trigger."""
        import hashlib
        import io
        import json
        import zipfile
        from unittest.mock import AsyncMock, MagicMock, patch

        import yaml

        monkeypatch.setattr(backup_mod, "_DBAS_RESTORE_TMP_DIR", tmp_path)

        # Seed an orphaned (stale) temp from a hypothetical prior failed run.
        stale = _make_temp(
            tmp_path, backup_mod._DBAS_RESTORE_TMP_MAX_AGE_SECONDS + 120
        )
        assert stale.exists()

        cat = {"dispatcharr": {"m3u_accounts": [{"id": 1, "name": "P"}]}}
        members = {"categories/m3u_accounts.yaml": yaml.dump(cat).encode("utf-8")}
        fh = {p: hashlib.sha256(b).hexdigest() for p, b in members.items()}
        manifest = {
            "schema_version": 1,
            "files": [{"path": p, "sha256": h} for p, h in fh.items()],
        }
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            for p, b in members.items():
                zf.writestr(p, b)
        artifact = buf.getvalue()

        engine = MagicMock()
        engine.run_task = AsyncMock(return_value=None)

        def _fake_create_task(coro):
            coro.close()
            return MagicMock()

        with patch("task_engine.get_engine", return_value=engine), \
             patch("asyncio.create_task", side_effect=_fake_create_task):
            files = {"file": ("artifact.zip", artifact, "application/zip")}
            resp = await async_client.post("/api/backup/restore-dbas", files=files)

        assert resp.status_code == 200
        # The stale orphan from the prior run is gone after this trigger.
        assert not stale.exists()
