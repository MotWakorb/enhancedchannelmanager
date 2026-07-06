"""enhancedchannelmanager-uc51o.6 — MCP restore_auto_creation_snapshot tool +
has_snapshot surfacing on list_auto_creation_executions (ADR-010 §D8).

The restore is a SAFETY-CRITICAL, optimistic-overwrite, non-undoable bulk
write-back across (often hundreds of) channels. Mirroring the rollback-tool
tests (test_0hjrk_rollback.py), this module pins the confirm posture and the
result/error surfacing:

* confirm=False (default) → the tool returns the §D5 warning (with the channel
  count when the snapshot metadata is readable) and DOES NOT call the restore
  endpoint.
* confirm=True → the tool calls the restore endpoint (confirm passed through as
  the backend query gate) and renders restored/removed counts.
* partial failure → the per-channel failures are surfaced, never a silent
  partial success.
* no snapshot (404) → a helpful message pointing at rollback_auto_creation,
  on both the confirm=False and confirm=True paths.
* has_snapshot from the backend list response is surfaced per execution.
"""
import pytest
from unittest.mock import AsyncMock, patch

from _endpoint_contracts import ENDPOINTS


def _register():
    from tools.channel_pipeline import register
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("test")
    register(mcp)
    return mcp


def _text(result) -> str:
    return result[0][0].text


# ---------------------------------------------------------------------------
# confirm posture
# ---------------------------------------------------------------------------


class TestConfirmGate:
    """confirm=False refuses to act; confirm=True performs the restore."""

    @pytest.mark.asyncio
    async def test_confirm_false_does_not_call_restore_endpoint(self):
        """Default confirm=False returns the warning and never POSTs the restore.

        It MAY read the snapshot (GET) for the channel count, but it must NOT
        call the restore endpoint (POST /restore-snapshot).
        """
        mcp = _register()

        async def fake_call_endpoint(ep, **kwargs):
            # The warning path reads the snapshot for the channel count.
            assert ep is not ENDPOINTS["ac_restore_snapshot"], (
                "restore endpoint MUST NOT be called when confirm=False"
            )
            if ep is ENDPOINTS["ac_get_execution_snapshot"]:
                return {"execution_id": 3, "channel_count": 42,
                        "snapshot_time": "2026-05-25T00:00:00Z"}
            raise AssertionError(f"unexpected endpoint {ep.name}")

        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = fake_call_endpoint

        with patch("tools.channel_pipeline.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "restore_auto_creation_snapshot", {"execution_id": 3}
            )

        text = _text(result)
        assert "confirm" in text.lower()
        assert "not performed" in text.lower() or "not restored" in text.lower() \
            or "confirmation required" in text.lower()
        # Blast radius surfaced.
        assert "42" in text
        # Tells the caller how to proceed.
        assert "confirm=true" in text.lower()

    @pytest.mark.asyncio
    async def test_confirm_false_warning_without_count_when_snapshot_unreadable(self):
        """If the snapshot metadata read raises a non-404 error, the warning is
        STILL returned (best-effort count) and the restore is NOT called."""
        mcp = _register()

        async def fake_call_endpoint(ep, **kwargs):
            assert ep is not ENDPOINTS["ac_restore_snapshot"]
            if ep is ENDPOINTS["ac_get_execution_snapshot"]:
                raise RuntimeError("GET /snapshot -> HTTP 500 Internal Server Error")
            raise AssertionError(f"unexpected endpoint {ep.name}")

        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = fake_call_endpoint

        with patch("tools.channel_pipeline.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "restore_auto_creation_snapshot", {"execution_id": 8}
            )

        text = _text(result)
        assert "confirm=true" in text.lower()
        assert "cannot be undone" in text.lower()

    @pytest.mark.asyncio
    async def test_confirm_true_calls_restore_endpoint_with_confirm_query(self):
        """confirm=True calls the restore endpoint, passing confirm=true as the
        backend query gate, and returns the result counts."""
        mcp = _register()

        seen = {}

        async def fake_call_endpoint(ep, **kwargs):
            seen["ep"] = ep
            seen["kwargs"] = kwargs
            return {
                "success": True,
                "execution_id": 5,
                "rule_name": "Sports Rule",
                "removed_channels": 2,
                "restored_channels": 40,
                "failed_channels": [],
            }

        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = fake_call_endpoint

        with patch("tools.channel_pipeline.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "restore_auto_creation_snapshot",
                {"execution_id": 5, "confirm": True},
            )

        # The restore endpoint was the one called, with confirm in the query.
        assert seen["ep"] is ENDPOINTS["ac_restore_snapshot"]
        assert seen["kwargs"].get("query") == {"confirm": True}
        assert seen["kwargs"].get("path_args") == {"execution_id": 5}

        text = _text(result)
        assert "40" in text
        assert "2" in text
        assert "restored" in text.lower()


