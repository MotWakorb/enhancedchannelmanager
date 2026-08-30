"""An agent is never told to retry an operation whose side effects landed.

Bead `enhancedchannelmanager-1e4at`. `deleteChannelGroup` reparents the group's
member channels and then deletes the group. When the reparent lands and the
delete fails, the envelope now says so — `operationsPartiallyApplied` plus
`sideEffectsLanded` on the operation's own `errors` entry — and this file pins
the layer that renders it.

Without this, the operation appears under "Failed operations" beside a create
that never happened, and the two read identically to an agent. They are not the
same: retrying the create is correct, and retrying the delete means acting on a
group whose membership has already silently changed. The renderer already keeps
`applied: true` entries in their own bucket for exactly this reason; a partially
applied operation is a third case and needs a third bucket, because it is
neither "done" nor "not started".
"""
import pytest
from unittest.mock import AsyncMock, patch

from mcp.server.fastmcp import FastMCP


def _register() -> FastMCP:
    mcp = FastMCP("test")
    from tools.channels import register

    register(mcp)
    return mcp


def _text(result) -> str:
    return result[0][0].text if isinstance(result[0], (list, tuple)) else result[0].text


def _delete_group_op():
    return [{"type": "deleteChannelGroup", "groupId": 42}]


def _envelope(**overrides):
    envelope = {
        "status": "completed",
        "success": False,
        "operationsApplied": 0,
        "operationsFailed": 1,
        "operationsPartiallyApplied": 1,
        "partial": True,
        "errors": [
            {
                "operationId": "op-0-deleteChannelGroup",
                "operationType": "deleteChannelGroup",
                "error": "400 Cannot delete group with associated channels",
                "sideEffectsLanded": True,
            }
        ],
        "tempIdMap": {},
        "groupIdMap": {},
        "validationIssues": [],
        "normalizationFailures": [],
    }
    envelope.update(overrides)
    return envelope


class TestPartiallyAppliedOperationsAreRenderedApart:

    @pytest.mark.asyncio
    async def test_a_partially_applied_operation_is_not_a_plain_failure(self):
        """The reproduction. It must not appear under "Failed operations"."""
        mcp = _register()
        client = AsyncMock()
        client.call_endpoint.return_value = _envelope()

        with patch("tools.channels.get_ecm_client", return_value=client):
            result = await mcp.call_tool(
                "bulk_commit_channels", {"operations": _delete_group_op()}
            )

        text = _text(result)
        assert "Failed operations" not in text, text
        assert "op-0-deleteChannelGroup" in text

    @pytest.mark.asyncio
    async def test_the_agent_is_told_the_upstream_state_has_changed(self):
        """"Failed" alone invites a retry against a precondition that moved."""
        mcp = _register()
        client = AsyncMock()
        client.call_endpoint.return_value = _envelope()

        with patch("tools.channels.get_ecm_client", return_value=client):
            result = await mcp.call_tool(
                "bulk_commit_channels", {"operations": _delete_group_op()}
            )

        text = _text(result)
        assert "PARTIALLY APPLIED" in text, text
        assert "reconcile" in text.lower()
        # The count reaches the caller too, not only the per-entry rendering.
        assert "1 partially applied" in text

    @pytest.mark.asyncio
    async def test_a_clean_failure_is_still_rendered_as_a_failure(self):
        """The new bucket must be able to be EMPTY while a failure is present.

        Otherwise every failure would read as "something landed", and the
        distinction the bucket exists to draw would carry no information.
        """
        mcp = _register()
        client = AsyncMock()
        client.call_endpoint.return_value = _envelope(
            operationsPartiallyApplied=0,
            errors=[
                {
                    "operationId": "op-0-deleteChannelGroup",
                    "operationType": "deleteChannelGroup",
                    "error": "500 Server Error",
                }
            ],
        )

        with patch("tools.channels.get_ecm_client", return_value=client):
            result = await mcp.call_tool(
                "bulk_commit_channels", {"operations": _delete_group_op()}
            )

        text = _text(result)
        assert "Failed operations" in text, text
        assert "PARTIALLY APPLIED" not in text

    @pytest.mark.asyncio
    async def test_all_three_buckets_can_coexist(self):
        """Landed-and-unrecorded, partially-applied and clean-failed are distinct."""
        mcp = _register()
        client = AsyncMock()
        client.call_endpoint.return_value = _envelope(
            operationsApplied=1,
            operationsFailed=2,
            operationsPartiallyApplied=1,
            errors=[
                {
                    "operationId": "op-0-createChannel",
                    "entityName": "CNN",
                    "error": "no usable id in the response",
                    "applied": True,
                },
                {
                    "operationId": "op-1-deleteChannelGroup",
                    "error": "400 Cannot delete group with associated channels",
                    "sideEffectsLanded": True,
                },
                {"operationId": "op-2-createChannel", "error": "500 Server Error"},
            ],
        )

        with patch("tools.channels.get_ecm_client", return_value=client):
            result = await mcp.call_tool(
                "bulk_commit_channels", {"operations": _delete_group_op() * 3}
            )

        text = _text(result)
        assert "APPLIED BUT NOT FULLY RECORDED" in text
        assert "PARTIALLY APPLIED" in text
        assert "Failed operations (1)" in text, text
