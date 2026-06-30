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

from normalization_engine import (
    NormalizationEngine,
    _tag_group_cache,
    clear_abbreviation_cache,
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
    clear_abbreviation_cache()
    yield
    _tag_group_cache.clear()
    NormalizationEngine._tag_group_id_cache.clear()
    clear_abbreviation_cache()


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
        clear_abbreviation_cache()
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
        clear_abbreviation_cache()

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


# ---------------------------------------------------------------------------
# require_delimiter (bd-0emgo.2 follow-up): the league strip must require a
# STRONG delimiter (':', '-', '|', '/'), not a bare space. A bare space means
# the league tag is part of a BRAND ("NFL RedZone", "NFL Network", "NFL Sunday
# Ticket") and must be preserved; a strong delimiter means it's a category
# column ("NFL: Buffalo Bills", "NFL | Sunday Ticket", "NFL - Replay") and must
# be stripped.
# ---------------------------------------------------------------------------

class TestRequireDelimiterLeagueStrip:
    @pytest.fixture
    def league_setup(self, test_session):
        """League Tags group + a strip_prefix rule with require_delimiter=True."""
        league = create_tag_group(test_session, name="League Tags")
        create_tag(test_session, group_id=league.id, value="NFL")

        rule_group = create_normalization_rule_group(
            test_session, name="Strip League Prefixes", enabled=True
        )
        rule = create_normalization_rule(
            test_session,
            group_id=rule_group.id,
            name="Match League Tags",
            condition_type="tag_group",
            condition_value=None,
            action_type="strip_prefix",
            tag_group_id=league.id,
            tag_match_position="prefix",
            require_delimiter=True,
        )
        return rule_group, rule

    # --- Bare space: NFL is part of the brand -> NOT stripped ---------------

    def test_nfl_redzone_preserved(self, engine, league_setup):
        """The headline brand: 'NFL RedZone' must stay 'NFL RedZone'.

        On the pre-fix branch (no require_delimiter, bare space accepted as a
        separator) this strips to 'RedZone'. With require_delimiter the bare
        space no longer matches, so the brand is preserved.
        """
        rule_group, _ = league_setup
        result = engine.normalize("NFL RedZone", group_ids=[rule_group.id]).normalized
        assert result == "NFL RedZone"

    def test_nfl_network_preserved_via_delimiter_not_generic_guard(self, engine, league_setup):
        """'NFL Network' preserved by the DELIMITER rule (bare space)."""
        rule_group, _ = league_setup
        result = engine.normalize("NFL Network", group_ids=[rule_group.id]).normalized
        assert result == "NFL Network"

    def test_nfl_sunday_ticket_preserved(self, engine, league_setup):
        """'NFL Sunday Ticket' is a brand on a bare space -> preserved.

        Note: 'Sunday Ticket' is a multi-word remainder, so the generic-word
        guard would NOT catch this — only the delimiter requirement does.
        """
        rule_group, _ = league_setup
        result = engine.normalize("NFL Sunday Ticket", group_ids=[rule_group.id]).normalized
        assert result == "NFL Sunday Ticket"

    # --- Strong delimiter: NFL is a category column -> stripped -------------

    def test_nfl_colon_buffalo_bills_strips(self, engine, league_setup):
        """'NFL: Buffalo Bills' -> 'Buffalo Bills' (colon delimiter)."""
        rule_group, _ = league_setup
        result = engine.normalize("NFL: Buffalo Bills", group_ids=[rule_group.id]).normalized
        assert result == "Buffalo Bills"

    def test_nfl_pipe_sunday_ticket_strips(self, engine, league_setup):
        """'NFL | Sunday Ticket' -> 'Sunday Ticket' (pipe delimiter)."""
        rule_group, _ = league_setup
        result = engine.normalize("NFL | Sunday Ticket", group_ids=[rule_group.id]).normalized
        assert result == "Sunday Ticket"

    def test_nfl_dash_replay_strips(self, engine, league_setup):
        """'NFL - Replay' -> 'Replay' (space-padded dash delimiter)."""
        rule_group, _ = league_setup
        result = engine.normalize("NFL - Replay", group_ids=[rule_group.id]).normalized
        assert result == "Replay"

    def test_nfl_slash_redzone_strips(self, engine, league_setup):
        """'NFL/RedZone' -> 'RedZone' (slash delimiter, no surrounding space)."""
        rule_group, _ = league_setup
        result = engine.normalize("NFL/RedZone", group_ids=[rule_group.id]).normalized
        assert result == "RedZone"

    def test_generic_guard_still_backstops_delimiter_case(self, engine, test_session):
        """Generic-word guard remains a backstop on a delimiter case.

        'MLB: Network' has a strong delimiter so the delimiter check passes,
        but the remainder is the bare generic word 'Network' -> the generic
        guard refuses the strip, keeping the name distinct (no cross-merge).
        """
        league = create_tag_group(test_session, name="League Tags")
        create_tag(test_session, group_id=league.id, value="MLB")

        rule_group = create_normalization_rule_group(
            test_session, name="Strip League Prefixes", enabled=True
        )
        create_normalization_rule(
            test_session,
            group_id=rule_group.id,
            name="Match League Tags",
            condition_type="tag_group",
            condition_value=None,
            action_type="strip_prefix",
            tag_group_id=league.id,
            tag_match_position="prefix",
            require_delimiter=True,
        )
        result = engine.normalize("MLB: Network", group_ids=[rule_group.id]).normalized
        assert result != "Network"
        assert result == "MLB: Network"


class TestRequireDelimiterDefaultFalseRegression:
    """Country/other strips MUST keep working on a bare space (require_delimiter
    defaults to False). This is the critical scoping guard: the league fix must
    not break country-prefix strips (country codes like US/UK/DE are never brand
    components, so 'US ESPN' must still strip to 'ESPN')."""

    @pytest.fixture
    def country_setup(self, test_session):
        country = create_tag_group(test_session, name="Country Tags")
        create_tag(test_session, group_id=country.id, value="US")

        rule_group = create_normalization_rule_group(
            test_session, name="Strip Country Prefixes", enabled=True
        )
        # No require_delimiter -> defaults False -> bare space still strips.
        create_normalization_rule(
            test_session,
            group_id=rule_group.id,
            name="Match Country Tags",
            condition_type="tag_group",
            condition_value=None,
            action_type="strip_prefix",
            tag_group_id=country.id,
            tag_match_position="prefix",
        )
        return rule_group

    def test_country_bare_space_still_strips(self, engine, country_setup):
        """'US ESPN' -> 'ESPN' (bare space, default require_delimiter=False)."""
        result = engine.normalize("US ESPN", group_ids=[country_setup.id]).normalized
        assert result == "ESPN"

    def test_country_strong_delimiter_still_strips(self, engine, country_setup):
        """'US: ESPN' -> 'ESPN' (strong delimiter also strips)."""
        result = engine.normalize("US: ESPN", group_ids=[country_setup.id]).normalized
        assert result == "ESPN"


# ---------------------------------------------------------------------------
# bd-xxzxe: extract_core_name now strips channel-number noise UNCONDITIONALLY
# (leading channel numbers + trailing "(NNN)") so the shared engine ignores
# channel-number noise for both merge core-name matching and EPG matching.
# ---------------------------------------------------------------------------

class TestExtractCoreNameNumberStrip:
    def test_number_space_letter_prefix_stripped(self, engine):
        """The headline bug: '201 ESPN' -> 'ESPN' (number + space + letter)."""
        assert engine.extract_core_name("201 ESPN") == "ESPN"

    def test_decimal_number_space_letter_stripped(self, engine):
        """'2.2 KATU' -> 'KATU' (decimal LCN + space + letter)."""
        assert engine.extract_core_name("2.2 KATU") == "KATU"

    def test_trailing_parenthesized_number_stripped(self, engine):
        """'ESPN (201)' -> 'ESPN' (trailing channel-number tag)."""
        assert engine.extract_core_name("ESPN (201)") == "ESPN"

    def test_delimited_number_prefix_stripped(self, engine):
        """'107 | Discovery' -> 'Discovery' (delimited prefix)."""
        assert engine.extract_core_name("107 | Discovery") == "Discovery"

    # --- preservation guards: brand digits / trailing counts are NOT noise ---

    def test_leading_brand_digit_preserved(self, engine):
        """'3ABN' keeps its brand digit (no space after the digit)."""
        assert engine.extract_core_name("3ABN") == "3ABN"

    def test_trailing_number_word_preserved(self, engine):
        """'Fox 5' keeps the trailing number (part of the name, not a prefix)."""
        assert engine.extract_core_name("Fox 5") == "Fox 5"

    def test_generic_collapse_guard_still_holds(self, engine, test_session):
        """The bd-0emgo.2 generic-collapse guard is untouched.

        'NFL Network' must NOT collapse to 'Network' even with 'NFL' as a
        country-like prefix tag.
        """
        country = create_tag_group(test_session, name="Country Tags")
        create_tag(test_session, group_id=country.id, value="NFL")
        assert engine.extract_core_name("NFL Network") == "NFL Network"


# ---------------------------------------------------------------------------
# bd-xxzxe: timezone stripping is OPT-IN via for_matching=True. The default
# keeps East/West DISTINCT — this is the regression guard that prevents
# auto-creation from cross-merging regional feeds.
# ---------------------------------------------------------------------------

class TestExtractCoreNameMatchingMode:
    @pytest.fixture
    def timezone_setup(self, test_session):
        tz = create_tag_group(test_session, name="Timezone Tags")
        for tag in ["EAST", "WEST"]:
            create_tag(test_session, group_id=tz.id, value=tag)
        return tz

    def test_default_keeps_east_west_distinct(self, engine, timezone_setup):
        """DEFAULT (merge) mode: East/West stay distinct -> no cross-merge."""
        east = engine.extract_core_name("ESPN East")
        west = engine.extract_core_name("ESPN West")
        assert east == "ESPN East"
        assert west == "ESPN West"
        assert east != west

    def test_for_matching_strips_timezone(self, engine, timezone_setup):
        """MATCHING mode: timezone stripped so both collapse to 'ESPN'."""
        assert engine.extract_core_name("ESPN East", for_matching=True) == "ESPN"
        assert engine.extract_core_name("ESPN West", for_matching=True) == "ESPN"
