"""The final channel numbering a bulk commit proposes, and what is wrong with it.

Bead ``enhancedchannelmanager-ic884.2``. Validating staged numbering operations
one at a time misses the collisions their COMBINED result creates. Every edit in
"move ESPN off 5, move TNT onto 5, move AMC onto 5" is individually legal
against the lineup the operator is looking at; only the three together put two
channels on one number. So this module materialises the proposed final state —
the latest Dispatcharr lineup plus every staged create, update, delete and
range assignment — and validates that, once, before the first mutation.

Two things it deliberately does NOT do, both settled on bead
``enhancedchannelmanager-ic884.1``:

* **It does not enforce uniqueness.** Dispatcharr declares ``channel_number``
  as a non-unique float and its release notes state duplicates are permitted;
  real lineups have them. A duplicate that already exists in the lineup is
  therefore never reported. Only a duplicate this request CREATES is.
* **It does not police numbers this request did not touch.** There is no ECM
  column for ``channel_number``; the values come from Dispatcharr, which
  enforces neither precision nor uniqueness, so a value written before the
  contract landed or by another client can be out of contract. Refusing the
  whole commit over a value the operator cannot reach from here would be a
  dead end rather than a safeguard.

An operator who was warned about a duplicate and chose it anyway sends
``acknowledgedDuplicate`` on the operation that creates it, carrying the NUMBER
and the OCCUPANTS they were shown. A pile-up is deliberate when every channel
this request put on the number arrived carrying consent naming everyone already
standing there; one unacknowledged newcomer is enough to make the whole thing
an error again, and only that newcomer's operation is named, because the rest is
a decision the operator already made.

The occupants are load-bearing, not decoration. Consent to join ESPN on 5 is not
consent to join AMC on 5, so an acknowledgement cannot be inherited by a later
placement, accumulated over a channel's history, or survive the occupant it
named moving away — which is exactly what a bare number did.

Comparison is canonical, on the tenths grid ``channel_number.py`` defines, so
``7``, ``7.0`` and ``07`` are one number and ``0.7 + 0.1`` is the channel
number ``0.8``.

The frontend runs the same check before it posts anything
(``frontend/src/utils/channelNumberPlan.ts``). Neither is the other's safety
net: the browser's is what can name the STAGED OPERATIONS the operator
recognises, and this one is what holds for a caller that never touches the UI.

Tests: ``backend/tests/unit/test_channel_number_plan.py``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Sequence

from channel_number import (
    CHANNEL_NUMBER_RULE_MESSAGE,
    CHANNEL_NUMBER_TICKS_PER_UNIT,
    is_valid_channel_number,
)

__all__ = [
    "NumberingIssue",
    "FinalNumberingState",
    "build_final_numbering_state",
    "evaluate_final_numbering",
]


# Every float at or above ``2**53`` is an exact integer, so scaling one by ten
# would leave the exactly-representable range (and ``1e308 * 10`` is ``inf``,
# which would make every absurd value compare equal to every other). Above the
# floor the float IS the identity; below it, the tenths tick is. Mirrors
# ``_EXACT_INTEGER_FLOOR`` in ``channel_number.py``.
_EXACT_INTEGER_FLOOR = 2.0**53


def _slot_key(value: float) -> str:
    """The slot ``value`` occupies. Two numbers share a slot exactly when this
    module calls them the same channel number.

    A string rather than a number so the two regimes cannot collide: below the
    floor a tick index, at or above it the float's own identity, with prefixes
    that keep one from ever being read as the other.
    """
    number = float(value)
    if not math.isfinite(number):
        return f"n{number}"
    if abs(number) >= _EXACT_INTEGER_FLOOR:
        return f"x{number!r}"
    return f"t{round(number * CHANNEL_NUMBER_TICKS_PER_UNIT)}"


def _same_number(a: Any, b: Any) -> bool:
    """Whether two channel numbers are the same number, canonically.

    ``None`` is "unassigned", which is not a slot: two unnumbered channels do
    not collide with each other, so ``None`` is never equal to anything,
    including another ``None``.
    """
    if a is None or b is None:
        return False
    if isinstance(a, bool) or isinstance(b, bool):
        return False
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return False
    if not math.isfinite(float(a)) or not math.isfinite(float(b)):
        return False
    return _slot_key(a) == _slot_key(b)


@dataclass(frozen=True)
class _Acknowledgement:
    """Consent carried by the operation that produced a channel's placement."""

    #: The slot the caller agreed to share.
    slot: str
    #: The channels the caller was told were already holding it.
    occupant_ids: frozenset[int]


