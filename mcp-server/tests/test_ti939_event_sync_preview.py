"""preview_event_sync MCP tool (bead enhancedchannelmanager-ti939.1.4).

Pins:
* the tool calls the ac_event_sync_preview endpoint (POST
  /api/channel-pipeline/event-sync-preview) through call_endpoint — the
  contract-checked path — with exactly one of rule_id / event_sync_config;
* zero write endpoints are ever touched (read-only by construction: the
  ONLY backend call is the preview endpoint);
* pre-flight failures and parse failures from the response surface in the
  text report;
* the exactly-one-source rule is enforced client-side with a teaching error.
"""
import pytest
from unittest.mock import AsyncMock, patch


def _make_mcp_and_register():
    from mcp.server.fastmcp import FastMCP
    from tools.channel_pipeline import register

    mcp = FastMCP("test")
    register(mcp)
    return mcp


def _preview_response(**overrides):
    response = {
        "preflight": {"ok": True, "failures": []},
        "summary": {
            "secondary_streams": 3,
            "would_attach": 1,
            "ambiguous_skipped": 1,
            "unmatched": 1,
            "parse_failed": 0,
            "master_channels": 2,
            "master_channels_unparsed": 0,
        },
        "streams": [
            {
                "stream_id": 201,
                "stream_name": "WNBA TV 01: Mercury vs. Aces @ 11 Jul 06:00 PM ET",
                "group_id": 20, "provider": "FuboProvider",
                "parsed_title": "Mercury vs. Aces",
                "parsed_start": "2026-07-11T18:00:00-04:00",
                "matched_pattern": "slot-title-day-first-date",
                "disposition": "would_attach",
                "unmatchable_reason": None,
                "would_attach_master": {"channel_id": 55, "name": "Peacock 14: Mercury vs. Aces @ 11 Jul 06:00 PM ET"},
                "candidates": [{
                    "master_channel_name": "Peacock 14: Mercury vs. Aces @ 11 Jul 06:00 PM ET",
                    "master_channel_id": 55,
                    "master_parsed_title": "Mercury vs. Aces",
                    "master_parsed_start": "2026-07-11T18:00:00-04:00",
                    "score": 1.0, "band": "attach", "team_verdict": "agree",
                    "time_delta_minutes": 0.0, "reject_reason": None,
                }],
            },
            {
                "stream_id": 202,
                "stream_name": "IMSA TV 03 : IMSA VPRC at CTMP R2 @ 11 Jul 03:55 PM ET",
                "group_id": 20, "provider": "FuboProvider",
                "parsed_title": "IMSA VPRC at CTMP R2",
                "parsed_start": "2026-07-11T15:55:00-04:00",
                "matched_pattern": "slot-title-day-first-date",
                "disposition": "ambiguous",
                "unmatchable_reason": None,
                "would_attach_master": None,
                "candidates": [{
                    "master_channel_name": "Peacock 11: IMSA CTMP Qualifying @ 11 Jul 03:55 PM ET",
                    "master_channel_id": 56,
                    "master_parsed_title": "IMSA CTMP Qualifying",
                    "master_parsed_start": "2026-07-11T15:55:00-04:00",
                    "score": 0.71, "band": "ambiguous", "team_verdict": "absent",
                    "time_delta_minutes": 0.0, "reject_reason": None,
                }],
            },
            {
                "stream_id": 301,
                "stream_name": "DAZN 05: Fury vs. Usyk @ 11 Jul 11:00 PM ET",
                "group_id": 30, "provider": "DaznProvider",
                "parsed_title": "Fury vs. Usyk",
                "parsed_start": "2026-07-11T23:00:00-04:00",
                "matched_pattern": "slot-title-day-first-date",
                "disposition": "unmatched",
                "unmatchable_reason": None,
                "would_attach_master": None,
                "candidates": [],
            },
        ],
        "unmatched_streams": [
            {"stream_id": 301,
             "stream_name": "DAZN 05: Fury vs. Usyk @ 11 Jul 11:00 PM ET",
             "group_id": 30, "provider": "DaznProvider",
             "parsed_title": "Fury vs. Usyk",
             "parsed_start": "2026-07-11T23:00:00-04:00",
             "best_candidate": None},
        ],
        "parse_failures": [],
        "unparsed_master_channels": [],
        "truncated": False,
    }
    response.update(overrides)
    return response


async def _call_tool(mcp, client, args):
    with patch("tools.channel_pipeline.get_ecm_client", return_value=client):
        result = await mcp.call_tool("preview_event_sync", args)
    return result[0][0].text


