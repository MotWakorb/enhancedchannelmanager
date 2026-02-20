"""
Unit tests for auto-creation endpoints.

Tests: 19 endpoints covering rule CRUD, reorder, toggle, duplicate,
       pipeline execution, execution history, rollback, YAML import/export,
       validation, and schema endpoints.
Mocks: auto_creation_engine, auto_creation_schema, get_client(), get_session().
"""
import json
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, patch

from models import AutoCreationRule, AutoCreationExecution


def _create_rule(session, **overrides):
    """Helper to create an AutoCreationRule."""
    defaults = {
        "name": "Test Rule",
        "enabled": True,
        "priority": 0,
        "conditions": json.dumps([{"type": "stream_name_contains", "value": "ESPN"}]),
        "actions": json.dumps([{"type": "create_channel", "name_template": "{stream_name}"}]),
        "run_on_refresh": False,
        "stop_on_first_match": True,
        "sort_order": "asc",
        "orphan_action": "delete",
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }
    defaults.update(overrides)
    rule = AutoCreationRule(**defaults)
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return rule


def _create_execution(session, **overrides):
    """Helper to create an AutoCreationExecution."""
    defaults = {
        "rule_id": None,
        "rule_name": "Test Rule",
        "mode": "execute",
        "triggered_by": "api",
        "started_at": datetime.utcnow(),
        "status": "completed",
        "streams_evaluated": 10,
        "streams_matched": 5,
        "channels_created": 3,
    }
    defaults.update(overrides)
    execution = AutoCreationExecution(**defaults)
    session.add(execution)
    session.commit()
    session.refresh(execution)
    return execution


class TestGetAutoCreationRules:
    """Tests for GET /api/auto-creation/rules."""

    @pytest.mark.asyncio
    async def test_returns_empty(self, async_client):
        """Returns empty rules list."""
        response = await async_client.get("/api/auto-creation/rules")
        assert response.status_code == 200
        assert response.json()["rules"] == []

    @pytest.mark.asyncio
    async def test_returns_rules_ordered_by_priority(self, async_client, test_session):
        """Returns rules ordered by priority."""
        _create_rule(test_session, name="Second", priority=1)
        _create_rule(test_session, name="First", priority=0)

        response = await async_client.get("/api/auto-creation/rules")
        assert response.status_code == 200
        rules = response.json()["rules"]
        assert len(rules) == 2
        assert rules[0]["name"] == "First"
        assert rules[1]["name"] == "Second"


