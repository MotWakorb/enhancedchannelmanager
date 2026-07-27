"""enhancedchannelmanager-2uwi3 — MCP propagation of the $N group-ref 422.

The backend now rejects rule saves whose name-transform replacement
references a capture group the pattern does not define (e.g. ``$4`` with 3
groups) with a 422 REGEX_VALIDATION_ERROR envelope. The MCP rule tools call
the same endpoints; these tests pin that the actionable message ("replacement
references group 4 but pattern defines 3 capture groups") survives the whole
error path the real client uses (``ecm_client._http_error`` → tool except
arm → tool result text), rather than degrading to a bare "HTTP 422".
"""
import httpx
import pytest
from unittest.mock import AsyncMock, patch

from ecm_client import _http_error

ACTIONABLE_MESSAGE = (
    "Replacement references group 4 but pattern defines 3 capture groups. "
    "See docs/style_guide.md#regex for guidance."
)

BACKEND_422_BODY = {
    "detail": {
        "error": {
            "code": "REGEX_VALIDATION_ERROR",
            "message": ACTIONABLE_MESSAGE,
            "details": [
                {
                    "field": "actions[0].name_transform_replacement",
                    "code": "REGEX_GROUP_REF_OUT_OF_RANGE",
                    "message": ACTIONABLE_MESSAGE,
                    "severity": "error",
                    "detail": {"group_ref": "4", "groups_defined": 3},
                }
            ],
        }
    }
}

BAD_ACTION = {
    "type": "create_channel",
    "name_template": "{stream_name}",
    "name_transform_pattern": r"(\w+) (\w+) (\w+)",
    "name_transform_replacement": "$2 $1 $3 $4",
}


def _register_and_get_mcp():
    from tools.channel_pipeline import register
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("test")
    register(mcp)
    return mcp


def _text(result) -> str:
    return result[0][0].text


def _backend_422_error() -> RuntimeError:
    """Build the exact error the real ECM client raises for this 422."""
    request = httpx.Request("POST", "http://ecm/api/auto-creation/rules")
    response = httpx.Response(422, json=BACKEND_422_BODY, request=request)
    exc = httpx.HTTPStatusError("422", request=request, response=response)
    return _http_error("POST", "/api/auto-creation/rules", exc)


class TestGroupRefErrorPropagation:
    @pytest.mark.asyncio
    async def test_create_rule_surfaces_actionable_message(self):
        mcp = _register_and_get_mcp()
        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = _backend_422_error()

        with patch("tools.channel_pipeline.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "create_auto_creation_rule",
                {
                    "name": "Bad group ref",
                    "conditions": [{"type": "stream_name_contains", "value": "ESPN"}],
                    "actions": [BAD_ACTION],
                },
            )

        text = _text(result)
        assert "references group 4 but pattern defines 3 capture groups" in text
        assert "422" in text

    @pytest.mark.asyncio
    async def test_update_rule_surfaces_actionable_message(self):
        mcp = _register_and_get_mcp()
        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = _backend_422_error()

        with patch("tools.channel_pipeline.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "update_auto_creation_rule",
                {"rule_id": 7, "actions": [BAD_ACTION]},
            )

        text = _text(result)
        assert "references group 4 but pattern defines 3 capture groups" in text
