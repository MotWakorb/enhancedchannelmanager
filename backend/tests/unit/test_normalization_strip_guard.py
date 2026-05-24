"""
Unit tests for the strip-collapse guard (bd-0emgo.2).

Production data-corruption bug: the "Strip League Prefixes" normalization
(a strip_prefix action driven by a "League Tags" group: NFL, MLB, NHL, ...)
reduced "NFL Network" / "MLB Network" / "NHL Network" all to the bare common
word "Network", because the strip had no guard against leaving a single
generic word. The three names then normalized identically and cross-merged
(an NHL Network channel received MLB + NFL streams).

Fix (Option B): refuse a strip whose remainder is a SINGLE generic word.
On refusal the strip is a no-op (returns the original text) so:
  - the rule-engine multi-pass loop terminates (no infinite oscillation), and
  - the merge core-name path keeps the un-stripped distinct name.

These tests cover BOTH paths:
  - the rule-engine `normalize(...)` path (strip_prefix + strip_suffix), and
  - the merge `extract_core_name(...)` path (country prefix + quality suffix).
They also prove legitimate strips are NOT blocked.
"""
import pytest

import normalization_engine
from normalization_engine import (
    NormalizationEngine,
    _tag_group_cache,
)
from tests.fixtures.factories import (
    create_tag_group,
    create_tag,
    create_normalization_rule_group,
    create_normalization_rule,
)

# NOTE: the predicate / loader / default-set symbols (_would_collapse_to_generic,
# _load_generic_words, _DEFAULT_GENERIC_WORDS) are imported lazily inside the
# tests that exercise them directly. The behavioral tests below use ONLY the
# public engine API so this module still COLLECTS against unpatched code —
# letting those behavioral tests reproduce the production collapse (they FAIL
# pre-fix, PASS post-fix) rather than erroring at import time.


@pytest.fixture(autouse=True)
def clear_caches():
    """Clear every normalization cache before/after each test."""
    _tag_group_cache.clear()
    NormalizationEngine._tag_group_id_cache.clear()
    normalization_engine.clear_abbreviation_cache()
    yield
    _tag_group_cache.clear()
    NormalizationEngine._tag_group_id_cache.clear()
    normalization_engine.clear_abbreviation_cache()


@pytest.fixture
def engine(test_session):
    return NormalizationEngine(test_session)


# ---------------------------------------------------------------------------
# Predicate: _would_collapse_to_generic
# ---------------------------------------------------------------------------

class TestWouldCollapseToGenericPredicate:
    def test_single_generic_word_collapses(self):
        from normalization_engine import _would_collapse_to_generic
        assert _would_collapse_to_generic("Network") is True
        assert _would_collapse_to_generic("network") is True  # case-insensitive
        assert _would_collapse_to_generic("  Network  ") is True  # whitespace-tolerant

    def test_single_non_generic_word_allowed(self):
        from normalization_engine import _would_collapse_to_generic
        # ESPN is a single token but NOT in the generic set -> allowed.
        assert _would_collapse_to_generic("ESPN") is False

    def test_multi_word_remainder_allowed(self):
        from normalization_engine import _would_collapse_to_generic
        # Any multi-word remainder is always allowed, even if it contains a
        # generic word.
        assert _would_collapse_to_generic("Sky Sport Bundesliga") is False
        assert _would_collapse_to_generic("Sports Network") is False

    def test_empty_remainder_is_not_generic_collapse(self):
        from normalization_engine import _would_collapse_to_generic
        # Empty string is not a "single generic word"; the empty-result guard
        # is handled separately at each strip site.
        assert _would_collapse_to_generic("") is False
        assert _would_collapse_to_generic("   ") is False


# ---------------------------------------------------------------------------
# Operator-tunable loader: _load_generic_words
# ---------------------------------------------------------------------------

