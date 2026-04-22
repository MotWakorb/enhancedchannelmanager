"""
Integration tests for bd-eio04.7 write-time pattern linting.

Negative tests: each POST/PUT endpoint returns 422 with the
``REGEX_VALIDATION_ERROR`` envelope when a pathological pattern is
submitted. Positive tests: a benign pattern produces a 200/201.

Also verifies the new ``GET /lint-findings`` endpoints return the
findings written by the migration scan.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from models import (
    AutoCreationRule,
    DummyEPGProfile,
    NormalizationRule,
    NormalizationRuleGroup,
    RuleLintFinding,
)


EVIL_PATTERN = r"(a+)+b"  # nested unbounded quantifier with killer


# =========================================================================
# Normalization router.
# =========================================================================


@pytest.fixture
def norm_group(test_session):
    group = NormalizationRuleGroup(
        name="Test Group", enabled=True, priority=0, is_builtin=False
    )
    test_session.add(group)
    test_session.commit()
    test_session.refresh(group)
    return group


class TestNormalizationCreateRuleLinting:
    @pytest.mark.asyncio
    async def test_rejects_evil_condition_value(self, async_client, norm_group):
        payload = {
            "group_id": norm_group.id,
            "name": "Evil Rule",
            "condition_type": "regex",
            "condition_value": EVIL_PATTERN,
            "action_type": "remove",
        }
        response = await async_client.post("/api/normalization/rules", json=payload)
        assert response.status_code == 422
        body = response.json()
        # FastAPI wraps our custom detail under "detail".
        err = body["detail"]["error"]
        assert err["code"] == "REGEX_VALIDATION_ERROR"
        details = err["details"]
        assert len(details) == 1
        assert details[0]["field"] == "condition_value"
        assert details[0]["code"] == "REGEX_NESTED_QUANTIFIER"

    @pytest.mark.asyncio
    async def test_rejects_oversize_condition_value(self, async_client, norm_group):
        payload = {
            "group_id": norm_group.id,
            "name": "Oversize",
            "condition_type": "regex",
            "condition_value": "a" * 600,
            "action_type": "remove",
        }
        response = await async_client.post("/api/normalization/rules", json=payload)
        assert response.status_code == 422
        err = response.json()["detail"]["error"]
        assert err["details"][0]["code"] == "REGEX_TOO_LONG"

    @pytest.mark.asyncio
    async def test_rejects_uncompilable_condition_value(
        self, async_client, norm_group
    ):
        payload = {
            "group_id": norm_group.id,
            "name": "Bad syntax",
            "condition_type": "regex",
            "condition_value": r"^(unclosed",
            "action_type": "remove",
        }
        response = await async_client.post("/api/normalization/rules", json=payload)
        assert response.status_code == 422
        err = response.json()["detail"]["error"]
        assert err["details"][0]["code"] == "REGEX_COMPILE_ERROR"

    @pytest.mark.asyncio
    async def test_accepts_benign_pattern(self, async_client, norm_group):
        payload = {
            "group_id": norm_group.id,
            "name": "Strip HD",
            "condition_type": "regex",
            "condition_value": r"\s*HD$",
            "action_type": "remove",
        }
        response = await async_client.post("/api/normalization/rules", json=payload)
        assert response.status_code == 200
        assert response.json()["name"] == "Strip HD"

    @pytest.mark.asyncio
    async def test_skips_lint_when_condition_type_not_regex(
        self, async_client, norm_group
    ):
        """Non-regex condition types pass even if the value looks pathological
        as a regex — it's treated as a literal string, not a pattern."""
        payload = {
            "group_id": norm_group.id,
            "name": "Literal contains",
            "condition_type": "contains",
            "condition_value": EVIL_PATTERN,  # literal — fine
            "action_type": "remove",
        }
        response = await async_client.post("/api/normalization/rules", json=payload)
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_compound_conditions_are_linted(self, async_client, norm_group):
        payload = {
            "group_id": norm_group.id,
            "name": "Compound evil",
            "action_type": "remove",
            "conditions": [
                {"type": "regex", "value": EVIL_PATTERN},
            ],
        }
        response = await async_client.post("/api/normalization/rules", json=payload)
        assert response.status_code == 422
        err = response.json()["detail"]["error"]
        assert err["details"][0]["field"] == "conditions[0].value"