@dataclass
class _Placement:
    channel_id: int
    name: str
    number: Optional[float]
    #: The number before this request, or ``None`` for a channel it creates.
    base_number: Optional[float]
    #: True when this channel did not exist before the request.
    is_new: bool
    #: Indexes of the operations that set this channel's number, in order.
    touched_by: list[int] = field(default_factory=list)
    #: The acknowledgement carried by the operation that made the FINAL
    #: placement, or ``None``.
    #:
    #: Bound to one placement, never accumulated. A channel moved onto an
    #: occupied 5 with consent, then to 6, then back onto 5 with nobody asked,
    #: ends up with no acknowledgement at all: consent belonged to the
    #: operation that was superseded. Unioning them over the channel's history
    #: is what let an old confirmation authorise a placement nobody saw.
    #: Mirrors ``NumberPlacement.acknowledgement`` in
    #: ``frontend/src/utils/channelNumberPlan.ts``.
    acknowledgement: Optional[_Acknowledgement] = None

    @property
    def moved(self) -> bool:
        """Did this request put the channel where it now is?

        Judged against the number it started on, not against each intermediate
        step: a channel moved and moved back has not been placed anywhere,
        whatever route it took to get there. That is what keeps an unrelated
        edit from turning a duplicate the lineup already had into this
        request's problem.
        """
        if self.is_new:
            return True
        if self.base_number is None and self.number is None:
            return False
        if self.base_number is None or self.number is None:
            return True
        return not _same_number(self.base_number, self.number)


@dataclass
class FinalNumberingState:
    """The lineup as it would stand once every operation had been applied."""

    placements: list[_Placement]

    def channel_ids(self) -> list[int]:
        return [placement.channel_id for placement in self.placements]

    def number_of(self, channel_id: int) -> Optional[float]:
        for placement in self.placements:
            if placement.channel_id == channel_id:
                return placement.number
        return None


@dataclass
class NumberingIssue:
    """One thing wrong with the proposed final state."""

    #: ``duplicate_channel_number`` or ``invalid_channel_number``.
    type: str
    severity: str
    #: Operator-facing sentence naming the number, the channels, and what to do.
    message: str
    channel_number: float
    #: Every channel sitting on the number in the proposed final state.
    channel_ids: list[int]
    #: Positions in ``operations`` of the operations to change to resolve it.
    operation_indexes: list[int]

    def as_validation_issue(self) -> dict:
        """The shape ``/api/channels/bulk-commit`` returns in ``validationIssues``."""
        issue = {
            "type": self.type,
            "severity": self.severity,
            "message": self.message,
            "channelNumber": self.channel_number,
            "channelIds": self.channel_ids,
        }
        if self.operation_indexes:
            # The first is what the response schema has room for; the full list
            # travels in ``operationIndexes`` so a caller can highlight all of
            # them rather than only the first one it can name.
            issue["operationIndex"] = self.operation_indexes[0]
            issue["operationIndexes"] = self.operation_indexes
        if self.channel_ids:
            issue["channelId"] = self.channel_ids[0]
        return issue


