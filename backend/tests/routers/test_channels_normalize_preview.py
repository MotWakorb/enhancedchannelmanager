"""
Unit tests for normalize-preview endpoints on the channels router.

bd-eio04.13 — per-channel would_normalize indicator.

Endpoints tested:
  GET  /api/channels/{id}/normalize-preview
  POST /api/channels/normalize-preview-batch

Mocks `routers.channels.get_client` for the Dispatcharr API call that
fetches the current channel name, and the NormalizationEngine factory so
we exercise the route wiring without depending on DB-backed rules.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_mock_result(original: str, normalized: str, transformations=None):
    """Build a mock NormalizationResult matching the real dataclass surface."""
    mock = MagicMock()
    mock.original = original
    mock.normalized = normalized
    mock.rules_applied = []
    mock.transformations = transformations or []
    return mock


class TestNormalizePreviewSingle:
    """Tests for GET /api/channels/{channel_id}/normalize-preview."""

    @pytest.mark.asyncio
    async def test_would_change_true(self, async_client):
        """Rule that matches current name -> would_change=True, proposed differs."""
        mock_client = AsyncMock()
        mock_client.get_channel.return_value = {"id": 7, "name": "ESPN HD"}

        mock_engine = MagicMock()
        mock_engine.normalize.return_value = _make_mock_result(
            "ESPN HD", "ESPN", transformations=[(1, "ESPN HD", "ESPN")]
        )

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.get_normalization_engine", return_value=mock_engine):
            response = await async_client.get("/api/channels/7/normalize-preview")

        assert response.status_code == 200
        data = response.json()
        assert data["channel_id"] == 7
        assert data["current_name"] == "ESPN HD"
        assert data["proposed_name"] == "ESPN"
        assert data["would_change"] is True
        assert len(data["transformations"]) == 1
        assert data["transformations"][0]["rule_id"] == 1

    @pytest.mark.asyncio
    async def test_would_change_false(self, async_client):
        """Name already normalized -> would_change=False, proposed == current."""
        mock_client = AsyncMock()
        mock_client.get_channel.return_value = {"id": 9, "name": "CNN"}

        mock_engine = MagicMock()
        mock_engine.normalize.return_value = _make_mock_result("CNN", "CNN")

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.get_normalization_engine", return_value=mock_engine):
            response = await async_client.get("/api/channels/9/normalize-preview")

        assert response.status_code == 200
        data = response.json()
        assert data["would_change"] is False
        assert data["current_name"] == data["proposed_name"] == "CNN"
        assert data["transformations"] == []

    @pytest.mark.asyncio
    async def test_channel_not_found(self, async_client):
        """Dispatcharr 404 propagates as 500 (router keeps generic error shape)."""
        mock_client = AsyncMock()
        mock_client.get_channel.side_effect = Exception("not found")

        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.get("/api/channels/9999/normalize-preview")

        assert response.status_code == 500


class TestNormalizePreviewBatch:
    """Tests for POST /api/channels/normalize-preview-batch."""

    @pytest.mark.asyncio
    async def test_returns_per_row_preview_from_names(self, async_client):
        """Fast path: caller supplies {channel_id, name} — no Dispatcharr call."""
        def _normalize(name, group_ids=None):
            if name == "ESPN HD":
                return _make_mock_result("ESPN HD", "ESPN", [(1, "ESPN HD", "ESPN")])
            return _make_mock_result(name, name)

        mock_engine = MagicMock()
        mock_engine.normalize.side_effect = _normalize

        with patch("routers.channels.get_normalization_engine", return_value=mock_engine):
            response = await async_client.post(
                "/api/channels/normalize-preview-batch",
                json={"channels": [
                    {"channel_id": 101, "name": "ESPN HD"},
                    {"channel_id": 102, "name": "CNN"},
                ]},
            )

        assert response.status_code == 200
        data = response.json()
        assert len(data["results"]) == 2
        by_id = {r["channel_id"]: r for r in data["results"]}
        assert by_id[101]["would_change"] is True
        assert by_id[101]["proposed_name"] == "ESPN"
        assert by_id[102]["would_change"] is False

    @pytest.mark.asyncio
    async def test_falls_back_to_ids(self, async_client):
        """Fallback path: ids-only input triggers Dispatcharr fetch."""
        mock_client = AsyncMock()

        async def _fetch(cid):
            return {
                101: {"id": 101, "name": "ESPN HD"},
                102: {"id": 102, "name": "CNN"},
            }[cid]

        mock_client.get_channel.side_effect = _fetch

        def _normalize(name, group_ids=None):
            if name == "ESPN HD":
                return _make_mock_result("ESPN HD", "ESPN", [(1, "ESPN HD", "ESPN")])
            return _make_mock_result(name, name)

        mock_engine = MagicMock()
        mock_engine.normalize.side_effect = _normalize

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.get_normalization_engine", return_value=mock_engine):
            response = await async_client.post(
                "/api/channels/normalize-preview-batch",
                json={"channel_ids": [101, 102]},
            )

        assert response.status_code == 200
        data = response.json()
        by_id = {r["channel_id"]: r for r in data["results"]}
        assert by_id[101]["would_change"] is True
        assert by_id[102]["would_change"] is False

    @pytest.mark.asyncio
    async def test_rejects_both_shapes(self, async_client):
        """Passing both `channels` and `channel_ids` is a 400."""
        response = await async_client.post(
            "/api/channels/normalize-preview-batch",
            json={
                "channels": [{"channel_id": 1, "name": "A"}],
                "channel_ids": [2],
            },
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_empty_list(self, async_client):
        """Empty input -> empty results, 200."""
        response = await async_client.post(
            "/api/channels/normalize-preview-batch",
            json={"channels": []},
        )
        assert response.status_code == 200
        assert response.json() == {"results": []}

    @pytest.mark.asyncio
    async def test_caps_batch_at_100(self, async_client):
        """More than 100 rows -> 400 to bound per-request cost."""
        response = await async_client.post(
            "/api/channels/normalize-preview-batch",
            json={"channels": [
                {"channel_id": i, "name": f"ch{i}"} for i in range(200)
            ]},
        )
        assert response.status_code == 400
        assert "100" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_skips_missing_channels_in_ids_fallback(self, async_client):
        """A Dispatcharr failure for one id does not poison the whole batch."""
        mock_client = AsyncMock()

        async def _fetch(cid):
            if cid == 102:
                raise Exception("missing")
            return {"id": cid, "name": "Good Name"}

        mock_client.get_channel.side_effect = _fetch

        mock_engine = MagicMock()
        mock_engine.normalize.return_value = _make_mock_result("Good Name", "Good Name")

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.get_normalization_engine", return_value=mock_engine):
            response = await async_client.post(
                "/api/channels/normalize-preview-batch",
                json={"channel_ids": [101, 102, 103]},
            )

        assert response.status_code == 200
        data = response.json()
        ids = {r["channel_id"] for r in data["results"]}
        assert ids == {101, 103}
