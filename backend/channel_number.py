"""Canonical channel-number contract for ECM (bead ``enhancedchannelmanager-ic884.1``).

A channel number is a **non-negative number with at most one decimal place**.
That is the PO's rule, recorded on bead ``enhancedchannelmanager-ic884.1``:
Dispatcharr's channel-number precision is one significant digit, so only the
tenths place carries meaning.

``None`` means "unassigned" and is always allowed; a channel with no number is a
normal state in Dispatcharr.

What the contract deliberately does NOT say:

* **No uniqueness.** Dispatcharr declares ``channel_number`` as a non-unique
  float and its release notes state duplicates are permitted. Real lineups have
  them. Uniqueness is a separate question and is not enforced here.
* **No maximum among floats.** The PO's rule named none, and Dispatcharr stores
  a plain float, so inventing an upper bound here would narrow compatibility. An
  absurd-but-finite value such as ``1e308`` is therefore in contract: it is an
  exact integer (every float at or above ``2**53`` is), so it carries no
  fractional part. It is answered, not raised on. ``inf`` and ``nan`` are not
  finite and are rejected.

There is one limit, and it is about representability rather than magnitude.
Python ``int`` is arbitrary precision, so a JSON body can carry a value such as
``10**400`` that has no ``float`` representation at all. Those are **rejected**.
A channel number has to survive the round trip this contract spans: JSON number
to a JavaScript ``number`` in the browser, and to Dispatcharr's plain float
column. ``10**400`` survives neither -- the frontend parses it to ``Infinity``
and rejects it as non-finite, and there is no float to store. Accepting it in
Python on the grounds that an ``int`` has no fractional part would leave the two
halves disagreeing about an input either side can be handed, while their
docstrings claim an identical rule. So the predicate answers ``False`` rather
than raising ``OverflowError`` out of ``float()``.

Out-of-contract values are **rejected at the boundary**, not silently rounded.
That is an explicit PO choice: an import that previously succeeded may now fail,
on the grounds that surfacing bad data beats silently altering it.

Every backend entry point that accepts a channel number consumes this module
rather than re-implementing a check. The frontend mirror is
``frontend/src/utils/channelNumber.ts`` and carries the identical message text.

Tests: ``backend/tests/unit/test_channel_number.py``.
"""

from __future__ import annotations

import math
import re
from typing import Annotated, Any, Mapping, Optional

from pydantic import BeforeValidator
from pydantic_core import PydanticCustomError

__all__ = [
    "CHANNEL_NUMBER_DECIMAL_PLACES",
    "CHANNEL_NUMBER_RULE_MESSAGE",
    "InvalidChannelNumberError",
    "ChannelNumber",
    "is_valid_channel_number",
    "validate_channel_number",
    "parse_channel_number_text",
    "validate_channel_number_in_payload",
]


#: How many decimal places a channel number may carry.
CHANNEL_NUMBER_DECIMAL_PLACES = 1

#: The one operator-facing sentence every rejection uses, backend and frontend.
#: Keep byte-identical with ``CHANNEL_NUMBER_RULE_MESSAGE`` in
#: ``frontend/src/utils/channelNumber.ts``.
CHANNEL_NUMBER_RULE_MESSAGE = (
    "Channel numbers must be 0 or greater and support one decimal place "
    "(for example 1.0 or 1.1)."
)

