"""The MCP bulk-commit renderer never tells an agent to retry work that landed.

Bead `enhancedchannelmanager-e9e5o`, fix round 4.

The reviewer's finding named the MCP presentation as the layer that "obscures
the contradiction further": the tool rendered a bare `SUCCESS`/`FAILED` plus
"N operations submitted" and dropped `operationsApplied`, `operationsFailed`,
`partial` and the whole `errors` list. An agent reading `FAILED: 1 operations
submitted` about a batch whose channel HAD been created retries it and creates
the channel a second time.

The rule this file pins: whatever the envelope says applied is visible to the
caller, and an operation that applied but could not be recorded is named as
such, with an explicit instruction not to retry it.
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


def _one_create_op():
    return [{"type": "createChannel", "tempId": -1, "name": "CNN"}]


class TestBulkCommitAccountingIsVisible:
    @pytest.mark.asyncio
    async def test_applied_and_failed_counts_reach_the_caller(self):
        """"N operations submitted" is what was ASKED FOR, not what happened."""
        mcp = _register()
        client = AsyncMock()
        client.call_endpoint.return_value = {
            "status": "completed",
            "success": False,
            "operationsApplied": 2,
            "operationsFailed": 1,
            "partial": True,
            "errors": [
                {"operationId": "op-2-createChannel", "error": "Dispatcharr rejected the create"}
            ],
            "tempIdMap": {},
            "groupIdMap": {},
            "validationIssues": [],
            "normalizationFailures": [],
        }

        with patch("tools.channels.get_ecm_client", return_value=client):
            result = await mcp.call_tool(
                "bulk_commit_channels", {"operations": _one_create_op() * 3}
            )

        text = _text(result)
        assert "2 applied" in text
        assert "1 failed" in text
        assert "partial" in text.lower()

    @pytest.mark.asyncio
    async def test_an_operation_that_applied_incompletely_is_never_presented_as_undone(self):
        """The reviewer's reproduction, at the layer that renders it.

        Dispatcharr persisted the channel and answered without an id. The
        envelope now counts the operation as applied and marks the error entry
        `applied: true`; the renderer must say the work landed and must say not
        to retry it.
        """
        mcp = _register()
        client = AsyncMock()
        client.call_endpoint.return_value = {
            "status": "completed",
            "success": False,
            "operationsApplied": 1,
            "operationsFailed": 0,
            "partial": True,
            "errors": [
                {
                    "operationId": "op-0-createChannel",
                    "operationType": "createChannel",
                    "entityName": "CNN",
                    "applied": True,
                    "error": "Dispatcharr accepted the create but returned no usable channel id",
                }
            ],
            "tempIdMap": {},
            "groupIdMap": {},
            "validationIssues": [],
            "normalizationFailures": [],
        }

        with patch("tools.channels.get_ecm_client", return_value=client):
            result = await mcp.call_tool(
                "bulk_commit_channels", {"operations": _one_create_op()}
            )

        text = _text(result)
        assert "1 applied" in text
        assert "0 failed" in text
        assert "do not retry" in text.lower()
        assert "op-0-createChannel" in text

    @pytest.mark.asyncio
    async def test_failed_operations_are_named_rather_than_counted(self):
        """`errors` used to be fetched, commented about, and dropped."""
        mcp = _register()
        client = AsyncMock()
        client.call_endpoint.return_value = {
            "status": "completed",
            "success": False,
            "operationsApplied": 0,
            "operationsFailed": 1,
            "partial": False,
            "errors": [
                {
                    "operationId": "op-0-updateChannel",
                    "operationType": "updateChannel",
                    "channelId": 7,
                    "channelName": "ESPN",
                    "error": "upstream 400",
                }
            ],
            "tempIdMap": {},
            "groupIdMap": {},
            "validationIssues": [],
            "normalizationFailures": [],
        }

        with patch("tools.channels.get_ecm_client", return_value=client):
            result = await mcp.call_tool(
                "bulk_commit_channels", {"operations": _one_create_op()}
            )

        text = _text(result)
        assert "op-0-updateChannel" in text
        assert "upstream 400" in text

    @pytest.mark.asyncio
    async def test_a_clean_batch_stays_quiet_about_failures(self):
        """The additions are conditional; a clean batch reads as it always did."""
        mcp = _register()
        client = AsyncMock()
        client.call_endpoint.return_value = {
            "status": "completed",
            "success": True,
            "operationsApplied": 3,
            "operationsFailed": 0,
            "partial": False,
            "errors": [],
            "tempIdMap": {},
            "groupIdMap": {},
            "validationIssues": [],
            "normalizationFailures": [],
        }

        with patch("tools.channels.get_ecm_client", return_value=client):
            result = await mcp.call_tool(
                "bulk_commit_channels", {"operations": _one_create_op() * 3}
            )

        text = _text(result)
        assert "3 applied" in text
        assert "do not retry" not in text.lower()
        assert "partial" not in text.lower()
