"""
Unit + end-to-end tests for the normalization repair migration (znc76.3).

Covers the two idempotent repair helpers added to normalization_migration.py:

  - backfill_tag_group_rule_ids: wires tag_group_id (and tag_match_position)
    onto tag_group rules whose tag_group_id was never set (CONFIG drift), by
    matching each rule's parent group to its tag group BY NAME.
  - ensure_abbreviation_tags_acronyms: adds US/UK/EU to the Abbreviation Tags
    group so Smart Title Case preserves them (US -> US, not US -> Us).

The end-to-end test reproduces the original bug ("US : ESPN HD" -> "Us : ESPN HD")
and verifies that after the migration the strips fire and the acronym survives.
"""
import pytest

from normalization_migration import (
    backfill_tag_group_rule_ids,
    ensure_abbreviation_tags_acronyms,
    RULE_GROUP_TO_TAG_GROUP,
    ACTION_TYPE_TO_POSITION,
)
from models import NormalizationRule, Tag, TagGroup
from tests.fixtures.factories import (
    create_tag_group,
    create_tag,
    create_normalization_rule_group,
    create_normalization_rule,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_null_strip_rule(session, rule_group_name, tag_group, action_type):
    """Create a strip rule group + a tag_group rule with tag_group_id NULL.

    Mirrors the broken pre-existing state: condition_type='tag_group' but the
    tag_group_id was never wired (and position left NULL).
    """
    rule_group = create_normalization_rule_group(
        session, name=rule_group_name, enabled=True, priority=0,
    )
    rule = create_normalization_rule(
        session,
        group_id=rule_group.id,
        name=f"Match {tag_group.name}",
        condition_type="tag_group",
        condition_value=None,
        action_type=action_type,
        tag_group_id=None,          # the bug: never wired
        tag_match_position=None,    # the bug: never set
    )
    return rule_group, rule


# ---------------------------------------------------------------------------
# backfill_tag_group_rule_ids
# ---------------------------------------------------------------------------

class TestBackfillTagGroupRuleIds:
    """Wiring NULL tag_group_id rules to the right tag group + position."""

    def test_wires_suffix_rule_to_quality_tag_group(self, test_session):
        """strip_suffix rule -> tag group by name, position 'suffix'."""
        quality = create_tag_group(test_session, name="Quality Tags")
        create_tag(test_session, group_id=quality.id, value="HD")
        _, rule = _make_null_strip_rule(
            test_session, "Strip Quality Suffixes", quality, "strip_suffix",
        )

        result = backfill_tag_group_rule_ids(test_session)

        assert result["rules_wired"] == 1
        assert result["rules_skipped"] == 0
        test_session.refresh(rule)
        assert rule.tag_group_id == quality.id
        assert rule.tag_match_position == "suffix"

    def test_wires_prefix_rule_to_country_tag_group(self, test_session):
        """strip_prefix rule -> position 'prefix'."""
        country = create_tag_group(test_session, name="Country Tags")
        create_tag(test_session, group_id=country.id, value="US")
        _, rule = _make_null_strip_rule(
            test_session, "Strip Country Prefixes", country, "strip_prefix",
        )

        backfill_tag_group_rule_ids(test_session)

        test_session.refresh(rule)
        assert rule.tag_group_id == country.id
        assert rule.tag_match_position == "prefix"

    def test_wires_remove_rule_to_contains_position(self, test_session):
        """'remove' action (State/Province) -> position 'contains'."""
        state = create_tag_group(test_session, name="State/Province Tags")
        create_tag(test_session, group_id=state.id, value="TX")
        _, rule = _make_null_strip_rule(
            test_session, "Strip State/Province Tags", state, "remove",
        )

        backfill_tag_group_rule_ids(test_session)

        test_session.refresh(rule)
        assert rule.tag_group_id == state.id
        assert rule.tag_match_position == "contains"

    def test_wires_all_seven_strip_groups(self, test_session):
        """All 7 live strip groups get wired to the correct tag group/position."""
        # (rule_group_name, tag_group_name, action_type, expected_position)
        specs = [
            ("Strip Quality Suffixes", "Quality Tags", "strip_suffix", "suffix"),
            ("Strip Country Prefixes", "Country Tags", "strip_prefix", "prefix"),
            ("Strip Timezone Suffixes", "Timezone Tags", "strip_suffix", "suffix"),
            ("Strip League Prefixes", "League Tags", "strip_prefix", "prefix"),
            ("Strip Network Tags", "Network Tags", "strip_suffix", "suffix"),
            ("Strip State/Province Tags", "State/Province Tags", "remove", "contains"),
            ("Strip Provider Tags", "Provider Tags", "strip_suffix", "suffix"),
        ]
        rules = {}
        for rg_name, tg_name, action, _pos in specs:
            tg = create_tag_group(test_session, name=tg_name)
            _, rule = _make_null_strip_rule(test_session, rg_name, tg, action)
            rules[rg_name] = (rule, tg)

        result = backfill_tag_group_rule_ids(test_session)

        assert result["rules_wired"] == 7
        assert result["rules_skipped"] == 0
        for rg_name, tg_name, _action, expected_pos in specs:
            rule, tg = rules[rg_name]
            test_session.refresh(rule)
            assert rule.tag_group_id == tg.id, rg_name
            assert rule.tag_match_position == expected_pos, rg_name

    def test_legacy_state_province_suffixes_name_mapped(self, test_session):
        """Legacy 'Strip State/Province Suffixes' group name is aliased."""
        state = create_tag_group(test_session, name="State/Province Tags")
        _, rule = _make_null_strip_rule(
            test_session, "Strip State/Province Suffixes", state, "remove",
        )

        result = backfill_tag_group_rule_ids(test_session)

        assert result["rules_wired"] == 1
        test_session.refresh(rule)
        assert rule.tag_group_id == state.id

    def test_skips_already_wired_rules(self, test_session):
        """Rules that already have tag_group_id are left untouched."""
        quality = create_tag_group(test_session, name="Quality Tags")
        rule_group = create_normalization_rule_group(
            test_session, name="Strip Quality Suffixes", enabled=True,
        )
        rule = create_normalization_rule(
            test_session,
            group_id=rule_group.id,
            name="Match Quality Tags",
            condition_type="tag_group",
            condition_value=None,
            action_type="strip_suffix",
            tag_group_id=quality.id,        # already wired
            tag_match_position="suffix",
        )

        result = backfill_tag_group_rule_ids(test_session)

        assert result["rules_wired"] == 0
        test_session.refresh(rule)
        assert rule.tag_group_id == quality.id

    def test_skips_rule_with_unmapped_group_name(self, test_session):
        """Conservative: unknown group name is skipped (not guessed)."""
        create_tag_group(test_session, name="Quality Tags")
        rule_group = create_normalization_rule_group(
            test_session, name="Some Custom User Group", enabled=True,
        )
        rule = create_normalization_rule(
            test_session,
            group_id=rule_group.id,
            name="Match something",
            condition_type="tag_group",
            condition_value=None,
            action_type="strip_suffix",
            tag_group_id=None,
            tag_match_position=None,
        )

        result = backfill_tag_group_rule_ids(test_session)

        assert result["rules_wired"] == 0
        assert result["rules_skipped"] == 1
        test_session.refresh(rule)
        assert rule.tag_group_id is None

    def test_skips_when_target_tag_group_missing(self, test_session):
        """Group name maps to a tag group, but the tag group doesn't exist."""
        # No Quality Tags group created.
        rule_group = create_normalization_rule_group(
            test_session, name="Strip Quality Suffixes", enabled=True,
        )
        rule = create_normalization_rule(
            test_session,
            group_id=rule_group.id,
            name="Match Quality Tags",
            condition_type="tag_group",
            condition_value=None,
            action_type="strip_suffix",
            tag_group_id=None,
            tag_match_position=None,
        )

        result = backfill_tag_group_rule_ids(test_session)

        assert result["rules_wired"] == 0
        assert result["rules_skipped"] == 1
        test_session.refresh(rule)
        assert rule.tag_group_id is None

    def test_preserves_existing_position_when_set(self, test_session):
        """If position is already set (only id is NULL), keep the position."""
        quality = create_tag_group(test_session, name="Quality Tags")
        rule_group = create_normalization_rule_group(
            test_session, name="Strip Quality Suffixes", enabled=True,
        )
        rule = create_normalization_rule(
            test_session,
            group_id=rule_group.id,
            name="Match Quality Tags",
            condition_type="tag_group",
            condition_value=None,
            action_type="strip_suffix",
            tag_group_id=None,
            tag_match_position="prefix",  # unusual but explicitly set
        )

        backfill_tag_group_rule_ids(test_session)

        test_session.refresh(rule)
        assert rule.tag_group_id == quality.id
        assert rule.tag_match_position == "prefix"  # not overwritten

    def test_idempotent_on_rerun(self, test_session):
        """Re-running after a successful wire is a no-op."""
        quality = create_tag_group(test_session, name="Quality Tags")
        _, rule = _make_null_strip_rule(
            test_session, "Strip Quality Suffixes", quality, "strip_suffix",
        )

        first = backfill_tag_group_rule_ids(test_session)
        second = backfill_tag_group_rule_ids(test_session)

        assert first["rules_wired"] == 1
        assert second["rules_wired"] == 0
        assert second["rules_skipped"] == 0
        test_session.refresh(rule)
        assert rule.tag_group_id == quality.id

    def test_no_rules_is_noop(self, test_session):
        """Empty DB -> no-op."""
        result = backfill_tag_group_rule_ids(test_session)
        assert result == {"rules_wired": 0, "rules_skipped": 0}

    def test_does_not_touch_non_tag_group_rules(self, test_session):
        """A 'contains' rule with NULL tag_group_id is irrelevant and untouched."""
        rule_group = create_normalization_rule_group(
            test_session, name="Strip Quality Suffixes", enabled=True,
        )
        rule = create_normalization_rule(
            test_session,
            group_id=rule_group.id,
            name="legacy contains rule",
            condition_type="contains",
            condition_value="HD",
            action_type="remove",
            tag_group_id=None,
        )

        result = backfill_tag_group_rule_ids(test_session)

        assert result["rules_wired"] == 0
        test_session.refresh(rule)
        assert rule.tag_group_id is None


class TestMappingConstants:
    """Sanity checks on the derived mapping constants."""

    def test_rule_group_map_covers_all_demo_strip_groups(self):
        for name in (
            "Strip Quality Suffixes", "Strip Country Prefixes",
            "Strip Timezone Suffixes", "Strip League Prefixes",
            "Strip Network Tags", "Strip State/Province Tags",
            "Strip Provider Tags",
        ):
            assert name in RULE_GROUP_TO_TAG_GROUP

    def test_action_type_position_map(self):
        assert ACTION_TYPE_TO_POSITION["strip_prefix"] == "prefix"
        assert ACTION_TYPE_TO_POSITION["strip_suffix"] == "suffix"
        assert ACTION_TYPE_TO_POSITION["remove"] == "contains"


# ---------------------------------------------------------------------------
# ensure_abbreviation_tags_acronyms
# ---------------------------------------------------------------------------

class TestEnsureAbbreviationAcronyms:
    """Adding US/UK/EU to the Abbreviation Tags group."""

    def test_adds_all_three_when_missing(self, test_session):
        create_tag_group(test_session, name="Abbreviation Tags")

        result = ensure_abbreviation_tags_acronyms(test_session)

        assert result["tags_added"] == 3
        group = test_session.query(TagGroup).filter(
            TagGroup.name == "Abbreviation Tags"
        ).first()
        values = {
            t.value for t in test_session.query(Tag).filter(
                Tag.group_id == group.id
            ).all()
        }
        assert {"US", "UK", "EU"} <= values

    def test_adds_only_missing(self, test_session):
        group = create_tag_group(test_session, name="Abbreviation Tags")
        create_tag(test_session, group_id=group.id, value="US")  # already there

        result = ensure_abbreviation_tags_acronyms(test_session)

        assert result["tags_added"] == 2  # only UK + EU
        values = {
            t.value for t in test_session.query(Tag).filter(
                Tag.group_id == group.id
            ).all()
        }
        assert {"US", "UK", "EU"} <= values

    def test_idempotent_on_rerun(self, test_session):
        create_tag_group(test_session, name="Abbreviation Tags")

        first = ensure_abbreviation_tags_acronyms(test_session)
        second = ensure_abbreviation_tags_acronyms(test_session)

        assert first["tags_added"] == 3
        assert second["tags_added"] == 0

    def test_skips_when_group_missing(self, test_session):
        result = ensure_abbreviation_tags_acronyms(test_session)
        assert result == {"tags_added": 0, "skipped": True}


# ---------------------------------------------------------------------------
# End-to-end: the original znc76.3 bug
# ---------------------------------------------------------------------------

class TestEndToEndAfterMigration:
    """After the repair migration, 'US : ESPN HD' strips and preserves US."""

    @pytest.fixture
    def patched_engine_session(self, test_session, monkeypatch):
        """Point the engine's abbreviation-tag loader at the test session.

        _load_abbreviation_tags() opens its own get_session(); patch it so the
        engine reads the same in-memory DB the test populates, and clear the
        module-level abbreviation cache before/after.
        """
        import normalization_engine
        import database

        monkeypatch.setattr(database, "get_session", lambda: test_session)
        normalization_engine.clear_abbreviation_cache()
        normalization_engine._tag_group_cache.clear()
        yield
        normalization_engine.clear_abbreviation_cache()
        normalization_engine._tag_group_cache.clear()

    def _seed_broken_ruleset(self, session):
        """Seed Quality + Country tag groups, Abbreviation Tags, and the
        broken (NULL tag_group_id) Quality/Country strip rules + Title Case.
        """
        quality = create_tag_group(session, name="Quality Tags")
        for v in ["HD", "FHD", "UHD", "4K", "SD"]:
            create_tag(session, group_id=quality.id, value=v)

        country = create_tag_group(session, name="Country Tags")
        for v in ["US", "USA", "UK", "CA"]:
            create_tag(session, group_id=country.id, value=v)

        # Abbreviation Tags WITHOUT US (mirrors the seed: USA present, US absent)
        create_tag_group(session, name="Abbreviation Tags")

        # Broken strip rules: condition_type tag_group but tag_group_id NULL
        _make_null_strip_rule(session, "Strip Quality Suffixes", quality, "strip_suffix")
        _make_null_strip_rule(session, "Strip Country Prefixes", country, "strip_prefix")

        # Title Case group (runs last)
        tc_group = create_normalization_rule_group(
            session, name="Title Case", enabled=True, priority=99,
        )
        create_normalization_rule(
            session,
            group_id=tc_group.id,
            name="Smart Title Case",
            condition_type="always",
            condition_value=None,
            action_type="capitalize",
            action_value="title",
        )

    def test_bug_reproduces_before_migration(self, test_session, patched_engine_session):
        """Sanity: before the repair, strips don't fire and US is lowercased."""
        from normalization_engine import NormalizationEngine
        self._seed_broken_ruleset(test_session)

        engine = NormalizationEngine(test_session)
        result = engine.normalize("US : ESPN HD")

        # Strips never fired (HD survives) and US got title-cased to "Us".
        assert "HD" in result.normalized
        assert "Us" in result.normalized

    def test_strips_and_preserves_us_after_migration(
        self, test_session, patched_engine_session
    ):
        """After the repair migration: HD + US stripped, ESPN preserved."""
        from normalization_engine import NormalizationEngine
        self._seed_broken_ruleset(test_session)

        # Run the repair.
        backfill_tag_group_rule_ids(test_session)
        ensure_abbreviation_tags_acronyms(test_session)

        # Caches must reflect the freshly-added Abbreviation Tags.
        import normalization_engine
        normalization_engine.clear_abbreviation_cache()
        normalization_engine._tag_group_cache.clear()

        engine = NormalizationEngine(test_session)
        result = engine.normalize("US : ESPN HD")

        # Quality suffix stripped.
        assert "HD" not in result.normalized
        # Country prefix stripped (no leading "US ..." separator left).
        assert not result.normalized.upper().startswith("US")
        # ESPN survives and is not corrupted.
        assert "ESPN" in result.normalized.upper()

    def test_us_acronym_preserved_in_title_case(
        self, test_session, patched_engine_session
    ):
        """With US in Abbreviation Tags, Title Case keeps it uppercase."""
        from normalization_engine import NormalizationEngine

        # Only Title Case + Abbreviation Tags (no strips) so we isolate the
        # title-case acronym behavior on a trailing US.
        abbr = create_tag_group(test_session, name="Abbreviation Tags")
        create_tag(test_session, group_id=abbr.id, value="USA")

        tc_group = create_normalization_rule_group(
            test_session, name="Title Case", enabled=True, priority=99,
        )
        create_normalization_rule(
            test_session,
            group_id=tc_group.id,
            name="Smart Title Case",
            condition_type="always",
            condition_value=None,
            action_type="capitalize",
            action_value="title",
        )

        ensure_abbreviation_tags_acronyms(test_session)
        import normalization_engine
        normalization_engine.clear_abbreviation_cache()

        engine = NormalizationEngine(test_session)
        result = engine.normalize("ESPN US")

        assert "US" in result.normalized
        assert "Us" not in result.normalized