class TestPreviewEventSync:
    @pytest.mark.asyncio
    async def test_inline_config_calls_only_the_preview_endpoint(self):
        mcp = _make_mcp_and_register()
        calls = []

        async def call_endpoint_side_effect(endpoint, **kwargs):
            calls.append((endpoint.name, kwargs.get("body")))
            return _preview_response()

        client = AsyncMock()
        client.call_endpoint.side_effect = call_endpoint_side_effect
        config = {"master_group_id": 10, "secondary_group_ids": [20, 30]}
        text = await _call_tool(mcp, client, {"event_sync_config": config})

        assert [name for name, _ in calls] == ["ac_event_sync_preview"]
        assert calls[0][1] == {"event_sync_config": config}
        assert "zero writes" in text
        assert "Pre-flight: OK" in text
        assert "1 would attach" in text
        assert "ATTACH" in text and "REVIEW" in text and "UNMATCHED" in text
        assert "channel 55" in text
        # A plain default-threshold in-window match carries no provenance suffix.
        assert "matched via" not in text

    @pytest.mark.asyncio
    async def test_matched_via_provenance_suffix_on_attach_line(self):
        # S5 (bead sf8dj): would-attach rows admitted only by an optional
        # relaxation get a "(matched via: ...)" suffix so the operator can
        # double-check them.
        mcp = _make_mcp_and_register()
        response = _preview_response()
        response["streams"][0]["matched_via"] = [
            {"key": "time_window_ignored", "label": "time ignored"},
            {"key": "assume_current_date", "label": "assumed date"},
        ]
        client = AsyncMock()
        client.call_endpoint.return_value = response
        text = await _call_tool(
            mcp, client,
            {"event_sync_config": {"master_group_id": 10, "secondary_group_ids": [20]}},
        )
        assert "matched via: time ignored, assumed date" in text

    @pytest.mark.asyncio
    async def test_include_master_group_streams_flag_forwarded_verbatim(self):
        # bead 6xxmp: the tool forwards event_sync_config as-is, so the new
        # flag reaches the backend validator with no MCP-side handling.
        mcp = _make_mcp_and_register()
        bodies = []

        async def call_endpoint_side_effect(endpoint, **kwargs):
            bodies.append(kwargs.get("body"))
            return _preview_response()

        client = AsyncMock()
        client.call_endpoint.side_effect = call_endpoint_side_effect
        config = {
            "master_group_id": 10,
            "secondary_group_ids": [20],
            "include_master_group_streams": True,
            "assume_current_date": True,
        }
        await _call_tool(mcp, client, {"event_sync_config": config})
        assert bodies == [{"event_sync_config": config}]

    @pytest.mark.asyncio
    async def test_rule_id_sends_rule_id_body(self):
        mcp = _make_mcp_and_register()
        bodies = []

        async def call_endpoint_side_effect(endpoint, **kwargs):
            bodies.append(kwargs.get("body"))
            return _preview_response()

        client = AsyncMock()
        client.call_endpoint.side_effect = call_endpoint_side_effect
        await _call_tool(mcp, client, {"rule_id": 7})
        assert bodies == [{"rule_id": 7}]

    @pytest.mark.asyncio
    async def test_preflight_failures_surface_loudly(self):
        mcp = _make_mcp_and_register()
        client = AsyncMock()
        client.call_endpoint.return_value = _preview_response(preflight={
            "ok": False,
            "failures": [{
                "group_id": 10, "role": "master",
                "check": "master_auto_sync_on",
                "expected": "auto_channel_sync ON",
                "got": "auto_channel_sync OFF",
                "message": "Master group 10 has auto_channel_sync OFF in Dispatcharr",
            }],
        })
        text = await _call_tool(
            mcp, client,
            {"event_sync_config": {"master_group_id": 10,
                                   "secondary_group_ids": [20]}},
        )
        assert "Pre-flight: FAILED" in text
        assert "auto_channel_sync OFF" in text
        assert "ECM never toggles" in text

    @pytest.mark.asyncio
    async def test_parse_failures_surface_loudly(self):
        mcp = _make_mcp_and_register()
        client = AsyncMock()
        client.call_endpoint.return_value = _preview_response(
            parse_failures=[{
                "group_id": 30, "group_name": "DAZN Events",
                "reason": "parse_failure", "count": 12,
                "stream_names": ["DAZN 01: foo", "DAZN 02: bar"],
            }],
        )
        text = await _call_tool(
            mcp, client,
            {"event_sync_config": {"master_group_id": 10,
                                   "secondary_group_ids": [30]}},
        )
        assert "PARSE FAILURES in group 30" in text
        assert "12 stream(s)" in text

    @pytest.mark.asyncio
    async def test_exactly_one_source_enforced(self):
        mcp = _make_mcp_and_register()
        client = AsyncMock()
        neither = await _call_tool(mcp, client, {})
        both = await _call_tool(
            mcp, client,
            {"rule_id": 1,
             "event_sync_config": {"master_group_id": 10,
                                   "secondary_group_ids": [20]}},
        )
        assert "exactly one" in neither
        assert "exactly one" in both
        client.call_endpoint.assert_not_called()

    @pytest.mark.asyncio
    async def test_backend_error_is_reported_not_raised(self):
        mcp = _make_mcp_and_register()
        client = AsyncMock()
        client.call_endpoint.side_effect = RuntimeError("backend down")
        text = await _call_tool(mcp, client, {"rule_id": 1})
        assert "Error previewing event sync" in text
        assert "backend down" in text


