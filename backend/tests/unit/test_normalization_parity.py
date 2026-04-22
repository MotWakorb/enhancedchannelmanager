"""
Parity tests for NormalizationEngine — Test Rules vs. Auto-Create (bd-eio04.1).

Closes the bug class behind GH #104: the Test Rules preview path
(`engine.test_rule`, `engine.test_rules_batch`) and the auto-creation
execution path (`engine.normalize`) must produce byte-identical output
for the same input. Any divergence between the two paths is the exact
failure mode users reported ("Settings → Normalization Test shows HD but
the created channel still says ᴴᴰ").

Two variants per grooming decision 1 (2026-04-22):

Variant A — per-rule equivalence
    For every rule R in a seeded rule set and every fixture s:
        engine.test_rule(s, R) ==  step_of_normalize(s, R)
    where step_of_normalize is a test-only helper that runs exactly one
    rule on s with the same preprocessing the full normalize() pipeline
    applies (NormalizationPolicy).

Variant B — full-pipeline equivalence
    For every fixture s:
        engine.test_rules_batch([s])[0].normalized == engine.normalize(s).normalized

Fixtures are imported from `backend/tests/fixtures/unicode_fixtures.py`
(bd-eio04.3's bank). Do not invent one-off Unicode strings here — extend
the bank instead.
"""
from __future__ import annotations

import re

import pytest

from normalization_engine import (
    NormalizationEngine,
    _tag_group_cache,
)
from tests.fixtures.factories import (
    create_normalization_rule,
    create_normalization_rule_group,
)
from tests.fixtures.unicode_fixtures import (
    ALL_FIXTURES,
    LETTER_SUPERSCRIPT_FIXTURES,
    MIXED_FIXTURES,
    NFC_NFD_FIXTURES,
    NUMERIC_SUPERSCRIPT_FIXTURES,
    ZERO_WIDTH_FIXTURES,
    NormalizationFixture,
)


# -----------------------------------------------------------------------------
# Critical pinned regressions — named so failures are greppable.
# These MUST be part of the parity sweep or the bug class slips back in.
# -----------------------------------------------------------------------------

_PINNED_FIXTURE_NAMES = {
    "case_issue104_espn_hd",        # GH #104 reporter input
    "case_issue104_fox_sports_2",   # GH #104 second reporter
    "case_bd_yui1k_numeric_strip",  # proves superscript-numeric-preservation drop
    "case_nfd_decomposition_cafe",  # proves NFC canonicalization (bd-eio04.4)
}


def _find_fixture(name: str) -> NormalizationFixture:
    for f in ALL_FIXTURES:
        if f.name == name:
            return f
    raise KeyError(f"fixture {name!r} missing from bank")


@pytest.fixture(autouse=True)
def _clear_tag_cache():
    """Each parametrized test must see a clean tag-group cache."""
    _tag_group_cache.clear()
    yield
    _tag_group_cache.clear()


@pytest.fixture
def engine(test_session):
    return NormalizationEngine(test_session)


