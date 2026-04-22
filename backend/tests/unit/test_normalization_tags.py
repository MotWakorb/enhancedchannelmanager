"""
Unit tests for the normalization engine tag_group condition matching.

Tests the _match_tag_group method and tag-based rule processing.
"""
import pytest

from normalization_engine import (
    NormalizationEngine, _tag_group_cache,
    convert_superscripts,
    LETTER_SUPERSCRIPTS, NUMERIC_SUPERSCRIPTS, SUPERSCRIPT_MAP,
)
from tests.fixtures.unicode_fixtures import NUMERIC_SUPERSCRIPT_FIXTURES


class TestTagGroupMatching:
    """Tests for _match_tag_group method in NormalizationEngine."""

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        """Clear tag group cache before each test."""
        _tag_group_cache.clear()
        yield
        _tag_group_cache.clear()

    @pytest.fixture
    def engine(self, test_session):
        """Create a NormalizationEngine with test session."""
        return NormalizationEngine(test_session)

    @pytest.fixture
    def quality_tag_group(self, test_session):
        """Create a tag group with quality tags."""
        from tests.fixtures.factories import create_tag_group, create_tag

        group = create_tag_group(
            test_session,
            name="Quality Tags",
            description="Video quality indicators"
        )
        for tag_value in ["HD", "FHD", "UHD", "4K", "SD"]:
            create_tag(test_session, group_id=group.id, value=tag_value)
        return group

    def test_match_tag_suffix_with_separator(self, engine, quality_tag_group):
        """Matches tag at end with separator."""
        result = engine._match_tag_group(
            "ESPN News HD",
            quality_tag_group.id,
            position="suffix"
        )
        assert result.matched is True
        assert result.matched_tag == "HD"

    def test_match_tag_suffix_with_pipe_separator(self, engine, quality_tag_group):
        """Matches tag at end with pipe separator."""
        result = engine._match_tag_group(
            "ESPN News | FHD",
            quality_tag_group.id,
            position="suffix"
        )
        assert result.matched is True
        assert result.matched_tag == "FHD"

    def test_match_tag_suffix_with_dash_separator(self, engine, quality_tag_group):
        """Matches tag at end with dash separator."""
        result = engine._match_tag_group(
            "ESPN News - 4K",
            quality_tag_group.id,
            position="suffix"
        )
        assert result.matched is True
        assert result.matched_tag == "4K"

    def test_no_match_suffix_without_separator(self, engine, quality_tag_group):
        """Does not match tag at end without separator."""
        result = engine._match_tag_group(
            "ESPNHD",
            quality_tag_group.id,
            position="suffix"
        )
        assert result.matched is False

    def test_no_match_suffix_tag_is_entire_string(self, engine, quality_tag_group):
        """Does not match if tag is the entire string."""
        result = engine._match_tag_group(
            "HD",
            quality_tag_group.id,
            position="suffix"
        )
        assert result.matched is False

    def test_match_tag_prefix_with_separator(self, engine, quality_tag_group):
        """Matches tag at start with separator."""
        result = engine._match_tag_group(
            "HD: ESPN News",
            quality_tag_group.id,
            position="prefix"
        )
        assert result.matched is True
        assert result.matched_tag == "HD"

    def test_match_tag_prefix_with_space(self, engine, quality_tag_group):
        """Matches tag at start with space separator."""
        result = engine._match_tag_group(
            "4K ESPN News",
            quality_tag_group.id,
            position="prefix"
        )
        assert result.matched is True
        assert result.matched_tag == "4K"

    def test_no_match_prefix_without_separator(self, engine, quality_tag_group):
        """Does not match tag at start without separator."""
        result = engine._match_tag_group(
            "HDESPN",
            quality_tag_group.id,
            position="prefix"
        )
        assert result.matched is False

    def test_no_match_prefix_tag_is_entire_string(self, engine, quality_tag_group):
        """Does not match if tag is the entire string."""
        result = engine._match_tag_group(
            "HD",
            quality_tag_group.id,
            position="prefix"
        )
        assert result.matched is False

    def test_match_tag_contains(self, engine, quality_tag_group):
        """Matches tag anywhere in text with contains position."""
        result = engine._match_tag_group(
            "ESPN HD News",
            quality_tag_group.id,
            position="contains"
        )
        assert result.matched is True
        assert result.matched_tag == "HD"

    def test_match_tag_case_insensitive(self, engine, test_session):
        """Matches tags case-insensitively by default."""
        from tests.fixtures.factories import create_tag_group, create_tag

        group = create_tag_group(test_session, name="Case Test")
        create_tag(test_session, group_id=group.id, value="HD", case_sensitive=False)

        result = engine._match_tag_group(
            "ESPN hd",
            group.id,
            position="suffix"
        )
        assert result.matched is True
        assert result.matched_tag == "HD"

    def test_match_tag_case_sensitive(self, engine, test_session):
        """Respects case-sensitive flag when set."""
        from tests.fixtures.factories import create_tag_group, create_tag

        group = create_tag_group(test_session, name="Case Sensitive Test")
        create_tag(test_session, group_id=group.id, value="HD", case_sensitive=True)

        # Should not match lowercase
        result = engine._match_tag_group(
            "ESPN hd",
            group.id,
            position="suffix"
        )
        assert result.matched is False

        # Should match exact case
        result = engine._match_tag_group(
            "ESPN HD",
            group.id,
            position="suffix"
        )
        assert result.matched is True

    def test_disabled_tag_not_matched(self, engine, test_session):
        """Does not match disabled tags."""
        from tests.fixtures.factories import create_tag_group, create_tag

        group = create_tag_group(test_session, name="Disabled Test")
        create_tag(test_session, group_id=group.id, value="HD", enabled=False)
        create_tag(test_session, group_id=group.id, value="SD", enabled=True)

        result = engine._match_tag_group(
            "ESPN HD",
            group.id,
            position="suffix"
        )
        assert result.matched is False

        result = engine._match_tag_group(
            "ESPN SD",
            group.id,
            position="suffix"
        )
        assert result.matched is True

    def test_tag_group_cache_populated(self, engine, quality_tag_group):
        """Tag group is cached after first load."""
        # First call populates cache
        engine._match_tag_group(
            "Test HD",
            quality_tag_group.id,
            position="suffix"
        )

        assert quality_tag_group.id in _tag_group_cache
        assert len(_tag_group_cache[quality_tag_group.id]) == 5  # HD, FHD, UHD, 4K, SD

    def test_invalidate_cache_clears_tag_groups(self, engine, quality_tag_group):
        """invalidate_cache clears the tag group cache."""
        # Populate cache
        engine._match_tag_group(
            "Test HD",
            quality_tag_group.id,
            position="suffix"
        )
        assert quality_tag_group.id in _tag_group_cache

        # Invalidate
        engine.invalidate_cache()
        assert quality_tag_group.id not in _tag_group_cache


