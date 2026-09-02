"""
Unit tests for auto-creation endpoints.

Tests: rule CRUD, bulk-update, reorder, toggle, duplicate,
       pipeline execution, execution history, rollback, YAML import/export,
       validation, and schema endpoints.
Mocks: channel_pipeline_engine, channel_pipeline_schema, get_client(), get_session().
"""
import json
import pytest
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from models import ChannelPipelineRule, ChannelPipelineExecution, NormalizationRuleGroup
from routers.channel_pipeline import (
    BulkUpdateChannelPipelineRulesRequest,
    CreateChannelPipelineRuleRequest,
    UpdateChannelPipelineRuleRequest,
)


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
    """Helper to create an ChannelPipelineRule."""
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
    rule = ChannelPipelineRule(**defaults)
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return rule


def _create_execution(session, **overrides):
    """Helper to create an ChannelPipelineExecution."""
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
    execution = ChannelPipelineExecution(**defaults)
    session.add(execution)
    session.commit()
    session.refresh(execution)
    return execution


@pytest.mark.parametrize("value", [[True, 2], ["1", 2], [1.0, 2]])
@pytest.mark.parametrize(
    "model,payload",
    [
        (CreateChannelPipelineRuleRequest, {
            "name": "Strict providers", "conditions": [], "actions": [],
        }),
        (UpdateChannelPipelineRuleRequest, {}),
        (BulkUpdateChannelPipelineRulesRequest, {"rule_ids": [1]}),
    ],
)
def test_request_models_reject_coerced_required_provider_ids(model, payload, value):
    with pytest.raises(ValueError):
        model(**payload, required_provider_ids=value)


class TestGetChannelPipelineRules:
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

    @pytest.mark.asyncio
    async def test_malformed_required_provider_state_does_not_break_rule_reads(
        self, async_client, test_session
    ):
        _create_rule(test_session, name="Malformed", required_provider_ids="{}")

        response = await async_client.get("/api/auto-creation/rules")

        assert response.status_code == 200
        assert response.json()["rules"][0]["required_provider_ids"] == []


class TestGetChannelPipelineRule:
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


class TestCreateChannelPipelineRule:
    """Tests for POST /api/auto-creation/rules."""

    @pytest.mark.asyncio
    async def test_creates_rule(self, async_client):
        """Creates a new auto-creation rule."""
        with patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.channel_pipeline.journal"):
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
    async def test_round_trips_required_provider_ids(self, async_client):
        provider_client = MagicMock()
        provider_client.get_m3u_accounts = AsyncMock(return_value=[
            {"id": 11, "name": "Primary"},
            {"id": 22, "name": "Backup"},
        ])
        with patch("routers.channel_pipeline.get_client", return_value=provider_client), \
             patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.channel_pipeline.journal"):
            response = await async_client.post("/api/auto-creation/rules", json={
                "name": "Redundant sports",
                "conditions": [{"type": "always"}],
                "actions": [{"type": "create_channel", "name_template": "{stream_name}"}],
                "required_provider_ids": [22, 11, 22],
            })

        assert response.status_code == 200, response.text
        assert response.json()["required_provider_ids"] == [11, 22]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("value", "expected_status"),
        [([11], 422), ([11, "bad"], 422), ([11, 999], 422)],
        ids=["fewer-than-two", "malformed", "missing-reference"],
    )
    async def test_rejects_invalid_required_provider_ids(
        self, async_client, value, expected_status
    ):
        provider_client = MagicMock()
        provider_client.get_m3u_accounts = AsyncMock(return_value=[
            {"id": 11, "name": "Primary"},
            {"id": 22, "name": "Backup"},
        ])
        with patch("routers.channel_pipeline.get_client", return_value=provider_client):
            response = await async_client.post("/api/auto-creation/rules", json={
                "name": "Invalid coverage",
                "conditions": [{"type": "always"}],
                "actions": [{"type": "create_channel", "name_template": "{stream_name}"}],
                "required_provider_ids": value,
            })

        assert response.status_code == expected_status
        if value == [11, 999]:
            assert response.json()["detail"]["invalid_required_provider_ids"] == [999]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", [[True, 2], ["1", 2], [1.0, 2]])
    async def test_rejects_coerced_required_provider_ids_without_creating(
        self, async_client, test_session, value
    ):
        response = await async_client.post("/api/auto-creation/rules", json={
            "name": "Coerced coverage",
            "conditions": [{"type": "always"}],
            "actions": [{"type": "skip"}],
            "required_provider_ids": value,
        })

        assert response.status_code == 422
        assert test_session.query(ChannelPipelineRule).count() == 0

    @pytest.mark.asyncio
    async def test_round_trips_active_date_window(self, async_client):
        with patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.channel_pipeline.journal"):
            response = await async_client.post("/api/auto-creation/rules", json={
                "name": "Football season",
                "conditions": [{"type": "always"}],
                "actions": [{"type": "skip"}],
                "active_from": "2026-09-01",
                "active_until": "2027-02-15",
            })

        assert response.status_code == 200, response.text
        assert response.json()["active_from"] == "2026-09-01"
        assert response.json()["active_until"] == "2027-02-15"

    @pytest.mark.asyncio
    async def test_rejects_active_window_with_end_before_start(self, async_client):
        response = await async_client.post("/api/auto-creation/rules", json={
            "name": "Invalid season",
            "conditions": [{"type": "always"}],
            "actions": [{"type": "skip"}],
            "active_from": "2027-02-15",
            "active_until": "2026-09-01",
        })

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_rejects_invalid_rule(self, async_client):
        """Returns 400 for invalid rule configuration."""
        with patch("channel_pipeline_schema.validate_rule", return_value={
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

        with patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.channel_pipeline.journal"):
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
        with patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.channel_pipeline.journal"):
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


class TestUpdateChannelPipelineRule:
    """Tests for PUT /api/auto-creation/rules/{rule_id}."""

    @pytest.mark.asyncio
    async def test_updates_rule(self, async_client, test_session):
        """Updates an auto-creation rule."""
        rule = _create_rule(test_session, name="Old Name")

        with patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.channel_pipeline.journal"):
            response = await async_client.put(
                f"/api/auto-creation/rules/{rule.id}",
                json={"name": "New Name"},
            )

        assert response.status_code == 200
        assert response.json()["name"] == "New Name"

    @pytest.mark.asyncio
    async def test_updates_and_clears_required_provider_ids(
        self, async_client, test_session
    ):
        rule = _create_rule(test_session, name="Coverage update")
        provider_client = MagicMock()
        provider_client.get_m3u_accounts = AsyncMock(return_value=[
            {"id": 11, "name": "Primary"},
            {"id": 22, "name": "Backup"},
        ])
        with patch("routers.channel_pipeline.get_client", return_value=provider_client), \
             patch("routers.channel_pipeline.journal"):
            updated = await async_client.put(
                f"/api/auto-creation/rules/{rule.id}",
                json={"required_provider_ids": [22, 11]},
            )
            cleared = await async_client.put(
                f"/api/auto-creation/rules/{rule.id}",
                json={"required_provider_ids": []},
            )

        assert updated.status_code == 200, updated.text
        assert updated.json()["required_provider_ids"] == [11, 22]
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["required_provider_ids"] == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", [[True, 2], ["1", 2], [1.0, 2]])
    async def test_rejects_coerced_required_providers_atomically(
        self, async_client, test_session, value
    ):
        rule = _create_rule(test_session, name="Unchanged", enabled=False)

        response = await async_client.put(
            f"/api/auto-creation/rules/{rule.id}",
            json={"name": "Mutated", "enabled": True, "required_provider_ids": value},
        )

        assert response.status_code == 422
        test_session.expire_all()
        stored = test_session.get(ChannelPipelineRule, rule.id)
        assert stored.name == "Unchanged"
        assert stored.enabled is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "stream_sort_field",
        [None, "smart_sort", "quality", "stream_name", "stream_name_natural", "provider_order"],
    )
    async def test_stream_sort_round_trips_through_update_and_reopen(
        self, async_client, test_session, stream_sort_field
    ):
        """GH #833: the persisted API value is authoritative on reopen."""
        rule = _create_rule(test_session, stream_sort_field="smart_sort")
        payload_value = stream_sort_field if stream_sort_field is not None else ""

        with patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.channel_pipeline.journal"):
            updated = await async_client.put(
                f"/api/auto-creation/rules/{rule.id}",
                json={"stream_sort_field": payload_value},
            )
        reopened = await async_client.get(f"/api/auto-creation/rules/{rule.id}")

        assert updated.status_code == 200, updated.text
        assert reopened.status_code == 200, reopened.text
        assert updated.json()["stream_sort_field"] == stream_sort_field
        assert reopened.json()["stream_sort_field"] == stream_sort_field

    @pytest.mark.asyncio
    async def test_clears_active_window_and_rejects_cross_field_reversal(self, async_client, test_session):
        rule = _create_rule(test_session, name="Windowed", active_from=date(2026, 9, 1),
                            active_until=date(2027, 2, 15))
        with patch("routers.channel_pipeline.journal"):
            rejected = await async_client.put(
                f"/api/auto-creation/rules/{rule.id}",
                json={"active_until": "2026-08-31"},
            )
        assert rejected.status_code == 400
        test_session.expire_all()
        assert test_session.get(ChannelPipelineRule, rule.id).active_until == date(2027, 2, 15)

        with patch("routers.channel_pipeline.journal"):
            cleared = await async_client.put(
                f"/api/auto-creation/rules/{rule.id}",
                json={"active_from": None, "active_until": None},
            )
        assert cleared.status_code == 200
        assert cleared.json()["active_from"] is None
        assert cleared.json()["active_until"] is None

    @pytest.mark.asyncio
    async def test_returns_404(self, async_client):
        """Returns 404 for nonexistent rule."""
        with patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}):
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

        with patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.channel_pipeline.journal"):
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

        with patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.channel_pipeline.journal"):
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

        with patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.channel_pipeline.journal"):
            # Update an unrelated field — must succeed even though stored
            # normalization_group_ids reference a missing group.
            response = await async_client.put(
                f"/api/auto-creation/rules/{rule.id}",
                json={"name": "Renamed"},
            )

        assert response.status_code == 200, response.text
        assert response.json()["name"] == "Renamed"


class TestMatchScopeGroupIdPersistence:
    """GH #298 / bd-kncun: persistence + round-trip of ``match_scope_group_id``.

    The new explicit scope-group column must survive create, be readable via
    GET, be settable AND clearable-to-NULL via PUT (the "Auto" choice), and be
    included in the YAML export so import/restore round-trips it.
    """

    @pytest.mark.asyncio
    async def test_create_persists_scope_group_and_get_round_trips(
        self, async_client
    ):
        """POST with match_scope_group_id stores it; GET returns it."""
        with patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.channel_pipeline.journal"):
            create = await async_client.post("/api/auto-creation/rules", json={
                "name": "Scoped Rule",
                "conditions": [{"type": "stream_name_contains", "value": "ESPN"}],
                "actions": [{"type": "merge_streams", "target": "auto"}],
                "match_scope_target_group": True,
                "match_scope_group_id": 7,
            })

        assert create.status_code == 200, create.text
        rule_id = create.json()["id"]
        assert create.json()["match_scope_group_id"] == 7

        get = await async_client.get(f"/api/auto-creation/rules/{rule_id}")
        assert get.status_code == 200
        assert get.json()["match_scope_group_id"] == 7

    @pytest.mark.asyncio
    async def test_create_defaults_scope_group_to_null(self, async_client):
        """POST without the field stores NULL (the Auto default)."""
        with patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.channel_pipeline.journal"):
            create = await async_client.post("/api/auto-creation/rules", json={
                "name": "Unscoped Rule",
                "conditions": [{"type": "stream_name_contains", "value": "CNN"}],
                "actions": [{"type": "create_channel", "name_template": "{stream_name}"}],
            })

        assert create.status_code == 200, create.text
        assert create.json()["match_scope_group_id"] is None

    @pytest.mark.asyncio
    async def test_update_sets_scope_group(self, async_client, test_session):
        """PUT match_scope_group_id stores the value."""
        rule = _create_rule(test_session, name="ToScope")

        with patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.channel_pipeline.journal"):
            response = await async_client.put(
                f"/api/auto-creation/rules/{rule.id}",
                json={"match_scope_group_id": 3},
            )

        assert response.status_code == 200, response.text
        assert response.json()["match_scope_group_id"] == 3

    @pytest.mark.asyncio
    async def test_update_clears_scope_group_to_null(self, async_client, test_session):
        """PUT with explicit null resets to Auto (model_fields_set path).

        The other Optional fields use the ``is not None`` convention which can
        never express "reset to None"; match_scope_group_id uses
        ``model_fields_set`` so an explicit null clears the stored group.
        """
        rule = _create_rule(test_session, name="ScopedThenAuto", match_scope_group_id=5)
        assert rule.match_scope_group_id == 5

        with patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.channel_pipeline.journal"):
            response = await async_client.put(
                f"/api/auto-creation/rules/{rule.id}",
                json={"match_scope_group_id": None},
            )

        assert response.status_code == 200, response.text
        assert response.json()["match_scope_group_id"] is None

    @pytest.mark.asyncio
    async def test_update_omitting_field_leaves_scope_group_unchanged(
        self, async_client, test_session
    ):
        """PUT without the field must not touch the stored scope group."""
        rule = _create_rule(test_session, name="KeepScope", match_scope_group_id=9)

        with patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.channel_pipeline.journal"):
            response = await async_client.put(
                f"/api/auto-creation/rules/{rule.id}",
                json={"name": "Renamed Keep"},
            )

        assert response.status_code == 200, response.text
        assert response.json()["match_scope_group_id"] == 9

    @pytest.mark.asyncio
    async def test_export_includes_scope_group(self, async_client, test_session):
        """The YAML export carries match_scope_group_id for round-trip restore."""
        _create_rule(test_session, name="ExportScoped", match_scope_group_id=4)

        mock_client = AsyncMock()
        mock_client.get_channel_groups.return_value = []
        mock_client.get_m3u_accounts.return_value = []

        with patch("routers.channel_pipeline.get_client", return_value=mock_client):
            response = await async_client.get("/api/auto-creation/export/yaml")

        assert response.status_code == 200
        assert "match_scope_group_id" in response.text


class TestAllowManualChannelMergePersistence:
    """enhancedchannelmanager-orzck (W1): persist + round-trip the new flag.

    The manual-channel isolation flag must survive create, be readable via GET,
    default to False (protected) when omitted, be settable via PUT, and appear
    in the YAML export so import/restore round-trips it.
    """

    @pytest.mark.asyncio
    async def test_create_defaults_flag_to_false(self, async_client):
        with patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.channel_pipeline.journal"):
            create = await async_client.post("/api/auto-creation/rules", json={
                "name": "Protected Rule",
                "conditions": [{"type": "stream_name_contains", "value": "ESPN"}],
                "actions": [{"type": "create_channel", "name_template": "{stream_name}"}],
            })
        assert create.status_code == 200, create.text
        assert create.json()["allow_manual_channel_merge"] is False

    @pytest.mark.asyncio
    async def test_create_persists_opt_in_and_get_round_trips(self, async_client):
        with patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.channel_pipeline.journal"):
            create = await async_client.post("/api/auto-creation/rules", json={
                "name": "Opt-in Rule",
                "conditions": [{"type": "stream_name_contains", "value": "ESPN"}],
                "actions": [{"type": "merge_streams", "target": "auto"}],
                "allow_manual_channel_merge": True,
            })
        assert create.status_code == 200, create.text
        rule_id = create.json()["id"]
        assert create.json()["allow_manual_channel_merge"] is True

        get = await async_client.get(f"/api/auto-creation/rules/{rule_id}")
        assert get.status_code == 200
        assert get.json()["allow_manual_channel_merge"] is True

    @pytest.mark.asyncio
    async def test_update_sets_flag(self, async_client, test_session):
        rule = _create_rule(test_session, name="ToOptIn")
        assert rule.allow_manual_channel_merge is False
        with patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.channel_pipeline.journal"):
            response = await async_client.put(
                f"/api/auto-creation/rules/{rule.id}",
                json={"allow_manual_channel_merge": True},
            )
        assert response.status_code == 200, response.text
        assert response.json()["allow_manual_channel_merge"] is True

    @pytest.mark.asyncio
    async def test_export_includes_flag(self, async_client, test_session):
        _create_rule(test_session, name="ExportFlag", allow_manual_channel_merge=True)
        mock_client = AsyncMock()
        mock_client.get_channel_groups.return_value = []
        mock_client.get_m3u_accounts.return_value = []
        with patch("routers.channel_pipeline.get_client", return_value=mock_client):
            response = await async_client.get("/api/auto-creation/export/yaml")
        assert response.status_code == 200
        assert "allow_manual_channel_merge" in response.text


