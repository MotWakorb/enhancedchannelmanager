"""
Integration tests for the auto-creation API endpoints.

Tests the full API workflow for creating, updating, and managing auto-creation rules.
"""
import pytest
import json
from unittest.mock import patch, MagicMock, AsyncMock

from fastapi.testclient import TestClient


@pytest.fixture
def mock_db_session():
    """Mock database session."""
    with patch("main.get_session") as mock:
        session = MagicMock()
        mock.return_value = session
        yield session


@pytest.fixture
def test_client():
    """Create test client."""
    from main import app
    return TestClient(app)


class TestAutoCreationRulesAPI:
    """Tests for auto-creation rules CRUD endpoints."""

    def test_get_rules_empty(self, test_client, mock_db_session):
        """Get rules returns empty list when no rules exist."""
        mock_db_session.query.return_value.order_by.return_value.all.return_value = []

        response = test_client.get("/api/auto-creation/rules")

        assert response.status_code == 200
        assert response.json() == {"rules": []}

    def test_get_rules_with_data(self, test_client, mock_db_session):
        """Get rules returns list of rules."""
        mock_rule = MagicMock()
        mock_rule.to_dict.return_value = {
            "id": 1,
            "name": "Test Rule",
            "enabled": True,
            "priority": 0,
            "conditions": [{"type": "always"}],
            "actions": [{"type": "skip"}]
        }
        mock_db_session.query.return_value.order_by.return_value.all.return_value = [mock_rule]

        response = test_client.get("/api/auto-creation/rules")

        assert response.status_code == 200
        data = response.json()
        assert len(data["rules"]) == 1
        assert data["rules"][0]["name"] == "Test Rule"

    def test_get_rule_by_id_found(self, test_client, mock_db_session):
        """Get single rule by ID when it exists."""
        mock_rule = MagicMock()
        mock_rule.to_dict.return_value = {
            "id": 1,
            "name": "Test Rule",
            "enabled": True
        }
        mock_db_session.query.return_value.filter.return_value.first.return_value = mock_rule

        response = test_client.get("/api/auto-creation/rules/1")

        assert response.status_code == 200
        assert response.json()["name"] == "Test Rule"

    def test_get_rule_by_id_not_found(self, test_client, mock_db_session):
        """Get single rule returns 404 when not found."""
        mock_db_session.query.return_value.filter.return_value.first.return_value = None

        response = test_client.get("/api/auto-creation/rules/999")

        assert response.status_code == 404

    def test_create_rule_valid(self, test_client, mock_db_session):
        """Create rule with valid data."""
        mock_rule = MagicMock()
        mock_rule.id = 1
        mock_rule.name = "New Rule"
        mock_rule.to_dict.return_value = {
            "id": 1,
            "name": "New Rule",
            "enabled": True,
            "conditions": [{"type": "always"}],
            "actions": [{"type": "skip"}]
        }

        # Mock the rule creation
        def add_rule(rule):
            pass

        mock_db_session.add = add_rule
        mock_db_session.commit = MagicMock()
        mock_db_session.refresh = MagicMock(side_effect=lambda x: setattr(x, 'id', 1))

        with patch("main.journal.log_entry"):
            response = test_client.post(
                "/api/auto-creation/rules",
                json={
                    "name": "New Rule",
                    "conditions": [{"type": "always"}],
                    "actions": [{"type": "skip"}]
                }
            )

        assert response.status_code == 200

    def test_create_rule_invalid_conditions(self, test_client, mock_db_session):
        """Create rule fails with invalid conditions."""
        response = test_client.post(
            "/api/auto-creation/rules",
            json={
                "name": "Bad Rule",
                "conditions": [{"type": "stream_name_matches", "value": "[invalid("}],
                "actions": [{"type": "skip"}]
            }
        )

        assert response.status_code == 400
        assert "Invalid" in str(response.json()["detail"])

    def test_create_rule_invalid_actions(self, test_client, mock_db_session):
        """Create rule fails with invalid actions."""
        response = test_client.post(
            "/api/auto-creation/rules",
            json={
                "name": "Bad Rule",
                "conditions": [{"type": "always"}],
                "actions": [{"type": "merge_streams", "target": "invalid"}]
            }
        )

        assert response.status_code == 400

    def test_delete_rule(self, test_client, mock_db_session):
        """Delete rule successfully."""
        mock_rule = MagicMock()
        mock_rule.name = "Test Rule"
        mock_db_session.query.return_value.filter.return_value.first.return_value = mock_rule

        with patch("main.journal.log_entry"):
            response = test_client.delete("/api/auto-creation/rules/1")

        assert response.status_code == 200
        assert response.json()["status"] == "deleted"

    def test_delete_rule_not_found(self, test_client, mock_db_session):
        """Delete rule returns 404 when not found."""
        mock_db_session.query.return_value.filter.return_value.first.return_value = None

        response = test_client.delete("/api/auto-creation/rules/999")

        assert response.status_code == 404