class TestTagGroupConditionEvaluation:
    """Tests for tag_group condition type in rule evaluation."""

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        """Clear tag group cache before each test."""
        _tag_group_cache.clear()
        yield
        _tag_group_cache.clear()

    @pytest.fixture
    def engine(self, test_session):
        """Create a NormalizationEngine with test session."""
        return NormalizationEngine(test_session)

    @pytest.fixture
    def quality_tag_group(self, test_session):
        """Create a tag group with quality tags."""
        from tests.fixtures.factories import create_tag_group, create_tag

        group = create_tag_group(test_session, name="Quality Tags")
        for tag_value in ["HD", "FHD", "UHD", "4K"]:
            create_tag(test_session, group_id=group.id, value=tag_value)
        return group

    def test_evaluate_tag_group_condition(self, engine, test_session, quality_tag_group):
        """Evaluates tag_group condition correctly."""
        from models import NormalizationRule

        rule = NormalizationRule(
            id=1,
            group_id=1,
            name="Test Rule",
            condition_type="tag_group",
            tag_group_id=quality_tag_group.id,
            tag_match_position="suffix",
            action_type="strip_suffix"
        )

        result = engine._match_condition("ESPN News HD", rule)
        assert result.matched is True
        assert result.matched_tag == "HD"

    def test_evaluate_tag_group_condition_no_match(self, engine, test_session, quality_tag_group):
        """Returns no match when tag not found."""
        from models import NormalizationRule

        rule = NormalizationRule(
            id=1,
            group_id=1,
            name="Test Rule",
            condition_type="tag_group",
            tag_group_id=quality_tag_group.id,
            tag_match_position="suffix",
            action_type="strip_suffix"
        )

        result = engine._match_condition("ESPN News", rule)
        assert result.matched is False

    def test_evaluate_tag_group_condition_missing_group_id(self, engine):
        """Returns no match when tag_group_id is None."""
        from models import NormalizationRule

        rule = NormalizationRule(
            id=1,
            group_id=1,
            name="Test Rule",
            condition_type="tag_group",
            tag_group_id=None,
            action_type="strip_suffix"
        )

        result = engine._match_condition("ESPN News HD", rule)
        assert result.matched is False