# ---------------------------------------------------------------------------
# partial-failure surfacing
# ---------------------------------------------------------------------------


class TestPartialFailureSurfaced:
    @pytest.mark.asyncio
    async def test_failed_channels_are_surfaced(self):
        """A restore that failed on some channels names them — never a silent
        partial success."""
        mcp = _register()

        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = {
            "success": False,
            "execution_id": 9,
            "rule_name": "News Rule",
            "removed_channels": 0,
            "restored_channels": 197,
            "failed_channels": [
                {"id": 11, "name": "GONE", "error": "404"},
                {"id": 12, "name": "VANISHED", "error": "stream 9001 not found"},
            ],
        }

        with patch("tools.channel_pipeline.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "restore_auto_creation_snapshot",
                {"execution_id": 9, "confirm": True},
            )

        text = _text(result)
        assert "197" in text
        assert "fail" in text.lower()
        # Each failed channel is named with its reason.
        assert "11" in text and "GONE" in text
        assert "12" in text and "VANISHED" in text
        assert "404" in text
        assert "stream 9001 not found" in text


# ---------------------------------------------------------------------------
# no-snapshot (404) → helpful message
# ---------------------------------------------------------------------------


class TestNoSnapshotGuidance:
    @pytest.mark.asyncio
    async def test_confirm_true_404_points_at_rollback(self):
        """confirm=True against a snapshot-less run (backend 404) → guidance to
        use rollback_auto_creation, not a raw error."""
        mcp = _register()

        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = RuntimeError(
            "POST /api/channel-pipeline/executions/4/restore-snapshot -> HTTP 404 "
            "Not Found: No snapshot for execution 4; use /rollback instead."
        )

        with patch("tools.channel_pipeline.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "restore_auto_creation_snapshot",
                {"execution_id": 4, "confirm": True},
            )

        text = _text(result)
        assert "rollback_channel_pipeline" in text
        assert "no" in text.lower() and "snapshot" in text.lower()

    @pytest.mark.asyncio
    async def test_confirm_false_404_points_at_rollback_without_restoring(self):
        """confirm=False where the snapshot read 404s → guidance to use
        rollback_auto_creation; the restore endpoint is never called."""
        mcp = _register()

        async def fake_call_endpoint(ep, **kwargs):
            assert ep is not ENDPOINTS["ac_restore_snapshot"]
            if ep is ENDPOINTS["ac_get_execution_snapshot"]:
                raise RuntimeError(
                    "GET /api/channel-pipeline/executions/4/snapshot -> HTTP 404 "
                    "Not Found: No snapshot for this execution"
                )
            raise AssertionError(f"unexpected endpoint {ep.name}")

        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = fake_call_endpoint

        with patch("tools.channel_pipeline.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "restore_auto_creation_snapshot", {"execution_id": 4}
            )

        text = _text(result)
        assert "rollback_channel_pipeline" in text

    @pytest.mark.asyncio
    async def test_confirm_true_no_snapshot_dict_signal(self):
        """Defensive: a no_snapshot signal that arrives as a dict (not HTTP 404)
        is surfaced as the same guidance, not a phantom success."""
        mcp = _register()

        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = {
            "success": False,
            "no_snapshot": True,
            "error": "No snapshot for execution 6; use /rollback instead.",
        }

        with patch("tools.channel_pipeline.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "restore_auto_creation_snapshot",
                {"execution_id": 6, "confirm": True},
            )

        text = _text(result)
        assert "rollback_channel_pipeline" in text
        assert "restored" not in text.lower() or "no" in text.lower()


