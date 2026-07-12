"""Guided-setup auto_channel_sync toggle (bead enhancedchannelmanager-ti939.3.4).

POST /api/m3u/accounts/{account_id}/group-auto-sync-toggle — the ONLY
Dispatcharr group-settings write reachable from the Event Sync guided
setup. Hard constraints pinned here (security, locked at planning):

* **Confirm-gated**: ``confirm: true`` is required; without it the request
  is refused with a teaching error and NOTHING is written — the toggle can
  never happen as a side effect.
* **Both directions**: enable (master group) and disable (secondary
  group), each writing exactly ONE group's record with every other field
  preserved verbatim.
* **Journaled per toggle** (routers/settings.py's
  allow_multi_provider_auto_sync precedent): before/after values plus the
  recovery-breadcrumb note — snapshot restore does NOT revert Dispatcharr
  group settings.
* **Admin-gated** via ``RequireAdminIfEnabled``.
* **No-op honesty**: a request for the current value changes nothing and
  journals nothing.

The endpoint deliberately lives in routers/m3u.py — OUTSIDE the event_sync
feature modules scanned by the AST no-group-writes gate
(tests/unit/test_event_sync_rollback_roundtrip.py), which stays green
as-is.
"""
import pytest
from unittest.mock import AsyncMock, patch


def _account(auto_sync: bool, group_id: int = 20) -> dict:
    return {
        "id": 1,
        "name": "FuboProvider",
        "channel_groups": [{
            "id": 555,
            "channel_group": group_id,
            "enabled": True,
            "auto_channel_sync": auto_sync,
            "auto_sync_channel_start": 4000,
            "custom_properties": {"group_override": 7},
        }],
    }


def _mock_client(auto_sync: bool, group_id: int = 20) -> AsyncMock:
    client = AsyncMock()
    client.get_m3u_account.return_value = _account(auto_sync, group_id)
    client.get_channel_groups.return_value = [
        {"id": group_id, "name": "FIFA | World Cup"},
    ]
    client.update_m3u_group_settings.return_value = {"id": 1}
    return client


def _body(**overrides) -> dict:
    body = {"channel_group_id": 20, "auto_channel_sync": False,
            "confirm": True}
    body.update(overrides)
    return body


class TestConfirmGate:
    """The toggle is reachable ONLY through explicit confirmation."""

    @pytest.mark.asyncio
    async def test_absent_confirm_is_refused_and_writes_nothing(
        self, async_client
    ):
        client = _mock_client(auto_sync=True)
        body = _body()
        del body["confirm"]
        with patch("routers.m3u.get_client", return_value=client), \
             patch("journal.log_entry") as mock_journal:
            resp = await async_client.post(
                "/api/m3u/accounts/1/group-auto-sync-toggle", json=body
            )

        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "confirm" in detail
        assert "explicit operator action" in detail
        assert "never" in detail
        client.update_m3u_group_settings.assert_not_called()
        mock_journal.assert_not_called()

    @pytest.mark.asyncio
    async def test_confirm_false_is_refused_and_writes_nothing(
        self, async_client
    ):
        client = _mock_client(auto_sync=True)
        with patch("routers.m3u.get_client", return_value=client), \
             patch("journal.log_entry") as mock_journal:
            resp = await async_client.post(
                "/api/m3u/accounts/1/group-auto-sync-toggle",
                json=_body(confirm=False),
            )

        assert resp.status_code == 400
        client.update_m3u_group_settings.assert_not_called()
        mock_journal.assert_not_called()