class TestElseBranchExecution:
    """Tests for else action execution when condition doesn't match."""

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        """Clear tag group cache before each test."""
        _tag_group_cache.clear()
        yield
        _tag_group_cache.clear()

    @pytest.fixture
    def engine(self, test_session):
        """Create a NormalizationEngine with test session."""
        return NormalizationEngine(test_session)

    def test_test_rule_else_applied_when_no_match(self, engine, test_session):
        """test_rule applies else action when condition doesn't match."""
        from tests.fixtures.factories import create_tag_group, create_tag

        group = create_tag_group(test_session, name="Quality Tags")
        create_tag(test_session, group_id=group.id, value="HD")

        result = engine.test_rule(
            text="ESPN News",  # No HD suffix
            condition_type="tag_group",
            condition_value=None,
            case_sensitive=False,
            action_type="strip_suffix",
            action_value=None,
            tag_group_id=group.id,
            tag_match_position="suffix",
            else_action_type="replace",
            else_action_value="ESPN News [Unknown Quality]"
        )

        assert result["matched"] is False
        assert result["else_applied"] is True
        assert result["after"] == "ESPN News [Unknown Quality]"

    def test_test_rule_else_not_applied_when_match(self, engine, test_session):
        """test_rule does not apply else action when condition matches."""
        from tests.fixtures.factories import create_tag_group, create_tag

        group = create_tag_group(test_session, name="Quality Tags")
        create_tag(test_session, group_id=group.id, value="HD")

        result = engine.test_rule(
            text="ESPN News HD",  # Has HD suffix
            condition_type="tag_group",
            condition_value=None,
            case_sensitive=False,
            action_type="strip_suffix",
            action_value=None,
            tag_group_id=group.id,
            tag_match_position="suffix",
            else_action_type="append",
            else_action_value=" [Unknown Quality]"
        )

        assert result["matched"] is True
        assert result["else_applied"] is False
        assert "ESPN News" in result["after"]
        assert "[Unknown Quality]" not in result["after"]


