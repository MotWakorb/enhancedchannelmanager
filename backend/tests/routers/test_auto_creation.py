"""
Unit tests for auto-creation endpoints.

Tests: rule CRUD, bulk-update, reorder, toggle, duplicate,
       pipeline execution, execution history, rollback, YAML import/export,
       validation, and schema endpoints.
Mocks: auto_creation_engine, auto_creation_schema, get_client(), get_session().
"""
import json
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from models import AutoCreationRule, AutoCreationExecution, NormalizationRuleGroup


def _create_normalization_group(session, **overrides):
    """Helper to create a NormalizationRuleGroup."""
    defaults = {
        "name": "Test Group",
        "enabled": True,
        "priority": 0,
        "is_builtin": False,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }
    defaults.update(overrides)
    group = NormalizationRuleGroup(**defaults)
    session.add(group)
    session.commit()
    session.refresh(group)
    return group


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

    @pytest.mark.asyncio
    async def test_accepts_valid_normalization_group_ids(
        self, async_client, test_session
    ):
        """bd-j5p4k: POST accepts normalization_group_ids that all exist.

        Mirrors the PUT/bulk-update validation added in bd-i75ax to close
        the symmetric write-time gap on the create endpoint.
        """
        g1 = _create_normalization_group(test_session, name="Group A")
        g2 = _create_normalization_group(test_session, name="Group B")

        with patch("auto_creation_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.auto_creation.journal"):
            response = await async_client.post("/api/auto-creation/rules", json={
                "name": "WithNorm",
                "conditions": [{"type": "stream_name_contains", "value": "CNN"}],
                "actions": [{"type": "create_channel", "name_template": "{stream_name}"}],
                "normalization_group_ids": [g1.id, g2.id],
            })

        assert response.status_code == 200, response.text
        assert sorted(response.json()["normalization_group_ids"]) == sorted([g1.id, g2.id])

    @pytest.mark.asyncio
    async def test_accepts_empty_normalization_group_ids(
        self, async_client
    ):
        """bd-j5p4k: POST accepts an empty normalization_group_ids list
        (no normalization groups is a legitimate state)."""
        with patch("auto_creation_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.auto_creation.journal"):
            response = await async_client.post("/api/auto-creation/rules", json={
                "name": "EmptyNorm",
                "conditions": [{"type": "stream_name_contains", "value": "CNN"}],
                "actions": [{"type": "create_channel", "name_template": "{stream_name}"}],
                "normalization_group_ids": [],
            })

        assert response.status_code == 200, response.text
        assert response.json()["normalization_group_ids"] == []

    @pytest.mark.asyncio
    async def test_rejects_missing_normalization_group_id(
        self, async_client, test_session
    ):
        """bd-j5p4k: POST returns 422 when a submitted ID is not in
        normalization_rule_groups, and the error names the offending ID."""
        g1 = _create_normalization_group(test_session, name="Group Real")
        missing_id = 999999

        response = await async_client.post("/api/auto-creation/rules", json={
            "name": "BadNorm",
            "conditions": [{"type": "stream_name_contains", "value": "CNN"}],
            "actions": [{"type": "create_channel", "name_template": "{stream_name}"}],
            "normalization_group_ids": [g1.id, missing_id],
        })

        assert response.status_code == 422, response.text
        body = response.text
        assert str(missing_id) in body
        detail = response.json().get("detail")
        assert detail is not None
        if isinstance(detail, dict):
            offending = detail.get("invalid_normalization_group_ids") or detail.get("offending_ids") or []
            assert missing_id in offending
            assert g1.id not in offending

    @pytest.mark.asyncio
    async def test_rejects_lists_all_invalid_normalization_group_ids(
        self, async_client, test_session
    ):
        """bd-j5p4k: When multiple submitted IDs are missing on POST, all are listed."""
        g1 = _create_normalization_group(test_session, name="Real Group")
        bad_a, bad_b, bad_c = 700001, 700002, 700003

        response = await async_client.post("/api/auto-creation/rules", json={
            "name": "MultiBadNorm",
            "conditions": [{"type": "stream_name_contains", "value": "CNN"}],
            "actions": [{"type": "create_channel", "name_template": "{stream_name}"}],
            "normalization_group_ids": [g1.id, bad_a, bad_b, bad_c],
        })

        assert response.status_code == 422, response.text
        detail = response.json().get("detail")
        assert isinstance(detail, dict)
        offending = detail.get("invalid_normalization_group_ids") or detail.get("offending_ids") or []
        assert sorted(offending) == sorted([bad_a, bad_b, bad_c])
        assert g1.id not in offending


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

    @pytest.mark.asyncio
    async def test_accepts_valid_normalization_group_ids(
        self, async_client, test_session
    ):
        """bd-i75ax: PUT accepts normalization_group_ids that all exist."""
        rule = _create_rule(test_session, name="WithNorm")
        g1 = _create_normalization_group(test_session, name="Group A")
        g2 = _create_normalization_group(test_session, name="Group B")

        with patch("auto_creation_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.auto_creation.journal"):
            response = await async_client.put(
                f"/api/auto-creation/rules/{rule.id}",
                json={"normalization_group_ids": [g1.id, g2.id]},
            )

        assert response.status_code == 200, response.text
        assert sorted(response.json()["normalization_group_ids"]) == sorted([g1.id, g2.id])

    @pytest.mark.asyncio
    async def test_accepts_empty_normalization_group_ids(
        self, async_client, test_session
    ):
        """bd-i75ax: PUT accepts an empty normalization_group_ids list (disables normalization)."""
        rule = _create_rule(test_session, name="EmptyNorm")
        # Pre-populate with valid IDs to verify empty clears them
        g1 = _create_normalization_group(test_session, name="Group X")
        rule.set_normalization_group_ids([g1.id])
        test_session.commit()

        with patch("auto_creation_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.auto_creation.journal"):
            response = await async_client.put(
                f"/api/auto-creation/rules/{rule.id}",
                json={"normalization_group_ids": []},
            )

        assert response.status_code == 200, response.text
        assert response.json()["normalization_group_ids"] == []

    @pytest.mark.asyncio
    async def test_rejects_missing_normalization_group_id(
        self, async_client, test_session
    ):
        """bd-i75ax: PUT returns 422 when a submitted ID is not in normalization_rule_groups,
        and the error names the offending ID."""
        rule = _create_rule(test_session, name="BadNorm")
        g1 = _create_normalization_group(test_session, name="Group Real")
        missing_id = 999999

        response = await async_client.put(
            f"/api/auto-creation/rules/{rule.id}",
            json={"normalization_group_ids": [g1.id, missing_id]},
        )

        assert response.status_code == 422, response.text
        body = response.text
        assert str(missing_id) in body
        # Sanity: the valid id should not be in the offending list
        # (we look for the structured field rather than substring to avoid
        # false-positive overlap with rule_id or other numbers in the error)
        detail = response.json().get("detail")
        assert detail is not None
        # detail may be a dict with an offending list; assert structure carries it
        if isinstance(detail, dict):
            offending = detail.get("invalid_normalization_group_ids") or detail.get("offending_ids") or []
            assert missing_id in offending
            assert g1.id not in offending

    @pytest.mark.asyncio
    async def test_rejects_lists_all_invalid_normalization_group_ids(
        self, async_client, test_session
    ):
        """bd-i75ax: When multiple submitted IDs are missing, all are listed."""
        rule = _create_rule(test_session, name="MultiBadNorm")
        g1 = _create_normalization_group(test_session, name="Real Group")
        bad_a, bad_b, bad_c = 700001, 700002, 700003

        response = await async_client.put(
            f"/api/auto-creation/rules/{rule.id}",
            json={"normalization_group_ids": [g1.id, bad_a, bad_b, bad_c]},
        )

        assert response.status_code == 422, response.text
        detail = response.json().get("detail")
        assert isinstance(detail, dict)
        offending = detail.get("invalid_normalization_group_ids") or detail.get("offending_ids") or []
        assert sorted(offending) == sorted([bad_a, bad_b, bad_c])
        assert g1.id not in offending

    @pytest.mark.asyncio
    async def test_does_not_validate_when_normalization_group_ids_omitted(
        self, async_client, test_session
    ):
        """bd-i75ax delta-on-write: PUT requests that don't include
        normalization_group_ids must not re-validate the existing stored value.
        This preserves backward-compat with rules whose stored IDs reference
        groups that have since been deleted."""
        rule = _create_rule(test_session, name="StaleStored")
        # Simulate a stale stored id (group was deleted out from under us)
        stale_id = 999998
        rule.set_normalization_group_ids([stale_id])
        test_session.commit()

        with patch("auto_creation_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.auto_creation.journal"):
            # Update an unrelated field — must succeed even though stored
            # normalization_group_ids reference a missing group.
            response = await async_client.put(
                f"/api/auto-creation/rules/{rule.id}",
                json={"name": "Renamed"},
            )

        assert response.status_code == 200, response.text
        assert response.json()["name"] == "Renamed"