class TestPromotionRendering:
    """bead ti939.4.1: the would-promote plan renders as its own block —
    and ONLY when the payload carries it (opt-in invisibility)."""

    def _promotion_block(self):
        return {
            "enabled": True,
            "target_group_id": 40,
            "would_promote": 2,
            "would_promote_streams": 3,
            "would_create": 1,
            "would_attach_existing": 1,
            "cap": 25,
            "capped": True,
            "cap_overage": 4,
            "units": [
                {
                    "channel_name": "Fury Vs. Usyk @ Jul 11 11:00 PM",
                    "action": "create",
                    "event_key": "fury vs. usyk|2026-07-12T03:00:00+00:00",
                    "dateless": False,
                    "existing_channel_id": None,
                    "streams": [
                        {"stream_id": 301,
                         "stream_name": "DAZN 05: Fury vs. Usyk @ 11 Jul 11:00 PM ET",
                         "provider": "DaznProvider", "group_id": 30,
                         "disposition": "unmatched"},
                        {"stream_id": 555,
                         "stream_name": "FightBox 02: Fury vs. Usyk @ 11 Jul 11:00 PM ET",
                         "provider": "FightBox", "group_id": 20,
                         "disposition": "unmatched"},
                    ],
                },
                {
                    "channel_name": "Tyson Vs. Paul @ Jul 11 09:00 PM",
                    "action": "attach_existing",
                    "event_key": "tyson vs. paul|2026-07-12T01:00:00+00:00",
                    "dateless": False,
                    "existing_channel_id": 901,
                    "streams": [
                        {"stream_id": 302,
                         "stream_name": "DAZN 06: Tyson vs. Paul @ 11 Jul 09:00 PM ET",
                         "provider": "DaznProvider", "group_id": 30,
                         "disposition": "unmatched"},
                    ],
                },
            ],
        }

    @pytest.mark.asyncio
    async def test_promotion_block_renders_counts_units_and_cap(self):
        mcp = _make_mcp_and_register()
        client = AsyncMock()
        client.call_endpoint.return_value = _preview_response(
            promotion=self._promotion_block(),
        )
        text = await _call_tool(
            mcp, client,
            {"event_sync_config": {"master_group_id": 10,
                                   "secondary_group_ids": [20, 30],
                                   "promote_unmatched": True,
                                   "promote_target_group_id": 40}},
        )
        assert "Would promote: 2 channel(s) in target group 40" in text
        assert "(1 new, 1 adopt existing)" in text
        assert "creates AND deletes channels" in text
        assert "promotion capped at 25" in text and "4 unit(s) deferred" in text
        assert "PROMOTE [create] 'Fury Vs. Usyk @ Jul 11 11:00 PM'" in text
        assert "PROMOTE [attach_existing] 'Tyson Vs. Paul @ Jul 11 09:00 PM'" in text
        assert "'DAZN 05: Fury vs. Usyk @ 11 Jul 11:00 PM ET' [DaznProvider]" in text

    @pytest.mark.asyncio
    async def test_no_promotion_block_renders_nothing(self):
        mcp = _make_mcp_and_register()
        client = AsyncMock()
        client.call_endpoint.return_value = _preview_response()
        text = await _call_tool(
            mcp, client,
            {"event_sync_config": {"master_group_id": 10,
                                   "secondary_group_ids": [20, 30]}},
        )
        assert "Would promote" not in text
        assert "PROMOTE" not in text
