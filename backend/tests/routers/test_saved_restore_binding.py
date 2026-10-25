"""Saved DBAS restores consume a private snapshot, never a rebindable pathname."""

import asyncio
import os
import stat
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from routers import backup


@pytest.fixture
def storage(tmp_path, monkeypatch):
    saved = tmp_path / "backups"
    private = tmp_path / "restore_uploads"
    saved.mkdir()
    monkeypatch.setattr(backup, "BACKUPS_DIR", saved)
    monkeypatch.setattr(backup, "_DBAS_RESTORE_TMP_DIR", private)
    return saved, private


@pytest.fixture
def scheduled(monkeypatch):
    create_task = asyncio.create_task
    tasks = []

    def capture(coro, *args, **kwargs):
        task = create_task(coro, *args, **kwargs)
        tasks.append(task)
        return task

    monkeypatch.setattr(backup.asyncio, "create_task", capture)
    return tasks


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name",
    [
        "ecm-backup-2026-01-01_000000.zip",
        "ecm-backup-2026-01-01_000000-ab12cd34.zip",
        "ecm-backup-ab12cd34.zip",
    ],
)
async def test_planted_symlink_never_schedules_restore(
    async_client, storage, tmp_path, name
):
    saved, _ = storage
    outside = tmp_path / "outside.zip"
    outside.write_bytes(b"outside bytes must not be consumed")
    (saved / name).symlink_to(outside)
    with patch(
        "task_engine.get_engine", return_value=MagicMock(run_task=AsyncMock())
    ) as engine:
        response = await async_client.post(
            "/api/backup/restore-dbas-saved", json={"filename": name}
        )
    assert response.status_code == 404
    engine.assert_not_called()
    assert outside.read_bytes() == b"outside bytes must not be consumed"


@pytest.mark.asyncio
async def test_queued_restore_is_bound_to_selected_bytes(
    async_client, storage, tmp_path, scheduled
):
    saved, private = storage
    name = "ecm-backup-2026-01-01_000000.zip"
    original = saved / name
    original.write_bytes(b"selected backup bytes")
    outside = tmp_path / "outside.zip"
    outside.write_bytes(b"replacement backup bytes")
    consumed = []

    async def consume(_task_id, *, parameters, run_id):
        original.unlink()
        original.symlink_to(outside)
        selected = Path(parameters["artifact_path"])
        assert selected != original
        assert stat.S_IMODE(selected.stat().st_mode) == 0o600
        assert stat.S_IMODE(selected.parent.stat().st_mode) == 0o700
        assert parameters["cleanup_artifact"] is True
        consumed.append(selected.read_bytes())

    engine = MagicMock(run_task=AsyncMock(side_effect=consume))
    with patch("task_engine.get_engine", return_value=engine):
        response = await async_client.post(
            "/api/backup/restore-dbas-saved", json={"filename": name}
        )
        assert scheduled
        await asyncio.gather(*scheduled)
    assert response.status_code == 200
    assert consumed == [b"selected backup bytes"]
    assert original.is_symlink()
    assert outside.read_bytes() == b"replacement backup bytes"
    assert not list(private.rglob("*.zip"))


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["rejected", "error", "cancelled"])
async def test_snapshot_cleanup_even_if_engine_does_not_execute(
    async_client, storage, outcome, scheduled
):
    saved, private = storage
    name = "ecm-backup-2026-01-01_000000.zip"
    original = saved / name
    original.write_bytes(b"retained saved artifact")
    selected_paths = []

    async def consume(*args, **kwargs):
        selected = Path(kwargs["parameters"]["artifact_path"])
        selected_paths.append(selected)
        assert selected != original
        assert selected.is_file()
        if outcome == "error":
            raise RuntimeError("engine failed before execute")
        if outcome == "cancelled":
            raise asyncio.CancelledError

    with patch("task_engine.get_engine", return_value=MagicMock(run_task=consume)):
        response = await async_client.post(
            "/api/backup/restore-dbas-saved", json={"filename": name}
        )
        assert scheduled
        results = await asyncio.gather(*scheduled, return_exceptions=True)
    expected = {
        "rejected": type(None),
        "error": RuntimeError,
        "cancelled": asyncio.CancelledError,
    }
    assert all(isinstance(result, expected[outcome]) for result in results)
    assert selected_paths
    assert all(not path.exists() for path in selected_paths)
    assert response.status_code == 200
    assert original.read_bytes() == b"retained saved artifact"
    assert not list(private.rglob("*.zip"))


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["directory", "fifo"])
async def test_non_regular_selection_refused(async_client, storage, kind):
    saved, _ = storage
    name = "ecm-backup-2026-01-01_000000.zip"
    if kind == "directory":
        (saved / name).mkdir()
    else:
        os.mkfifo(saved / name)
    with patch("task_engine.get_engine") as engine:
        response = await async_client.post(
            "/api/backup/restore-dbas-saved", json={"filename": name}
        )
    assert response.status_code == 404
    engine.assert_not_called()