class TestBulkUpdateAutoCreationRules:
    """Tests for POST /api/auto-creation/rules/bulk-update."""

    @pytest.mark.asyncio
    async def test_updates_multiple_rules(self, async_client, test_session):
        """Applies the same scalar updates to several rules."""
        r1 = _create_rule(test_session, name="BulkA", run_on_refresh=False, orphan_action="delete")
        r2 = _create_rule(test_session, name="BulkB", run_on_refresh=False)
        with patch("auto_creation_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.auto_creation.journal"):
            response = await async_client.post("/api/auto-creation/rules/bulk-update", json={
                "rule_ids": [r1.id, r2.id],
                "run_on_refresh": True,
                "orphan_action": "none",
            })
        assert response.status_code == 200
        data = response.json()
        assert data["updated_count"] == 2
        assert len(data["rules"]) == 2
        test_session.expire_all()
        assert test_session.query(AutoCreationRule).get(r1.id).run_on_refresh is True
        assert test_session.query(AutoCreationRule).get(r1.id).orphan_action == "none"
        assert test_session.query(AutoCreationRule).get(r2.id).run_on_refresh is True

    @pytest.mark.asyncio
    async def test_rejects_empty_rule_ids(self, async_client):
        """rule_ids must be non-empty."""
        response = await async_client.post("/api/auto-creation/rules/bulk-update", json={
            "rule_ids": [],
            "enabled": False,
        })
        # Pydantic request validation rejects empty lists.
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_rejects_more_than_500_rule_ids(self, async_client):
        """rule_ids is capped to prevent pathological requests."""
        response = await async_client.post("/api/auto-creation/rules/bulk-update", json={
            "rule_ids": list(range(1, 502)),  # 501 ids
            "enabled": False,
        })
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_accepts_exactly_500_rule_ids(self, async_client, test_session):
        rules = [_create_rule(test_session, name=f"Bulk500-{i}", enabled=True) for i in range(500)]
        with patch("auto_creation_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.auto_creation.journal"):
            response = await async_client.post("/api/auto-creation/rules/bulk-update", json={
                "rule_ids": [r.id for r in rules],
                "enabled": False,
            })
        assert response.status_code == 200
        assert response.json()["updated_count"] == 500

    @pytest.mark.asyncio
    async def test_rejects_duplicate_rule_ids(self, async_client, test_session):
        r = _create_rule(test_session, name="DupRule", enabled=True)
        response = await async_client.post("/api/auto-creation/rules/bulk-update", json={
            "rule_ids": [r.id, r.id],
            "enabled": False,
        })
        assert response.status_code == 400
        assert "duplicate" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_rejects_no_fields(self, async_client):
        """At least one update field is required."""
        response = await async_client.post("/api/auto-creation/rules/bulk-update", json={
            "rule_ids": [1],
        })
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_rolls_back_when_any_rule_id_missing(self, async_client, test_session):
        """If one rule id is missing, nothing is committed."""
        r1 = _create_rule(test_session, name="BulkRB1", enabled=True)
        r2 = _create_rule(test_session, name="BulkRB2", enabled=True)
        r3 = _create_rule(test_session, name="BulkRB3", enabled=True)

        missing_id = 999999
        with patch("auto_creation_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.auto_creation.journal"):
            response = await async_client.post("/api/auto-creation/rules/bulk-update", json={
                "rule_ids": [r1.id, r2.id, r3.id, missing_id],
                "enabled": False,
            })

        assert response.status_code == 404

        test_session.expire_all()
        assert test_session.query(AutoCreationRule).get(r1.id).enabled is True
        assert test_session.query(AutoCreationRule).get(r2.id).enabled is True
        assert test_session.query(AutoCreationRule).get(r3.id).enabled is True

    @pytest.mark.asyncio
    async def test_reports_all_missing_ids(self, async_client, test_session):
        """bd-bh1hh: When multiple rule_ids are missing, the 404 body mentions
        every missing id (not just the first one encountered). This is a visible
        API change from the original loop-and-fail-fast behavior.
        """
        r1 = _create_rule(test_session, name="BulkMiss1", enabled=True)
        r2 = _create_rule(test_session, name="BulkMiss2", enabled=True)

        missing_a = 99999
        missing_b = 99998
        with patch("auto_creation_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.auto_creation.journal"):
            response = await async_client.post(
                "/api/auto-creation/rules/bulk-update",
                json={
                    "rule_ids": [r1.id, missing_a, r2.id, missing_b],
                    "enabled": False,
                },
            )

        assert response.status_code == 404
        detail = str(response.json()["detail"])
        assert str(missing_a) in detail
        assert str(missing_b) in detail

    @pytest.mark.asyncio
    async def test_sets_merge_streams_remove_non_matching(self, async_client, test_session):
        """Updates remove_non_matching on all merge_streams actions."""
        merge_action = {
            "type": "merge_streams",
            "target": "auto",
            "match_by": "tvg_id",
            "remove_non_matching": False,
        }
        r = _create_rule(
            test_session,
            name="MergeRule",
            actions=json.dumps([merge_action]),
        )
        with patch("auto_creation_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.auto_creation.journal"):
            response = await async_client.post("/api/auto-creation/rules/bulk-update", json={
                "rule_ids": [r.id],
                "merge_streams_remove_non_matching": True,
            })
        assert response.status_code == 200
        test_session.expire_all()
        rule = test_session.query(AutoCreationRule).get(r.id)
        acts = json.loads(rule.actions)
        assert acts[0]["remove_non_matching"] is True

    @pytest.mark.asyncio
    async def test_scalars_only_update_skips_validate_on_drifted_rule(
        self, async_client, test_session
    ):
        """bd-z7xqy: Scalar-only bulk edits must succeed even when the stored
        rule's conditions/actions fail validate_rule (schema drift / legacy data).

        Uses the real validate_rule — no mock — to prove the handler no longer
        gates scalar-only updates on post-update schema validation.
        """
        rule = _create_rule(
            test_session,
            name="DriftedScalar",
            enabled=False,
            conditions=json.dumps([]),  # validate_rule rejects empty conditions
        )

        with patch("routers.auto_creation.journal"):
            response = await async_client.post(
                "/api/auto-creation/rules/bulk-update",
                json={"rule_ids": [rule.id], "enabled": True},
            )

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["updated_count"] == 1

        test_session.expire_all()
        refreshed = test_session.query(AutoCreationRule).get(rule.id)
        assert refreshed.enabled is True

    @pytest.mark.asyncio
    async def test_merge_streams_payload_still_validates_drifted_rule(
        self, async_client, test_session
    ):
        """bd-z7xqy: When the bulk payload touches rule logic
        (merge_streams_remove_non_matching), validate_rule must still gate
        the change and the transaction must roll back on failure.
        """
        merge_action = {
            "type": "merge_streams",
            "target": "auto",
            "match_by": "tvg_id",
            "remove_non_matching": False,
        }
        original_actions = json.dumps([merge_action])
        rule = _create_rule(
            test_session,
            name="DriftedMerge",
            conditions=json.dumps([]),  # drift: empty conditions fail validate_rule
            actions=original_actions,
        )

        with patch("routers.auto_creation.journal"):
            response = await async_client.post(
                "/api/auto-creation/rules/bulk-update",
                json={
                    "rule_ids": [rule.id],
                    "merge_streams_remove_non_matching": True,
                },
            )

        assert response.status_code == 400, response.text
        detail = response.json()["detail"]
        # detail is a dict with {"message": "...", "errors": [...]}
        message = detail["message"] if isinstance(detail, dict) else str(detail)
        assert "Invalid rule configuration" in message

        # Rollback: actions JSON must be unchanged.
        test_session.expire_all()
        refreshed = test_session.query(AutoCreationRule).get(rule.id)
        assert json.loads(refreshed.actions) == [merge_action]

    @pytest.mark.asyncio
    async def test_rejects_conditions_in_payload(self, async_client, test_session):
        """bd-gjoe5: conditions is not supported in bulk-update; silent-drop
        is the wrong default for an API contract. Must reject (4xx) and name
        the offending field in the error message.
        """
        r = _create_rule(test_session, name="RejectCond", enabled=True)
        response = await async_client.post(
            "/api/auto-creation/rules/bulk-update",
            json={
                "rule_ids": [r.id],
                "conditions": [{"type": "stream_name_contains", "value": "X"}],
            },
        )
        assert response.status_code in (400, 422), response.text
        body = response.text.lower()
        assert "conditions" in body

    @pytest.mark.asyncio
    async def test_rejects_actions_in_payload(self, async_client, test_session):
        """bd-gjoe5: actions is not supported in bulk-update."""
        r = _create_rule(test_session, name="RejectActs", enabled=True)
        response = await async_client.post(
            "/api/auto-creation/rules/bulk-update",
            json={
                "rule_ids": [r.id],
                "actions": [{"type": "create_channel", "name_template": "{stream_name}"}],
            },
        )
        assert response.status_code in (400, 422), response.text
        body = response.text.lower()
        assert "actions" in body

    @pytest.mark.asyncio
    async def test_scalars_only_update_still_succeeds(self, async_client, test_session):
        """bd-gjoe5 regression guard: scalars-only bulk updates must still
        return 200 after the conditions/actions rejection is added.
        """
        r = _create_rule(test_session, name="ScalarsOnly", enabled=False)
        with patch("auto_creation_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.auto_creation.journal"):
            response = await async_client.post(
                "/api/auto-creation/rules/bulk-update",
                json={"rule_ids": [r.id], "enabled": True, "priority": 5},
            )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["updated_count"] == 1
        test_session.expire_all()
        refreshed = test_session.query(AutoCreationRule).get(r.id)
        assert refreshed.enabled is True
        assert refreshed.priority == 5

    @pytest.mark.asyncio
    async def test_emits_per_entity_journal_entries_with_shared_batch_id(
        self, async_client, test_session
    ):
        """bd-91mcq: Bulk-update must emit one journal entry per mutated rule,
        each with entity_id=rule.id, and all sharing the same batch_id.

        Matches the pattern in backend/routers/channels.py:800 (bulk channel
        renumber) — per-entity forensics over a single summary entry.
        """
        r1 = _create_rule(test_session, name="JournalA", enabled=False)
        r2 = _create_rule(test_session, name="JournalB", enabled=False)
        r3 = _create_rule(test_session, name="JournalC", enabled=False)

        mock_journal = MagicMock()
        with patch("auto_creation_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.auto_creation.journal", mock_journal):
            response = await async_client.post(
                "/api/auto-creation/rules/bulk-update",
                json={"rule_ids": [r1.id, r2.id, r3.id], "enabled": True},
            )

        assert response.status_code == 200, response.text

        # One log_entry call per rule mutated.
        assert mock_journal.log_entry.call_count == 3

        # Collect entity_ids and batch_ids from each call.
        call_entity_ids = []
        call_batch_ids = []
        for call in mock_journal.log_entry.call_args_list:
            kwargs = call.kwargs
            call_entity_ids.append(kwargs["entity_id"])
            call_batch_ids.append(kwargs["batch_id"])

        # Each entity_id matches one of the seeded rules, all distinct.
        assert sorted(call_entity_ids) == sorted([r1.id, r2.id, r3.id])

        # All three calls share the same batch_id (grouping).
        assert len(set(call_batch_ids)) == 1
        assert call_batch_ids[0] is not None and call_batch_ids[0] != ""

    @pytest.mark.asyncio
    async def test_journal_description_reflects_scalar_diff(
        self, async_client, test_session
    ):
        """bd-91mcq: Journal description must show the before→after diff of
        changed scalar fields (e.g. 'enabled: False → True, priority: 3 → 5').
        """
        rule = _create_rule(
            test_session, name="DiffRule", enabled=False, priority=3
        )

        mock_journal = MagicMock()
        with patch("auto_creation_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.auto_creation.journal", mock_journal):
            response = await async_client.post(
                "/api/auto-creation/rules/bulk-update",
                json={"rule_ids": [rule.id], "enabled": True, "priority": 5},
            )

        assert response.status_code == 200, response.text
        assert mock_journal.log_entry.call_count == 1

        call = mock_journal.log_entry.call_args
        description = call.kwargs["description"]
        # Description must reflect both transitions.
        assert "enabled" in description
        assert "priority" in description
        assert "False" in description and "True" in description
        assert "3" in description and "5" in description

        # before/after also capture the diff, mirroring channels.py pattern.
        before = call.kwargs.get("before_value") or {}
        after = call.kwargs.get("after_value") or {}
        assert before.get("enabled") is False
        assert after.get("enabled") is True
        assert before.get("priority") == 3
        assert after.get("priority") == 5

    @pytest.mark.asyncio
    async def test_no_journal_entries_when_rollback(
        self, async_client, test_session
    ):
        """bd-91mcq: On rollback path (missing rule id triggers 404), no
        journal entries must be emitted.
        """
        r1 = _create_rule(test_session, name="NoJournalRB1", enabled=True)
        r2 = _create_rule(test_session, name="NoJournalRB2", enabled=True)
        missing_id = 999999

        mock_journal = MagicMock()
        with patch("auto_creation_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.auto_creation.journal", mock_journal):
            response = await async_client.post(
                "/api/auto-creation/rules/bulk-update",
                json={
                    "rule_ids": [r1.id, r2.id, missing_id],
                    "enabled": False,
                },
            )

        assert response.status_code == 404
        # Zero log_entry calls on the rollback path.
        assert mock_journal.log_entry.call_count == 0

    @pytest.mark.asyncio
    async def test_accepts_valid_normalization_group_ids(
        self, async_client, test_session
    ):
        """bd-i75ax: bulk-update accepts normalization_group_ids that all exist."""
        r1 = _create_rule(test_session, name="BulkNormA")
        r2 = _create_rule(test_session, name="BulkNormB")
        g1 = _create_normalization_group(test_session, name="Bulk Group A")
        g2 = _create_normalization_group(test_session, name="Bulk Group B")

        with patch("auto_creation_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.auto_creation.journal"):
            response = await async_client.post(
                "/api/auto-creation/rules/bulk-update",
                json={
                    "rule_ids": [r1.id, r2.id],
                    "normalization_group_ids": [g1.id, g2.id],
                },
            )

        assert response.status_code == 200, response.text
        assert response.json()["updated_count"] == 2
        test_session.expire_all()
        for rid in (r1.id, r2.id):
            refreshed = test_session.query(AutoCreationRule).get(rid)
            assert sorted(refreshed.get_normalization_group_ids()) == sorted([g1.id, g2.id])

    @pytest.mark.asyncio
    async def test_accepts_empty_normalization_group_ids(
        self, async_client, test_session
    ):
        """bd-i75ax: bulk-update accepts empty normalization_group_ids list."""
        r1 = _create_rule(test_session, name="BulkEmptyNorm")
        # Pre-populate so empty actually clears something
        g1 = _create_normalization_group(test_session, name="Bulk Pre Group")
        r1.set_normalization_group_ids([g1.id])
        test_session.commit()

        with patch("auto_creation_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.auto_creation.journal"):
            response = await async_client.post(
                "/api/auto-creation/rules/bulk-update",
                json={
                    "rule_ids": [r1.id],
                    "normalization_group_ids": [],
                },
            )

        assert response.status_code == 200, response.text
        test_session.expire_all()
        refreshed = test_session.query(AutoCreationRule).get(r1.id)
        assert refreshed.get_normalization_group_ids() == []

    @pytest.mark.asyncio
    async def test_rejects_missing_normalization_group_id(
        self, async_client, test_session
    ):
        """bd-i75ax: bulk-update returns 422 with offending IDs named when any
        submitted ID is missing from normalization_rule_groups, and rolls back
        (no rule is mutated)."""
        r1 = _create_rule(test_session, name="BulkBadNormA", enabled=False)
        r2 = _create_rule(test_session, name="BulkBadNormB", enabled=False)
        g1 = _create_normalization_group(test_session, name="Bulk Real Group")
        bad_a = 800001
        bad_b = 800002

        mock_journal = MagicMock()
        with patch("auto_creation_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.auto_creation.journal", mock_journal):
            response = await async_client.post(
                "/api/auto-creation/rules/bulk-update",
                json={
                    "rule_ids": [r1.id, r2.id],
                    "normalization_group_ids": [g1.id, bad_a, bad_b],
                    # Try a scalar update too — must not be applied on rollback
                    "enabled": True,
                },
            )

        assert response.status_code == 422, response.text
        detail = response.json().get("detail")
        assert isinstance(detail, dict)
        offending = detail.get("invalid_normalization_group_ids") or detail.get("offending_ids") or []
        assert sorted(offending) == sorted([bad_a, bad_b])
        assert g1.id not in offending

        # No journal entries should be written on the validation failure path.
        assert mock_journal.log_entry.call_count == 0

        # Sanity: scalar update must not have been persisted.
        test_session.expire_all()
        for rid in (r1.id, r2.id):
            refreshed = test_session.query(AutoCreationRule).get(rid)
            assert refreshed.enabled is False, f"rule id={rid} was mutated despite 422"

    @pytest.mark.asyncio
    async def test_does_not_validate_when_normalization_group_ids_omitted(
        self, async_client, test_session
    ):
        """bd-i75ax delta-on-write: bulk-update requests that don't include
        normalization_group_ids must not re-validate stored values, even if
        any rule in scope has stale stored IDs."""
        r1 = _create_rule(test_session, name="BulkStale", enabled=False)
        # Simulate a stale stored id
        r1.set_normalization_group_ids([999997])
        test_session.commit()

        with patch("auto_creation_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.auto_creation.journal"):
            response = await async_client.post(
                "/api/auto-creation/rules/bulk-update",
                json={"rule_ids": [r1.id], "enabled": True},
            )

        assert response.status_code == 200, response.text
        test_session.expire_all()
        refreshed = test_session.query(AutoCreationRule).get(r1.id)
        assert refreshed.enabled is True


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
    """Tests for POST /api/auto-creation/run (background-task pattern, bd-enfsy)."""

    @pytest.mark.asyncio
    async def test_returns_202_with_execution_id(self, async_client, test_session):
        """POST /run enqueues work and returns 202 + execution_id immediately."""
        # Use an Event so the background task blocks until the assertion runs,
        # so we can observe the "running" status before the engine completes.
        import asyncio as _asyncio
        gate = _asyncio.Event()

        async def slow_run_pipeline(*args, **kwargs):
            await gate.wait()
            return {"success": True, "execution_id": kwargs.get("execution_id")}

        mock_engine = AsyncMock()
        mock_engine.run_pipeline = AsyncMock(side_effect=slow_run_pipeline)

        with patch("auto_creation_engine.get_auto_creation_engine", return_value=mock_engine):
            response = await async_client.post("/api/auto-creation/run", json={"dry_run": False})

        assert response.status_code == 202, response.text
        body = response.json()
        assert "execution_id" in body
        assert body["status"] == "running"
        execution_id = body["execution_id"]

        # Execution row should already exist with status="running"
        from models import AutoCreationExecution
        exe = test_session.query(AutoCreationExecution).filter_by(id=execution_id).first()
        assert exe is not None
        assert exe.status == "running"
        assert exe.mode == "execute"
        assert exe.triggered_by == "api"

        # Release the background task
        gate.set()
        # Yield so the background task can complete (drain it)
        for _ in range(20):
            await _asyncio.sleep(0)
        # Engine call must have been issued with execution_id binding
        mock_engine.run_pipeline.assert_called()
        call_kwargs = mock_engine.run_pipeline.call_args.kwargs
        assert call_kwargs["dry_run"] is False
        assert call_kwargs["triggered_by"] == "api"
        assert call_kwargs["execution_id"] == execution_id

    @pytest.mark.asyncio
    async def test_dry_run_creates_dry_run_execution(self, async_client, test_session):
        """dry_run=True must create execution with mode='dry_run'."""
        mock_engine = AsyncMock()
        mock_engine.run_pipeline = AsyncMock(return_value={"success": True})

        with patch("auto_creation_engine.get_auto_creation_engine", return_value=mock_engine):
            response = await async_client.post("/api/auto-creation/run", json={"dry_run": True})

        assert response.status_code == 202
        execution_id = response.json()["execution_id"]
        from models import AutoCreationExecution
        exe = test_session.query(AutoCreationExecution).filter_by(id=execution_id).first()
        assert exe is not None
        assert exe.mode == "dry_run"

    @pytest.mark.asyncio
    async def test_background_task_failure_marks_execution_failed(self, async_client, test_session):
        """If the engine raises, the background supervisor marks the execution failed."""
        import asyncio as _asyncio

        async def boom(*args, **kwargs):
            raise RuntimeError("engine exploded")

        mock_engine = AsyncMock()
        mock_engine.run_pipeline = AsyncMock(side_effect=boom)

        with patch("auto_creation_engine.get_auto_creation_engine", return_value=mock_engine):
            response = await async_client.post("/api/auto-creation/run", json={"dry_run": False})

        assert response.status_code == 202
        execution_id = response.json()["execution_id"]

        # Yield to let the background task run
        for _ in range(50):
            await _asyncio.sleep(0)

        from models import AutoCreationExecution
        # Use a fresh query to pick up the supervised handler's commit
        test_session.expire_all()
        exe = test_session.query(AutoCreationExecution).filter_by(id=execution_id).first()
        assert exe is not None
        assert exe.status == "failed"
        assert exe.error_message and "engine exploded" in exe.error_message

    @pytest.mark.asyncio
    async def test_enqueue_completes_within_timeout_budget(self, async_client, test_session):
        """The handler itself must return fast (well under the 30s timeout) — the
        whole point of bd-enfsy is to make /run not synchronous."""
        import asyncio as _asyncio
        import time as _time
        gate = _asyncio.Event()

        async def slow(*args, **kwargs):
            await gate.wait()
            return {"success": True}

        mock_engine = AsyncMock()
        mock_engine.run_pipeline = AsyncMock(side_effect=slow)

        with patch("auto_creation_engine.get_auto_creation_engine", return_value=mock_engine):
            start = _time.monotonic()
            response = await async_client.post("/api/auto-creation/run", json={"dry_run": False})
            elapsed = _time.monotonic() - start

        # Must enqueue and return well under 30s — even with a worker stuck in the engine
        assert response.status_code == 202
        assert elapsed < 5.0, f"enqueue took {elapsed:.2f}s — handler is not actually async-enqueuing"

        gate.set()
        for _ in range(20):
            await _asyncio.sleep(0)


