"""Event Sync operator-exclusion MCP tools (bead enhancedchannelmanager-ti939.3.5).

Pins:
* list/create/delete route through call_endpoint with the es_* contract
  entries — the contract-checked path — and the exact fingerprint body
  keys (never stream/channel ids);
* create is fingerprint-only: the body carries rule_id/provider_id/
  stream_name_hash/event_key (+ optional note and display-only evidence);
* delete is two-step: confirm=False previews (GET only, no DELETE call),
  confirm=True deletes;
* preview_event_sync renders the excluded_by_operator disposition —
  summary count + EXCLUDED row line naming the excluded master.
"""
import pytest
from unittest.mock import AsyncMock, patch


def _make_mcp_and_register():
    from mcp.server.fastmcp import FastMCP
    from tools.event_sync_exclusions import register

    mcp = FastMCP("test")
    register(mcp)
    return mcp


def _row(**overrides):
    row = {
        "id": 11,
        "rule_id": 3,
        "provider_id": 7,
        "stream_name_hash": "a" * 64,
        "event_key": "fury vs usyk prelims|2026-07-12T00:00:00+00:00",
        "created_at": 1_752_800_000_000,
        "note": None,
        "evidence": {
            "stream_name": "BOX HD: Fury vs. Usyk @ 11 Jul 08:00 PM ET",
            "master_channel_name":
                "PPV 02: Fury vs. Usyk Prelims @ 11 Jul 08:00 PM ET",
            "provider": "BoxProvider",
        },
        "already_existed": False,
    }
    row.update(overrides)
    return row


def _list_response(rows):
    return {
        "exclusions": rows,
        "total": len(rows),
        "page": 1,
        "page_size": 50,
        "total_pages": 1 if rows else 0,
    }


async def _call_tool(mcp, client, name, args):
    with patch(
        "tools.event_sync_exclusions.get_ecm_client", return_value=client
    ):
        result = await mcp.call_tool(name, args)
    return result[0][0].text


class TestListEventSyncExclusions:
    @pytest.mark.asyncio
    async def test_lists_pairings_with_names_and_rule_filter(self):
        mcp = _make_mcp_and_register()
        calls = []

        async def side_effect(endpoint, **kwargs):
            calls.append((endpoint.name, kwargs.get("query")))
            return _list_response([_row(note="bad feed")])

        client = AsyncMock()
        client.call_endpoint.side_effect = side_effect
        text = await _call_tool(
            mcp, client, "list_event_sync_exclusions", {"rule_id": 3}
        )

        assert calls == [(
            "es_list_exclusions",
            {"page": 1, "page_size": 50, "rule_id": 3},
        )]
        assert "NEVER attaches to" in text
        assert "BOX HD: Fury vs. Usyk @ 11 Jul 08:00 PM ET" in text
        assert "PPV 02: Fury vs. Usyk Prelims" in text
        assert "note: bad feed" in text

    @pytest.mark.asyncio
    async def test_empty_list_explains_the_disposition(self):
        mcp = _make_mcp_and_register()
        client = AsyncMock()
        client.call_endpoint.return_value = _list_response([])
        text = await _call_tool(mcp, client, "list_event_sync_exclusions", {})
        assert "No Event Sync exclusions" in text
        assert "excluded_by_operator" in text


