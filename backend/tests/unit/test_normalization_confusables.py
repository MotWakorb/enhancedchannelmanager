"""
Unicode confusables / homoglyph fold tests (bd-sc5s4).

Covers the opt-in ECM_NORMALIZATION_CONFUSABLES_FOLD flag on
NormalizationPolicy. The fold maps a curated subset of Unicode TR39
confusables (Cyrillic, Greek, math italic, fullwidth) to Latin/ASCII so
homoglyph-disguised inputs (e.g. an attacker-registered 'chаnnel' with
Cyrillic 'а' U+0430) compare equal to the legitimate 'channel'.

Layout:
    * Default-off regression: with the fold disabled (default), Cyrillic
      look-alikes stay distinct from Latin (the prior behavior).
    * Default-off ASCII regression: ASCII-only inputs are byte-identical
      with and without the fold, on every code path.
    * Fold-on positive cases: at least one pair from each script class
      called out in the bead — Cyrillic, Greek, math symbols, fullwidth,
      decomposed combining marks (combining marks are handled by NFC,
      not the fold, so we assert the combined NFC + fold pipeline).
    * Engine integration: when the fold is on, a rule pattern typed in
      Latin matches an input typed in Cyrillic homoglyphs.
"""
from __future__ import annotations

import unicodedata

import pytest

from normalization_engine import (
    CONFUSABLES_MAP,
    NormalizationPolicy,
    _confusables_fold_enabled,
    fold_confusables,
)


# =============================================================================
# fold_confusables() unit behavior
# =============================================================================


class TestFoldConfusablesUnit:
    """Direct tests of fold_confusables() — no policy context."""

    def test_empty_string_returns_empty(self):
        assert fold_confusables("") == ""

    def test_pure_ascii_unchanged(self):
        """ASCII-only fast path: same object semantics not required, but
        the value must be byte-identical."""
        for s in ("ESPN HD", "Fox News", "channel123", "ABC-1080p"):
            assert fold_confusables(s) == s

    def test_cyrillic_lowercase_maps_to_latin(self):
        # 'chаnnel' with U+0430 Cyrillic 'а' should fold to 'channel'.
        attacker = "chаnnel"
        assert attacker != "channel"  # they really are different code points
        assert fold_confusables(attacker) == "channel"

    def test_cyrillic_uppercase_maps_to_latin(self):
        # 'ЕSPN' with U+0415 Cyrillic 'Е' should fold to 'ESPN'.
        attacker = "ЕSPN"
        assert fold_confusables(attacker) == "ESPN"

    def test_greek_uppercase_maps_to_latin(self):
        # 'ΑBC' with U+0391 Greek 'Α' (Alpha) should fold to 'ABC'.
        attacker = "ΑBC"
        assert fold_confusables(attacker) == "ABC"

    def test_math_italic_letter_maps_to_latin(self):
        # U+1D44E MATHEMATICAL ITALIC SMALL A.
        attacker = "channel\U0001d44e"
        assert fold_confusables(attacker) == "channela"

    def test_math_bold_digit_maps_to_ascii(self):
        # U+1D7CE MATHEMATICAL BOLD DIGIT ZERO.
        attacker = "channel\U0001d7ce"
        assert fold_confusables(attacker) == "channel0"

    def test_fullwidth_letter_maps_to_ascii(self):
        # U+FF21 FULLWIDTH LATIN CAPITAL LETTER A.
        attacker = "ＡBC"
        assert fold_confusables(attacker) == "ABC"

    def test_fullwidth_digit_maps_to_ascii(self):
        # U+FF11 FULLWIDTH DIGIT ONE.
        attacker = "ESPN１"
        assert fold_confusables(attacker) == "ESPN1"


# =============================================================================
# Confusables map sanity — no entry is a no-op (would indicate a typo).
# =============================================================================