# One tick per tenth, matching the grid `frontend/src/utils/channelNumberShift.ts`
# plans on.
#
# What the tolerance guarantees, and over what range. It is compared against
# `value * 10`, so in value terms it admits a deviation of 1e-10.
#
# * Reject side. The nearest out-of-contract value to a tenth is half a tick
#   away, which is 0.5 once scaled -- a margin of 5e8 over the tolerance, so
#   `1.05` can never slip through. Measured: `10**n + 0.05` scales to a
#   deviation of exactly 0.5 and is rejected for every n from 0 to 14. That
#   discrimination holds up to `2**48`. At `2**49` float spacing is 0.125, which
#   exceeds the 0.05 half-tick, so `base + 0.05` is not a distinct float and
#   there is no two-decimal value left to reject.
# * Accept side. The tolerance is not what makes ordinary numbers pass. For
#   every one-decimal value `k / 10` below `_EXACT_INTEGER_FLOOR`, scaling back
#   by ten returns `k` exactly: measured deviation 0.0 over 3.4M sampled values
#   across all seventeen decades, plus exhaustively over 0.0-200.0. Parsed input
#   (`float("10000000.1")`) and planned input (`channelNumberShift.ts` divides
#   integer ticks by ten once, at the end) are both drawn from that population.
# * Dust. The tolerance therefore earns its keep on one case only: arithmetic
#   drift, such as `0.7 + 0.1` being `0.7999999999999999`. Being absolute, the
#   dust it absorbs shrinks with magnitude -- about 4.5e5 float steps at
#   magnitude 1, about 7 at 1e5, and less than one above roughly 1e6. Above that
#   the predicate accepts only values that round-trip exactly, which is what the
#   two producers above deliver. A magnitude-relative tolerance was measured as
#   the alternative and is worse where it matters: at 8 float steps it accepts
#   `1e14 + 0.05`, which is a real two-decimal value that must be rejected.
_TENTHS = 10
_TENTH_TOLERANCE = 1e-9

# Every float at or above `2**53` is an exact integer, because the gap between
# adjacent floats there is at least 2. Such a value carries no fractional part,
# so it is in contract under a rule that names no maximum, and the check can
# short-circuit before any arithmetic. That is also what keeps the scaling below
# safe: the largest value that reaches it is just under `2**53`, and
# `2**53 * 10` is about 9.0e16, some 290 orders of magnitude below
# `sys.float_info.max`, so the multiplication cannot overflow.
_EXACT_INTEGER_FLOOR = 2.0**53

# Text accepted before the numeric rule is applied. Digits, optionally followed
# by a decimal point and one or more digits. This admits the canonical
# equivalents the contract requires to compare equal (`7`, `7.0`, `07`, `1.10`)
# and excludes forms whose meaning would have to be guessed (`1e3`, `.5`, `1_0`,
# `+7`, `7.`). Sign is excluded here so a negative reads as out-of-contract
# rather than as unparseable.
_CHANNEL_NUMBER_TEXT = re.compile(r"^\d+(?:\.\d+)?$")


class InvalidChannelNumberError(ValueError):
    """Raised when a value is outside the canonical channel-number domain.

    Carries the operator-facing message, so callers can hand ``str(exc)``
    straight to an HTTP ``detail`` without composing their own wording.
    """

    def __init__(self, message: str = CHANNEL_NUMBER_RULE_MESSAGE) -> None:
        super().__init__(message)


