"""Persistence round-trips must preserve the event_sync rule KIND
(bead ti939.1.3, PR #612 review blocker).

Two paths previously stripped ``event_sync_config`` silently, resurrecting
the rule as a STANDARD rule whose dormant conditions/actions execute on the
next run — the exact hazard the duplicate endpoint guards against:

* **DBAS backup restore** (``routers.backup._restore_auto_creation_rules``)
  — delete-all-and-recreate, so a dropped config is destructive.
* **YAML export/import** (``/api/auto-creation/export/yaml`` +
  ``/api/auto-creation/import/yaml``) — both the create-new and the
  overwrite-update import branches.

Both must keep the kind; the YAML import must route the config through
``validate_event_sync_config`` (an unvalidated import path would bypass the
write-time scoping rail); DBAS restore downgrades validation failures to
warnings and restores as-is (the raw column keeps the rule the event_sync
kind — the fail-safe direction: still excluded from execution).
"""
from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from models import ChannelPipelineRule


def _event_sync_config(**overrides) -> dict:
    config = {
        "master_group_id": 10,
        "secondary_group_ids": [20, 30],
        "time_window_minutes": 30,
        "attach_threshold": 0.80,
        # ti939.2.1: the write-time validator default-fills the per-run
        # attach cap, so a round-tripped stored config carries it explicitly.
        "max_attach_per_run": 100,
        "enabled": True,
    }
    config.update(overrides)
    return config


def _create_rule(session, **overrides) -> ChannelPipelineRule:
    defaults = {
        "name": "Event Rule",
        "enabled": True,
        "priority": 0,
        # Dormant standard-rule logic — the hazard payload: if the kind is
        # stripped anywhere, these come alive on the next pipeline run.
        "conditions": json.dumps([{"type": "always"}]),
        "actions": json.dumps([{"type": "create_channel",
                                "name_template": "{stream_name}"}]),
        "run_on_refresh": True,
        "stop_on_first_match": True,
        "sort_order": "asc",
        "orphan_action": "delete",
        "event_sync_config": json.dumps(_event_sync_config()),
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }
    defaults.update(overrides)
    rule = ChannelPipelineRule(**defaults)
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return rule


def _mock_client() -> AsyncMock:
    client = AsyncMock()
    client.get_channel_groups.return_value = []
    client.get_m3u_accounts.return_value = []
    return client


class TestDbasRestoreRoundTrip:
    """routers.backup._restore_auto_creation_rules keeps the kind."""

    def test_backup_restore_round_trip_preserves_kind(self, test_session):
        """Export via to_dict() (what the DBAS backup writes) → restore →
        the rule is still the event_sync kind with an equal config."""
        from routers.backup import _restore_auto_creation_rules

        rule = _create_rule(test_session, name="RoundTrip")
        exported = rule.to_dict()  # config exported as a parsed dict
        assert exported["event_sync_config"] == _event_sync_config()

        with patch("routers.backup.get_session", return_value=test_session):
            result = _restore_auto_creation_rules([exported])

        assert result["warnings"] == []
        restored = test_session.query(ChannelPipelineRule).filter(
            ChannelPipelineRule.name == "RoundTrip"
        ).one()
        assert restored.is_event_sync() is True, (
            "DBAS restore stripped event_sync_config — the rule came back "
            "as a standard rule with dormant actions live"
        )
        assert restored.get_event_sync_config() == _event_sync_config()

    def test_restore_standard_rule_stays_standard(self, test_session):
        from routers.backup import _restore_auto_creation_rules

        rule = _create_rule(
            test_session, name="Standard", event_sync_config=None
        )
        exported = rule.to_dict()
        assert exported["event_sync_config"] is None

        with patch("routers.backup.get_session", return_value=test_session):
            result = _restore_auto_creation_rules([exported])

        assert result["warnings"] == []
        restored = test_session.query(ChannelPipelineRule).filter(
            ChannelPipelineRule.name == "Standard"
        ).one()
        assert restored.is_event_sync() is False
        assert restored.event_sync_config is None

    def test_restore_pre_feature_backup_without_field(self, test_session):
        """A backup taken before the column existed omits the key entirely."""
        from routers.backup import _restore_auto_creation_rules

        item = _create_rule(
            test_session, name="Ancient", event_sync_config=None
        ).to_dict()
        item.pop("event_sync_config")

        with patch("routers.backup.get_session", return_value=test_session):
            result = _restore_auto_creation_rules([item])

        assert result["warnings"] == []
        restored = test_session.query(ChannelPipelineRule).filter(
            ChannelPipelineRule.name == "Ancient"
        ).one()
        assert restored.is_event_sync() is False

    def test_restore_invalid_config_warns_but_keeps_kind(self, test_session):
        """Validation failures are DOWNGRADED to warnings (DBAS threat-model
        convention): restore is destructive delete-all-and-recreate, and the
        raw column is the fail-safe — the rule keeps the event_sync kind and
        stays excluded from pipeline execution."""
        from routers.backup import _restore_auto_creation_rules

        rule = _create_rule(test_session, name="BadConfig")
        item = rule.to_dict()
        # Corrupt the config: master inside secondaries (the scoping rail).
        item["event_sync_config"] = _event_sync_config(
            secondary_group_ids=[10, 20]
        )

        with patch("routers.backup.get_session", return_value=test_session):
            result = _restore_auto_creation_rules([item])

        assert len(result["warnings"]) == 1
        assert "BadConfig" in result["warnings"][0]
        assert "event_sync" in result["warnings"][0]
        restored = test_session.query(ChannelPipelineRule).filter(
            ChannelPipelineRule.name == "BadConfig"
        ).one()
        assert restored.is_event_sync() is True, (
            "invalid config must still be restored — the kind is what keeps "
            "the rule excluded from execution"
        )


