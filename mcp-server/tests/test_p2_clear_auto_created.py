"""TDD tests for clear_auto_created — bd-1wq7z.18.

Covers the empty-group_ids safety fix:
- empty group_ids without all_groups=True must be rejected locally (no backend call)
- all_groups=True with no group_ids clears everything and reports it correctly
- a non-empty group_ids list clears just those groups and reports the correct count
"""
import pytest
from unittest.mock import AsyncMock, patch

from tools._guardrails import derive_token


def _page(channels):
    """A channels_list page envelope for enumeration in clear_auto_created."""
    return {"results": channels, "next": None, "count": len(channels)}


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
        """all_groups=True with the correct confirm_token triggers the backend
        clear and the summary says 'all groups'. (bd-onazy: clear is now a
        two-call token-gated op; the first call enumerates + previews.)"""
        mcp = _register_and_get_mcp()
        auto_channels = [
            {"id": 1, "name": "A", "auto_created": True, "channel_group_id": 5},
            {"id": 2, "name": "B", "auto_created": True, "channel_group_id": 6},
        ]

        def _side(ep, **kw):
            if ep.name == "channels_list":
                return _page(auto_channels)
            return {"deleted": 42, "updated_count": 42}

        mock_client = _make_mock(side_effect=_side)
        token = derive_token([1, 2])

        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "clear_auto_created", {"all_groups": True, "confirm_token": token}
            )

        text = result[0][0].text
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
        """Specific group_ids are forwarded to the backend and count reported,
        once the confirm_token (derived from the resolved auto-created channel
        ids in those groups) is supplied (bd-onazy two-call gate)."""
        mcp = _register_and_get_mcp()
        # Auto-created channels in the targeted groups; the clear endpoint then
        # returns the deleted count.
        auto_channels = [
            {"id": 100, "name": "A", "auto_created": True, "channel_group_id": 10},
            {"id": 101, "name": "B", "auto_created": True, "channel_group_id": 20},
        ]

        body_seen = {}

        def _side(ep, **kw):
            if ep.name == "channels_list":
                return _page(auto_channels)
            body_seen.update(kw.get("body") or {})
            return {"deleted": 7, "updated_count": 7}

        mock_client = _make_mock(side_effect=_side)
        token = derive_token([100, 101])

        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "clear_auto_created",
                {"group_ids": [10, 20, 30], "confirm_token": token},
            )

        text = result[0][0].text
        # Verify group_ids were forwarded to the clear endpoint.
        assert body_seen.get("group_ids") == [10, 20, 30], (
            f"Expected group_ids=[10,20,30] forwarded to backend, got body={body_seen!r}"
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
        """A single group_id drives the real clear path and reports '1 group'.

        bd-onazy two-call gate: supply a valid confirm_token derived from the
        resolved auto-created channel ids so the tool executes the clear (not
        the preview). Mutation: changing the wording to '1 groups' (wrong
        plural) or '0 groups' must fail this test.
        """
        mcp = _register_and_get_mcp()
        # Three auto-created channels in group 5; ids used to derive the token.
        auto_channels = [
            {"id": 10, "name": "A", "auto_created": True, "channel_group_id": 5},
            {"id": 11, "name": "B", "auto_created": True, "channel_group_id": 5},
            {"id": 12, "name": "C", "auto_created": True, "channel_group_id": 5},
        ]
        body_seen = {}

        def _side(ep, **kw):
            if ep.name == "channels_list":
                return _page(auto_channels)
            body_seen.update(kw.get("body") or {})
            return {"deleted": 3, "updated_count": 3}

        mock_client = _make_mock(side_effect=_side)
        token = derive_token([10, 11, 12])

        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "clear_auto_created",
                {"group_ids": [5], "confirm_token": token},
            )

        text = result[0][0].text
        # The clear path must return the exact wording, not the preview.
        assert "Cleared 3 auto-created channels in 1 group." == text, (
            f"Expected exact clear wording, got: {text!r}"
        )
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

        bd-onazy two-call gate: supply a valid confirm_token derived from the
        resolved auto-created channel ids so the tool executes the clear (not
        the preview). Mutation: if the wording became 'across all groups' or
        'in 3 groups' instead of 'in 2 groups', this test must fail.
        """
        mcp = _register_and_get_mcp()
        # Two auto-created channels, one in each targeted group.
        auto_channels = [
            {"id": 100, "name": "X", "auto_created": True, "channel_group_id": 100},
            {"id": 200, "name": "Y", "auto_created": True, "channel_group_id": 200},
        ]

        def _side(ep, **kw):
            if ep.name == "channels_list":
                return _page(auto_channels)
            return {"deleted": 5, "updated_count": 5}

        mock_client = _make_mock(side_effect=_side)
        token = derive_token([100, 200])

        with patch("tools.channels.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "clear_auto_created",
                {"group_ids": [100, 200], "confirm_token": token},
            )

        text = result[0][0].text
        # Must report the exact clear wording with 2 groups, not a preview.
        assert "Cleared 5 auto-created channels in 2 groups." == text, (
            f"Expected exact clear wording for 2-group call, got: {text!r}"
        )
        assert "all groups" not in text.lower(), (
            f"'all groups' must not appear when specific groups were given, got: {text!r}"
        )
