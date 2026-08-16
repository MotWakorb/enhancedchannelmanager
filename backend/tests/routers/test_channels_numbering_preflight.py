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


class TestPreflightAndContinueOnError:
    """How the final-state check interacts with Edit Mode's two-phase Apply.

    Edit Mode's Apply is not one request: creates go up in their own call, then
    everything else in batches of 200. So phase 1 can legitimately show a
    collision that a phase-2 operation resolves — create a channel on 5 while a
    later batch moves the incumbent off it — and refusing phase 1 outright would
    break a plan that is fine as a whole.

    The split that resolves it, pinned here because it is the kind of
    interaction that breaks silently:

    * The BROWSER holds the whole plan, so its preflight
      (`frontend/src/utils/channelNumberPlan.ts`) is the binding gate for the
      UI, and it blocks before the first request — proven in
      `e2e/edit-mode-numbering-guards.spec.ts` by a request count of zero.
    * The SERVER sees one request at a time. Under ``continueOnError`` — which
      is exactly what Apply All sends — its finding is advisory and execution
      proceeds, matching how every other validation issue already behaves.
      Making numbering the one exception would change approved behaviour for a
      plan that is legal.
    * Without ``continueOnError``, which is the default and what a non-UI
      caller gets, it blocks.
    """

    @pytest.mark.asyncio
    async def test_continue_on_error_downgrades_the_finding_to_advisory(self, async_client):
        from routers import channels as router_module
        router_module._BULK_COMMIT_JOBS.clear()

        mock_client = _client()
        mock_client.create_channel.return_value = {"id": 99, "name": "New", "channel_number": 5}
        mock_client.get_channel.return_value = {"id": 99, "name": "New", "channel_number": 5}

        import asyncio
        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post("/api/channels/bulk-commit", json={
                "operations": [
                    {"type": "createChannel", "tempId": -1, "name": "New", "channelNumber": 5},
                ],
                "continueOnError": True,
            })
            assert response.status_code == 202, response.text
            job_id = response.json()["job_id"]
            payload = None
            for _ in range(200):
                await asyncio.sleep(0)
                poll = await async_client.get(f"/api/channels/bulk-commit/{job_id}")
                payload = poll.json()
                if payload["status"] in ("completed", "failed"):
                    break

        assert payload["status"] == "completed", payload
        assert mock_client.create_channel.called, (
            "a phase-1 create must still execute; a phase-2 operation may be what resolves it"
        )
        # The finding is still REPORTED — advisory is not silent.
        assert _numbering_issues(payload["result"]), payload["result"]

    @pytest.mark.asyncio
    async def test_without_continue_on_error_it_blocks_before_executing(self, async_client):
        from routers import channels as router_module
        router_module._BULK_COMMIT_JOBS.clear()

        mock_client = _client()
        mock_client.create_channel.return_value = {"id": 99, "name": "New", "channel_number": 5}

        import asyncio
        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post("/api/channels/bulk-commit", json={
                "operations": [
                    {"type": "createChannel", "tempId": -1, "name": "New", "channelNumber": 5},
                ],
            })
            assert response.status_code == 202, response.text
            job_id = response.json()["job_id"]
            payload = None
            for _ in range(200):
                await asyncio.sleep(0)
                poll = await async_client.get(f"/api/channels/bulk-commit/{job_id}")
                payload = poll.json()
                if payload["status"] in ("completed", "failed"):
                    break

        assert not mock_client.create_channel.called, (
            "the default path must refuse before the first mutation"
        )