class TestYamlExportImportRoundTrip:
    """/export/yaml ↔ /import/yaml keep the kind, with validation on import."""

    @pytest.mark.asyncio
    async def test_export_includes_event_sync_config(
        self, async_client, test_session
    ):
        _create_rule(test_session, name="ExportKind")
        with patch("routers.channel_pipeline.get_client",
                   return_value=_mock_client()):
            response = await async_client.get("/api/auto-creation/export/yaml")
        assert response.status_code == 200
        exported = yaml.safe_load(response.text)
        rule_data = exported["rules"][0]
        assert rule_data["event_sync_config"] == _event_sync_config()

    @pytest.mark.asyncio
    async def test_export_import_create_round_trip(
        self, async_client, test_session
    ):
        """export → delete → import (create-new branch) preserves the kind."""
        _create_rule(test_session, name="YamlRoundTrip")
        with patch("routers.channel_pipeline.get_client",
                   return_value=_mock_client()):
            export_response = await async_client.get(
                "/api/auto-creation/export/yaml"
            )
        assert export_response.status_code == 200

        test_session.query(ChannelPipelineRule).delete()
        test_session.commit()

        with patch("routers.channel_pipeline.get_client",
                   return_value=_mock_client()), \
             patch("routers.channel_pipeline.journal"):
            import_response = await async_client.post(
                "/api/auto-creation/import/yaml",
                json={"yaml_content": export_response.text},
            )
        assert import_response.status_code == 200, import_response.text
        body = import_response.json()
        assert body["errors"] == []
        assert body["imported"] == [
            {"name": "YamlRoundTrip", "action": "created"}
        ]

        restored = test_session.query(ChannelPipelineRule).filter(
            ChannelPipelineRule.name == "YamlRoundTrip"
        ).one()
        assert restored.is_event_sync() is True, (
            "YAML import-create stripped event_sync_config"
        )
        assert restored.get_event_sync_config() == _event_sync_config()

    @pytest.mark.asyncio
    async def test_export_import_update_round_trip(
        self, async_client, test_session
    ):
        """export → clear the config in place → import with overwrite
        (update-existing branch) restores the kind."""
        rule = _create_rule(test_session, name="YamlUpdate")
        with patch("routers.channel_pipeline.get_client",
                   return_value=_mock_client()):
            export_response = await async_client.get(
                "/api/auto-creation/export/yaml"
            )
        assert export_response.status_code == 200

        # Simulate drift: the live rule lost its config.
        rule.event_sync_config = None
        test_session.commit()

        with patch("routers.channel_pipeline.get_client",
                   return_value=_mock_client()), \
             patch("routers.channel_pipeline.journal"):
            import_response = await async_client.post(
                "/api/auto-creation/import/yaml",
                json={"yaml_content": export_response.text,
                      "overwrite": True},
            )
        assert import_response.status_code == 200, import_response.text
        body = import_response.json()
        assert body["errors"] == []
        assert body["imported"] == [
            {"name": "YamlUpdate", "action": "updated"}
        ]

        test_session.expire_all()
        updated = test_session.query(ChannelPipelineRule).filter(
            ChannelPipelineRule.name == "YamlUpdate"
        ).one()
        assert updated.is_event_sync() is True, (
            "YAML import-update stripped event_sync_config"
        )
        assert updated.get_event_sync_config() == _event_sync_config()

    @pytest.mark.asyncio
    async def test_import_update_with_standard_rule_clears_kind(
        self, async_client, test_session
    ):
        """Overwrite-import of an exported STANDARD rule clears the config —
        import-update overwrites every field unconditionally."""
        _create_rule(test_session, name="ToStandard")
        yaml_content = yaml.dump({"rules": [{
            "name": "ToStandard",
            "conditions": [{"type": "always"}],
            "actions": [{"type": "skip"}],
        }]})

        with patch("routers.channel_pipeline.get_client",
                   return_value=_mock_client()), \
             patch("routers.channel_pipeline.journal"):
            response = await async_client.post(
                "/api/auto-creation/import/yaml",
                json={"yaml_content": yaml_content, "overwrite": True},
            )
        assert response.status_code == 200, response.text
        assert response.json()["errors"] == []

        test_session.expire_all()
        updated = test_session.query(ChannelPipelineRule).filter(
            ChannelPipelineRule.name == "ToStandard"
        ).one()
        assert updated.is_event_sync() is False

    @pytest.mark.asyncio
    async def test_import_invalid_config_rejected_with_teaching_errors(
        self, async_client, test_session
    ):
        """The import path routes through validate_event_sync_config — an
        unvalidated import would bypass the write-time scoping rail."""
        yaml_content = yaml.dump({"rules": [{
            "name": "BadImport",
            "conditions": [{"type": "always"}],
            "actions": [{"type": "skip"}],
            "event_sync_config": {
                "master_group_id": 10,
                "secondary_group_ids": [10, 20],  # master in secondaries
            },
        }]})

        with patch("routers.channel_pipeline.get_client",
                   return_value=_mock_client()), \
             patch("routers.channel_pipeline.journal"):
            response = await async_client.post(
                "/api/auto-creation/import/yaml",
                json={"yaml_content": yaml_content},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["imported"] == []
        assert len(body["errors"]) == 1
        assert body["errors"][0]["rule_name"] == "BadImport"
        assert any(
            "secondary_group_ids" in e and "docs/event_sync.md" in e
            for e in body["errors"][0]["errors"]
        )
        assert test_session.query(ChannelPipelineRule).filter(
            ChannelPipelineRule.name == "BadImport"
        ).first() is None
