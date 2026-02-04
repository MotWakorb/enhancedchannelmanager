"""
Integration tests for Group Management.

TDD SPEC: These tests define expected group management behavior.
They will FAIL initially - implementation makes them pass.

Test Spec: Group Management (v6dxf.8.11)
"""
import pytest


class TestGroupCRUD:
    """Tests for group CRUD operations."""

    @pytest.mark.asyncio
    async def test_create_group(self, async_client):
        """POST /api/admin/groups creates group."""
        # Login as admin
        await async_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "adminpass"},
        )

        response = await async_client.post(
            "/api/admin/groups",
            json={
                "name": "developers",
                "description": "Development team",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["group"]["name"] == "developers"
        assert data["group"]["description"] == "Development team"

    @pytest.mark.asyncio
    async def test_list_groups_with_member_count(self, async_client):
        """GET /api/admin/groups returns all groups with member count."""
        # Login as admin
        await async_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "adminpass"},
        )

        response = await async_client.get("/api/admin/groups")
        assert response.status_code == 200
        data = response.json()
        assert "groups" in data
        # Each group should have member_count
        for group in data["groups"]:
            assert "member_count" in group

    @pytest.mark.asyncio
    async def test_update_group(self, async_client):
        """PATCH /api/admin/groups/{id} updates group."""
        # Login as admin
        await async_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "adminpass"},
        )

        # Create group first
        create_response = await async_client.post(
            "/api/admin/groups",
            json={"name": "test_group"},
        )
        group_id = create_response.json()["group"]["id"]

        # Update group
        response = await async_client.patch(
            f"/api/admin/groups/{group_id}",
            json={"description": "Updated description"},
        )
        assert response.status_code == 200
        assert response.json()["group"]["description"] == "Updated description"

    @pytest.mark.asyncio
    async def test_delete_group(self, async_client):
        """DELETE /api/admin/groups/{id} removes group."""
        # Login as admin
        await async_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "adminpass"},
        )

        # Create group first
        create_response = await async_client.post(
            "/api/admin/groups",
            json={"name": "deletable_group"},
        )
        group_id = create_response.json()["group"]["id"]

        # Delete group
        response = await async_client.delete(f"/api/admin/groups/{group_id}")
        assert response.status_code == 200

        # Verify deletion
        get_response = await async_client.get(f"/api/admin/groups/{group_id}")
        assert get_response.status_code == 404


class TestGroupMembership:
    """Tests for group membership management."""

    @pytest.mark.asyncio
    async def test_add_user_to_group(self, async_client):
        """POST /api/admin/groups/{id}/members adds user to group."""
        # Login as admin
        await async_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "adminpass"},
        )

        # Create group
        group_response = await async_client.post(
            "/api/admin/groups",
            json={"name": "team_a"},
        )
        group_id = group_response.json()["group"]["id"]

        # Add user to group
        response = await async_client.post(
            f"/api/admin/groups/{group_id}/members",
            json={"user_id": 2},
        )
        assert response.status_code == 200

        # Verify membership
        members_response = await async_client.get(f"/api/admin/groups/{group_id}/members")
        assert any(m["id"] == 2 for m in members_response.json()["members"])

    @pytest.mark.asyncio
    async def test_remove_user_from_group(self, async_client):
        """DELETE /api/admin/groups/{id}/members/{user_id} removes user."""
        # Login as admin
        await async_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "adminpass"},
        )

        # Create group and add user
        group_response = await async_client.post(
            "/api/admin/groups",
            json={"name": "team_b"},
        )
        group_id = group_response.json()["group"]["id"]

        await async_client.post(
            f"/api/admin/groups/{group_id}/members",
            json={"user_id": 2},
        )

        # Remove user
        response = await async_client.delete(f"/api/admin/groups/{group_id}/members/2")
        assert response.status_code == 200

        # Verify removal
        members_response = await async_client.get(f"/api/admin/groups/{group_id}/members")
        assert not any(m["id"] == 2 for m in members_response.json()["members"])

    @pytest.mark.asyncio
    async def test_user_inherits_group_roles(self, async_client):
        """User inherits all roles assigned to their groups."""
        from auth.rbac import has_permission, get_user_effective_permissions

        # Login as admin
        await async_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "adminpass"},
        )

        # Create group with operator role
        group_response = await async_client.post(
            "/api/admin/groups",
            json={"name": "operators"},
        )
        group_id = group_response.json()["group"]["id"]

        # Assign operator role to group
        await async_client.post(
            f"/api/admin/groups/{group_id}/roles",
            json={"role": "operator"},
        )

        # Add user (who has no direct roles) to group
        await async_client.post(
            f"/api/admin/groups/{group_id}/members",
            json={"user_id": 3},  # Assume user 3 has no roles
        )

        # Check user's effective permissions
        perms_response = await async_client.get("/api/admin/users/3/effective-permissions")
        permissions = perms_response.json()["permissions"]
        assert "channels:write" in permissions  # Inherited from operator role

    @pytest.mark.asyncio
    async def test_removing_user_removes_inherited_roles(self, async_client):
        """Removing user from group removes inherited roles."""
        # Login as admin
        await async_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "adminpass"},
        )

        # Create group with admin role
        group_response = await async_client.post(
            "/api/admin/groups",
            json={"name": "admins_group"},
        )
        group_id = group_response.json()["group"]["id"]

        await async_client.post(
            f"/api/admin/groups/{group_id}/roles",
            json={"role": "admin"},
        )

        # Add user to group
        await async_client.post(
            f"/api/admin/groups/{group_id}/members",
            json={"user_id": 4},
        )

        # Verify user has admin permissions
        perms_before = await async_client.get("/api/admin/users/4/effective-permissions")
        assert "users:write" in perms_before.json()["permissions"]

        # Remove user from group
        await async_client.delete(f"/api/admin/groups/{group_id}/members/4")

        # Verify user no longer has admin permissions
        perms_after = await async_client.get("/api/admin/users/4/effective-permissions")
        assert "users:write" not in perms_after.json()["permissions"]


