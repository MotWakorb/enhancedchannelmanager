"""TDD tests for bd-1wq7z.19 — create_task_schedule timezone parameter.

Tests are written RED-first: they assert the fix before it exists.
After implementation the tests must turn GREEN.
"""
import pytest
from unittest.mock import AsyncMock, patch


def _make_client(return_value=None, side_effect=None):
    mock = AsyncMock()
    if side_effect is not None:
        mock.call_endpoint.side_effect = side_effect
    else:
        mock.call_endpoint.return_value = return_value
    return mock


class TestCreateTaskScheduleTimezone:
    """bd-1wq7z.19 — timezone must be passed through to the backend body."""

    @pytest.mark.asyncio
    async def test_timezone_in_body_when_provided(self):
        """When caller passes timezone it must appear in the POST body."""
        from tools.tasks import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_client(
            return_value={"id": 7, "description": "daily 08:00 America/Chicago"}
        )

        with patch("tools.tasks.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "create_task_schedule",
                {
                    "task_id": "m3u_refresh",
                    "schedule_type": "daily",
                    "schedule_time": "08:00",
                    "timezone": "America/Chicago",
                },
            )

        # Verify call_endpoint was called with a body that contains timezone
        call_kwargs = mock_client.call_endpoint.call_args
        body = call_kwargs.kwargs.get("body") or call_kwargs[1].get("body")
        assert body is not None, "call_endpoint must be called with a body kwarg"
        assert "timezone" in body, "body must include 'timezone'"
        assert body["timezone"] == "America/Chicago"

        text = result[0][0].text
        assert "Schedule created" in text

    @pytest.mark.asyncio
    async def test_default_timezone_utc(self):
        """When timezone is not provided the default 'UTC' must be sent in body."""
        from tools.tasks import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_client(
            return_value={"id": 8, "description": "interval 3600s"}
        )

        with patch("tools.tasks.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "create_task_schedule",
                {
                    "task_id": "stream_probe",
                    "schedule_type": "interval",
                    "interval_seconds": 3600,
                },
            )

        call_kwargs = mock_client.call_endpoint.call_args
        body = call_kwargs.kwargs.get("body") or call_kwargs[1].get("body")
        assert body is not None
        # Default timezone should be "UTC"
        assert "timezone" in body, "body must include 'timezone' even when not provided by caller"
        assert body["timezone"] == "UTC"

    @pytest.mark.asyncio
    async def test_timezone_not_in_body_when_explicitly_none(self):
        """Passing timezone=None explicitly should still send the default 'UTC'."""
        from tools.tasks import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_client(return_value={"id": 9, "description": "monthly"})

        with patch("tools.tasks.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "create_task_schedule",
                {
                    "task_id": "epg_refresh",
                    "schedule_type": "monthly",
                    "schedule_time": "03:00",
                    "day_of_month": 1,
                    "timezone": None,
                },
            )

        call_kwargs = mock_client.call_endpoint.call_args
        body = call_kwargs.kwargs.get("body") or call_kwargs[1].get("body")
        assert body is not None
        # When None is passed, default "UTC" should still be sent
        assert "timezone" in body
        assert body["timezone"] == "UTC"

    @pytest.mark.asyncio
    async def test_america_new_york_timezone(self):
        """A non-UTC timezone passes through correctly."""
        from tools.tasks import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_client(
            return_value={"id": 10, "description": "weekly Monday 09:00 America/New_York"}
        )

        with patch("tools.tasks.get_ecm_client", return_value=mock_client):
            await mcp.call_tool(
                "create_task_schedule",
                {
                    "task_id": "m3u_refresh",
                    "schedule_type": "weekly",
                    "schedule_time": "09:00",
                    "days_of_week": [1],
                    "timezone": "America/New_York",
                },
            )

        call_kwargs = mock_client.call_endpoint.call_args
        body = call_kwargs.kwargs.get("body") or call_kwargs[1].get("body")
        assert body["timezone"] == "America/New_York"

    @pytest.mark.asyncio
    async def test_error_returns_message(self):
        """API errors are caught and reported gracefully."""
        from tools.tasks import register
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("test")
        register(mcp)

        mock_client = _make_client(side_effect=Exception("backend unavailable"))

        with patch("tools.tasks.get_ecm_client", return_value=mock_client):
            result = await mcp.call_tool(
                "create_task_schedule",
                {"task_id": "m3u_refresh", "schedule_type": "daily"},
            )

        text = result[0][0].text
        assert "Error" in text
        assert "backend unavailable" in text
