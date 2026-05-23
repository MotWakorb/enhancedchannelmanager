"""TDD tests for clear_auto_created — bd-1wq7z.18.

Covers the empty-group_ids safety fix:
- empty group_ids without all_groups=True must be rejected locally (no backend call)
- all_groups=True with no group_ids clears everything and reports it correctly
- a non-empty group_ids list clears just those groups and reports the correct count
"""
import pytest
from unittest.mock import AsyncMock, patch


def _make_mock(return_value=None, side_effect=None):
    """Return an AsyncMock whose call_endpoint returns return_value (or raises)."""
    mock = AsyncMock()
    if side_effect is not None:
        mock.call_endpoint.side_effect = side_effect
    else:
        mock.call_endpoint.return_value = return_value
    return mock


def _register_and_get_mcp():
    """Register channels tools and return the FastMCP instance."""
    from tools.channels import register
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("test")
    register(mcp)
    return mcp


class TestClearAutoCreatedEmptyGroupIds:
    """empty group_ids WITHOUT all_groups=True must be rejected locally — no backend call."""

    @pytest.mark.asyncio
    async def test_empty_group_ids_rejected_no_backend_call(self):
        """empty group_ids is rejected before reaching the ECM backend."""
        mcp = _register_and_get_mcp()
        mock_client = _make_mock(return_value={"deleted": 999})

        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("clear_auto_created", {"group_ids": []})

        text = result[0][0].text
        # Must be an error / rejection — not a success
        assert "Error" in text or "error" in text or "reject" in text.lower() or "empty" in text.lower() or "require" in text.lower() or "must" in text.lower(), (
            f"Expected a rejection message for empty group_ids, got: {text!r}"
        )
        # No destructive call to the backend
        mock_client.call_endpoint.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_group_ids_error_message_is_actionable(self):
        """Rejection message tells the caller what to do instead."""
        mcp = _register_and_get_mcp()
        mock_client = _make_mock(return_value={"deleted": 0})

        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("clear_auto_created", {"group_ids": []})

        text = result[0][0].text
        # Message should hint at all_groups or group_ids
        assert "all_groups" in text or "group_ids" in text, (
            f"Rejection message should mention all_groups or group_ids, got: {text!r}"
        )

    @pytest.mark.asyncio
    async def test_none_group_ids_without_all_groups_rejected(self):
        """None group_ids without all_groups=True must also be rejected."""
        mcp = _register_and_get_mcp()
        mock_client = _make_mock(return_value={"deleted": 999})

        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            # Calling with neither group_ids nor all_groups — should be rejected
            result = await mcp.call_tool("clear_auto_created", {})

        text = result[0][0].text
        assert "Error" in text or "error" in text or "reject" in text.lower() or "empty" in text.lower() or "require" in text.lower() or "must" in text.lower() or "all_groups" in text, (
            f"Expected rejection without explicit intent, got: {text!r}"
        )
        mock_client.call_endpoint.assert_not_called()


class TestClearAutoCreatedAllGroupsFlag:
    """all_groups=True clears all and reports it correctly."""

    @pytest.mark.asyncio
    async def test_all_groups_true_sends_request_and_reports_scope(self):
        """all_groups=True triggers backend call and summary says 'all groups'."""
        mcp = _register_and_get_mcp()
        mock_client = _make_mock(return_value={"deleted": 42, "updated_count": 42})

        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool("clear_auto_created", {"all_groups": True})

        text = result[0][0].text
        mock_client.call_endpoint.assert_called_once()
        # Scope must say "all groups" (not "in 0 groups" or "in 1 groups")
        assert "all" in text.lower(), (
            f"Expected 'all groups' scope in result, got: {text!r}"
        )
        assert "42" in text, (
            f"Expected deleted count 42 in result, got: {text!r}"
        )

    @pytest.mark.asyncio
    async def test_all_groups_true_with_group_ids_is_rejected(self):
        """Passing both all_groups=True and a non-empty group_ids is ambiguous — reject it."""
        mcp = _register_and_get_mcp()
        mock_client = _make_mock(return_value={"deleted": 0})

        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "clear_auto_created",
                {"all_groups": True, "group_ids": [1, 2, 3]},
            )

        text = result[0][0].text
        # Must not silently proceed with one interpretation
        assert "Error" in text or "error" in text or "not both" in text.lower() or "cannot" in text.lower() or "ambiguous" in text.lower() or "conflict" in text.lower(), (
            f"Expected rejection for ambiguous all_groups+group_ids, got: {text!r}"
        )
        mock_client.call_endpoint.assert_not_called()

    @pytest.mark.asyncio
    async def test_all_groups_false_with_empty_group_ids_rejected(self):
        """all_groups=False + group_ids=[] is still ambiguous/unsafe — reject it."""
        mcp = _register_and_get_mcp()
        mock_client = _make_mock(return_value={"deleted": 99})

        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "clear_auto_created",
                {"all_groups": False, "group_ids": []},
            )

        text = result[0][0].text
        assert "Error" in text or "error" in text or "require" in text.lower() or "empty" in text.lower() or "must" in text.lower(), (
            f"Expected rejection for all_groups=False + group_ids=[], got: {text!r}"
        )
        mock_client.call_endpoint.assert_not_called()


