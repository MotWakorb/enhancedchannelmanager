"""Tests for the Emby Clear-Logos endpoints (GH #475, bd-v9tp7).

Covers ``POST /api/emby/clear-logos`` (202 + background job) and
``GET /api/emby/clear-logos/{job_id}`` (poll):

  - Validation: empty / invalid logo types → 400; Emby not configured → 400.
  - Happy path: enumerates Live TV channels and deletes the selected image
    types, reporting deleted / missing / error counts in the job result.
  - channel_ids filter: only the targeted channels are touched.
  - Auth failure during enumeration fails the whole job (no partial deletes).
  - Per-channel delete failures are counted, not fatal.
  - Unknown job id → 404.

Mocks ``routers.emby.EmbyClient`` (no real HTTP) and ``routers.emby.get_settings``.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import asyncio
import pytest

from emby_client import EmbyClientError, EmbyLiveTvChannel


@pytest.fixture(autouse=True)
def notify_mocks():
    """Patch the notification-service calls the worker makes so router tests
    don't touch the notifications table. ``create`` returns an id so the
    update/finalize path is exercised. Yields the three mocks for assertions."""
    with patch("routers.emby.create_notification_internal", new=AsyncMock(return_value={"id": 1})) as create, \
         patch("routers.emby.update_notification_internal", new=AsyncMock(return_value={"id": 1})) as update, \
         patch("routers.emby.delete_notifications_by_source_internal", new=AsyncMock(return_value=0)) as delete:
        yield SimpleNamespace(create=create, update=update, delete=delete)


def _settings(enabled=True, base_url="http://emby.local:8096", api_key="key-xyz"):
    return SimpleNamespace(
        emby_enabled=enabled,
        emby_base_url=base_url,
        emby_api_key=api_key,
    )


def _channels(*ids):
    return [EmbyLiveTvChannel(channel_id=i, name=f"ch-{i}", channel_number=None) for i in ids]


async def _poll_until_terminal(async_client, job_id, tries=50):
    """Poll the job status endpoint until it leaves 'running'."""
    for _ in range(tries):
        resp = await async_client.get(f"/api/emby/clear-logos/{job_id}")
        body = resp.json()
        if body.get("status") != "running":
            return body
        await asyncio.sleep(0.01)
    raise AssertionError(f"job {job_id} did not terminate")


class TestClearLogosValidation:
    @pytest.mark.asyncio
    async def test_rejects_empty_logo_types(self, async_client):
        with patch("routers.emby.get_settings", return_value=_settings()):
            resp = await async_client.post(
                "/api/emby/clear-logos", json={"logo_types": []}
            )
        assert resp.status_code == 400
        assert "logo type" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_rejects_invalid_logo_type(self, async_client):
        with patch("routers.emby.get_settings", return_value=_settings()):
            resp = await async_client.post(
                "/api/emby/clear-logos", json={"logo_types": ["Primary", "Banner"]}
            )
        assert resp.status_code == 400
        assert "Banner" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_rejects_when_emby_not_configured(self, async_client):
        with patch("routers.emby.get_settings", return_value=_settings(enabled=False)):
            resp = await async_client.post("/api/emby/clear-logos", json={})
        assert resp.status_code == 400
        assert "not enabled" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_rejects_when_api_key_missing(self, async_client):
        with patch("routers.emby.get_settings", return_value=_settings(api_key="")):
            resp = await async_client.post("/api/emby/clear-logos", json={})
        assert resp.status_code == 400


class TestClearLogosHappyPath:
    @pytest.mark.asyncio
    async def test_default_clears_all_types_for_all_channels(self, async_client):
        """No body → defaults to all three types × every channel; counts
        deleted vs missing (404) per the delete_item_image return."""
        mock_client = AsyncMock()
        mock_client.get_livetv_channels = AsyncMock(return_value=_channels("a", "b"))
        # First delete per channel succeeds, the rest are 404 (missing).
        mock_client.delete_item_image = AsyncMock(side_effect=[True, False, False, True, False, False])
        mock_client.close = AsyncMock()

        with patch("routers.emby.get_settings", return_value=_settings()), \
             patch("routers.emby.EmbyClient", return_value=mock_client):
            resp = await async_client.post("/api/emby/clear-logos", json={})
            assert resp.status_code == 202
            job_id = resp.json()["job_id"]
            body = await _poll_until_terminal(async_client, job_id)

        assert body["status"] == "completed"
        result = body["result"]
        assert result["channels_processed"] == 2
        # 2 channels × 3 types = 6 deletes; 2 succeeded, 4 were 404.
        assert result["images_deleted"] == 2
        assert result["images_missing"] == 4
        assert result["errors"] == 0
        assert sorted(result["logo_types"]) == ["LogoLight", "LogoLightColor", "Primary"]
        mock_client.close.assert_awaited()

    @pytest.mark.asyncio
    async def test_channel_ids_filter_limits_targets(self, async_client):
        """channel_ids restricts the run to the listed channels only."""
        mock_client = AsyncMock()
        mock_client.get_livetv_channels = AsyncMock(return_value=_channels("a", "b", "c"))
        mock_client.delete_item_image = AsyncMock(return_value=True)
        mock_client.close = AsyncMock()

        with patch("routers.emby.get_settings", return_value=_settings()), \
             patch("routers.emby.EmbyClient", return_value=mock_client):
            resp = await async_client.post(
                "/api/emby/clear-logos",
                json={"logo_types": ["Primary"], "channel_ids": ["b"]},
            )
            job_id = resp.json()["job_id"]
            body = await _poll_until_terminal(async_client, job_id)

        assert body["result"]["channels_processed"] == 1
        # Only channel "b", only Primary → exactly one delete call.
        assert mock_client.delete_item_image.await_count == 1
        called_ids = {c.args[0] for c in mock_client.delete_item_image.await_args_list}
        assert called_ids == {"b"}

    @pytest.mark.asyncio
    async def test_per_channel_delete_error_is_counted_not_fatal(self, async_client):
        """A delete raising EmbyClientError bumps the error count and the run
        continues to the remaining channels."""
        mock_client = AsyncMock()
        mock_client.get_livetv_channels = AsyncMock(return_value=_channels("a", "b"))
        mock_client.delete_item_image = AsyncMock(
            side_effect=[EmbyClientError("boom"), True]
        )
        mock_client.close = AsyncMock()

        with patch("routers.emby.get_settings", return_value=_settings()), \
             patch("routers.emby.EmbyClient", return_value=mock_client):
            resp = await async_client.post(
                "/api/emby/clear-logos", json={"logo_types": ["Primary"]}
            )
            job_id = resp.json()["job_id"]
            body = await _poll_until_terminal(async_client, job_id)

        result = body["result"]
        assert result["channels_processed"] == 2
        assert result["errors"] == 1
        assert result["images_deleted"] == 1


class TestClearLogosNotifications:
    @pytest.mark.asyncio
    async def test_emits_progress_notification_lifecycle(self, async_client, notify_mocks):
        """Create one progress notification (source task_clear_emby_logos,
        status 'clearing'), update it during the run, and finalize it with
        status 'completed' — the same shape stream probe uses."""
        mock_client = AsyncMock()
        mock_client.get_livetv_channels = AsyncMock(return_value=_channels("a", "b"))
        mock_client.delete_item_image = AsyncMock(return_value=True)
        mock_client.close = AsyncMock()

        with patch("routers.emby.get_settings", return_value=_settings()), \
             patch("routers.emby.EmbyClient", return_value=mock_client):
            resp = await async_client.post(
                "/api/emby/clear-logos", json={"logo_types": ["Primary"]}
            )
            job_id = resp.json()["job_id"]
            await _poll_until_terminal(async_client, job_id)

        # Stale notifications cleared, exactly one created with the task_ source
        # and an initial 'clearing' status.
        notify_mocks.delete.assert_awaited_with("task_clear_emby_logos")
        notify_mocks.create.assert_awaited_once()
        create_kwargs = notify_mocks.create.await_args.kwargs
        assert create_kwargs["source"] == "task_clear_emby_logos"
        assert create_kwargs["metadata"]["progress"]["status"] == "clearing"
        assert create_kwargs["metadata"]["progress"]["total"] == 2

        # Finalized: the last update carries status 'completed' and success type.
        assert notify_mocks.update.await_count >= 1
        final_kwargs = notify_mocks.update.await_args.kwargs
        assert final_kwargs["metadata"]["progress"]["status"] == "completed"
        assert final_kwargs["notification_type"] == "success"
        assert final_kwargs["metadata"]["progress"]["success"] == 2  # 2 deleted

    @pytest.mark.asyncio
    async def test_errors_finalize_as_warning_notification(self, async_client, notify_mocks):
        """A run with delete errors finalizes the notification as 'warning'."""
        mock_client = AsyncMock()
        mock_client.get_livetv_channels = AsyncMock(return_value=_channels("a"))
        mock_client.delete_item_image = AsyncMock(side_effect=EmbyClientError("boom"))
        mock_client.close = AsyncMock()

        with patch("routers.emby.get_settings", return_value=_settings()), \
             patch("routers.emby.EmbyClient", return_value=mock_client):
            resp = await async_client.post(
                "/api/emby/clear-logos", json={"logo_types": ["Primary"]}
            )
            job_id = resp.json()["job_id"]
            await _poll_until_terminal(async_client, job_id)

        final_kwargs = notify_mocks.update.await_args.kwargs
        assert final_kwargs["notification_type"] == "warning"
        assert final_kwargs["metadata"]["progress"]["failed"] == 1


class TestClearLogosFailureModes:
    @pytest.mark.asyncio
    async def test_auth_failure_during_enumeration_fails_job(self, async_client, notify_mocks):
        """If listing channels raises (bad key), the job fails with the error,
        never issues a delete, and emits a failed notification."""
        mock_client = AsyncMock()
        mock_client.get_livetv_channels = AsyncMock(
            side_effect=EmbyClientError("Emby /LiveTv/Channels returned 401 unauthorized")
        )
        mock_client.delete_item_image = AsyncMock()
        mock_client.close = AsyncMock()

        with patch("routers.emby.get_settings", return_value=_settings()), \
             patch("routers.emby.EmbyClient", return_value=mock_client):
            resp = await async_client.post("/api/emby/clear-logos", json={})
            job_id = resp.json()["job_id"]
            body = await _poll_until_terminal(async_client, job_id)

        assert body["status"] == "failed"
        assert "401" in body["error"]
        mock_client.delete_item_image.assert_not_called()
        mock_client.close.assert_awaited()  # client still closed via finally
        # A failed-status error notification was emitted for the operator.
        notify_mocks.create.assert_awaited_once()
        assert notify_mocks.create.await_args.kwargs["notification_type"] == "error"

    @pytest.mark.asyncio
    async def test_status_404_for_unknown_job(self, async_client):
        resp = await async_client.get("/api/emby/clear-logos/does-not-exist")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_completed_job_is_evicted_on_first_read(self, async_client):
        """Single-shot retrieval: a completed result is dropped after one read."""
        mock_client = AsyncMock()
        mock_client.get_livetv_channels = AsyncMock(return_value=_channels("a"))
        mock_client.delete_item_image = AsyncMock(return_value=True)
        mock_client.close = AsyncMock()

        with patch("routers.emby.get_settings", return_value=_settings()), \
             patch("routers.emby.EmbyClient", return_value=mock_client):
            resp = await async_client.post(
                "/api/emby/clear-logos", json={"logo_types": ["Primary"]}
            )
            job_id = resp.json()["job_id"]
            body = await _poll_until_terminal(async_client, job_id)
            assert body["status"] == "completed"
            # Second read → gone.
            again = await async_client.get(f"/api/emby/clear-logos/{job_id}")
        assert again.status_code == 404
