"""TDD tests for bd-0hjrk.3 — auto-created groups must report total membership.

ROOT CAUSE: GET /api/channel-groups/auto-created returned each group as
``{id, name, auto_created_count, sample_channels}``. ``auto_created_count``
counts ONLY channels with ``auto_created=True`` in the group — it is NOT the
group's total membership. The MCP tool read a non-existent ``channel_count``
key (always 0). The fix surfaces the Dispatcharr group object's ``channel_count``
(total membership) alongside ``auto_created_count`` so the operator sees both
the full group size and the auto-created subset.

Mocks: get_client() at router module level (per backend/CLAUDE.md).
"""
import pytest
from unittest.mock import AsyncMock, patch


class TestAutoCreatedGroupsTotalMembership:
    """GET /api/channel-groups/auto-created reports both counts."""

    @pytest.mark.asyncio
    async def test_includes_total_channel_count_and_auto_created_count(self, async_client):
        """A group with 170 total channels but 12 auto-created reports BOTH.

        The Dispatcharr group object carries ``channel_count`` (total
        membership = 170). Only 12 of those channels are ``auto_created=True``.
        The endpoint entry must report ``channel_count == 170`` AND
        ``auto_created_count == 12``.
        """
        mock_client = AsyncMock()
        # Dispatcharr group object: total membership is 170 (channel_count),
        # which is NOT equal to the auto-created subset.
        mock_client.get_channel_groups.return_value = [
            {"id": 1, "name": "Sports", "channel_count": 170},
        ]
        # 12 auto-created channels in group 1 (a subset of the 170 total).
        auto_created_channels = [
            {
                "id": 100 + i,
                "name": f"Auto Channel {i}",
                "channel_group_id": 1,
                "auto_created": True,
                "channel_number": 100 + i,
                "auto_created_by": 1,
                "auto_created_by_name": "Rule 1",
            }
            for i in range(12)
        ]
        mock_client.get_channels.return_value = {
            "results": auto_created_channels,
            "next": None,
        }

        with patch("routers.channel_groups.get_client", return_value=mock_client):
            response = await async_client.get("/api/channel-groups/auto-created")

        assert response.status_code == 200
        data = response.json()
        assert len(data["groups"]) == 1
        group = data["groups"][0]
        assert group["channel_count"] == 170, (
            f"Expected total membership channel_count=170, got: {group!r}"
        )
        assert group["auto_created_count"] == 12, (
            f"Expected auto_created_count=12 (the subset), got: {group!r}"
        )

    @pytest.mark.asyncio
    async def test_channel_count_defaults_to_zero_when_missing(self, async_client):
        """If the Dispatcharr group object lacks channel_count, default to 0.

        Guards against a KeyError when the upstream group object is missing the
        field — the entry must still surface auto_created_count.
        """
        mock_client = AsyncMock()
        mock_client.get_channel_groups.return_value = [
            {"id": 1, "name": "Sports"},  # no channel_count key
        ]
        mock_client.get_channels.return_value = {
            "results": [
                {"id": 10, "name": "ESPN", "channel_group_id": 1, "auto_created": True,
                 "channel_number": 100, "auto_created_by": 1, "auto_created_by_name": "Rule 1"},
            ],
            "next": None,
        }

        with patch("routers.channel_groups.get_client", return_value=mock_client):
            response = await async_client.get("/api/channel-groups/auto-created")

        assert response.status_code == 200
        group = response.json()["groups"][0]
        assert group["channel_count"] == 0
        assert group["auto_created_count"] == 1
