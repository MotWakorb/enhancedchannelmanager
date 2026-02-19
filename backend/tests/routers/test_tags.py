"""
Unit tests for tag endpoints.

Tests: GET /api/tags/groups, POST /api/tags/groups, GET /api/tags/groups/{id},
       PATCH /api/tags/groups/{id}, DELETE /api/tags/groups/{id},
       POST /api/tags/groups/{id}/tags, PATCH /api/tags/groups/{id}/tags/{id},
       DELETE /api/tags/groups/{id}/tags/{id}, POST /api/tags/test
Uses async_client fixture which patches database session.
"""
import pytest
from unittest.mock import patch

from models import TagGroup, Tag


def _create_tag_group(session, **overrides):
    """Helper to create a TagGroup."""
    defaults = {"name": "Test Group", "is_builtin": False}
    defaults.update(overrides)
    group = TagGroup(**defaults)
    session.add(group)
    session.commit()
    session.refresh(group)
    return group


def _create_tag(session, group_id, value="HD", **overrides):
    """Helper to create a Tag."""
    defaults = {
        "group_id": group_id,
        "value": value,
        "case_sensitive": False,
        "enabled": True,
        "is_builtin": False,
    }
    defaults.update(overrides)
    tag = Tag(**defaults)
    session.add(tag)
    session.commit()
    session.refresh(tag)
    return tag


class TestListTagGroups:
    """Tests for GET /api/tags/groups."""

    @pytest.mark.asyncio
    async def test_returns_empty(self, async_client):
        """Returns empty groups list."""
        response = await async_client.get("/api/tags/groups")
        assert response.status_code == 200
        data = response.json()
        assert data["groups"] == []

    @pytest.mark.asyncio
    async def test_returns_groups_with_tag_count(self, async_client, test_session):
        """Returns groups with correct tag counts."""
        group = _create_tag_group(test_session, name="Quality")
        _create_tag(test_session, group.id, "HD")
        _create_tag(test_session, group.id, "SD")

        response = await async_client.get("/api/tags/groups")
        assert response.status_code == 200
        data = response.json()
        assert len(data["groups"]) == 1
        assert data["groups"][0]["name"] == "Quality"
        assert data["groups"][0]["tag_count"] == 2


