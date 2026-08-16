"""A caller who asks for normalization can tell when it did not happen.

Bead `enhancedchannelmanager-e9e5o`. Commit 19ff3282 stopped the FRONTEND
swallowing a normalization failure. The wire contract still did it: both
`POST /api/channels` and the `createChannel` bulk operation wrapped the
engine call in `try/except`, logged a warning and carried on with the raw
name, returning a 200 that was observationally identical to
``normalize=false``. The capability the caller asked for silently did not
happen and nothing in the response said so.

The create still succeeds — a channel that exists must not start reporting
as a failure, and the affected callers are third-party MCP/REST clients we
cannot see. What changed is that the response now carries an explicit
indicator, so the two outcomes are distinguishable without reading the
server's logs.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_mock_result(original: str, normalized: str):
    """Mock NormalizationResult matching the real dataclass surface."""
    mock = MagicMock()
    mock.original = original
    mock.normalized = normalized
    mock.rules_applied = []
    mock.transformations = []
    return mock


async def _commit_and_wait(async_client, body, *, max_polls=50):
    """POST a bulk commit and poll until terminal, returning the result envelope."""
    import asyncio as _asyncio

    response = await async_client.post("/api/channels/bulk-commit", json=body)
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    for _ in range(max_polls):
        await _asyncio.sleep(0)
        poll = await async_client.get(f"/api/channels/bulk-commit/{job_id}")
        assert poll.status_code == 200, poll.text
        payload = poll.json()
        if payload["status"] == "completed":
            return payload["result"]
        if payload["status"] == "failed":
            raise AssertionError(f"bulk-commit job {job_id} failed: {payload}")
    raise AssertionError(f"bulk-commit job {job_id} did not terminate in {max_polls} polls")


class TestSingleCreateNormalizationDisclosure:
    """POST /api/channels — `normalize=true` reports whether it happened."""

    @pytest.mark.asyncio
    async def test_engine_failure_is_reported_not_swallowed(self, async_client):
        """The engine blew up, so the response says normalization did not apply
        and names the raw name that was used instead — while still returning
        200, because the channel WAS created."""
        mock_client = AsyncMock()
        mock_client.create_channel.return_value = {
            "id": 1, "name": "US: CNN", "channel_number": 100,
        }

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.get_normalization_engine",
                   side_effect=RuntimeError("engine offline")), \
             patch("routers.channels.journal"):
            response = await async_client.post("/api/channels", json={
                "name": "US: CNN", "normalize": True,
            })

        assert response.status_code == 200
        data = response.json()
        # The channel still exists, under the name that was actually applied.
        assert data["id"] == 1
        assert mock_client.create_channel.call_args[0][0]["name"] == "US: CNN"
        # ...and the caller can SEE that the capability it asked for did not run.
        assert data["normalization"]["requested"] is True
        assert data["normalization"]["applied"] is False
        assert data["normalization"]["nameApplied"] == "US: CNN"
        assert "engine offline" in data["normalization"]["error"]

    @pytest.mark.asyncio
    async def test_successful_normalization_reports_applied(self, async_client):
        """A clean run reports `applied` true, so `applied is False` is a
        signal and not merely the absence of one."""
        mock_client = AsyncMock()
        mock_client.create_channel.return_value = {"id": 2, "name": "CNN"}

        mock_engine = MagicMock()
        mock_engine.normalize.return_value = _make_mock_result("US: CNN", "CNN")

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.get_normalization_engine", return_value=mock_engine), \
             patch("routers.channels.journal"):
            response = await async_client.post("/api/channels", json={
                "name": "US: CNN", "normalize": True,
            })

        assert response.status_code == 200
        data = response.json()
        assert mock_client.create_channel.call_args[0][0]["name"] == "CNN"
        assert data["normalization"]["requested"] is True
        assert data["normalization"]["applied"] is True
        assert data["normalization"]["nameApplied"] == "CNN"
        assert data["normalization"]["error"] is None

    @pytest.mark.asyncio
    async def test_normalize_not_requested_leaves_response_shape_alone(self, async_client):
        """A caller that never asked for normalization sees exactly the
        response it saw before — the indicator is additive, not a new
        mandatory field on every create."""
        mock_client = AsyncMock()
        mock_client.create_channel.return_value = {"id": 3, "name": "US: CNN"}

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.journal"):
            response = await async_client.post("/api/channels", json={"name": "US: CNN"})

        assert response.status_code == 200
        assert "normalization" not in response.json()


class TestBulkCommitNormalizationDisclosure:
    """POST /api/channels/bulk-commit — createChannel ops report the same thing."""

    @pytest.fixture(autouse=True)
    def _clear_jobs(self):
        from routers import channels as router_module

        router_module._BULK_COMMIT_JOBS.clear()
        yield
        router_module._BULK_COMMIT_JOBS.clear()

    @pytest.mark.asyncio
    async def test_engine_failure_is_listed_in_the_result_envelope(self, async_client):
        """The op still applies and the batch is still a success, but the
        envelope names the op whose normalization did not run."""
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {"results": [], "count": 0, "next": None}
        mock_client.get_streams.return_value = {"results": [], "count": 0, "next": None}
        mock_client.create_channel.return_value = {"id": 11, "name": "US: CNN"}

        ops = [{"type": "createChannel", "tempId": -1, "name": "US: CNN", "normalize": True}]

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.get_normalization_engine",
                   side_effect=RuntimeError("engine offline")), \
             patch("routers.channels.journal"):
            data = await _commit_and_wait(async_client, {"operations": ops})

        # The create landed and the batch did not become a failure.
        assert data["success"] is True
        assert data["operationsApplied"] == 1
        assert mock_client.create_channel.await_args.args[0]["name"] == "US: CNN"
        # ...and the caller can detect the capability that did not run.
        assert len(data["normalizationFailures"]) == 1
        failure = data["normalizationFailures"][0]
        assert failure["tempId"] == -1
        assert failure["name"] == "US: CNN"
        assert failure["nameApplied"] == "US: CNN"
        assert "engine offline" in failure["error"]

    @pytest.mark.asyncio
    async def test_a_create_that_never_persisted_is_not_listed_as_normalized_raw(
        self, async_client
    ):
        """Normalization broke AND the create was rejected, so no channel exists.

        The envelope used to contradict itself here: the op was appended to
        `normalizationFailures` BEFORE `create_channel` was attempted, so a
        rejected create appeared in `errors`/`operationsFailed` *and* stayed in
        `normalizationFailures` carrying a `nameApplied`. The MCP renderer
        reports that list as channels "which were created with the name as
        given", which named a channel that was never persisted.

        The invariant: every entry in `normalizationFailures` corresponds to a
        channel that exists.
        """
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {"results": [], "count": 0, "next": None}
        mock_client.get_streams.return_value = {"results": [], "count": 0, "next": None}
        mock_client.create_channel.side_effect = RuntimeError("Dispatcharr rejected the create")

        ops = [{"type": "createChannel", "tempId": -1, "name": "US: CNN", "normalize": True}]

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.get_normalization_engine",
                   side_effect=RuntimeError("engine offline")), \
             patch("routers.channels.journal"):
            data = await _commit_and_wait(async_client, {"operations": ops})

        # The op failed, and it says so in the one place a failure belongs.
        assert data["success"] is False
        assert data["operationsApplied"] == 0
        assert data["operationsFailed"] == 1
        assert len(data["errors"]) == 1
        # ...and it is NOT also reported as a channel created with a raw name.
        assert data["normalizationFailures"] == []

    @pytest.mark.asyncio
    async def test_a_persisted_neighbour_is_still_listed_when_another_op_fails(
        self, async_client
    ):
        """Ordering the append after the create must not lose a real failure.

        Two createChannel ops, both with a broken engine: the first persists,
        the second is rejected. The one that exists is disclosed; the one that
        does not is not.
        """
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {"results": [], "count": 0, "next": None}
        mock_client.get_streams.return_value = {"results": [], "count": 0, "next": None}
        mock_client.create_channel.side_effect = [
            {"id": 21, "name": "US: CNN"},
            RuntimeError("Dispatcharr rejected the create"),
        ]

        ops = [
            {"type": "createChannel", "tempId": -1, "name": "US: CNN", "normalize": True},
            {"type": "createChannel", "tempId": -2, "name": "US: MSNBC", "normalize": True},
        ]

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.get_normalization_engine",
                   side_effect=RuntimeError("engine offline")), \
             patch("routers.channels.journal"):
            data = await _commit_and_wait(
                async_client, {"operations": ops, "continueOnError": True}
            )

        assert data["operationsApplied"] == 1
        assert data["operationsFailed"] == 1
        assert [f["tempId"] for f in data["normalizationFailures"]] == [-1]
        assert data["normalizationFailures"][0]["nameApplied"] == "US: CNN"

    @pytest.mark.asyncio
    async def test_clean_batch_reports_an_empty_failure_list(self, async_client):
        """`normalizationFailures` is always present, so a caller checks its
        length rather than probing for a key that may or may not exist."""
        mock_client = AsyncMock()
        mock_client.get_channels.return_value = {"results": [], "count": 0, "next": None}
        mock_client.get_streams.return_value = {"results": [], "count": 0, "next": None}
        mock_client.create_channel.return_value = {"id": 12, "name": "CNN"}

        mock_engine = MagicMock()
        mock_engine.normalize.return_value = _make_mock_result("US: CNN", "CNN")

        ops = [{"type": "createChannel", "tempId": -1, "name": "US: CNN", "normalize": True}]

        with patch("routers.channels.get_client", return_value=mock_client), \
             patch("routers.channels.get_normalization_engine", return_value=mock_engine), \
             patch("routers.channels.journal"):
            data = await _commit_and_wait(async_client, {"operations": ops})

        assert data["success"] is True
        assert data["normalizationFailures"] == []
        assert mock_client.create_channel.await_args.args[0]["name"] == "CNN"
