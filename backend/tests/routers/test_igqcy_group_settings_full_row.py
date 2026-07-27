"""Full-row group-settings writes vs Dispatcharr's upsert (bead enhancedchannelmanager-igqcy).

Dispatcharr v0.25.0+ ``update_group_settings`` (their apps/m3u/api_views.py)
is a FULL-ROW upsert: ``bulk_create(update_conflicts=True, update_fields=
[enabled, auto_channel_sync, auto_sync_channel_start, auto_sync_channel_end,
custom_properties])`` built with ``setting.get(...)`` defaults — every field
omitted from a row is silently reset (enabled -> True, auto_channel_sync ->
False, start/end -> None, custom_properties -> {}). Proven live against
v0.27.2: an enabled-only write through ECM reset auto_channel_sync
true -> false and auto_sync_channel_start 1.0 -> null (bead 478fe QA pass).

These tests pin ECM's defense: every row forwarded to the Dispatcharr client
is completed from the group's CURRENT stored state before the write, so a
partial-intent save (MCP tools, linked-account cascade, Sync Groups, any
direct API caller) can never clobber fields it did not mean to touch —
including ``auto_sync_channel_end`` (new in v0.25.0) and unknown
``custom_properties`` keys Dispatcharr's sync consumes
(channel_numbering_mode, force_dummy_epg, ...).

The account fixture is the REAL v0.27.2 payload recorded during the QA pass
(tests/fixtures/bd_igqcy/dispatcharr_v0272_m3u_account.json) — note its
``channel_groups`` entries carry NO ``name`` field (serializer change in
v0.24.0) and DO carry ``auto_sync_channel_end``.
"""
import copy
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from routers.m3u import merge_group_settings_row

FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "fixtures" / "bd_igqcy" / "dispatcharr_v0272_m3u_account.json"
)

# The "native-configured row" from the live QA scenario: a group fully
# configured through Dispatcharr's own UI/API on v0.27.2 — auto-sync ON,
# start AND end set, custom_properties carrying both an ECM-known key and
# keys ECM does not model (added by Dispatcharr after v0.24).
NATIVE_ROW = {
    "channel_group": 1304,
    "enabled": True,
    "auto_channel_sync": True,
    "auto_sync_channel_start": 1,
    "auto_sync_channel_end": 500,
    "custom_properties": {
        "group_override": 7,                    # ECM-known key
        "channel_numbering_mode": "assigned",   # v0.27.2 sync key, unknown to ECM
        "channel_numbering_fallback": "next",   # v0.27.2 sync key, unknown to ECM
        "force_dummy_epg": True,                # v0.27.2 sync key, unknown to ECM
    },
    "is_stale": False,
    "last_seen": "2026-07-16T23:34:02.303249Z",
    "stream_count": 56,
}


def _account() -> dict:
    """Real recorded v0.27.2 account payload with its group row replaced by
    the native-configured scenario row."""
    account = json.loads(FIXTURE.read_text())
    account["channel_groups"] = [copy.deepcopy(NATIVE_ROW)]
    return account


def _mock_client() -> AsyncMock:
    client = AsyncMock()
    client.get_m3u_account.return_value = _account()
    client.get_channel_groups.return_value = [{"id": 1304, "name": "HD Homerun"}]
    client.update_m3u_group_settings.return_value = {
        "message": "Group settings updated successfully"
    }
    return client


def _sent_row(client, channel_group: int = 1304) -> dict:
    body = client.update_m3u_group_settings.await_args.args[1]
    return next(
        r for r in body["group_settings"] if r["channel_group"] == channel_group
    )


class TestMergeGroupSettingsRow:
    """Unit pins on the shared merge helper."""

    def test_absent_fields_fill_from_current(self):
        merged = merge_group_settings_row(
            NATIVE_ROW, {"channel_group": 1304, "enabled": False}
        )
        assert merged["enabled"] is False
        assert merged["auto_channel_sync"] is True
        assert merged["auto_sync_channel_start"] == 1
        assert merged["auto_sync_channel_end"] == 500
        assert merged["custom_properties"] == NATIVE_ROW["custom_properties"]

    def test_present_fields_win_even_when_null(self):
        """Explicit null is an intentional clear, not an omission."""
        merged = merge_group_settings_row(
            NATIVE_ROW,
            {"channel_group": 1304, "auto_sync_channel_start": None},
        )
        assert merged["auto_sync_channel_start"] is None
        assert merged["auto_sync_channel_end"] == 500

    def test_custom_properties_taken_verbatim_when_present(self):
        """A caller that sends custom_properties owns the whole dict — no
        deep merge (a deep merge would make clearing a key impossible)."""
        merged = merge_group_settings_row(
            NATIVE_ROW,
            {"channel_group": 1304, "custom_properties": {"group_override": 9}},
        )
        assert merged["custom_properties"] == {"group_override": 9}

    def test_unknown_current_row_passes_incoming_through(self):
        merged = merge_group_settings_row(None, {"channel_group": 42, "enabled": True})
        assert merged == {"channel_group": 42, "enabled": True}

    def test_read_only_serializer_fields_are_not_copied(self):
        """is_stale/last_seen/stream_count are serializer output, not upsert
        fields — they must not leak into the write payload."""
        merged = merge_group_settings_row(
            NATIVE_ROW, {"channel_group": 1304, "enabled": False}
        )
        assert "is_stale" not in merged
        assert "last_seen" not in merged
        assert "stream_count" not in merged


