"""Canonical channel-number contract (bead ``enhancedchannelmanager-ic884.1``).

The contract: a channel number is a non-negative number with at most one
decimal place, or ``None`` for unassigned. These tests pin the domain itself;
the entry-point tests that prove each boundary rejects live in
``backend/tests/routers/test_channel_number_enforcement.py``.
"""

import math
import sys

import pytest

from channel_number import (
    CHANNEL_NUMBER_RULE_MESSAGE,
    InvalidChannelNumberError,
    _EXACT_INTEGER_FLOOR,
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
            # The magnitudes where scaling the whole value overflows. Every
            # float at or above 2**53 is an exact integer, so it carries no
            # fractional part at all and is in contract under a rule that names
            # no maximum. See `test_answers_rather_than_raising_at_float_limits`.
            1e307,
            1e308,
            sys.float_info.max,
        ],
    )
    def test_accepts_in_contract_values(self, value):
        assert is_valid_channel_number(value) is True

    @pytest.mark.parametrize(
        "value",
        [1e15, 1e16, 2.0**53, 1e307, 1e308, sys.float_info.max],
    )
    def test_answers_rather_than_raising_at_float_limits(self, value):
        """A validator must answer every finite input, never raise.

        Scaling the whole value by ten overflows to infinity near
        ``sys.float_info.max``, and ``round(inf)`` raises ``OverflowError``.
        Raising here would surface as a 500 from every entry point that
        consumes the predicate, turning "reject this input" into "server
        error". The ``2**53`` short-circuit answers before any scaling happens,
        so no input reaches a multiplication that could overflow.
        """
        assert is_valid_channel_number(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            100000.1,
            1000000.1,
            10000000.1,  # the regression: fraction-only scaling rejected this
            10000000.5,
            123456789.9,
            1e12 + 0.1,
            1e12 + 0.5,
            1e14 + 0.1,
        ],
    )
    def test_accepts_large_magnitude_values_with_representable_tenths(self, value):
        """Ordinary big channel numbers, not just huge integers.

        Scaling only the fractional part to dodge the overflow above threw away
        the precision these depend on: ``math.fmod(10000000.1, 1.0)`` is
        ``0.09999999962747097``, whose scaled distance from a whole tenth is
        ``3.7e-9`` -- past the ``1e-9`` tolerance, so ``10000000.1`` was
        rejected by both halves of the stack. Scaling the whole value returns
        ``100000001.0`` exactly. This is the case the earlier limit tests
        missed: they covered huge integers, which have no fractional part at
        all, and so could not see the loss.
        """
        assert is_valid_channel_number(value) is True

    def test_accepts_every_one_decimal_value_across_the_representable_range(self):
        """The accept-side property, sampled across the whole float range.

        A one-decimal value is exactly what ``float(f"{k // 10}.{k % 10}")``
        produces, so the population to sweep is ``k / 10`` for integer ``k``.
        Scaling back by ten has to return ``k`` for every one of them. The
        deviation is not merely inside the tolerance here, it is exactly zero,
        which is why the tolerance can stay absolute: it is doing no work for
        this population and is reserved for arithmetic dust.
        """
        step = 7  # coprime with 10, so every tenths digit is exercised
        for exponent in range(0, 17):
            low = 10**exponent
            for offset in range(0, 4000):
                k = low + offset * step
                value = k / 10.0
                if value >= 2.0**53:
                    break
                assert is_valid_channel_number(value) is True, f"rejected {value!r}"
                assert abs(value * 10 - round(value * 10)) == 0.0

    @pytest.mark.parametrize(
        "value",
        [
            2.0**53 - 2,  # below the short-circuit: goes through the scaling
            2.0**53 - 1,
            2.0**53,  # the short-circuit itself
            math.nextafter(2.0**53, math.inf),  # just above it
            2.0**52,  # well below, spacing is already 1
            2.0**52 + 0.5,  # spacing is 1 here, so this IS 2**52
        ],
    )
    def test_answers_consistently_either_side_of_the_exact_integer_floor(self, value):
        """The ``2**53`` short-circuit must not introduce a discontinuity.

        Below the floor the value is scaled and compared; at and above it the
        answer is returned directly. Every value straddling the boundary is an
        exact integer, so both paths have to say the same thing. ``2**52 + 0.5``
        makes the point that this is about representability rather than the
        branch: float spacing at ``2**52`` is already 1, so that expression is
        just ``2**52`` and is accepted as the integer it actually is.
        """
        assert is_valid_channel_number(value) is True

    def test_the_exact_integer_floor_short_circuit_cannot_overflow(self):
        """The guard's other job: keep the scaling below it overflow-proof.

        The largest value that reaches the multiplication is the float just
        below the floor, and scaling that by ten stays finite by an enormous
        margin. This is what replaced the fraction-only scaling as the overflow
        defence, so it is pinned rather than left as a comment. The module
        constant is read rather than restated, so raising the floor to a value
        that could overflow fails here instead of in production.
        """
        assert _EXACT_INTEGER_FLOOR == 2.0**53
        largest_scaled = math.nextafter(_EXACT_INTEGER_FLOOR, 0.0) * 10
        assert math.isfinite(largest_scaled)
        assert largest_scaled < sys.float_info.max / 1e100

    @pytest.mark.parametrize("value", [10**400, 10**309, -(10**400), 2**1024])
    def test_answers_rather_than_raising_on_arbitrary_precision_integers(self, value):
        """``float(10**400)`` raises ``OverflowError``; the predicate must not.

        Python ``int`` is arbitrary precision, so a JSON body can carry a value
        with no float representation. Before the guard this escaped as an
        ``OverflowError`` from every entry point consuming the predicate, which
        is a 500 rather than a rejection.

        ``False`` is the deliberate answer rather than "an ``int`` has no
        fractional part, so it is in contract": the frontend parses the same
        JSON to ``Infinity`` and rejects it as non-finite, and the two halves
        document an identical rule, so they must agree on every input either
        side can be handed.
        """
        assert is_valid_channel_number(value) is False

    @pytest.mark.parametrize("value", [10**300, 10**308, 2**60])
    def test_accepts_huge_integers_with_a_float_representation(self, value):
        """The rejection above is about representability, not magnitude.

        ``10**308`` converts to a finite float and is an exact integer, so it
        stays in contract under a rule that names no maximum. The frontend
        agrees: JSON ``1e308`` parses to a finite ``number``.
        """
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

    def test_rejects_the_half_tenth_at_every_magnitude_that_can_hold_one(self):
        """The reject-side range the tolerance comment claims.

        A half-tenth is the nearest out-of-contract value to a tenth, so it is
        the hardest thing to reject. Its scaled distance is exactly 0.5, a
        margin of 5e8 over the ``1e-9`` tolerance, and that holds at every
        magnitude where the half-tenth is a distinct float at all. It stops
        being one just above ``2**48``: float spacing reaches 0.125 at
        ``2**49``, which exceeds the 0.05 gap, so ``base + 0.05`` is simply
        ``base`` there and is correctly accepted as the integer it has become.
        """
        for exponent in range(0, 15):
            base = float(10**exponent)
            value = base + 0.05
            assert value != base, f"0.05 not representable at 1e{exponent}"
            assert abs(value * 10 - round(value * 10)) == 0.5
            assert is_valid_channel_number(value) is False, f"admitted {value!r}"

        # Where the distinction dissolves, and why accepting is then correct.
        assert 2.0**49 + 0.05 == 2.0**49
        assert is_valid_channel_number(2.0**49 + 0.05) is True
        assert 2.0**48 + 0.05 != 2.0**48
        assert is_valid_channel_number(2.0**48 + 0.05) is False


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

    def test_arbitrary_precision_integers_reject_rather_than_overflow(self):
        """The raising form must raise the contract error, not ``OverflowError``.

        Callers catch :class:`InvalidChannelNumberError` and turn it into a 400.
        An ``OverflowError`` escaping ``float(value)`` would bypass every one of
        those handlers and surface as a 500 instead.
        """
        with pytest.raises(InvalidChannelNumberError) as exc:
            validate_channel_number(10**400)
        assert str(exc.value) == CHANNEL_NUMBER_RULE_MESSAGE

    def test_large_magnitude_one_decimal_values_survive_validation(self):
        """The regression, at the raising boundary callers actually use."""
        assert validate_channel_number(10000000.1) == 10000000.1
        assert validate_channel_number(1e12 + 0.1) == 1e12 + 0.1

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
