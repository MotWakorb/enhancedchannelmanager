"""
Unit tests for M3U digest/changes endpoints.

Tests: 7 endpoints covering M3U change logs, change summary,
       account changes, snapshots, digest settings CRUD, and test digest.
Mocks: get_session() (via conftest), M3UChangeDetector, M3UDigestTask.
"""
import json
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from models import M3UChangeLog, M3USnapshot, M3UDigestSettings


def _create_change_log(session, **overrides):
    """Insert a change log record for testing."""
    defaults = {
        "m3u_account_id": 1,
        "change_time": datetime(2024, 6, 15, 12, 0, 0),
        "change_type": "streams_added",
        "group_name": "Sports",
        "stream_names": json.dumps(["ESPN", "Fox Sports"]),
        "count": 2,
        "enabled": True,
    }
    defaults.update(overrides)
    record = M3UChangeLog(**defaults)
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def _create_snapshot(session, **overrides):
    """Insert a snapshot record for testing."""
    defaults = {
        "m3u_account_id": 1,
        "snapshot_time": datetime(2024, 6, 15, 12, 0, 0),
        "groups_data": json.dumps({"groups": [{"name": "Sports", "stream_count": 50}]}),
        "total_streams": 50,
        "dispatcharr_updated_at": "2024-06-15T12:00:00Z",
    }
    defaults.update(overrides)
    record = M3USnapshot(**defaults)
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def _create_digest_settings(session, **overrides):
    """Insert digest settings for testing."""
    defaults = {
        "enabled": False,
        "frequency": "daily",
        "include_group_changes": True,
        "include_stream_changes": True,
        "show_detailed_list": True,
        "min_changes_threshold": 1,
        "send_to_discord": False,
    }
    defaults.update(overrides)
    record = M3UDigestSettings(**defaults)
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


class TestGetM3UChanges:
    """Tests for GET /api/m3u/changes."""

    @pytest.mark.asyncio
    async def test_returns_empty(self, async_client):
        """Returns empty results when no changes exist."""
        response = await async_client.get("/api/m3u/changes")

        assert response.status_code == 200
        data = response.json()
        assert data["results"] == []
        assert data["total"] == 0
        assert data["page"] == 1

    @pytest.mark.asyncio
    async def test_returns_changes(self, async_client, test_session):
        """Returns paginated change logs."""
        _create_change_log(test_session, change_type="streams_added", count=5)
        _create_change_log(test_session, change_type="group_removed", group_name="News", count=1)

        response = await async_client.get("/api/m3u/changes")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert len(data["results"]) == 2

    @pytest.mark.asyncio
    async def test_filters_by_account(self, async_client, test_session):
        """Filters changes by M3U account ID."""
        _create_change_log(test_session, m3u_account_id=1)
        _create_change_log(test_session, m3u_account_id=2)

        response = await async_client.get("/api/m3u/changes", params={"m3u_account_id": 1})

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1

    @pytest.mark.asyncio
    async def test_filters_by_change_type(self, async_client, test_session):
        """Filters changes by type."""
        _create_change_log(test_session, change_type="streams_added")
        _create_change_log(test_session, change_type="group_removed")

        response = await async_client.get("/api/m3u/changes", params={"change_type": "group_removed"})

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["results"][0]["change_type"] == "group_removed"

    @pytest.mark.asyncio
    async def test_filters_by_enabled(self, async_client, test_session):
        """Filters changes by enabled status."""
        _create_change_log(test_session, enabled=True)
        _create_change_log(test_session, enabled=False)

        response = await async_client.get("/api/m3u/changes", params={"enabled": False})

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["results"][0]["enabled"] is False

    @pytest.mark.asyncio
    async def test_pagination(self, async_client, test_session):
        """Paginates results correctly."""
        for i in range(5):
            _create_change_log(test_session, count=i)

        response = await async_client.get("/api/m3u/changes", params={"page": 2, "page_size": 2})

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 5
        assert len(data["results"]) == 2
        assert data["page"] == 2
        assert data["total_pages"] == 3

    @pytest.mark.asyncio
    async def test_sort_ascending(self, async_client, test_session):
        """Sorts results in ascending order."""
        _create_change_log(test_session, count=10,
                           change_time=datetime(2024, 6, 15, 12, 0, 0))
        _create_change_log(test_session, count=5,
                           change_time=datetime(2024, 6, 14, 12, 0, 0))

        response = await async_client.get("/api/m3u/changes", params={
            "sort_by": "change_time", "sort_order": "asc",
        })

        assert response.status_code == 200
        data = response.json()
        assert data["results"][0]["count"] == 5  # Earlier date first