@pytest.fixture
def seeded_rules(test_session):
    """A small but deliberately diverse rule set covering condition types that
    historically diverged between Test Rules and Auto-Create.

    Each rule lives in its own group so per-rule equivalence (Variant A) can
    filter the normalize() pipeline to exactly one rule via group_ids.
    """
    rules = []

    # Rule 1: contains "HD" -> replace with "HD" (identity-ish — proves
    # that superscript-preprocessing happens before condition matching).
    g1 = create_normalization_rule_group(
        test_session, name="contains-hd", enabled=True,
    )
    r1 = create_normalization_rule(
        test_session, group_id=g1.id,
        name="contains-hd",
        condition_type="contains",
        condition_value="HD",
        action_type="replace",
        action_value="HD",
    )
    rules.append((g1, r1))

    # Rule 2: contains "Café" -> replace with "Cafe" (proves NFC canonical
    # form matters: NFD-decomposed input will only match if both sides
    # NFC-canonicalize through the same policy).
    g2 = create_normalization_rule_group(
        test_session, name="contains-cafe", enabled=True,
    )
    r2 = create_normalization_rule(
        test_session, group_id=g2.id,
        name="contains-cafe",
        condition_type="contains",
        condition_value="Café",
        action_type="replace",
        action_value="Cafe",
    )
    rules.append((g2, r2))

    # Rule 3: contains "ESPN" -> remove "ESPN".
    g3 = create_normalization_rule_group(
        test_session, name="contains-espn", enabled=True,
    )
    r3 = create_normalization_rule(
        test_session, group_id=g3.id,
        name="contains-espn",
        condition_type="contains",
        condition_value="ESPN",
        action_type="remove",
    )
    rules.append((g3, r3))

    # Rule 4: regex (?i)fps -> replace with FPS. Exercises regex path,
    # which also went through _match_single_condition.
    g4 = create_normalization_rule_group(
        test_session, name="regex-fps", enabled=True,
    )
    r4 = create_normalization_rule(
        test_session, group_id=g4.id,
        name="regex-fps",
        condition_type="regex",
        condition_value=r"fps",
        action_type="replace",
        action_value="FPS",
    )
    rules.append((g4, r4))

    # Rule 5: always -> capitalize (title). Proves preprocessing happens
    # even when the condition doesn't itself inspect the text.
    g5 = create_normalization_rule_group(
        test_session, name="always-title", enabled=True,
    )
    r5 = create_normalization_rule(
        test_session, group_id=g5.id,
        name="always-title",
        condition_type="always",
        condition_value="",
        action_type="capitalize",
        action_value="title",
    )
    rules.append((g5, r5))

    return rules


def _step_of_normalize(engine: NormalizationEngine, text: str, group_id: int) -> str:
    """Run exactly ONE rule-group through the full normalize() pipeline.

    This is the "per-rule effect" of normalize() — the same preprocessing
    (NormalizationPolicy), the same whitespace-collapse, the same
    multi-pass loop, but filtered to a single group so we're comparing
    apples to apples with test_rule's one-rule-at-a-time semantics.
    """
    return engine.normalize(text, group_ids=[group_id]).normalized


# =============================================================================
# Variant A — per-rule equivalence
# =============================================================================

# Build the parametrize matrix: every fixture × every rule name we want to
# exercise. Use fixture.name and rule name as the pytest id so failures
# point at exactly which pair diverged.
_PARITY_PAIRS = [
    pytest.param(f, rule_name, id=f"{f.name}__{rule_name}")
    for f in ALL_FIXTURES
    for rule_name in (
        "contains-hd",
        "contains-cafe",
        "contains-espn",
        "regex-fps",
        "always-title",
    )
]


@pytest.mark.parametrize("fixture, rule_name", _PARITY_PAIRS)
def test_per_rule_equivalence(
    engine, seeded_rules, fixture: NormalizationFixture, rule_name: str
):
    """Variant A: test_rule(s, R) == step_of_normalize(s, R) for all (s, R)."""
    group, rule = next((g, r) for g, r in seeded_rules if r.name == rule_name)

    # test_rule path — what the Test Rules preview produces.
    preview = engine.test_rule(
        text=fixture.input,
        condition_type=rule.condition_type,
        condition_value=rule.condition_value,
        case_sensitive=rule.case_sensitive,
        action_type=rule.action_type,
        action_value=rule.action_value or "",
        tag_group_id=rule.tag_group_id,
        tag_match_position=rule.tag_match_position or "contains",
        else_action_type=rule.else_action_type,
        else_action_value=rule.else_action_value,
    )

    # step_of_normalize path — what a single-rule normalize() produces.
    via_normalize = _step_of_normalize(engine, fixture.input, group.id)

    assert preview["after"] == via_normalize, (
        f"Test Rules vs Auto-Create divergence for {fixture.name!r} "
        f"through rule {rule_name!r}:\n"
        f"  input:          {fixture.input!r}\n"
        f"  test_rule:      {preview['after']!r}\n"
        f"  normalize():    {via_normalize!r}"
    )


# =============================================================================
# Variant B — full-pipeline equivalence
# =============================================================================