class TestManualChannelIsolationRun:
    """enhancedchannelmanager-orzck (W1): a real run must not overwrite a
    hand-built MANUAL channel on a name collision.

    Drives the executor against a rule persisted in the real test-session DB,
    with a mocked Dispatcharr client. Proves the bleed fix end-to-end: a
    create_channel if_exists=merge action that name-collides with an existing
    MANUAL channel (auto_created=False) creates a NEW auto channel instead of
    adopting (and mutating) the manual one.
    """

    @pytest.mark.asyncio
    async def test_run_does_not_overwrite_manual_channel(self, test_session):
        from channel_pipeline_executor import ActionExecutor, ExecutionContext
        from channel_pipeline_evaluator import StreamContext

        # Persist a rule with the protective default (flag False) in real SQLite.
        rule = _create_rule(
            test_session,
            name="MergeESPN",
            actions=json.dumps([
                {"type": "create_channel", "name_template": "{stream_name}",
                 "if_exists": "merge"}
            ]),
        )
        assert rule.allow_manual_channel_merge is False

        created = {}

        async def _create_channel(data):
            created["data"] = data
            return {"id": 4242, "name": data["name"],
                    "channel_group_id": data.get("channel_group_id"),
                    "streams": data.get("streams", [])}

        client = MagicMock()
        client.create_channel = AsyncMock(side_effect=_create_channel)
        client.update_channel = AsyncMock(return_value={})
        client.get_channel = AsyncMock(return_value={"id": 99, "streams": [501]})

        # A hand-built MANUAL channel named "ESPN".
        manual = {"id": 99, "name": "ESPN", "channel_number": 100,
                  "channel_group_id": 1, "streams": [501], "auto_created": False}
        executor = ActionExecutor(
            client,
            existing_channels=[manual],
            existing_groups=[{"id": 1, "name": "SPORTS"}],
        )

        action = json.loads(rule.actions)[0]
        stream_ctx = StreamContext(stream_id=502, stream_name="ESPN", m3u_account_id=1)
        result = await executor.execute(
            action, stream_ctx, ExecutionContext(),
            rule_target_group_id=1,
            match_scope_target_group=bool(rule.match_scope_target_group),
            allow_manual_channel_merge=bool(rule.allow_manual_channel_merge),
        )

        assert result.success is True
        # The manual channel (id=99) is byte-identical: never updated/merged.
        for call in client.update_channel.call_args_list:
            assert call[0][0] != 99
        for call in client.get_channel.call_args_list:
            assert call[0][0] != 99
        # A new auto channel was created instead.
        client.create_channel.assert_called_once()
        assert created["data"]["name"] == "ESPN"


class TestBulkUpdateChannelPipelineRules:
    """Tests for POST /api/auto-creation/rules/bulk-update."""

    @pytest.mark.asyncio
    async def test_updates_multiple_rules(self, async_client, test_session):
        """Applies the same scalar updates to several rules."""
        r1 = _create_rule(test_session, name="BulkA", run_on_refresh=False, orphan_action="delete")
        r2 = _create_rule(test_session, name="BulkB", run_on_refresh=False)
        with patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.channel_pipeline.journal"):
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
        assert test_session.query(ChannelPipelineRule).get(r1.id).run_on_refresh is True
        assert test_session.query(ChannelPipelineRule).get(r1.id).orphan_action == "none"
        assert test_session.query(ChannelPipelineRule).get(r2.id).run_on_refresh is True

    @pytest.mark.asyncio
    async def test_updates_required_providers_for_all_rules(self, async_client, test_session):
        r1 = _create_rule(test_session, name="Bulk providers A")
        r2 = _create_rule(test_session, name="Bulk providers B")
        provider_client = MagicMock()
        provider_client.get_m3u_accounts = AsyncMock(return_value=[
            {"id": 11, "name": "Primary"},
            {"id": 22, "name": "Backup"},
        ])

        with patch("routers.channel_pipeline.get_client", return_value=provider_client), \
             patch("routers.channel_pipeline.journal"):
            response = await async_client.post(
                "/api/auto-creation/rules/bulk-update",
                json={
                    "rule_ids": [r1.id, r2.id],
                    "required_provider_ids": [22, 11],
                },
            )

        assert response.status_code == 200, response.text
        test_session.expire_all()
        assert test_session.get(ChannelPipelineRule, r1.id).get_required_provider_ids() == [11, 22]
        assert test_session.get(ChannelPipelineRule, r2.id).get_required_provider_ids() == [11, 22]

    @pytest.mark.asyncio
    async def test_rejects_missing_bulk_required_provider_before_any_mutation(
        self, async_client, test_session
    ):
        r1 = _create_rule(test_session, name="Bulk invalid provider A", enabled=False)
        r2 = _create_rule(test_session, name="Bulk invalid provider B", enabled=False)
        provider_client = MagicMock()
        provider_client.get_m3u_accounts = AsyncMock(return_value=[
            {"id": 11, "name": "Primary"},
            {"id": 22, "name": "Backup"},
        ])
        mock_journal = MagicMock()

        with patch("routers.channel_pipeline.get_client", return_value=provider_client), \
             patch("routers.channel_pipeline.journal", mock_journal):
            response = await async_client.post(
                "/api/auto-creation/rules/bulk-update",
                json={
                    "rule_ids": [r1.id, r2.id],
                    "required_provider_ids": [11, 999],
                    "enabled": True,
                },
            )

        assert response.status_code == 422, response.text
        assert response.json()["detail"]["invalid_required_provider_ids"] == [999]
        assert mock_journal.log_entry.call_count == 0
        test_session.expire_all()
        assert test_session.get(ChannelPipelineRule, r1.id).enabled is False
        assert test_session.get(ChannelPipelineRule, r2.id).enabled is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", [[True, 2], ["1", 2], [1.0, 2]])
    async def test_rejects_coerced_required_providers_atomically(
        self, async_client, test_session, value
    ):
        r1 = _create_rule(test_session, name="Bulk strict A", enabled=False)
        r2 = _create_rule(test_session, name="Bulk strict B", enabled=False)

        response = await async_client.post(
            "/api/auto-creation/rules/bulk-update",
            json={
                "rule_ids": [r1.id, r2.id],
                "enabled": True,
                "required_provider_ids": value,
            },
        )

        assert response.status_code == 422
        test_session.expire_all()
        assert test_session.get(ChannelPipelineRule, r1.id).enabled is False
        assert test_session.get(ChannelPipelineRule, r2.id).enabled is False

    @pytest.mark.asyncio
    async def test_clears_windows_and_rejects_atomically(self, async_client, test_session):
        r1 = _create_rule(test_session, name="BulkWindowA", active_from=date(2026, 9, 1),
                          active_until=date(2027, 2, 15))
        r2 = _create_rule(test_session, name="BulkWindowB", active_from=date(2026, 1, 1),
                          active_until=date(2026, 12, 31))
        with patch("routers.channel_pipeline.journal"):
            rejected = await async_client.post("/api/auto-creation/rules/bulk-update", json={
                "rule_ids": [r1.id, r2.id], "active_until": "2026-08-31",
            })
        assert rejected.status_code == 400
        test_session.expire_all()
        assert test_session.get(ChannelPipelineRule, r1.id).active_until == date(2027, 2, 15)
        assert test_session.get(ChannelPipelineRule, r2.id).active_until == date(2026, 12, 31)

        with patch("routers.channel_pipeline.journal"):
            cleared = await async_client.post("/api/auto-creation/rules/bulk-update", json={
                "rule_ids": [r1.id, r2.id], "active_from": None, "active_until": None,
            })
        assert cleared.status_code == 200
        assert all(item["active_from"] is None and item["active_until"] is None
                   for item in cleared.json()["rules"])

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
        with patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.channel_pipeline.journal"):
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
        with patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.channel_pipeline.journal"):
            response = await async_client.post("/api/auto-creation/rules/bulk-update", json={
                "rule_ids": [r1.id, r2.id, r3.id, missing_id],
                "enabled": False,
            })

        assert response.status_code == 404

        test_session.expire_all()
        assert test_session.query(ChannelPipelineRule).get(r1.id).enabled is True
        assert test_session.query(ChannelPipelineRule).get(r2.id).enabled is True
        assert test_session.query(ChannelPipelineRule).get(r3.id).enabled is True

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
        with patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.channel_pipeline.journal"):
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
        with patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.channel_pipeline.journal"):
            response = await async_client.post("/api/auto-creation/rules/bulk-update", json={
                "rule_ids": [r.id],
                "merge_streams_remove_non_matching": True,
            })
        assert response.status_code == 200
        test_session.expire_all()
        rule = test_session.query(ChannelPipelineRule).get(r.id)
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

        with patch("routers.channel_pipeline.journal"):
            response = await async_client.post(
                "/api/auto-creation/rules/bulk-update",
                json={"rule_ids": [rule.id], "enabled": True},
            )

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["updated_count"] == 1

        test_session.expire_all()
        refreshed = test_session.query(ChannelPipelineRule).get(rule.id)
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

        with patch("routers.channel_pipeline.journal"):
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
        refreshed = test_session.query(ChannelPipelineRule).get(rule.id)
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
        with patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.channel_pipeline.journal"):
            response = await async_client.post(
                "/api/auto-creation/rules/bulk-update",
                json={"rule_ids": [r.id], "enabled": True, "priority": 5},
            )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["updated_count"] == 1
        test_session.expire_all()
        refreshed = test_session.query(ChannelPipelineRule).get(r.id)
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
        with patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.channel_pipeline.journal", mock_journal):
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
        with patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.channel_pipeline.journal", mock_journal):
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
        with patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.channel_pipeline.journal", mock_journal):
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

        with patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.channel_pipeline.journal"):
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
            refreshed = test_session.query(ChannelPipelineRule).get(rid)
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

        with patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.channel_pipeline.journal"):
            response = await async_client.post(
                "/api/auto-creation/rules/bulk-update",
                json={
                    "rule_ids": [r1.id],
                    "normalization_group_ids": [],
                },
            )

        assert response.status_code == 200, response.text
        test_session.expire_all()
        refreshed = test_session.query(ChannelPipelineRule).get(r1.id)
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
        with patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.channel_pipeline.journal", mock_journal):
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
            refreshed = test_session.query(ChannelPipelineRule).get(rid)
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

        with patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.channel_pipeline.journal"):
            response = await async_client.post(
                "/api/auto-creation/rules/bulk-update",
                json={"rule_ids": [r1.id], "enabled": True},
            )

        assert response.status_code == 200, response.text
        test_session.expire_all()
        refreshed = test_session.query(ChannelPipelineRule).get(r1.id)
        assert refreshed.enabled is True


class TestDeleteChannelPipelineRule:
    """Tests for DELETE /api/auto-creation/rules/{rule_id}."""

    @pytest.mark.asyncio
    async def test_deletes_rule(self, async_client, test_session):
        """Deletes an auto-creation rule."""
        rule = _create_rule(test_session)
        rule_id = rule.id

        with patch("routers.channel_pipeline.journal"):
            response = await async_client.delete(f"/api/auto-creation/rules/{rule_id}")

        assert response.status_code == 200
        assert response.json()["status"] == "deleted"
        assert test_session.query(ChannelPipelineRule).filter_by(id=rule_id).first() is None

    @pytest.mark.asyncio
    async def test_returns_404(self, async_client):
        """Returns 404 for nonexistent rule."""
        response = await async_client.delete("/api/auto-creation/rules/99999")
        assert response.status_code == 404


class TestReorderChannelPipelineRules:
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
        assert test_session.query(ChannelPipelineRule).get(r2.id).priority == 0
        assert test_session.query(ChannelPipelineRule).get(r1.id).priority == 1

    @pytest.mark.asyncio
    async def test_reorders_rules_on_canonical_path(self, async_client, test_session):
        """The canonical /api/channel-pipeline path reorders too (GH #755).

        The rules list reorders and copies through this one endpoint rather than
        a PUT per rule, so every drag and every copy in the UI depends on this
        exact path resolving. The pre-existing coverage above only exercised the
        deprecated /api/auto-creation alias.
        """
        r1 = _create_rule(test_session, name="A", priority=0)
        r2 = _create_rule(test_session, name="B", priority=1)
        r3 = _create_rule(test_session, name="C", priority=2)

        response = await async_client.post(
            "/api/channel-pipeline/rules/reorder",
            json=[r3.id, r1.id, r2.id],
        )
        assert response.status_code == 200
        assert response.json()["rule_ids"] == [r3.id, r1.id, r2.id]

        test_session.expire_all()
        assert test_session.query(ChannelPipelineRule).get(r3.id).priority == 0
        assert test_session.query(ChannelPipelineRule).get(r1.id).priority == 1
        assert test_session.query(ChannelPipelineRule).get(r2.id).priority == 2


class TestToggleChannelPipelineRule:
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


class TestDuplicateChannelPipelineRule:
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
    async def test_duplicate_journals_create_entry(self, async_client, test_session):
        """gjb01 audit-trail fix: duplicating a rule journals a create entry
        (matching POST /rules), so a later delete of the copy has its
        creation half in the journal — no more delete-only pairs."""
        from models import JournalEntry

        rule = _create_rule(test_session, name="Original", priority=5)

        response = await async_client.post(f"/api/auto-creation/rules/{rule.id}/duplicate")
        assert response.status_code == 200
        new_id = response.json()["id"]

        entry = test_session.query(JournalEntry).filter_by(
            category="auto_creation", action_type="create",
            entity_name="Original (Copy)",
        ).one()
        assert entry.entity_id == new_id
        assert "Original" in entry.description

    @pytest.mark.asyncio
    async def test_duplicate_preserves_active_window(self, async_client, test_session):
        rule = _create_rule(test_session, name="Season", active_from=date(2026, 9, 1),
                            active_until=date(2027, 2, 15))
        response = await async_client.post(f"/api/auto-creation/rules/{rule.id}/duplicate")
        assert response.status_code == 200
        assert response.json()["active_from"] == "2026-09-01"
        assert response.json()["active_until"] == "2027-02-15"

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

        with patch("channel_pipeline_engine.get_channel_pipeline_engine", return_value=mock_engine):
            response = await async_client.post("/api/auto-creation/run", json={"dry_run": False})

        assert response.status_code == 202, response.text
        body = response.json()
        assert "execution_id" in body
        assert body["status"] == "running"
        execution_id = body["execution_id"]

        # Execution row should already exist with status="running"
        from models import ChannelPipelineExecution
        exe = test_session.query(ChannelPipelineExecution).filter_by(id=execution_id).first()
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

        with patch("channel_pipeline_engine.get_channel_pipeline_engine", return_value=mock_engine):
            response = await async_client.post("/api/auto-creation/run", json={"dry_run": True})

        assert response.status_code == 202
        execution_id = response.json()["execution_id"]
        from models import ChannelPipelineExecution
        exe = test_session.query(ChannelPipelineExecution).filter_by(id=execution_id).first()
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

        with patch("channel_pipeline_engine.get_channel_pipeline_engine", return_value=mock_engine):
            response = await async_client.post("/api/auto-creation/run", json={"dry_run": False})

        assert response.status_code == 202
        execution_id = response.json()["execution_id"]

        # Yield to let the background task run
        for _ in range(50):
            await _asyncio.sleep(0)

        from models import ChannelPipelineExecution
        # Use a fresh query to pick up the supervised handler's commit
        test_session.expire_all()
        exe = test_session.query(ChannelPipelineExecution).filter_by(id=execution_id).first()
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

        with patch("channel_pipeline_engine.get_channel_pipeline_engine", return_value=mock_engine):
            start = _time.monotonic()
            response = await async_client.post("/api/auto-creation/run", json={"dry_run": False})
            elapsed = _time.monotonic() - start

        # Must enqueue and return well under 30s — even with a worker stuck in the engine
        assert response.status_code == 202
        assert elapsed < 5.0, f"enqueue took {elapsed:.2f}s — handler is not actually async-enqueuing"

        gate.set()
        for _ in range(20):
            await _asyncio.sleep(0)


