"""
Event Sync preview endpoint — dry-run matching, ZERO writes
(bead enhancedchannelmanager-ti939.1.4).

POST /api/channel-pipeline/event-sync-preview accepts a saved rule id OR an
inline event_sync_config, runs the read-only pre-flight, fetches the master
group's channels and the secondary groups' streams from Dispatcharr, and
resolves matches through services.event_sync_resolver.resolve_event_sync —
the EXACT function the Phase 1B attach path will call.

Pinned here (acceptance criteria):
* happy path — per-candidate rows with the full field contract, zero writes;
* pre-flight failure surfacing — preview still runs, failures in response;
* parse-failure surfacing — broken pattern is LOUD (grouped by group);
* count/detail reconciliation — summary counts equal the detail rows and
  sum to the fetched stream total.
"""
from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models import ChannelPipelineRule

MASTER_GROUP_ID = 10
SECONDARY_A = 20
SECONDARY_B = 30

GROUP_NAMES = {
    MASTER_GROUP_ID: "Peacock Events",
    SECONDARY_A: "Fubo Events",
    SECONDARY_B: "DAZN Events",
}

MASTER_CHANNELS = [
    {"id": 55, "name": "Peacock 14: Mercury vs. Aces @ 11 Jul 06:00 PM ET",
     "channel_group_id": MASTER_GROUP_ID},
    {"id": 56, "name": "Peacock 11: IMSA CTMP Qualifying @ 11 Jul 03:55 PM ET",
     "channel_group_id": MASTER_GROUP_ID},
    {"id": 57, "name": "Peacock 40: NO EVENT",
     "channel_group_id": MASTER_GROUP_ID},
]

# Group A: one attach + one ambiguous. Group B: one unmatched + one parse fail.
SECONDARY_STREAMS = {
    "Fubo Events": [
        {"id": 201, "name": "WNBA TV 01: Mercury vs. Aces @ 11 Jul 06:00 PM ET",
         "m3u_account": 1},
        {"id": 202, "name": "IMSA TV 03 : IMSA VPRC at CTMP R2 @ 11 Jul 03:55 PM ET",
         "m3u_account": 1},
    ],
    "DAZN Events": [
        {"id": 301, "name": "DAZN 05: Fury vs. Usyk @ 11 Jul 11:00 PM ET",
         "m3u_account": 2},
        {"id": 302, "name": "DAZN 09: NO EVENT", "m3u_account": 2},
    ],
}

M3U_ACCOUNTS = [
    {"id": 1, "name": "FuboProvider"},
    {"id": 2, "name": "DaznProvider"},
]

# All groups correctly configured: master auto-sync ON, secondaries OFF.
GROUP_SETTINGS_OK = {
    MASTER_GROUP_ID: {"auto_channel_sync": True},
    SECONDARY_A: {"auto_channel_sync": False},
    SECONDARY_B: {"auto_channel_sync": False},
}


def _config(**overrides) -> dict:
    config = {
        "master_group_id": MASTER_GROUP_ID,
        "secondary_group_ids": [SECONDARY_A, SECONDARY_B],
    }
    config.update(overrides)
    return config


def _mock_client(
    *,
    group_settings=None,
    master_channels=None,
    secondary_streams=None,
):
    """Mock Dispatcharr client. Mutating methods are AsyncMocks so tests can
    assert they were never awaited (the ZERO-writes invariant)."""
    group_settings = GROUP_SETTINGS_OK if group_settings is None else group_settings
    master_channels = MASTER_CHANNELS if master_channels is None else master_channels
    secondary_streams = (
        SECONDARY_STREAMS if secondary_streams is None else secondary_streams
    )

    client = MagicMock()
    client.get_all_m3u_group_settings = AsyncMock(return_value=group_settings)
    client.get_m3u_accounts = AsyncMock(return_value=M3U_ACCOUNTS)

    async def _group_name_for_id(group_id):
        return GROUP_NAMES.get(group_id)

    client._channel_group_name_for_id = AsyncMock(side_effect=_group_name_for_id)

    async def _get_channels(page=1, page_size=100, search=None, channel_group=None):
        results = [
            c for c in master_channels
            if channel_group is None or c["channel_group_id"] == channel_group
        ]
        return {"count": len(results), "next": None, "results": results}

    client.get_channels = AsyncMock(side_effect=_get_channels)

    async def _get_streams(page=1, page_size=100, search=None,
                           channel_group_name=None, m3u_account=None):
        results = list(secondary_streams.get(channel_group_name, []))
        return {"count": len(results), "next": None, "results": results}

    client.get_streams = AsyncMock(side_effect=_get_streams)

    # Mutating methods — must NEVER be called on the preview path.
    client.update_channel = AsyncMock()
    client.create_channel = AsyncMock()
    client.delete_channel = AsyncMock()
    client.update_stream = AsyncMock()
    client.add_stream_to_channel = AsyncMock()
    client.update_m3u_group_settings = AsyncMock()
    client.update_channel_group = AsyncMock()
    return client