class TestAutoCreationSchemaAPI:
    """Tests for schema discovery endpoints."""

    def test_get_condition_schema(self, test_client):
        """Get condition schema returns all condition types."""
        response = test_client.get("/api/auto-creation/schema/conditions")

        assert response.status_code == 200
        data = response.json()
        assert "conditions" in data
        assert len(data["conditions"]) > 0

        # Check some expected conditions
        types = [c["type"] for c in data["conditions"]]
        assert "stream_name_contains" in types
        assert "quality_min" in types
        assert "has_channel" in types
        assert "and" in types
        assert "or" in types

    def test_get_action_schema(self, test_client):
        """Get action schema returns all action types."""
        response = test_client.get("/api/auto-creation/schema/actions")

        assert response.status_code == 200
        data = response.json()
        assert "actions" in data
        assert len(data["actions"]) > 0

        # Check some expected actions
        types = [a["type"] for a in data["actions"]]
        assert "create_channel" in types
        assert "create_group" in types
        assert "merge_streams" in types
        assert "skip" in types

    def test_get_template_variables(self, test_client):
        """Get template variables returns all variables."""
        response = test_client.get("/api/auto-creation/schema/template-variables")

        assert response.status_code == 200
        data = response.json()
        assert "variables" in data

        # Check some expected variables
        names = [v["name"] for v in data["variables"]]
        assert "{stream_name}" in names
        assert "{stream_group}" in names
        assert "{quality}" in names


class TestAutoCreationValidationAPI:
    """Tests for validation endpoint."""

    def test_validate_valid_rule(self, test_client):
        """Validation passes for valid rule."""
        response = test_client.post(
            "/api/auto-creation/validate",
            json={
                "conditions": [{"type": "always"}],
                "actions": [{"type": "skip"}]
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True
        assert len(data["errors"]) == 0

    def test_validate_invalid_condition(self, test_client):
        """Validation fails for invalid condition."""
        response = test_client.post(
            "/api/auto-creation/validate",
            json={
                "conditions": [{"type": "stream_name_matches", "value": "[invalid("}],
                "actions": [{"type": "skip"}]
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False
        assert len(data["errors"]) > 0

    def test_validate_empty_conditions(self, test_client):
        """Validation fails for empty conditions."""
        response = test_client.post(
            "/api/auto-creation/validate",
            json={
                "conditions": [],
                "actions": [{"type": "skip"}]
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False


class TestAutoCreationExecutionsAPI:
    """Tests for execution history endpoints."""

    def test_get_executions_empty(self, test_client, mock_db_session):
        """Get executions returns empty list when none exist."""
        mock_query = MagicMock()
        mock_query.count.return_value = 0
        mock_query.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []
        mock_db_session.query.return_value = mock_query

        response = test_client.get("/api/auto-creation/executions")

        assert response.status_code == 200
        data = response.json()
        assert data["executions"] == []
        assert data["total"] == 0

    def test_get_executions_with_filters(self, test_client, mock_db_session):
        """Get executions with status filter."""
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.count.return_value = 0
        mock_query.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []
        mock_db_session.query.return_value = mock_query

        response = test_client.get("/api/auto-creation/executions?status=completed&limit=10")

        assert response.status_code == 200

    def test_get_execution_not_found(self, test_client, mock_db_session):
        """Get execution returns 404 when not found."""
        mock_db_session.query.return_value.filter.return_value.first.return_value = None

        response = test_client.get("/api/auto-creation/executions/999")

        assert response.status_code == 404


class TestAutoCreationYAMLAPI:
    """Tests for YAML import/export endpoints."""

    def test_export_yaml(self, test_client, mock_db_session):
        """Export rules as YAML."""
        mock_rule = MagicMock()
        mock_rule.name = "Test Rule"
        mock_rule.description = None
        mock_rule.enabled = True
        mock_rule.priority = 0
        mock_rule.m3u_account_id = None
        mock_rule.target_group_id = None
        mock_rule.run_on_refresh = False
        mock_rule.stop_on_first_match = True
        mock_rule.get_conditions.return_value = [{"type": "always"}]
        mock_rule.get_actions.return_value = [{"type": "skip"}]
        mock_db_session.query.return_value.order_by.return_value.all.return_value = [mock_rule]

        response = test_client.get("/api/auto-creation/export/yaml")

        assert response.status_code == 200
        assert response.headers["content-type"] == "text/yaml; charset=utf-8"
        assert "Test Rule" in response.text

    def test_import_yaml_valid(self, test_client, mock_db_session):
        """Import valid YAML rules."""
        yaml_content = """
version: 1
rules:
  - name: Imported Rule
    enabled: true
    priority: 0
    conditions:
      - type: always
    actions:
      - type: skip
"""
        mock_db_session.query.return_value.filter.return_value.first.return_value = None

        with patch("main.journal.log_entry"):
            response = test_client.post(
                "/api/auto-creation/import/yaml",
                json={
                    "yaml_content": yaml_content,
                    "overwrite": False
                }
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert len(data["imported"]) == 1

    def test_import_yaml_invalid(self, test_client, mock_db_session):
        """Import invalid YAML returns error."""
        response = test_client.post(
            "/api/auto-creation/import/yaml",
            json={
                "yaml_content": "not: valid: yaml: [",
                "overwrite": False
            }
        )

        assert response.status_code == 400

    def test_import_yaml_missing_rules(self, test_client, mock_db_session):
        """Import YAML without rules array returns error."""
        response = test_client.post(
            "/api/auto-creation/import/yaml",
            json={
                "yaml_content": "version: 1\nno_rules: true",
                "overwrite": False
            }
        )

        assert response.status_code == 400