class TestRunSelectedChannelPipelineRules:
    """Tests for the strict, atomic selected-rule run endpoint."""

    @pytest.mark.asyncio
    async def test_rejects_empty_and_duplicate_rule_ids(self, async_client):
        empty = await async_client.post(
            "/api/channel-pipeline/run-selected", json={"rule_ids": []}
        )
        duplicate = await async_client.post(
            "/api/channel-pipeline/run-selected", json={"rule_ids": [7, 7]}
        )

        assert empty.status_code == 400
        assert empty.json()["detail"]["code"] == "empty_rule_selection"
        assert duplicate.status_code == 400
        assert duplicate.json()["detail"]["code"] == "duplicate_rule_ids"

    @pytest.mark.asyncio
    async def test_rejects_unknown_rule_without_enqueuing(self, async_client):
        response = await async_client.post(
            "/api/channel-pipeline/run-selected", json={"rule_ids": [99999]}
        )

        assert response.status_code == 404
        assert response.json()["detail"] == {
            "code": "unknown_rule_ids",
            "message": "Selected rules were not found",
            "rule_ids": [99999],
        }

    @pytest.mark.asyncio
    async def test_rejects_entire_partially_eligible_selection(
        self, async_client, test_session
    ):
        enabled = _create_rule(test_session, name="Runnable", priority=2)
        disabled = _create_rule(
            test_session, name="Disabled", priority=1, enabled=False
        )
        inactive = _create_rule(
            test_session,
            name="Not started",
            priority=3,
            active_from=date(2099, 1, 1),
        )

        response = await async_client.post(
            "/api/channel-pipeline/run-selected",
            json={"rule_ids": [enabled.id, disabled.id, inactive.id]},
        )

        assert response.status_code == 409
        assert response.json()["detail"] == {
            "code": "selected_rules_not_runnable",
            "message": "Every selected rule must be runnable",
            "issues": [
                {"rule_id": disabled.id, "rule_name": "Disabled", "reason": "disabled"},
                {"rule_id": inactive.id, "rule_name": "Not started", "reason": "inactive"},
            ],
        }
        assert test_session.query(ChannelPipelineExecution).count() == 0

    @pytest.mark.asyncio
    async def test_rejects_invalid_stored_rule(self, async_client, test_session):
        invalid = _create_rule(
            test_session,
            name="Invalid stored rule",
            conditions=json.dumps([{"type": "not_a_condition"}]),
            actions=json.dumps([]),
        )

        response = await async_client.post(
            "/api/channel-pipeline/run-selected", json={"rule_ids": [invalid.id]}
        )

        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["code"] == "selected_rules_not_runnable"
        assert detail["issues"][0]["rule_id"] == invalid.id
        assert detail["issues"][0]["reason"] == "invalid"
        assert detail["issues"][0]["errors"]

    @pytest.mark.asyncio
    async def test_rejects_event_sync_rule_disabled_in_nested_config(
        self, async_client, test_session
    ):
        from tests.event_sync_fixtures import event_sync_config

        config = event_sync_config()
        config["enabled"] = False
        nested_disabled = _create_rule(
            test_session,
            name="Nested disabled",
            event_sync_config=json.dumps(config),
        )

        response = await async_client.post(
            "/api/channel-pipeline/run-selected",
            json={"rule_ids": [nested_disabled.id]},
        )

        assert response.status_code == 409
        assert response.json()["detail"]["issues"] == [{
            "rule_id": nested_disabled.id,
            "rule_name": "Nested disabled",
            "reason": "disabled",
        }]
        assert test_session.query(ChannelPipelineExecution).count() == 0

    @pytest.mark.asyncio
    async def test_enqueues_exact_selection_in_canonical_order_and_records_scope(
        self, async_client, test_session
    ):
        import asyncio as _asyncio

        later = _create_rule(test_session, name="Later", priority=20)
        first = _create_rule(test_session, name="First", priority=10)
        unselected = _create_rule(test_session, name="Unselected", priority=0)
        gate = _asyncio.Event()

        async def slow_run_pipeline(*args, **kwargs):
            await gate.wait()
            return {"success": True}

        mock_engine = AsyncMock()
        mock_engine.run_pipeline = AsyncMock(side_effect=slow_run_pipeline)
        with patch(
            "channel_pipeline_engine.get_channel_pipeline_engine",
            return_value=mock_engine,
        ):
            response = await async_client.post(
                "/api/channel-pipeline/run-selected",
                json={"rule_ids": [later.id, first.id], "dry_run": False},
            )

        assert response.status_code == 202, response.text
        execution_id = response.json()["execution_id"]
        call = mock_engine.run_pipeline.call_args.kwargs
        assert call["rule_ids"] == [first.id, later.id]
        assert call["require_all_rule_ids"] is True
        assert unselected.id not in call["rule_ids"]

        test_session.expire_all()
        execution = test_session.get(ChannelPipelineExecution, execution_id)
        assert execution.to_dict()["run_scope"] == "selected"
        assert execution.to_dict()["selected_rule_ids"] == [first.id, later.id]
        assert execution.to_dict()["selected_rule_outcomes"] == [
            {
                "rule_id": first.id,
                "rule_name": "First",
                "rule_kind": "standard",
                "status": "pending",
            },
            {
                "rule_id": later.id,
                "rule_name": "Later",
                "rule_kind": "standard",
                "status": "pending",
            },
        ]

        gate.set()
        for _ in range(20):
            await _asyncio.sleep(0)


