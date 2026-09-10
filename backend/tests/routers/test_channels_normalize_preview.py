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
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

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


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["names", "ids", "single"])
@pytest.mark.parametrize("name,proposed,trace,expected_trace", [
    ("HD News", "News", [(8, "HD News", "News "), (3, "News ", "News")], [
        {"rule_id": 8, "before": "HD News", "after": "News "},
        {"rule_id": 3, "before": "News ", "after": "News"},
    ]),
    ("News", "News", [], []),
    ("", "", None, []),
])
async def test_preview_complete_golden(async_client, shape, name, proposed, trace, expected_trace):
    client = AsyncMock()
    client.get_channel.return_value = {"name": name}
    session, engine = MagicMock(), MagicMock()
    engine.normalize.return_value = SimpleNamespace(normalized=proposed, transformations=trace)
    with patch("routers.channels.get_client", return_value=client) as factory, \
         patch("routers.channels.get_session", return_value=session), \
         patch("routers.channels.get_normalization_engine", return_value=engine):
        if shape == "single":
            response = await async_client.get("/api/channels/7/normalize-preview")
        else:
            payload = {"channels": [{"channel_id": 7, "name": name}]} if shape == "names" else {"channel_ids": [7]}
            response = await async_client.post("/api/channels/normalize-preview-batch", json=payload)
    golden = {"channel_id": 7, "current_name": name, "proposed_name": proposed,
              "would_change": proposed != name, "transformations": expected_trace}
    assert response.status_code == 200
    assert response.json() == (golden if shape == "single" else {"results": [golden]})
    engine.normalize.assert_called_once_with(name)
    session.close.assert_called_once_with()
    if shape == "names":
        factory.assert_not_called()
    else:
        client.get_channel.assert_awaited_once_with(7)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", [None, {}, {"name": None}])
async def test_ids_preserve_duplicates_order_and_empty_name_fallback(async_client, channel):
    client, engine, session = AsyncMock(), MagicMock(), MagicMock()
    client.get_channel.side_effect = [channel, RuntimeError("missing"), channel, channel]
    engine.normalize.return_value = SimpleNamespace(normalized="", transformations=None)
    with patch("routers.channels.get_client", return_value=client), \
         patch("routers.channels.get_session", return_value=session), \
         patch("routers.channels.get_normalization_engine", return_value=engine):
        response = await async_client.post("/api/channels/normalize-preview-batch", json={"channel_ids": [9, 2, 9, 1]})
    assert response.status_code == 200
    assert response.json() == {"results": [
        {"channel_id": cid, "current_name": "", "proposed_name": "", "would_change": False, "transformations": []}
        for cid in [9, 9, 1]
    ]}
    assert client.get_channel.await_args_list == [call(cid) for cid in [9, 2, 9, 1]]
    assert engine.normalize.call_args_list == [call("")] * 3
    session.close.assert_called_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["names", "ids", "single"])
@pytest.mark.parametrize("failure", ["normalize", "short_trace"])
async def test_preview_error_boundary_and_session_close(async_client, shape, failure):
    client, engine, session = AsyncMock(), MagicMock(), MagicMock()
    client.get_channel.return_value = {"name": "News"}
    engine.normalize.return_value = SimpleNamespace(normalized="News", transformations=[(1, "before")])
    if failure == "normalize":
        engine.normalize.side_effect = ValueError("private diagnostic")
    with patch("routers.channels.get_client", return_value=client), \
         patch("routers.channels.get_session", return_value=session), \
         patch("routers.channels.get_normalization_engine", return_value=engine):
        if shape == "single":
            response = await async_client.get("/api/channels/7/normalize-preview")
            assert response.status_code == 500
            assert response.json() == {"detail": "Internal server error"}
        else:
            # ASGITransport re-raises the batch handler's unhandled error.
            payload = {"channels": [{"channel_id": 7, "name": "News"}]} if shape == "names" else {"channel_ids": [7, 8]}
            with pytest.raises(ValueError if failure == "normalize" else IndexError):
                await async_client.post("/api/channels/normalize-preview-batch", json=payload)
            if shape == "ids":
                client.get_channel.assert_awaited_once_with(7)
    session.close.assert_called_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize("payload,status", [
    ({}, 200), ({"channels": None, "channel_ids": None}, 200),
    ({"channel_ids": []}, 200), ({"channels": [], "channel_ids": []}, 400),
    ({"channels": [{"channel_id": 7, "name": None}]}, 422),
])
async def test_preview_empty_exclusive_and_null_input(async_client, payload, status):
    with patch("routers.channels.get_client") as client, patch("routers.channels.get_session") as session:
        response = await async_client.post("/api/channels/normalize-preview-batch", json=payload)
    assert response.status_code == status
    if status == 200:
        assert response.json() == {"results": []}
    client.assert_not_called()
    session.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["names", "ids"])
@pytest.mark.parametrize("size", [100, 101])
async def test_preview_exact_batch_limit(async_client, shape, size):
    client, engine, session = AsyncMock(), MagicMock(), MagicMock()
    client.get_channel.return_value = {"name": ""}
    engine.normalize.return_value = SimpleNamespace(normalized="", transformations=[])
    payload = {"channels": [{"channel_id": cid, "name": ""} for cid in range(size)]} if shape == "names" else {"channel_ids": list(range(size))}
    with patch("routers.channels.get_client", return_value=client), \
         patch("routers.channels.get_session", return_value=session) as sessions, \
         patch("routers.channels.get_normalization_engine", return_value=engine):
        response = await async_client.post("/api/channels/normalize-preview-batch", json=payload)
    assert response.status_code == (200 if size == 100 else 400)
    if size == 100:
        assert len(response.json()["results"]) == 100
        assert engine.normalize.call_count == 100
        assert client.get_channel.await_count == (100 if shape == "ids" else 0)
        session.close.assert_called_once_with()
    else:
        sessions.assert_not_called()
        client.get_channel.assert_not_awaited()
