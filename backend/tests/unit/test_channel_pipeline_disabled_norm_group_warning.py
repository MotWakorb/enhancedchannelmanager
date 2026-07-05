"""enhancedchannelmanager-e8p1h — warn when a rule references DISABLED
normalization groups.

Product footgun: an auto-creation rule can carry ``normalization_group_ids``
that point at NormalizationRuleGroup rows whose ``enabled=False`` (or that no
longer exist). When that happens every ``[NORMALIZE]`` decision applies nothing
(rules=[], applied=False), provider prefixes/suffixes are never stripped, and
``merge_streams target:auto`` matches almost nothing — with NO warning anywhere.

This module pins the additive *detection* behavior (it must NOT change what gets
merged/normalized):

  * ``_detect_disabled_normalization_group_warnings`` returns a structured
    warning for every rule that references a disabled or missing group, and an
    empty list when all referenced groups are enabled / no rule references any.
  * The warnings are surfaced in the run-summary result dict
    (``normalization_warnings``) and persisted on the execution record
    (``ChannelPipelineExecution.warnings`` via ``set_warnings`` / ``get_warnings``)
    so the polled record and the executions UI can show them.
"""
import asyncio
import json
from unittest.mock import MagicMock, AsyncMock, patch

from channel_pipeline_engine import ChannelPipelineEngine
from models import ChannelPipelineRule, ChannelPipelineExecution, NormalizationRuleGroup


def _make_rule(name, group_ids, enabled=True, priority=0):
    rule = ChannelPipelineRule(
        name=name,
        enabled=enabled,
        priority=priority,
        conditions=json.dumps([]),
        actions=json.dumps([]),
    )
    rule.set_normalization_group_ids(group_ids)
    return rule


class TestDetectDisabledNormalizationGroupWarnings:
    """Unit tests for the detection helper (real in-memory session)."""

    def setup_method(self):
        self.engine = ChannelPipelineEngine(MagicMock())

    def _detect(self, session, rules):
        with patch("channel_pipeline_engine.get_session", return_value=session):
            return asyncio.get_event_loop().run_until_complete(
                self.engine._detect_disabled_normalization_group_warnings(rules)
            )

    def test_warns_when_rule_references_disabled_group(self, test_session):
        """A rule referencing a disabled group produces a warning naming the
        rule and the disabled group(s)."""
        g_on = NormalizationRuleGroup(name="Quality Tags", enabled=True, priority=0)
        g_off = NormalizationRuleGroup(name="Country Prefixes", enabled=False, priority=1)
        test_session.add_all([g_on, g_off])
        test_session.commit()

        rule = _make_rule("Movie Channels", [g_on.id, g_off.id])
        test_session.add(rule)
        test_session.commit()

        warnings = self._detect(test_session, [rule])

        assert len(warnings) == 1
        w = warnings[0]
        assert w["rule_id"] == rule.id
        assert w["rule_name"] == "Movie Channels"
        # Only the disabled group is reported, not the enabled one.
        assert [g["id"] for g in w["disabled_groups"]] == [g_off.id]
        assert w["disabled_groups"][0]["name"] == "Country Prefixes"

    def test_no_warning_when_all_groups_enabled(self, test_session):
        """No warning when every referenced group is enabled — guards against a
        false positive that would cry wolf on healthy configs."""
        g1 = NormalizationRuleGroup(name="Quality Tags", enabled=True, priority=0)
        g2 = NormalizationRuleGroup(name="Country Prefixes", enabled=True, priority=1)
        test_session.add_all([g1, g2])
        test_session.commit()

        rule = _make_rule("Movie Channels", [g1.id, g2.id])
        test_session.add(rule)
        test_session.commit()

        warnings = self._detect(test_session, [rule])
        assert warnings == []

    def test_no_warning_when_rule_references_no_groups(self, test_session):
        """A rule with no normalization_group_ids never warns."""
        rule = _make_rule("No Norm Rule", [])
        test_session.add(rule)
        test_session.commit()

        warnings = self._detect(test_session, [rule])
        assert warnings == []

    def test_warns_when_referenced_group_missing(self, test_session):
        """A reference to a non-existent group id is reported as missing — a
        dangling id no-ops normalization exactly like a disabled group."""
        rule = _make_rule("Dangling Rule", [999])
        test_session.add(rule)
        test_session.commit()

        warnings = self._detect(test_session, [rule])
        assert len(warnings) == 1
        assert warnings[0]["disabled_groups"][0]["id"] == 999
        assert warnings[0]["disabled_groups"][0]["missing"] is True


