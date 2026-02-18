"""
Unit tests for normalization endpoints.

Tests: 18 endpoints for normalization rule groups, rules, testing, and migration.
Uses async_client fixture which patches database session.
"""
import pytest
from unittest.mock import MagicMock, patch

from models import NormalizationRuleGroup, NormalizationRule


def _create_group(session, **overrides):
    """Helper to create a NormalizationRuleGroup."""
    defaults = {"name": "Test Group", "enabled": True, "priority": 0, "is_builtin": False}
    defaults.update(overrides)
    group = NormalizationRuleGroup(**defaults)
    session.add(group)
    session.commit()
    session.refresh(group)
    return group


def _create_rule(session, group_id, **overrides):
    """Helper to create a NormalizationRule."""
    defaults = {
        "group_id": group_id,
        "name": "Test Rule",
        "enabled": True,
        "priority": 0,
        "condition_type": "contains",
        "condition_value": "HD",
        "case_sensitive": False,
        "action_type": "remove",
        "action_value": "",
        "is_builtin": False,
    }
    defaults.update(overrides)
    rule = NormalizationRule(**defaults)
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return rule


class TestGetAllRules:
    """Tests for GET /api/normalization/rules."""

    @pytest.mark.asyncio
    async def test_returns_rules_by_group(self, async_client):
        """Returns all rules organized by group."""
        mock_engine = MagicMock()
        mock_engine.get_all_rules.return_value = [
            {"id": 1, "name": "Group A", "rules": []}
        ]

        with patch("normalization_engine.get_normalization_engine", return_value=mock_engine):
            response = await async_client.get("/api/normalization/rules")

        assert response.status_code == 200
        data = response.json()
        assert "groups" in data


class TestGetGroups:
    """Tests for GET /api/normalization/groups."""

    @pytest.mark.asyncio
    async def test_returns_empty(self, async_client):
        """Returns empty groups list."""
        response = await async_client.get("/api/normalization/groups")
        assert response.status_code == 200
        assert response.json()["groups"] == []

    @pytest.mark.asyncio
    async def test_returns_groups_ordered_by_priority(self, async_client, test_session):
        """Returns groups ordered by priority."""
        _create_group(test_session, name="Second", priority=1)
        _create_group(test_session, name="First", priority=0)

        response = await async_client.get("/api/normalization/groups")
        assert response.status_code == 200
        groups = response.json()["groups"]
        assert len(groups) == 2
        assert groups[0]["name"] == "First"
        assert groups[1]["name"] == "Second"


