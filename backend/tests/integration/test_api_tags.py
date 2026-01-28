"""
Integration tests for the Tag Engine API endpoints.

Tests the tag groups and tags CRUD operations via the FastAPI test client.
"""
import pytest


class TestListTagGroups:
    """Tests for GET /api/tags/groups endpoint."""

    @pytest.mark.asyncio
    async def test_list_returns_empty_array(self, async_client):
        """GET /api/tags/groups returns empty groups array when no groups."""
        response = await async_client.get("/api/tags/groups")
        assert response.status_code == 200
        data = response.json()
        assert "groups" in data
        assert isinstance(data["groups"], list)

    @pytest.mark.asyncio
    async def test_list_returns_created_groups(self, async_client, test_session):
        """GET /api/tags/groups returns created groups."""
        from tests.fixtures.factories import create_tag_group

        create_tag_group(test_session, name="Quality Tags")
        create_tag_group(test_session, name="Country Tags")

        response = await async_client.get("/api/tags/groups")
        assert response.status_code == 200

        data = response.json()
        groups = data["groups"]
        assert len(groups) >= 2
        names = [g["name"] for g in groups]
        assert "Quality Tags" in names
        assert "Country Tags" in names

    @pytest.mark.asyncio
    async def test_list_includes_tag_count(self, async_client, test_session):
        """GET /api/tags/groups includes tag_count field."""
        from tests.fixtures.factories import create_tag_group, create_tag

        group = create_tag_group(test_session, name="Test Group")
        create_tag(test_session, group_id=group.id, value="TAG1")
        create_tag(test_session, group_id=group.id, value="TAG2")

        response = await async_client.get("/api/tags/groups")
        assert response.status_code == 200

        data = response.json()
        groups = data["groups"]
        test_group = next((g for g in groups if g["name"] == "Test Group"), None)
        assert test_group is not None
        assert test_group["tag_count"] == 2


class TestCreateTagGroup:
    """Tests for POST /api/tags/groups endpoint."""

    @pytest.mark.asyncio
    async def test_create_tag_group(self, async_client):
        """POST /api/tags/groups creates a new group."""
        response = await async_client.post(
            "/api/tags/groups",
            json={
                "name": "New Test Group",
                "description": "A test description"
            }
        )
        assert response.status_code == 200

        data = response.json()
        assert data["name"] == "New Test Group"
        assert data["description"] == "A test description"
        assert data["is_builtin"] is False
        assert "id" in data

    @pytest.mark.asyncio
    async def test_create_requires_name(self, async_client):
        """POST /api/tags/groups requires name field."""
        response = await async_client.post(
            "/api/tags/groups",
            json={"description": "Missing name"}
        )
        assert response.status_code == 422  # Validation error

    @pytest.mark.asyncio
    async def test_create_rejects_duplicate_name(self, async_client, test_session):
        """POST /api/tags/groups rejects duplicate names."""
        from tests.fixtures.factories import create_tag_group

        create_tag_group(test_session, name="Existing Group")

        response = await async_client.post(
            "/api/tags/groups",
            json={"name": "Existing Group"}
        )
        assert response.status_code == 400
        assert "already exists" in response.json()["detail"]


class TestGetTagGroup:
    """Tests for GET /api/tags/groups/{group_id} endpoint."""

    @pytest.mark.asyncio
    async def test_get_tag_group_returns_details(self, async_client, test_session):
        """GET /api/tags/groups/{group_id} returns group with tags."""
        from tests.fixtures.factories import create_tag_group, create_tag

        group = create_tag_group(test_session, name="Test Group")
        create_tag(test_session, group_id=group.id, value="TAG1")
        create_tag(test_session, group_id=group.id, value="TAG2")

        response = await async_client.get(f"/api/tags/groups/{group.id}")
        assert response.status_code == 200

        data = response.json()
        assert data["name"] == "Test Group"
        assert "tags" in data
        assert len(data["tags"]) == 2
        tag_values = [t["value"] for t in data["tags"]]
        assert "TAG1" in tag_values
        assert "TAG2" in tag_values

    @pytest.mark.asyncio
    async def test_get_tag_group_not_found(self, async_client):
        """GET /api/tags/groups/{group_id} returns 404 for unknown ID."""
        response = await async_client.get("/api/tags/groups/99999")
        assert response.status_code == 404


