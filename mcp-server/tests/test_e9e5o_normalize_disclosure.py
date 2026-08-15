"""MCP callers are told when the normalization they asked for did not run.

Bead `enhancedchannelmanager-e9e5o`. The backend used to swallow a
normalization failure and return a 200 with the raw name, which is
observationally identical to ``normalize=false``. It now reports the outcome
(``normalization.applied`` on a single create, ``normalizationFailures`` in
the bulk-commit envelope) — and the MCP tools are the live third-party
callers that forward ``normalize``, so they have to render it. A tool that
receives the indicator and drops it leaves the caller exactly as blind as
before.
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


class TestCreateChannelNormalizationDisclosure:
    @pytest.mark.asyncio
    async def test_reports_that_normalization_did_not_run(self):
        """`applied: false` reaches the caller as words, not as silence."""
        mcp = _register()
        client = AsyncMock()
        client.call_endpoint.return_value = {
            "id": 9,
            "name": "US: CNN",
            "channel_number": 100,
            "normalization": {
                "requested": True,
                "applied": False,
                "nameApplied": "US: CNN",
                "error": "engine offline",
            },
        }

        with patch("tools.channels.get_ecm_client", return_value=client):
            result = await mcp.call_tool(
                "create_channel", {"name": "US: CNN", "normalize": True}
            )

        text = _text(result)
        assert "Channel created" in text
        assert "normaliz" in text.lower()
        assert "engine offline" in text

    @pytest.mark.asyncio
    async def test_stays_quiet_when_normalization_applied(self):
        """A clean run adds no noise — the warning has to mean something."""
        mcp = _register()
        client = AsyncMock()
        client.call_endpoint.return_value = {
            "id": 9,
            "name": "CNN",
            "channel_number": 100,
            "normalization": {
                "requested": True,
                "applied": True,
                "nameApplied": "CNN",
                "error": None,
            },
        }

        with patch("tools.channels.get_ecm_client", return_value=client):
            result = await mcp.call_tool(
                "create_channel", {"name": "US: CNN", "normalize": True}
            )

        text = _text(result)
        assert "Channel created" in text
        assert "WARNING" not in text.upper()


class TestBulkCommitNormalizationDisclosure:
    @pytest.mark.asyncio
    async def test_lists_ops_whose_normalization_did_not_run(self):
        mcp = _register()
        client = AsyncMock()
        client.call_endpoint.return_value = {
            "status": "completed",
            "success": True,
            "operationsApplied": 1,
            "tempIdMap": {},
            "groupIdMap": {},
            "validationIssues": [],
            "normalizationFailures": [
                {
                    "tempId": -1,
                    "name": "US: CNN",
                    "nameApplied": "US: CNN",
                    "error": "engine offline",
                }
            ],
        }

        with patch("tools.channels.get_ecm_client", return_value=client):
            result = await mcp.call_tool(
                "bulk_commit_channels",
                {
                    "operations": [
                        {
                            "type": "createChannel",
                            "tempId": -1,
                            "name": "US: CNN",
                            "normalize": True,
                        }
                    ]
                },
            )

        text = _text(result)
        assert "normaliz" in text.lower()
        assert "US: CNN" in text

    @pytest.mark.asyncio
    async def test_clean_batch_says_nothing_about_normalization(self):
        mcp = _register()
        client = AsyncMock()
        client.call_endpoint.return_value = {
            "status": "completed",
            "success": True,
            "operationsApplied": 1,
            "tempIdMap": {},
            "groupIdMap": {},
            "validationIssues": [],
            "normalizationFailures": [],
        }

        with patch("tools.channels.get_ecm_client", return_value=client):
            result = await mcp.call_tool("bulk_commit_channels", {"operations": []})

        assert "normaliz" not in _text(result).lower()
