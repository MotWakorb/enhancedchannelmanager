"""Canonical channel-number contract (bead ``enhancedchannelmanager-ic884.1``).

The contract: a channel number is a non-negative number with at most one
decimal place, or ``None`` for unassigned. These tests pin the domain itself;
the entry-point tests that prove each boundary rejects live in
``backend/tests/routers/test_channel_number_enforcement.py``.
"""

import math

import pytest

from channel_number import (
    CHANNEL_NUMBER_RULE_MESSAGE,
    InvalidChannelNumberError,
    is_valid_channel_number,
    parse_channel_number_text,
    validate_channel_number,
    validate_channel_number_in_payload,
)


class TestIsValidChannelNumber:
    """The predicate every entry point consumes."""

    @pytest.mark.parametrize(
        "value",
        [
            0,
            0.0,
            1,
            1.0,
            7,
            38,
            1.1,
            0.1,
            0.9,
            38.4,
            999.9,
            100000,
            100000.5,
            1_000_000_000,
        ],
    )
    def test_accepts_in_contract_values(self, value):
        assert is_valid_channel_number(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            1.05,  # the boundary that matters most: between 1.0 and 1.1
            0.05,
            1.15,
            1.01,
            1.001,
            1.234,
            2.0001,
            -1,
            -0.1,
            -1.0,
            float("nan"),
            float("inf"),
            float("-inf"),
        ],
    )
    def test_rejects_out_of_contract_values(self, value):
        assert is_valid_channel_number(value) is False

    @pytest.mark.parametrize("value", [None, "1", "1.0", "abc", [], {}, True, False])
    def test_rejects_non_numeric_values(self, value):
        """Strings are not channel numbers; they go through the text parser.

        ``True``/``False`` are excluded explicitly: Python treats ``bool`` as a
        subclass of ``int``, so without the guard ``True`` would read as 1.0.
        """
        assert is_valid_channel_number(value) is False

    def test_tolerates_binary_float_dust_without_admitting_a_half_tenth(self):
        """The tolerance absorbs representation error, not a real half-tenth.

        ``0.7 + 0.1`` is ``0.7999999999999999`` in binary floating point, and
        ``0.2 + 0.1`` is ``0.30000000000000004``. Both are the channel numbers
        ``0.8`` and ``0.3``, and both must pass, while ``1.05``, a genuine
        two-decimal value, must not.
        """
        for drifted, intended in ((0.7 + 0.1, 0.8), (0.2 + 0.1, 0.3)):
            assert drifted != intended
            assert is_valid_channel_number(drifted) is True
        assert is_valid_channel_number(1.05) is False


class TestValidateChannelNumber:
    """The raising form, used where the caller wants the message."""

    def test_none_is_unassigned_and_allowed_by_default(self):
        assert validate_channel_number(None) is None

    def test_none_can_be_disallowed(self):
        with pytest.raises(InvalidChannelNumberError):
            validate_channel_number(None, allow_none=False)

    def test_returns_the_value_unchanged_as_a_float(self):
        assert validate_channel_number(7) == 7.0
        assert isinstance(validate_channel_number(7), float)
        assert validate_channel_number(1.1) == 1.1

    def test_rejects_rather_than_rounding(self):
        """The PO chose rejection over silent normalisation."""
        with pytest.raises(InvalidChannelNumberError):
            validate_channel_number(1.05)

    def test_message_names_the_rule_and_gives_an_example(self):
        with pytest.raises(InvalidChannelNumberError) as exc:
            validate_channel_number(1.05)
        assert str(exc.value) == CHANNEL_NUMBER_RULE_MESSAGE
        assert "one decimal place" in str(exc.value)
        assert "1.1" in str(exc.value)


class TestParseChannelNumberText:
    """Operator-entered and CSV text."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("0", 0.0),
            ("1", 1.0),
            ("7", 7.0),
            ("7.0", 7.0),
            ("07", 7.0),
            ("1.1", 1.1),
            ("1.10", 1.1),
            ("  38.4  ", 38.4),
            ("999.9", 999.9),
        ],
    )
    def test_parses_in_contract_text(self, text, expected):
        assert parse_channel_number_text(text) == pytest.approx(expected)

    def test_canonical_equivalents_compare_equal(self):
        """Acceptance criterion 3: 7, 7.0 and 07 are the same number."""
        assert (
            parse_channel_number_text("7")
            == parse_channel_number_text("7.0")
            == parse_channel_number_text("07")
        )

    @pytest.mark.parametrize("text", ["", "   ", None])
    def test_empty_text_is_unassigned(self, text):
        assert parse_channel_number_text(text) is None

    def test_empty_text_can_be_disallowed(self):
        with pytest.raises(InvalidChannelNumberError):
            parse_channel_number_text("", allow_empty=False)

    @pytest.mark.parametrize(
        "text",
        [
            "1.05",
            "1.001",
            "-5",
            "-0.1",
            "abc",
            "nan",
            "NaN",
            "inf",
            "Infinity",
            "1e3",
            "1_0",
            "+7",
            "7.",
            ".5",
            "1,5",
            "7 8",
        ],
    )
    def test_rejects_out_of_contract_text(self, text):
        with pytest.raises(InvalidChannelNumberError):
            parse_channel_number_text(text)

    def test_accepts_a_number_that_is_already_numeric(self):
        assert parse_channel_number_text(1.1) == 1.1

    def test_rejects_a_numeric_that_is_out_of_contract(self):
        with pytest.raises(InvalidChannelNumberError):
            parse_channel_number_text(1.05)

    def test_rejects_non_finite_numerics(self):
        for value in (math.nan, math.inf, -math.inf):
            with pytest.raises(InvalidChannelNumberError):
                parse_channel_number_text(value)


class TestValidateChannelNumberInPayload:
    """Free-form update dicts (``PATCH`` body, bulk ``updateChannel`` data)."""

    def test_absent_key_passes(self):
        validate_channel_number_in_payload({"name": "ESPN"})

    def test_explicit_none_clears_the_number_and_passes(self):
        validate_channel_number_in_payload({"channel_number": None})

    def test_in_contract_value_passes(self):
        validate_channel_number_in_payload({"channel_number": 1.1})

    def test_out_of_contract_value_raises(self):
        with pytest.raises(InvalidChannelNumberError):
            validate_channel_number_in_payload({"channel_number": 1.05})

    def test_non_mapping_is_ignored(self):
        validate_channel_number_in_payload(None)
        validate_channel_number_in_payload("not a dict")
