"""
Smoke tests for the shared Unicode fixture bank (bd-eio04.3).

These tests validate that the fixture module loads correctly and that every
fixture is well-formed. They do NOT exercise the normalization engine — that
is the job of downstream consumers (bd-eio04.1 parity tests, bd-eio04.4 NFC
tests, etc.).

The goal: if this test is green, the fixture bank is structurally sound and
safe for import by every other normalization test.
"""
import pytest

from tests.fixtures.unicode_fixtures import (
    ALL_FIXTURES,
    COMBINING_FIXTURES,
    LETTER_SUPERSCRIPT_FIXTURES,
    MIXED_FIXTURES,
    NFC_NFD_FIXTURES,
    NUMERIC_SUPERSCRIPT_FIXTURES,
    NormalizationFixture,
    RTL_FIXTURES,
    ZERO_WIDTH_FIXTURES,
)


VALID_CATEGORIES = {
    "letter_sup",
    "numeric_sup",
    "mixed",
    "nfc_nfd",
    "zero_width",
    "combining",
    "rtl",
}


class TestFixtureBankWellFormed:
    """Every fixture in the bank has the metadata downstream tests rely on."""

    def test_all_fixtures_nonempty(self):
        """The bank has at least the minimum required cases."""
        assert len(ALL_FIXTURES) >= 12, (
            f"Fixture bank below minimum set (got {len(ALL_FIXTURES)}, expected >= 12)"
        )

    @pytest.mark.parametrize("fixture", ALL_FIXTURES, ids=lambda f: f.name)
    def test_fixture_has_required_metadata(self, fixture: NormalizationFixture):
        """Every fixture has a non-empty name, origin, and category."""
        assert fixture.name, "fixture.name must be non-empty"
        assert fixture.origin, f"fixture.origin must be non-empty for {fixture.name}"
        assert fixture.category, f"fixture.category must be non-empty for {fixture.name}"

    @pytest.mark.parametrize("fixture", ALL_FIXTURES, ids=lambda f: f.name)
    def test_fixture_category_is_valid(self, fixture: NormalizationFixture):
        """Category is from the agreed taxonomy."""
        assert fixture.category in VALID_CATEGORIES, (
            f"fixture {fixture.name!r} has unknown category {fixture.category!r}; "
            f"expected one of {sorted(VALID_CATEGORIES)}"
        )

    def test_fixture_names_are_unique(self):
        """Fixture names are stable IDs — no duplicates."""
        names = [f.name for f in ALL_FIXTURES]
        duplicates = {n for n in names if names.count(n) > 1}
        assert not duplicates, f"Duplicate fixture names: {sorted(duplicates)}"

    def test_required_named_fixtures_present(self):
        """The grooming-locked minimum set is present by name."""
        required = {
            "case_issue104_espn_hd",
            "case_issue104_fox_sports_2",
            "case_bd_yj5yi_reorder",
            "case_bd_yui1k_numeric_strip",
            "case_nfd_decomposition_cafe",
            "case_zero_width_joiner_injection",
            "case_zero_width_space_suffix",
            "case_bom_prefix",
            "case_mid_string_superscript",
            "case_numeric_superscript_framerate",
            "case_empty_string",
            "case_only_superscripts",
        }
        names = {f.name for f in ALL_FIXTURES}
        missing = required - names
        assert not missing, f"Required fixtures missing from bank: {sorted(missing)}"


class TestCategoryListsMatchAllFixtures:
    """Per-category lists are subsets of ALL_FIXTURES with matching categories."""

    @pytest.mark.parametrize(
        "category_list, expected_category",
        [
            (LETTER_SUPERSCRIPT_FIXTURES, "letter_sup"),
            (NUMERIC_SUPERSCRIPT_FIXTURES, "numeric_sup"),
            (MIXED_FIXTURES, "mixed"),
            (NFC_NFD_FIXTURES, "nfc_nfd"),
            (ZERO_WIDTH_FIXTURES, "zero_width"),
            (COMBINING_FIXTURES, "combining"),
            (RTL_FIXTURES, "rtl"),
        ],
    )
    def test_category_list_is_homogeneous(self, category_list, expected_category):
        """Every entry in a category list has the matching category tag."""
        for fixture in category_list:
            assert fixture.category == expected_category, (
                f"{fixture.name} in {expected_category} list has category "
                f"{fixture.category!r}"
            )

    def test_category_lists_are_subsets_of_all_fixtures(self):
        """Nothing lives in a category list without being in ALL_FIXTURES."""
        all_names = {f.name for f in ALL_FIXTURES}
        for list_name, category_list in [
            ("LETTER_SUPERSCRIPT_FIXTURES", LETTER_SUPERSCRIPT_FIXTURES),
            ("NUMERIC_SUPERSCRIPT_FIXTURES", NUMERIC_SUPERSCRIPT_FIXTURES),
            ("MIXED_FIXTURES", MIXED_FIXTURES),
            ("NFC_NFD_FIXTURES", NFC_NFD_FIXTURES),
            ("ZERO_WIDTH_FIXTURES", ZERO_WIDTH_FIXTURES),
            ("COMBINING_FIXTURES", COMBINING_FIXTURES),
            ("RTL_FIXTURES", RTL_FIXTURES),
        ]:
            for fixture in category_list:
                assert fixture.name in all_names, (
                    f"{fixture.name} in {list_name} but not in ALL_FIXTURES"
                )


class TestFixtureDataclassIsFrozen:
    """NormalizationFixture is immutable so tests can't mutate shared state."""

    def test_fixture_is_frozen(self):
        sample = ALL_FIXTURES[0]
        with pytest.raises((AttributeError, Exception)):
            sample.name = "mutated"  # type: ignore[misc]