class TestGetM3UChangesSummary:
    """Tests for GET /api/m3u/changes/summary."""

    @pytest.mark.asyncio
    async def test_returns_summary(self, async_client):
        """Returns aggregated change summary."""
        mock_detector = MagicMock()
        mock_detector.get_change_summary.return_value = {
            "total_changes": 10,
            "groups_added": 2,
            "groups_removed": 1,
        }

        with patch("m3u_change_detector.M3UChangeDetector", return_value=mock_detector):
            response = await async_client.get("/api/m3u/changes/summary")

        assert response.status_code == 200
        data = response.json()
        assert data["total_changes"] == 10

    @pytest.mark.asyncio
    async def test_passes_hours_param(self, async_client):
        """Passes hours parameter to detector."""
        mock_detector = MagicMock()
        mock_detector.get_change_summary.return_value = {}

        with patch("m3u_change_detector.M3UChangeDetector", return_value=mock_detector):
            response = await async_client.get("/api/m3u/changes/summary", params={"hours": 48})

        assert response.status_code == 200
        mock_detector.get_change_summary.assert_called_once()

    @pytest.mark.asyncio
    async def test_passes_account_filter(self, async_client):
        """Passes account filter to detector."""
        mock_detector = MagicMock()
        mock_detector.get_change_summary.return_value = {}

        with patch("m3u_change_detector.M3UChangeDetector", return_value=mock_detector):
            response = await async_client.get("/api/m3u/changes/summary", params={
                "m3u_account_id": 1,
            })

        assert response.status_code == 200


class TestGetM3UAccountChanges:
    """Tests for GET /api/m3u/accounts/{account_id}/changes."""

    @pytest.mark.asyncio
    async def test_returns_account_changes(self, async_client, test_session):
        """Returns changes for a specific account."""
        _create_change_log(test_session, m3u_account_id=1)
        _create_change_log(test_session, m3u_account_id=2)

        response = await async_client.get("/api/m3u/accounts/1/changes")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["m3u_account_id"] == 1

    @pytest.mark.asyncio
    async def test_filters_by_type(self, async_client, test_session):
        """Filters account changes by type."""
        _create_change_log(test_session, m3u_account_id=1, change_type="streams_added")
        _create_change_log(test_session, m3u_account_id=1, change_type="group_removed")

        response = await async_client.get("/api/m3u/accounts/1/changes", params={
            "change_type": "streams_added",
        })

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1

    @pytest.mark.asyncio
    async def test_returns_empty_for_unknown_account(self, async_client):
        """Returns empty for nonexistent account."""
        response = await async_client.get("/api/m3u/accounts/999/changes")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0


class TestGetM3USnapshots:
    """Tests for GET /api/m3u/snapshots."""

    @pytest.mark.asyncio
    async def test_returns_snapshots(self, async_client, test_session):
        """Returns recent snapshots."""
        _create_snapshot(test_session, m3u_account_id=1)
        _create_snapshot(test_session, m3u_account_id=2)

        response = await async_client.get("/api/m3u/snapshots")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

    @pytest.mark.asyncio
    async def test_filters_by_account(self, async_client, test_session):
        """Filters snapshots by M3U account."""
        _create_snapshot(test_session, m3u_account_id=1)
        _create_snapshot(test_session, m3u_account_id=2)

        response = await async_client.get("/api/m3u/snapshots", params={"m3u_account_id": 1})

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1

    @pytest.mark.asyncio
    async def test_respects_limit(self, async_client, test_session):
        """Respects the limit parameter."""
        for i in range(5):
            _create_snapshot(test_session, m3u_account_id=1,
                             snapshot_time=datetime(2024, 6, 15 - i, 12, 0, 0))

        response = await async_client.get("/api/m3u/snapshots", params={"limit": 2})

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

    @pytest.mark.asyncio
    async def test_returns_empty(self, async_client):
        """Returns empty list when no snapshots exist."""
        response = await async_client.get("/api/m3u/snapshots")

        assert response.status_code == 200
        assert response.json() == []