class TestCreateTagGroup:
    """Tests for POST /api/tags/groups."""

    @pytest.mark.asyncio
    async def test_creates_group(self, async_client):
        """Creates a new tag group."""
        response = await async_client.post("/api/tags/groups", json={
            "name": "New Group",
            "description": "A test group",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "New Group"
        assert data["description"] == "A test group"
        assert data["is_builtin"] is False

    @pytest.mark.asyncio
    async def test_rejects_duplicate_name(self, async_client, test_session):
        """Returns 400 for duplicate group name."""
        _create_tag_group(test_session, name="Existing")

        response = await async_client.post("/api/tags/groups", json={
            "name": "Existing",
        })
        assert response.status_code == 400
        assert "already exists" in response.json()["detail"]


class TestGetTagGroup:
    """Tests for GET /api/tags/groups/{group_id}."""

    @pytest.mark.asyncio
    async def test_returns_group_with_tags(self, async_client, test_session):
        """Returns group with all its tags."""
        group = _create_tag_group(test_session, name="Quality")
        _create_tag(test_session, group.id, "HD")
        _create_tag(test_session, group.id, "4K")

        response = await async_client.get(f"/api/tags/groups/{group.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Quality"
        assert "tags" in data
        assert len(data["tags"]) == 2

    @pytest.mark.asyncio
    async def test_returns_404_for_nonexistent(self, async_client):
        """Returns 404 for nonexistent group."""
        response = await async_client.get("/api/tags/groups/99999")
        assert response.status_code == 404


class TestUpdateTagGroup:
    """Tests for PATCH /api/tags/groups/{group_id}."""

    @pytest.mark.asyncio
    async def test_updates_name(self, async_client, test_session):
        """Updates group name."""
        group = _create_tag_group(test_session, name="Old Name")

        response = await async_client.patch(
            f"/api/tags/groups/{group.id}",
            json={"name": "New Name"},
        )
        assert response.status_code == 200
        assert response.json()["name"] == "New Name"

    @pytest.mark.asyncio
    async def test_updates_description(self, async_client, test_session):
        """Updates group description."""
        group = _create_tag_group(test_session, name="Test")

        response = await async_client.patch(
            f"/api/tags/groups/{group.id}",
            json={"description": "Updated description"},
        )
        assert response.status_code == 200
        assert response.json()["description"] == "Updated description"

    @pytest.mark.asyncio
    async def test_prevents_renaming_builtin(self, async_client, test_session):
        """Cannot rename built-in group."""
        group = _create_tag_group(test_session, name="Builtin Group", is_builtin=True)

        response = await async_client.patch(
            f"/api/tags/groups/{group.id}",
            json={"name": "Different Name"},
        )
        assert response.status_code == 400
        assert "built-in" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_rejects_duplicate_name(self, async_client, test_session):
        """Returns 400 when renaming to existing name."""
        _create_tag_group(test_session, name="Taken")
        group = _create_tag_group(test_session, name="Mine")

        response = await async_client.patch(
            f"/api/tags/groups/{group.id}",
            json={"name": "Taken"},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_returns_404_for_nonexistent(self, async_client):
        """Returns 404 for nonexistent group."""
        response = await async_client.patch(
            "/api/tags/groups/99999",
            json={"name": "Ghost"},
        )
        assert response.status_code == 404


class TestDeleteTagGroup:
    """Tests for DELETE /api/tags/groups/{group_id}."""

    @pytest.mark.asyncio
    async def test_deletes_group(self, async_client, test_session):
        """Deletes group and cascades to tags."""
        group = _create_tag_group(test_session, name="Delete Me")
        _create_tag(test_session, group.id, "TAG1")
        group_id = group.id

        with patch("normalization_engine.invalidate_tag_cache"):
            response = await async_client.delete(f"/api/tags/groups/{group_id}")

        assert response.status_code == 200
        assert response.json()["status"] == "deleted"

        result = test_session.query(TagGroup).filter(TagGroup.id == group_id).first()
        assert result is None

    @pytest.mark.asyncio
    async def test_prevents_deleting_builtin(self, async_client, test_session):
        """Cannot delete built-in group."""
        group = _create_tag_group(test_session, name="Builtin", is_builtin=True)

        response = await async_client.delete(f"/api/tags/groups/{group.id}")
        assert response.status_code == 400
        assert "built-in" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_returns_404_for_nonexistent(self, async_client):
        """Returns 404 for nonexistent group."""
        response = await async_client.delete("/api/tags/groups/99999")
        assert response.status_code == 404


class TestAddTagsToGroup:
    """Tests for POST /api/tags/groups/{group_id}/tags."""

    @pytest.mark.asyncio
    async def test_adds_tags(self, async_client, test_session):
        """Adds multiple tags to a group."""
        group = _create_tag_group(test_session)

        with patch("normalization_engine.invalidate_tag_cache"):
            response = await async_client.post(
                f"/api/tags/groups/{group.id}/tags",
                json={"tags": ["HD", "SD", "4K"]},
            )

        assert response.status_code == 200
        data = response.json()
        assert sorted(data["created"]) == ["4K", "HD", "SD"]
        assert data["skipped"] == []

    @pytest.mark.asyncio
    async def test_skips_duplicates(self, async_client, test_session):
        """Skips tags that already exist in the group."""
        group = _create_tag_group(test_session)
        _create_tag(test_session, group.id, "HD")

        with patch("normalization_engine.invalidate_tag_cache"):
            response = await async_client.post(
                f"/api/tags/groups/{group.id}/tags",
                json={"tags": ["HD", "4K"]},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["created"] == ["4K"]
        assert data["skipped"] == ["HD"]

    @pytest.mark.asyncio
    async def test_returns_404_for_nonexistent_group(self, async_client):
        """Returns 404 when group doesn't exist."""
        response = await async_client.post(
            "/api/tags/groups/99999/tags",
            json={"tags": ["TAG"]},
        )
        assert response.status_code == 404


class TestUpdateTag:
    """Tests for PATCH /api/tags/groups/{group_id}/tags/{tag_id}."""

    @pytest.mark.asyncio
    async def test_updates_enabled(self, async_client, test_session):
        """Updates tag enabled status."""
        group = _create_tag_group(test_session)
        tag = _create_tag(test_session, group.id, "HD", enabled=True)

        with patch("normalization_engine.invalidate_tag_cache"):
            response = await async_client.patch(
                f"/api/tags/groups/{group.id}/tags/{tag.id}",
                json={"enabled": False},
            )

        assert response.status_code == 200
        assert response.json()["enabled"] is False

    @pytest.mark.asyncio
    async def test_updates_case_sensitive(self, async_client, test_session):
        """Updates tag case_sensitive status."""
        group = _create_tag_group(test_session)
        tag = _create_tag(test_session, group.id, "HD", case_sensitive=False)

        with patch("normalization_engine.invalidate_tag_cache"):
            response = await async_client.patch(
                f"/api/tags/groups/{group.id}/tags/{tag.id}",
                json={"case_sensitive": True},
            )

        assert response.status_code == 200
        assert response.json()["case_sensitive"] is True

    @pytest.mark.asyncio
    async def test_returns_404_for_nonexistent(self, async_client, test_session):
        """Returns 404 for nonexistent tag."""
        group = _create_tag_group(test_session)

        response = await async_client.patch(
            f"/api/tags/groups/{group.id}/tags/99999",
            json={"enabled": False},
        )
        assert response.status_code == 404


class TestDeleteTag:
    """Tests for DELETE /api/tags/groups/{group_id}/tags/{tag_id}."""

    @pytest.mark.asyncio
    async def test_deletes_tag(self, async_client, test_session):
        """Deletes a tag."""
        group = _create_tag_group(test_session)
        tag = _create_tag(test_session, group.id, "HD")
        tag_id = tag.id

        with patch("normalization_engine.invalidate_tag_cache"):
            response = await async_client.delete(
                f"/api/tags/groups/{group.id}/tags/{tag_id}"
            )

        assert response.status_code == 200
        assert response.json()["status"] == "deleted"

        result = test_session.query(Tag).filter(Tag.id == tag_id).first()
        assert result is None

    @pytest.mark.asyncio
    async def test_prevents_deleting_builtin(self, async_client, test_session):
        """Cannot delete built-in tag."""
        group = _create_tag_group(test_session)
        tag = _create_tag(test_session, group.id, "HD", is_builtin=True)

        response = await async_client.delete(
            f"/api/tags/groups/{group.id}/tags/{tag.id}"
        )
        assert response.status_code == 400
        assert "built-in" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_returns_404_for_nonexistent(self, async_client, test_session):
        """Returns 404 for nonexistent tag."""
        group = _create_tag_group(test_session)

        response = await async_client.delete(
            f"/api/tags/groups/{group.id}/tags/99999"
        )
        assert response.status_code == 404


class TestTestTags:
    """Tests for POST /api/tags/test."""

    @pytest.mark.asyncio
    async def test_finds_matches(self, async_client, test_session):
        """Finds tags that match the given text."""
        group = _create_tag_group(test_session)
        _create_tag(test_session, group.id, "HD")
        _create_tag(test_session, group.id, "SD")

        response = await async_client.post("/api/tags/test", json={
            "text": "ESPN HD Sports",
            "group_id": group.id,
        })
        assert response.status_code == 200
        data = response.json()
        assert any(m["value"] == "HD" for m in data["matches"])

    @pytest.mark.asyncio
    async def test_returns_404_for_nonexistent_group(self, async_client):
        """Returns 404 when group doesn't exist."""
        response = await async_client.post("/api/tags/test", json={
            "text": "test",
            "group_id": 99999,
        })
        assert response.status_code == 404