class TestGroupRoleAssignment:
    """Tests for assigning roles to groups."""

    @pytest.mark.asyncio
    async def test_assign_role_to_group(self, async_client):
        """POST /api/admin/groups/{id}/roles assigns role to group."""
        # Login as admin
        await async_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "adminpass"},
        )

        # Create group
        group_response = await async_client.post(
            "/api/admin/groups",
            json={"name": "role_test_group"},
        )
        group_id = group_response.json()["group"]["id"]

        # Assign role
        response = await async_client.post(
            f"/api/admin/groups/{group_id}/roles",
            json={"role": "operator"},
        )
        assert response.status_code == 200

        # Verify role assignment
        group_detail = await async_client.get(f"/api/admin/groups/{group_id}")
        roles = group_detail.json()["group"]["roles"]
        assert any(r["name"] == "operator" for r in roles)

    @pytest.mark.asyncio
    async def test_group_members_gain_role_permissions(self, async_client):
        """All group members gain role permissions."""
        # Login as admin
        await async_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "adminpass"},
        )

        # Create group
        group_response = await async_client.post(
            "/api/admin/groups",
            json={"name": "editors_group"},
        )
        group_id = group_response.json()["group"]["id"]

        # Add two users to group
        await async_client.post(
            f"/api/admin/groups/{group_id}/members",
            json={"user_id": 5},
        )
        await async_client.post(
            f"/api/admin/groups/{group_id}/members",
            json={"user_id": 6},
        )

        # Assign operator role to group
        await async_client.post(
            f"/api/admin/groups/{group_id}/roles",
            json={"role": "operator"},
        )

        # Both users should have operator permissions
        perms_5 = await async_client.get("/api/admin/users/5/effective-permissions")
        perms_6 = await async_client.get("/api/admin/users/6/effective-permissions")

        assert "channels:write" in perms_5.json()["permissions"]
        assert "channels:write" in perms_6.json()["permissions"]

    @pytest.mark.asyncio
    async def test_remove_role_from_group(self, async_client):
        """DELETE /api/admin/groups/{id}/roles/{role_id} removes role."""
        # Login as admin
        await async_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "adminpass"},
        )

        # Create group with role
        group_response = await async_client.post(
            "/api/admin/groups",
            json={"name": "role_removal_group"},
        )
        group_id = group_response.json()["group"]["id"]

        await async_client.post(
            f"/api/admin/groups/{group_id}/roles",
            json={"role": "operator"},
        )

        # Remove role
        response = await async_client.delete(f"/api/admin/groups/{group_id}/roles/operator")
        assert response.status_code == 200

        # Verify role removed
        group_detail = await async_client.get(f"/api/admin/groups/{group_id}")
        roles = group_detail.json()["group"]["roles"]
        assert not any(r["name"] == "operator" for r in roles)

    @pytest.mark.asyncio
    async def test_role_removal_reflects_in_member_permissions(self, async_client):
        """Role removal reflects in member permissions."""
        # Login as admin
        await async_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "adminpass"},
        )

        # Create group, add user, assign role
        group_response = await async_client.post(
            "/api/admin/groups",
            json={"name": "perm_test_group"},
        )
        group_id = group_response.json()["group"]["id"]

        await async_client.post(
            f"/api/admin/groups/{group_id}/members",
            json={"user_id": 7},
        )

        await async_client.post(
            f"/api/admin/groups/{group_id}/roles",
            json={"role": "operator"},
        )

        # Verify user has permissions
        perms_before = await async_client.get("/api/admin/users/7/effective-permissions")
        assert "channels:write" in perms_before.json()["permissions"]

        # Remove role from group
        await async_client.delete(f"/api/admin/groups/{group_id}/roles/operator")

        # User should no longer have permissions
        perms_after = await async_client.get("/api/admin/users/7/effective-permissions")
        assert "channels:write" not in perms_after.json()["permissions"]