@pytest.mark.parametrize(
    "fixture",
    ALL_FIXTURES,
    ids=[f.name for f in ALL_FIXTURES],
)
def test_full_pipeline_equivalence(engine, seeded_rules, fixture: NormalizationFixture):
    """Variant B: test_rules_batch([s])[0].normalized == normalize(s).normalized."""
    batched = engine.test_rules_batch([fixture.input])[0].normalized
    direct = engine.normalize(fixture.input).normalized

    assert batched == direct, (
        f"test_rules_batch vs normalize divergence for {fixture.name!r}:\n"
        f"  input:              {fixture.input!r}\n"
        f"  test_rules_batch:   {batched!r}\n"
        f"  normalize:          {direct!r}"
    )


# =============================================================================
# Pinned-regression spot checks — named, greppable, load-bearing.
# These assert the *expected* normalized output, not just parity.
# If parity holds but the output is wrong, these fail loudly.
# =============================================================================

class TestPinnedRegressions:
    """Named regressions that closed GH #104 — treat these as canaries."""

    def test_espn_hd_mixed_superscripts_through_normalize(self, engine):
        """US: ESPN ᴴᴰ ⁶⁰fps -> US: ESPN HD 60fps (letters AND numerics convert)."""
        f = _find_fixture("case_issue104_espn_hd")
        assert engine.normalize(f.input).normalized == f.expected_normalized

    def test_espn_hd_mixed_superscripts_through_test_rules_batch(self, engine):
        """Same input through the Test Rules path — must match normalize()."""
        f = _find_fixture("case_issue104_espn_hd")
        [result] = engine.test_rules_batch([f.input])
        assert result.normalized == f.expected_normalized

    def test_fox_sports_2_letter_superscripts_through_both_paths(self, engine):
        """|US| Fox Sports 2 ᴴᴰ US — letter superscripts convert on both paths."""
        f = _find_fixture("case_issue104_fox_sports_2")
        assert engine.normalize(f.input).normalized == f.expected_normalized
        [batch] = engine.test_rules_batch([f.input])
        assert batch.normalized == f.expected_normalized

    def test_bd_yui1k_numeric_strip_drops_preservation(self, engine):
        """ESPN ² -> ESPN 2 on ALL paths.

        Pre-bd-eio04.1: auto-creation path preserved ² via
        preserve_superscripts=True. Post: the preserve_superscripts kwarg
        is gone and numerics convert on every code path.
        """
        f = _find_fixture("case_bd_yui1k_numeric_strip")
        assert engine.normalize(f.input).normalized == f.expected_normalized
        [batch] = engine.test_rules_batch([f.input])
        assert batch.normalized == f.expected_normalized

    def test_nfd_cafe_normalizes_to_nfc(self, engine):
        """NFD-decomposed 'Café Sports' collapses to NFC pre-composed form.

        Pinned for bd-eio04.4 (bundled into .1): NFC canonicalization must
        happen inside NormalizationPolicy so NFD and NFC inputs produce
        byte-identical output.
        """
        import unicodedata
        f = _find_fixture("case_nfd_decomposition_cafe")
        normalized = engine.normalize(f.input).normalized
        # Assert the string is in NFC form (round-trip stability).
        assert normalized == unicodedata.normalize("NFC", normalized)
        # Assert the visible string equals the NFC-canonical expected.
        assert normalized == unicodedata.normalize("NFC", f.expected_normalized)


# =============================================================================
# Sanity checks on the parity-test rigging itself
# =============================================================================

def test_parity_matrix_is_nonempty():
    """If the fixture bank or rule list shrinks to zero, parity is vacuous."""
    assert len(_PARITY_PAIRS) > 0


def test_pinned_fixtures_present_in_bank():
    """Every pinned name must resolve against the bank. If the bank renames
    a fixture, this test fires before the parity sweep silently drops it."""
    for name in _PINNED_FIXTURE_NAMES:
        f = _find_fixture(name)
        assert f.name == name


# =============================================================================
# Feature-flag fallback — ECM_NORMALIZATION_UNIFIED_POLICY=false
# PO decision 10: the flag is a one-way-door rollback switch. When false,
# engine falls back to pre-bd-eio04.1 behavior (superscript conversion only,
# no NFC, no Cf-stripping).
# =============================================================================