class TestConfusablesMapSanity:
    def test_no_entry_maps_to_itself(self):
        """A confusable that maps to its own bytes is a copy-paste typo
        in the map (the source and target are the same character)."""
        for src, tgt in CONFUSABLES_MAP.items():
            assert src != tgt, f"map entry {src!r} ({hex(ord(src))}) -> itself"

    def test_all_targets_are_ascii(self):
        """The fold is a TO-ASCII fold by design. A non-ASCII target is
        a bug — it would mean the fold leaves you in the same situation
        you started in."""
        for src, tgt in CONFUSABLES_MAP.items():
            assert tgt.isascii(), f"map entry {src!r} -> non-ASCII {tgt!r}"


# =============================================================================
# Policy integration — opt-in semantic and ordering with NFC + Cf-strip
# =============================================================================


class TestPolicyConfusablesFoldDisabled:
    """Default behavior: fold is OFF. Cyrillic stays distinct from Latin."""

    def test_default_policy_does_not_fold_cyrillic(self):
        policy = NormalizationPolicy(unified_enabled=True, confusables_fold=False)
        attacker = "chаnnel"  # Cyrillic 'а'
        assert policy.apply_to_text(attacker) == attacker
        assert policy.apply_to_text(attacker) != "channel"

    def test_default_policy_ascii_unchanged(self):
        policy = NormalizationPolicy(unified_enabled=True, confusables_fold=False)
        assert policy.apply_to_text("ESPN HD") == "ESPN HD"

    def test_default_policy_default_constructor(self):
        """The dataclass default keeps confusables OFF (opt-in)."""
        policy = NormalizationPolicy()
        assert policy.confusables_fold is False


class TestPolicyConfusablesFoldEnabled:
    """confusables_fold=True: bead's required pair classes all collapse."""

    def test_cyrillic_pair_folds(self):
        """Pair class 1 — Cyrillic. 'chаnnel' (U+0430) -> 'channel'."""
        policy = NormalizationPolicy(unified_enabled=True, confusables_fold=True)
        assert policy.apply_to_text("chаnnel") == "channel"

    def test_greek_pair_folds(self):
        """Pair class 2 — Greek. 'ΑΒC' (Α=U+0391, Β=U+0392) -> 'ABC'."""
        policy = NormalizationPolicy(unified_enabled=True, confusables_fold=True)
        assert policy.apply_to_text("ΑΒC") == "ABC"

    def test_math_symbol_pair_folds(self):
        """Pair class 3 — math italic / math bold."""
        policy = NormalizationPolicy(unified_enabled=True, confusables_fold=True)
        # MATHEMATICAL ITALIC SMALL A + B + C
        assert policy.apply_to_text("\U0001d44e\U0001d44f\U0001d450") == "abc"

    def test_fullwidth_pair_folds(self):
        """Pair class 4 — fullwidth Latin / digits."""
        policy = NormalizationPolicy(unified_enabled=True, confusables_fold=True)
        assert policy.apply_to_text("ＡＢＣ１") == "ABC1"

    def test_decomposed_combining_marks_collapse_via_nfc_then_fold(self):
        """Pair class 5 — decomposed combining marks. NFC handles the
        decomposition step; the fold is a no-op for ASCII letters but
        the combined output must match a pre-composed reference."""
        policy = NormalizationPolicy(unified_enabled=True, confusables_fold=True)
        nfd = unicodedata.normalize("NFD", "Café")  # e + U+0301
        out = policy.apply_to_text(nfd)
        # NFC re-composition produces 'Café' with U+00E9; the fold
        # leaves U+00E9 alone (not in CONFUSABLES_MAP — accented Latin
        # is intentional content, not a confusable). The combining mark
        # MUST be gone.
        assert "́" not in out
        assert out == unicodedata.normalize("NFC", "Café")

    def test_ascii_only_input_unchanged_when_fold_enabled(self):
        """Regression contract from the bead: opt-in must not perturb
        ASCII-only inputs. Same output enabled or disabled."""
        on = NormalizationPolicy(unified_enabled=True, confusables_fold=True)
        off = NormalizationPolicy(unified_enabled=True, confusables_fold=False)
        for s in ("ESPN HD", "Fox News 1080p", "channel-123", "[US] ABC"):
            assert on.apply_to_text(s) == off.apply_to_text(s) == s