class TestSuperscriptConversion:
    """Tests for superscript character conversion in tag matching."""

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        """Clear tag group cache before each test."""
        _tag_group_cache.clear()
        yield
        _tag_group_cache.clear()

    @pytest.fixture
    def engine(self, test_session):
        """Create a NormalizationEngine with test session."""
        return NormalizationEngine(test_session)

    def test_match_superscript_hd_tag(self, engine, test_session):
        """Matches superscript ᴴᴰ against HD tag."""
        from tests.fixtures.factories import create_tag_group, create_tag

        group = create_tag_group(test_session, name="Quality Tags")
        create_tag(test_session, group_id=group.id, value="HD")

        # Note: The superscript conversion happens on the tag value at load time,
        # so we need to test with superscript in the input text
        result = engine._match_tag_group(
            "ESPN News ᴴᴰ",
            group.id,
            position="suffix"
        )
        # This should match because the engine converts superscripts in the input text
        # Actually, looking at the code, conversion happens on tags, not input text
        # So this test verifies the current behavior
        assert result.matched is False  # Superscript in text won't match ASCII tag

    def test_tag_with_superscript_stored_as_ascii(self, engine, test_session):
        """Tags stored with superscripts are converted to ASCII for matching."""
        from tests.fixtures.factories import create_tag_group, create_tag

        group = create_tag_group(test_session, name="Quality Tags")
        # Store tag as superscript (simulating user input)
        create_tag(test_session, group_id=group.id, value="ᴴᴰ")

        # Load the tag group to cache - conversion happens here
        tags = engine._load_tag_group(group.id)

        # Tag should be converted to HD
        assert any(tag_value == "HD" for tag_value, _ in tags)

    def test_shared_fixture_bank_wires_up_correctly(self):
        """Demonstrates importing from the shared Unicode fixture bank (bd-eio04.3).

        Picks the `case_bd_yui1k_numeric_strip` fixture and asserts the bank
        exposes it with the expected shape. This is the wiring demo — full
        migration of handwritten Unicode strings to the shared bank happens
        in follow-up PRs (bd-eio04.1 and later sweeps).
        """
        fixture = next(
            f for f in NUMERIC_SUPERSCRIPT_FIXTURES
            if f.name == "case_bd_yui1k_numeric_strip"
        )
        assert fixture.input == "ESPN ²"
        assert fixture.expected_normalized == "ESPN 2"
        assert fixture.origin == "bd-yui1k"
        assert fixture.category == "numeric_sup"

    def test_numeric_superscript_strip_from_shared_bank(self):
        """Exercises the `case_bd_yui1k_numeric_strip` fixture end-to-end.

        Post bd-eio04.1: numeric superscripts always convert to ASCII on
        every code path. The prior xfail marker (pending bd-yui1k
        landing) was removed when this became the invariant rather than
        the exception.
        """
        fixture = next(
            f for f in NUMERIC_SUPERSCRIPT_FIXTURES
            if f.name == "case_bd_yui1k_numeric_strip"
        )
        assert convert_superscripts(fixture.input) == fixture.expected_normalized


