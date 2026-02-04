"""
Integration tests for Role-Based Access Control (RBAC).

TDD SPEC: These tests define expected RBAC behavior.
They will FAIL initially - implementation makes them pass.

Test Spec: RBAC Permissions (v6dxf.8.10)
"""
import pytest
from unittest.mock import patch, MagicMock


class TestSystemRoles:
    """Tests for system-defined roles."""

    @pytest.mark.asyncio
    async def test_admin_role_has_all_permissions(self, async_client):
        """'admin' role has all permissions."""
        from auth.rbac import has_permission, get_role

        admin_role = get_role("admin")
        assert admin_role is not None

        # Admin should have all common permissions
        permissions = [
            ("channels", "read"),
            ("channels", "write"),
            ("channels", "delete"),
            ("users", "read"),
            ("users", "write"),
            ("users", "delete"),
            ("settings", "read"),
            ("settings", "write"),
            ("admin", "access"),
        ]

        for resource, action in permissions:
            assert has_permission(
                admin_role, resource, action
            ), f"Admin should have {action} on {resource}"

    @pytest.mark.asyncio
    async def test_operator_role_has_read_write_not_user_management(self, async_client):
        """'operator' role has read/write but not user management."""
        from auth.rbac import has_permission, get_role

        operator_role = get_role("operator")

        # Should have read/write on channels
        assert has_permission(operator_role, "channels", "read") is True
        assert has_permission(operator_role, "channels", "write") is True

        # Should NOT have user management
        assert has_permission(operator_role, "users", "write") is False
        assert has_permission(operator_role, "admin", "access") is False

    @pytest.mark.asyncio
    async def test_viewer_role_has_read_only(self, async_client):
        """'viewer' role has read-only access."""
        from auth.rbac import has_permission, get_role

        viewer_role = get_role("viewer")

        # Should have read
        assert has_permission(viewer_role, "channels", "read") is True
        assert has_permission(viewer_role, "settings", "read") is True

        # Should NOT have write
        assert has_permission(viewer_role, "channels", "write") is False
        assert has_permission(viewer_role, "settings", "write") is False
        assert has_permission(viewer_role, "users", "write") is False

    @pytest.mark.asyncio
    async def test_system_roles_cannot_be_deleted(self, async_client):
        """System roles cannot be deleted."""
        # Login as admin
        await async_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "adminpass"},
        )

        # Try to delete system roles
        for role_name in ["admin", "operator", "viewer"]:
            response = await async_client.delete(f"/api/admin/roles/{role_name}")
            assert response.status_code in (400, 403)
            assert "system" in response.json()["detail"].lower()


class TestPermissionChecking:
    """Tests for permission checking logic."""

    @pytest.mark.asyncio
    async def test_viewer_has_read_permission(self, async_client):
        """has_permission(user, 'channels', 'read') returns True for viewer."""
        from auth.rbac import has_permission, User, Role

        viewer_user = User(id=1, username="viewer", roles=[Role(name="viewer")])
        assert has_permission(viewer_user, "channels", "read") is True

    @pytest.mark.asyncio
    async def test_viewer_no_write_permission(self, async_client):
        """has_permission(user, 'channels', 'write') returns False for viewer."""
        from auth.rbac import has_permission, User, Role

        viewer_user = User(id=1, username="viewer", roles=[Role(name="viewer")])
        assert has_permission(viewer_user, "channels", "write") is False

    @pytest.mark.asyncio
    async def test_only_admin_has_user_write(self, async_client):
        """has_permission(user, 'users', 'write') returns True only for admin."""
        from auth.rbac import has_permission, User, Role

        admin_user = User(id=1, username="admin", roles=[Role(name="admin")])
        operator_user = User(id=2, username="operator", roles=[Role(name="operator")])
        viewer_user = User(id=3, username="viewer", roles=[Role(name="viewer")])

        assert has_permission(admin_user, "users", "write") is True
        assert has_permission(operator_user, "users", "write") is False
        assert has_permission(viewer_user, "users", "write") is False

    @pytest.mark.asyncio
    async def test_permissions_inherited_from_groups(self, async_client):
        """Permissions are inherited from groups."""
        from auth.rbac import has_permission, User, Role, Group

        admin_group = Group(name="Admins", roles=[Role(name="admin")])
        user_with_group = User(id=1, username="groupuser", roles=[], groups=[admin_group])

        # User should inherit admin permissions from group
        assert has_permission(user_with_group, "users", "write") is True
        assert has_permission(user_with_group, "admin", "access") is True


