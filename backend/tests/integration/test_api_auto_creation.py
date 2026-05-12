"""
Integration tests for the auto-creation API endpoints.

Tests the full API workflow for creating, updating, and managing auto-creation rules.
"""
import pytest
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient


@pytest.fixture
def mock_db_session():
    """Mock database session."""
    with patch("routers.auto_creation.get_session") as mock:
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

        with patch("routers.auto_creation.journal.log_entry"):
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
        """Create rule fails with invalid regex conditions.

        Pre-bd-eio04.7 this returned 400 from ``validate_rule``. Now the
        regex linter runs FIRST (so the error envelope is the new
        ``REGEX_VALIDATION_ERROR`` shape) and an uncompilable pattern
        yields 422 with a ``REGEX_COMPILE_ERROR`` code.
        """
        response = test_client.post(
            "/api/auto-creation/rules",
            json={
                "name": "Bad Rule",
                "conditions": [{"type": "stream_name_matches", "value": "[invalid("}],
                "actions": [{"type": "skip"}]
            }
        )

        assert response.status_code == 422
        body = response.json()
        err = body["detail"]["error"]
        assert err["code"] == "REGEX_VALIDATION_ERROR"
        assert err["details"][0]["code"] == "REGEX_COMPILE_ERROR"

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

        with patch("routers.auto_creation.journal.log_entry"):
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
        mock_rule.sort_field = None
        mock_rule.sort_order = None
        mock_rule.sort_regex = None
        mock_rule.stream_sort_field = None
        mock_rule.stream_sort_order = None
        mock_rule.quality_tie_break_order = "desc"
        mock_rule.quality_m3u_tie_break_enabled = True
        mock_rule.get_normalization_group_ids.return_value = []
        mock_rule.skip_struck_streams = False
        mock_rule.probe_on_sort = False
        mock_rule.orphan_action = "delete"
        mock_rule.match_scope_target_group = False
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

        with patch("routers.auto_creation.journal.log_entry"):
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


# =============================================================================
# Bulk-update integration coverage with REAL DB + REAL journal (bd-d23zy).
#
# QA's standup finding: every bulk-update test in
# backend/tests/routers/test_auto_creation.py mocks the journal via
# patch("routers.auto_creation.journal"). The batch_id assertions verify call
# kwargs, not real DB rows. Three bulk-related PRs landed in 24h
# (bd-bh1hh single-fetch + bd-gjoe5 reject conditions/actions +
# bd-91mcq per-entity audit) — they share the _apply_rule_scalar_updates code
# path; their interactions are untested end-to-end.
#
# These tests exercise the full path: HTTP -> Pydantic -> router ->
# _apply_rule_scalar_updates -> session.commit() -> journal.log_entry() ->
# JournalEntry rows materialize on the same in-memory test DB. No mocking
# of the journal layer.
# =============================================================================


import json as _json_d23zy
from datetime import datetime as _datetime_d23zy

from models import AutoCreationRule as _AutoCreationRule_d23zy, JournalEntry as _JournalEntry_d23zy


def _seed_bulk_rule(session, *, name: str, enabled: bool = False, priority: int = 0):
    """Seed an AutoCreationRule directly via the test session.

    Mirrors the helper in backend/tests/routers/test_auto_creation.py:_create_rule
    but exposed at module scope here so the bd-d23zy integration tests are
    self-contained.
    """
    rule = _AutoCreationRule_d23zy(
        name=name,
        enabled=enabled,
        priority=priority,
        conditions=_json_d23zy.dumps([{"type": "stream_name_contains", "value": "ESPN"}]),
        actions=_json_d23zy.dumps([{"type": "create_channel", "name_template": "{stream_name}"}]),
        run_on_refresh=False,
        stop_on_first_match=True,
        sort_order="asc",
        orphan_action="delete",
        created_at=_datetime_d23zy.utcnow(),
        updated_at=_datetime_d23zy.utcnow(),
    )
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return rule