class TestRunChannelPipelineRule:
    """Tests for POST /api/auto-creation/rules/{rule_id}/run (background-task pattern)."""

    @pytest.mark.asyncio
    async def test_returns_202_and_invokes_run_rule_with_execution_id(self, async_client, test_session):
        """POST /rules/{id}/run returns 202 + execution_id, runs in background."""
        import asyncio as _asyncio
        rule = _create_rule(test_session, name="Sports")
        mock_engine = AsyncMock()
        mock_engine.run_rule = AsyncMock(return_value={"success": True})

        with patch("channel_pipeline_engine.get_channel_pipeline_engine", return_value=mock_engine):
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

        with patch("channel_pipeline_engine.get_channel_pipeline_engine", return_value=mock_engine):
            response = await async_client.post(f"/api/auto-creation/rules/{rule.id}/run")

        assert response.status_code == 202
        execution_id = response.json()["execution_id"]

        for _ in range(50):
            await _asyncio.sleep(0)

        from models import ChannelPipelineExecution
        test_session.expire_all()
        exe = test_session.query(ChannelPipelineExecution).filter_by(id=execution_id).first()
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

    @pytest.mark.asyncio
    async def test_pagination_is_stable_when_start_times_tie(self, async_client, test_session):
        started_at = datetime(2026, 9, 2, 12, 0, 0)
        executions = [
            _create_execution(test_session, started_at=started_at)
            for _ in range(4)
        ]

        first_page = await async_client.get(
            "/api/auto-creation/executions", params={"limit": 2, "offset": 0}
        )
        second_page = await async_client.get(
            "/api/auto-creation/executions", params={"limit": 2, "offset": 2}
        )

        assert first_page.status_code == 200
        assert second_page.status_code == 200
        assert [item["id"] for item in first_page.json()["executions"]] == [
            executions[3].id,
            executions[2].id,
        ]
        assert [item["id"] for item in second_page.json()["executions"]] == [
            executions[1].id,
            executions[0].id,
        ]

    @pytest.mark.asyncio
    async def test_unfiltered_history_is_bounded(self, async_client, test_session):
        for _ in range(51):
            _create_execution(test_session)

        response = await async_client.get("/api/auto-creation/executions")

        assert response.status_code == 200
        assert response.json()["total"] == 51
        assert len(response.json()["executions"]) == 50

    @pytest.mark.asyncio
    async def test_has_snapshot_flag(self, async_client, test_session):
        """Each execution carries a derived has_snapshot boolean (ADR-010 §D6):
        true for executions that have an ChannelPipelineSnapshot row, false
        otherwise. uc51o.6/.7 gate the snapshot-restore affordance on it."""
        from models import ChannelPipelineSnapshot

        with_snap = _create_execution(test_session, rule_name="HasSnap")
        without_snap = _create_execution(test_session, rule_name="NoSnap")

        snapshot = ChannelPipelineSnapshot(
            execution_id=with_snap.id,
            snapshot_time=datetime.utcnow(),
            channel_count=1,
        )
        snapshot.set_channels_data({"channels": [
            {"id": 10, "name": "ESPN", "stream_ids": [501]},
        ]})
        test_session.add(snapshot)
        test_session.commit()

        response = await async_client.get("/api/auto-creation/executions")
        assert response.status_code == 200
        flags = {e["id"]: e["has_snapshot"] for e in response.json()["executions"]}
        assert flags[with_snap.id] is True
        assert flags[without_snap.id] is False

    @pytest.mark.asyncio
    async def test_has_snapshot_no_n_plus_one(self, async_client, test_session):
        """has_snapshot is resolved with a SINGLE existence query over the page's
        ids, not one query per execution. Asserts the snapshot table is hit at
        most once regardless of page size (no N+1)."""
        from models import ChannelPipelineSnapshot

        execs = [_create_execution(test_session, rule_name=f"E{i}") for i in range(5)]
        # Snapshot a couple of them.
        for ex in execs[:2]:
            snap = ChannelPipelineSnapshot(
                execution_id=ex.id, snapshot_time=datetime.utcnow(), channel_count=0,
            )
            snap.set_channels_data({"channels": []})
            test_session.add(snap)
        test_session.commit()

        # Count how many times the ChannelPipelineSnapshot table is queried while
        # building the list response. A per-execution lookup would be 5 (N);
        # the batched IN query is exactly 1. We wrap Session.query and inspect
        # its args (the snapshot probe is session.query(ChannelPipelineSnapshot
        # .execution_id) — a column attribute owned by ChannelPipelineSnapshot).
        from sqlalchemy.orm import Session

        snapshot_query_count = 0
        orig_query = Session.query

        def _is_snapshot_arg(a):
            return (
                a is ChannelPipelineSnapshot
                or getattr(a, "class_", None) is ChannelPipelineSnapshot
            )

        def counting_query(self, *args, **kwargs):
            nonlocal snapshot_query_count
            if any(_is_snapshot_arg(a) for a in args):
                snapshot_query_count += 1
            return orig_query(self, *args, **kwargs)

        with patch.object(Session, "query", counting_query):
            response = await async_client.get("/api/auto-creation/executions")

        assert response.status_code == 200
        assert len(response.json()["executions"]) == 5
        # Exactly one query against the snapshot table — never one per execution.
        assert snapshot_query_count == 1, (
            f"expected 1 snapshot query (batched IN), got {snapshot_query_count} "
            "— likely an N+1"
        )


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
    async def test_filters_stored_log_before_response_limit(self, async_client, test_session):
        """A match after the former 500-entry response window remains searchable."""
        execution = _create_execution(test_session, mode="dry_run")
        execution.set_execution_log([
            {
                "stream_id": index,
                "stream_name": f"Recent stream {index}",
                "actions_executed": [{
                    "type": "skip",
                    "description": "Stream skipped",
                    "success": True,
                }],
            }
            for index in range(500)
        ] + [{
            "stream_id": 501,
            "stream_name": "Older %_ needle stream",
            "actions_executed": [{
                "type": "merge_stream",
                "description": "Merged into channel",
                "success": False,
            }],
        }])
        test_session.commit()

        response = await async_client.get(
            f"/api/auto-creation/executions/{execution.id}",
            params={
                "include_log": "true",
                "log_search": "%_ NEEDLE",
                "log_categories": "merged,errors",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert [entry["stream_id"] for entry in data["execution_log"]] == [501]
        assert data["execution_log_total"] == 501
        assert data["execution_log_filtered_total"] == 1
        assert data["execution_log_limit"] == 500
        assert data["execution_log_offset"] == 0
        assert data["execution_log_filter_counts"]["skipped"] == 500
        assert data["execution_log_filter_counts"]["merged"] == 1
        assert data["execution_log_filter_counts"]["errors"] == 1

    @pytest.mark.asyncio
    async def test_unfiltered_stored_log_respects_response_limit(
        self, async_client, test_session
    ):
        execution = _create_execution(test_session)
        execution.set_execution_log([
            {
                "stream_id": index,
                "stream_name": f"Stream {index}",
                "actions_executed": [{"type": "skip", "success": True}],
            }
            for index in range(501)
        ])
        test_session.commit()

        response = await async_client.get(
            f"/api/auto-creation/executions/{execution.id}",
            params={"include_log": "true"},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["execution_log"]) == 500
        assert data["execution_log_total"] == 501
        assert data["execution_log_filtered_total"] == 501
        assert data["execution_log_limit"] == 500
        assert data["execution_log_offset"] == 0

    @pytest.mark.asyncio
    async def test_log_category_filters_use_or_semantics(self, async_client, test_session):
        execution = _create_execution(test_session)
        execution.set_execution_log([
            {
                "stream_id": 1,
                "stream_name": "Created",
                "actions_executed": [{"type": "create_channel", "success": True}],
            },
            {
                "stream_id": 2,
                "stream_name": "Assigned",
                "actions_executed": [{"type": "assign_epg", "success": True}],
            },
            {
                "stream_id": 3,
                "stream_name": "Uncategorized",
                "actions_executed": [{"type": "log_match", "success": True}],
            },
        ])
        test_session.commit()

        response = await async_client.get(
            f"/api/auto-creation/executions/{execution.id}",
            params={
                "include_log": "true",
                "log_categories": "assigned,created",
                "log_limit": 1,
                "log_offset": 1,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["execution_log_filtered_total"] == 2
        assert [entry["stream_id"] for entry in data["execution_log"]] == [2]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("params", "expected_status"),
        [
            ({"include_log": "true", "log_categories": "created,unknown"}, 422),
            ({"include_log": "true", "log_limit": 501}, 422),
            ({"include_log": "true", "log_limit": 0}, 422),
            ({"include_log": "true", "log_offset": -1}, 422),
            ({"include_log": "true", "log_search": "x" * 201}, 422),
        ],
    )
    async def test_rejects_invalid_log_filter_queries(
        self, async_client, test_session, params, expected_status
    ):
        execution = _create_execution(test_session)

        response = await async_client.get(
            f"/api/auto-creation/executions/{execution.id}",
            params=params,
        )

        assert response.status_code == expected_status

    @pytest.mark.asyncio
    async def test_returns_404(self, async_client):
        """Returns 404 for nonexistent execution."""
        response = await async_client.get("/api/auto-creation/executions/99999")
        assert response.status_code == 404


class TestGetExecutionSnapshot:
    """Tests for GET /api/auto-creation/executions/{execution_id}/snapshot (ADR-010)."""

    @pytest.mark.asyncio
    async def test_returns_snapshot(self, async_client, test_session):
        """Returns the pre-run snapshot payload for an execution that has one."""
        from models import ChannelPipelineSnapshot

        execution = _create_execution(test_session)
        snapshot = ChannelPipelineSnapshot(
            execution_id=execution.id,
            snapshot_time=datetime.utcnow(),
            channel_count=2,
        )
        snapshot.set_channels_data({"channels": [
            {"id": 10, "name": "ESPN", "channel_group_id": 1,
             "epg_data_id": 99, "tvg_id": "espn.us", "stream_ids": [501, 502]},
            {"id": 11, "name": "CNN", "channel_group_id": 2,
             "epg_data_id": None, "tvg_id": "cnn.us", "stream_ids": [601]},
        ]})
        test_session.add(snapshot)
        test_session.commit()

        response = await async_client.get(
            f"/api/auto-creation/executions/{execution.id}/snapshot"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["execution_id"] == execution.id
        assert data["channel_count"] == 2
        assert data["snapshot_time"] is not None
        assert len(data["channels"]) == 2
        espn = next(c for c in data["channels"] if c["id"] == 10)
        assert espn["stream_ids"] == [501, 502]
        assert espn["tvg_id"] == "espn.us"
        # IDs only — no URL leakage anywhere in the payload.
        assert "url" not in espn
        assert "streams" not in espn

    @pytest.mark.asyncio
    async def test_returns_404_when_no_snapshot(self, async_client, test_session):
        """Returns 404 when the execution exists but has no snapshot (dry-run,
        legacy, or capture-failure run)."""
        execution = _create_execution(test_session, mode="dry_run")

        response = await async_client.get(
            f"/api/auto-creation/executions/{execution.id}/snapshot"
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_404_for_nonexistent_execution(self, async_client):
        """Returns 404 for an execution id with no snapshot row at all."""
        response = await async_client.get(
            "/api/auto-creation/executions/99999/snapshot"
        )
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

        with patch("channel_pipeline_engine.get_channel_pipeline_engine", return_value=mock_engine), \
             patch("routers.channel_pipeline.journal"):
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

        with patch("channel_pipeline_engine.get_channel_pipeline_engine", return_value=mock_engine):
            response = await async_client.post("/api/auto-creation/executions/1/rollback")

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_legacy_path_needs_no_confirm(self, async_client):
        """uc51o.5: a no-snapshot run is rolled back without confirm (byte-compat).
        The endpoint passes confirm=False through to the engine."""
        mock_engine = AsyncMock()
        mock_engine.rollback_execution.return_value = {
            "success": True,
            "rule_name": "Sports Rule",
            "entities_removed": 3,
            "entities_restored": 0,
        }
        with patch("channel_pipeline_engine.get_channel_pipeline_engine", return_value=mock_engine), \
             patch("routers.channel_pipeline.journal"):
            response = await async_client.post("/api/auto-creation/executions/1/rollback")
        assert response.status_code == 200
        # confirm defaulted to False and was threaded to the engine.
        _, kwargs = mock_engine.rollback_execution.call_args
        assert kwargs.get("confirm") is False

    @pytest.mark.asyncio
    async def test_snapshot_present_without_confirm_returns_409(self, async_client):
        """uc51o.5: a snapshotted run rolled back WITHOUT confirm → 409 (the
        overwrite acknowledgement is required). The engine signals this with
        requires_confirm."""
        mock_engine = AsyncMock()
        mock_engine.rollback_execution.return_value = {
            "success": False,
            "has_snapshot": True,
            "requires_confirm": True,
            "error": (
                "Execution 1 has a pre-run snapshot, so rollback performs a "
                "FULL restore that overwrites ... confirm=true."
            ),
        }
        with patch("channel_pipeline_engine.get_channel_pipeline_engine", return_value=mock_engine):
            response = await async_client.post("/api/auto-creation/executions/1/rollback")
        assert response.status_code == 409
        assert "confirm" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_snapshot_present_with_confirm_does_full_restore(self, async_client):
        """uc51o.5: confirm=true on a snapshotted run runs the full restore and
        returns the restore-shaped result (restored_channels)."""
        mock_engine = AsyncMock()
        mock_engine.rollback_execution.return_value = {
            "success": True,
            "rule_name": "Sports Rule",
            "removed_channels": 1,
            "restored_channels": 5,
            "failed_channels": [],
        }
        with patch("channel_pipeline_engine.get_channel_pipeline_engine", return_value=mock_engine), \
             patch("routers.channel_pipeline.journal"):
            response = await async_client.post(
                "/api/auto-creation/executions/1/rollback?confirm=true"
            )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["restored_channels"] == 5
        _, kwargs = mock_engine.rollback_execution.call_args
        assert kwargs.get("confirm") is True

    @pytest.mark.asyncio
    async def test_snapshot_partial_failure_is_200_with_failures(self, async_client):
        """uc51o.5: a partial restore via /rollback returns 200 success=False
        with failures surfaced — never a blanket 500."""
        mock_engine = AsyncMock()
        mock_engine.rollback_execution.return_value = {
            "success": False,
            "rule_name": "Sports Rule",
            "removed_channels": 0,
            "restored_channels": 4,
            "failed_channels": [{"id": 11, "name": "GONE", "error": "404"}],
        }
        with patch("channel_pipeline_engine.get_channel_pipeline_engine", return_value=mock_engine), \
             patch("routers.channel_pipeline.journal"):
            response = await async_client.post(
                "/api/auto-creation/executions/1/rollback?confirm=true"
            )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert data["restored_channels"] == 4
        assert len(data["failed_channels"]) == 1


class TestRestoreSnapshot:
    """Tests for POST /api/auto-creation/executions/{id}/restore-snapshot (ADR-010 §D8).

    SAFETY-CRITICAL destructive write — admin-gated, confirm-gated, surfaces
    partial failures.
    """

    @pytest.mark.asyncio
    async def test_requires_confirm(self, async_client):
        """Without confirm=true → 400 (the §D5 warning is unacknowledged); the
        engine is never invoked."""
        mock_engine = AsyncMock()
        with patch("channel_pipeline_engine.get_channel_pipeline_engine", return_value=mock_engine):
            response = await async_client.post(
                "/api/auto-creation/executions/1/restore-snapshot"
            )
        assert response.status_code == 400
        assert "confirm" in response.json()["detail"].lower()
        mock_engine.restore_snapshot.assert_not_called()

    @pytest.mark.asyncio
    async def test_restores_with_confirm(self, async_client):
        """confirm=true → engine.restore_snapshot runs and the result is returned."""
        mock_engine = AsyncMock()
        mock_engine.restore_snapshot.return_value = {
            "success": True,
            "execution_id": 1,
            "rule_name": "Sports Rule",
            "removed_channels": 2,
            "restored_channels": 5,
            "failed_channels": [],
        }
        with patch("channel_pipeline_engine.get_channel_pipeline_engine", return_value=mock_engine), \
             patch("routers.channel_pipeline.journal"):
            response = await async_client.post(
                "/api/auto-creation/executions/1/restore-snapshot?confirm=true"
            )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["restored_channels"] == 5
        mock_engine.restore_snapshot.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_snapshot_returns_404(self, async_client):
        """An execution with no snapshot → 404 (use /rollback instead)."""
        mock_engine = AsyncMock()
        mock_engine.restore_snapshot.return_value = {
            "success": False,
            "no_snapshot": True,
            "error": "No snapshot for execution 1; use /rollback instead.",
        }
        with patch("channel_pipeline_engine.get_channel_pipeline_engine", return_value=mock_engine):
            response = await async_client.post(
                "/api/auto-creation/executions/1/restore-snapshot?confirm=true"
            )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_dry_run_guard_returns_400(self, async_client):
        """A dry-run / already-reverted guard failure → 400."""
        mock_engine = AsyncMock()
        mock_engine.restore_snapshot.return_value = {
            "success": False,
            "error": "Cannot restore a dry-run execution",
        }
        with patch("channel_pipeline_engine.get_channel_pipeline_engine", return_value=mock_engine):
            response = await async_client.post(
                "/api/auto-creation/executions/1/restore-snapshot?confirm=true"
            )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_partial_failure_is_200_with_failures_surfaced(self, async_client):
        """A restore that failed on some channels returns 200 with success=False
        and the per-item failures — success-with-warnings, never a blanket 200
        that hides them, never a blanket 500."""
        mock_engine = AsyncMock()
        mock_engine.restore_snapshot.return_value = {
            "success": False,
            "execution_id": 1,
            "rule_name": "Sports Rule",
            "removed_channels": 0,
            "restored_channels": 4,
            "failed_channels": [{"id": 11, "name": "GONE", "error": "404"}],
        }
        with patch("channel_pipeline_engine.get_channel_pipeline_engine", return_value=mock_engine), \
             patch("routers.channel_pipeline.journal"):
            response = await async_client.post(
                "/api/auto-creation/executions/1/restore-snapshot?confirm=true"
            )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert data["restored_channels"] == 4
        assert len(data["failed_channels"]) == 1
        assert data["failed_channels"][0]["id"] == 11


class TestExportYAML:
    """Tests for GET /api/auto-creation/export/yaml."""

    @pytest.mark.asyncio
    async def test_exports_rules(self, async_client, test_session):
        """Exports rules as YAML."""
        _create_rule(test_session, name="Export Me")

        mock_client = AsyncMock()
        mock_client.get_channel_groups.return_value = []
        mock_client.get_m3u_accounts.return_value = []

        with patch("routers.channel_pipeline.get_client", return_value=mock_client):
            response = await async_client.get("/api/auto-creation/export/yaml")

        assert response.status_code == 200
        assert "Export Me" in response.text

    @pytest.mark.asyncio
    async def test_exports_active_window_and_null_bounds(self, async_client, test_session):
        import yaml

        _create_rule(test_session, name="Seasonal", active_from=date(2026, 9, 1),
                     active_until=date(2027, 2, 15))
        _create_rule(test_session, name="Always")
        mock_client = AsyncMock()
        mock_client.get_channel_groups.return_value = []
        mock_client.get_m3u_accounts.return_value = []
        with patch("routers.channel_pipeline.get_client", return_value=mock_client):
            response = await async_client.get("/api/auto-creation/export/yaml")

        by_name = {item["name"]: item for item in yaml.safe_load(response.text)["rules"]}
        assert by_name["Seasonal"]["active_from"] == "2026-09-01"
        assert by_name["Seasonal"]["active_until"] == "2027-02-15"
        assert by_name["Always"]["active_from"] is None
        assert by_name["Always"]["active_until"] is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("accounts", "expected_names"),
        [
            (RuntimeError("lookup unavailable"), []),
            ([{"id": 11, "name": "Primary"}], []),
        ],
        ids=["lookup-failure", "partial-map"],
    )
    async def test_required_provider_names_are_only_exported_when_complete(
        self, async_client, test_session, accounts, expected_names
    ):
        import yaml

        _create_rule(
            test_session, name="Portable only with complete names",
            required_provider_ids=json.dumps([11, 22]),
        )
        mock_client = AsyncMock()
        mock_client.get_channel_groups.return_value = []
        if isinstance(accounts, Exception):
            mock_client.get_m3u_accounts.side_effect = accounts
        else:
            mock_client.get_m3u_accounts.return_value = accounts

        with patch("routers.channel_pipeline.get_client", return_value=mock_client):
            response = await async_client.get("/api/auto-creation/export/yaml")

        rule = yaml.safe_load(response.text)["rules"][0]
        assert rule["required_provider_ids"] == [11, 22]
        assert rule["required_provider_names"] == expected_names


class TestImportYAML:
    """Tests for POST /api/auto-creation/import/yaml."""

    @pytest.mark.asyncio
    async def test_rejects_invalid_yaml(self, async_client):
        """Returns 400 for invalid YAML."""
        mock_client = AsyncMock()
        mock_client.get_channel_groups.return_value = []
        mock_client.get_m3u_accounts.return_value = []

        with patch("routers.channel_pipeline.get_client", return_value=mock_client):
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

        with patch("routers.channel_pipeline.get_client", return_value=mock_client):
            response = await async_client.post("/api/auto-creation/import/yaml", json={
                "yaml_content": "foo: bar",
            })

        assert response.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.parametrize("start,end", [
        ('"2026-09-01"', '"2027-02-15"'),
        ("2026-09-01", "2027-02-15"),
        ("null", "2027-02-15"),
        ("2026-09-01", "null"),
    ])
    async def test_imports_quoted_unquoted_and_open_active_windows(
        self, async_client, test_session, start, end
    ):
        rule_name = f"Window {start} {end}"
        content = f"""rules:\n  - name: {rule_name}\n    conditions: [{{type: always}}]\n    actions: [{{type: skip}}]\n    active_from: {start}\n    active_until: {end}\n"""
        mock_client = AsyncMock()
        mock_client.get_channel_groups.return_value = []
        mock_client.get_m3u_accounts.return_value = []
        with patch("routers.channel_pipeline.get_client", return_value=mock_client), \
             patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.channel_pipeline.journal"):
            response = await async_client.post("/api/auto-creation/import/yaml", json={"yaml_content": content})
        assert response.status_code == 200, response.text
        assert response.json()["errors"] == []
        test_session.expire_all()
        stored = test_session.query(ChannelPipelineRule).filter_by(name=rule_name).one()
        expected_start = None if start == "null" else date(2026, 9, 1)
        expected_end = None if end == "null" else date(2027, 2, 15)
        assert stored.active_from == expected_start
        assert stored.active_until == expected_end

    @pytest.mark.asyncio
    async def test_export_import_round_trip_persists_both_bounds(
        self, async_client, test_session
    ):
        import yaml

        source = _create_rule(
            test_session, name="Round-trip source",
            active_from=date(2026, 9, 1), active_until=date(2027, 2, 15),
        )
        mock_client = AsyncMock()
        mock_client.get_channel_groups.return_value = []
        mock_client.get_m3u_accounts.return_value = []
        with patch("routers.channel_pipeline.get_client", return_value=mock_client):
            exported = await async_client.get("/api/auto-creation/export/yaml")
        assert exported.status_code == 200

        document = yaml.safe_load(exported.text)
        document["rules"][0]["name"] = "Round-trip target"
        test_session.delete(source)
        test_session.commit()
        with patch("routers.channel_pipeline.get_client", return_value=mock_client), \
             patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.channel_pipeline.journal"):
            imported = await async_client.post("/api/auto-creation/import/yaml", json={
                "yaml_content": yaml.safe_dump(document, sort_keys=False),
            })
        assert imported.status_code == 200, imported.text
        assert imported.json()["errors"] == []
        test_session.expire_all()
        target = test_session.query(ChannelPipelineRule).filter_by(
            name="Round-trip target"
        ).one()
        assert target.active_from == date(2026, 9, 1)
        assert target.active_until == date(2027, 2, 15)

    @pytest.mark.asyncio
    async def test_export_import_round_trip_preserves_required_provider_ids_and_names(
        self, async_client, test_session
    ):
        import yaml

        source = _create_rule(
            test_session,
            name="Coverage source",
            required_provider_ids=json.dumps([11, 22]),
        )
        mock_client = AsyncMock()
        mock_client.get_channel_groups.return_value = []
        mock_client.get_m3u_accounts.return_value = [
            {"id": 11, "name": "Primary"},
            {"id": 22, "name": "Backup"},
        ]
        with patch("routers.channel_pipeline.get_client", return_value=mock_client):
            exported = await async_client.get("/api/auto-creation/export/yaml")

        document = yaml.safe_load(exported.text)
        exported_rule = document["rules"][0]
        assert exported_rule["required_provider_ids"] == [11, 22]
        assert exported_rule["required_provider_names"] == ["Primary", "Backup"]
        exported_rule["name"] = "Coverage target"
        test_session.delete(source)
        test_session.commit()

        with patch("routers.channel_pipeline.get_client", return_value=mock_client), \
             patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.channel_pipeline.journal"):
            imported = await async_client.post("/api/auto-creation/import/yaml", json={
                "yaml_content": yaml.safe_dump(document, sort_keys=False),
            })

        assert imported.status_code == 200, imported.text
        assert imported.json()["errors"] == []
        test_session.expire_all()
        target = test_session.query(ChannelPipelineRule).filter_by(name="Coverage target").one()
        assert target.get_required_provider_ids() == [11, 22]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("stored", ["{}", "not-json", "[11]"])
    async def test_malformed_stored_coverage_exports_without_becoming_unconfigured(
        self, async_client, test_session, stored
    ):
        import yaml

        source = _create_rule(
            test_session, name="Malformed coverage source",
            required_provider_ids=stored,
        )
        mock_client = AsyncMock()
        mock_client.get_channel_groups.return_value = []
        mock_client.get_m3u_accounts.return_value = [
            {"id": 11, "name": "Primary"},
            {"id": 22, "name": "Backup"},
        ]
        with patch("routers.channel_pipeline.get_client", return_value=mock_client):
            exported = await async_client.get("/api/auto-creation/export/yaml")

        document = yaml.safe_load(exported.text)
        document["rules"][0]["name"] = "Malformed coverage target"
        test_session.delete(source)
        test_session.commit()
        with patch("routers.channel_pipeline.get_client", return_value=mock_client):
            imported = await async_client.post(
                "/api/auto-creation/import/yaml",
                json={"yaml_content": yaml.safe_dump(document, sort_keys=False)},
            )

        assert imported.status_code == 200
        assert imported.json()["imported"] == []
        assert "required_provider_ids" in imported.json()["errors"][0]["errors"][0]
        assert test_session.query(ChannelPipelineRule).filter_by(
            name="Malformed coverage target"
        ).one_or_none() is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("coverage", [pytest.param("absent"), None, []])
    async def test_yaml_import_absent_or_explicit_empty_coverage_clears_configuration(
        self, async_client, test_session, coverage
    ):
        import yaml

        rule_data = {
            "name": f"Cleared coverage {coverage}",
            "conditions": [{"type": "always"}],
            "actions": [{"type": "skip"}],
        }
        if coverage != "absent":
            rule_data["required_provider_ids"] = coverage
        mock_client = AsyncMock()
        mock_client.get_channel_groups.return_value = []
        mock_client.get_m3u_accounts.return_value = []
        with patch("routers.channel_pipeline.get_client", return_value=mock_client), \
             patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.channel_pipeline.journal"):
            response = await async_client.post(
                "/api/auto-creation/import/yaml",
                json={"yaml_content": yaml.safe_dump({"rules": [rule_data]})},
            )

        assert response.status_code == 200, response.text
        assert response.json()["errors"] == []
        stored = test_session.query(ChannelPipelineRule).filter_by(
            name=rule_data["name"]
        ).one()
        assert stored.required_provider_ids is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("accounts", [
        RuntimeError("lookup unavailable"),
        [{"id": 11, "name": "Primary"}],
    ], ids=["lookup-failure", "partial-map"])
    async def test_non_authoritative_names_round_trip_by_id_after_lookup_recovers(
        self, async_client, test_session, accounts
    ):
        import yaml

        source = _create_rule(
            test_session, name="Same-install coverage source",
            required_provider_ids=json.dumps([11, 22]),
        )
        source_client = AsyncMock()
        source_client.get_channel_groups.return_value = []
        if isinstance(accounts, Exception):
            source_client.get_m3u_accounts.side_effect = accounts
        else:
            source_client.get_m3u_accounts.return_value = accounts
        with patch("routers.channel_pipeline.get_client", return_value=source_client):
            exported = await async_client.get("/api/auto-creation/export/yaml")

        document = yaml.safe_load(exported.text)
        assert document["rules"][0]["required_provider_names"] == []
        document["rules"][0]["name"] = "Same-install coverage target"
        test_session.delete(source)
        test_session.commit()
        recovered_client = AsyncMock()
        recovered_client.get_channel_groups.return_value = []
        recovered_client.get_m3u_accounts.return_value = [
            {"id": 11, "name": "Primary"},
            {"id": 22, "name": "Backup"},
        ]
        with patch("routers.channel_pipeline.get_client", return_value=recovered_client), \
             patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.channel_pipeline.journal"):
            imported = await async_client.post(
                "/api/auto-creation/import/yaml",
                json={"yaml_content": yaml.safe_dump(document, sort_keys=False)},
            )

        assert imported.json()["errors"] == []
        target = test_session.query(ChannelPipelineRule).filter_by(
            name="Same-install coverage target"
        ).one()
        assert target.get_required_provider_ids() == [11, 22]

    @pytest.mark.asyncio
    async def test_exported_required_provider_names_resolve_across_installation_ids(
        self, async_client, test_session
    ):
        import yaml

        source = _create_rule(
            test_session,
            name="Portable coverage source",
            required_provider_ids=json.dumps([11, 22]),
        )
        source_client = AsyncMock()
        source_client.get_channel_groups.return_value = []
        source_client.get_m3u_accounts.return_value = [
            {"id": 11, "name": "Primary"},
            {"id": 22, "name": "Backup"},
        ]
        with patch("routers.channel_pipeline.get_client", return_value=source_client):
            exported = await async_client.get("/api/auto-creation/export/yaml")

        document = yaml.safe_load(exported.text)
        document["rules"][0]["name"] = "Portable coverage target"
        test_session.delete(source)
        test_session.commit()
        target_client = AsyncMock()
        target_client.get_channel_groups.return_value = []
        target_client.get_m3u_accounts.return_value = [
            {"id": 101, "name": "Primary"},
            {"id": 202, "name": "Backup"},
        ]

        with patch("routers.channel_pipeline.get_client", return_value=target_client), \
             patch("routers.channel_pipeline.journal"):
            imported = await async_client.post(
                "/api/auto-creation/import/yaml",
                json={"yaml_content": yaml.safe_dump(document, sort_keys=False)},
            )

        assert imported.status_code == 200, imported.text
        assert imported.json()["errors"] == []
        test_session.expire_all()
        target = test_session.query(ChannelPipelineRule).filter_by(
            name="Portable coverage target"
        ).one()
        assert target.get_required_provider_ids() == [101, 202]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", [{}, False, 0, ""])
    async def test_yaml_import_rejects_explicit_malformed_required_providers(
        self, async_client, value
    ):
        import yaml

        document = {
            "rules": [{
                "name": "Malformed coverage import",
                "conditions": [{"type": "always"}],
                "actions": [{"type": "skip"}],
                "required_provider_ids": value,
            }]
        }
        mock_client = AsyncMock()
        mock_client.get_channel_groups.return_value = []
        mock_client.get_m3u_accounts.return_value = []

        with patch("routers.channel_pipeline.get_client", return_value=mock_client):
            response = await async_client.post(
                "/api/auto-creation/import/yaml",
                json={"yaml_content": yaml.safe_dump(document, sort_keys=False)},
            )

        assert response.status_code == 200
        assert response.json()["imported"] == []
        assert "required_provider_ids" in response.json()["errors"][0]["errors"][0]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "stream_sort_field",
        [None, "smart_sort", "quality", "stream_name", "stream_name_natural", "provider_order"],
    )
    async def test_export_import_round_trip_preserves_stream_sort_field(
        self, async_client, test_session, stream_sort_field
    ):
        """GH #833: exercise the actual YAML serializer and importer pair."""
        import yaml

        source = _create_rule(
            test_session,
            name="Stream-sort source",
            stream_sort_field=stream_sort_field,
        )
        mock_client = AsyncMock()
        mock_client.get_channel_groups.return_value = []
        mock_client.get_m3u_accounts.return_value = []
        with patch("routers.channel_pipeline.get_client", return_value=mock_client):
            exported = await async_client.get("/api/auto-creation/export/yaml")
        assert exported.status_code == 200, exported.text

        document = yaml.safe_load(exported.text)
        assert document["rules"][0]["stream_sort_field"] == stream_sort_field
        document["rules"][0]["name"] = "Stream-sort target"
        test_session.delete(source)
        test_session.commit()
        with patch("routers.channel_pipeline.get_client", return_value=mock_client), \
             patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.channel_pipeline.journal"):
            imported = await async_client.post(
                "/api/auto-creation/import/yaml",
                json={"yaml_content": yaml.safe_dump(document, sort_keys=False)},
            )

        assert imported.status_code == 200, imported.text
        assert imported.json()["errors"] == []
        test_session.expire_all()
        target = test_session.query(ChannelPipelineRule).filter_by(
            name="Stream-sort target"
        ).one()
        assert target.stream_sort_field == stream_sort_field

    @pytest.mark.asyncio
    async def test_valid_overwrite_persists_changed_active_window(
        self, async_client, test_session
    ):
        existing = _create_rule(
            test_session, name="Overwrite window",
            active_from=date(2026, 1, 1), active_until=date(2026, 6, 1),
        )
        content = """rules:\n  - name: Overwrite window\n    conditions: [{type: always}]\n    actions: [{type: skip}]\n    active_from: 2026-09-01\n    active_until: 2027-02-15\n"""
        mock_client = AsyncMock()
        mock_client.get_channel_groups.return_value = []
        mock_client.get_m3u_accounts.return_value = []
        with patch("routers.channel_pipeline.get_client", return_value=mock_client), \
             patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.channel_pipeline.journal"):
            response = await async_client.post("/api/auto-creation/import/yaml", json={
                "yaml_content": content, "overwrite": True,
            })
        assert response.status_code == 200, response.text
        assert response.json()["errors"] == []
        test_session.expire_all()
        stored = test_session.get(ChannelPipelineRule, existing.id)
        assert stored.active_from == date(2026, 9, 1)
        assert stored.active_until == date(2027, 2, 15)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("start,end", [
        ("not-a-date", "null"),
        ("2027-02-15", "2026-09-01"),
    ])
    async def test_invalid_active_window_does_not_create(self, async_client, test_session, start, end):
        content = f"""rules:\n  - name: Invalid Window\n    conditions: [{{type: always}}]\n    actions: [{{type: skip}}]\n    active_from: {start}\n    active_until: {end}\n"""
        mock_client = AsyncMock()
        mock_client.get_channel_groups.return_value = []
        mock_client.get_m3u_accounts.return_value = []
        with patch("routers.channel_pipeline.get_client", return_value=mock_client), \
             patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}):
            response = await async_client.post("/api/auto-creation/import/yaml", json={"yaml_content": content})
        assert response.status_code == 200
        assert response.json()["errors"]
        test_session.expire_all()
        assert test_session.query(ChannelPipelineRule).filter_by(name="Invalid Window").first() is None

    @pytest.mark.asyncio
    async def test_invalid_active_window_does_not_mutate_overwrite(self, async_client, test_session):
        existing = _create_rule(test_session, name="Keep Window", active_from=date(2026, 1, 1),
                                active_until=date(2026, 12, 31))
        content = """rules:\n  - name: Keep Window\n    conditions: [{type: always}]\n    actions: [{type: skip}]\n    active_from: 2027-02-15\n    active_until: 2026-09-01\n"""
        mock_client = AsyncMock()
        mock_client.get_channel_groups.return_value = []
        mock_client.get_m3u_accounts.return_value = []
        with patch("routers.channel_pipeline.get_client", return_value=mock_client), \
             patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}):
            response = await async_client.post("/api/auto-creation/import/yaml", json={"yaml_content": content, "overwrite": True})
        assert response.json()["errors"]
        test_session.expire_all()
        kept = test_session.get(ChannelPipelineRule, existing.id)
        assert (kept.active_from, kept.active_until) == (date(2026, 1, 1), date(2026, 12, 31))