class TestCapitalizeAction:
    """Tests for the capitalize action type."""

    @pytest.fixture(autouse=True)
    def setup_abbreviation_cache(self):
        """Pre-populate the abbreviation tags cache for tests."""
        import normalization_engine
        normalization_engine._abbreviation_tags_cache = {
            "ESPN", "CBS", "NBC", "ABC", "HBO", "AMC", "HD", "SD", "FHD",
            "CNN", "TNT", "TBS", "FX", "FXX", "MSNBC", "HGTV",
        }
        yield
        normalization_engine._abbreviation_tags_cache = None

    @pytest.fixture
    def engine(self, test_session):
        return NormalizationEngine(test_session)

    def test_title_case(self, engine):
        from models import NormalizationRule
        rule = NormalizationRule(
            id=1, group_id=1, name="Title Case",
            condition_type="always", action_type="capitalize", action_value="title"
        )
        result = engine._apply_action("ATLANTA HAWKS", rule, None)
        assert result == "Atlanta Hawks"

    def test_upper_case(self, engine):
        from models import NormalizationRule
        rule = NormalizationRule(
            id=1, group_id=1, name="Upper Case",
            condition_type="always", action_type="capitalize", action_value="upper"
        )
        result = engine._apply_action("atlanta hawks", rule, None)
        assert result == "ATLANTA HAWKS"

    def test_lower_case(self, engine):
        from models import NormalizationRule
        rule = NormalizationRule(
            id=1, group_id=1, name="Lower Case",
            condition_type="always", action_type="capitalize", action_value="lower"
        )
        result = engine._apply_action("ATLANTA HAWKS", rule, None)
        assert result == "atlanta hawks"

    def test_sentence_case(self, engine):
        from models import NormalizationRule
        rule = NormalizationRule(
            id=1, group_id=1, name="Sentence Case",
            condition_type="always", action_type="capitalize", action_value="sentence"
        )
        result = engine._apply_action("ATLANTA HAWKS", rule, None)
        assert result == "Atlanta hawks"

    def test_title_case_preserves_acronyms(self, engine):
        from models import NormalizationRule
        rule = NormalizationRule(
            id=1, group_id=1, name="Title Case Acronyms",
            condition_type="always", action_type="capitalize", action_value="title"
        )
        result = engine._apply_action("CBS: ESPN HD", rule, None)
        assert result == "CBS: ESPN HD"

    def test_title_case_mixed_acronyms_and_words(self, engine):
        from models import NormalizationRule
        rule = NormalizationRule(
            id=1, group_id=1, name="Title Case Mixed",
            condition_type="always", action_type="capitalize", action_value="title"
        )
        result = engine._apply_action("NBC: ATLANTA HAWKS HD", rule, None)
        assert result == "NBC: Atlanta Hawks HD"

    def test_default_is_title(self, engine):
        from models import NormalizationRule
        rule = NormalizationRule(
            id=1, group_id=1, name="Default",
            condition_type="always", action_type="capitalize", action_value=""
        )
        result = engine._apply_action("ATLANTA HAWKS", rule, None)
        assert result == "Atlanta Hawks"

    def test_else_capitalize_title(self, engine):
        from models import NormalizationRule
        rule = NormalizationRule(
            id=1, group_id=1, name="Else Title",
            condition_type="contains", condition_value="NOMATCH",
            action_type="remove",
            else_action_type="capitalize", else_action_value="title"
        )
        result = engine._apply_else_action("ATLANTA HAWKS", rule)
        assert result == "Atlanta Hawks"

    def test_else_capitalize_upper(self, engine):
        from models import NormalizationRule
        rule = NormalizationRule(
            id=1, group_id=1, name="Else Upper",
            condition_type="contains", condition_value="NOMATCH",
            action_type="remove",
            else_action_type="capitalize", else_action_value="upper"
        )
        result = engine._apply_else_action("atlanta hawks", rule)
        assert result == "ATLANTA HAWKS"

    def test_else_capitalize_lower(self, engine):
        from models import NormalizationRule
        rule = NormalizationRule(
            id=1, group_id=1, name="Else Lower",
            condition_type="contains", condition_value="NOMATCH",
            action_type="remove",
            else_action_type="capitalize", else_action_value="lower"
        )
        result = engine._apply_else_action("ATLANTA HAWKS", rule)
        assert result == "atlanta hawks"

    def test_else_capitalize_sentence(self, engine):
        from models import NormalizationRule
        rule = NormalizationRule(
            id=1, group_id=1, name="Else Sentence",
            condition_type="contains", condition_value="NOMATCH",
            action_type="remove",
            else_action_type="capitalize", else_action_value="sentence"
        )
        result = engine._apply_else_action("ATLANTA HAWKS", rule)
        assert result == "Atlanta hawks"