class TestBothDirections:
    @pytest.mark.asyncio
    async def test_disable_secondary_writes_one_preserved_record(
        self, async_client
    ):
        """Direction 1 — secondary with auto-sync ON gets turned OFF; the
        single-group payload preserves every other field verbatim."""
        client = _mock_client(auto_sync=True)
        with patch("routers.m3u.get_client", return_value=client), \
             patch("journal.log_entry"):
            resp = await async_client.post(
                "/api/m3u/accounts/1/group-auto-sync-toggle",
                json=_body(auto_channel_sync=False),
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["changed"] is True
        assert data["auto_channel_sync"] is False
        assert data["was"] is True
        assert data["group_name"] == "FIFA | World Cup"
        assert data["account_name"] == "FuboProvider"
        client.update_m3u_group_settings.assert_awaited_once_with(1, {
            "group_settings": [{
                "channel_group": 20,
                "enabled": True,
                "auto_channel_sync": False,
                "auto_sync_channel_start": 4000,
                "custom_properties": {"group_override": 7},
                "id": 555,
            }],
        })

    @pytest.mark.asyncio
    async def test_enable_master_direction(self, async_client):
        """Direction 2 — master with auto-sync OFF gets turned ON."""
        client = _mock_client(auto_sync=False, group_id=10)
        with patch("routers.m3u.get_client", return_value=client), \
             patch("journal.log_entry"):
            resp = await async_client.post(
                "/api/m3u/accounts/1/group-auto-sync-toggle",
                json=_body(channel_group_id=10, auto_channel_sync=True),
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["changed"] is True
        assert data["auto_channel_sync"] is True
        assert data["was"] is False
        sent = client.update_m3u_group_settings.await_args.args[1]
        assert sent["group_settings"][0]["auto_channel_sync"] is True
        assert sent["group_settings"][0]["channel_group"] == 10


class TestJournaling:
    @pytest.mark.asyncio
    async def test_every_toggle_writes_the_recovery_breadcrumb(
        self, async_client
    ):
        client = _mock_client(auto_sync=True)
        with patch("routers.m3u.get_client", return_value=client), \
             patch("journal.log_entry") as mock_journal:
            resp = await async_client.post(
                "/api/m3u/accounts/1/group-auto-sync-toggle",
                json=_body(auto_channel_sync=False),
            )

        assert resp.status_code == 200
        mock_journal.assert_called_once()
        kwargs = mock_journal.call_args.kwargs
        assert kwargs["category"] == "m3u"
        assert kwargs["action_type"] == "update"
        assert kwargs["entity_id"] == 1
        assert kwargs["entity_name"] == "FuboProvider"
        assert "Guided setup" in kwargs["description"]
        assert "auto_channel_sync OFF" in kwargs["description"]
        assert "FIFA | World Cup" in kwargs["description"]
        # The recovery breadcrumb: snapshot restore does NOT revert
        # Dispatcharr group settings.
        assert "Snapshot restore does NOT revert" in kwargs["description"]
        assert kwargs["before_value"]["auto_channel_sync"] is True
        assert kwargs["after_value"]["auto_channel_sync"] is False

    @pytest.mark.asyncio
    async def test_noop_request_changes_nothing_and_journals_nothing(
        self, async_client
    ):
        client = _mock_client(auto_sync=False)
        with patch("routers.m3u.get_client", return_value=client), \
             patch("journal.log_entry") as mock_journal:
            resp = await async_client.post(
                "/api/m3u/accounts/1/group-auto-sync-toggle",
                json=_body(auto_channel_sync=False),
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["changed"] is False
        assert data["auto_channel_sync"] is False
        client.update_m3u_group_settings.assert_not_called()
        mock_journal.assert_not_called()


class TestErrors:
    @pytest.mark.asyncio
    async def test_unknown_group_returns_404(self, async_client):
        client = _mock_client(auto_sync=True)
        with patch("routers.m3u.get_client", return_value=client):
            resp = await async_client.post(
                "/api/m3u/accounts/1/group-auto-sync-toggle",
                json=_body(channel_group_id=999),
            )

        assert resp.status_code == 404
        assert "999" in resp.json()["detail"]
        client.update_m3u_group_settings.assert_not_called()

    @pytest.mark.asyncio
    async def test_upstream_failure_is_a_500_without_a_journal_entry(
        self, async_client
    ):
        client = _mock_client(auto_sync=True)
        client.update_m3u_group_settings.side_effect = RuntimeError("boom")
        with patch("routers.m3u.get_client", return_value=client), \
             patch("journal.log_entry") as mock_journal:
            resp = await async_client.post(
                "/api/m3u/accounts/1/group-auto-sync-toggle",
                json=_body(auto_channel_sync=False),
            )

        assert resp.status_code == 500
        # No journal entry for a write that did not happen.
        mock_journal.assert_not_called()


class TestAdminGate:
    """Admin-gated like the other duplicate-channel-risk toggles."""

    @pytest.mark.asyncio
    async def test_non_admin_is_forbidden_when_auth_enabled(
        self, async_client
    ):
        from fastapi import HTTPException, status
        from main import app
        from auth import RequireAdminIfEnabled as _prebuilt

        async def _reject() -> None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin access required",
            )

        client = _mock_client(auto_sync=True)
        app.dependency_overrides[_prebuilt.dependency] = _reject
        try:
            with patch("routers.m3u.get_client", return_value=client):
                resp = await async_client.post(
                    "/api/m3u/accounts/1/group-auto-sync-toggle",
                    json=_body(),
                )
        finally:
            app.dependency_overrides.pop(_prebuilt.dependency, None)

        assert resp.status_code == 403
        client.update_m3u_group_settings.assert_not_called()
