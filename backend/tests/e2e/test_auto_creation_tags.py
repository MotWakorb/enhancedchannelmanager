"""
E2E tests for auto-creation, normalization, tags, and ffmpeg endpoints.

Endpoints: /api/auto-creation/*, /api/normalization/*, /api/tags/*, /api/ffmpeg/*
"""
from tests.e2e.conftest import skip_if_not_api


class TestAutoCreationRules:
    """Tests for /api/auto-creation endpoints."""

    def test_list_rules(self, e2e_client):
        """GET /api/auto-creation/rules returns rules."""
        response = e2e_client.get("/api/auto-creation/rules")
        assert response.status_code == 200
        data = response.json()
        assert "rules" in data

    def test_schema_conditions(self, e2e_client):
        """GET /api/auto-creation/schema/conditions returns condition schema."""
        response = e2e_client.get("/api/auto-creation/schema/conditions")
        skip_if_not_api(response)
        assert response.status_code == 200

    def test_schema_actions(self, e2e_client):
        """GET /api/auto-creation/schema/actions returns action schema."""
        response = e2e_client.get("/api/auto-creation/schema/actions")
        skip_if_not_api(response)
        assert response.status_code == 200

    def test_executions(self, e2e_client):
        """GET /api/auto-creation/executions returns execution history."""
        response = e2e_client.get("/api/auto-creation/executions")
        skip_if_not_api(response)
        assert response.status_code == 200


class TestNormalization:
    """Tests for /api/normalization endpoints."""

    def test_list_groups(self, e2e_client):
        """GET /api/normalization/groups returns rule groups."""
        response = e2e_client.get("/api/normalization/groups")
        skip_if_not_api(response)
        assert response.status_code == 200

    def test_normalize_preview(self, e2e_client):
        """POST /api/normalization/test tests normalization on a name."""
        response = e2e_client.post("/api/normalization/test", json={
            "text": "ESPN HD (East)",
            "condition_type": "contains",
            "action_type": "remove",
            "pattern": "HD",
        })
        skip_if_not_api(response)
        assert response.status_code == 200


class TestTags:
    """Tests for /api/tags endpoints."""

    def test_list_tag_groups(self, e2e_client):
        """GET /api/tags/groups returns tag groups."""
        response = e2e_client.get("/api/tags/groups")
        assert response.status_code == 200
        data = response.json()
        assert "groups" in data

    def test_tag_test(self, e2e_client):
        """POST /api/tags/test tests tag matching."""
        # Get first tag group ID
        groups_resp = e2e_client.get("/api/tags/groups")
        groups = groups_resp.json().get("groups", [])
        if not groups:
            return  # No tag groups to test with
        group_id = groups[0]["id"]
        response = e2e_client.post("/api/tags/test", json={
            "text": "CNN",
            "group_id": group_id,
        })
        skip_if_not_api(response)
        assert response.status_code == 200


class TestFFmpegProfiles:
    """Tests for /api/ffmpeg endpoints (may not exist in older versions)."""

    def test_list_profiles(self, e2e_client):
        """GET /api/ffmpeg/profiles returns profiles."""
        response = e2e_client.get("/api/ffmpeg/profiles")
        skip_if_not_api(response)
        data = response.json()
        assert "profiles" in data

    def test_capabilities(self, e2e_client):
        """GET /api/ffmpeg/capabilities returns FFmpeg capabilities."""
        response = e2e_client.get("/api/ffmpeg/capabilities")
        skip_if_not_api(response)
        assert response.status_code == 200