class TestGenericWordsLoader:
    def test_hardcoded_fallback_when_group_absent(self):
        """With no 'Generic Word Tags' group, the hardcoded fallback applies."""
        from normalization_engine import _load_generic_words, _DEFAULT_GENERIC_WORDS
        normalization_engine.clear_abbreviation_cache()
        words = _load_generic_words()
        assert words == _DEFAULT_GENERIC_WORDS
        assert "network" in words and "tv" in words and "channel" in words

    def test_operator_group_overrides_fallback(self, test_session, monkeypatch):
        """A 'Generic Word Tags' group, when present, replaces the fallback.

        _load_generic_words() opens its own get_session(); patch it so the
        loader reads the same in-memory DB the test populates (mirrors the
        existing _load_abbreviation_tags test convention).
        """
        import database
        from normalization_engine import _load_generic_words
        group = create_tag_group(test_session, name="Generic Word Tags")
        create_tag(test_session, group_id=group.id, value="Bouquet")
        monkeypatch.setattr(database, "get_session", lambda: test_session)
        normalization_engine.clear_abbreviation_cache()

        words = _load_generic_words()
        assert "bouquet" in words
        # The operator-defined group replaces the fallback set entirely.
        assert words == {"bouquet"}


# ---------------------------------------------------------------------------
# Rule-engine path: normalize() with a strip_prefix League rule
# ---------------------------------------------------------------------------

class TestNormalizeStripPrefixGuard:
    @pytest.fixture
    def strip_league_setup(self, test_session):
        """League Tags group + a strip_prefix rule that matches NFL/MLB/NHL."""
        league = create_tag_group(test_session, name="League Tags")
        for tag in ["NFL", "MLB", "NHL"]:
            create_tag(test_session, group_id=league.id, value=tag)

        rule_group = create_normalization_rule_group(
            test_session, name="Strip League Prefixes", enabled=True
        )
        create_normalization_rule(
            test_session,
            group_id=rule_group.id,
            name="Strip league prefix",
            condition_type="tag_group",
            condition_value=None,
            action_type="strip_prefix",
            tag_group_id=league.id,
            tag_match_position="prefix",
        )
        return rule_group

    def test_league_network_names_do_not_collapse(self, engine, strip_league_setup):
        """The production failure: NFL/MLB/NHL Network must stay distinct.

        Unpatched, all three collapse to the bare common word "Network".
        Patched, the strip is refused (remainder is a single generic word)
        so each keeps its distinct prefix.
        """
        gids = [strip_league_setup.id]
        nfl = engine.normalize("NFL Network", group_ids=gids).normalized
        mlb = engine.normalize("MLB Network", group_ids=gids).normalized
        nhl = engine.normalize("NHL Network", group_ids=gids).normalized

        # None collapse to the bare generic word.
        assert nfl != "Network"
        assert mlb != "Network"
        assert nhl != "Network"

        # And critically, they remain distinct from each other (no cross-merge).
        assert len({nfl, mlb, nhl}) == 3

        # The prefix is preserved (strip refused -> original text).
        assert nfl == "NFL Network"
        assert mlb == "MLB Network"
        assert nhl == "NHL Network"

    def test_multi_word_remainder_still_strips(self, engine, test_session):
        """A multi-word remainder is always allowed: 'Sky Sport Bundesliga'."""
        country = create_tag_group(test_session, name="Provider Tags")
        create_tag(test_session, group_id=country.id, value="Sky")

        rule_group = create_normalization_rule_group(
            test_session, name="Strip Provider", enabled=True
        )
        create_normalization_rule(
            test_session,
            group_id=rule_group.id,
            name="Strip provider prefix",
            condition_type="tag_group",
            condition_value=None,
            action_type="strip_prefix",
            tag_group_id=country.id,
            tag_match_position="prefix",
        )
        result = engine.normalize(
            "Sky: Sport Bundesliga", group_ids=[rule_group.id]
        ).normalized
        assert result == "Sport Bundesliga"

    def test_legitimate_single_token_strip_still_works(self, engine, test_session):
        """'US: ESPN' -> 'ESPN' (single token, not generic -> allowed)."""
        country = create_tag_group(test_session, name="Country Tags")
        create_tag(test_session, group_id=country.id, value="US")

        rule_group = create_normalization_rule_group(
            test_session, name="Strip Country", enabled=True
        )
        create_normalization_rule(
            test_session,
            group_id=rule_group.id,
            name="Strip country prefix",
            condition_type="tag_group",
            condition_value=None,
            action_type="strip_prefix",
            tag_group_id=country.id,
            tag_match_position="prefix",
        )
        result = engine.normalize("US: ESPN", group_ids=[rule_group.id]).normalized
        assert result == "ESPN"


# ---------------------------------------------------------------------------
# Rule-engine path: strip_suffix guard
# ---------------------------------------------------------------------------