class TestValidateRule:
    """Tests for POST /api/auto-creation/validate."""

    @pytest.mark.asyncio
    async def test_validates_valid_rule(self, async_client):
        """Returns valid for good conditions/actions."""
        with patch("channel_pipeline_schema.validate_rule", return_value={
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
        with patch("channel_pipeline_schema.validate_rule", return_value={
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
    def _clear_jobs(self, debug_bundle_admin):
        # Each test starts with an empty job dict so state never leaks across
        # tests (the dict is module-level by design so the in-memory job
        # lookup survives between requests within a single process).
        from routers import channel_pipeline as router_module

        router_module._clear_debug_bundle_jobs_for_tests()
        yield
        router_module._clear_debug_bundle_jobs_for_tests()

    @pytest.mark.asyncio
    async def test_post_returns_202_and_job_id(self, async_client):
        """POST /debug-bundle enqueues work and returns 202 + job_id immediately."""
        import asyncio as _asyncio

        gate = _asyncio.Event()

        async def slow_build():
            await gate.wait()
            return ("ecm-debug-bundle.tar.gz", b"fake-tar-gz")

        with patch("routers.channel_pipeline._build_debug_bundle", side_effect=slow_build):
            response = await async_client.post("/api/auto-creation/debug-bundle")
            assert response.status_code == 202, response.text
            body = response.json()
            assert "job_id" in body and body["job_id"]
            assert body["status"] == "running"
            job_id = body["job_id"]

            # The job should already exist with status="running" before the build finishes.
            from routers.channel_pipeline import _DEBUG_BUNDLE_JOBS
            assert job_id in _DEBUG_BUNDLE_JOBS
            assert _DEBUG_BUNDLE_JOBS[job_id].status == "running"

            # Release the build and let it complete.
            gate.set()
            for _ in range(20):
                await _asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_post_reuses_the_running_job(self, async_client):
        import asyncio as _asyncio

        gate = _asyncio.Event()

        async def slow_build():
            await gate.wait()
            return ("ecm-debug-bundle.tar.gz", b"fake-tar-gz")

        try:
            with patch(
                "routers.channel_pipeline._build_debug_bundle",
                side_effect=slow_build,
            ) as build:
                first = await async_client.post("/api/auto-creation/debug-bundle")
                second = await async_client.post("/api/auto-creation/debug-bundle")

            assert first.status_code == 202
            assert second.status_code == 202
            assert second.json()["job_id"] == first.json()["job_id"]
            assert build.call_count == 1
        finally:
            gate.set()
            for _ in range(20):
                await _asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_running_job_stays_admitted_past_ttl(self, async_client, monkeypatch):
        import asyncio as _asyncio
        from routers import channel_pipeline as router_module

        monkeypatch.setattr(router_module, "_DEBUG_BUNDLE_JOB_TTL_SECONDS", 0.02)
        release = _asyncio.Event()
        started = _asyncio.Event()
        active_builders = 0
        max_active_builders = 0

        async def stalled_build():
            nonlocal active_builders, max_active_builders
            active_builders += 1
            max_active_builders = max(max_active_builders, active_builders)
            started.set()
            try:
                await release.wait()
                return ("ecm-debug-bundle.tar.gz", b"fake-tar-gz")
            finally:
                active_builders -= 1

        try:
            with patch(
                "routers.channel_pipeline._build_debug_bundle",
                side_effect=stalled_build,
            ) as build:
                first = await async_client.post("/api/auto-creation/debug-bundle")
                await _asyncio.wait_for(started.wait(), timeout=1)
                await _asyncio.sleep(0.08)
                second = await async_client.post("/api/auto-creation/debug-bundle")
                await _asyncio.sleep(0)

                assert second.status_code == 202
                assert second.json()["job_id"] == first.json()["job_id"]
                assert build.call_count == 1
                assert max_active_builders == 1
        finally:
            release.set()
            for _ in range(100):
                if active_builders == 0:
                    break
                await _asyncio.sleep(0.005)

    @pytest.mark.asyncio
    async def test_other_admin_cannot_reuse_or_download_initiators_job(
        self, async_client
    ):
        import asyncio as _asyncio

        from auth.dependencies import require_authenticated_human_admin
        from main import app
        from models import User

        gate = _asyncio.Event()

        async def slow_build():
            await gate.wait()
            return ("ecm-debug-bundle.tar.gz", b"fake")

        second_admin = User(
            id=5151,
            username="other-admin",
            is_admin=True,
            is_active=True,
            auth_provider="local",
        )
        original_admin_override = app.dependency_overrides[
            require_authenticated_human_admin
        ]
        try:
            with patch("routers.channel_pipeline._build_debug_bundle", side_effect=slow_build):
                first = await async_client.post("/api/auto-creation/debug-bundle")
                job_id = first.json()["job_id"]
                app.dependency_overrides[require_authenticated_human_admin] = (
                    lambda: second_admin
                )

                second = await async_client.post("/api/auto-creation/debug-bundle")
                download = await async_client.get(
                    f"/api/auto-creation/debug-bundle/{job_id}"
                )

            assert second.status_code == 409
            assert download.status_code == 404
        finally:
            gate.set()
            app.dependency_overrides[require_authenticated_human_admin] = (
                original_admin_override
            )
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
            with patch("routers.channel_pipeline._build_debug_bundle", side_effect=slow_build):
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

        with patch("routers.channel_pipeline._build_debug_bundle", side_effect=fast_build):
            enqueue = await async_client.post("/api/auto-creation/debug-bundle")
            job_id = enqueue.json()["job_id"]

            for _ in range(100):
                response = await async_client.get(
                    f"/api/auto-creation/debug-bundle/{job_id}"
                )
                if response.headers.get("content-type", "").startswith(
                    "application/gzip"
                ):
                    break
                await _asyncio.sleep(0.01)
            else:
                pytest.fail("debug bundle did not complete within 1 second")

            assert response.status_code == 200
            assert response.headers["content-type"].startswith("application/gzip")
            disposition = response.headers["content-disposition"]
            assert "ecm-debug-bundle-test.tar.gz" in disposition
            assert response.content == b"\x1f\x8btar-bytes"

            # Single-shot read — job must be evicted so RAM is freed.
            from routers.channel_pipeline import _DEBUG_BUNDLE_JOBS
            assert job_id not in _DEBUG_BUNDLE_JOBS

    @pytest.mark.asyncio
    async def test_get_failed_job_returns_status_json(self, async_client):
        """A build that raises is marked failed and exposed via GET status."""
        import asyncio as _asyncio

        async def boom():
            raise RuntimeError("dispatcharr unreachable")

        with patch("routers.channel_pipeline._build_debug_bundle", side_effect=boom):
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
            from routers.channel_pipeline import _DEBUG_BUNDLE_JOBS
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
            with patch("routers.channel_pipeline._build_debug_bundle", side_effect=slow_build):
                start = _time.monotonic()
                response = await async_client.post("/api/auto-creation/debug-bundle")
                elapsed = _time.monotonic() - start

            assert response.status_code == 202
            assert elapsed < 5.0, f"enqueue took {elapsed:.2f}s — handler is not async-enqueuing"
        finally:
            gate.set()
            for _ in range(20):
                await _asyncio.sleep(0)

    def test_prune_drops_expired_terminal_jobs(self):
        """Terminal debug-bundle jobs expire relative to completion time."""
        from routers import channel_pipeline as router_module

        now = router_module.time.time()
        old = router_module._DebugBundleJob()
        old.status = "completed"
        old.completed_at = now - (router_module._DEBUG_BUNDLE_JOB_TTL_SECONDS + 60)
        fresh = router_module._DebugBundleJob()
        fresh.status = "failed"
        fresh.completed_at = now
        router_module._DEBUG_BUNDLE_JOBS["old"] = old
        router_module._DEBUG_BUNDLE_JOBS["fresh"] = fresh

        router_module._prune_old_debug_bundle_jobs()

        assert "old" not in router_module._DEBUG_BUNDLE_JOBS
        assert "fresh" in router_module._DEBUG_BUNDLE_JOBS

    @pytest.mark.asyncio
    async def test_terminal_job_expires_without_a_later_post(self, async_client, monkeypatch):
        import asyncio as _asyncio
        from routers import channel_pipeline as router_module

        monkeypatch.setattr(router_module, "_DEBUG_BUNDLE_JOB_TTL_SECONDS", 0.02)

        async def fast_build():
            return ("ecm-debug-bundle.tar.gz", b"fake")

        with patch("routers.channel_pipeline._build_debug_bundle", side_effect=fast_build):
            response = await async_client.post("/api/auto-creation/debug-bundle")
            job_id = response.json()["job_id"]
            await _asyncio.sleep(0.08)

        assert job_id not in router_module._DEBUG_BUNDLE_JOBS

    @pytest.mark.asyncio
    async def test_expiry_removes_completed_private_artifact(
        self, async_client, monkeypatch
    ):
        import asyncio as _asyncio
        import os as _os
        from routers import channel_pipeline as router_module

        monkeypatch.setattr(router_module, "_DEBUG_BUNDLE_JOB_TTL_SECONDS", 0.15)
        artifact_path: str | None = None

        async def fast_build():
            return ("ecm-debug-bundle.tar.gz", b"private-artifact")

        with patch("routers.channel_pipeline._build_debug_bundle", side_effect=fast_build):
            response = await async_client.post("/api/auto-creation/debug-bundle")
            job_id = response.json()["job_id"]
            for _ in range(100):
                job = router_module._DEBUG_BUNDLE_JOBS.get(job_id)
                if job is not None and job.artifact_path is not None:
                    artifact_path = job.artifact_path
                    break
                await _asyncio.sleep(0.005)
            else:
                pytest.fail("completed artifact was not persisted")
            assert artifact_path is not None
            assert _os.path.isfile(artifact_path)
            await _asyncio.sleep(0.2)

        assert job_id not in router_module._DEBUG_BUNDLE_JOBS
        assert artifact_path is not None
        assert not _os.path.exists(artifact_path)

    @pytest.mark.asyncio
    async def test_terminal_job_retention_has_a_hard_count_bound(
        self, async_client
    ):
        import asyncio as _asyncio
        from routers import channel_pipeline as router_module

        async def fast_build():
            return ("ecm-debug-bundle.tar.gz", b"bounded")

        with patch("routers.channel_pipeline._build_debug_bundle", side_effect=fast_build):
            for _ in range(router_module._DEBUG_BUNDLE_MAX_RETAINED_JOBS + 2):
                response = await async_client.post(
                    "/api/auto-creation/debug-bundle"
                )
                job_id = response.json()["job_id"]
                for _ in range(100):
                    if router_module._DEBUG_BUNDLE_JOBS[job_id].status == "completed":
                        break
                    await _asyncio.sleep(0.005)
                else:
                    pytest.fail("debug bundle did not complete")

        assert len(router_module._DEBUG_BUNDLE_JOBS) == (
            router_module._DEBUG_BUNDLE_MAX_RETAINED_JOBS
        )

    @pytest.mark.asyncio
    async def test_anonymous_start_and_download_are_denied_when_general_auth_is_off(
        self, async_client
    ):
        from auth.dependencies import require_authenticated_human_admin
        from main import app

        original_admin_override = app.dependency_overrides.pop(
            require_authenticated_human_admin
        )
        try:
            start = await async_client.post("/api/auto-creation/debug-bundle")
            download = await async_client.get(
                "/api/auto-creation/debug-bundle/not-a-job"
            )
        finally:
            # The class fixture restores this after the test as well, but put it
            # back now so teardown/background work stays under the same identity.
            app.dependency_overrides[require_authenticated_human_admin] = (
                original_admin_override
            )

        assert start.status_code == 401
        assert download.status_code == 401

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

        with patch("routers.channel_pipeline.get_client", return_value=mock_client), \
             patch("log_utils.get_recent_logs", return_value=[]):
            enqueue = await async_client.post("/api/auto-creation/debug-bundle")
            assert enqueue.status_code == 202
            job_id = enqueue.json()["job_id"]
            for _ in range(100):
                response = await async_client.get(
                    f"/api/auto-creation/debug-bundle/{job_id}"
                )
                if response.headers["content-type"].startswith("application/gzip"):
                    break
                assert response.json()["status"] == "running"
                await _asyncio.sleep(0.05)
            else:
                pytest.fail("debug bundle did not complete within 5 seconds")
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

    @pytest.mark.asyncio
    async def test_bundle_redacts_both_dispatcharr_api_key_and_legacy_api_key(
        self, async_client
    ):
        """The debug-bundle settings.json redactor must scrub BOTH the
        canonical ``dispatcharr_api_key`` (bd-jmi1c) and the legacy
        ``api_key`` field (pre-existing leak since v0.16.0-0004). Regression
        guard for bd-46g4t / bd-jmi1c P0-1.
        """
        import asyncio as _asyncio
        import io as _io
        import json as _json
        import tarfile as _tarfile
        from config import DispatcharrSettings

        # Distinctive sentinels so a substring scan on the tarball bytes
        # catches any leak path, not just the settings.json file we assert on.
        raw_canonical = "raw-canon-VANSIRTEST"
        raw_legacy = "raw-leg-XYZSENTINEL"
        raw_password = "raw-pass-PWDLEAK"
        raw_smtp = "raw-smtp-SMTPLEAK"
        raw_telegram_bot = "raw-tg-bot-TELEGRAMLEAK"
        raw_mcp = "raw-mcp-MCPLEAK"

        seeded = DispatcharrSettings(
            url="http://dispatcharr:9191",
            auth_method="api_key",
            username="admin",
            password=raw_password,
            dispatcharr_api_key=raw_canonical,
            api_key=raw_legacy,
            smtp_password=raw_smtp,
            telegram_bot_token=raw_telegram_bot,
            mcp_api_key=raw_mcp,
        )

        mock_client = AsyncMock()
        mock_client.get_channels = AsyncMock(return_value={"results": [], "next": None, "count": 0})
        mock_client.get_channel_groups = AsyncMock(return_value=[])
        mock_client.get_streams_by_ids = AsyncMock(return_value=[])
        mock_client.get_m3u_accounts = AsyncMock(return_value=[])

        with patch("routers.channel_pipeline.get_client", return_value=mock_client), \
             patch("log_utils.get_recent_logs", return_value=[]), \
             patch("config.get_settings", return_value=seeded), \
             patch("config.load_settings", return_value=seeded):
            enqueue = await async_client.post("/api/auto-creation/debug-bundle")
            assert enqueue.status_code == 202
            job_id = enqueue.json()["job_id"]
            for _ in range(100):
                response = await async_client.get(
                    f"/api/auto-creation/debug-bundle/{job_id}"
                )
                if response.headers["content-type"].startswith("application/gzip"):
                    break
                assert response.json()["status"] == "running"
                await _asyncio.sleep(0.05)
            else:
                pytest.fail("debug bundle did not complete within 5 seconds")
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("application/gzip")
            archive_bytes = response.content

        # Belt-and-suspenders: no raw credential value may appear anywhere in
        # the tar.gz bytes — catches future leak paths beyond settings.json.
        for label, raw in (
            ("dispatcharr_api_key", raw_canonical),
            ("api_key", raw_legacy),
            ("password", raw_password),
            ("smtp_password", raw_smtp),
            ("telegram_bot_token", raw_telegram_bot),
            ("mcp_api_key", raw_mcp),
        ):
            assert raw.encode() not in archive_bytes, (
                f"raw {label} value '{raw}' leaked into the debug bundle"
            )

        with _tarfile.open(fileobj=_io.BytesIO(archive_bytes), mode="r:gz") as tf:
            settings_member = tf.extractfile("settings.json")
            assert settings_member is not None
            settings_in_bundle = _json.loads(settings_member.read().decode("utf-8"))

        assert settings_in_bundle["dispatcharr_api_key"] == "***REDACTED***"
        assert settings_in_bundle["api_key"] == "***REDACTED***"
        assert settings_in_bundle["password"] == "***REDACTED***"
        assert settings_in_bundle["smtp_password"] == "***REDACTED***"
        assert settings_in_bundle["telegram_bot_token"] == "***REDACTED***"
        assert settings_in_bundle["mcp_api_key"] == "***REDACTED***"


class TestDebugBundleEventSyncMatching:
    """event_sync_matching.json — Event Sync matching diagnostics (bead 03nji).

    For each ENABLED event_sync rule the bundle runs the ZERO-WRITE resolver
    (via the shared preview fetch/resolve path) and serializes the full
    per-stream matching evidence a user can send to PROVE OUT matching.
    """

    @pytest.fixture(autouse=True)
    def _authorize_bundle(self, debug_bundle_admin):
        assert debug_bundle_admin.is_admin is True

    def _es_mock_client(self):
        from tests.event_sync_fixtures import (
            GROUP_NAMES,
            GROUP_SETTINGS_OK,
            M3U_ACCOUNTS,
            MASTER_CHANNELS,
            MASTER_GROUP_ID,
            SECONDARY_STREAMS,
        )

        client = MagicMock()
        client.get_channel_groups = AsyncMock(return_value=[
            {"id": gid, "name": name} for gid, name in GROUP_NAMES.items()
        ])
        client.get_streams_by_ids = AsyncMock(return_value=[])
        client.get_m3u_accounts = AsyncMock(return_value=M3U_ACCOUNTS)
        client.get_all_m3u_group_settings = AsyncMock(return_value=GROUP_SETTINGS_OK)

        async def _group_name_for_id(group_id):
            return GROUP_NAMES.get(group_id)

        client._channel_group_name_for_id = AsyncMock(side_effect=_group_name_for_id)

        async def _get_channels(page=1, page_size=100, search=None,
                                channel_group=None, **kwargs):
            if channel_group is not None:
                results = [
                    c for c in MASTER_CHANNELS
                    if c["channel_group_id"] == channel_group
                ]
            else:
                results = list(MASTER_CHANNELS)
            return {"count": len(results), "next": None, "results": results}

        client.get_channels = AsyncMock(side_effect=_get_channels)

        async def _get_streams(page=1, page_size=100, search=None,
                               channel_group_name=None, m3u_account=None,
                               **kwargs):
            results = list(SECONDARY_STREAMS.get(channel_group_name, []))
            return {"count": len(results), "next": None, "results": results}

        client.get_streams = AsyncMock(side_effect=_get_streams)

        # Mutating methods — the bundle path must NEVER call them.
        client.update_channel = AsyncMock()
        client.create_channel = AsyncMock()
        client.delete_channel = AsyncMock()
        client.add_stream_to_channel = AsyncMock()
        client.update_m3u_group_settings = AsyncMock()
        client.update_channel_group = AsyncMock()
        return client, MASTER_GROUP_ID

    async def _run_bundle(self, async_client, client):
        import asyncio as _asyncio
        import io as _io
        import tarfile as _tarfile

        with patch("routers.channel_pipeline.get_client", return_value=client), \
             patch("log_utils.get_recent_logs", return_value=[]):
            enqueue = await async_client.post("/api/auto-creation/debug-bundle")
            assert enqueue.status_code == 202
            job_id = enqueue.json()["job_id"]
            # Poll to completion. The event_sync resolve is offloaded to a
            # thread pool (run_cpu_bound), so real (non-zero) sleeps are needed
            # to let the worker thread finish before we read the artifact.
            response = None
            for _ in range(200):
                response = await async_client.get(
                    f"/api/auto-creation/debug-bundle/{job_id}")
                ctype = response.headers.get("content-type", "")
                if ctype.startswith("application/gzip"):
                    break
                body = response.json()
                assert body.get("status") != "failed", body
                await _asyncio.sleep(0.02)
        assert response is not None
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/gzip")
        members: dict[str, bytes] = {}
        with _tarfile.open(fileobj=_io.BytesIO(response.content), mode="r:gz") as tf:
            for name in tf.getnames():
                members[name] = tf.extractfile(name).read()
        return members, response.content

    @pytest.mark.asyncio
    async def test_bundle_includes_event_sync_matching_with_per_stream_fields(
        self, async_client, test_session
    ):
        from tests.event_sync_fixtures import event_sync_config

        client, master_group_id = self._es_mock_client()
        rule = _create_rule(
            test_session,
            name="Test Sync",
            event_sync_config=json.dumps(event_sync_config()),
        )

        members, _ = await self._run_bundle(async_client, client)
        assert "event_sync_matching.json" in members, list(members)
        section = json.loads(members["event_sync_matching.json"])

        assert section["rule_count"] == 1
        assert len(section["rules"]) == 1
        entry = section["rules"][0]
        assert entry["rule_id"] == rule.id
        assert entry["rule_name"] == "Test Sync"
        assert entry["master_group_id"] == master_group_id
        assert "error" not in entry

        # Matching controls are captured so a reader knows the effective policy.
        controls = entry["matching_controls"]
        assert controls["attach_threshold"] == 0.80
        assert controls["enforce_time_window"] is True
        # bead yjchp: the refresh-trigger opt-in is exported — "why didn't
        # this rule fire on refresh" is diagnosable from the bundle alone.
        assert controls["auto_run"] is False

        # bead yjchp: per-rule pre-flight status (the unattended run gates
        # on exactly this check). The fixture settings are all correct.
        assert entry["preflight"] == {"ok": True, "failures": [], "warnings": []}

        summary = entry["summary"]
        for key in (
            "secondary_streams", "would_attach", "ambiguous_skipped",
            "unmatched", "parse_failed", "master_channels",
            "master_channels_unparsed",
        ):
            assert key in summary
        # Counts reconcile with the per-stream rows by construction.
        assert (
            summary["would_attach"] + summary["ambiguous_skipped"]
            + summary["unmatched"] + summary["parse_failed"]
        ) == summary["secondary_streams"] == len(entry["streams"])
        # The Mercury vs. Aces fixture stream attaches at score 1.0.
        assert summary["would_attach"] >= 1

        # Every per-stream row carries the full diagnostic contract.
        row = entry["streams"][0]
        for field in (
            "stream_id", "stream_name", "group_id", "provider",
            "parsed_title", "parsed_start", "matched_pattern", "disposition",
            "unmatchable_reason", "ambiguous_reason", "attach_source",
            "matched_via", "best_candidate",
        ):
            assert field in row, field

        # A would_attach row proves out best-candidate evidence.
        attach_rows = [
            s for s in entry["streams"] if s["disposition"] == "would_attach"
        ]
        assert attach_rows
        best = attach_rows[0]["best_candidate"]
        assert best is not None
        for field in (
            "master_channel_name", "master_channel_id", "score", "band",
            "team_verdict", "time_delta_minutes", "reject_reason",
        ):
            assert field in best
        assert best["band"] == "attach"

        # Manifest advertises the count so a reviewer needn't grep the JSON.
        manifest = json.loads(members["manifest.json"])
        assert manifest["event_sync_rule_count"] == 1

    @pytest.mark.asyncio
    async def test_bundle_matching_handles_no_event_sync_rules(
        self, async_client, test_session
    ):
        """A standard (non-event_sync) rule yields an empty, note-bearing
        section — never a crash or a missing member."""
        client, _ = self._es_mock_client()
        _create_rule(test_session, name="Standard Rule")  # no event_sync_config

        members, _ = await self._run_bundle(async_client, client)
        assert "event_sync_matching.json" in members
        section = json.loads(members["event_sync_matching.json"])
        assert section["rules"] == []
        assert section["rule_count"] == 0
        assert "note" in section

        manifest = json.loads(members["manifest.json"])
        assert manifest["event_sync_rule_count"] == 0

    @pytest.mark.asyncio
    async def test_bundle_preflight_reports_cross_rule_master_conflict(
        self, async_client, test_session
    ):
        """bead yjchp: a rule whose SECONDARY group is another enabled
        event_sync rule's MASTER gets the tailored pre-flight failure in the
        bundle — naming the conflicting rule instead of advising the
        auto-sync toggle that would break it."""
        from tests.event_sync_fixtures import (
            MASTER_GROUP_ID,
            SECONDARY_A,
            SECONDARY_B,
            event_sync_config,
        )

        client, _ = self._es_mock_client()
        # SECONDARY_A carries auto_channel_sync ON because it is the master
        # of the second rule below.
        client.get_all_m3u_group_settings = AsyncMock(return_value={
            MASTER_GROUP_ID: {"auto_channel_sync": True},
            SECONDARY_A: {"auto_channel_sync": True},
            SECONDARY_B: {"auto_channel_sync": False},
        })
        _create_rule(
            test_session,
            name="Dirtvision",
            event_sync_config=json.dumps(event_sync_config(
                secondary_group_ids=[SECONDARY_A],
            )),
        )
        _create_rule(
            test_session,
            name="PPV",
            event_sync_config=json.dumps(event_sync_config(
                master_group_id=SECONDARY_A,
                secondary_group_ids=[SECONDARY_B],
            )),
        )

        members, _ = await self._run_bundle(async_client, client)
        section = json.loads(members["event_sync_matching.json"])
        by_name = {e["rule_name"]: e for e in section["rules"]}

        dirt = by_name["Dirtvision"]["preflight"]
        assert dirt["ok"] is False
        (failure,) = dirt["failures"]
        assert failure["check"] == "secondary_auto_sync_off"
        assert failure["conflicting_rule"] == "PPV"
        assert "'PPV'" in failure["message"]
        assert "Do NOT disable auto_channel_sync" in failure["message"]

        # The PPV rule itself pre-flights clean (its master IS auto-synced).
        assert by_name["PPV"]["preflight"]["ok"] is True

    @pytest.mark.asyncio
    async def test_bundle_matching_skips_disabled_event_sync_rule(
        self, async_client, test_session
    ):
        """A DISABLED event_sync rule is not resolved (matches run behavior)."""
        from tests.event_sync_fixtures import event_sync_config

        client, _ = self._es_mock_client()
        _create_rule(
            test_session,
            name="Disabled Sync",
            enabled=False,
            event_sync_config=json.dumps(event_sync_config()),
        )

        members, _ = await self._run_bundle(async_client, client)
        section = json.loads(members["event_sync_matching.json"])
        assert section["rules"] == []
        assert "note" in section


# =========================================================================
# Rule analyzer endpoints (bd-0gntx).
#
# /api/auto-creation/rules/analyze            — analyze rules in DB
# /api/auto-creation/rules/analyze/from-bundle — analyze rules.yaml from
#                                                 an uploaded debug bundle
# Both reuse channel_pipeline_rule_analyzer.analyze_rules; the router is a
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


class TestAnalyzeRuleBody:
    """POST /api/channel-pipeline/rules/analyze-body — analyze an UNSAVED
    rule body (enhancedchannelmanager-m1s38.2).

    These tests exercise routing, auth, deserialization/validation, the
    input cap, and the diagnostic wiring — NOT the analyzer's own 100+
    finding cases (those live in tests/unit/test_channel_pipeline_rule_analyzer.py).
    They assert the endpoint forwards the right data to analyze_rules and
    returns the shared response shape.
    """

    ANALYZE_BODY_PATH = "/api/channel-pipeline/rules/analyze-body"

    @pytest.mark.asyncio
    async def test_match_all_regex_flags_trivially_matches_all(self, async_client):
        response = await async_client.post(
            self.ANALYZE_BODY_PATH,
            json={
                "name": "Draft",
                "conditions": [
                    {"type": "stream_group_matches", "value": "UK|", "connector": "and"},
                ],
                "actions": [],
            },
        )
        assert response.status_code == 200
        body = response.json()
        # Shared analyzer response shape.
        assert set(body.keys()) == {"rules", "summary"}
        assert set(body["summary"].keys()) == {"error", "warning", "info"}
        codes = {f["code"] for r in body["rules"] for f in r["findings"]}
        assert "REGEX_TRIVIALLY_MATCHES_ALL" in codes

    @pytest.mark.asyncio
    async def test_merge_scope_not_target_group_fires(self, async_client):
        """create_channel + if_exists=merge with match_scope_target_group off
        → MERGE_SCOPE_NOT_TARGET_GROUP (info)."""
        response = await async_client.post(
            self.ANALYZE_BODY_PATH,
            json={
                "conditions": [],
                "actions": [{"type": "create_channel", "if_exists": "merge"}],
                "match_scope_target_group": False,
            },
        )
        assert response.status_code == 200
        body = response.json()
        findings = [f for r in body["rules"] for f in r["findings"]]
        codes = {f["code"] for f in findings}
        assert "MERGE_SCOPE_NOT_TARGET_GROUP" in codes
        scope = next(f for f in findings if f["code"] == "MERGE_SCOPE_NOT_TARGET_GROUP")
        assert scope["severity"] == "info"

    @pytest.mark.asyncio
    async def test_clean_rule_produces_no_findings(self, async_client):
        response = await async_client.post(
            self.ANALYZE_BODY_PATH,
            json={
                "name": "Movie Networks - UK add",
                "conditions": [
                    {"type": "normalized_name_in_group", "value": 1473, "connector": "and"},
                    {"type": "stream_group_matches", "value": r"^UK\|", "connector": "and"},
                ],
                "actions": [{"type": "merge_streams", "target": "auto"}],
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["summary"] == {"error": 0, "warning": 0, "info": 0}
        assert body["rules"][0]["findings"] == []

    @pytest.mark.asyncio
    async def test_multiple_findings_in_one_body(self, async_client):
        """The 2026-04-28 Sports rule shape surfaces several codes at once."""
        response = await async_client.post(
            self.ANALYZE_BODY_PATH,
            json={
                "conditions": [
                    {"type": "normalized_name_in_group", "value": 1464, "connector": "and"},
                    {"type": "stream_group_matches", "value": "UK|", "connector": "and"},
                    {"type": "stream_group_matches", "value": "US|", "connector": "or"},
                    {"type": "stream_group_contains", "value": "^4K", "connector": "or"},
                ],
                "actions": [{"type": "merge_streams", "target": "auto"}],
            },
        )
        assert response.status_code == 200
        body = response.json()
        codes = {f["code"] for r in body["rules"] for f in r["findings"]}
        assert "REGEX_TRIVIALLY_MATCHES_ALL" in codes
        assert "OPERATOR_VALUE_LOOKS_LIKE_REGEX" in codes
        assert len(codes) >= 2

    @pytest.mark.asyncio
    async def test_andor_drops_guard_fires(self, async_client):
        """Guard condition in the first AND-group but not the OR-arms →
        ANDOR_DROPS_GUARD."""
        response = await async_client.post(
            self.ANALYZE_BODY_PATH,
            json={
                "conditions": [
                    {"type": "normalized_name_in_group", "value": 1464, "connector": "and"},
                    {"type": "stream_group_is", "value": "Sports", "connector": "and"},
                    {"type": "stream_group_is", "value": "News", "connector": "or"},
                ],
                "actions": [{"type": "merge_streams", "target": "auto"}],
            },
        )
        assert response.status_code == 200
        codes = {
            f["code"]
            for r in response.json()["rules"]
            for f in r["findings"]
        }
        assert "ANDOR_DROPS_GUARD" in codes

    @pytest.mark.asyncio
    async def test_fifty_wellformed_conditions_are_clean(self, async_client):
        """A large but benign body analyzes to empty findings (guardrail f)."""
        conditions = [
            {"type": "stream_name_contains", "value": f"Channel{i}", "connector": "and"}
            for i in range(50)
        ]
        response = await async_client.post(
            self.ANALYZE_BODY_PATH,
            json={"conditions": conditions, "actions": []},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["rules"][0]["findings"] == []

    @pytest.mark.asyncio
    async def test_conditions_over_cap_rejected(self, async_client):
        """>200 conditions is rejected by the Pydantic cap, not analyzed."""
        conditions = [
            {"type": "stream_name_contains", "value": "x", "connector": "and"}
            for _ in range(201)
        ]
        response = await async_client.post(
            self.ANALYZE_BODY_PATH,
            json={"conditions": conditions, "actions": []},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_malformed_conditions_type_is_validation_error_not_500(
        self, async_client,
    ):
        """conditions must be a list — a string body is a 4xx validation
        error, never a 500."""
        response = await async_client.post(
            self.ANALYZE_BODY_PATH,
            json={"conditions": "not a list", "actions": []},
        )
        assert response.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_null_conditions_is_validation_error(self, async_client):
        response = await async_client.post(
            self.ANALYZE_BODY_PATH,
            json={"conditions": None, "actions": []},
        )
        assert response.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_empty_body_returns_clean_summary(self, async_client):
        """No conditions/actions at all (fresh draft) → empty findings, 200."""
        response = await async_client.post(self.ANALYZE_BODY_PATH, json={})
        assert response.status_code == 200
        body = response.json()
        assert body["summary"] == {"error": 0, "warning": 0, "info": 0}

    @pytest.mark.asyncio
    async def test_disabled_normalization_group_advisory_fires(
        self, async_client, test_session,
    ):
        """A body referencing a DISABLED normalization group surfaces the
        disabled-group advisory — proves normalization_groups is wired in
        from the DB."""
        group = _create_normalization_group(
            test_session, name="Disabled Group", enabled=False,
        )
        response = await async_client.post(
            self.ANALYZE_BODY_PATH,
            json={
                "conditions": [],
                "actions": [{"type": "merge_streams", "target": "auto"}],
                "normalization_group_ids": [group.id],
            },
        )
        assert response.status_code == 200
        codes = {
            f["code"]
            for r in response.json()["rules"]
            for f in r["findings"]
        }
        assert "RULE_REFERENCES_DISABLED_NORMALIZATION_GROUP" in codes

    @pytest.mark.asyncio
    async def test_merge_empty_target_group_advisory_fires(self, async_client):
        """merge_streams + explicit target_group_id pointing at an empty
        group → MERGE_STREAMS_NO_TARGET_CHANNELS. Proves the live channel-
        group diagnostic is built and forwarded to the analyzer."""
        mock_client = MagicMock()
        mock_client.get_channel_groups = AsyncMock(
            return_value=[{"id": 99, "name": "Empty", "channel_count": 0}]
        )
        with patch(
            "routers.channel_pipeline.get_client", return_value=mock_client
        ):
            response = await async_client.post(
                self.ANALYZE_BODY_PATH,
                json={
                    "conditions": [],
                    "actions": [{"type": "merge_streams", "target": "auto"}],
                    "target_group_id": 99,
                },
            )
        assert response.status_code == 200
        mock_client.get_channel_groups.assert_awaited_once()
        codes = {
            f["code"]
            for r in response.json()["rules"]
            for f in r["findings"]
        }
        assert "MERGE_STREAMS_NO_TARGET_CHANNELS" in codes

    @pytest.mark.asyncio
    async def test_no_dispatcharr_call_without_merge_target(self, async_client):
        """No merge_streams action ⇒ the endpoint must NOT fetch channel
        groups (keeps the debounced authoring call cheap)."""
        mock_client = MagicMock()
        mock_client.get_channel_groups = AsyncMock(return_value=[])
        with patch(
            "routers.channel_pipeline.get_client", return_value=mock_client
        ):
            response = await async_client.post(
                self.ANALYZE_BODY_PATH,
                json={
                    "conditions": [
                        {"type": "stream_name_contains", "value": "ESPN"},
                    ],
                    "actions": [{"type": "create_channel", "name_template": "{stream_name}"}],
                    "target_group_id": 99,
                },
            )
        assert response.status_code == 200
        mock_client.get_channel_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_channel_groups_fetch_failure_degrades_not_500(
        self, async_client,
    ):
        """If the Dispatcharr channel-groups fetch fails, the advisory
        endpoint degrades to fewer findings — it must not 500."""
        mock_client = MagicMock()
        mock_client.get_channel_groups = AsyncMock(
            side_effect=RuntimeError("dispatcharr down")
        )
        with patch(
            "routers.channel_pipeline.get_client", return_value=mock_client
        ):
            response = await async_client.post(
                self.ANALYZE_BODY_PATH,
                json={
                    "conditions": [],
                    "actions": [{"type": "merge_streams", "target": "auto"}],
                    "target_group_id": 99,
                },
            )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_requires_auth_when_enabled(self, async_client):
        """The route inherits the global /api/* auth gate — an unauthenticated
        request with auth enabled is rejected (401). Proves it is NOT in
        AUTH_EXEMPT_PATHS (guardrail c/f)."""
        with patch("main.get_auth_settings") as auth_mock:
            auth_mock.return_value.require_auth = True
            auth_mock.return_value.setup_complete = True
            response = await async_client.post(
                self.ANALYZE_BODY_PATH,
                json={"conditions": [], "actions": []},
            )
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# Admin gating (bd-757hc) — destructive / mutating endpoints carry
# RequireAdminIfEnabled, matching backup.py's create_backup / restore_backup.
#
# SECURITY FINDING: every mutating endpoint in this router was previously only
# authenticated (via the global middleware) but NOT authorized to admin. Any
# authenticated principal — including a narrowly-scoped MCP static-key session —
# could invoke destructive bulk rollback / run / write ops. These tests prove
# the gate is now in place.
#
# Pattern mirrors tests/routers/test_normalization.py::TestApplyToChannelsAdminGuard
# and tests/routers/test_0hjrk_backup_save_restore.py: the default `async_client`
# fixture runs with auth DISABLED (RequireAdminIfEnabled is a no-op → returns
# None), so the existing happy-path tests above already prove behavior is
# unchanged when auth is off. Here we override the prebuilt dependency to
# simulate auth-enabled non-admin (403) and auth-enabled admin (pass-through).
# ---------------------------------------------------------------------------

# (path, http_method, request_kwargs) for every endpoint that must be admin-gated.
# Bodies are intentionally well-formed enough to pass FastAPI request parsing —
# the admin dependency raises BEFORE the handler runs, so the handler internals
# are never reached when the gate rejects.
_GATED_ENDPOINTS = [
    ("/api/auto-creation/rules", "post", {"json": {"name": "X", "conditions": [], "actions": []}}),
    ("/api/auto-creation/rules/1", "put", {"json": {"name": "X"}}),
    ("/api/auto-creation/rules/bulk-update", "post", {"json": {"rule_ids": [1], "enabled": True}}),
    ("/api/auto-creation/rules/1", "delete", {}),
    ("/api/auto-creation/rules/reorder", "post", {"json": [1, 2, 3]}),
    ("/api/auto-creation/rules/1/toggle", "post", {}),
    ("/api/auto-creation/rules/1/duplicate", "post", {}),
    ("/api/auto-creation/run", "post", {"json": {}}),
    ("/api/auto-creation/rules/1/run", "post", {}),
    ("/api/auto-creation/executions/1/rollback", "post", {}),
    # restore-snapshot: the admin dependency raises BEFORE the confirm check
    # and BEFORE the handler, so a non-admin caller is rejected even without
    # confirm=true (proving the gate, not the confirm gate).
    ("/api/auto-creation/executions/1/restore-snapshot", "post", {}),
    ("/api/auto-creation/import/yaml", "post", {"json": {"yaml_content": "rules: []"}}),
]


class TestAutoCreationAdminGating:
    """Mutating/destructive auto-creation endpoints require admin when auth is
    enabled; read endpoints stay open. Mirrors backup.py's admin guard."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path, method, kwargs", _GATED_ENDPOINTS)
    async def test_non_admin_is_forbidden_when_auth_enabled(
        self, async_client, path, method, kwargs
    ):
        """Auth enabled + non-admin principal → 403 on every gated endpoint.

        Overriding RequireAdminIfEnabled.dependency to raise 403 simulates an
        authenticated-but-non-admin caller regardless of the test's auth state."""
        from fastapi import HTTPException, status
        from main import app
        from auth import RequireAdminIfEnabled as _prebuilt

        async def _reject() -> None:
            # Parameterless so FastAPI's DI introspection doesn't pull query args.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin access required",
            )

        app.dependency_overrides[_prebuilt.dependency] = _reject
        try:
            response = await getattr(async_client, method)(path, **kwargs)
        finally:
            app.dependency_overrides.pop(_prebuilt.dependency, None)

        assert response.status_code == 403, (
            f"{method.upper()} {path} should be admin-gated but returned "
            f"{response.status_code}"
        )
        assert "admin" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_admin_principal_can_rollback_when_auth_enabled(self, async_client):
        """Auth enabled + admin principal → the gate passes through and the
        destructive rollback (the headline finding) executes normally."""
        from main import app
        from auth import RequireAdminIfEnabled as _prebuilt

        async def _allow_admin():
            # Stand in for an authenticated admin User; the handler ignores the
            # returned value (param is the unused `_admin`).
            return MagicMock(is_admin=True, username="admin")

        mock_engine = AsyncMock()
        mock_engine.rollback_execution.return_value = {
            "success": True,
            "rule_name": "Sports Rule",
            "entities_removed": 3,
            "entities_restored": 0,
        }

        app.dependency_overrides[_prebuilt.dependency] = _allow_admin
        try:
            with patch("channel_pipeline_engine.get_channel_pipeline_engine", return_value=mock_engine), \
                 patch("routers.channel_pipeline.journal"):
                response = await async_client.post(
                    "/api/auto-creation/executions/1/rollback"
                )
        finally:
            app.dependency_overrides.pop(_prebuilt.dependency, None)

        assert response.status_code == 200
        assert response.json()["success"] is True

    @pytest.mark.asyncio
    async def test_rollback_allowed_when_auth_disabled(self, async_client):
        """Auth disabled (default async_client) → RequireAdminIfEnabled is a
        no-op and the endpoint behaves exactly as before the gate was added."""
        mock_engine = AsyncMock()
        mock_engine.rollback_execution.return_value = {
            "success": True,
            "rule_name": "Sports Rule",
            "entities_removed": 1,
            "entities_restored": 0,
        }

        with patch("channel_pipeline_engine.get_channel_pipeline_engine", return_value=mock_engine), \
             patch("routers.channel_pipeline.journal"):
            response = await async_client.post(
                "/api/auto-creation/executions/1/rollback"
            )

        assert response.status_code == 200
        assert response.json()["success"] is True

    @pytest.mark.asyncio
    async def test_admin_principal_can_restore_when_auth_enabled(self, async_client):
        """Auth enabled + admin principal → the gate passes and the destructive
        snapshot restore (with confirm) executes normally."""
        from main import app
        from auth import RequireAdminIfEnabled as _prebuilt

        async def _allow_admin():
            return MagicMock(is_admin=True, username="admin")

        mock_engine = AsyncMock()
        mock_engine.restore_snapshot.return_value = {
            "success": True,
            "execution_id": 1,
            "rule_name": "Sports Rule",
            "removed_channels": 1,
            "restored_channels": 3,
            "failed_channels": [],
        }

        app.dependency_overrides[_prebuilt.dependency] = _allow_admin
        try:
            with patch("channel_pipeline_engine.get_channel_pipeline_engine", return_value=mock_engine), \
                 patch("routers.channel_pipeline.journal"):
                response = await async_client.post(
                    "/api/auto-creation/executions/1/restore-snapshot?confirm=true"
                )
        finally:
            app.dependency_overrides.pop(_prebuilt.dependency, None)

        assert response.status_code == 200
        assert response.json()["success"] is True

    @pytest.mark.asyncio
    async def test_restore_allowed_when_auth_disabled(self, async_client):
        """Auth disabled (default async_client) → RequireAdminIfEnabled is a
        no-op; the restore endpoint behaves normally (confirm still required)."""
        mock_engine = AsyncMock()
        mock_engine.restore_snapshot.return_value = {
            "success": True,
            "execution_id": 1,
            "rule_name": "Sports Rule",
            "removed_channels": 0,
            "restored_channels": 2,
            "failed_channels": [],
        }

        with patch("channel_pipeline_engine.get_channel_pipeline_engine", return_value=mock_engine), \
             patch("routers.channel_pipeline.journal"):
            response = await async_client.post(
                "/api/auto-creation/executions/1/restore-snapshot?confirm=true"
            )

        assert response.status_code == 200
        assert response.json()["success"] is True

    @pytest.mark.asyncio
    async def test_read_endpoints_not_admin_gated(self, async_client, test_session):
        """Read-only endpoints stay reachable for a non-admin principal even
        when auth is enabled — only mutating ops are gated. We override the admin
        dependency to reject; the read endpoints don't depend on it, so they
        must still return 200."""
        from fastapi import HTTPException, status
        from main import app
        from auth import RequireAdminIfEnabled as _prebuilt

        async def _reject() -> None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin access required",
            )

        _create_rule(test_session, name="Readable")

        app.dependency_overrides[_prebuilt.dependency] = _reject
        try:
            list_resp = await async_client.get("/api/auto-creation/rules")
            execs_resp = await async_client.get("/api/auto-creation/executions")
            schema_resp = await async_client.get(
                "/api/auto-creation/schema/conditions"
            )
        finally:
            app.dependency_overrides.pop(_prebuilt.dependency, None)

        assert list_resp.status_code == 200
        assert execs_resp.status_code == 200
        assert schema_resp.status_code == 200


def _http_status_error(status: int) -> "object":
    """Build an httpx.HTTPStatusError with the given upstream status (bd-59x51)."""
    import httpx

    request = httpx.Request("GET", "http://upstream/api/channels/channels/")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(f"{status}", request=request, response=response)


class TestDebugBundleFetchResilience:
    """Debug-bundle upstream fan-out tolerates transient 504s (bd-59x51).

    A debug bundle is generated precisely when Dispatcharr may be slow/overloaded,
    so a transient upstream 504 on one page must NOT abort the whole build — it is
    retried, and if it still fails the slice is skipped and the manifest is stamped
    partial. ``asyncio.sleep`` is patched out so retry backoff doesn't slow tests.
    """

    @pytest.mark.asyncio
    async def test_with_retry_retries_5xx_then_succeeds(self):
        from routers import channel_pipeline as m

        calls = {"n": 0}

        async def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise _http_status_error(504)
            return "ok"

        with patch("routers.channel_pipeline.asyncio.sleep", new_callable=AsyncMock):
            result = await m._with_retry(flaky, what="test")

        assert result == "ok"
        assert calls["n"] == 3  # failed twice, succeeded on the third attempt

    @pytest.mark.asyncio
    async def test_with_retry_does_not_retry_4xx(self):
        from routers import channel_pipeline as m

        calls = {"n": 0}

        async def bad_request():
            calls["n"] += 1
            raise _http_status_error(400)

        import httpx

        with patch("routers.channel_pipeline.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(httpx.HTTPStatusError):
                await m._with_retry(bad_request, what="test")

        assert calls["n"] == 1  # a 4xx is the caller's fault — no retry

    @pytest.mark.asyncio
    async def test_with_retry_exhausts_and_reraises(self):
        from routers import channel_pipeline as m

        calls = {"n": 0}

        async def always_504():
            calls["n"] += 1
            raise _http_status_error(504)

        import httpx

        with patch("routers.channel_pipeline.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(httpx.HTTPStatusError):
                await m._with_retry(always_504, what="test")

        assert calls["n"] == m._DEBUG_BUNDLE_FETCH_RETRIES

    @pytest.mark.asyncio
    async def test_fetch_all_channels_complete(self):
        from routers import channel_pipeline as m

        client = MagicMock()

        async def get_channels(page, page_size):
            # 3 pages of 100, total 250.
            results = [{"id": (page - 1) * 100 + i} for i in range(100 if page < 3 else 50)]
            return {"count": 250, "results": results}

        client.get_channels = AsyncMock(side_effect=get_channels)

        channels, report = await m._fetch_all_channels(client)

        assert len(channels) == 250
        assert report["complete"] is True
        assert report["expected_pages"] == 3
        assert report["failed_pages"] == []

    @pytest.mark.asyncio
    async def test_fetch_all_channels_tolerates_failed_page(self):
        from routers import channel_pipeline as m

        client = MagicMock()

        async def get_channels(page, page_size):
            if page == 2:
                raise _http_status_error(504)  # retried, then given up on
            results = [{"id": (page - 1) * 100 + i} for i in range(100 if page < 3 else 50)]
            return {"count": 250, "results": results}

        client.get_channels = AsyncMock(side_effect=get_channels)

        with patch("routers.channel_pipeline.asyncio.sleep", new_callable=AsyncMock):
            channels, report = await m._fetch_all_channels(client)

        # Page 1 (100) + page 3 (50) survive; page 2 dropped — bundle is partial,
        # but the build did NOT abort.
        assert len(channels) == 150
        assert report["complete"] is False
        assert report["failed_pages"] == [2]

    @pytest.mark.asyncio
    async def test_fetch_all_channels_page1_failure_propagates(self):
        from routers import channel_pipeline as m
        import httpx

        client = MagicMock()
        client.get_channels = AsyncMock(side_effect=_http_status_error(504))

        # No first page => no catalog at all; this is the one case that propagates.
        with patch("routers.channel_pipeline.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(httpx.HTTPStatusError):
                await m._fetch_all_channels(client)

    @pytest.mark.asyncio
    async def test_fetch_stream_details_tolerates_failed_batch(self):
        from routers import channel_pipeline as m

        client = MagicMock()

        async def get_streams_by_ids(batch):
            if 0 in batch:  # first batch always fails
                raise _http_status_error(504)
            return [{"id": sid, "name": f"s{sid}", "m3u_account": 1, "url": "u"} for sid in batch]

        client.get_streams_by_ids = AsyncMock(side_effect=get_streams_by_ids)

        ids = list(range(250))  # 3 batches of 100/100/50
        with patch("routers.channel_pipeline.asyncio.sleep", new_callable=AsyncMock):
            lookup, report = await m._fetch_stream_details(client, ids, obfuscate_url=lambda u: u)

        assert report["complete"] is False
        assert report["failed_batches"] == 1
        assert report["expected_batches"] == 3
        # The two surviving batches still populated the lookup.
        assert len(lookup) == 150


class TestChannelPipelineCanonicalPrefixMount:
    """Smoke coverage for the NEW canonical /api/channel-pipeline mount
    (enhancedchannelmanager-dl0kk, Phase 3). The router is now mounted TWICE
    in main.py — once at /api/channel-pipeline (canonical, schema-visible)
    and once at /api/auto-creation (deprecated alias, hidden from schema) —
    so both prefixes must serve identical, fully-functional route tables.
    The rest of this file's ~3000 lines exercise the legacy /api/auto-creation
    alias path exhaustively; these two tests just confirm the canonical path
    is wired up the same way, not a duplicate of the full suite above.
    """

    @pytest.mark.asyncio
    async def test_list_rules_via_canonical_prefix(self, async_client):
        """GET /api/channel-pipeline/rules works identically to the legacy alias."""
        response = await async_client.get("/api/channel-pipeline/rules")
        assert response.status_code == 200
        assert response.json()["rules"] == []

    @pytest.mark.asyncio
    async def test_get_rule_via_canonical_prefix(self, async_client, test_session):
        """GET /api/channel-pipeline/rules/{rule_id} works identically to the legacy alias."""
        rule = _create_rule(test_session, name="Canonical Prefix Rule")

        response = await async_client.get(f"/api/channel-pipeline/rules/{rule.id}")
        assert response.status_code == 200
        assert response.json()["name"] == "Canonical Prefix Rule"


class TestFoldMatchKeyPersistence:
    """GH #645 / bead enhancedchannelmanager-0vao3: persist + round-trip the
    opt-in ``fold_match_key`` flag.

    The flag must default to False (existing installs unchanged), survive
    create, be readable via GET, be settable via PUT, survive rule
    duplication, and round-trip through the YAML export/import pair.
    """

    @pytest.mark.asyncio
    async def test_create_defaults_flag_to_false(self, async_client):
        with patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.channel_pipeline.journal"):
            create = await async_client.post("/api/auto-creation/rules", json={
                "name": "Strict Match Rule",
                "conditions": [{"type": "stream_name_contains", "value": "ESPN"}],
                "actions": [{"type": "create_channel", "name_template": "{stream_name}"}],
            })
        assert create.status_code == 200, create.text
        assert create.json()["fold_match_key"] is False

    @pytest.mark.asyncio
    async def test_create_persists_opt_in_and_get_round_trips(self, async_client):
        with patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.channel_pipeline.journal"):
            create = await async_client.post("/api/auto-creation/rules", json={
                "name": "Folded Match Rule",
                "conditions": [{"type": "stream_name_contains", "value": "ESPN"}],
                "actions": [{"type": "create_channel", "name_template": "{stream_name}",
                             "if_exists": "merge"}],
                "fold_match_key": True,
            })
        assert create.status_code == 200, create.text
        rule_id = create.json()["id"]
        assert create.json()["fold_match_key"] is True

        get = await async_client.get(f"/api/auto-creation/rules/{rule_id}")
        assert get.status_code == 200
        assert get.json()["fold_match_key"] is True

    @pytest.mark.asyncio
    async def test_update_sets_flag(self, async_client, test_session):
        rule = _create_rule(test_session, name="ToFold")
        assert rule.fold_match_key is False
        with patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.channel_pipeline.journal"):
            response = await async_client.put(
                f"/api/auto-creation/rules/{rule.id}",
                json={"fold_match_key": True},
            )
        assert response.status_code == 200, response.text
        assert response.json()["fold_match_key"] is True

    @pytest.mark.asyncio
    async def test_update_omitting_field_leaves_flag_unchanged(self, async_client, test_session):
        rule = _create_rule(test_session, name="KeepFold", fold_match_key=True)
        with patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.channel_pipeline.journal"):
            response = await async_client.put(
                f"/api/auto-creation/rules/{rule.id}",
                json={"name": "Renamed KeepFold"},
            )
        assert response.status_code == 200, response.text
        assert response.json()["fold_match_key"] is True

    @pytest.mark.asyncio
    async def test_duplicate_preserves_flag(self, async_client, test_session):
        rule = _create_rule(test_session, name="FoldOriginal", fold_match_key=True)
        with patch("routers.channel_pipeline.journal"):
            response = await async_client.post(
                f"/api/auto-creation/rules/{rule.id}/duplicate"
            )
        assert response.status_code == 200, response.text
        assert response.json()["fold_match_key"] is True

    @pytest.mark.asyncio
    async def test_export_includes_flag(self, async_client, test_session):
        _create_rule(test_session, name="ExportFold", fold_match_key=True)
        mock_client = AsyncMock()
        mock_client.get_channel_groups.return_value = []
        mock_client.get_m3u_accounts.return_value = []
        with patch("routers.channel_pipeline.get_client", return_value=mock_client):
            response = await async_client.get("/api/auto-creation/export/yaml")
        assert response.status_code == 200
        assert "fold_match_key" in response.text

    @pytest.mark.asyncio
    async def test_import_round_trips_flag(self, async_client, test_session):
        """A YAML doc with fold_match_key: true imports as an opted-in rule;
        one without the key defaults to False (backward compatible)."""
        yaml_content = """
rules:
  - name: Imported Folded Rule
    conditions:
      - type: stream_name_contains
        value: ESPN
    actions:
      - type: create_channel
        name_template: "{stream_name}"
        if_exists: merge
    fold_match_key: true
  - name: Imported Legacy Rule
    conditions:
      - type: stream_name_contains
        value: CNN
    actions:
      - type: create_channel
        name_template: "{stream_name}"
"""
        with patch("channel_pipeline_schema.validate_rule", return_value={"valid": True, "errors": []}), \
             patch("routers.channel_pipeline.journal"):
            response = await async_client.post("/api/auto-creation/import/yaml", json={
                "yaml_content": yaml_content,
            })
        assert response.status_code == 200, response.text

        rules = (await async_client.get("/api/auto-creation/rules")).json()["rules"]
        by_name = {r["name"]: r for r in rules}
        assert by_name["Imported Folded Rule"]["fold_match_key"] is True
        assert by_name["Imported Legacy Rule"]["fold_match_key"] is False