def _acknowledgement(op: Any) -> Any:
    """The collision this operation says the caller accepted, or ``None``."""
    acknowledged = getattr(op, "acknowledgedDuplicate", None)
    if acknowledged is None:
        return None
    number = getattr(acknowledged, "number", None)
    if not isinstance(number, (int, float)) or isinstance(number, bool):
        return None
    if not math.isfinite(float(number)):
        return None
    return acknowledged


def build_final_numbering_state(
    existing_channels: Iterable[dict],
    operations: Sequence[Any],
) -> FinalNumberingState:
    """Materialise the numbering every operation together would leave behind.

    Args:
        existing_channels: The lineup as Dispatcharr currently holds it. Each
            entry needs ``id``, ``name`` and ``channel_number``.
        operations: The request's operations, in the order they will run. Later
            operations win, exactly as the executor will apply them.

    An operation naming a channel that is not in ``existing_channels`` and was
    not created earlier in the same request is skipped rather than invented:
    whether that channel exists is a different question, already answered by
    the per-operation validation, and answering it twice in two voices helps
    nobody.
    """
    by_id: dict[int, _Placement] = {}
    order: list[int] = []

    for channel in existing_channels:
        channel_id = channel.get("id")
        if channel_id is None:
            continue
        by_id[channel_id] = _Placement(
            channel_id=channel_id,
            name=channel.get("name") or f"Channel {channel_id}",
            number=channel.get("channel_number"),
            base_number=channel.get("channel_number"),
            is_new=False,
        )
        order.append(channel_id)

    def set_number(placement: _Placement, value, index: int, op: Any) -> None:
        placement.number = value
        placement.touched_by.append(index)
        # REPLACES, never accumulates -- see ``_Placement.acknowledgement``.
        acknowledged = _acknowledgement(op)
        if acknowledged is None:
            placement.acknowledgement = None
            return
        occupants = getattr(acknowledged, "occupantChannelIds", None) or []
        placement.acknowledgement = _Acknowledgement(
            slot=_slot_key(acknowledged.number),
            occupant_ids=frozenset(
                cid for cid in occupants if isinstance(cid, int) and not isinstance(cid, bool)
            ),
        )

    for index, op in enumerate(operations):
        op_type = getattr(op, "type", None)
        if op_type == "updateChannel":
            placement = by_id.get(op.channelId)
            if placement is None:
                continue
            data = op.data if isinstance(op.data, dict) else {}
            if "name" in data and isinstance(data["name"], str):
                placement.name = data["name"]
            if "channel_number" in data:
                set_number(placement, data["channel_number"], index, op)
        elif op_type == "createChannel":
            temp_id = op.tempId
            placement = _Placement(
                channel_id=temp_id,
                name=getattr(op, "name", None) or f"Channel {temp_id}",
                number=None,
                base_number=None,
                is_new=True,
            )
            by_id[temp_id] = placement
            order.append(temp_id)
            set_number(placement, getattr(op, "channelNumber", None), index, op)
        elif op_type == "deleteChannel":
            if by_id.pop(op.channelId, None) is not None:
                order = [cid for cid in order if cid != op.channelId]
        elif op_type == "bulkAssignChannelNumbers":
            start = getattr(op, "startingNumber", None)
            start = 1 if start is None else start
            for offset, channel_id in enumerate(op.channelIds):
                placement = by_id.get(channel_id)
                if placement is None:
                    continue
                set_number(placement, start + offset, index, op)

    return FinalNumberingState(placements=[by_id[cid] for cid in order if cid in by_id])


def _describe(placements: Sequence[_Placement]) -> str:
    return ", ".join(f'"{placement.name}"' for placement in placements)