@pytest.mark.asyncio
async def test_rebinding_between_listing_and_open_is_denied(
    async_client, storage, tmp_path, monkeypatch
):
    saved, _ = storage
    name = "ecm-backup-2026-01-01_000000.zip"
    selected = saved / name
    selected.write_bytes(b"original")
    outside = tmp_path / "outside.zip"
    outside.write_bytes(b"outside")
    real_open = os.open
    rebound = []

    def swap(path, flags, *args, **kwargs):
        if path == name and "dir_fd" in kwargs:
            selected.unlink()
            selected.symlink_to(outside)
            rebound.append(True)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(backup.os, "open", swap)
    with patch("task_engine.get_engine") as engine:
        response = await async_client.post(
            "/api/backup/restore-dbas-saved", json={"filename": name}
        )
    assert rebound == [True]
    assert response.status_code == 404
    engine.assert_not_called()
    assert outside.read_bytes() == b"outside"


@pytest.mark.asyncio
async def test_snapshot_size_cap_and_partial_cleanup(
    async_client, storage, monkeypatch
):
    saved, private = storage
    name = "ecm-backup-2026-01-01_000000.zip"
    selected = saved / name
    selected.write_bytes(b"too much for this test budget")
    monkeypatch.setattr(backup, "_RESTORE_MAX_UPLOAD_BYTES", 8)
    with patch("task_engine.get_engine") as engine:
        response = await async_client.post(
            "/api/backup/restore-dbas-saved", json={"filename": name}
        )
    assert response.status_code == 413
    engine.assert_not_called()
    assert not list(private.iterdir())
    assert selected.read_bytes() == b"too much for this test budget"


@pytest.mark.asyncio
async def test_failed_scheduling_removes_snapshot_only(async_client, storage):
    saved, private = storage
    name = "ecm-backup-2026-01-01_000000.zip"
    selected = saved / name
    selected.write_bytes(b"retained backup")
    with patch("task_engine.get_engine", side_effect=RuntimeError("not available")):
        response = await async_client.post(
            "/api/backup/restore-dbas-saved", json={"filename": name}
        )
    assert response.status_code == 500
    assert not list(private.iterdir())
    assert selected.read_bytes() == b"retained backup"


@pytest.mark.asyncio
async def test_cancel_before_first_step_cleans_snapshot_with_task_reference_retained(
    async_client, storage, monkeypatch
):
    saved, private = storage
    name = "ecm-backup-2026-01-01_000000.zip"
    selected = saved / name
    selected.write_bytes(b"retained backup")
    create_task = asyncio.create_task
    retained = []

    def cancel_before_start(coro):
        task = create_task(coro)
        task.cancel()
        retained.append(task)
        return task

    engine = MagicMock(run_task=AsyncMock())
    with patch("task_engine.get_engine", return_value=engine):
        monkeypatch.setattr(backup.asyncio, "create_task", cancel_before_start)
        response = await async_client.post(
            "/api/backup/restore-dbas-saved", json={"filename": name}
        )
        assert retained
        await asyncio.gather(*retained, return_exceptions=True)
        await asyncio.sleep(0)  # execute completed-task callbacks
    assert response.status_code == 200
    engine.run_task.assert_not_awaited()
    assert all(task.cancelled() for task in retained)
    assert not list(private.iterdir())
    assert selected.read_bytes() == b"retained backup"