# ---------------------------------------------------------------------------
# has_snapshot surfacing on the list tool
# ---------------------------------------------------------------------------


class TestCallEndpointQueryOnPost:
    """call_endpoint forwards a query param on a write verb (the confirm gate)
    while leaving the no-query write path byte-identical (regression guard)."""

    @pytest.mark.asyncio
    async def test_post_forwards_query_param(self):
        from ecm_client import ECMClient

        client = ECMClient()
        with patch.object(ECMClient, "post", new=AsyncMock(return_value={"ok": True})) as post_mock:
            await client.call_endpoint(
                ENDPOINTS["ac_restore_snapshot"],
                path_args={"execution_id": 5},
                query={"confirm": True},
                timeout=300.0,
            )
        post_mock.assert_awaited_once_with(
            "/api/channel-pipeline/executions/5/restore-snapshot",
            json_data=None,
            timeout=300.0,
            params={"confirm": True},
        )

    @pytest.mark.asyncio
    async def test_post_without_query_omits_params(self):
        """No query → the verb method is called WITHOUT params (back-compat)."""
        from ecm_client import ECMClient

        client = ECMClient()
        with patch.object(ECMClient, "post", new=AsyncMock(return_value={"ok": True})) as post_mock:
            await client.call_endpoint(
                ENDPOINTS["channels_add_stream"],
                path_args={"channel_id": 42},
                body={"stream_id": 7},
            )
        post_mock.assert_awaited_once_with(
            "/api/channels/42/add-stream", json_data={"stream_id": 7}, timeout=None
        )


class TestListExecutionsHasSnapshot:
    @pytest.mark.asyncio
    async def test_snapshot_marker_appears_per_execution(self):
        """list_auto_creation_executions marks each run [snapshot] / [no
        snapshot] from the backend has_snapshot boolean."""
        mcp = _register()

        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = {
            "executions": [
                {"id": 1, "status": "completed", "created_at": "2026-05-25",
                 "channels_created": 12, "has_snapshot": True},
                {"id": 2, "status": "completed", "created_at": "2026-05-24",
                 "channels_created": 0, "has_snapshot": False},
            ],
            "total": 2,
        }

        with patch("tools.channel_pipeline.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "list_auto_creation_executions", {"limit": 10}
            )

        text = _text(result)
        # Execution 1 (has_snapshot True) shows [snapshot]; execution 2 shows
        # [no snapshot].
        line1 = next(ln for ln in text.splitlines() if ln.strip().startswith("#1"))
        line2 = next(ln for ln in text.splitlines() if ln.strip().startswith("#2"))
        assert "[snapshot]" in line1 and "[no snapshot]" not in line1
        assert "[no snapshot]" in line2

    @pytest.mark.asyncio
    async def test_missing_has_snapshot_defaults_to_no_snapshot(self):
        """An execution row without has_snapshot (older backend) renders as
        [no snapshot] rather than erroring."""
        mcp = _register()

        mock_client = AsyncMock()
        mock_client.call_endpoint.return_value = {
            "executions": [
                {"id": 7, "status": "completed", "created_at": "2026-05-25",
                 "channels_created": 3},
            ],
            "total": 1,
        }

        with patch("tools.channel_pipeline.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "list_auto_creation_executions", {"limit": 10}
            )

        text = _text(result)
        assert "[no snapshot]" in text
