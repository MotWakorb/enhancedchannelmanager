"""Privileged-task admin gate on the generic task-run endpoint (O8TBV-1).

Security review of the DBAS restore work (bead enhancedchannelmanager-o8tbv)
found that ``POST /api/tasks/{task_id}/run`` had NO admin gate — the global auth
middleware only validates the token, it does not check admin. A non-admin (or an
MCP-key holder) could therefore ``POST /api/tasks/dbas_restore/run`` with apply
parameters and drive a destructive restore, bypassing the admin-gated
``/restore-dbas`` upload endpoint. The same gap exposed ``dbas_backup``.

The fix admin-gates exactly the privileged task ids
(``routers.tasks.PRIVILEGED_TASK_IDS``) on the generic run/cancel endpoints,
WITHOUT putting a blanket admin dependency on the endpoint (that would regress
ordinary user-triggerable tasks). These tests prove:

  * a non-admin POST to a privileged task's run/cancel -> 403,
  * an admin (or auth-disabled setup mode) POST -> allowed (reaches the engine),
  * an ordinary non-privileged task is UNCHANGED for a non-admin.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from auth import resolve_is_admin_if_enabled
from routers.tasks import PRIVILEGED_TASK_IDS


def _override_admin(app, is_admin: bool):
    """Override the admin-resolver dependency to simulate an authenticated user
    of the given privilege. Returns a cleanup callable."""
    # Override the *factory-produced* dependency callable registered on the
    # endpoints (ResolveIsAdminIfEnabled = Depends(resolve_is_admin_if_enabled())).
    async def _resolved() -> bool:
        return is_admin

    # The prebuilt Depends wraps a single shared inner callable; override that.
    from auth import ResolveIsAdminIfEnabled as _prebuilt

    app.dependency_overrides[_prebuilt.dependency] = _resolved
    return lambda: app.dependency_overrides.pop(_prebuilt.dependency, None)


@pytest.mark.asyncio
class TestPrivilegedTaskRunGate:
    async def test_privileged_run_ids_are_dbas(self):
        assert PRIVILEGED_TASK_IDS == frozenset(
            {"dbas_restore", "dbas_backup", "dbas_sync"}
        )

    async def test_per_target_sync_prefix_is_privileged(self):
        """7ipq2.3: sync tasks are registered per target (dbas_sync_<id>), so
        the gate matches the prefix family too — the outbound-write surface
        did not stop being privileged because the id grew a suffix."""
        from routers.tasks import is_privileged_task_id

        assert is_privileged_task_id("dbas_sync_1")
        assert is_privileged_task_id("dbas_sync_12345")
        assert not is_privileged_task_id("cleanup")

    @pytest.mark.parametrize("task_id", ["dbas_restore", "dbas_backup", "dbas_sync", "dbas_sync_7"])
    async def test_non_admin_run_privileged_task_forbidden(self, async_client, task_id):
        from main import app

        cleanup = _override_admin(app, is_admin=False)
        engine = MagicMock()
        engine.run_task = AsyncMock(return_value=MagicMock())
        try:
            with patch("task_engine.get_engine", return_value=engine):
                resp = await async_client.post(f"/api/tasks/{task_id}/run")
        finally:
            cleanup()

        assert resp.status_code == 403
        # The engine must never have been reached for a refused privileged task.
        engine.run_task.assert_not_called()

    @pytest.mark.parametrize("task_id", ["dbas_restore", "dbas_backup", "dbas_sync", "dbas_sync_7"])
    async def test_admin_run_privileged_task_allowed(self, async_client, task_id):
        from main import app

        cleanup = _override_admin(app, is_admin=True)
        mock_result = MagicMock()
        mock_result.to_dict.return_value = {"success": True}
        engine = MagicMock()
        engine.run_task = AsyncMock(return_value=mock_result)
        try:
            with patch("task_engine.get_engine", return_value=engine):
                resp = await async_client.post(f"/api/tasks/{task_id}/run")
        finally:
            cleanup()

        assert resp.status_code == 200
        engine.run_task.assert_called_once()

    async def test_non_admin_ordinary_task_unchanged(self, async_client):
        """An ordinary non-privileged task is NOT gated by the privileged check —
        a non-admin can still run it (this preserves existing behaviour)."""
        from main import app

        cleanup = _override_admin(app, is_admin=False)
        mock_result = MagicMock()
        mock_result.to_dict.return_value = {"success": True}
        engine = MagicMock()
        engine.run_task = AsyncMock(return_value=mock_result)
        try:
            with patch("task_engine.get_engine", return_value=engine):
                resp = await async_client.post("/api/tasks/stream_probe/run")
        finally:
            cleanup()

        assert resp.status_code == 200
        engine.run_task.assert_called_once()

    async def test_setup_mode_run_privileged_task_allowed(self, async_client):
        """Auth-disabled (setup) mode: the resolver yields admin=True, so the
        privileged task runs exactly as the rest of the app behaves in setup
        mode. This is the default test fixture state (no override)."""
        mock_result = MagicMock()
        mock_result.to_dict.return_value = {"success": True}
        engine = MagicMock()
        engine.run_task = AsyncMock(return_value=mock_result)
        with patch("task_engine.get_engine", return_value=engine):
            resp = await async_client.post("/api/tasks/dbas_restore/run")
        assert resp.status_code == 200
        engine.run_task.assert_called_once()


@pytest.mark.asyncio
class TestPrivilegedTaskCancelGate:
    @pytest.mark.parametrize("task_id", ["dbas_restore", "dbas_backup", "dbas_sync", "dbas_sync_7"])
    async def test_non_admin_cancel_privileged_task_forbidden(self, async_client, task_id):
        from main import app

        cleanup = _override_admin(app, is_admin=False)
        engine = MagicMock()
        engine.cancel_task = AsyncMock(return_value={"status": "cancelled"})
        try:
            with patch("task_engine.get_engine", return_value=engine):
                resp = await async_client.post(f"/api/tasks/{task_id}/cancel")
        finally:
            cleanup()

        assert resp.status_code == 403
        engine.cancel_task.assert_not_called()

    async def test_admin_cancel_privileged_task_allowed(self, async_client):
        from main import app

        cleanup = _override_admin(app, is_admin=True)
        engine = MagicMock()
        engine.cancel_task = AsyncMock(return_value={"status": "cancelled"})
        try:
            with patch("task_engine.get_engine", return_value=engine):
                resp = await async_client.post("/api/tasks/dbas_restore/cancel")
        finally:
            cleanup()

        assert resp.status_code == 200
        engine.cancel_task.assert_called_once()

    async def test_non_admin_cancel_ordinary_task_unchanged(self, async_client):
        from main import app

        cleanup = _override_admin(app, is_admin=False)
        engine = MagicMock()
        engine.cancel_task = AsyncMock(return_value={"status": "cancelled"})
        try:
            with patch("task_engine.get_engine", return_value=engine):
                resp = await async_client.post("/api/tasks/stream_probe/cancel")
        finally:
            cleanup()

        assert resp.status_code == 200
        engine.cancel_task.assert_called_once()


@pytest.mark.asyncio
class TestPrivilegedTaskScheduleGate:
    async def test_non_admin_cannot_enable_privileged_parent(self, async_client):
        from main import app

        cleanup = _override_admin(app, is_admin=False)
        registry = MagicMock()
        try:
            with patch("task_registry.get_registry", return_value=registry):
                response = await async_client.patch(
                    "/api/tasks/dbas_sync_7", json={"enabled": True}
                )
        finally:
            cleanup()

        assert response.status_code == 403
        registry.update_task_config.assert_not_called()

    async def test_non_admin_cannot_create_privileged_schedule(self, async_client):
        from main import app

        cleanup = _override_admin(app, is_admin=False)
        try:
            response = await async_client.post(
                "/api/tasks/dbas_sync_7/schedules",
                json={
                    "schedule_type": "daily",
                    "schedule_time": "06:00",
                    "parameters": {"confirm_apply": True},
                },
            )
        finally:
            cleanup()

        assert response.status_code == 403

    async def test_non_admin_cannot_update_privileged_schedule(self, async_client):
        from main import app

        cleanup = _override_admin(app, is_admin=False)
        try:
            response = await async_client.patch(
                "/api/tasks/dbas_sync_7/schedules/1",
                json={"enabled": True, "parameters": {"confirm_apply": True}},
            )
        finally:
            cleanup()

        assert response.status_code == 403

    async def test_non_admin_ordinary_schedule_write_is_unchanged(
        self, async_client, test_session
    ):
        from main import app
        from models import ScheduledTask

        test_session.add(ScheduledTask(
            task_id="stream_probe",
            task_name="Stream Probe",
            description="Probe streams",
            enabled=True,
            schedule_type="manual",
        ))
        test_session.commit()
        cleanup = _override_admin(app, is_admin=False)
        try:
            response = await async_client.post(
                "/api/tasks/stream_probe/schedules",
                json={"schedule_type": "daily", "schedule_time": "06:00"},
            )
        finally:
            cleanup()

        assert response.status_code == 200

    async def test_auth_disabled_privileged_schedule_write_is_allowed(
        self, async_client, test_session
    ):
        from export_models import SyncTarget
        from models import ScheduledTask
        from tasks.dbas_sync import make_sync_task_class

        test_session.add(ScheduledTask(
            task_id="dbas_sync_7",
            task_name="Cross-Instance Sync",
            description="Sync",
            enabled=True,
            schedule_type="manual",
        ))
        test_session.add(SyncTarget(
            id=7,
            name="Replica",
            base_url="https://replica.example.com",
            credentials="{}",
            enabled=True,
            credential_version=3,
            insecure=False,
        ))
        test_session.commit()
        registry = MagicMock()
        registry.get_task_class.return_value = make_sync_task_class(7, "Replica")

        with patch("task_registry.get_registry", return_value=registry):
            response = await async_client.post(
                "/api/tasks/dbas_sync_7/schedules",
                json={
                    "schedule_type": "daily",
                    "schedule_time": "06:00",
                    "parameters": {"confirm_apply": True},
                },
            )

        assert response.status_code == 200
        assert response.json()["parameters"]["cloud_credential_version"] == 3