class TestRunAutoCreationRule:
    """Tests for POST /api/auto-creation/rules/{rule_id}/run (background-task pattern)."""

    @pytest.mark.asyncio
    async def test_returns_202_and_invokes_run_rule_with_execution_id(self, async_client, test_session):
        """POST /rules/{id}/run returns 202 + execution_id, runs in background."""
        import asyncio as _asyncio
        rule = _create_rule(test_session, name="Sports")
        mock_engine = AsyncMock()
        mock_engine.run_rule = AsyncMock(return_value={"success": True})

        with patch("auto_creation_engine.get_auto_creation_engine", return_value=mock_engine):
            response = await async_client.post(f"/api/auto-creation/rules/{rule.id}/run")

        assert response.status_code == 202, response.text
        body = response.json()
        assert "execution_id" in body
        assert body["status"] == "running"
        assert body["rule_id"] == rule.id
        execution_id = body["execution_id"]

        # Yield to let background task run
        for _ in range(20):
            await _asyncio.sleep(0)

        mock_engine.run_rule.assert_called()
        call_kwargs = mock_engine.run_rule.call_args.kwargs
        assert call_kwargs["rule_id"] == rule.id
        assert call_kwargs["dry_run"] is False
        assert call_kwargs["triggered_by"] == "api"
        assert call_kwargs["execution_id"] == execution_id

    @pytest.mark.asyncio
    async def test_returns_404_for_unknown_rule(self, async_client):
        """Pre-validation rejects unknown rule_id with a clean 404 (so the
        FK-constrained execution row is never even attempted)."""
        response = await async_client.post("/api/auto-creation/rules/99999/run")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_rule_run_failure_marks_execution_failed(self, async_client, test_session):
        """Background failure on per-rule run is captured to the execution record."""
        import asyncio as _asyncio
        rule = _create_rule(test_session, name="BoomRule")

        async def boom(*args, **kwargs):
            raise ValueError("rule borked")

        mock_engine = AsyncMock()
        mock_engine.run_rule = AsyncMock(side_effect=boom)

        with patch("auto_creation_engine.get_auto_creation_engine", return_value=mock_engine):
            response = await async_client.post(f"/api/auto-creation/rules/{rule.id}/run")

        assert response.status_code == 202
        execution_id = response.json()["execution_id"]

        for _ in range(50):
            await _asyncio.sleep(0)

        from models import AutoCreationExecution
        test_session.expire_all()
        exe = test_session.query(AutoCreationExecution).filter_by(id=execution_id).first()
        assert exe is not None
        assert exe.status == "failed"
        assert exe.error_message and "rule borked" in exe.error_message
        assert exe.rule_id == rule.id


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


