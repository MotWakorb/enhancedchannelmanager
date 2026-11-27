"""``PATCH /api/channels/{id}`` carries a baseline whenever it sets a number.

The invariant this file holds the PATCH route to, the same one
``test_renumber_baselines.py`` holds bulk-commit and assign-numbers to:

    No numbering write to an existing channel may overwrite a server-side
    number that differs from the caller's baseline. A missing baseline is
    refused, not assumed.

The baseline travels as ``expectedNumber: {"number": n}``, the shape the
bulk-commit ``updateChannel`` operation uses for the same write. It is ECM
bookkeeping and never reaches Dispatcharr.

It is a check, not a guarantee: Dispatcharr 0.28.x has no conditional write, so
a change landing between the route's own read and its write is still lost.
"""
import json
from unittest.mock import AsyncMock, patch

import pytest

from tests.routers.numbering_baselines import patch_body_with_baseline


def make_client(channel_number=5, name="ESPN"):
    client = AsyncMock()
    client.get_channel.return_value = {
        "id": 1, "name": name, "channel_number": channel_number, "streams": [],
    }

    async def update_channel(channel_id, data):
        return {"id": channel_id, "name": name, "channel_number": channel_number, **data}

    client.update_channel.side_effect = update_channel
    return client


async def patch_channel(async_client, client, body=None, *, content=None):
    with patch("routers.channels.get_client", return_value=client), \
         patch("routers.channels.write_journal_rows", return_value=0) as rows:
        if content is not None:
            response = await async_client.patch(
                "/api/channels/1", content=content,
                headers={"Content-Type": "application/json"},
            )
        else:
            response = await async_client.patch("/api/channels/1", json=body)
    return response, rows


def validation_types(response) -> list[str]:
    return [err.get("type") for err in response.json()["detail"]]


def validation_text(response) -> str:
    return " ".join(err.get("msg", "") for err in response.json()["detail"])


class TestMissingBaseline:
    @pytest.mark.asyncio
    async def test_a_number_change_without_a_baseline_is_a_422_before_any_call(
        self, async_client,
    ):
        client = make_client()
        response, _ = await patch_channel(async_client, client, {"channel_number": 20})
        assert response.status_code == 422, response.text
        assert validation_types(response) == ["missing_channel_number_baseline"]
        assert "expectedNumber" in validation_text(response)
        client.get_channel.assert_not_awaited()
        client.update_channel.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_clearing_the_number_needs_a_baseline_too(self, async_client):
        client = make_client()
        response, _ = await patch_channel(async_client, client, {"channel_number": None})
        assert response.status_code == 422, response.text
        assert validation_types(response) == ["missing_channel_number_baseline"]
        client.update_channel.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resending_the_current_number_still_needs_a_baseline(self, async_client):
        # The same rule bulk-commit applies: the presence of channel_number is
        # what makes it a numbering write, whatever value it carries.
        client = make_client(channel_number=5)
        response, _ = await patch_channel(async_client, client, {"channel_number": 5})
        assert response.status_code == 422, response.text
        client.update_channel.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_bare_number_is_not_a_baseline(self, async_client):
        # The wrapper is what lets null mean "no number" rather than "no claim".
        client = make_client()
        response, _ = await patch_channel(
            async_client, client, {"channel_number": 20, "expectedNumber": 5},
        )
        assert response.status_code == 422, response.text
        client.update_channel.assert_not_awaited()


class TestStaleBaseline:
    @pytest.mark.asyncio
    async def test_a_stale_baseline_is_a_409_with_no_write(self, async_client):
        client = make_client(channel_number=5)
        response, rows = await patch_channel(
            async_client, client,
            {"channel_number": 20, "expectedNumber": {"number": 99}},
        )
        assert response.status_code == 409, response.text
        client.update_channel.assert_not_awaited()
        rows.assert_not_called()
        detail = response.json()["detail"]
        assert "Somebody else changed" in detail["message"]
        assert detail["concurrentNumberChanges"] == [
            {"channelId": 1, "baselineNumber": 99, "serverNumber": 5},
        ]

    @pytest.mark.asyncio
    async def test_null_against_a_numbered_channel_is_a_change(self, async_client):
        client = make_client(channel_number=5)
        response, _ = await patch_channel(
            async_client, client,
            {"channel_number": 20, "expectedNumber": {"number": None}},
        )
        assert response.status_code == 409, response.text
        client.update_channel.assert_not_awaited()
        assert response.json()["detail"]["concurrentNumberChanges"] == [
            {"channelId": 1, "baselineNumber": None, "serverNumber": 5},
        ]

    @pytest.mark.asyncio
    async def test_a_number_against_an_unnumbered_channel_is_a_change(self, async_client):
        client = make_client(channel_number=None)
        response, _ = await patch_channel(
            async_client, client,
            {"channel_number": 20, "expectedNumber": {"number": 5}},
        )
        assert response.status_code == 409, response.text
        client.update_channel.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_resent_current_number_over_a_moved_channel_is_refused(
        self, async_client,
    ):
        client = make_client(channel_number=7)
        response, _ = await patch_channel(
            async_client, client,
            {"channel_number": 7, "expectedNumber": {"number": 5}},
        )
        assert response.status_code == 409, response.text
        client.update_channel.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_channel_that_cannot_be_read_is_not_written(self, async_client):
        client = make_client()
        client.get_channel.side_effect = RuntimeError("Dispatcharr unreachable")
        response, _ = await patch_channel(
            async_client, client,
            {"channel_number": 20, "expectedNumber": {"number": 5}},
        )
        assert response.status_code == 500, response.text
        client.update_channel.assert_not_awaited()