class TestCreateGroup:
    """Tests for POST /api/normalization/groups."""

    @pytest.mark.asyncio
    async def test_creates_group(self, async_client):
        """Creates a new normalization group."""
        response = await async_client.post("/api/normalization/groups", json={
            "name": "Quality Tags",
            "description": "Remove quality suffixes",
            "enabled": True,
            "priority": 5,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Quality Tags"
        assert data["priority"] == 5
        assert data["is_builtin"] is False


class TestGetGroup:
    """Tests for GET /api/normalization/groups/{group_id}."""

    @pytest.mark.asyncio
    async def test_returns_group_with_rules(self, async_client, test_session):
        """Returns group including its rules."""
        group = _create_group(test_session, name="Quality")
        _create_rule(test_session, group.id, name="Remove HD")

        response = await async_client.get(f"/api/normalization/groups/{group.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Quality"
        assert len(data["rules"]) == 1
        assert data["rules"][0]["name"] == "Remove HD"

    @pytest.mark.asyncio
    async def test_returns_404(self, async_client):
        """Returns 404 for nonexistent group."""
        response = await async_client.get("/api/normalization/groups/99999")
        assert response.status_code == 404


class TestUpdateGroup:
    """Tests for PATCH /api/normalization/groups/{group_id}."""

    @pytest.mark.asyncio
    async def test_updates_fields(self, async_client, test_session):
        """Updates group fields."""
        group = _create_group(test_session, name="Old", enabled=True)

        response = await async_client.patch(
            f"/api/normalization/groups/{group.id}",
            json={"name": "New", "enabled": False},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "New"
        assert data["enabled"] is False

    @pytest.mark.asyncio
    async def test_returns_404(self, async_client):
        """Returns 404 for nonexistent group."""
        response = await async_client.patch(
            "/api/normalization/groups/99999",
            json={"name": "Ghost"},
        )
        assert response.status_code == 404


class TestDeleteGroup:
    """Tests for DELETE /api/normalization/groups/{group_id}."""

    @pytest.mark.asyncio
    async def test_deletes_group_and_rules(self, async_client, test_session):
        """Deletes group and all its rules."""
        group = _create_group(test_session)
        _create_rule(test_session, group.id)
        group_id = group.id

        response = await async_client.delete(f"/api/normalization/groups/{group_id}")
        assert response.status_code == 200
        assert response.json()["status"] == "deleted"

        assert test_session.query(NormalizationRuleGroup).filter(
            NormalizationRuleGroup.id == group_id
        ).first() is None
        assert test_session.query(NormalizationRule).filter(
            NormalizationRule.group_id == group_id
        ).count() == 0

    @pytest.mark.asyncio
    async def test_returns_404(self, async_client):
        """Returns 404 for nonexistent group."""
        response = await async_client.delete("/api/normalization/groups/99999")
        assert response.status_code == 404


class TestReorderGroups:
    """Tests for POST /api/normalization/groups/reorder."""

    @pytest.mark.asyncio
    async def test_reorders_groups(self, async_client, test_session):
        """Reorders groups by setting new priorities."""
        g1 = _create_group(test_session, name="A", priority=0)
        g2 = _create_group(test_session, name="B", priority=1)

        response = await async_client.post("/api/normalization/groups/reorder", json={
            "group_ids": [g2.id, g1.id],
        })
        assert response.status_code == 200

        test_session.refresh(g1)
        test_session.refresh(g2)
        assert g2.priority == 0  # now first
        assert g1.priority == 1  # now second


class TestGetRule:
    """Tests for GET /api/normalization/rules/{rule_id}."""

    @pytest.mark.asyncio
    async def test_returns_rule(self, async_client, test_session):
        """Returns a specific rule."""
        group = _create_group(test_session)
        rule = _create_rule(test_session, group.id, name="Remove HD")

        response = await async_client.get(f"/api/normalization/rules/{rule.id}")
        assert response.status_code == 200
        assert response.json()["name"] == "Remove HD"

    @pytest.mark.asyncio
    async def test_returns_404(self, async_client):
        """Returns 404 for nonexistent rule."""
        response = await async_client.get("/api/normalization/rules/99999")
        assert response.status_code == 404


class TestCreateRule:
    """Tests for POST /api/normalization/rules."""

    @pytest.mark.asyncio
    async def test_creates_rule(self, async_client, test_session):
        """Creates a new rule in a group."""
        group = _create_group(test_session)

        response = await async_client.post("/api/normalization/rules", json={
            "group_id": group.id,
            "name": "Remove HD",
            "condition_type": "contains",
            "condition_value": "HD",
            "action_type": "remove",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Remove HD"
        assert data["group_id"] == group.id

    @pytest.mark.asyncio
    async def test_returns_404_for_nonexistent_group(self, async_client):
        """Returns 404 when group doesn't exist."""
        response = await async_client.post("/api/normalization/rules", json={
            "group_id": 99999,
            "name": "Orphan Rule",
            "condition_type": "contains",
            "condition_value": "X",
            "action_type": "remove",
        })
        assert response.status_code == 404


class TestUpdateRule:
    """Tests for PATCH /api/normalization/rules/{rule_id}."""

    @pytest.mark.asyncio
    async def test_updates_rule(self, async_client, test_session):
        """Updates rule fields."""
        group = _create_group(test_session)
        rule = _create_rule(test_session, group.id, name="Old", enabled=True)

        response = await async_client.patch(
            f"/api/normalization/rules/{rule.id}",
            json={"name": "New", "enabled": False},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "New"
        assert data["enabled"] is False

    @pytest.mark.asyncio
    async def test_returns_404(self, async_client):
        """Returns 404 for nonexistent rule."""
        response = await async_client.patch(
            "/api/normalization/rules/99999",
            json={"name": "Ghost"},
        )
        assert response.status_code == 404


class TestDeleteRule:
    """Tests for DELETE /api/normalization/rules/{rule_id}."""

    @pytest.mark.asyncio
    async def test_deletes_rule(self, async_client, test_session):
        """Deletes a rule."""
        group = _create_group(test_session)
        rule = _create_rule(test_session, group.id)
        rule_id = rule.id

        response = await async_client.delete(f"/api/normalization/rules/{rule_id}")
        assert response.status_code == 200
        assert response.json()["status"] == "deleted"

        assert test_session.query(NormalizationRule).filter(
            NormalizationRule.id == rule_id
        ).first() is None

    @pytest.mark.asyncio
    async def test_returns_404(self, async_client):
        """Returns 404 for nonexistent rule."""
        response = await async_client.delete("/api/normalization/rules/99999")
        assert response.status_code == 404


class TestReorderRules:
    """Tests for POST /api/normalization/groups/{group_id}/rules/reorder."""

    @pytest.mark.asyncio
    async def test_reorders_rules(self, async_client, test_session):
        """Reorders rules within a group."""
        group = _create_group(test_session)
        r1 = _create_rule(test_session, group.id, name="A", priority=0)
        r2 = _create_rule(test_session, group.id, name="B", priority=1)

        response = await async_client.post(
            f"/api/normalization/groups/{group.id}/rules/reorder",
            json={"rule_ids": [r2.id, r1.id]},
        )
        assert response.status_code == 200

        test_session.refresh(r1)
        test_session.refresh(r2)
        assert r2.priority == 0
        assert r1.priority == 1


class TestTestRule:
    """Tests for POST /api/normalization/test."""

    @pytest.mark.asyncio
    async def test_tests_rule_against_text(self, async_client):
        """Tests a rule configuration against sample text."""
        mock_engine = MagicMock()
        mock_engine.test_rule.return_value = {
            "matched": True,
            "original": "ESPN HD",
            "result": "ESPN",
        }

        with patch("normalization_engine.get_normalization_engine", return_value=mock_engine):
            response = await async_client.post("/api/normalization/test", json={
                "text": "ESPN HD",
                "condition_type": "contains",
                "condition_value": "HD",
                "action_type": "remove",
            })

        assert response.status_code == 200
        data = response.json()
        assert data["matched"] is True


class TestTestBatch:
    """Tests for POST /api/normalization/test-batch."""

    @pytest.mark.asyncio
    async def test_tests_batch(self, async_client):
        """Tests all rules against multiple texts."""
        mock_result = MagicMock()
        mock_result.original = "ESPN HD"
        mock_result.normalized = "ESPN"
        mock_result.rules_applied = 1
        mock_result.transformations = [(1, "ESPN HD", "ESPN")]

        mock_engine = MagicMock()
        mock_engine.test_rules_batch.return_value = [mock_result]

        with patch("normalization_engine.get_normalization_engine", return_value=mock_engine):
            response = await async_client.post("/api/normalization/test-batch", json={
                "texts": ["ESPN HD"],
            })

        assert response.status_code == 200
        data = response.json()
        assert len(data["results"]) == 1
        assert data["results"][0]["original"] == "ESPN HD"
        assert data["results"][0]["normalized"] == "ESPN"


class TestNormalize:
    """Tests for POST /api/normalization/normalize."""

    @pytest.mark.asyncio
    async def test_normalizes_texts(self, async_client):
        """Normalizes texts using all enabled rules."""
        mock_result = MagicMock()
        mock_result.original = "BBC HD"
        mock_result.normalized = "BBC"

        mock_engine = MagicMock()
        mock_engine.test_rules_batch.return_value = [mock_result]

        with patch("normalization_engine.get_normalization_engine", return_value=mock_engine):
            response = await async_client.post("/api/normalization/normalize", json={
                "texts": ["BBC HD"],
            })

        assert response.status_code == 200
        data = response.json()
        assert data["results"][0]["original"] == "BBC HD"
        assert data["results"][0]["normalized"] == "BBC"


class TestRuleStats:
    """Tests for GET /api/normalization/rule-stats."""

    @pytest.mark.asyncio
    async def test_returns_stats(self, async_client, test_session):
        """Returns rule match stats against streams."""
        from unittest.mock import AsyncMock

        group = _create_group(test_session, name="Quality")
        _create_rule(test_session, group.id, name="HD rule")

        mock_client = AsyncMock()
        mock_client.get_streams.return_value = {"results": [
            {"name": "ESPN HD"}, {"name": "CNN"},
        ]}

        mock_engine = MagicMock()
        mock_match_yes = MagicMock()
        mock_match_yes.matched = True
        mock_match_no = MagicMock()
        mock_match_no.matched = False
        mock_engine._match_condition.side_effect = [mock_match_yes, mock_match_no]

        with patch("routers.normalization.get_client", return_value=mock_client), \
             patch("normalization_engine.get_normalization_engine", return_value=mock_engine):
            response = await async_client.get("/api/normalization/rule-stats")

        assert response.status_code == 200
        data = response.json()
        assert "rule_stats" in data
        assert data["total_streams_tested"] == 2


class TestMigrationStatus:
    """Tests for GET /api/normalization/migration/status."""

    @pytest.mark.asyncio
    async def test_returns_status(self, async_client):
        """Returns migration status."""
        with patch("normalization_migration.get_migration_status", return_value={
            "migrated": True, "groups_count": 3, "rules_count": 15,
        }):
            response = await async_client.get("/api/normalization/migration/status")

        assert response.status_code == 200
        data = response.json()
        assert data["migrated"] is True


class TestRunMigration:
    """Tests for POST /api/normalization/migration/run."""

    @pytest.mark.asyncio
    async def test_runs_migration(self, async_client):
        """Creates demo normalization rules."""
        with patch("normalization_migration.create_demo_rules", return_value={
            "created_groups": 3, "created_rules": 15,
        }), patch("routers.normalization.get_settings") as mock_settings:
            mock_settings.return_value.custom_normalization_tags = []
            response = await async_client.post("/api/normalization/migration/run")

        assert response.status_code == 200
        data = response.json()
        assert data["created_groups"] == 3
