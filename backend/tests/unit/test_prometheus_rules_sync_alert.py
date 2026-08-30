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

    # Staleness expression keyed on the FULL-APPLY freshness gauge (PR #752
    # review, Block 2). The generic ecm_task_schedule_last_success_timestamp
    # is stamped by the task engine on ANY successful run — including a
    # dry-run PREVIEW, which writes nothing to B — so keying the drift alert
    # on it let a recurring preview mask real divergence. The dedicated gauge
    # is stamped only on an APPLY with a clean SUCCESS outcome.
    assert "ecm_sync_last_full_success_timestamp" in expr
    assert "ecm_task_schedule_last_success_timestamp" not in expr, (
        "the generic per-task gauge is stamped on dry-run previews too — the "
        "drift alert must key on the apply-only gauge"
    )
    # Per-target evaluation (7ipq2.3 / ADR-013 S6) is a property of the
    # metric's LABELS, not of the expression: PromQL evaluates a labeled
    # vector per series, so one healthy replica cannot reset another's
    # staleness clock without any matcher in the expr. The label's presence
    # is pinned by the annotation test below (it can only render if the
    # series carries it) and by the gauge's labelnames, exercised in
    # backend/tests/tasks/test_dbas_sync_concurrency.py.
    assert 'task_id="dbas_sync"' not in expr
    assert "time() -" in expr
    # 3h budget = 3 missed hourly runs (ADR-013 S9 cheap-reads cadence basis).
    assert "10800" in expr
    # Fresh-install / operator-disabled guard — mirrors every sibling alert.
    assert "AND ecm_sync_last_full_success_timestamp > 0" in expr


def test_sync_alert_annotations_reference_the_target_label(_rules):
    """The responder needs to know WHICH target drifted — the summary must
    carry the per-target label, not a bare task name."""
    alert = _find_alert(_rules, "ecm_task_scheduler", _ALERT_NAME)
    assert "$labels.sync_target_id" in alert["annotations"]["summary"]


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
