"""bd-0hjrk.2 — MCP rollback_auto_creation: refusal surfacing + success wording.

The engine refuses to roll back an execution with no recorded created/modified
entities (returns ``success=False`` + an error). The backend router turns that
into an HTTP 400 whose ``detail`` is the refusal message, so the MCP client's
``call_endpoint`` raises with that text. The tool must surface it clearly rather
than report a phantom clean rollback.

On success the tool clarifies the wording: it reports channels deleted AND
channels restored to their pre-run stream sets (streams un-merged).
"""
import pytest
from unittest.mock import AsyncMock, patch


def _make_ecm_client_mock(**method_returns):
    mock = AsyncMock()
    for method, return_value in method_returns.items():
        getattr(mock, method).return_value = return_value
    return mock


class TestRollbackRefusalSurfaced:
    """The refusal message reaches the user clearly."""

    @pytest.mark.asyncio
    async def test_refusal_via_http_error_is_surfaced(self):
        """Backend 400 (engine refusal) -> call_endpoint raises -> tool returns
        the refusal text, not a phantom 'rolled back' success line."""
        from tools.channel_pipeline import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        refusal = (
            "No recorded created or modified entities for execution 5; cannot "
            "guarantee a rollback (the run predates entity tracking or made no "
            "changes). Refusing to mark it rolled_back."
        )
        mock_client = AsyncMock()
        mock_client.call_endpoint.side_effect = RuntimeError(
            f"POST /api/auto-creation/executions/5/rollback failed (400): {refusal}"
        )

        with patch("tools.channel_pipeline.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("rollback_auto_creation", {"execution_id": 5})

        text = result[0][0].text
        assert "cannot guarantee a rollback" in text.lower()
        assert "5" in text
        # Must NOT claim a clean rollback happened.
        assert "rolled back. 0 channel" not in text.lower()

    @pytest.mark.asyncio
    async def test_refusal_via_success_false_dict_is_surfaced(self):
        """Defensive: if the engine refusal reaches the tool as a dict
        (success False / error) instead of an exception, surface it too."""
        from tools.channel_pipeline import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        refusal = (
            "No recorded created or modified entities for execution 9; cannot "
            "guarantee a rollback. Refusing to mark it rolled_back."
        )
        mock_client = _make_ecm_client_mock(call_endpoint={
            "success": False,
            "error": refusal,
        })

        with patch("tools.channel_pipeline.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("rollback_auto_creation", {"execution_id": 9})

        text = result[0][0].text
        assert "cannot guarantee a rollback" in text.lower()
        # The tool surfaces the refusal as "Cannot roll back execution 9: ..."
        # The old assertion 'not in ... or "refus" in' was always-True because
        # the error path says "Cannot roll back" (no "rolled back" phrase).
        # Assert the exact prefix so inversion/deletion of the success=False
        # handling would fail this test.
        assert text.startswith("Cannot roll back execution 9"), (
            f"Expected refusal to start with 'Cannot roll back execution 9', got: {text!r}"
        )


class TestRollbackSuccessWording:
    """On success the tool clarifies deleted vs un-merged channels."""

    @pytest.mark.asyncio
    async def test_success_reports_deleted_and_unmerged(self):
        """N deleted + M restored renders both counts and explains un-merge."""
        from tools.channel_pipeline import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_ecm_client_mock(call_endpoint={
            "success": True,
            "execution_id": 7,
            "rule_name": "Sports Rule",
            "entities_removed": 45,
            "entities_restored": 3,
        })

        with patch("tools.channel_pipeline.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("rollback_auto_creation", {"execution_id": 7})

        text = result[0][0].text
        assert "45" in text
        assert "3" in text
        assert "delet" in text.lower()
        # Clarified wording: restored channels had their pre-run stream sets put
        # back (streams un-merged).
        assert "un-merg" in text.lower() or "pre-run stream" in text.lower()

    @pytest.mark.asyncio
    async def test_success_with_only_deletions(self):
        """A create-only run rollback reports deletions and 0 restored."""
        from tools.channel_pipeline import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_ecm_client_mock(call_endpoint={
            "success": True,
            "execution_id": 8,
            "rule_name": "News Rule",
            "entities_removed": 10,
            "entities_restored": 0,
        })

        with patch("tools.channel_pipeline.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("rollback_auto_creation", {"execution_id": 8})

        text = result[0][0].text
        assert "10" in text
        assert "delet" in text.lower()