class TestCreateEventSyncExclusion:
    @pytest.mark.asyncio
    async def test_body_is_the_content_fingerprint_never_ids(self):
        mcp = _make_mcp_and_register()
        calls = []

        async def side_effect(endpoint, **kwargs):
            calls.append((endpoint.name, kwargs.get("body")))
            return _row()

        client = AsyncMock()
        client.call_endpoint.side_effect = side_effect
        text = await _call_tool(
            mcp, client, "create_event_sync_exclusion",
            {
                "rule_id": 3,
                "provider_id": 7,
                "stream_name_hash": "a" * 64,
                "event_key": "fury vs usyk prelims|2026-07-12T00:00:00+00:00",
                "stream_name": "BOX HD: Fury vs. Usyk @ 11 Jul 08:00 PM ET",
                "master_channel_name":
                    "PPV 02: Fury vs. Usyk Prelims @ 11 Jul 08:00 PM ET",
            },
        )

        assert [name for name, _ in calls] == ["es_create_exclusion"]
        body = calls[0][1]
        # SECURITY: identity keys are the content fingerprint; the raw
        # names ride ONLY inside the display-only evidence snapshot.
        assert set(body) == {
            "rule_id", "provider_id", "stream_name_hash", "event_key",
            "evidence",
        }
        assert body["evidence"] == {
            "stream_name": "BOX HD: Fury vs. Usyk @ 11 Jul 08:00 PM ET",
            "master_channel_name":
                "PPV 02: Fury vs. Usyk Prelims @ 11 Jul 08:00 PM ET",
        }
        assert "Exclusion created" in text
        assert "excluded_by_operator" in text
        assert "outranks any review-queue accept" in text

    @pytest.mark.asyncio
    async def test_idempotent_create_reports_already_excluded(self):
        mcp = _make_mcp_and_register()
        client = AsyncMock()
        client.call_endpoint.return_value = _row(already_existed=True)
        text = await _call_tool(
            mcp, client, "create_event_sync_exclusion",
            {
                "rule_id": 3, "provider_id": 7,
                "stream_name_hash": "a" * 64,
                "event_key": "fury vs usyk prelims|2026-07-12T00:00:00+00:00",
            },
        )
        assert "Already excluded" in text


class TestDeleteEventSyncExclusion:
    @pytest.mark.asyncio
    async def test_confirm_false_previews_without_deleting(self):
        mcp = _make_mcp_and_register()
        calls = []

        async def side_effect(endpoint, **kwargs):
            calls.append(endpoint.name)
            return _list_response([_row()])

        client = AsyncMock()
        client.call_endpoint.side_effect = side_effect
        text = await _call_tool(
            mcp, client, "delete_event_sync_exclusion", {"exclusion_id": 11}
        )

        assert calls == ["es_list_exclusions"]  # read-only preview
        assert "PREVIEW" in text
        assert "confirm=True" in text

    @pytest.mark.asyncio
    async def test_confirm_true_deletes_via_the_contract_path(self):
        mcp = _make_mcp_and_register()
        calls = []

        async def side_effect(endpoint, **kwargs):
            calls.append((endpoint.name, kwargs.get("path_args")))
            return None

        client = AsyncMock()
        client.call_endpoint.side_effect = side_effect
        text = await _call_tool(
            mcp, client, "delete_event_sync_exclusion",
            {"exclusion_id": 11, "confirm": True},
        )

        assert calls == [("es_delete_exclusion", {"exclusion_id": 11})]
        assert "removed" in text
        assert "matchable again" in text


class TestPreviewRendersExclusions:
    @pytest.mark.asyncio
    async def test_excluded_disposition_renders_count_and_row(self):
        from mcp.server.fastmcp import FastMCP
        from tools.channel_pipeline import register as register_cp

        mcp = FastMCP("test")
        register_cp(mcp)

        response = {
            "preflight": {"ok": True, "failures": []},
            "summary": {
                "secondary_streams": 1,
                "would_attach": 0,
                "ambiguous_skipped": 0,
                "unmatched": 0,
                "parse_failed": 0,
                "excluded_by_operator": 1,
                "master_channels": 1,
                "master_channels_unparsed": 0,
            },
            "streams": [{
                "stream_id": 201,
                "stream_name":
                    "WNBA TV 01: Mercury vs. Aces @ 11 Jul 06:00 PM ET",
                "group_id": 20, "provider": "FuboProvider",
                "disposition": "excluded_by_operator",
                "unmatchable_reason": None,
                "would_attach_master": None,
                "excluded_masters": [
                    "Peacock 14: Mercury vs. Aces @ 11 Jul 06:00 PM ET",
                ],
                "candidates": [],
            }],
            "unmatched_streams": [],
            "parse_failures": [],
            "unparsed_master_channels": [],
            "truncated": False,
        }
        client = AsyncMock()
        client.call_endpoint.return_value = response
        with patch(
            "tools.channel_pipeline.get_ecm_client", return_value=client
        ):
            result = await mcp.call_tool(
                "preview_event_sync",
                {"event_sync_config": {
                    "master_group_id": 10, "secondary_group_ids": [20],
                }},
            )
        text = result[0][0].text

        assert "1 excluded by operator" in text
        assert "EXCLUDED [FuboProvider]" in text
        assert "operator never-attach: Peacock 14: Mercury vs. Aces" in text