def evaluate_final_numbering(
    existing_channels: Iterable[dict],
    operations: Sequence[Any],
) -> list[NumberingIssue]:
    """What is wrong with the numbering these operations propose, if anything.

    Two properties, both checked over the final state rather than over any one
    operation, so no combination can slip between them:

    * Every number this request PUTS a channel on is in contract.
    * No number this request puts a channel on is shared, unless every channel
      this request put there was put there deliberately.
    """
    state = build_final_numbering_state(existing_channels, operations)

    issues: list[NumberingIssue] = []
    by_slot: dict[str, list[_Placement]] = {}

    for placement in state.placements:
        if placement.number is None:
            continue
        placed = placement.moved and bool(placement.touched_by)
        if placed and not is_valid_channel_number(placement.number):
            issues.append(
                NumberingIssue(
                    type="invalid_channel_number",
                    severity="error",
                    message=(
                        f'"{placement.name}" would end up on channel number '
                        f"{placement.number}, which is not a valid channel number. "
                        f"{CHANNEL_NUMBER_RULE_MESSAGE}"
                    ),
                    channel_number=placement.number,
                    channel_ids=[placement.channel_id],
                    operation_indexes=list(placement.touched_by),
                )
            )
            continue
        if not isinstance(placement.number, (int, float)) or isinstance(placement.number, bool):
            continue
        if not math.isfinite(float(placement.number)):
            continue
        by_slot.setdefault(_slot_key(placement.number), []).append(placement)

    for slot, occupants in by_slot.items():
        if len(occupants) < 2:
            continue
        contributors = [p for p in occupants if p.moved and p.touched_by]
        # Nobody moved onto this number here, so the duplicate is the lineup's,
        # not this request's. ic884.1 declined to enforce uniqueness.
        if not contributors:
            continue

        # WHO WAS ALREADY THERE WHEN EACH NEWCOMER ARRIVED. An acknowledgement
        # is consent to one collision -- this channel, this number, THESE
        # occupants -- so it authorises a newcomer only if it named everyone
        # the newcomer landed on top of. Walked in request order, adding each
        # newcomer as it goes, because that reproduces what the caller was
        # shown: the second channel to join a pile-up was warned about the
        # first. A confirmation carrying only a NUMBER kept authorising after
        # its occupant moved away and a stranger took the slot, which is a
        # collision nobody ever saw.
        #
        # SUBSET, NOT EQUALITY, and deliberately so. The acknowledgement names
        # what the operator was SHOWN; ``standing`` is what actually
        # materialised. ``standing <= shown`` refuses the dangerous direction —
        # consent to {X} while {X, A} stand — and accepts the harmless one,
        # where a collision the operator agreed to got smaller before Apply.
        # Requiring equality would re-interrogate an operator whose situation
        # strictly improved, and would refuse a placement whose own dialog
        # named a channel that a later staging order puts AFTER it.
        #
        # A placement that landed on an EMPTY slot consents to nothing because
        # there was nothing to consent to. Without that, an operator who moved
        # B onto a free number and then moved A on top of it — confirming the
        # only collision that ever existed — was refused, and told to confirm a
        # duplicate at a place where no dialog had ever fired. There is no
        # weakening here: the second arrival still has to have named B.
        standing = {p.channel_id for p in occupants if not (p.moved and p.touched_by)}
        accidental = []
        for placement in sorted(contributors, key=lambda p: p.touched_by[-1]):
            acknowledgement = placement.acknowledgement
            consented = not standing or (
                acknowledgement is not None
                and acknowledgement.slot == slot
                and standing <= acknowledgement.occupant_ids
            )
            if not consented:
                accidental.append(placement)
            standing.add(placement.channel_id)
        if not accidental:
            continue

        operation_indexes = sorted({idx for p in accidental for idx in p.touched_by})
        others = [p for p in occupants if p not in accidental]
        number = accidental[0].number
        issues.append(
            NumberingIssue(
                type="duplicate_channel_number",
                severity="error",
                message=(
                    f"Channel number {number} would be used by {len(occupants)} channels: "
                    f"{_describe(accidental)} would join {_describe(others)}. "
                    "Change one of them, or confirm the duplicate where it was staged."
                ),
                channel_number=number,
                channel_ids=[p.channel_id for p in occupants],
                operation_indexes=operation_indexes,
            )
        )

    return issues
