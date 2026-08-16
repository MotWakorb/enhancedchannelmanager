"""Bulk commit refuses a plan whose COMBINED final numbering is illegal.

Bead ``enhancedchannelmanager-ic884.2``. These are the cross-operation cases
per-operation validation cannot see: every operation in the collision requests
below is individually legal against the lineup, and only the whole plan puts
two channels on one number.

``validateOnly`` is used throughout so the assertion is about the pre-execution
gate itself rather than about anything the executor did or did not do — the
same gate the asynchronous path passes through before its first mutation.
"""
import pytest
from unittest.mock import AsyncMock, patch


LINEUP = [
    {"id": 1, "name": "ESPN", "channel_number": 5, "streams": []},
    {"id": 2, "name": "TNT", "channel_number": 6, "streams": []},
    {"id": 3, "name": "AMC", "channel_number": 7, "streams": []},
]


def _client(lineup=None):
    mock_client = AsyncMock()
    mock_client.get_channels.return_value = {
        "results": LINEUP if lineup is None else lineup,
        "count": 3,
        "next": None,
    }
    mock_client.get_streams.return_value = {"results": [], "count": 0, "next": None}
    return mock_client


async def _validate(async_client, operations, lineup=None):
    with patch("routers.channels.get_client", return_value=_client(lineup)), \
         patch("routers.channels.journal"):
        response = await async_client.post("/api/channels/bulk-commit", json={
            "operations": operations,
            "validateOnly": True,
        })
    assert response.status_code == 200
    return response.json()


def _numbering_issues(data):
    return [
        issue for issue in (data.get("validationIssues") or [])
        if issue["type"] in ("duplicate_channel_number", "invalid_channel_number")
    ]