class TestUpdateTagGroup:
    """Tests for PATCH /api/tags/groups/{group_id} endpoint."""

    @pytest.mark.asyncio
    async def test_update_tag_group_name(self, async_client, test_session):
        """PATCH /api/tags/groups/{group_id} updates name."""
        from tests.fixtures.factories import create_tag_group

        group = create_tag_group(test_session, name="Original Name")

        response = await async_client.patch(
            f"/api/tags/groups/{group.id}",
            json={"name": "Updated Name"}
        )
        assert response.status_code == 200

        data = response.json()
        assert data["name"] == "Updated Name"

    @pytest.mark.asyncio
    async def test_update_tag_group_description(self, async_client, test_session):
        """PATCH /api/tags/groups/{group_id} updates description."""
        from tests.fixtures.factories import create_tag_group

        group = create_tag_group(test_session, name="Test", description="Old")

        response = await async_client.patch(
            f"/api/tags/groups/{group.id}",
            json={"description": "New description"}
        )
        assert response.status_code == 200

        data = response.json()
        assert data["description"] == "New description"

    @pytest.mark.asyncio
    async def test_update_builtin_group_name_rejected(self, async_client, test_session):
        """PATCH /api/tags/groups/{group_id} rejects renaming built-in groups."""
        from tests.fixtures.factories import create_tag_group

        group = create_tag_group(test_session, name="Built-in Group", is_builtin=True)

        response = await async_client.patch(
            f"/api/tags/groups/{group.id}",
            json={"name": "New Name"}
        )
        assert response.status_code == 400
        assert "Cannot rename built-in" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_update_tag_group_not_found(self, async_client):
        """PATCH /api/tags/groups/{group_id} returns 404 for unknown ID."""
        response = await async_client.patch(
            "/api/tags/groups/99999",
            json={"name": "Test"}
        )
        assert response.status_code == 404


class TestDeleteTagGroup:
    """Tests for DELETE /api/tags/groups/{group_id} endpoint."""

    @pytest.mark.asyncio
    async def test_delete_tag_group(self, async_client, test_session):
        """DELETE /api/tags/groups/{group_id} removes group."""
        from tests.fixtures.factories import create_tag_group

        group = create_tag_group(test_session, name="To Delete")
        group_id = group.id

        response = await async_client.delete(f"/api/tags/groups/{group_id}")
        assert response.status_code == 200

        # Verify deleted
        response = await async_client.get(f"/api/tags/groups/{group_id}")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_builtin_group_rejected(self, async_client, test_session):
        """DELETE /api/tags/groups/{group_id} rejects deleting built-in groups."""
        from tests.fixtures.factories import create_tag_group

        group = create_tag_group(test_session, name="Built-in", is_builtin=True)

        response = await async_client.delete(f"/api/tags/groups/{group.id}")
        assert response.status_code == 400
        assert "Cannot delete built-in" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_delete_tag_group_not_found(self, async_client):
        """DELETE /api/tags/groups/{group_id} returns 404 for unknown ID."""
        response = await async_client.delete("/api/tags/groups/99999")
        assert response.status_code == 404


class TestAddTagsToGroup:
    """Tests for POST /api/tags/groups/{group_id}/tags endpoint."""

    @pytest.mark.asyncio
    async def test_add_single_tag(self, async_client, test_session):
        """POST /api/tags/groups/{group_id}/tags adds a single tag."""
        from tests.fixtures.factories import create_tag_group

        group = create_tag_group(test_session, name="Test Group")

        response = await async_client.post(
            f"/api/tags/groups/{group.id}/tags",
            json={"tags": ["NewTag"]}
        )
        assert response.status_code == 200

        data = response.json()
        assert "created" in data
        assert len(data["created"]) == 1
        assert data["created"][0] == "NewTag"

    @pytest.mark.asyncio
    async def test_add_multiple_tags(self, async_client, test_session):
        """POST /api/tags/groups/{group_id}/tags adds multiple tags."""
        from tests.fixtures.factories import create_tag_group

        group = create_tag_group(test_session, name="Test Group")

        response = await async_client.post(
            f"/api/tags/groups/{group.id}/tags",
            json={"tags": ["Tag1", "Tag2", "Tag3"]}
        )
        assert response.status_code == 200

        data = response.json()
        assert len(data["created"]) == 3

    @pytest.mark.asyncio
    async def test_add_duplicate_tag_skipped(self, async_client, test_session):
        """POST /api/tags/groups/{group_id}/tags skips duplicate tags."""
        from tests.fixtures.factories import create_tag_group, create_tag

        group = create_tag_group(test_session, name="Test Group")
        create_tag(test_session, group_id=group.id, value="Existing")

        response = await async_client.post(
            f"/api/tags/groups/{group.id}/tags",
            json={"tags": ["Existing", "NewTag"]}
        )
        assert response.status_code == 200

        data = response.json()
        assert len(data["created"]) == 1
        assert len(data["skipped"]) == 1
        assert "Existing" in data["skipped"]

    @pytest.mark.asyncio
    async def test_add_tags_to_nonexistent_group(self, async_client):
        """POST /api/tags/groups/{group_id}/tags returns 404 for unknown group."""
        response = await async_client.post(
            "/api/tags/groups/99999/tags",
            json={"tags": ["Test"]}
        )
        assert response.status_code == 404


