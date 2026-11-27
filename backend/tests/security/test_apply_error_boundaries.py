"""Normalization and M3U apply errors keep upstream text out of responses and logs.

Every case drives the real route or background helper with a synthetic failure
carrying a registered credential, then checks the HTTP body (or notification,
or summary entry) and the real console, ring and persistent log handlers.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from tests.routers.test_e9e5o_normalize_failure_disclosure import _commit_and_wait
from tests.routers.test_profile_reconcile_hooks import _account, _mock_client
from tests.security import test_credential_error_boundaries as _boundaries
from tests.security.test_credential_error_boundaries import (
    CAUSE,
    HOST,
    SECRET,
    assert_safe_logs,
    upstream_error,
)

# The real console, ring and persistent handlers, and the bulk-job isolation,
# shared with the profile and bulk boundary suite.
log_outputs = _boundaries.log_outputs
own_bulk_jobs = _boundaries.own_bulk_jobs


def assert_body_safe(text):
    assert SECRET not in text
    assert CAUSE not in text
    assert HOST not in text


def mapped_error():
    """An upstream 4xx in the hand-built ``<thing> failed: <status> - <body>`` shape."""
    error = RuntimeError(f"Request failed: 404 - Not found. {HOST} api_key={SECRET}")
    error.__cause__ = RuntimeError("unlabelled registered credential " + CAUSE)
    return error


# ---------------------------------------------------------------------------
# M3U routes whose catch-all logs the exception and answers a fixed 500
# ---------------------------------------------------------------------------

ROUTE_CASES = [
    ("GET", "/api/m3u/accounts/11", None, "get_m3u_account",
     "[M3U] Failed to fetch M3U account 11"),
    ("PUT", "/api/m3u/accounts/11", {"name": "x"}, "get_m3u_account",
     "[M3U] Failed to update M3U account 11"),
    ("PATCH", "/api/m3u/accounts/11", {"is_active": False}, "get_m3u_account",
     "[M3U] Failed to patch M3U account 11"),
    ("DELETE", "/api/m3u/accounts/11", None, "get_m3u_account",
     "[M3U] Failed to delete M3U account 11"),
    ("POST", "/api/m3u/refresh/11", None, "get_m3u_account",
     "[M3U-REFRESH] Failed to refresh M3U account 11"),
    ("GET", "/api/m3u/accounts/11/stream-metadata", None, "get_m3u_account",
     "[M3U] Failed to parse M3U metadata for account 11"),
    ("POST", "/api/m3u/accounts/11/group-auto-sync-toggle",
     {"channel_group_id": 1304, "auto_channel_sync": True, "confirm": True},
     "get_m3u_account",
     "[M3U] Guided auto-sync toggle failed for account 11 group 1304"),
]

MAPPED_CASES = [
    ("GET", "/api/m3u/accounts/11", None,
     "[M3U] Fetch M3U account 11 rejected by Dispatcharr"),
    ("PUT", "/api/m3u/accounts/11", {"name": "x"},
     "[M3U] Update M3U account 11 rejected by Dispatcharr"),
    ("PATCH", "/api/m3u/accounts/11", {"is_active": False},
     "[M3U] Patch M3U account 11 rejected by Dispatcharr"),
    ("DELETE", "/api/m3u/accounts/11", None,
     "[M3U] Delete M3U account 11 rejected by Dispatcharr"),
    ("POST", "/api/m3u/refresh/11", None,
     "[M3U-REFRESH] Refresh M3U account 11 rejected by Dispatcharr"),
]


async def _send(async_client, method, path, body):
    return await async_client.request(method, path, json=body)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["runtime", "status"])
@pytest.mark.parametrize("method,path,body,failing,marker", ROUTE_CASES)
async def test_m3u_route_failure_answers_fixed_500_and_scrubs_logs(
    async_client, log_outputs, kind, method, path, body, failing, marker
):
    client = AsyncMock()
    getattr(client, failing).side_effect = upstream_error(kind)
    with (
        patch("routers.m3u.get_client", return_value=client),
        patch("routers.m3u.journal"),
        patch("routers.m3u.send_alert", AsyncMock()),
    ):
        response = await _send(async_client, method, path, body)
    assert response.status_code == 500
    assert response.json()["detail"] == "Internal server error"
    assert_body_safe(response.text)
    assert_safe_logs(log_outputs, marker)


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,body,marker", MAPPED_CASES)
async def test_m3u_route_upstream_rejection_keeps_status_and_scrubs_logs(
    async_client, log_outputs, method, path, body, marker
):
    client = AsyncMock()
    client.get_m3u_account.side_effect = mapped_error()
    with (
        patch("routers.m3u.get_client", return_value=client),
        patch("routers.m3u.journal"),
        patch("routers.m3u.send_alert", AsyncMock()),
    ):
        response = await _send(async_client, method, path, body)
    assert response.status_code == 404
    assert_safe_logs(log_outputs, marker)


# ---------------------------------------------------------------------------
# M3U profile routes used to answer detail=str(e). The app-wide handler already
# replaces every 500 detail, so the wire assertion pins that contract and the
# log assertion is the one these routes could fail.
# ---------------------------------------------------------------------------

PROFILE_CASES = [
    ("GET", None, "get_m3u_profile", "[M3U] Failed to fetch profile 12 for account 11"),
    ("PATCH", {"name": "x"}, "update_m3u_profile",
     "[M3U] Failed to update profile 12 for account 11"),
    ("DELETE", None, "delete_m3u_profile",
     "[M3U] Failed to delete profile 12 for account 11"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["runtime", "status"])
@pytest.mark.parametrize("method,body,failing,marker", PROFILE_CASES)
async def test_m3u_profile_failure_answers_fixed_detail(
    async_client, log_outputs, kind, method, body, failing, marker
):
    client = AsyncMock()
    getattr(client, failing).side_effect = upstream_error(kind)
    with patch("routers.m3u.get_client", return_value=client):
        response = await _send(
            async_client, method, "/api/m3u/accounts/11/profiles/12/", body
        )
    assert response.status_code == 500
    assert response.json()["detail"] == "Internal server error"
    assert_body_safe(response.text)
    assert_safe_logs(log_outputs, marker)


# ---------------------------------------------------------------------------
# Stream metadata: the fetched URL carries Xtream Codes credentials
# ---------------------------------------------------------------------------


class _FailingHttpClient:
    def __init__(self, error):
        self._error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, **kwargs):
        raise self._error(url)


def _status_error(url):
    request = httpx.Request("GET", url)
    response = httpx.Response(404, request=request)
    return httpx.HTTPStatusError(
        f"Client error '404 Not Found' for url '{url}'", request=request, response=response
    )


def _connect_error(url):
    return httpx.ConnectError(
        f"connection to {url} refused", request=httpx.Request("GET", url)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "make_error,detail",
    [
        (_status_error, "Failed to fetch M3U file: the provider answered HTTP 404."),
        (_connect_error, "Failed to fetch M3U file: the provider could not be reached."),
    ],
)
async def test_stream_metadata_fetch_failure_keeps_provider_credentials_private(
    async_client, log_outputs, make_error, detail
):
    client = AsyncMock()
    client.get_m3u_account.return_value = {
        "id": 11,
        "account_type": "XC",
        "server_url": f"http://{HOST}",
        "username": "synthetic-user",
        "password": SECRET,
    }
    with (
        patch("routers.m3u.get_client", return_value=client),
        patch(
            "routers.m3u.httpx.AsyncClient",
            lambda **kwargs: _FailingHttpClient(make_error),
        ),
    ):
        response = await async_client.get("/api/m3u/accounts/11/stream-metadata")
    assert response.status_code == 502
    assert response.json()["detail"] == detail
    assert_body_safe(response.text)
    for text in log_outputs():
        assert "[M3U] Failed to fetch M3U file for account 11" in text
        assert SECRET not in text


# ---------------------------------------------------------------------------
# Upload and account delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_failure_answers_fixed_detail(async_client, log_outputs, tmp_path):
    def failing_open(*args, **kwargs):
        raise upstream_error("runtime")

    with (
        patch("routers.m3u.CONFIG_DIR", tmp_path),
        patch("aiofiles.open", failing_open),
    ):
        response = await async_client.post(
            "/api/m3u/upload", files={"file": ("list.m3u", b"#EXTM3U\n")}
        )
    assert response.status_code == 500
    assert response.json()["detail"] == "Internal server error"
    assert_body_safe(response.text)
    assert_safe_logs(log_outputs, "[M3U] Failed to upload M3U file")


@pytest.mark.asyncio
async def test_account_delete_reports_failed_group_without_upstream_text(
    async_client, log_outputs
):
    client = AsyncMock()
    client.get_m3u_account.return_value = _account(11, 1304, [12])
    client.get_m3u_accounts.return_value = [_account(11, 1304, [12])]
    client.delete_channel_group.side_effect = upstream_error("runtime")
    with (
        patch("routers.m3u.get_client", return_value=client),
        patch("routers.m3u.journal") as journal,
        patch("routers.m3u.get_settings") as settings,
    ):
        settings.return_value.linked_m3u_accounts = []
        response = await async_client.delete("/api/m3u/accounts/11")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "deleted"
    assert body["failed_groups"] == [{
        "id": 1304,
        "error": "Channel group was not deleted; check server logs for details.",
    }]
    assert_body_safe(response.text)
    assert_body_safe(str(journal.log_entry.call_args))
    assert_safe_logs(log_outputs, "[M3U] Failed to delete channel group 1304")


# ---------------------------------------------------------------------------
# Refresh trigger notification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("make", [lambda: upstream_error("runtime"), mapped_error])
async def test_refresh_trigger_notification_carries_no_upstream_text(
    async_client, log_outputs, make
):
    client = AsyncMock()
    client.get_m3u_account.side_effect = make()
    alert = AsyncMock()
    with (
        patch("routers.m3u.get_client", return_value=client),
        patch("routers.m3u.send_alert", alert),
    ):
        await async_client.post("/api/m3u/refresh/11")
    alert.assert_awaited_once()
    kwargs = alert.await_args.kwargs
    assert kwargs["message"] == (
        "Failed to trigger M3U refresh for account (ID: 11); check server logs for details."
    )
    assert kwargs["metadata"] == {"account_id": 11, "error": "refresh trigger failed"}
    assert_body_safe(str(kwargs))


# ---------------------------------------------------------------------------
# Background refresh helpers (no HTTP response; logs only)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_change_capture_stream_fetch_failure_scrubs_log(log_outputs):
    from routers import m3u

    client = AsyncMock()
    client.get_m3u_account.return_value = _account(11, 1304, [12])
    client.get_channel_groups.return_value = [{"id": 1304, "name": "News"}]
    client.get_stream_groups_with_counts.return_value = []
    client.get_streams.side_effect = upstream_error("runtime")
    with (
        patch("routers.m3u.get_client", return_value=client),
        patch("routers.m3u.get_session", MagicMock()),
        patch("m3u_change_detector.M3UChangeDetector", MagicMock()),
    ):
        await m3u._capture_m3u_changes_after_refresh(11, "Account 11")
    assert_safe_logs(log_outputs, "[M3U-CHANGE] Could not fetch streams for group 'News'")


@pytest.mark.asyncio
async def test_change_capture_failure_scrubs_log(log_outputs):
    from routers import m3u

    client = AsyncMock()
    client.get_m3u_account.side_effect = upstream_error("runtime")
    with patch("routers.m3u.get_client", return_value=client):
        await m3u._capture_m3u_changes_after_refresh(11, "Account 11")
    assert_safe_logs(log_outputs, "[M3U-CHANGE] Failed to capture changes for Account 11")


def test_watermark_failure_scrubs_log(log_outputs):
    from routers import m3u

    with patch("routers.m3u.get_settings", side_effect=upstream_error("runtime")):
        m3u._advance_refresh_watermark()
    assert_safe_logs(log_outputs, "[M3U-REFRESH] Failed to advance refresh watermark")


@pytest.mark.asyncio
async def test_post_refresh_reconcile_failure_returns_fixed_note(log_outputs):
    from routers import m3u

    with patch(
        "services.profile_reconcile.reconcile_all_selected_groups",
        AsyncMock(side_effect=upstream_error("runtime")),
    ):
        note = await m3u._reconcile_profiles_after_refresh(AsyncMock(), "Account 11")
    assert note == (
        "the channel-profile reconcile failed — it will retry on the next scheduled sync"
    )
    assert_safe_logs(log_outputs, "[M3U-REFRESH] Profile reconcile failed for 'Account 11'")


@pytest.mark.asyncio
async def test_placeholder_rebind_failure_scrubs_log(log_outputs):
    from routers import m3u

    with (
        patch("routers.m3u.get_client", return_value=AsyncMock()),
        patch(
            "dbas.placeholder_rebind.rebind_placeholders_after_refresh",
            AsyncMock(side_effect=upstream_error("runtime")),
        ),
    ):
        await m3u._rebind_restore_placeholders("Account 11")
    assert_safe_logs(
        log_outputs, "[M3U-REFRESH] Placeholder rebind after 'Account 11' failed"
    )


def _poll_patches(client, **overrides):
    from routers import m3u

    targets = {
        "get_client": MagicMock(return_value=client),
        "get_cache": MagicMock(),
        "send_alert": AsyncMock(),
        "send_immediate_digest": AsyncMock(),
        "journal": MagicMock(),
        "_advance_refresh_watermark": MagicMock(),
        "_capture_m3u_changes_after_refresh": AsyncMock(),
        "_rebind_restore_placeholders": AsyncMock(),
        "_reconcile_profiles_after_refresh": AsyncMock(return_value=None),
    }
    targets.update(overrides)
    patches = [patch.object(m3u, name, value) for name, value in targets.items()]
    patches.append(patch.object(m3u.asyncio, "sleep", new=AsyncMock()))
    return patches


async def _run_poll(client, initial_updated, **overrides):
    from contextlib import ExitStack

    from routers import m3u

    with ExitStack() as stack:
        for p in _poll_patches(client, **overrides):
            stack.enter_context(p)
        await m3u._poll_m3u_refresh_completion(11, "Account 11", initial_updated)


@pytest.mark.asyncio
async def test_refresh_poll_account_read_failure_scrubs_log(log_outputs):
    client = AsyncMock()
    client.get_m3u_account.side_effect = upstream_error("runtime")
    await _run_poll(client, "before")
    assert_safe_logs(
        log_outputs, "[M3U-REFRESH] Could not fetch account 11 during polling"
    )


@pytest.mark.asyncio
async def test_refresh_poll_digest_failure_scrubs_log(log_outputs):
    client = AsyncMock()
    client.get_m3u_account.return_value = {"updated_at": "after"}
    await _run_poll(
        client, "before",
        send_immediate_digest=AsyncMock(side_effect=upstream_error("runtime")),
    )
    assert_safe_logs(
        log_outputs, "[M3U-REFRESH] Failed to send immediate digest for 'Account 11'"
    )


@pytest.mark.asyncio
async def test_refresh_poll_untimestamped_digest_failure_scrubs_log(log_outputs):
    import datetime as real_datetime

    class _Clock(real_datetime.datetime):
        ticks = 0

        @classmethod
        def utcnow(cls):
            cls.ticks += 1
            return real_datetime.datetime(2026, 1, 1) + real_datetime.timedelta(
                seconds=31 * cls.ticks
            )

    client = AsyncMock()
    client.get_m3u_account.return_value = {}
    with patch("datetime.datetime", _Clock):
        await _run_poll(
            client, None,
            send_immediate_digest=AsyncMock(side_effect=upstream_error("runtime")),
        )
    assert_safe_logs(
        log_outputs, "[M3U-REFRESH] Failed to send immediate digest for 'Account 11'"
    )


@pytest.mark.asyncio
async def test_refresh_poll_unexpected_failure_scrubs_log(log_outputs):
    client = AsyncMock()
    client.get_m3u_account.return_value = {"updated_at": "after"}
    await _run_poll(
        client, "before", get_cache=MagicMock(side_effect=upstream_error("runtime")),
    )
    assert_safe_logs(log_outputs, "[M3U-REFRESH] Error polling for 'Account 11' completion")


# ---------------------------------------------------------------------------
# Group-settings save: account list, setup, per-group and outer reconcile
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enforced_global_set_account_list_failure_is_safe(async_client, log_outputs):
    client = _mock_client()
    client.get_m3u_account.return_value = _account(11, 1304, [12])
    client.get_m3u_accounts.side_effect = upstream_error("runtime")
    with (
        patch("routers.m3u.get_client", return_value=client),
        patch("routers.m3u.journal"),
        patch(
            "services.profile_reconcile._resolve_live_rule_ids",
            AsyncMock(return_value=set()),
        ),
        patch(
            "services.profile_reconcile.reconcile_group_profiles",
            AsyncMock(return_value={"status": "reconciled", "group_id": 1304}),
        ),
    ):
        response = await async_client.patch(
            "/api/m3u/accounts/11/group-settings",
            json={"group_settings": [
                {"channel_group": 1304, "custom_properties": {"channel_profile_ids": [13]}}
            ]},
        )
    assert response.status_code == 200
    errors = [o["error"] for o in response.json()["ecm_profile_apply"] if o["status"] == "error"]
    assert errors == [
        "Profile selection was saved on this account but could NOT be propagated "
        "to all M3U accounts (account list unavailable). Re-save from the listed "
        "account(s) to complete the change."
    ]
    assert_body_safe(response.text)
    assert_safe_logs(log_outputs, "[M3U] enforced-global: could not list accounts")


@pytest.mark.asyncio
async def test_enforced_global_clear_account_list_failure_is_safe(async_client, log_outputs):
    client = _mock_client()
    client.get_m3u_account.return_value = _account(11, 1304, [12])
    client.get_all_m3u_group_settings.return_value = {1304: {"custom_properties": {}}}
    client.get_m3u_accounts.side_effect = upstream_error("runtime")
    with (
        patch("routers.m3u.get_client", return_value=client),
        patch("routers.m3u.journal"),
        patch(
            "services.profile_reconcile._resolve_live_rule_ids",
            AsyncMock(return_value=set()),
        ),
    ):
        response = await async_client.patch(
            "/api/m3u/accounts/11/group-settings",
            json={"group_settings": [{"channel_group": 1304, "custom_properties": {}}]},
        )
    assert response.status_code == 503
    assert response.json()["detail"] == (
        "The channel-profile selection was NOT fully cleared (account list "
        "unavailable; clear NOT applied). The authoritative account keeps its "
        "selection; please re-save to complete the change."
    )
    assert_body_safe(response.text)
    assert_safe_logs(log_outputs, "[M3U] enforced-global clear: fresh account read FAILED")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stage,group_id,error,marker",
    [
        ("settings", None,
         "the channel-profile apply could not read group settings; retry the save.",
         "[M3U] group-settings fetch for apply failed"),
        ("live_rules", None,
         "the channel-profile apply failed; retry the save.",
         "[M3U] Profile reconcile after group-settings save failed"),
        ("group", 1304,
         "the channel-profile reconcile for this group failed; retry the save.",
         "[M3U] Profile reconcile for group 1304 failed"),
    ],
)
async def test_group_settings_apply_failure_summary_is_fixed_and_logs_scrubbed(
    async_client, log_outputs, stage, group_id, error, marker
):
    client = _mock_client()
    if stage == "settings":
        client.get_all_m3u_group_settings.side_effect = upstream_error("runtime")
    rules = AsyncMock(return_value=set())
    reconcile = AsyncMock(return_value={"status": "reconciled", "group_id": 1304})
    if stage == "live_rules":
        rules.side_effect = upstream_error("runtime")
    elif stage == "group":
        reconcile.side_effect = upstream_error("runtime")
    with (
        patch("routers.m3u.get_client", return_value=client),
        patch("routers.m3u.journal"),
        patch("services.profile_reconcile._resolve_live_rule_ids", rules),
        patch("services.profile_reconcile.reconcile_group_profiles", reconcile),
    ):
        response = await async_client.patch(
            "/api/m3u/accounts/11/group-settings",
            json={"group_settings": [
                {"channel_group": 1304, "custom_properties": {"channel_profile_ids": [12]}}
            ]},
        )
    assert response.status_code == 200
    assert response.json()["ecm_profile_apply"] == [
        {"status": "error", "group_id": group_id, "error": error}
    ]
    assert_body_safe(response.text)
    assert_safe_logs(log_outputs, marker)


# ---------------------------------------------------------------------------
# Channels: normalization apply, single create and single PATCH
# ---------------------------------------------------------------------------

NORMALIZATION_ERROR = "Normalization failed; the original channel name was used."


@pytest.mark.asyncio
async def test_single_create_normalization_failure_is_fixed_and_logs_scrubbed(
    async_client, log_outputs
):
    client = AsyncMock()
    client.create_channel.return_value = {"id": 11, "name": "Raw Name"}
    with (
        patch("routers.channels.get_client", return_value=client),
        patch(
            "routers.channels.get_normalization_engine",
            side_effect=upstream_error("runtime"),
        ),
        patch("routers.channels.journal"),
    ):
        response = await async_client.post(
            "/api/channels", json={"name": "Raw Name", "normalize": True}
        )
    assert response.status_code == 200
    assert response.json()["normalization"] == {
        "requested": True,
        "applied": False,
        "nameApplied": "Raw Name",
        "error": NORMALIZATION_ERROR,
    }
    assert_body_safe(response.text)
    assert_safe_logs(
        log_outputs, "[CHANNELS] Failed to normalize channel name 'Raw Name'"
    )


@pytest.mark.asyncio
async def test_bulk_normalization_failure_is_fixed_and_logs_scrubbed(
    async_client, log_outputs
):
    client = AsyncMock()
    client.get_channels.return_value = {"results": [], "count": 0, "next": None}
    client.get_streams.return_value = {"results": [], "count": 0, "next": None}
    client.create_channel.return_value = {"id": 11, "name": "Raw Name"}
    with (
        patch("routers.channels.get_client", return_value=client),
        patch(
            "routers.channels.get_normalization_engine",
            side_effect=upstream_error("runtime"),
        ),
        patch("routers.channels.journal"),
    ):
        result = await _commit_and_wait(
            async_client,
            {"operations": [{
                "type": "createChannel", "tempId": -1, "name": "Raw Name", "normalize": True,
            }]},
        )
    assert result["operationsApplied"] == 1
    assert result["normalizationFailures"] == [{
        "tempId": -1, "name": "Raw Name", "nameApplied": "Raw Name",
        "error": NORMALIZATION_ERROR,
    }]
    assert_body_safe(str(result))
    assert_safe_logs(
        log_outputs, "[CHANNELS-BULK] Failed to normalize channel name 'Raw Name'"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path,body,failing,make,status,marker",
    [
        ("POST", "/api/channels", {"name": "Raw Name"}, "create_channel",
         lambda: upstream_error("runtime"), 500, "[CHANNELS] Channel creation failed"),
        ("POST", "/api/channels", {"name": "Raw Name"}, "create_channel",
         mapped_error, 404, "[CHANNELS] Channel creation rejected by Dispatcharr"),
        ("PATCH", "/api/channels/5", {"name": "Renamed"}, "get_channel",
         lambda: upstream_error("runtime"), 500, "[CHANNELS] Failed to update channel 5"),
        ("PATCH", "/api/channels/5", {"name": "Renamed"}, "get_channel",
         mapped_error, 404, "[CHANNELS] Update channel 5 rejected by Dispatcharr"),
    ],
)
async def test_single_channel_write_failure_scrubs_logs(
    async_client, log_outputs, method, path, body, failing, make, status, marker
):
    client = AsyncMock()
    getattr(client, failing).side_effect = make()
    with (
        patch("routers.channels.get_client", return_value=client),
        patch("routers.channels.journal"),
    ):
        response = await _send(async_client, method, path, body)
    assert response.status_code == status
    if status == 500:
        assert response.json()["detail"] == "Internal server error"
        assert_body_safe(response.text)
    assert_safe_logs(log_outputs, marker)