class TestMatchingBaseline:
    @pytest.mark.asyncio
    async def test_a_matching_baseline_writes_without_forwarding_it(self, async_client):
        client = make_client(channel_number=5)
        response, _ = await patch_channel(
            async_client, client,
            {"channel_number": 20, "name": "ESPN 20", "expectedNumber": {"number": 5}},
        )
        assert response.status_code == 200, response.text
        client.update_channel.assert_awaited_once_with(
            1, {"channel_number": 20, "name": "ESPN 20"},
        )

    @pytest.mark.asyncio
    async def test_null_baseline_for_an_unnumbered_channel_writes(self, async_client):
        client = make_client(channel_number=None)
        response, _ = await patch_channel(
            async_client, client,
            {"channel_number": 20, "expectedNumber": {"number": None}},
        )
        assert response.status_code == 200, response.text
        client.update_channel.assert_awaited_once_with(1, {"channel_number": 20})

    @pytest.mark.asyncio
    async def test_resending_the_current_number_with_its_baseline_writes(self, async_client):
        client = make_client(channel_number=5)
        response, _ = await patch_channel(
            async_client, client,
            patch_body_with_baseline({"channel_number": 5}, 5),
        )
        assert response.status_code == 200, response.text
        client.update_channel.assert_awaited_once_with(1, {"channel_number": 5})

    @pytest.mark.asyncio
    async def test_canonical_equivalents_match(self, async_client):
        client = make_client(channel_number=7.0)
        response, _ = await patch_channel(
            async_client, client,
            {"channel_number": 20, "expectedNumber": {"number": 7}},
        )
        assert response.status_code == 200, response.text

    @pytest.mark.asyncio
    async def test_an_off_grid_baseline_that_matches_is_accepted(self, async_client):
        # A number set outside ECM can be read back, so it is a valid baseline.
        client = make_client(channel_number=8.25)
        response, _ = await patch_channel(
            async_client, client,
            {"channel_number": 20, "expectedNumber": {"number": 8.25}},
        )
        assert response.status_code == 200, response.text
        client.update_channel.assert_awaited_once_with(1, {"channel_number": 20})

    @pytest.mark.asyncio
    async def test_the_journal_row_does_not_record_the_baseline(self, async_client):
        client = make_client(channel_number=5)
        _, rows = await patch_channel(
            async_client, client,
            {"channel_number": 20, "expectedNumber": {"number": 5}},
        )
        written = rows.call_args.args[0]
        assert len(written) == 1
        assert "expectedNumber" not in json.dumps(written)
        assert written[0]["after_value"]["channel_number"] == 20


class TestBaselineValueContract:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
    async def test_a_non_finite_baseline_is_a_422(self, async_client, literal):
        client = make_client()
        content = json.dumps(
            {"channel_number": 20, "expectedNumber": {"number": "__BAD__"}},
        ).replace('"__BAD__"', literal)
        response, _ = await patch_channel(async_client, client, content=content)
        assert response.status_code == 422, response.text
        assert validation_types(response) == ["channel_number_baseline"]
        client.get_channel.assert_not_awaited()
        client.update_channel.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", [True, False])
    async def test_a_bool_baseline_is_a_422(self, async_client, value):
        client = make_client()
        response, _ = await patch_channel(
            async_client, client,
            {"channel_number": 20, "expectedNumber": {"number": value}},
        )
        assert response.status_code == 422, response.text
        assert validation_types(response) == ["channel_number_baseline"]
        client.update_channel.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_written_number_keeps_the_write_contract(self, async_client):
        client = make_client(channel_number=5)
        response, _ = await patch_channel(
            async_client, client,
            {"channel_number": 8.25, "expectedNumber": {"number": 5}},
        )
        assert response.status_code == 400, response.text
        client.update_channel.assert_not_awaited()


class TestEditsThatDoNotNumber:
    @pytest.mark.asyncio
    async def test_a_patch_without_channel_number_needs_no_baseline(self, async_client):
        client = make_client()
        response, _ = await patch_channel(async_client, client, {"name": "ESPN HD"})
        assert response.status_code == 200, response.text
        client.update_channel.assert_awaited_once_with(1, {"name": "ESPN HD"})

    @pytest.mark.asyncio
    async def test_a_baseline_on_a_patch_that_does_not_number_is_not_forwarded(
        self, async_client,
    ):
        # Ignored, as on a bulk-commit updateChannel that does not write the
        # number: such a write cannot overwrite anybody's change to it.
        client = make_client(channel_number=5)
        response, _ = await patch_channel(
            async_client, client,
            {"name": "ESPN HD", "expectedNumber": {"number": 99}},
        )
        assert response.status_code == 200, response.text
        client.update_channel.assert_awaited_once_with(1, {"name": "ESPN HD"})