class TestGroupSettingsEndpointCompletesRows:
    """PATCH /api/m3u/accounts/{id}/group-settings — the choke point every
    ECM writer (both modals, Sync Groups, linked cascade, MCP tools) goes
    through — must forward complete rows."""

    @pytest.mark.asyncio
    async def test_enabled_only_write_preserves_native_config(self, async_client):
        """The exact live-proven clobber: an enabled-only row must NOT reset
        auto_channel_sync / start / end / custom_properties."""
        client = _mock_client()
        with patch("routers.m3u.get_client", return_value=client), \
             patch("routers.m3u.journal"):
            resp = await async_client.patch(
                "/api/m3u/accounts/11/group-settings",
                json={"group_settings": [{"channel_group": 1304, "enabled": False}]},
            )

        assert resp.status_code == 200
        sent = _sent_row(client)
        assert sent["enabled"] is False
        assert sent["auto_channel_sync"] is True
        assert sent["auto_sync_channel_start"] == 1
        assert sent["auto_sync_channel_end"] == 500
        assert sent["custom_properties"] == NATIVE_ROW["custom_properties"]

    @pytest.mark.asyncio
    async def test_unknown_custom_properties_keys_survive_round_trip(self, async_client):
        """A row omitting custom_properties keeps every unknown key verbatim."""
        client = _mock_client()
        with patch("routers.m3u.get_client", return_value=client), \
             patch("routers.m3u.journal"):
            resp = await async_client.patch(
                "/api/m3u/accounts/11/group-settings",
                json={"group_settings": [
                    {"channel_group": 1304, "auto_sync_channel_start": 100}
                ]},
            )

        assert resp.status_code == 200
        sent = _sent_row(client)
        assert sent["auto_sync_channel_start"] == 100
        cp = sent["custom_properties"]
        assert cp["channel_numbering_mode"] == "assigned"
        assert cp["channel_numbering_fallback"] == "next"
        assert cp["force_dummy_epg"] is True
        assert cp["group_override"] == 7

    @pytest.mark.asyncio
    async def test_complete_field_set_always_emitted(self, async_client):
        """Every forwarded row carries the full Dispatcharr upsert field set."""
        client = _mock_client()
        with patch("routers.m3u.get_client", return_value=client), \
             patch("routers.m3u.journal"):
            await async_client.patch(
                "/api/m3u/accounts/11/group-settings",
                json={"group_settings": [{"channel_group": 1304, "enabled": True}]},
            )

        sent = _sent_row(client)
        for field in (
            "channel_group", "enabled", "auto_channel_sync",
            "auto_sync_channel_start", "auto_sync_channel_end",
            "custom_properties",
        ):
            assert field in sent, f"row missing upsert field {field!r}"

    @pytest.mark.asyncio
    async def test_row_for_group_not_on_account_passes_through(self, async_client):
        """No current state to merge — the row is forwarded as-is."""
        client = _mock_client()
        with patch("routers.m3u.get_client", return_value=client), \
             patch("routers.m3u.journal"):
            resp = await async_client.patch(
                "/api/m3u/accounts/11/group-settings",
                json={"group_settings": [{"channel_group": 9999, "enabled": True}]},
            )

        assert resp.status_code == 200
        sent = _sent_row(client, channel_group=9999)
        assert sent == {"channel_group": 9999, "enabled": True}

    @pytest.mark.asyncio
    async def test_body_without_group_settings_list_is_untouched(self, async_client):
        """Legacy/odd bodies are forwarded unchanged (no crash, no rewrite)."""
        client = _mock_client()
        with patch("routers.m3u.get_client", return_value=client), \
             patch("routers.m3u.journal"):
            resp = await async_client.patch(
                "/api/m3u/accounts/11/group-settings",
                json={"auto_channel_sync": True},
            )

        assert resp.status_code == 200
        client.update_m3u_group_settings.assert_called_once_with(
            11, {"auto_channel_sync": True}
        )


class TestGuidedToggleSendsCompleteRow:
    """The Event Sync guided toggle writes through the same merge helper —
    its 'all other fields preserved verbatim' contract now includes
    auto_sync_channel_end and unknown custom_properties keys."""

    @pytest.mark.asyncio
    async def test_toggle_preserves_end_and_unknown_custom_properties(self, async_client):
        client = _mock_client()
        with patch("routers.m3u.get_client", return_value=client), \
             patch("journal.log_entry"):
            resp = await async_client.post(
                "/api/m3u/accounts/11/group-auto-sync-toggle",
                json={
                    "channel_group_id": 1304,
                    "auto_channel_sync": False,
                    "confirm": True,
                },
            )

        assert resp.status_code == 200
        sent = _sent_row(client)
        assert sent["auto_channel_sync"] is False
        assert sent["enabled"] is True
        assert sent["auto_sync_channel_start"] == 1
        assert sent["auto_sync_channel_end"] == 500
        assert sent["custom_properties"] == NATIVE_ROW["custom_properties"]
