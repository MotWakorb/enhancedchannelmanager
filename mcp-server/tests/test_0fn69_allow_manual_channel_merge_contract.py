"""GH #801 / bead 0fn69 - allow_manual_channel_merge must survive the contract guard.

The MCP tool signatures for create/update_auto_creation_rule have always
accepted ``allow_manual_channel_merge`` and forwarded it into the payload, and
the backend PUT has always accepted it. What blocked the reporter was the
client-side allowlist: ``_endpoint_contracts._AC_RULE_CREATE_FIELDS`` omitted
the field, so ``ECMClient.call_endpoint`` raised ContractError before the
request left the sidecar, and the only documented workaround for the
cross-run churn bug (bead 0ippw) was unreachable.

The other tests in this suite mock the whole client, so ``call_endpoint`` never
runs and the guard is never exercised. These tests drive the tools through a
REAL ``ECMClient`` with only the transport verb (``put`` / ``post``) mocked, so
the contract guard runs exactly as it does in production.
"""
import pytest
from unittest.mock import AsyncMock, patch

from ecm_client import ECMClient


def _client_with_real_contract_guard(verb: str, return_value: dict):
    """A real ECMClient with only its transport verb stubbed.

    ``call_endpoint`` - and therefore the request_fields subset check - runs for
    real; only the outgoing HTTP call is replaced.
    """
    client = ECMClient()
    setattr(client, verb, AsyncMock(return_value=return_value))
    return client


def _register_and_get_mcp():
    from tools.channel_pipeline import register
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("test")
    register(mcp)
    return mcp


def _sent_body(client, verb: str) -> dict:
    return getattr(client, verb).call_args.kwargs["json_data"]


class TestAllowManualChannelMergeReachesTheBackend:
    """The field passes the call-time guard and lands in the outgoing body."""

    @pytest.mark.asyncio
    async def test_update_rule_forwards_allow_manual_channel_merge(self):
        mcp = _register_and_get_mcp()
        client = _client_with_real_contract_guard("put", {"rule": {"id": 42}})

        with patch("tools.channel_pipeline.get_ecm_client", return_value=client):
            result = await mcp.call_tool(
                "update_auto_creation_rule",
                {"rule_id": 42, "allow_manual_channel_merge": True},
            )

        assert "not in this endpoint's request_fields" not in str(result)
        assert _sent_body(client, "put")["allow_manual_channel_merge"] is True

    @pytest.mark.asyncio
    async def test_create_rule_forwards_allow_manual_channel_merge(self):
        mcp = _register_and_get_mcp()
        client = _client_with_real_contract_guard("post", {"rule": {"id": 43}})

        with patch("tools.channel_pipeline.get_ecm_client", return_value=client):
            result = await mcp.call_tool(
                "create_auto_creation_rule",
                {
                    "name": "PPV merge",
                    "conditions": [{"type": "always"}],
                    "actions": [{"type": "create_channel", "if_exists": "merge"}],
                    "allow_manual_channel_merge": True,
                },
            )

        assert "not in this endpoint's request_fields" not in str(result)
        assert _sent_body(client, "post")["allow_manual_channel_merge"] is True

    @pytest.mark.asyncio
    async def test_update_rule_forwards_allow_manual_channel_merge_false(self):
        """False is a meaningful value (turn the workaround back off), so it
        must ride through rather than being dropped as falsy."""
        mcp = _register_and_get_mcp()
        client = _client_with_real_contract_guard("put", {"rule": {"id": 44}})

        with patch("tools.channel_pipeline.get_ecm_client", return_value=client):
            await mcp.call_tool(
                "update_auto_creation_rule",
                {"rule_id": 44, "allow_manual_channel_merge": False},
            )

        assert _sent_body(client, "put")["allow_manual_channel_merge"] is False


class TestContractGuardStillRejectsUnknownFields:
    """Proves the guard above is live, not vacuously satisfied: a key that is
    genuinely absent from the contract is still rejected client-side."""

    @pytest.mark.asyncio
    async def test_unknown_body_key_is_rejected_by_call_endpoint(self):
        from _endpoint_contracts import ENDPOINTS
        from ecm_client import ContractError

        client = _client_with_real_contract_guard("put", {})

        with pytest.raises(ContractError) as exc:
            await client.call_endpoint(
                ENDPOINTS["ac_update_rule"],
                path_args={"rule_id": 1},
                body={"no_such_rule_field": True},
            )

        assert "no_such_rule_field" in str(exc.value)