class TestFinalNumberingPreflight:
    @pytest.mark.asyncio
    async def test_a_clean_plan_passes(self, async_client):
        data = await _validate(async_client, [
            {"type": "updateChannel", "channelId": 1, "data": {"channel_number": 100}},
            {"type": "updateChannel", "channelId": 2, "data": {"channel_number": 5}},
        ])
        assert _numbering_issues(data) == []
        assert data["validationPassed"] is True

    @pytest.mark.asyncio
    async def test_blocks_a_collision_only_the_combination_creates(self, async_client):
        data = await _validate(async_client, [
            {"type": "updateChannel", "channelId": 1, "data": {"channel_number": 100}},
            {"type": "updateChannel", "channelId": 2, "data": {"channel_number": 5}},
            {"type": "updateChannel", "channelId": 3, "data": {"channel_number": 5}},
        ])
        issues = _numbering_issues(data)
        assert len(issues) == 1
        assert data["validationPassed"] is False
        assert data["success"] is False

    @pytest.mark.asyncio
    async def test_the_error_names_the_channels_and_the_operations(self, async_client):
        data = await _validate(async_client, [
            {"type": "updateChannel", "channelId": 1, "data": {"channel_number": 100}},
            {"type": "updateChannel", "channelId": 2, "data": {"channel_number": 5}},
            {"type": "updateChannel", "channelId": 3, "data": {"channel_number": 5}},
        ])
        issue = _numbering_issues(data)[0]
        assert "TNT" in issue["message"]
        assert "AMC" in issue["message"]
        assert sorted(issue["operationIndexes"]) == [1, 2]
        assert sorted(issue["channelIds"]) == [2, 3]

    @pytest.mark.asyncio
    async def test_a_valid_swap_passes(self, async_client):
        data = await _validate(async_client, [
            {"type": "updateChannel", "channelId": 1, "data": {"channel_number": 6}},
            {"type": "updateChannel", "channelId": 2, "data": {"channel_number": 5}},
        ])
        assert _numbering_issues(data) == []

    @pytest.mark.asyncio
    async def test_a_vacated_number_may_be_reused(self, async_client):
        data = await _validate(async_client, [
            {"type": "deleteChannel", "channelId": 1},
            {"type": "updateChannel", "channelId": 2, "data": {"channel_number": 5}},
        ])
        assert _numbering_issues(data) == []

    @pytest.mark.asyncio
    async def test_a_created_channel_collides_with_the_existing_lineup(self, async_client):
        data = await _validate(async_client, [
            {"type": "createChannel", "tempId": -1, "name": "Second ESPN", "channelNumber": 5},
        ])
        issues = _numbering_issues(data)
        assert len(issues) == 1
        assert "Second ESPN" in issues[0]["message"]

    @pytest.mark.asyncio
    async def test_two_created_channels_collide_with_each_other(self, async_client):
        data = await _validate(async_client, [
            {"type": "createChannel", "tempId": -1, "name": "A", "channelNumber": 50},
            {"type": "createChannel", "tempId": -2, "name": "B", "channelNumber": 50},
        ])
        assert len(_numbering_issues(data)) == 1

    @pytest.mark.asyncio
    async def test_a_bulk_range_colliding_with_an_untouched_channel_is_refused(self, async_client):
        data = await _validate(async_client, [
            {"type": "bulkAssignChannelNumbers", "channelIds": [1, 2], "startingNumber": 6},
        ])
        # 1 -> 6 and 2 -> 7, and 7 is AMC's, which nothing moved.
        issues = _numbering_issues(data)
        assert len(issues) == 1
        assert "AMC" in issues[0]["message"]

    @pytest.mark.asyncio
    async def test_a_pre_existing_duplicate_is_left_alone(self, async_client):
        lineup = [
            {"id": 1, "name": "ESPN", "channel_number": 5, "streams": []},
            {"id": 2, "name": "ESPN HD", "channel_number": 5, "streams": []},
            {"id": 3, "name": "AMC", "channel_number": 7, "streams": []},
        ]
        data = await _validate(async_client, [
            {"type": "updateChannel", "channelId": 3, "data": {"name": "AMC HD"}},
        ], lineup=lineup)
        assert _numbering_issues(data) == []

    @pytest.mark.asyncio
    async def test_an_acknowledged_duplicate_is_accepted(self, async_client):
        data = await _validate(async_client, [
            {
                "type": "updateChannel",
                "channelId": 2,
                "data": {"channel_number": 5},
                "acknowledgedDuplicateNumber": 5,
            },
        ])
        assert _numbering_issues(data) == []
        assert data["validationPassed"] is True

    @pytest.mark.asyncio
    async def test_an_unacknowledged_operation_joining_it_still_blocks(self, async_client):
        data = await _validate(async_client, [
            {
                "type": "updateChannel",
                "channelId": 2,
                "data": {"channel_number": 5},
                "acknowledgedDuplicateNumber": 5,
            },
            {"type": "updateChannel", "channelId": 3, "data": {"channel_number": 5}},
        ])
        issues = _numbering_issues(data)
        assert len(issues) == 1
        # Only the operation nobody agreed to is named.
        assert issues[0]["operationIndexes"] == [1]

    @pytest.mark.asyncio
    async def test_an_acknowledgement_never_reaches_dispatcharr(self, async_client):
        """It is ECM bookkeeping, not a channel field.

        It rides beside ``data`` rather than in it precisely so the PATCH body
        the executor forwards is unchanged.
        """
        from routers.channels import BulkUpdateChannelOp

        op = BulkUpdateChannelOp(
            channelId=2,
            data={"channel_number": 5},
            acknowledgedDuplicateNumber=5,
        )
        assert "acknowledgedDuplicateNumber" not in op.data

    @pytest.mark.asyncio
    async def test_a_plan_touching_no_numbers_passes(self, async_client):
        data = await _validate(async_client, [
            {"type": "updateChannel", "channelId": 1, "data": {"name": "ESPN Renamed"}},
        ])
        assert _numbering_issues(data) == []