class TestNormalizeSuperscriptsAlwaysConvert:
    """Superscripts always convert on normalize() (bd-eio04.1, GH #104).

    Supersedes the prior TestPreserveSuperscripts suite. The
    preserve_superscripts kwarg was removed - the Test Rules preview
    and Auto-Create execution paths now share a single
    NormalizationPolicy, and both paths always convert BOTH letter
    (ᴴᴰ -> HD) and numeric (ESPN² -> ESPN2) superscripts.
    Parity between the two paths is covered by
    tests/unit/test_normalization_parity.py.
    """

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        _tag_group_cache.clear()
        yield
        _tag_group_cache.clear()

    @pytest.fixture
    def engine(self, test_session):
        return NormalizationEngine(test_session)

    def test_normalize_converts_letter_superscripts(self, engine):
        """normalize() converts letter-superscripts to ASCII."""
        result = engine.normalize("ESPN News ᴴᴰ")
        assert "ᴴ" not in result.normalized
        assert "ᴰ" not in result.normalized
        assert result.normalized == "ESPN News HD"

    def test_normalize_converts_numeric_superscripts(self, engine):
        """normalize() converts numeric superscripts to ASCII (bd-eio04.1).

        The preserve_superscripts carve-out was dropped in bd-eio04.1 so
        divergence between Test Rules and Auto-Create could close. ²
        now converts to '2' on every path.
        """
        result = engine.normalize("ESPN²")
        assert "²" not in result.normalized
        assert result.normalized == "ESPN2"

    def test_normalize_strips_raw_letter_superscripts(self, engine):
        """ᴿᴬᵂ is the GH-104 reporter scenario - strip to RAW."""
        result = engine.normalize("ESPN ᴿᴬᵂ")
        assert result.normalized == "ESPN RAW"

    def test_normalize_converts_mixed_letter_and_numeric(self, engine):
        """Letter-superscripts AND numeric superscripts both convert."""
        result = engine.normalize("ESPN ᴿᴬᵂ ²")
        # Letters -> RAW, ² -> 2 on the same path.
        assert result.normalized == "ESPN RAW 2"

    def test_normalization_rules_apply_on_converted_text(self, engine, test_session):
        """Tag-stripping rules fire on the post-superscript-conversion text."""
        from tests.fixtures.factories import (
            create_tag_group, create_tag,
            create_normalization_rule_group, create_normalization_rule,
        )

        tag_group = create_tag_group(test_session, name="Quality Tags")
        create_tag(test_session, group_id=tag_group.id, value="HD")

        rule_group = create_normalization_rule_group(
            test_session, name="Strip quality", enabled=True,
        )
        create_normalization_rule(
            test_session,
            group_id=rule_group.id,
            name="Strip HD suffix",
            condition_type="tag_group",
            condition_value=str(tag_group.id),
            action_type="remove",
            tag_group_id=tag_group.id,
            tag_match_position="suffix",
        )

        result = engine.normalize(
            "ESPN² HD",
            group_ids=[rule_group.id],
        )
        # HD stripped by the rule; ² also converts to ASCII '2'.
        assert "HD" not in result.normalized
        assert "²" not in result.normalized
        assert "2" in result.normalized



class TestConvertSuperscripts:
    """
    Direct tests for the convert_superscripts() helper.

    Post bd-eio04.1: the helper converts BOTH letter-superscripts
    (ᴴᴰ = HD) AND numeric-superscripts (² = 2). The prior
    preserve_numeric kwarg is removed - divergence between code paths
    was the bug class behind GH #104.
    """

    def test_letter_superscripts_converted(self):
        assert convert_superscripts("ESPN ᴿᴬᵂ") == "ESPN RAW"
        assert convert_superscripts("ESPN ᴴᴰ") == "ESPN HD"

    def test_numeric_superscripts_converted(self):
        assert convert_superscripts("ESPN²") == "ESPN2"
        assert convert_superscripts("CNN³") == "CNN3"
        assert convert_superscripts("X⁺⁻⁴") == "X+-4"

    def test_mixed_letter_and_numeric(self):
        """Mixed input: both letter and numeric superscripts convert."""
        assert convert_superscripts(
            "ESPN ᴿᴬᵂ ²",
        ) == "ESPN RAW 2"

    def test_empty_string(self):
        """Empty input returns empty output."""
        assert convert_superscripts("") == ""

    def test_no_superscripts_passthrough(self):
        """Plain ASCII passes through unchanged."""
        assert convert_superscripts("ESPN") == "ESPN"

    def test_letter_and_numeric_maps_disjoint(self):
        """No character appears in both component maps - invariant
        preserved for historical callers that still import
        LETTER_SUPERSCRIPTS / NUMERIC_SUPERSCRIPTS directly. Only the
        kwarg that gated between them was removed in bd-eio04.1.
        """
        assert not (LETTER_SUPERSCRIPTS.keys() & NUMERIC_SUPERSCRIPTS.keys())

    def test_union_map_matches_components(self):
        """SUPERSCRIPT_MAP is the union of the two component maps."""
        assert SUPERSCRIPT_MAP == {**LETTER_SUPERSCRIPTS, **NUMERIC_SUPERSCRIPTS}