class TestExecutionWarningsPersistence:
    """ChannelPipelineExecution.warnings round-trip + to_dict exposure."""

    def test_set_get_warnings_round_trip(self):
        ex = ChannelPipelineExecution()
        payload = [{"rule_id": 1, "rule_name": "R", "disabled_groups": [{"id": 2, "name": "G"}]}]
        ex.set_warnings(payload)
        assert ex.get_warnings() == payload

    def test_get_warnings_empty_default(self):
        ex = ChannelPipelineExecution()
        assert ex.get_warnings() == []

    def test_to_dict_includes_warnings(self):
        from datetime import datetime
        ex = ChannelPipelineExecution(started_at=datetime.utcnow())
        ex.set_warnings([{"rule_id": 1, "rule_name": "R", "disabled_groups": []}])
        d = ex.to_dict()
        assert d["warnings"] == [{"rule_id": 1, "rule_name": "R", "disabled_groups": []}]

    def test_to_dict_warnings_empty_when_none(self):
        from datetime import datetime
        ex = ChannelPipelineExecution(started_at=datetime.utcnow())
        assert ex.to_dict()["warnings"] == []


class TestRunPipelineSurfacesWarnings:
    """The pipeline result dict and persisted execution carry the warnings."""

    def setup_method(self):
        self.client = MagicMock()
        self.client.get_channels = AsyncMock(return_value={"count": 0, "results": []})
        self.client.get_channel_groups = AsyncMock(return_value=[])
        self.client.get_m3u_accounts = AsyncMock(return_value=[{"id": 1, "name": "Provider A"}])
        self.client.get_streams = AsyncMock(return_value={"count": 0, "results": []})
        self.engine = ChannelPipelineEngine(self.client)

    def test_run_pipeline_includes_normalization_warnings(self, test_session):
        """A rule referencing a disabled group surfaces a warning in the run
        result and on the persisted execution record."""
        g_off = NormalizationRuleGroup(name="Country Prefixes", enabled=False, priority=0)
        test_session.add(g_off)
        test_session.commit()
        rule = _make_rule("Movie Channels", [g_off.id])
        test_session.add(rule)
        test_session.commit()

        with patch("channel_pipeline_engine.get_session", return_value=test_session):
            result = asyncio.get_event_loop().run_until_complete(
                self.engine.run_pipeline(dry_run=True)
            )

        assert result["success"] is True
        assert "normalization_warnings" in result
        assert len(result["normalization_warnings"]) == 1
        assert result["normalization_warnings"][0]["rule_name"] == "Movie Channels"

        # Persisted on the execution record (what the polled API / UI reads).
        ex = test_session.get(ChannelPipelineExecution, result["execution_id"])
        assert ex is not None
        assert len(ex.get_warnings()) == 1
        assert ex.get_warnings()[0]["rule_name"] == "Movie Channels"

    def test_run_pipeline_no_warnings_when_group_enabled(self, test_session):
        """No warning emitted when the referenced group is enabled."""
        g_on = NormalizationRuleGroup(name="Country Prefixes", enabled=True, priority=0)
        test_session.add(g_on)
        test_session.commit()
        rule = _make_rule("Movie Channels", [g_on.id])
        test_session.add(rule)
        test_session.commit()

        with patch("channel_pipeline_engine.get_session", return_value=test_session):
            result = asyncio.get_event_loop().run_until_complete(
                self.engine.run_pipeline(dry_run=True)
            )

        assert result["success"] is True
        assert result.get("normalization_warnings", []) == []