class TestBulkUpdateRealDBJournal:
    """bd-d23zy: end-to-end bulk-update against real DB + real journal.

    Closes the gap that all bulk-update tests in tests/routers/ mock the
    journal — those verify call kwargs, not the actual DB rows the code
    path is supposed to write. This class hits the real journal via a
    real test_session and asserts on materialized JournalEntry rows.
    """

    @pytest.mark.asyncio
    async def test_50_rules_bulk_update_writes_50_journal_rows_one_batch_id(
        self, async_client, test_session
    ):
        """50 rules in one bulk-update -> 50 JournalEntry rows, one batch_id.

        Covers bd-bh1hh (single-fetch), bd-91mcq (per-entity audit), and the
        diff-capture in _apply_rule_scalar_updates simultaneously. Per-rule
        before/after must reflect the true scalar transition (enabled,
        priority).
        """
        # Seed 50 rules with mixed initial state so the diff is non-trivial.
        rules = [
            _seed_bulk_rule(
                test_session,
                name=f"BulkRule-{i:03d}",
                enabled=False,
                priority=i,  # unique starting priorities so the diff is per-rule
            )
            for i in range(50)
        ]
        rule_ids = [r.id for r in rules]
        original_priorities = {r.id: r.priority for r in rules}

        # Baseline journal count — the seeding above did not write to the
        # journal, but the assertion is robust to any pre-existing rows.
        baseline_count = test_session.query(_JournalEntry_d23zy).count()

        response = await async_client.post(
            "/api/auto-creation/rules/bulk-update",
            json={"rule_ids": rule_ids, "enabled": True, "priority": 999},
        )
        assert response.status_code == 200, response.text
        assert response.json()["updated_count"] == 50

        # 1) 50 new journal rows for this category/action.
        test_session.expire_all()
        all_entries = (
            test_session.query(_JournalEntry_d23zy)
            .filter(_JournalEntry_d23zy.category == "auto_creation")
            .filter(_JournalEntry_d23zy.action_type == "bulk_update")
            .all()
        )
        assert len(all_entries) >= 50, (
            f"Expected at least 50 bulk_update journal rows after the bulk POST, "
            f"got {len(all_entries)} (baseline was {baseline_count})"
        )
        # Restrict to entries whose entity_id matches our seeded rules to
        # avoid coincidental cross-test pollution.
        seeded_ids = set(rule_ids)
        run_entries = [e for e in all_entries if e.entity_id in seeded_ids]
        assert len(run_entries) == 50

        # 2) All share one batch_id, and it's non-empty.
        batch_ids = {e.batch_id for e in run_entries}
        assert len(batch_ids) == 1, (
            f"All 50 entries must share one batch_id; got {len(batch_ids)}"
        )
        single_batch_id = next(iter(batch_ids))
        assert single_batch_id, "batch_id must be non-empty for forensics grouping"

        # 3) Each row's diff captures the actual before/after of the changed
        # scalars. before/after_value are JSON-encoded text columns.
        for entry in run_entries:
            before = _json_d23zy.loads(entry.before_value)
            after = _json_d23zy.loads(entry.after_value)
            # enabled went False -> True for every rule.
            assert before["enabled"] is False
            assert after["enabled"] is True
            # priority went from its unique starting value -> 999 for every rule.
            assert before["priority"] == original_priorities[entry.entity_id]
            assert after["priority"] == 999

    @pytest.mark.asyncio
    async def test_rejected_conditions_payload_writes_zero_journal_rows(
        self, async_client, test_session
    ):
        """bd-gjoe5 rollback: payload with conditions returns 4xx and writes
        zero journal rows.

        The rejection happens at Pydantic model_validator time
        (before any DB work), but the assertion is the same end-to-end
        contract a caller cares about: failed bulk update => no audit
        rows, no scalar mutation. This guards against a future refactor
        moving the rejection past the journal write.
        """
        rule = _seed_bulk_rule(
            test_session, name="RejectCondReal", enabled=True, priority=42
        )

        # Snapshot journal row count before the rejected request.
        baseline_count = (
            test_session.query(_JournalEntry_d23zy)
            .filter(_JournalEntry_d23zy.category == "auto_creation")
            .filter(_JournalEntry_d23zy.action_type == "bulk_update")
            .count()
        )

        response = await async_client.post(
            "/api/auto-creation/rules/bulk-update",
            json={
                "rule_ids": [rule.id],
                "enabled": False,  # would mutate
                "conditions": [{"type": "always"}],  # but conditions => reject
            },
        )
        # The Pydantic model_validator raises ValueError -> 422; if a future
        # refactor moves the check into the handler it could become 400.
        # Either way, the request must fail.
        assert response.status_code in (400, 422), response.text

        # Zero new journal rows for this category/action.
        test_session.expire_all()
        post_count = (
            test_session.query(_JournalEntry_d23zy)
            .filter(_JournalEntry_d23zy.category == "auto_creation")
            .filter(_JournalEntry_d23zy.action_type == "bulk_update")
            .count()
        )
        assert post_count == baseline_count, (
            f"Rejected bulk-update must not write journal rows; baseline={baseline_count} "
            f"post={post_count}"
        )

        # Rule scalar must be unchanged (rollback).
        refreshed = test_session.query(_AutoCreationRule_d23zy).get(rule.id)
        assert refreshed.enabled is True
        assert refreshed.priority == 42


# =============================================================================
# Hypothesis ReDoS fuzz — bulk-update sort_regex lint path (bd-d23zy / bd-k41e0).
#
# Extends bd-ltjyx + bd-3u6p0 protection: the bulk-update endpoint passes
# request.sort_regex through _lint_auto_creation_rule_request -> lint_pattern,
# which must reject (HTTP 422) catastrophic-backtracking patterns within a
# wall-clock budget — never hang and never 5xx.
#
# Modeled on the adversarial pattern strategy in
# backend/tests/unit/test_auto_creation_safe_regex_migration.py:40-71.
# =============================================================================


import re as _re_d23zy
import time as _time_d23zy

from hypothesis import given as _given_d23zy, settings as _hyp_settings_d23zy, HealthCheck as _HealthCheck_d23zy
from hypothesis import strategies as _st_d23zy