class TestGetDigestSettings:
    """Tests for GET /api/m3u/digest/settings."""

    @pytest.mark.asyncio
    async def test_returns_existing_settings(self, async_client, test_session):
        """Returns existing digest settings."""
        _create_digest_settings(test_session, enabled=True, frequency="hourly")

        response = await async_client.get("/api/m3u/digest/settings")

        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is True
        assert data["frequency"] == "hourly"

    @pytest.mark.asyncio
    async def test_creates_default_settings(self, async_client):
        """Creates default settings when none exist."""
        response = await async_client.get("/api/m3u/digest/settings")

        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is False
        assert data["frequency"] == "daily"


class TestUpdateDigestSettings:
    """Tests for PUT /api/m3u/digest/settings."""

    @pytest.mark.asyncio
    async def test_updates_settings(self, async_client, test_session):
        """Updates digest settings."""
        _create_digest_settings(test_session)

        with patch("routers.m3u_digest.journal"):
            response = await async_client.put("/api/m3u/digest/settings", json={
                "enabled": True,
                "frequency": "hourly",
            })

        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is True
        assert data["frequency"] == "hourly"

    @pytest.mark.asyncio
    async def test_rejects_invalid_frequency(self, async_client, test_session):
        """Returns 400 for invalid frequency."""
        _create_digest_settings(test_session)

        with patch("routers.m3u_digest.journal"):
            response = await async_client.put("/api/m3u/digest/settings", json={
                "frequency": "biweekly",
            })

        assert response.status_code == 400
        assert "frequency" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_rejects_invalid_email(self, async_client, test_session):
        """Returns 400 for invalid email address."""
        _create_digest_settings(test_session)

        with patch("routers.m3u_digest.journal"):
            response = await async_client.put("/api/m3u/digest/settings", json={
                "email_recipients": ["not-an-email"],
            })

        assert response.status_code == 400
        assert "email" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_rejects_invalid_threshold(self, async_client, test_session):
        """Returns 400 for threshold less than 1."""
        _create_digest_settings(test_session)

        with patch("routers.m3u_digest.journal"):
            response = await async_client.put("/api/m3u/digest/settings", json={
                "min_changes_threshold": 0,
            })

        assert response.status_code == 400
        assert "threshold" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_rejects_invalid_regex(self, async_client, test_session):
        """Returns 400 for invalid regex pattern."""
        _create_digest_settings(test_session)

        with patch("routers.m3u_digest.journal"):
            response = await async_client.put("/api/m3u/digest/settings", json={
                "exclude_group_patterns": ["[invalid"],
            })

        assert response.status_code == 400
        assert "regex" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_accepts_valid_emails(self, async_client, test_session):
        """Accepts valid email recipients."""
        _create_digest_settings(test_session)

        with patch("routers.m3u_digest.journal"):
            response = await async_client.put("/api/m3u/digest/settings", json={
                "email_recipients": ["user@example.com", "admin@test.org"],
            })

        assert response.status_code == 200
        data = response.json()
        assert data["email_recipients"] == ["user@example.com", "admin@test.org"]


class TestSendTestDigest:
    """Tests for POST /api/m3u/digest/test."""

    @pytest.mark.asyncio
    async def test_rejects_no_targets(self, async_client, test_session):
        """Returns 400 when no notification targets configured."""
        _create_digest_settings(test_session, send_to_discord=False)

        response = await async_client.post("/api/m3u/digest/test")

        assert response.status_code == 400
        assert "no notification targets" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_executes_digest(self, async_client, test_session):
        """Executes digest when targets are configured."""
        _create_digest_settings(test_session, send_to_discord=True)

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.message = "Digest sent"
        mock_result.details = {}

        mock_task = MagicMock()
        mock_task.execute = AsyncMock(return_value=mock_result)

        with patch("tasks.m3u_digest.M3UDigestTask", return_value=mock_task):
            response = await async_client.post("/api/m3u/digest/test")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        mock_task.execute.assert_called_once_with(force=True)

    @pytest.mark.asyncio
    async def test_handles_execution_error(self, async_client, test_session):
        """Returns 500 on task execution error."""
        _create_digest_settings(test_session, send_to_discord=True)

        mock_task = MagicMock()
        mock_task.execute = AsyncMock(side_effect=RuntimeError("SMTP failed"))

        with patch("tasks.m3u_digest.M3UDigestTask", return_value=mock_task):
            response = await async_client.post("/api/m3u/digest/test")

        assert response.status_code == 500