class TestUpdateTag:
    """Tests for PATCH /api/tags/groups/{group_id}/tags/{tag_id} endpoint."""

    @pytest.mark.asyncio
    async def test_update_tag_enabled(self, async_client, test_session):
        """PATCH /api/tags/groups/{group_id}/tags/{tag_id} updates enabled status."""
        from tests.fixtures.factories import create_tag_group, create_tag

        group = create_tag_group(test_session, name="Test Group")
        tag = create_tag(test_session, group_id=group.id, value="TestTag", enabled=True)

        response = await async_client.patch(
            f"/api/tags/groups/{group.id}/tags/{tag.id}",
            json={"enabled": False}
        )
        assert response.status_code == 200

        data = response.json()
        assert data["enabled"] is False

    @pytest.mark.asyncio
    async def test_update_tag_case_sensitive(self, async_client, test_session):
        """PATCH /api/tags/groups/{group_id}/tags/{tag_id} updates case_sensitive."""
        from tests.fixtures.factories import create_tag_group, create_tag

        group = create_tag_group(test_session, name="Test Group")
        tag = create_tag(test_session, group_id=group.id, value="TestTag", case_sensitive=False)

        response = await async_client.patch(
            f"/api/tags/groups/{group.id}/tags/{tag.id}",
            json={"case_sensitive": True}
        )
        assert response.status_code == 200

        data = response.json()
        assert data["case_sensitive"] is True

    @pytest.mark.asyncio
    async def test_update_tag_not_found(self, async_client, test_session):
        """PATCH /api/tags/groups/{group_id}/tags/{tag_id} returns 404 for unknown tag."""
        from tests.fixtures.factories import create_tag_group

        group = create_tag_group(test_session, name="Test Group")

        response = await async_client.patch(
            f"/api/tags/groups/{group.id}/tags/99999",
            json={"enabled": False}
        )
        assert response.status_code == 404


class TestDeleteTag:
    """Tests for DELETE /api/tags/groups/{group_id}/tags/{tag_id} endpoint."""

    @pytest.mark.asyncio
    async def test_delete_tag(self, async_client, test_session):
        """DELETE /api/tags/groups/{group_id}/tags/{tag_id} removes tag."""
        from tests.fixtures.factories import create_tag_group, create_tag

        group = create_tag_group(test_session, name="Test Group")
        tag = create_tag(test_session, group_id=group.id, value="ToDelete")

        response = await async_client.delete(
            f"/api/tags/groups/{group.id}/tags/{tag.id}"
        )
        assert response.status_code == 200

        # Verify deleted via group details
        response = await async_client.get(f"/api/tags/groups/{group.id}")
        data = response.json()
        tag_values = [t["value"] for t in data.get("tags", [])]
        assert "ToDelete" not in tag_values

    @pytest.mark.asyncio
    async def test_delete_builtin_tag_rejected(self, async_client, test_session):
        """DELETE /api/tags/groups/{group_id}/tags/{tag_id} rejects built-in tags."""
        from tests.fixtures.factories import create_tag_group, create_tag

        group = create_tag_group(test_session, name="Test Group")
        tag = create_tag(test_session, group_id=group.id, value="Builtin", is_builtin=True)

        response = await async_client.delete(
            f"/api/tags/groups/{group.id}/tags/{tag.id}"
        )
        assert response.status_code == 400
        assert "Cannot delete built-in" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_delete_tag_not_found(self, async_client, test_session):
        """DELETE /api/tags/groups/{group_id}/tags/{tag_id} returns 404 for unknown tag."""
        from tests.fixtures.factories import create_tag_group

        group = create_tag_group(test_session, name="Test Group")

        response = await async_client.delete(
            f"/api/tags/groups/{group.id}/tags/99999"
        )
        assert response.status_code == 404


class TestTestTags:
    """Tests for POST /api/tags/test endpoint."""

    @pytest.mark.asyncio
    async def test_test_tags_finds_match(self, async_client, test_session):
        """POST /api/tags/test finds matching tags in text."""
        from tests.fixtures.factories import create_tag_group, create_tag

        group = create_tag_group(test_session, name="Quality Tags")
        create_tag(test_session, group_id=group.id, value="HD")
        create_tag(test_session, group_id=group.id, value="4K")

        response = await async_client.post(
            "/api/tags/test",
            json={
                "group_id": group.id,
                "text": "ESPN News HD"
            }
        )
        assert response.status_code == 200

        data = response.json()
        assert data["match_count"] > 0
        matched_values = [m["value"] for m in data["matches"]]
        assert "HD" in matched_values

    @pytest.mark.asyncio
    async def test_test_tags_no_match(self, async_client, test_session):
        """POST /api/tags/test returns no match when no tags found."""
        from tests.fixtures.factories import create_tag_group, create_tag

        group = create_tag_group(test_session, name="Quality Tags")
        create_tag(test_session, group_id=group.id, value="HD")

        response = await async_client.post(
            "/api/tags/test",
            json={
                "group_id": group.id,
                "text": "ESPN News"
            }
        )
        assert response.status_code == 200

        data = response.json()
        assert data["match_count"] == 0

    @pytest.mark.asyncio
    async def test_test_tags_group_not_found(self, async_client):
        """POST /api/tags/test returns 404 for unknown group."""
        response = await async_client.post(
            "/api/tags/test",
            json={
                "group_id": 99999,
                "text": "Test"
            }
        )
        assert response.status_code == 404