def is_valid_channel_number(value: Any) -> bool:
    """Return whether ``value`` is a channel number the contract can hold.

    ``None`` is not a channel number and returns ``False``; callers that treat
    "unassigned" as acceptable check for ``None`` themselves (or use
    :func:`validate_channel_number`, which allows it by default).
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        number = float(value)
    except OverflowError:
        # ``int`` is arbitrary precision, so a JSON body can carry a value with
        # no float representation (``10**400``). Answer rather than raise: a
        # predicate that raises turns "reject this input" into a 500 at every
        # entry point that consumes it. ``False`` is also the answer the
        # frontend gives, which parses the same JSON to ``Infinity``. See the
        # module docstring for why rejection is the right answer, not merely
        # the convenient one.
        return False
    if not math.isfinite(number):
        return False
    if number < 0:
        return False
    if number >= _EXACT_INTEGER_FLOOR:
        # An exact integer: no fractional part to check, and short-circuiting
        # here is what makes the scaling below overflow-proof.
        return True
    # Scale the WHOLE value, not just its fractional part. Scaling only the
    # fraction looks like the safer way to dodge the overflow, but it discards
    # the precision that makes an ordinary large channel number work:
    # ``math.fmod(10000000.1, 1.0)`` is ``0.09999999962747097``, which scales to
    # ``0.9999999962747097`` and misses a whole tenth by far more than the
    # tolerance, so ``10000000.1`` reads as out of contract. Scaling the whole
    # value returns ``100000001.0`` exactly. The magnitude guard above supplies
    # the overflow safety instead, so both properties hold at once.
    scaled = number * _TENTHS
    return abs(scaled - round(scaled)) <= _TENTH_TOLERANCE


def validate_channel_number(value: Any, *, allow_none: bool = True) -> Optional[float]:
    """Return ``value`` as a float, or raise :class:`InvalidChannelNumberError`.

    The value is returned unchanged apart from the ``int``-to-``float`` widening.
    Nothing here rounds or snaps: an out-of-contract value is rejected, never
    repaired.
    """
    if value is None:
        if allow_none:
            return None
        raise InvalidChannelNumberError()
    if not is_valid_channel_number(value):
        raise InvalidChannelNumberError()
    return float(value)


def parse_channel_number_text(
    text: Any, *, allow_empty: bool = True
) -> Optional[float]:
    """Parse operator-entered or CSV text into a channel number.

    Empty or whitespace-only text means "unassigned" and returns ``None`` when
    ``allow_empty``. Anything else must both look like a plain decimal number
    and satisfy the numeric contract, otherwise
    :class:`InvalidChannelNumberError` is raised.
    """
    if text is None:
        if allow_empty:
            return None
        raise InvalidChannelNumberError()
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return validate_channel_number(text, allow_none=False)
    if not isinstance(text, str):
        raise InvalidChannelNumberError()

    stripped = text.strip()
    if not stripped:
        if allow_empty:
            return None
        raise InvalidChannelNumberError()
    if not _CHANNEL_NUMBER_TEXT.match(stripped):
        raise InvalidChannelNumberError()
    try:
        number = float(stripped)
    except (TypeError, ValueError) as exc:  # pragma: no cover - regex precludes it
        raise InvalidChannelNumberError() from exc
    return validate_channel_number(number, allow_none=False)


def validate_channel_number_in_payload(payload: Any) -> None:
    """Validate the ``channel_number`` entry of a free-form update payload.

    Several write paths take an untyped ``dict`` of channel fields (the
    ``PATCH /api/channels/{id}`` body and the bulk-commit ``updateChannel``
    operation). Absent key means "not being changed" and passes; an explicit
    ``None`` means "clear the number" and passes.
    """
    if not isinstance(payload, Mapping):
        return
    if "channel_number" not in payload:
        return
    validate_channel_number(payload["channel_number"], allow_none=True)


def _pydantic_channel_number(value: Any) -> Any:
    """Pydantic hook: reject out-of-contract values with the canonical message.

    ``PydanticCustomError`` is used rather than a bare ``ValueError`` so the
    ``msg`` FastAPI serialises is exactly :data:`CHANNEL_NUMBER_RULE_MESSAGE`,
    with no ``"Value error, "`` prefix for the operator to read past.
    """
    if value is None:
        return value
    if isinstance(value, str):
        try:
            return parse_channel_number_text(value, allow_empty=True)
        except InvalidChannelNumberError:
            raise PydanticCustomError("channel_number", CHANNEL_NUMBER_RULE_MESSAGE) from None
    if not is_valid_channel_number(value):
        raise PydanticCustomError("channel_number", CHANNEL_NUMBER_RULE_MESSAGE)
    return float(value)


#: Field type for every request model carrying a channel number. Use as
#: ``Optional[ChannelNumber]`` where the field may be absent or cleared.
ChannelNumber = Annotated[float, BeforeValidator(_pydantic_channel_number)]