class TestLegacyPolicyFallback:
    """Exercise the ECM_NORMALIZATION_UNIFIED_POLICY=false rollback path.

    These tests construct a NormalizationPolicy directly with
    unified_enabled=False (bypassing the env-var latch) and assert the
    fallback preserves the pre-bd-eio04.1 behavior: superscript
    conversion still happens, but NFC canonicalization and Cf-code-point
    stripping do NOT.
    """

    def test_legacy_policy_converts_superscripts(self):
        """Legacy path: superscripts still convert (preserve pre-bd-eio04.1)."""
        from normalization_engine import NormalizationPolicy
        policy = NormalizationPolicy(unified_enabled=False)
        assert policy.apply_to_text("ESPN²") == "ESPN2"
        assert policy.apply_to_text("ESPN ᴴᴰ") == "ESPN HD"

    def test_legacy_policy_does_not_nfc_canonicalize(self):
        """Legacy path: NFD input stays NFD (operator rollback semantic)."""
        import unicodedata
        from normalization_engine import NormalizationPolicy
        policy = NormalizationPolicy(unified_enabled=False)
        # 'Café' NFD form: e + U+0301
        nfd = unicodedata.normalize("NFD", "Café")
        out = policy.apply_to_text(nfd)
        # The decomposed combining acute stays in place (no NFC).
        assert "́" in out

    def test_legacy_policy_does_not_strip_cf(self):
        """Legacy path: BOM / ZWJ / ZWSP pass through (rollback semantic)."""
        from normalization_engine import NormalizationPolicy
        policy = NormalizationPolicy(unified_enabled=False)
        bom_prefixed = "﻿Sports"
        zwj_injected = "ESPN‍ HD"
        assert policy.apply_to_text(bom_prefixed) == bom_prefixed
        assert policy.apply_to_text(zwj_injected) == zwj_injected

    def test_unified_policy_nfc_canonicalizes(self):
        """Unified path (default): NFD collapses to NFC."""
        import unicodedata
        from normalization_engine import NormalizationPolicy
        policy = NormalizationPolicy(unified_enabled=True)
        nfd = unicodedata.normalize("NFD", "Café")
        out = policy.apply_to_text(nfd)
        # NFC output: single pre-composed codepoint, no combining U+0301.
        assert "́" not in out
        assert out == unicodedata.normalize("NFC", out)

    def test_unified_policy_strips_cf_whitelist(self):
        """Unified path (default): whitelisted Cf code points strip."""
        from normalization_engine import NormalizationPolicy
        policy = NormalizationPolicy(unified_enabled=True)
        assert policy.apply_to_text("﻿Sports") == "Sports"       # BOM
        assert policy.apply_to_text("ESPN‍ HD") == "ESPN HD"     # ZWJ
        assert policy.apply_to_text("Fox News​") == "Fox News"   # ZWSP
        assert policy.apply_to_text("X‌Y") == "XY"               # ZWNJ

    def test_unified_policy_preserves_rtl_marks(self):
        """Unified path: U+200F RTL mark is NOT in the Cf strip whitelist."""
        from normalization_engine import NormalizationPolicy
        policy = NormalizationPolicy(unified_enabled=True)
        rtl = "Al Jazeera ‏Arabic"
        assert policy.apply_to_text(rtl) == rtl

    def test_env_var_parses_truthy_defaults(self, monkeypatch):
        """ECM_NORMALIZATION_UNIFIED_POLICY: default + common truthy/falsy values."""
        from normalization_engine import _unified_policy_enabled
        # Default (unset): true.
        monkeypatch.delenv("ECM_NORMALIZATION_UNIFIED_POLICY", raising=False)
        assert _unified_policy_enabled() is True
        # Common truthy values.
        for raw in ("true", "True", "TRUE", "1", "yes", "on"):
            monkeypatch.setenv("ECM_NORMALIZATION_UNIFIED_POLICY", raw)
            assert _unified_policy_enabled() is True, f"truthy {raw!r}"
        # Common falsy values.
        for raw in ("false", "False", "FALSE", "0", "no", "off"):
            monkeypatch.setenv("ECM_NORMALIZATION_UNIFIED_POLICY", raw)
            assert _unified_policy_enabled() is False, f"falsy {raw!r}"