class TestNormalizeStripSuffixGuard:
    def test_suffix_strip_refused_when_remainder_generic(self, engine, test_session):
        """Stripping a suffix that leaves a bare generic word is refused.

        e.g. tag 'Sports' stripped from 'TV Sports' would leave 'TV' (generic)
        -> refused; original retained.
        """
        suffix_group = create_tag_group(test_session, name="Suffix Tags")
        create_tag(test_session, group_id=suffix_group.id, value="Sports")

        rule_group = create_normalization_rule_group(
            test_session, name="Strip Suffix", enabled=True
        )
        create_normalization_rule(
            test_session,
            group_id=rule_group.id,
            name="Strip suffix tag",
            condition_type="tag_group",
            condition_value=None,
            action_type="strip_suffix",
            tag_group_id=suffix_group.id,
            tag_match_position="suffix",
        )
        result = engine.normalize("TV - Sports", group_ids=[rule_group.id]).normalized
        # 'TV' is generic -> strip refused -> original retained.
        assert result == "TV - Sports"

    def test_suffix_strip_allowed_when_remainder_not_generic(self, engine, test_session):
        """Stripping leaving a non-generic single token is allowed."""
        suffix_group = create_tag_group(test_session, name="Quality Tags")
        create_tag(test_session, group_id=suffix_group.id, value="HD")

        rule_group = create_normalization_rule_group(
            test_session, name="Strip HD", enabled=True
        )
        create_normalization_rule(
            test_session,
            group_id=rule_group.id,
            name="Strip HD suffix",
            condition_type="tag_group",
            condition_value=None,
            action_type="strip_suffix",
            tag_group_id=suffix_group.id,
            tag_match_position="suffix",
        )
        result = engine.normalize("ESPN HD", group_ids=[rule_group.id]).normalized
        assert result == "ESPN"


# ---------------------------------------------------------------------------
# Merge core-name path: extract_core_name()
# ---------------------------------------------------------------------------

class TestExtractCoreNameGuard:
    def test_country_prefix_strip_refused_when_remainder_generic(self, engine, test_session):
        """extract_core_name must not collapse 'NFL Network' to 'Network'.

        Country Tags drives the prefix strip here (the engine's built-in
        core-name path uses 'Country Tags' / 'Quality Tags'). With 'NFL' as a
        country-like prefix tag, stripping it would leave the bare generic
        word 'Network' -> must be refused.
        """
        country = create_tag_group(test_session, name="Country Tags")
        for tag in ["NFL", "MLB", "NHL"]:
            create_tag(test_session, group_id=country.id, value=tag)

        nfl = engine.extract_core_name("NFL Network")
        mlb = engine.extract_core_name("MLB Network")
        nhl = engine.extract_core_name("NHL Network")

        assert nfl != "Network"
        assert mlb != "Network"
        assert nhl != "Network"
        # Distinct -> no cross-merge via the core-name fallback.
        assert len({nfl, mlb, nhl}) == 3
        assert nfl == "NFL Network"

    def test_quality_suffix_strip_refused_when_remainder_generic(self, engine, test_session):
        """extract_core_name suffix strip is guarded too.

        'Sports' as a quality-ish suffix tag stripped from 'TV Sports' would
        leave 'TV' (generic) -> refused.
        """
        quality = create_tag_group(test_session, name="Quality Tags")
        create_tag(test_session, group_id=quality.id, value="Sports")

        result = engine.extract_core_name("TV - Sports")
        assert result == "TV - Sports"

    def test_country_prefix_legitimate_strip_still_works(self, engine, test_session):
        """'US: ESPN' -> 'ESPN' via extract_core_name (single non-generic)."""
        country = create_tag_group(test_session, name="Country Tags")
        create_tag(test_session, group_id=country.id, value="US")

        assert engine.extract_core_name("US: ESPN") == "ESPN"

    def test_multi_word_remainder_still_strips_in_core_name(self, engine, test_session):
        """Multi-word remainder always strips: 'UK: Sky Sport Bundesliga'."""
        country = create_tag_group(test_session, name="Country Tags")
        create_tag(test_session, group_id=country.id, value="UK")

        assert engine.extract_core_name("UK: Sky Sport Bundesliga") == "Sky Sport Bundesliga"