class TestDebugBundle:
    """Tests for POST /api/auto-creation/debug-bundle and GET /{job_id} (bd-cns7j 202+poll)."""

    @pytest.fixture(autouse=True)
    def _clear_jobs(self):
        # Each test starts with an empty job dict so state never leaks across
        # tests (the dict is module-level by design so the in-memory job
        # lookup survives between requests within a single process).
        from routers import auto_creation as router_module

        router_module._DEBUG_BUNDLE_JOBS.clear()
        yield
        router_module._DEBUG_BUNDLE_JOBS.clear()

    @pytest.mark.asyncio
    async def test_post_returns_202_and_job_id(self, async_client):
        """POST /debug-bundle enqueues work and returns 202 + job_id immediately."""
        import asyncio as _asyncio

        gate = _asyncio.Event()

        async def slow_build():
            await gate.wait()
            return ("ecm-debug-bundle.tar.gz", b"fake-tar-gz")

        with patch("routers.auto_creation._build_debug_bundle", side_effect=slow_build):
            response = await async_client.post("/api/auto-creation/debug-bundle")
            assert response.status_code == 202, response.text
            body = response.json()
            assert "job_id" in body and body["job_id"]
            assert body["status"] == "running"
            job_id = body["job_id"]

            # The job should already exist with status="running" before the build finishes.
            from routers.auto_creation import _DEBUG_BUNDLE_JOBS
            assert job_id in _DEBUG_BUNDLE_JOBS
            assert _DEBUG_BUNDLE_JOBS[job_id].status == "running"

            # Release the build and let it complete.
            gate.set()
            for _ in range(20):
                await _asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_get_while_running_returns_status_json(self, async_client):
        """GET /{job_id} returns JSON status while the build is still running."""
        import asyncio as _asyncio

        gate = _asyncio.Event()

        async def slow_build():
            await gate.wait()
            return ("ecm-debug-bundle.tar.gz", b"fake")

        try:
            with patch("routers.auto_creation._build_debug_bundle", side_effect=slow_build):
                enqueue = await async_client.post("/api/auto-creation/debug-bundle")
                job_id = enqueue.json()["job_id"]

                response = await async_client.get(f"/api/auto-creation/debug-bundle/{job_id}")
                assert response.status_code == 200
                assert response.headers.get("content-type", "").startswith("application/json")
                body = response.json()
                assert body["status"] == "running"
                assert body["job_id"] == job_id
        finally:
            gate.set()
            for _ in range(20):
                await _asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_get_after_completion_returns_binary_and_evicts_job(self, async_client):
        """Once complete, GET /{job_id} returns the tar.gz bytes and removes the job."""
        import asyncio as _asyncio

        async def fast_build():
            return ("ecm-debug-bundle-test.tar.gz", b"\x1f\x8btar-bytes")

        with patch("routers.auto_creation._build_debug_bundle", side_effect=fast_build):
            enqueue = await async_client.post("/api/auto-creation/debug-bundle")
            job_id = enqueue.json()["job_id"]

            # Drain the background task so the job reaches "completed".
            for _ in range(50):
                await _asyncio.sleep(0)

            response = await async_client.get(f"/api/auto-creation/debug-bundle/{job_id}")
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("application/gzip")
            disposition = response.headers["content-disposition"]
            assert "ecm-debug-bundle-test.tar.gz" in disposition
            assert response.content == b"\x1f\x8btar-bytes"

            # Single-shot read — job must be evicted so RAM is freed.
            from routers.auto_creation import _DEBUG_BUNDLE_JOBS
            assert job_id not in _DEBUG_BUNDLE_JOBS

    @pytest.mark.asyncio
    async def test_get_failed_job_returns_status_json(self, async_client):
        """A build that raises is marked failed and exposed via GET status."""
        import asyncio as _asyncio

        async def boom():
            raise RuntimeError("dispatcharr unreachable")

        with patch("routers.auto_creation._build_debug_bundle", side_effect=boom):
            enqueue = await async_client.post("/api/auto-creation/debug-bundle")
            job_id = enqueue.json()["job_id"]

            for _ in range(50):
                await _asyncio.sleep(0)

            response = await async_client.get(f"/api/auto-creation/debug-bundle/{job_id}")
            assert response.status_code == 200
            body = response.json()
            assert body["status"] == "failed"
            assert "dispatcharr unreachable" in body["error"]
            # Failed jobs stay in the dict until the TTL prune so the operator
            # can re-poll and see the error message; eviction happens only on
            # successful binary download.
            from routers.auto_creation import _DEBUG_BUNDLE_JOBS
            assert job_id in _DEBUG_BUNDLE_JOBS

    @pytest.mark.asyncio
    async def test_get_unknown_job_id_returns_404(self, async_client):
        response = await async_client.get("/api/auto-creation/debug-bundle/does-not-exist")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_post_returns_within_timeout_budget(self, async_client):
        """The handler itself must return fast — the whole point of bd-cns7j is
        to make /debug-bundle not synchronous on large catalogs."""
        import asyncio as _asyncio
        import time as _time

        gate = _asyncio.Event()

        async def slow_build():
            await gate.wait()
            return ("ecm-debug-bundle.tar.gz", b"")

        try:
            with patch("routers.auto_creation._build_debug_bundle", side_effect=slow_build):
                start = _time.monotonic()
                response = await async_client.post("/api/auto-creation/debug-bundle")
                elapsed = _time.monotonic() - start

            assert response.status_code == 202
            assert elapsed < 5.0, f"enqueue took {elapsed:.2f}s — handler is not async-enqueuing"
        finally:
            gate.set()
            for _ in range(20):
                await _asyncio.sleep(0)

    def test_prune_drops_expired_jobs(self):
        """_prune_old_debug_bundle_jobs evicts jobs older than the TTL."""
        from routers import auto_creation as router_module

        old = router_module._DebugBundleJob()
        old.created_at = router_module.time.time() - (router_module._DEBUG_BUNDLE_JOB_TTL_SECONDS + 60)
        fresh = router_module._DebugBundleJob()
        router_module._DEBUG_BUNDLE_JOBS["old"] = old
        router_module._DEBUG_BUNDLE_JOBS["fresh"] = fresh

        router_module._prune_old_debug_bundle_jobs()

        assert "old" not in router_module._DEBUG_BUNDLE_JOBS
        assert "fresh" in router_module._DEBUG_BUNDLE_JOBS

    @pytest.mark.asyncio
    async def test_bundle_includes_normalization_rules_yaml(self, async_client, test_session):
        """bd-cns7j follow-up: normalization_rules.yaml is in the tarball with
        the user's group + rule definitions so 'normalization isn't stripping
        X' reports can be diagnosed from the bundle alone."""
        import asyncio as _asyncio
        import io as _io
        import tarfile as _tarfile
        import yaml as _yaml
        from models import NormalizationRule, NormalizationRuleGroup

        # Seed a representative group + rule pair (mirrors a typical "strip
        # country prefix" rule the user would author).
        group = NormalizationRuleGroup(
            name="Country Prefixes",
            description="Strip DE:/AT:/MG: leading prefixes",
            enabled=True,
            priority=0,
            is_builtin=False,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        test_session.add(group)
        test_session.commit()
        test_session.refresh(group)

        rule = NormalizationRule(
            group_id=group.id,
            name="Strip DE/AT/MG prefix",
            description=None,
            enabled=True,
            priority=0,
            condition_type="regex",
            condition_value=r"^(DE|AT|MG)\s*:\s*",
            case_sensitive=False,
            condition_logic="AND",
            action_type="remove",
            action_value=None,
            stop_processing=False,
            is_builtin=False,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        test_session.add(rule)
        test_session.commit()

        # Mock the Dispatcharr client + heavy bundle dependencies so we can
        # exercise the assembly end-to-end without standing up a fake server.
        mock_client = AsyncMock()
        mock_client.get_channels = AsyncMock(return_value={"results": [], "next": None, "count": 0})
        mock_client.get_channel_groups = AsyncMock(return_value=[])
        mock_client.get_streams_by_ids = AsyncMock(return_value=[])
        mock_client.get_m3u_accounts = AsyncMock(return_value=[])

        with patch("routers.auto_creation.get_client", return_value=mock_client), \
             patch("log_utils.get_recent_logs", return_value=[]):
            enqueue = await async_client.post("/api/auto-creation/debug-bundle")
            assert enqueue.status_code == 202
            job_id = enqueue.json()["job_id"]
            for _ in range(80):
                await _asyncio.sleep(0)

            response = await async_client.get(f"/api/auto-creation/debug-bundle/{job_id}")
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("application/gzip")

        with _tarfile.open(fileobj=_io.BytesIO(response.content), mode="r:gz") as tf:
            names = tf.getnames()
            assert "normalization_rules.yaml" in names, names

            extracted = tf.extractfile("normalization_rules.yaml")
            assert extracted is not None
            payload = _yaml.safe_load(extracted.read().decode("utf-8"))

        assert payload["version"] == 1
        assert "exported_at" in payload
        assert len(payload["groups"]) == 1
        g = payload["groups"][0]
        assert g["id"] == group.id, "group id is preserved so rules.yaml's normalization_group_ids resolves"
        assert g["name"] == "Country Prefixes"
        assert g["enabled"] is True
        assert g["rule_count"] == 1
        assert len(g["rules"]) == 1
        r = g["rules"][0]
        assert r["name"] == "Strip DE/AT/MG prefix"
        assert r["condition_type"] == "regex"
        assert r["condition_value"] == r"^(DE|AT|MG)\s*:\s*"
        assert r["action_type"] == "remove"
        # Numeric ids and timestamps deliberately stripped from rule body —
        # they aren't useful for diagnosis and add noise.
        assert "id" not in r
        assert "created_at" not in r
        assert "updated_at" not in r

        # Manifest reflects the new counts so reviewers don't have to grep the YAML.
        manifest_bytes = None
        with _tarfile.open(fileobj=_io.BytesIO(response.content), mode="r:gz") as tf:
            manifest_bytes = tf.extractfile("manifest.json").read()
        manifest = json.loads(manifest_bytes)
        assert manifest["normalization_group_count"] == 1
        assert manifest["normalization_rule_count"] == 1


# =========================================================================
# Rule analyzer endpoints (bd-0gntx).
#
# /api/auto-creation/rules/analyze            — analyze rules in DB
# /api/auto-creation/rules/analyze/from-bundle — analyze rules.yaml from
#                                                 an uploaded debug bundle
# Both reuse auto_creation_rule_analyzer.analyze_rules; the router is a
# thin adapter (DB→dict, or tar.gz→yaml→dict).
# =========================================================================


def _make_debug_bundle_bytes(
    rules_yaml: str | None,
    *,
    diagnostic: dict | None = None,
) -> bytes:
    """Build a minimal in-memory debug bundle tar.gz for tests.

    Mirrors the production bundle layout (rules.yaml at the root, plus
    optional channel_groups_diagnostic.json). ``rules_yaml=None`` omits
    the file so we can assert the 400 error path.
    """
    import io as _io
    import json as _json
    import tarfile as _tarfile

    buf = _io.BytesIO()
    with _tarfile.open(fileobj=buf, mode="w:gz") as tf:
        if rules_yaml is not None:
            data = rules_yaml.encode("utf-8")
            info = _tarfile.TarInfo(name="rules.yaml")
            info.size = len(data)
            tf.addfile(info, _io.BytesIO(data))
        if diagnostic is not None:
            data = _json.dumps(diagnostic).encode("utf-8")
            info = _tarfile.TarInfo(name="channel_groups_diagnostic.json")
            info.size = len(data)
            tf.addfile(info, _io.BytesIO(data))
    return buf.getvalue()


# The 2026-04-28 user's broken Sports rule, as it lives in rules.yaml.
_SPORTS_RULE_YAML = """
version: 1
rules:
- name: Sports Networks - excl Fr and Es
  enabled: true
  priority: 1
  conditions:
  - type: normalized_name_in_group
    value: 1464
    connector: and
  - type: stream_group_matches
    value: UK|
    connector: and
  - type: stream_group_matches
    value: US|
    connector: or
  - type: stream_group_contains
    value: '^4K'
    connector: or
  actions:
  - type: merge_streams
    target: auto
"""


# A clean rule — must produce zero findings.
_CLEAN_RULE_YAML = r"""
version: 1
rules:
- name: Movie Networks - UK add
  enabled: true
  priority: 2
  conditions:
  - type: normalized_name_in_group
    value: 1473
    connector: and
  - type: stream_group_matches
    value: ^UK\|
    connector: and
  actions:
  - type: merge_streams
    target: auto
"""


class TestAnalyzeRulesLive:
    """POST /api/auto-creation/rules/analyze — analyze rules in DB."""

    @pytest.mark.asyncio
    async def test_empty_db_returns_clean_summary(self, async_client):
        response = await async_client.post(
            "/api/auto-creation/rules/analyze"
        )
        assert response.status_code == 200
        body = response.json()
        assert body["rules"] == []
        assert body["summary"] == {"error": 0, "warning": 0, "info": 0}

    @pytest.mark.asyncio
    async def test_broken_rule_surfaces_findings(self, async_client, test_session):
        _create_rule(
            test_session,
            name="Sports Networks - excl Fr and Es",
            conditions=json.dumps([
                {"type": "normalized_name_in_group", "value": 1464, "connector": "and"},
                {"type": "stream_group_matches", "value": "UK|", "connector": "and"},
                {"type": "stream_group_matches", "value": "US|", "connector": "or"},
                {"type": "stream_group_contains", "value": "^4K", "connector": "or"},
            ]),
            actions=json.dumps([{"type": "merge_streams", "target": "auto"}]),
        )
        response = await async_client.post(
            "/api/auto-creation/rules/analyze"
        )
        assert response.status_code == 200
        body = response.json()
        assert body["summary"]["warning"] >= 1
        codes = {f["code"] for r in body["rules"] for f in r["findings"]}
        # All four finding categories surfaced by this rule:
        assert "REGEX_TRIVIALLY_MATCHES_ALL" in codes
        assert "OPERATOR_VALUE_LOOKS_LIKE_REGEX" in codes
        assert "ANDOR_DROPS_GUARD" in codes

    @pytest.mark.asyncio
    async def test_clean_rule_produces_no_findings(self, async_client, test_session):
        _create_rule(
            test_session,
            name="Movie Networks - UK add",
            conditions=json.dumps([
                {"type": "normalized_name_in_group", "value": 1473, "connector": "and"},
                {"type": "stream_group_matches", "value": r"^UK\|", "connector": "and"},
            ]),
            actions=json.dumps([{"type": "merge_streams", "target": "auto"}]),
        )
        response = await async_client.post(
            "/api/auto-creation/rules/analyze"
        )
        body = response.json()
        assert body["summary"]["warning"] == 0
        assert body["rules"][0]["findings"] == []


class TestAnalyzeRulesFromBundle:
    """POST /api/auto-creation/rules/analyze/from-bundle — analyze
    rules.yaml from a debug-bundle tar.gz. The endpoint never touches
    the DB.
    """

    @pytest.mark.asyncio
    async def test_bundle_with_broken_rule(self, async_client):
        bundle = _make_debug_bundle_bytes(_SPORTS_RULE_YAML)
        response = await async_client.post(
            "/api/auto-creation/rules/analyze/from-bundle",
            files={"file": ("debug.tar.gz", bundle, "application/gzip")},
        )
        assert response.status_code == 200
        body = response.json()
        codes = {f["code"] for r in body["rules"] for f in r["findings"]}
        assert "REGEX_TRIVIALLY_MATCHES_ALL" in codes
        assert "OPERATOR_VALUE_LOOKS_LIKE_REGEX" in codes
        assert "ANDOR_DROPS_GUARD" in codes

    @pytest.mark.asyncio
    async def test_bundle_with_clean_rule(self, async_client):
        bundle = _make_debug_bundle_bytes(_CLEAN_RULE_YAML)
        response = await async_client.post(
            "/api/auto-creation/rules/analyze/from-bundle",
            files={"file": ("debug.tar.gz", bundle, "application/gzip")},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["summary"]["warning"] == 0

    @pytest.mark.asyncio
    async def test_bundle_with_diagnostic_flags_empty_target_group(
        self, async_client,
    ):
        rules_yaml = """
version: 1
rules:
- name: Empty target rule
  conditions: []
  actions:
  - type: merge_streams
    target: auto
  target_group_id: 99
"""
        diagnostic = {"groups": [{"id": 99, "name": "Empty", "channel_count": 0}]}
        bundle = _make_debug_bundle_bytes(rules_yaml, diagnostic=diagnostic)
        response = await async_client.post(
            "/api/auto-creation/rules/analyze/from-bundle",
            files={"file": ("debug.tar.gz", bundle, "application/gzip")},
        )
        assert response.status_code == 200
        body = response.json()
        codes = {f["code"] for r in body["rules"] for f in r["findings"]}
        assert "MERGE_STREAMS_NO_TARGET_CHANNELS" in codes

    @pytest.mark.asyncio
    async def test_bundle_missing_rules_yaml_returns_400(self, async_client):
        bundle = _make_debug_bundle_bytes(None)
        response = await async_client.post(
            "/api/auto-creation/rules/analyze/from-bundle",
            files={"file": ("debug.tar.gz", bundle, "application/gzip")},
        )
        assert response.status_code == 400
        assert "rules.yaml" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_non_targz_returns_400(self, async_client):
        response = await async_client.post(
            "/api/auto-creation/rules/analyze/from-bundle",
            files={"file": ("not-a-bundle.txt", b"hello", "text/plain")},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_invalid_yaml_returns_400(self, async_client):
        bundle = _make_debug_bundle_bytes("not: valid: yaml: at: all: :")
        response = await async_client.post(
            "/api/auto-creation/rules/analyze/from-bundle",
            files={"file": ("debug.tar.gz", bundle, "application/gzip")},
        )
        assert response.status_code == 400