class TestPolicyConfusablesFoldLegacyInteraction:
    """confusables_fold has NO effect when unified_enabled=False —
    rollback switch keeps its byte-for-byte semantic."""

    def test_legacy_policy_ignores_fold_flag(self):
        legacy_with_fold = NormalizationPolicy(
            unified_enabled=False, confusables_fold=True
        )
        # Cyrillic 'а' must NOT fold under legacy, even with the flag on.
        attacker = "chаnnel"
        assert legacy_with_fold.apply_to_text(attacker) == attacker


# =============================================================================
# Env-var parsing — opt-in semantic
# =============================================================================


class TestConfusablesFoldEnvVar:
    def test_default_off(self, monkeypatch):
        monkeypatch.delenv("ECM_NORMALIZATION_CONFUSABLES_FOLD", raising=False)
        assert _confusables_fold_enabled() is False

    def test_truthy_values_enable(self, monkeypatch):
        for raw in ("true", "True", "TRUE", "1", "yes", "on"):
            monkeypatch.setenv("ECM_NORMALIZATION_CONFUSABLES_FOLD", raw)
            assert _confusables_fold_enabled() is True, f"truthy {raw!r}"

    def test_falsy_values_disable(self, monkeypatch):
        for raw in ("false", "False", "FALSE", "0", "no", "off", ""):
            monkeypatch.setenv("ECM_NORMALIZATION_CONFUSABLES_FOLD", raw)
            assert _confusables_fold_enabled() is False, f"falsy {raw!r}"


# =============================================================================
# Engine-level integration — pattern in Latin matches input in Cyrillic.
# This is the security property the fold delivers: with the flag on, a
# rule typed by an operator in Latin catches inputs that an attacker
# disguised in Cyrillic.
# =============================================================================


class TestEngineIntegrationConfusablesFold:
    """Rules use the policy via _match_single_condition. A 'contains'
    rule typed in Latin should match an input typed in Cyrillic when
    the fold is enabled."""

    def test_pattern_latin_matches_cyrillic_input_when_fold_on(self, monkeypatch):
        # Set the env BEFORE importing the engine so the singleton picks
        # up the value. We then explicitly call the test-only refresh.
        monkeypatch.setenv("ECM_NORMALIZATION_CONFUSABLES_FOLD", "true")
        from normalization_engine import (
            NormalizationEngine,
            _reset_default_policy_for_tests,
            get_default_policy,
        )

        _reset_default_policy_for_tests()
        try:
            assert get_default_policy().confusables_fold is True

            engine = NormalizationEngine(db=None)
            # Pattern is plain Latin 'channel'; input has Cyrillic 'а'.
            attacker_input = "Sky chаnnel HD"
            match = engine._match_single_condition(
                attacker_input, "contains", "channel", case_sensitive=False
            )
            assert match.matched is True
        finally:
            monkeypatch.delenv("ECM_NORMALIZATION_CONFUSABLES_FOLD", raising=False)
            _reset_default_policy_for_tests()

    def test_pattern_latin_does_not_match_cyrillic_input_when_fold_off(self):
        """Default behavior (fold off): Latin pattern does NOT match
        Cyrillic-disguised input. This is the regression baseline — we
        must not change pre-bd-sc5s4 default semantics."""
        from normalization_engine import (
            NormalizationEngine,
            _reset_default_policy_for_tests,
            get_default_policy,
        )

        _reset_default_policy_for_tests()
        assert get_default_policy().confusables_fold is False

        engine = NormalizationEngine(db=None)
        attacker_input = "Sky chаnnel HD"
        match = engine._match_single_condition(
            attacker_input, "contains", "channel", case_sensitive=False
        )
        assert match.matched is False