class TestGetAutoCreationRule:
    """Tests for GET /api/auto-creation/rules/{rule_id}."""

    @pytest.mark.asyncio
    async def test_returns_rule(self, async_client, test_session):
        """Returns a specific rule."""
        rule = _create_rule(test_session, name="Sports Rule")

        response = await async_client.get(f"/api/auto-creation/rules/{rule.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Sports Rule"
        assert data["conditions"] == [{"type": "stream_name_contains", "value": "ESPN"}]

    @pytest.mark.asyncio
    async def test_returns_404(self, async_client):
        """Returns 404 for nonexistent rule."""
        response = await async_client.get("/api/auto-creation/rules/99999")
        assert response.status_code == 404


class TestCreateAutoCreationRule:
    """Tests for POST /api/auto-creation/rules."""

    @pytest.mark.asyncio
    async def test_creates_rule(self, async_client):
        """Creates a new auto-creation rule."""
        with patch("auto_creation_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.auto_creation.journal"):
            response = await async_client.post("/api/auto-creation/rules", json={
                "name": "New Rule",
                "conditions": [{"type": "stream_name_contains", "value": "CNN"}],
                "actions": [{"type": "create_channel", "name_template": "{stream_name}"}],
            })

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "New Rule"
        assert data["enabled"] is True

    @pytest.mark.asyncio
    async def test_rejects_invalid_rule(self, async_client):
        """Returns 400 for invalid rule configuration."""
        with patch("auto_creation_schema.validate_rule", return_value={
            "valid": False,
            "errors": ["Actions must not be empty"],
        }):
            response = await async_client.post("/api/auto-creation/rules", json={
                "name": "Bad Rule",
                "conditions": [],
                "actions": [],
            })

        assert response.status_code == 400


class TestUpdateAutoCreationRule:
    """Tests for PUT /api/auto-creation/rules/{rule_id}."""

    @pytest.mark.asyncio
    async def test_updates_rule(self, async_client, test_session):
        """Updates an auto-creation rule."""
        rule = _create_rule(test_session, name="Old Name")

        with patch("auto_creation_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.auto_creation.journal"):
            response = await async_client.put(
                f"/api/auto-creation/rules/{rule.id}",
                json={"name": "New Name"},
            )

        assert response.status_code == 200
        assert response.json()["name"] == "New Name"

    @pytest.mark.asyncio
    async def test_returns_404(self, async_client):
        """Returns 404 for nonexistent rule."""
        with patch("auto_creation_schema.validate_rule", return_value={"valid": True, "errors": []}):
            response = await async_client.put(
                "/api/auto-creation/rules/99999",
                json={"name": "Ghost"},
            )

        assert response.status_code == 404


class TestDeleteAutoCreationRule:
    """Tests for DELETE /api/auto-creation/rules/{rule_id}."""

    @pytest.mark.asyncio
    async def test_deletes_rule(self, async_client, test_session):
        """Deletes an auto-creation rule."""
        rule = _create_rule(test_session)
        rule_id = rule.id

        with patch("routers.auto_creation.journal"):
            response = await async_client.delete(f"/api/auto-creation/rules/{rule_id}")

        assert response.status_code == 200
        assert response.json()["status"] == "deleted"
        assert test_session.query(AutoCreationRule).filter_by(id=rule_id).first() is None

    @pytest.mark.asyncio
    async def test_returns_404(self, async_client):
        """Returns 404 for nonexistent rule."""
        response = await async_client.delete("/api/auto-creation/rules/99999")
        assert response.status_code == 404


class TestReorderAutoCreationRules:
    """Tests for POST /api/auto-creation/rules/reorder."""

    @pytest.mark.asyncio
    async def test_reorders_rules(self, async_client, test_session):
        """Reorders rules by setting new priorities."""
        r1 = _create_rule(test_session, name="A", priority=0)
        r2 = _create_rule(test_session, name="B", priority=1)

        response = await async_client.post(
            "/api/auto-creation/rules/reorder",
            json=[r2.id, r1.id],
        )
        assert response.status_code == 200

        test_session.expire_all()
        assert test_session.query(AutoCreationRule).get(r2.id).priority == 0
        assert test_session.query(AutoCreationRule).get(r1.id).priority == 1


class TestToggleAutoCreationRule:
    """Tests for POST /api/auto-creation/rules/{rule_id}/toggle."""

    @pytest.mark.asyncio
    async def test_toggles_enabled(self, async_client, test_session):
        """Toggles rule enabled state."""
        rule = _create_rule(test_session, enabled=True)

        response = await async_client.post(f"/api/auto-creation/rules/{rule.id}/toggle")
        assert response.status_code == 200
        assert response.json()["enabled"] is False

        response = await async_client.post(f"/api/auto-creation/rules/{rule.id}/toggle")
        assert response.status_code == 200
        assert response.json()["enabled"] is True

    @pytest.mark.asyncio
    async def test_returns_404(self, async_client):
        """Returns 404 for nonexistent rule."""
        response = await async_client.post("/api/auto-creation/rules/99999/toggle")
        assert response.status_code == 404


class TestDuplicateAutoCreationRule:
    """Tests for POST /api/auto-creation/rules/{rule_id}/duplicate."""

    @pytest.mark.asyncio
    async def test_duplicates_rule(self, async_client, test_session):
        """Duplicates a rule with 'Copy' suffix and disabled."""
        rule = _create_rule(test_session, name="Original", priority=5, enabled=True)

        response = await async_client.post(f"/api/auto-creation/rules/{rule.id}/duplicate")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Original (Copy)"
        assert data["enabled"] is False
        assert data["priority"] == 6

    @pytest.mark.asyncio
    async def test_returns_404(self, async_client):
        """Returns 404 for nonexistent rule."""
        response = await async_client.post("/api/auto-creation/rules/99999/duplicate")
        assert response.status_code == 404


class TestRunAutoCreationPipeline:
    """Tests for POST /api/auto-creation/run."""

    @pytest.mark.asyncio
    async def test_runs_pipeline(self, async_client):
        """Runs the auto-creation pipeline."""
        mock_engine = AsyncMock()
        mock_engine.run_pipeline.return_value = {
            "status": "completed",
            "streams_matched": 10,
            "channels_created": 5,
        }

        with patch("auto_creation_engine.get_auto_creation_engine", return_value=mock_engine):
            response = await async_client.post("/api/auto-creation/run", json={
                "dry_run": False,
            })

        assert response.status_code == 200
        assert response.json()["channels_created"] == 5
        mock_engine.run_pipeline.assert_called_once_with(
            dry_run=False, triggered_by="api", m3u_account_ids=None, rule_ids=None,
        )

    @pytest.mark.asyncio
    async def test_dry_run(self, async_client):
        """Runs pipeline in dry-run mode."""
        mock_engine = AsyncMock()
        mock_engine.run_pipeline.return_value = {"status": "dry_run"}

        with patch("auto_creation_engine.get_auto_creation_engine", return_value=mock_engine):
            response = await async_client.post("/api/auto-creation/run", json={
                "dry_run": True,
            })

        assert response.status_code == 200
        mock_engine.run_pipeline.assert_called_once_with(
            dry_run=True, triggered_by="api", m3u_account_ids=None, rule_ids=None,
        )


class TestRunAutoCreationRule:
    """Tests for POST /api/auto-creation/rules/{rule_id}/run."""

    @pytest.mark.asyncio
    async def test_runs_single_rule(self, async_client):
        """Runs a specific auto-creation rule."""
        mock_engine = AsyncMock()
        mock_engine.run_rule.return_value = {"status": "completed"}

        with patch("auto_creation_engine.get_auto_creation_engine", return_value=mock_engine):
            response = await async_client.post("/api/auto-creation/rules/42/run")

        assert response.status_code == 200
        mock_engine.run_rule.assert_called_once_with(
            rule_id=42, dry_run=False, triggered_by="api",
        )


class TestGetExecutions:
    """Tests for GET /api/auto-creation/executions."""

    @pytest.mark.asyncio
    async def test_returns_executions(self, async_client, test_session):
        """Returns execution history."""
        _create_execution(test_session, rule_name="Rule A")
        _create_execution(test_session, rule_name="Rule B")

        response = await async_client.get("/api/auto-creation/executions")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert len(data["executions"]) == 2

    @pytest.mark.asyncio
    async def test_filters_by_status(self, async_client, test_session):
        """Filters executions by status."""
        _create_execution(test_session, status="completed")
        _create_execution(test_session, status="failed")

        response = await async_client.get(
            "/api/auto-creation/executions",
            params={"status": "failed"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["executions"][0]["status"] == "failed"

    @pytest.mark.asyncio
    async def test_pagination(self, async_client, test_session):
        """Pagination works with limit and offset."""
        for i in range(5):
            _create_execution(test_session, rule_name=f"Rule {i}")

        response = await async_client.get(
            "/api/auto-creation/executions",
            params={"limit": 2, "offset": 2},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 5
        assert len(data["executions"]) == 2


class TestGetExecution:
    """Tests for GET /api/auto-creation/executions/{execution_id}."""

    @pytest.mark.asyncio
    async def test_returns_execution(self, async_client, test_session):
        """Returns a specific execution with conflicts."""
        execution = _create_execution(test_session)

        response = await async_client.get(f"/api/auto-creation/executions/{execution.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert "conflicts" in data

    @pytest.mark.asyncio
    async def test_returns_404(self, async_client):
        """Returns 404 for nonexistent execution."""
        response = await async_client.get("/api/auto-creation/executions/99999")
        assert response.status_code == 404


class TestRollbackExecution:
    """Tests for POST /api/auto-creation/executions/{execution_id}/rollback."""

    @pytest.mark.asyncio
    async def test_rolls_back_execution(self, async_client):
        """Rolls back an execution."""
        mock_engine = AsyncMock()
        mock_engine.rollback_execution.return_value = {
            "success": True,
            "rule_name": "Sports Rule",
            "entities_removed": 3,
            "entities_restored": 0,
        }

        with patch("auto_creation_engine.get_auto_creation_engine", return_value=mock_engine), \
             patch("routers.auto_creation.journal"):
            response = await async_client.post("/api/auto-creation/executions/1/rollback")

        assert response.status_code == 200
        assert response.json()["success"] is True

    @pytest.mark.asyncio
    async def test_returns_400_on_failure(self, async_client):
        """Returns 400 when rollback fails."""
        mock_engine = AsyncMock()
        mock_engine.rollback_execution.return_value = {
            "success": False,
            "error": "Execution already rolled back",
        }

        with patch("auto_creation_engine.get_auto_creation_engine", return_value=mock_engine):
            response = await async_client.post("/api/auto-creation/executions/1/rollback")

        assert response.status_code == 400


class TestExportYAML:
    """Tests for GET /api/auto-creation/export/yaml."""

    @pytest.mark.asyncio
    async def test_exports_rules(self, async_client, test_session):
        """Exports rules as YAML."""
        _create_rule(test_session, name="Export Me")

        mock_client = AsyncMock()
        mock_client.get_channel_groups.return_value = []
        mock_client.get_m3u_accounts.return_value = []

        with patch("routers.auto_creation.get_client", return_value=mock_client):
            response = await async_client.get("/api/auto-creation/export/yaml")

        assert response.status_code == 200
        assert "Export Me" in response.text


class TestImportYAML:
    """Tests for POST /api/auto-creation/import/yaml."""

    @pytest.mark.asyncio
    async def test_rejects_invalid_yaml(self, async_client):
        """Returns 400 for invalid YAML."""
        mock_client = AsyncMock()
        mock_client.get_channel_groups.return_value = []
        mock_client.get_m3u_accounts.return_value = []

        with patch("routers.auto_creation.get_client", return_value=mock_client):
            response = await async_client.post("/api/auto-creation/import/yaml", json={
                "yaml_content": "{{invalid yaml",
            })

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_rejects_empty_yaml(self, async_client):
        """Returns 400 for YAML without rules."""
        mock_client = AsyncMock()
        mock_client.get_channel_groups.return_value = []
        mock_client.get_m3u_accounts.return_value = []

        with patch("routers.auto_creation.get_client", return_value=mock_client):
            response = await async_client.post("/api/auto-creation/import/yaml", json={
                "yaml_content": "foo: bar",
            })

        assert response.status_code == 400


class TestValidateRule:
    """Tests for POST /api/auto-creation/validate."""

    @pytest.mark.asyncio
    async def test_validates_valid_rule(self, async_client):
        """Returns valid for good conditions/actions."""
        with patch("auto_creation_schema.validate_rule", return_value={
            "valid": True, "errors": [],
        }):
            response = await async_client.post("/api/auto-creation/validate", json={
                "conditions": [{"type": "always"}],
                "actions": [{"type": "create_channel"}],
            })

        assert response.status_code == 200
        assert response.json()["valid"] is True

    @pytest.mark.asyncio
    async def test_validates_invalid_rule(self, async_client):
        """Returns invalid for bad conditions/actions."""
        with patch("auto_creation_schema.validate_rule", return_value={
            "valid": False, "errors": ["Missing action type"],
        }):
            response = await async_client.post("/api/auto-creation/validate", json={
                "conditions": [],
                "actions": [],
            })

        assert response.status_code == 200
        assert response.json()["valid"] is False


class TestGetConditionSchema:
    """Tests for GET /api/auto-creation/schema/conditions."""

    @pytest.mark.asyncio
    async def test_returns_conditions(self, async_client):
        """Returns available condition types."""
        response = await async_client.get("/api/auto-creation/schema/conditions")
        assert response.status_code == 200
        data = response.json()
        assert "conditions" in data
        types = [c["type"] for c in data["conditions"]]
        assert "stream_name_contains" in types
        assert "always" in types


class TestGetActionSchema:
    """Tests for GET /api/auto-creation/schema/actions."""

    @pytest.mark.asyncio
    async def test_returns_actions(self, async_client):
        """Returns available action types."""
        response = await async_client.get("/api/auto-creation/schema/actions")
        assert response.status_code == 200
        data = response.json()
        assert "actions" in data
        types = [a["type"] for a in data["actions"]]
        assert "create_channel" in types
        assert "skip" in types


class TestGetTemplateVariables:
    """Tests for GET /api/auto-creation/schema/template-variables."""

    @pytest.mark.asyncio
    async def test_returns_variables(self, async_client):
        """Returns available template variables."""
        response = await async_client.get("/api/auto-creation/schema/template-variables")
        assert response.status_code == 200
        data = response.json()
        assert "variables" in data
        names = [v["name"] for v in data["variables"]]
        assert "{stream_name}" in names
        assert "{quality}" in names