def _assert_zero_writes(client) -> None:
    client.update_channel.assert_not_called()
    client.create_channel.assert_not_called()
    client.delete_channel.assert_not_called()
    client.update_stream.assert_not_called()
    client.add_stream_to_channel.assert_not_called()
    client.update_m3u_group_settings.assert_not_called()
    client.update_channel_group.assert_not_called()


async def _preview(async_client, client, body):
    with patch("routers.channel_pipeline.get_client", return_value=client):
        return await async_client.post(
            "/api/channel-pipeline/event-sync-preview", json=body
        )


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_inline_config_returns_full_contract_with_zero_writes(
        self, async_client
    ):
        client = _mock_client()
        resp = await _preview(
            async_client, client, {"event_sync_config": _config()}
        )
        assert resp.status_code == 200
        data = resp.json()

        # Pre-flight passed and is included.
        assert data["preflight"] == {"ok": True, "failures": []}

        # Summary counts.
        assert data["summary"]["secondary_streams"] == 4
        assert data["summary"]["would_attach"] == 1
        assert data["summary"]["ambiguous_skipped"] == 1
        assert data["summary"]["unmatched"] == 1
        assert data["summary"]["parse_failed"] == 1
        assert data["summary"]["master_channels"] == 3
        assert data["summary"]["master_channels_unparsed"] == 1

        by_id = {s["stream_id"]: s for s in data["streams"]}

        # Per-candidate row field contract (bead ti939.1.4).
        attach = by_id[201]
        assert attach["stream_name"] == (
            "WNBA TV 01: Mercury vs. Aces @ 11 Jul 06:00 PM ET"
        )
        assert attach["provider"] == "FuboProvider"
        assert attach["group_id"] == SECONDARY_A
        assert attach["parsed_title"] == "Mercury vs. Aces"
        assert attach["parsed_start"]  # ISO datetime string
        assert attach["disposition"] == "would_attach"
        assert attach["would_attach_master"] == {
            "channel_id": 55,
            "name": "Peacock 14: Mercury vs. Aces @ 11 Jul 06:00 PM ET",
        }
        candidate = attach["candidates"][0]
        assert candidate["master_channel_id"] == 55
        assert candidate["master_channel_name"] == MASTER_CHANNELS[0]["name"]
        assert candidate["band"] == "attach"
        assert candidate["team_verdict"] == "agree"
        assert candidate["score"] >= 0.8
        assert candidate["time_delta_minutes"] == 0.0
        assert candidate["reject_reason"] is None

        ambiguous = by_id[202]
        assert ambiguous["disposition"] == "ambiguous"
        assert ambiguous["would_attach_master"] is None
        assert ambiguous["candidates"][0]["band"] == "ambiguous"

        unmatched = by_id[301]
        assert unmatched["disposition"] == "unmatched"
        assert unmatched["candidates"] == []

        parse_failed = by_id[302]
        assert parse_failed["disposition"] == "parse_failed"
        assert parse_failed["unmatchable_reason"] == "no_parsed_time"

        # Master-as-ceiling hedge: unmatched list carries the evidence.
        assert [u["stream_id"] for u in data["unmatched_streams"]] == [301]
        assert data["unmatched_streams"][0]["provider"] == "DaznProvider"

        # Unparsable master surfaced loudly.
        assert data["unparsed_master_channels"] == ["Peacock 40: NO EVENT"]

        assert data["truncated"] is False
        _assert_zero_writes(client)

    @pytest.mark.asyncio
    async def test_saved_rule_id_previews_stored_config(
        self, async_client, test_session
    ):
        rule = ChannelPipelineRule(
            name="Event Sync Rule",
            enabled=True,
            priority=0,
            conditions=json.dumps([]),
            actions=json.dumps([]),
            sort_order="asc",
            orphan_action="delete",
            event_sync_config=json.dumps(_config(
                time_window_minutes=30, attach_threshold=0.80, enabled=True,
            )),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        test_session.add(rule)
        test_session.commit()
        test_session.refresh(rule)

        client = _mock_client()
        resp = await _preview(async_client, client, {"rule_id": rule.id})
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["would_attach"] == 1
        _assert_zero_writes(client)

    @pytest.mark.asyncio
    async def test_deterministic_output_for_fixed_inputs(self, async_client):
        client = _mock_client()
        first = await _preview(async_client, client, {"event_sync_config": _config()})
        second = await _preview(async_client, client, {"event_sync_config": _config()})
        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()


class TestPreflightSurfacing:
    @pytest.mark.asyncio
    async def test_preflight_failure_does_not_block_preview(self, async_client):
        # Master auto-sync OFF + one secondary ON: both misconfigurations
        # must SURFACE while the preview still runs to completion.
        client = _mock_client(group_settings={
            MASTER_GROUP_ID: {"auto_channel_sync": False},
            SECONDARY_A: {"auto_channel_sync": True},
            SECONDARY_B: {"auto_channel_sync": False},
        })
        resp = await _preview(
            async_client, client, {"event_sync_config": _config()}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["preflight"]["ok"] is False
        checks = {(f["group_id"], f["check"]) for f in data["preflight"]["failures"]}
        assert (MASTER_GROUP_ID, "master_auto_sync_on") in checks
        assert (SECONDARY_A, "secondary_auto_sync_off") in checks
        # The preview itself still resolved matches.
        assert data["summary"]["secondary_streams"] == 4
        assert data["summary"]["would_attach"] == 1
        _assert_zero_writes(client)


class TestParseFailureSurfacing:
    @pytest.mark.asyncio
    async def test_broken_pattern_is_loud_and_grouped(self, async_client):
        # A pattern that never matches: EVERY stream in every group lands in
        # parse_failures, grouped by group — the silently-broken-pattern
        # alarm.
        client = _mock_client()
        config = _config(patterns=[{
            "name": "broken",
            "title_pattern": r"^ZZZ-(?P<title>.+)-ZZZ$",
        }])
        resp = await _preview(async_client, client, {"event_sync_config": config})
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["parse_failed"] == 4
        assert data["summary"]["would_attach"] == 0

        groups = {(g["group_id"], g["reason"]) for g in data["parse_failures"]}
        assert (SECONDARY_A, "parse_failure") in groups
        assert (SECONDARY_B, "parse_failure") in groups
        by_group = {g["group_id"]: g for g in data["parse_failures"]}
        assert by_group[SECONDARY_A]["count"] == 2
        assert by_group[SECONDARY_A]["group_name"] == "Fubo Events"
        assert len(by_group[SECONDARY_A]["stream_names"]) == 2
        # Every master fails the broken pattern too — loud on that side.
        assert len(data["unparsed_master_channels"]) == 3
        _assert_zero_writes(client)


class TestReconciliation:
    @pytest.mark.asyncio
    async def test_summary_counts_reconcile_exactly_with_detail_rows(
        self, async_client
    ):
        client = _mock_client()
        resp = await _preview(
            async_client, client, {"event_sync_config": _config()}
        )
        data = resp.json()
        summary = data["summary"]
        streams = data["streams"]

        by_disposition = {}
        for s in streams:
            by_disposition.setdefault(s["disposition"], []).append(s)

        assert summary["would_attach"] == len(by_disposition.get("would_attach", []))
        assert summary["ambiguous_skipped"] == len(by_disposition.get("ambiguous", []))
        assert summary["unmatched"] == len(by_disposition.get("unmatched", []))
        assert summary["parse_failed"] == len(by_disposition.get("parse_failed", []))
        assert (
            summary["would_attach"] + summary["ambiguous_skipped"]
            + summary["unmatched"] + summary["parse_failed"]
            == summary["secondary_streams"] == len(streams)
        )
        # Auxiliary lists reconcile too.
        assert len(data["unmatched_streams"]) == summary["unmatched"]
        assert (
            sum(g["count"] for g in data["parse_failures"])
            == summary["parse_failed"]
        )


class TestValidationAndErrors:
    @pytest.mark.asyncio
    async def test_invalid_inline_config_is_400_with_teaching_errors(
        self, async_client
    ):
        client = _mock_client()
        resp = await _preview(
            async_client, client,
            {"event_sync_config": {"master_group_id": 10}},  # no secondaries
        )
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert any("secondary_group_ids" in e for e in detail["errors"])
        client.get_all_m3u_group_settings.assert_not_called()

    @pytest.mark.asyncio
    async def test_unknown_rule_id_is_404(self, async_client):
        client = _mock_client()
        resp = await _preview(async_client, client, {"rule_id": 999999})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_rule_without_event_sync_config_is_400(
        self, async_client, test_session
    ):
        rule = ChannelPipelineRule(
            name="Standard Rule",
            enabled=True,
            priority=0,
            conditions=json.dumps([]),
            actions=json.dumps([]),
            sort_order="asc",
            orphan_action="delete",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        test_session.add(rule)
        test_session.commit()
        test_session.refresh(rule)

        client = _mock_client()
        resp = await _preview(async_client, client, {"rule_id": rule.id})
        assert resp.status_code == 400
        assert "event_sync" in str(resp.json()["detail"])

    @pytest.mark.asyncio
    async def test_neither_or_both_sources_rejected(self, async_client):
        client = _mock_client()
        neither = await _preview(async_client, client, {})
        both = await _preview(
            async_client, client,
            {"rule_id": 1, "event_sync_config": _config()},
        )
        assert neither.status_code == 422
        assert both.status_code == 422