class TestEndpointProtection:
    """Tests for endpoint-level permission enforcement."""

    @pytest.mark.asyncio
    async def test_get_endpoints_require_read_permission(self, async_client):
        """GET endpoints require 'read' permission."""
        # Login as viewer (read-only)
        await async_client.post(
            "/api/auth/login",
            json={"username": "viewer", "password": "viewerpass"},
        )

        # GET should succeed
        response = await async_client.get("/api/channels")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_post_requires_write_permission(self, async_client):
        """POST requires 'write' permission."""
        # Login as viewer (read-only)
        await async_client.post(
            "/api/auth/login",
            json={"username": "viewer", "password": "viewerpass"},
        )

        # POST should fail
        response = await async_client.post(
            "/api/channels",
            json={"name": "Test Channel"},
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_patch_requires_write_permission(self, async_client):
        """PATCH requires 'write' permission."""
        # Login as viewer (read-only)
        await async_client.post(
            "/api/auth/login",
            json={"username": "viewer", "password": "viewerpass"},
        )

        # PATCH should fail
        response = await async_client.patch(
            "/api/channels/1",
            json={"name": "Updated"},
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_delete_requires_write_permission(self, async_client):
        """DELETE requires 'write' permission."""
        # Login as viewer (read-only)
        await async_client.post(
            "/api/auth/login",
            json={"username": "viewer", "password": "viewerpass"},
        )

        # DELETE should fail
        response = await async_client.delete("/api/channels/1")
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_endpoints_require_admin_role(self, async_client):
        """'/api/admin/*' requires admin role."""
        # Login as operator (not admin)
        await async_client.post(
            "/api/auth/login",
            json={"username": "operator", "password": "operatorpass"},
        )

        # Admin endpoints should fail
        response = await async_client.get("/api/admin/users")
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_403_returns_clear_error_message(self, async_client):
        """403 returned with clear error message."""
        # Login as viewer
        await async_client.post(
            "/api/auth/login",
            json={"username": "viewer", "password": "viewerpass"},
        )

        response = await async_client.post(
            "/api/channels",
            json={"name": "Test"},
        )
        assert response.status_code == 403
        data = response.json()
        assert "permission" in data["detail"].lower() or "forbidden" in data["detail"].lower()


class TestRoleManagement:
    """Tests for custom role management."""

    @pytest.mark.asyncio
    async def test_admin_can_create_custom_roles(self, async_client):
        """Admin can create custom roles."""
        # Login as admin
        await async_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "adminpass"},
        )

        response = await async_client.post(
            "/api/admin/roles",
            json={
                "name": "custom_role",
                "description": "A custom role",
                "permissions": ["channels:read", "channels:write"],
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["role"]["name"] == "custom_role"

    @pytest.mark.asyncio
    async def test_custom_roles_can_have_subset_permissions(self, async_client):
        """Custom roles can be assigned subset of permissions."""
        # Login as admin
        await async_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "adminpass"},
        )

        # Create role with limited permissions
        response = await async_client.post(
            "/api/admin/roles",
            json={
                "name": "channel_viewer",
                "permissions": ["channels:read"],
            },
        )
        assert response.status_code == 201

        # Verify permissions
        role_response = await async_client.get("/api/admin/roles/channel_viewer")
        assert role_response.status_code == 200
        permissions = role_response.json()["role"]["permissions"]
        assert "channels:read" in permissions
        assert "channels:write" not in permissions

    @pytest.mark.asyncio
    async def test_roles_can_be_assigned_to_users(self, async_client):
        """Roles can be assigned to users."""
        # Login as admin
        await async_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "adminpass"},
        )

        # Assign role to user
        response = await async_client.post(
            "/api/admin/users/2/roles",
            json={"role": "operator"},
        )
        assert response.status_code == 200

        # Verify user has role
        user_response = await async_client.get("/api/admin/users/2")
        assert "operator" in [r["name"] for r in user_response.json()["user"]["roles"]]

    @pytest.mark.asyncio
    async def test_roles_can_be_assigned_to_groups(self, async_client):
        """Roles can be assigned to groups."""
        # Login as admin
        await async_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "adminpass"},
        )

        # Create group and assign role
        await async_client.post(
            "/api/admin/groups",
            json={"name": "operators_group"},
        )

        response = await async_client.post(
            "/api/admin/groups/operators_group/roles",
            json={"role": "operator"},
        )
        assert response.status_code == 200
