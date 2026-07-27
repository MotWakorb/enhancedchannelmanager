"""Validate the ECMSyncStalledTargetDrift alert in prometheus_rules.yaml.

Bead ``enhancedchannelmanager-k78ja`` (epic ``i39wu``). There is no ``promtool``
in the test image, so this is a structural validation: the rules file must be
loadable YAML, and the new sync staleness alert must live in the
``ecm_task_scheduler`` group and mirror the sibling ``ECMTaskScheduleStale*``
alerts EXACTLY in shape — the same staleness expression keyed on
``ecm_task_schedule_last_success_timestamp{task_id="dbas_sync"}``, the same
fresh-install ``> 0`` guard, ``severity: warning``, and a ``runbook_url``
pointing at a runbook that actually exists.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RULES_PATH = _REPO_ROOT / "docs" / "sre" / "prometheus_rules.yaml"
_ALERT_NAME = "ECMSyncStalledTargetDrift"


@pytest.fixture(scope="module")
def _rules() -> dict:
    assert _RULES_PATH.exists(), "prometheus_rules.yaml not found at %s" % _RULES_PATH
    with _RULES_PATH.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _find_alert(rules: dict, group_name: str, alert_name: str) -> dict:
    for group in rules.get("groups", []):
        if group.get("name") != group_name:
            continue
        for rule in group.get("rules", []):
            if rule.get("alert") == alert_name:
                return rule
    raise AssertionError(
        "alert %s not found in group %s" % (alert_name, group_name)
    )


def test_rules_file_is_valid_yaml(_rules):
    assert isinstance(_rules, dict)
    assert "groups" in _rules


def test_sync_alert_lives_in_task_scheduler_group(_rules):
    # Raises if the alert isn't in the ecm_task_scheduler group.
    _find_alert(_rules, "ecm_task_scheduler", _ALERT_NAME)


def test_sync_alert_mirrors_sibling_staleness_shape(_rules):
    alert = _find_alert(_rules, "ecm_task_scheduler", _ALERT_NAME)
    expr = alert["expr"]

    # Staleness expression keyed on the per-task last-success gauge. 7ipq2.3 /
    # ADR-013 S6: sync tasks are registered per target (dbas_sync_<target_id>),
    # so the matcher is a regex over the per-target id family — an exact
    # task_id="dbas_sync" matcher would match NOTHING and the alert would
    # silently die. Per-series evaluation also fixes the k78ja blind spot:
    # under the shared id, one healthy target's success reset the staleness
    # clock for every other (possibly drifting) target.
    assert (
        'ecm_task_schedule_last_success_timestamp{task_id=~"dbas_sync_.+"}' in expr
    )
    assert 'task_id="dbas_sync"' not in expr
    assert "time() -" in expr
    # 3h budget = 3 missed hourly runs (ADR-013 S9 cheap-reads cadence basis).
    assert "10800" in expr
    # Fresh-install / operator-disabled guard — mirrors every sibling alert.
    assert (
        'AND ecm_task_schedule_last_success_timestamp{task_id=~"dbas_sync_.+"} > 0'
        in expr
    )


def test_sync_alert_is_warning_not_page(_rules):
    """Self-hosted single operator — drift is recoverable by one idempotent
    re-sync, so this is warning severity, never page."""
    alert = _find_alert(_rules, "ecm_task_scheduler", _ALERT_NAME)
    assert alert["labels"]["severity"] == "warning"
    assert alert["labels"]["slo"] == "task_scheduler_health"
    assert alert["labels"]["service"] == "ecm"


def test_sync_alert_has_for_window_and_annotations(_rules):
    alert = _find_alert(_rules, "ecm_task_scheduler", _ALERT_NAME)
    assert alert["for"] == "1h"
    annotations = alert["annotations"]
    assert "summary" in annotations
    # The partial-apply caveat MUST be documented for the responder (only a
    # full success stamps the gauge; a sustained partial loop also trips this).
    assert "partial" in annotations["description"].lower()


def test_sync_alert_runbook_exists(_rules):
    alert = _find_alert(_rules, "ecm_task_scheduler", _ALERT_NAME)
    runbook_rel = alert["annotations"]["runbook_url"]
    assert runbook_rel == "docs/runbooks/sync-target-stalled-drift.md"
    assert (_REPO_ROOT / runbook_rel).exists(), (
        "runbook_url points at a non-existent file: %s" % runbook_rel
    )