class TestNormalizationUpdateRuleLinting:
    @pytest.mark.asyncio
    async def test_patch_rejects_evil_pattern(
        self, async_client, test_session, norm_group
    ):
        rule = NormalizationRule(
            group_id=norm_group.id,
            name="Initial",
            condition_type="regex",
            condition_value=r"\s*HD$",
            action_type="remove",
        )
        test_session.add(rule)
        test_session.commit()
        test_session.refresh(rule)

        response = await async_client.patch(
            f"/api/normalization/rules/{rule.id}",
            json={"condition_type": "regex", "condition_value": EVIL_PATTERN},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_patch_allows_unrelated_field_change(
        self, async_client, test_session, norm_group
    ):
        """Renaming a rule without touching the pattern MUST pass, even
        if the stored pattern would fail a fresh lint — pre-lint rows
        are surfaced by the startup scan, not the update path."""
        rule = NormalizationRule(
            group_id=norm_group.id,
            name="Initial",
            condition_type="regex",
            condition_value=EVIL_PATTERN,  # pre-lint
            action_type="remove",
        )
        test_session.add(rule)
        test_session.commit()
        test_session.refresh(rule)

        response = await async_client.patch(
            f"/api/normalization/rules/{rule.id}",
            json={"name": "Renamed"},
        )
        assert response.status_code == 200
        assert response.json()["name"] == "Renamed"


# =========================================================================
# Auto-creation router.
# =========================================================================


class TestAutoCreationCreateRuleLinting:
    @pytest.mark.asyncio
    async def test_rejects_evil_sort_regex(self, async_client):
        payload = {
            "name": "Evil sort",
            "conditions": [{"type": "stream_name_contains", "value": "ESPN"}],
            "actions": [{"type": "create_channel", "name_template": "{stream_name}"}],
            "sort_regex": EVIL_PATTERN,
        }
        response = await async_client.post("/api/auto-creation/rules", json=payload)
        assert response.status_code == 422
        err = response.json()["detail"]["error"]
        assert err["details"][0]["field"] == "sort_regex"
        assert err["details"][0]["code"] == "REGEX_NESTED_QUANTIFIER"

    @pytest.mark.asyncio
    async def test_rejects_evil_condition_value(self, async_client):
        payload = {
            "name": "Evil cond",
            "conditions": [{"type": "stream_name_matches", "value": EVIL_PATTERN}],
            "actions": [{"type": "create_channel", "name_template": "{stream_name}"}],
        }
        response = await async_client.post("/api/auto-creation/rules", json=payload)
        assert response.status_code == 422
        err = response.json()["detail"]["error"]
        assert err["details"][0]["field"] == "conditions[0].value"

    @pytest.mark.asyncio
    async def test_rejects_evil_action_name_transform(self, async_client):
        payload = {
            "name": "Evil name transform",
            "conditions": [{"type": "stream_name_contains", "value": "ESPN"}],
            "actions": [
                {
                    "type": "create_channel",
                    "name_template": "{stream_name}",
                    "name_transform_pattern": EVIL_PATTERN,
                    "name_transform_replacement": "",
                }
            ],
        }
        response = await async_client.post("/api/auto-creation/rules", json=payload)
        assert response.status_code == 422
        err = response.json()["detail"]["error"]
        assert err["details"][0]["field"] == "actions[0].name_transform_pattern"

    @pytest.mark.asyncio
    async def test_rejects_evil_set_variable_pattern(self, async_client):
        payload = {
            "name": "Evil set var",
            "conditions": [{"type": "stream_name_contains", "value": "ESPN"}],
            "actions": [
                {
                    "type": "set_variable",
                    "variable_name": "foo",
                    "variable_mode": "regex_extract",
                    "source_field": "stream_name",
                    "pattern": EVIL_PATTERN,
                }
            ],
        }
        response = await async_client.post("/api/auto-creation/rules", json=payload)
        assert response.status_code == 422
        err = response.json()["detail"]["error"]
        assert err["details"][0]["field"] == "actions[0].pattern"

    @pytest.mark.asyncio
    async def test_accepts_benign_rule(self, async_client):
        payload = {
            "name": "Benign",
            "conditions": [{"type": "stream_name_contains", "value": "ESPN"}],
            "actions": [{"type": "create_channel", "name_template": "{stream_name}"}],
            "sort_regex": r"(\d+)$",
        }
        response = await async_client.post("/api/auto-creation/rules", json=payload)
        assert response.status_code == 200


class TestAutoCreationUpdateRuleLinting:
    @pytest.mark.asyncio
    async def test_put_rejects_evil_pattern(self, async_client, test_session):
        rule = AutoCreationRule(
            name="Initial",
            conditions=json.dumps(
                [{"type": "stream_name_contains", "value": "ESPN"}]
            ),
            actions=json.dumps(
                [{"type": "create_channel", "name_template": "{stream_name}"}]
            ),
        )
        test_session.add(rule)
        test_session.commit()
        test_session.refresh(rule)

        response = await async_client.put(
            f"/api/auto-creation/rules/{rule.id}",
            json={"sort_regex": EVIL_PATTERN},
        )
        assert response.status_code == 422


# =========================================================================
# Dummy-EPG router.
# =========================================================================


class TestDummyEPGCreateProfileLinting:
    @pytest.mark.asyncio
    async def test_rejects_evil_title_pattern(self, async_client):
        payload = {
            "name": "Evil title",
            "title_pattern": EVIL_PATTERN,
        }
        response = await async_client.post("/api/dummy-epg/profiles", json=payload)
        assert response.status_code == 422
        err = response.json()["detail"]["error"]
        assert err["details"][0]["field"] == "title_pattern"

    @pytest.mark.asyncio
    async def test_rejects_evil_substitution_find_when_is_regex(self, async_client):
        payload = {
            "name": "Evil sub",
            "substitution_pairs": [
                {"find": EVIL_PATTERN, "replace": "", "is_regex": True, "enabled": True}
            ],
        }
        response = await async_client.post("/api/dummy-epg/profiles", json=payload)
        assert response.status_code == 422
        err = response.json()["detail"]["error"]
        assert err["details"][0]["field"] == "substitution_pairs[0].find"

    @pytest.mark.asyncio
    async def test_accepts_literal_substitution_find(self, async_client):
        """Literal (non-regex) substitution pairs are not linted."""
        payload = {
            "name": "Literal sub",
            "substitution_pairs": [
                {"find": EVIL_PATTERN, "replace": "", "is_regex": False, "enabled": True}
            ],
        }
        response = await async_client.post("/api/dummy-epg/profiles", json=payload)
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_accepts_b1g_title_pattern(self, async_client):
        """The spike's critical regression case — B1G production
        ``title_pattern`` MUST be accepted (regexploit falsely flagged
        it; hand-rolled correctly passes)."""
        b1g_pattern = (
            r"(?<channel>.+?):\s+(?<event>"
            r"(?:(?<sport>.+?)\s+\|\s+(?<team1>.+?)\s+vs\s+(?<team2>.+?))"
            r"|(?:.+?))\s+@\s+"
        )
        payload = {
            "name": "B1G Advanced EPG",
            "title_pattern": b1g_pattern,
        }
        response = await async_client.post("/api/dummy-epg/profiles", json=payload)
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_accepts_all_three_benign_patterns(self, async_client):
        payload = {
            "name": "Three good patterns",
            "title_pattern": r"(?P<title>.+)",
            "time_pattern": r"(?P<hour>\d+):(?P<minute>\d+)",
            "date_pattern": r"(?P<month>\d{2})/(?P<day>\d{2})",
        }
        response = await async_client.post("/api/dummy-epg/profiles", json=payload)
        assert response.status_code == 200


class TestDummyEPGUpdateProfileLinting:
    @pytest.mark.asyncio
    async def test_patch_rejects_evil_pattern(self, async_client, test_session):
        profile = DummyEPGProfile(
            name="Initial",
            title_pattern=r"(?P<title>.+)",
        )
        test_session.add(profile)
        test_session.commit()
        test_session.refresh(profile)

        response = await async_client.patch(
            f"/api/dummy-epg/profiles/{profile.id}",
            json={"title_pattern": EVIL_PATTERN},
        )
        assert response.status_code == 422


# =========================================================================
# GET /lint-findings endpoints.
# =========================================================================


class TestLintFindingsEndpoints:
    @pytest.mark.asyncio
    async def test_normalization_lint_findings_empty(self, async_client):
        response = await async_client.get("/api/normalization/lint-findings")
        assert response.status_code == 200
        assert response.json() == {"findings": []}

    @pytest.mark.asyncio
    async def test_normalization_lint_findings_returns_seeded_rows(
        self, async_client, test_session
    ):
        finding = RuleLintFinding(
            rule_type="normalization",
            rule_id=99,
            field="condition_value",
            code="REGEX_NESTED_QUANTIFIER",
            message="Pattern contains a nested unbounded quantifier ...",
            detail=json.dumps({"reason": "nested-unbounded-repeat-with-killer"}),
            detected_at=datetime.utcnow(),
        )
        test_session.add(finding)
        test_session.commit()

        response = await async_client.get("/api/normalization/lint-findings")
        assert response.status_code == 200
        data = response.json()
        assert len(data["findings"]) == 1
        f = data["findings"][0]
        assert f["rule_type"] == "normalization"
        assert f["rule_id"] == 99
        assert f["code"] == "REGEX_NESTED_QUANTIFIER"
        assert f["detail"] == {"reason": "nested-unbounded-repeat-with-killer"}

    @pytest.mark.asyncio
    async def test_lint_findings_scoped_by_rule_type(
        self, async_client, test_session
    ):
        """Each endpoint returns only its own ``rule_type`` — not a
        shared blob of all findings."""
        now = datetime.utcnow()
        test_session.add_all([
            RuleLintFinding(
                rule_type="normalization",
                rule_id=1,
                field="condition_value",
                code="REGEX_NESTED_QUANTIFIER",
                message="norm",
                detected_at=now,
            ),
            RuleLintFinding(
                rule_type="auto_creation",
                rule_id=2,
                field="sort_regex",
                code="REGEX_TOO_LONG",
                message="ac",
                detected_at=now,
            ),
            RuleLintFinding(
                rule_type="dummy_epg",
                rule_id=3,
                field="title_pattern",
                code="REGEX_COMPILE_ERROR",
                message="de",
                detected_at=now,
            ),
        ])
        test_session.commit()

        norm = (await async_client.get("/api/normalization/lint-findings")).json()
        ac = (await async_client.get("/api/auto-creation/lint-findings")).json()
        de = (await async_client.get("/api/dummy-epg/lint-findings")).json()

        assert [f["rule_id"] for f in norm["findings"]] == [1]
        assert [f["rule_id"] for f in ac["findings"]] == [2]
        assert [f["rule_id"] for f in de["findings"]] == [3]