class TestClearAutoCreatedSpecificGroups:
    """Non-empty group_ids list clears just those groups and reports the correct count."""

    @pytest.mark.asyncio
    async def test_specific_group_ids_sends_them_and_reports_count(self):
        """Specific group_ids are forwarded to the backend and count reported."""
        mcp = _register_and_get_mcp()
        mock_client = _make_mock(return_value={"deleted": 7, "updated_count": 7})

        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "clear_auto_created",
                {"group_ids": [10, 20, 30]},
            )

        text = result[0][0].text
        mock_client.call_endpoint.assert_called_once()
        # Verify group_ids were forwarded
        call_kwargs = mock_client.call_endpoint.call_args
        body = call_kwargs.kwargs.get("body") or (call_kwargs.args[1] if len(call_kwargs.args) > 1 else {})
        assert body.get("group_ids") == [10, 20, 30], (
            f"Expected group_ids=[10,20,30] forwarded to backend, got body={body!r}"
        )
        # Scope must say "3 groups" (not "all groups")
        assert "3" in text, (
            f"Expected count 3 in result for 3-group call, got: {text!r}"
        )
        assert "all" not in text.lower() or "3" in text, (
            f"Scope should reference specific groups, got: {text!r}"
        )
        # Deleted count
        assert "7" in text, (
            f"Expected deleted count 7 in result, got: {text!r}"
        )

    @pytest.mark.asyncio
    async def test_single_group_id_reported_correctly(self):
        """A single group_id is forwarded and reported as '1 group'."""
        mcp = _register_and_get_mcp()
        mock_client = _make_mock(return_value={"deleted": 3, "updated_count": 3})

        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "clear_auto_created",
                {"group_ids": [5]},
            )

        text = result[0][0].text
        mock_client.call_endpoint.assert_called_once()
        assert "3" in text
        # Should NOT say "all groups"
        assert "all groups" not in text.lower(), (
            f"'all groups' should not appear for single-group call, got: {text!r}"
        )

    @pytest.mark.asyncio
    async def test_api_error_is_surfaced(self):
        """Backend errors are surfaced as an Error message."""
        mcp = _register_and_get_mcp()
        mock_client = _make_mock(side_effect=Exception("connection timeout"))

        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "clear_auto_created",
                {"group_ids": [1]},
            )

        text = result[0][0].text
        assert "Error" in text or "error" in text, (
            f"Expected error surfaced, got: {text!r}"
        )
        assert "connection timeout" in text

    @pytest.mark.asyncio
    async def test_scope_wording_matches_what_happens(self):
        """Scope wording in the response must match the actual operation.

        This is the core bug from bd-1wq7z.18: 'in 0 groups' was reported as
        'across all groups' due to empty-list truthiness. Verify the group count
        in the summary matches the actual group_ids length.
        """
        mcp = _register_and_get_mcp()
        mock_client = _make_mock(return_value={"deleted": 5, "updated_count": 5})

        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "clear_auto_created",
                {"group_ids": [100, 200]},
            )

        text = result[0][0].text
        # Must mention 2 groups (the actual count), NOT "all groups"
        assert "2" in text, f"Expected '2' in scope text, got: {text!r}"
        assert "all groups" not in text.lower(), (
            f"'all groups' must not appear when specific groups were given, got: {text!r}"
        )