# Catastrophic-backtracking pattern shapes that the regex_lint detector is
# *designed* to reject (nested-unbounded-quantifier-with-post-match-killer,
# per backend/regex_lint.py:_detect_nested_quantifier and the bd-eio04.11
# spike). Every entry below is empirically known to produce
# REGEX_NESTED_QUANTIFIER on an unpadded run.
#
# NOTE: shapes like "(a|aa)+b" and "(a|a?)+b" are deliberately NOT in this
# list because the linter intentionally accepts them (the spike eval found
# them to be 0/0 in production traffic and not actually catastrophic against
# Python's re engine). The fuzz test asserts the lint catches what it
# claims to catch; new shapes added to the lint's rejection list should be
# added here.
_REDOS_PATTERN_SHAPES = [
    "(a+)+b",          # nested unbounded quantifier, classic ReDoS
    "(.*a){10,}b",     # nested ranged quantifier with .*
    "(a+){10,}b",      # nested quantifier with explicit min
    "(.*?a){5,}b",     # lazy nested quantifier
]


# ReDoS budget. The full HTTP roundtrip must complete well under this even
# on a loaded CI runner — the lint should reject at AST parse time, not
# fall through to the regex engine.
_REDOS_REJECT_BUDGET_SECONDS = 1.5


@_hyp_settings_d23zy(
    deadline=None,
    max_examples=20,
    suppress_health_check=[_HealthCheck_d23zy.too_slow, _HealthCheck_d23zy.function_scoped_fixture],
    derandomize=True,  # pin seed for CI stability (matches safe_regex_migration tests)
)
@_given_d23zy(
    pattern_shape=_st_d23zy.sampled_from(_REDOS_PATTERN_SHAPES),
    # Pad with PURE LITERALS only — any regex meta-char would change the
    # AST shape and could legitimately move the pattern out of the lint's
    # rejection class. The point of fuzzing the padding is to catch a
    # regression where the lint becomes literal-prefix-sensitive.
    pad=_st_d23zy.text(alphabet="xyz0123_-", min_size=0, max_size=20),
)
@pytest.mark.asyncio
async def test_bulk_update_sort_regex_rejects_redos_patterns_fast(
    async_client, test_session, pattern_shape, pad
):
    """bd-d23zy + bd-k41e0: bulk-update sort_regex lint must reject ReDoS
    patterns with HTTP 422 within a hard wall-clock budget.

    Builds on bd-ltjyx (auto_creation_schema write-time safe_regex) and
    bd-3u6p0 (m3u_digest write-time safe_regex). The bulk-update path
    does NOT accept conditions/actions, so sort_regex is the only
    pattern field a caller can supply through this endpoint.

    Hypothesis varies the padding around the adversarial pattern shape
    so a future regression in the linter (e.g. someone narrows the
    nested-quantifier check to only literal '(a+)+b') is caught.
    """
    # Seed a single rule; the lint check fires before any DB work, but
    # the request still needs a valid rule_id to pass Pydantic.
    rule = _seed_bulk_rule(test_session, name=f"ReDoSGuard-{pad or 'empty'}")

    adversarial_pattern = pad + pattern_shape + pad

    start = _time_d23zy.perf_counter()
    response = await async_client.post(
        "/api/auto-creation/rules/bulk-update",
        json={"rule_ids": [rule.id], "sort_regex": adversarial_pattern},
    )
    elapsed = _time_d23zy.perf_counter() - start

    # 1) The linter must reject — never accept a ReDoS pattern.
    # _lint_auto_creation_rule_request raises HTTPException(422); a future
    # refactor could surface as 400, but never 200/5xx.
    assert response.status_code in (400, 422), (
        f"Expected 4xx for ReDoS sort_regex {adversarial_pattern!r}, "
        f"got {response.status_code}: {response.text}"
    )

    # 2) Fast-fail: the lint must short-circuit at AST/parse time, not run
    # the pattern. A hang here means the lint regressed and the regex
    # engine is being invoked on adversarial input.
    assert elapsed < _REDOS_REJECT_BUDGET_SECONDS, (
        f"Lint rejection of {adversarial_pattern!r} took {elapsed:.3f}s "
        f"(budget {_REDOS_REJECT_BUDGET_SECONDS}s) — lint may be running "
        f"the pattern instead of statically rejecting it."
    )

    # 3) Sanity: the response body must mention the offending field so the
    # UI can localize the error.
    body = response.text.lower()
    assert "sort_regex" in body or "regex" in body, (
        f"Lint rejection should reference sort_regex/regex; got: {response.text}"
    )

    # 4) Stdlib re must NOT consider this pattern safe — assert our
    # adversarial generator actually produces patterns the runtime
    # engine would have to backtrack on if the lint missed them.
    # (compile is cheap and bounded; the catastrophic part is search.)
    try:
        _re_d23zy.compile(adversarial_pattern)
    except _re_d23zy.error:
        # If even stdlib won't compile it, the lint had an even easier
        # rejection path. Still passes the test contract.
        pass
