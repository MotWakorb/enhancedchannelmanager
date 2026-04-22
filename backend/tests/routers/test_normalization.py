"""
Unit tests for normalization endpoints.

Tests: 18 endpoints for normalization rule groups, rules, testing, migration,
and apply-to-channels (GH-104).
Uses async_client fixture which patches database session.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

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


class TestApplyToChannels:
    """Tests for POST /api/normalization/apply-to-channels (GH-104).

    The endpoint walks every existing channel, runs each name through the
    shared NormalizationEngine, and returns a per-channel diff. In execute
    mode the caller provides a per-row action (rename | merge | skip) and
    the endpoint mutates channels + writes journal entries.
    """

    def _mock_client(self, channels, groups=None):
        """Build an AsyncMock Dispatcharr client returning the given channels."""
        client = AsyncMock()
        client.get_channels.return_value = {"results": channels, "next": None}
        client.get_channel_groups.return_value = groups or []
        return client

    def _mock_engine(self, mapping):
        """Return a MagicMock normalization engine that looks up name -> normalized."""
        engine = MagicMock()

        def _normalize(name):
            result = MagicMock()
            result.normalized = mapping.get(name, name)
            return result

        engine.normalize.side_effect = _normalize
        return engine

    @pytest.mark.asyncio
    async def test_dry_run_returns_diff(self, async_client):
        """Dry-run returns per-channel diffs for channels that would change."""
        channels = [
            {"id": 1, "name": "RTL ᴿᴬᵂ", "channel_group_id": 5},
            {"id": 2, "name": "CNN", "channel_group_id": 5},  # unchanged
        ]
        client = self._mock_client(channels, groups=[{"id": 5, "name": "DE"}])
        engine = self._mock_engine({"RTL ᴿᴬᵂ": "RTL", "CNN": "CNN"})

        with patch("routers.normalization.get_client", return_value=client), \
             patch("normalization_engine.get_normalization_engine", return_value=engine):
            response = await async_client.post("/api/normalization/apply-to-channels?dry_run=true")

        assert response.status_code == 200
        data = response.json()
        assert data["dry_run"] is True
        # Only the channel with an actual change should appear in diffs
        assert data["channels_with_changes"] == 1
        assert len(data["diffs"]) == 1
        row = data["diffs"][0]
        assert row["channel_id"] == 1
        assert row["current_name"] == "RTL ᴿᴬᵂ"
        assert row["proposed_name"] == "RTL"
        assert row["collision"] is False
        assert row["suggested_action"] == "rename"
        # No mutations in dry-run mode
        client.update_channel.assert_not_called()
        client.delete_channel.assert_not_called()

    @pytest.mark.asyncio
    async def test_dry_run_flags_collision(self, async_client):
        """Dry-run marks rows whose proposed name already belongs to another channel."""
        channels = [
            {"id": 1, "name": "RTL ᴿᴬᵂ", "channel_group_id": 5},
            {"id": 2, "name": "RTL", "channel_group_id": 5},  # pre-existing target
        ]
        client = self._mock_client(channels)
        engine = self._mock_engine({"RTL ᴿᴬᵂ": "RTL", "RTL": "RTL"})

        with patch("routers.normalization.get_client", return_value=client), \
             patch("normalization_engine.get_normalization_engine", return_value=engine):
            response = await async_client.post("/api/normalization/apply-to-channels?dry_run=true")

        assert response.status_code == 200
        data = response.json()
        row = next(d for d in data["diffs"] if d["channel_id"] == 1)
        assert row["collision"] is True
        assert row["collision_target_id"] == 2
        assert row["suggested_action"] == "merge"

    @pytest.mark.asyncio
    async def test_execute_rename_non_collision(self, async_client):
        """Execute mode with action=rename updates the channel name and journals."""
        channels = [{"id": 1, "name": "RTL ᴿᴬᵂ", "channel_group_id": 5}]
        client = self._mock_client(channels)
        engine = self._mock_engine({"RTL ᴿᴬᵂ": "RTL"})

        with patch("routers.normalization.get_client", return_value=client), \
             patch("normalization_engine.get_normalization_engine", return_value=engine), \
             patch("routers.normalization.journal") as mock_journal:
            response = await async_client.post(
                "/api/normalization/apply-to-channels?dry_run=false",
                json={"actions": [{"channel_id": 1, "action": "rename"}]},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["dry_run"] is False
        assert len(data["renamed"]) == 1
        assert data["renamed"][0]["new_name"] == "RTL"
        client.update_channel.assert_awaited_once_with(1, {"name": "RTL"})
        # Two journal entries: one per rename, one bulk-apply audit summary
        # (bd-eio04.12).
        assert mock_journal.log_entry.call_count == 2
        action_types = {c.kwargs["action_type"] for c in mock_journal.log_entry.call_args_list}
        assert action_types == {"rename", "bulk_apply_normalization"}

    @pytest.mark.asyncio
    async def test_execute_merge_collision(self, async_client):
        """Execute mode with action=merge moves streams and deletes source."""
        channels = [
            {"id": 1, "name": "RTL ᴿᴬᵂ", "channel_group_id": 5, "streams": [10, 11]},
            {"id": 2, "name": "RTL", "channel_group_id": 5, "streams": [20]},
        ]
        client = self._mock_client(channels)
        # The merge path fetches each channel again for authoritative stream lists
        client.get_channel.side_effect = [
            {"id": 1, "name": "RTL ᴿᴬᵂ", "streams": [10, 11]},  # source
            {"id": 2, "name": "RTL", "streams": [20]},           # target
        ]
        engine = self._mock_engine({"RTL ᴿᴬᵂ": "RTL", "RTL": "RTL"})

        with patch("routers.normalization.get_client", return_value=client), \
             patch("normalization_engine.get_normalization_engine", return_value=engine), \
             patch("routers.normalization.journal") as mock_journal:
            response = await async_client.post(
                "/api/normalization/apply-to-channels?dry_run=false",
                json={"actions": [{"channel_id": 1, "action": "merge", "merge_target_id": 2}]},
            )

        assert response.status_code == 200
        data = response.json()
        assert len(data["merged"]) == 1
        merge_rec = data["merged"][0]
        assert merge_rec["channel_id"] == 1
        assert merge_rec["target_id"] == 2
        assert merge_rec["streams_added"] == 2
        # Target gets new stream list (original 20 + 10 + 11)
        client.update_channel.assert_awaited_once_with(2, {"streams": [20, 10, 11]})
        client.delete_channel.assert_awaited_once_with(1)
        # Two journal entries: one per merge, one bulk-apply audit summary
        # (bd-eio04.12).
        assert mock_journal.log_entry.call_count == 2
        action_types = {c.kwargs["action_type"] for c in mock_journal.log_entry.call_args_list}
        assert action_types == {"merge", "bulk_apply_normalization"}

    @pytest.mark.asyncio
    async def test_execute_skip_collision(self, async_client):
        """Execute mode with action=skip leaves the channel alone."""
        channels = [
            {"id": 1, "name": "RTL ᴿᴬᵂ", "channel_group_id": 5},
            {"id": 2, "name": "RTL", "channel_group_id": 5},
        ]
        client = self._mock_client(channels)
        engine = self._mock_engine({"RTL ᴿᴬᵂ": "RTL", "RTL": "RTL"})

        with patch("routers.normalization.get_client", return_value=client), \
             patch("normalization_engine.get_normalization_engine", return_value=engine), \
             patch("routers.normalization.journal") as mock_journal:
            response = await async_client.post(
                "/api/normalization/apply-to-channels?dry_run=false",
                json={"actions": [{"channel_id": 1, "action": "skip"}]},
            )

        assert response.status_code == 200
        data = response.json()
        assert len(data["skipped"]) == 1
        assert data["renamed"] == []
        assert data["merged"] == []
        client.update_channel.assert_not_called()
        client.delete_channel.assert_not_called()
        # Bulk-apply audit entry is always written, even when nothing changed
        # (bd-eio04.12). No per-channel entries because nothing was mutated.
        assert mock_journal.log_entry.call_count == 1
        assert (
            mock_journal.log_entry.call_args.kwargs["action_type"]
            == "bulk_apply_normalization"
        )

    @pytest.mark.asyncio
    async def test_execute_rename_collision_refused(self, async_client):
        """Rename into a collision is rejected with an error, not silently overwritten."""
        channels = [
            {"id": 1, "name": "RTL ᴿᴬᵂ", "channel_group_id": 5},
            {"id": 2, "name": "RTL", "channel_group_id": 5},
        ]
        client = self._mock_client(channels)
        engine = self._mock_engine({"RTL ᴿᴬᵂ": "RTL", "RTL": "RTL"})

        with patch("routers.normalization.get_client", return_value=client), \
             patch("normalization_engine.get_normalization_engine", return_value=engine), \
             patch("routers.normalization.journal"):
            response = await async_client.post(
                "/api/normalization/apply-to-channels?dry_run=false",
                json={"actions": [{"channel_id": 1, "action": "rename"}]},
            )

        assert response.status_code == 200
        data = response.json()
        assert len(data["errors"]) == 1
        assert "collide" in data["errors"][0]["error"].lower()
        client.update_channel.assert_not_called()

    @pytest.mark.asyncio
    async def test_preserves_number_prefix(self, async_client):
        """Channel-number prefix like '107 | ' is preserved across the rename."""
        channels = [{"id": 1, "name": "107 | RTL ᴿᴬᵂ", "channel_group_id": 5}]
        client = self._mock_client(channels)
        # Engine only sees the core name after prefix stripping
        engine = self._mock_engine({"RTL ᴿᴬᵂ": "RTL"})

        with patch("routers.normalization.get_client", return_value=client), \
             patch("normalization_engine.get_normalization_engine", return_value=engine):
            response = await async_client.post("/api/normalization/apply-to-channels?dry_run=true")

        assert response.status_code == 200
        data = response.json()
        assert len(data["diffs"]) == 1
        assert data["diffs"][0]["current_name"] == "107 | RTL ᴿᴬᵂ"
        assert data["diffs"][0]["proposed_name"] == "107 | RTL"
        assert data["diffs"][0]["channel_number_prefix"] == "107 | "


class TestApplyToChannelsRuleTrace:
    """Preview rows include the per-rule trace so the UI can render a
    'Rules fired' drawer (bd-eio04.12)."""

    @pytest.mark.asyncio
    async def test_dry_run_emits_transformations_field(self, async_client):
        """Every diff row exposes a transformations list sourced from
        NormalizationResult.transformations. Shape matches /test-batch."""
        channels = [{"id": 1, "name": "RTL ᴿᴬᵂ"}]

        # Build an engine mock whose `normalize()` returns a namespace with
        # the transformations shape the serializer expects.
        class _Result:
            normalized = "RTL"
            transformations = [(101, "RTL ᴿᴬᵂ", "RTL")]

        engine = MagicMock()
        engine.normalize.return_value = _Result()

        client = AsyncMock()
        client.get_channels.return_value = {"results": channels, "next": None}
        client.get_channel_groups.return_value = []

        with patch("routers.normalization.get_client", return_value=client), \
             patch("normalization_engine.get_normalization_engine", return_value=engine):
            response = await async_client.post(
                "/api/normalization/apply-to-channels?dry_run=true"
            )

        assert response.status_code == 200
        row = response.json()["diffs"][0]
        assert "transformations" in row
        assert row["transformations"] == [
            {"rule_id": 101, "before": "RTL ᴿᴬᵂ", "after": "RTL"}
        ]


class TestApplyToChannelsAdminGate:
    """Bulk apply-to-channels is admin-gated when auth is enabled
    (bd-ei4m9, absorbed by bd-eio04.12). Non-admin callers must see
    HTTP 403, not silently execute destructive renames."""

    @pytest.mark.asyncio
    async def test_non_admin_is_forbidden_when_auth_enabled(self, async_client):
        """When auth.require_auth and setup_complete are both true, a
        non-admin caller receives 403 from the dependency."""
        from fastapi import HTTPException, status
        from main import app
        from auth import RequireAdminIfEnabled as _prebuilt

        async def _reject() -> None:
            # FastAPI introspects the callable's signature for DI; keep it
            # parameterless so the framework doesn't try to pull query args.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin access required",
            )

        # Override the prebuilt RequireAdminIfEnabled dependency so the
        # route sees a 403 regardless of actual auth state.
        app.dependency_overrides[_prebuilt.dependency] = _reject
        try:
            response = await async_client.post(
                "/api/normalization/apply-to-channels?dry_run=true"
            )
        finally:
            app.dependency_overrides.pop(_prebuilt.dependency, None)

        assert response.status_code == 403
        assert "admin" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_anonymous_allowed_when_auth_disabled(self, async_client):
        """When auth is disabled (setup_complete=False or require_auth=False),
        the dependency returns None and the endpoint is reachable. The
        default `async_client` fixture runs with auth disabled, so we just
        verify the happy path still works after the admin gate was added."""
        channels = [{"id": 1, "name": "RTL ᴿᴬᵂ"}]
        client = AsyncMock()
        client.get_channels.return_value = {"results": channels, "next": None}
        client.get_channel_groups.return_value = []

        engine = MagicMock()
        engine.normalize.return_value = MagicMock(normalized="RTL", transformations=[])

        with patch("routers.normalization.get_client", return_value=client), \
             patch("normalization_engine.get_normalization_engine", return_value=engine):
            response = await async_client.post(
                "/api/normalization/apply-to-channels?dry_run=true"
            )

        assert response.status_code == 200


class TestApplyToChannelsIdempotencyLock:
    """Execute mode holds a module-level lock so only one bulk rename/merge
    can run at a time. A concurrent caller must see HTTP 409, not race
    into the critical section (bd-eio04.12)."""

    @pytest.mark.asyncio
    async def test_returns_409_when_another_bulk_in_flight(self, async_client):
        """Manually hold the lock and verify the endpoint returns 409."""
        from routers import normalization as normalization_router

        channels = [{"id": 1, "name": "RTL ᴿᴬᵂ"}]
        client = AsyncMock()
        client.get_channels.return_value = {"results": channels, "next": None}
        client.get_channel_groups.return_value = []

        engine = MagicMock()
        engine.normalize.return_value = MagicMock(normalized="RTL", transformations=[])

        # Pre-acquire the execute lock to simulate a bulk apply already in
        # progress. We release it in the finally below so the fixture's
        # test isolation stays clean for other tests.
        await normalization_router._APPLY_TO_CHANNELS_EXECUTE_LOCK.acquire()
        try:
            with patch("routers.normalization.get_client", return_value=client), \
                 patch("normalization_engine.get_normalization_engine", return_value=engine):
                response = await async_client.post(
                    "/api/normalization/apply-to-channels?dry_run=false",
                    json={"actions": [{"channel_id": 1, "action": "rename"}]},
                )
        finally:
            normalization_router._APPLY_TO_CHANNELS_EXECUTE_LOCK.release()

        assert response.status_code == 409
        assert "in progress" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_dry_run_is_not_blocked_by_lock(self, async_client):
        """The lock only gates execute mode; dry-run reads stay concurrent."""
        from routers import normalization as normalization_router

        channels = [{"id": 1, "name": "RTL ᴿᴬᵂ"}]
        client = AsyncMock()
        client.get_channels.return_value = {"results": channels, "next": None}
        client.get_channel_groups.return_value = []

        engine = MagicMock()
        engine.normalize.return_value = MagicMock(normalized="RTL", transformations=[])

        await normalization_router._APPLY_TO_CHANNELS_EXECUTE_LOCK.acquire()
        try:
            with patch("routers.normalization.get_client", return_value=client), \
                 patch("normalization_engine.get_normalization_engine", return_value=engine):
                response = await async_client.post(
                    "/api/normalization/apply-to-channels?dry_run=true"
                )
        finally:
            normalization_router._APPLY_TO_CHANNELS_EXECUTE_LOCK.release()

        assert response.status_code == 200


class TestApplyToChannelsRateLimit:
    """The endpoint carries a @limiter.limit decorator so a runaway client
    can't hammer destructive bulk renames. Rate limiting is disabled in
    the test conftest (RATE_LIMIT_ENABLED=0) so we just verify the
    decorator is wired — the actual throttling is validated by slowapi."""

    def test_rate_limit_decorator_applied(self):
        """The endpoint function is registered with the slowapi limiter."""
        from routers import normalization as normalization_router

        limiter = normalization_router.limiter
        # slowapi keys _route_limits by fully-qualified function name
        # (e.g. 'routers.normalization.apply_normalization_to_channels').
        key = "routers.normalization.apply_normalization_to_channels"
        assert key in limiter._route_limits, (
            "apply-to-channels endpoint must be registered with the limiter"
        )
        limits = limiter._route_limits[key]
        assert limits, "at least one Limit entry expected"
        rendered = " ".join(str(lim.limit) for lim in limits)
        assert "5" in rendered and "minute" in rendered


class TestFindChannelByNameNormalizationFallback:
    """Regression tests for the _find_channel_by_name lookup fix (GH-104 Part 2).

    When no exact match exists but the normalization engine would collapse
    the search name into a stored channel name, the lookup must find it so
    auto-creation doesn't build a brand-new duplicate.
    """

    def _build_executor(self, channels, normalizer):
        """Create an ActionExecutor wired to a fake normalization engine."""
        from auto_creation_executor import ActionExecutor

        client = MagicMock()
        engine = MagicMock()

        def _normalize(name):
            result = MagicMock()
            result.normalized = normalizer(name)
            return result

        engine.normalize.side_effect = _normalize
        engine.extract_core_name.side_effect = lambda n: normalizer(n)
        engine.extract_call_sign.return_value = None
        return ActionExecutor(client, existing_channels=channels, normalization_engine=engine)

    def test_lookup_finds_normalized_existing_channel(self):
        """Searching for 'RTL ᴿᴬᵂ' finds stored 'RTL' via normalized-search fallback."""
        # Simulate the bug scenario: a channel already exists with the
        # already-normalized name ("RTL"), and a new stream arrives with the
        # un-normalized form ("RTL ᴿᴬᵂ") that should be attached to it rather
        # than creating a duplicate.
        channels = [{"id": 42, "name": "RTL"}]
        executor = self._build_executor(
            channels,
            normalizer=lambda n: "RTL" if "RTL" in n else n,
        )
        found = executor._find_channel_by_name("RTL ᴿᴬᵂ")
        assert found is not None
        assert found["id"] == 42

    def test_lookup_misses_when_no_normalization_overlap(self):
        """Fallback returns None when the normalized form still doesn't match."""
        channels = [{"id": 42, "name": "Fox News"}]
        executor = self._build_executor(
            channels,
            normalizer=lambda n: n,  # identity normalizer
        )
        assert executor._find_channel_by_name("ESPN") is None

    def test_exact_match_still_wins(self):
        """Exact match is preferred over the normalization fallback path."""
        channels = [{"id": 1, "name": "ESPN"}, {"id": 2, "name": "ESPN2"}]
        executor = self._build_executor(
            channels,
            normalizer=lambda n: "ESPN" if "ESPN" in n else n,
        )
        found = executor._find_channel_by_name("ESPN2")
        assert found["id"] == 2  # not 1 — exact match wins
