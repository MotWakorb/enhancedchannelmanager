"""The order channel-number writes go in, and how a half-finished run is put back.

Bead ``enhancedchannelmanager-ic884.3``.

WHAT THE UPSTREAM API CAN AND CANNOT DO, measured rather than assumed. The
live Dispatcharr 0.28.x schema (``GET /api/schema/?format=json``, HTTP 200,
~717KB, fetched 2026-08-15) contains ZERO occurrences of ``If-Match``,
``If-None-Match``, ``If-Unmodified-Since``, ``ETag`` or ``412``, and neither
``Channel`` nor ``PatchedChannel`` carries a version or modified-at field.
There is no conditional update and no revision token to compare, not even
client side. Every channel number is therefore changed by its own unguarded
``PATCH``, one channel at a time, and a run of them is a sequence of
independent writes with no all-or-nothing behaviour available at any layer.

So this module does two things that are possible, and claims nothing that is
not:

1. :func:`order_numbering_writes` chooses an order in which no channel is moved
   onto a number another channel in the same plan has not yet left. Where the
   plan contains a genuine cycle — the two-channel swap is the smallest one —
   no such order exists, and the cycle is REPORTED rather than papered over:
   breaking it puts two channels on one number for the duration of one write.

   That transient sharing is legal upstream. ``channel_number`` is declared as
   a NON-UNIQUE float, Dispatcharr's release notes state duplicates are
   permitted, and the same-group ``clean()`` on the model is never reached
   because DRF serializers do not call ``full_clean()`` (measured on bead
   ``enhancedchannelmanager-ic884.1``). Ordering therefore is not what makes a
   swap possible — a swap already works. What ordering buys is that every
   intermediate state the run passes through is one the operator would
   recognise, which is what makes the report in (2) readable when a run stops
   half way.

2. :class:`NumberingCompensator` records every numbering write that LANDED, so
   a run that then fails part way can write the earlier channels back to the
   numbers they held. This is repair after the fact, best effort:

   * a change another client makes between the failure and the repair is
     neither detected nor preserved, because there is nothing to detect it
     with;
   * a compensating write can itself fail, and then the only honest thing left
     is to say exactly which channel is sitting on which number and what the
     operator must do about it — :meth:`NumberingCompensator.recovery_steps`.

   Every compensating write is a landed upstream write like any other and goes
   through ``OperationLedger.record_write`` with its journal row, so it is
   counted and auditable on the same terms as the write it is undoing (see
   ``backend/bulk_commit_accounting.py``, rule 7).

Slot comparison is the canonical one ``channel_number.py`` defines and
``channel_number_plan.py`` uses, so ``7`` and ``7.0`` are one number, ``7.1``
is a different one, and the two magnitude regimes either side of ``2**53``
cannot be confused for each other.

Tests: ``backend/tests/unit/test_channel_number_apply.py`` and
``backend/tests/routers/test_ic884_3_numbering_compensation.py``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence

from channel_number import CHANNEL_NUMBER_TICKS_PER_UNIT, format_channel_number

__all__ = [
    "NumberingCompensator",
    "NumberingOrder",
    "NumberingWrite",
    "order_numbering_writes",
    "same_channel_number",
]


# Mirrors ``_EXACT_INTEGER_FLOOR`` in ``channel_number_plan.py`` and
# ``EXACT_INTEGER_FLOOR`` in ``frontend/src/utils/channelNumberPlan.ts``.
_EXACT_INTEGER_FLOOR = 2.0**53


def _slot_key(value: Any) -> Optional[str]:
    """The slot ``value`` occupies, or ``None`` for "no slot at all".

    ``None`` is unassigned, which is not a slot: two unnumbered channels do not
    share anything, so an unassigned value never matches, including another
    unassigned value. A string rather than a number so the two magnitude
    regimes cannot collide.
    """
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    if abs(number) >= _EXACT_INTEGER_FLOOR:
        return f"x{number!r}"
    return f"t{round(number * CHANNEL_NUMBER_TICKS_PER_UNIT)}"


def same_channel_number(a: Any, b: Any) -> bool:
    """Whether two channel numbers are the same number, canonically."""
    slot_a = _slot_key(a)
    return slot_a is not None and slot_a == _slot_key(b)


@dataclass(frozen=True)
class NumberingWrite:
    """One channel moving from one number to another.

    ``before`` is the number the channel held before the run began, and
    ``after`` the number the run puts it on. Either may be ``None`` — a channel
    with no number, or a number being cleared.
    """

    channel_id: int
    name: str
    before: Optional[float]
    after: Optional[float]

    @property
    def moves(self) -> bool:
        """Does this write change anything?"""
        if self.before is None and self.after is None:
            return False
        if self.before is None or self.after is None:
            return True
        return not same_channel_number(self.before, self.after)

    def inverse(self) -> "NumberingWrite":
        """The write that puts this one back."""
        return NumberingWrite(
            channel_id=self.channel_id,
            name=self.name,
            before=self.after,
            after=self.before,
        )


@dataclass(frozen=True)
class NumberingOrder:
    """An order to write a numbering plan in, and what could not be ordered."""

    #: Every submitted write, exactly once, in the order to send them.
    writes: tuple[NumberingWrite, ...]
    #: Channel ids of each cycle that had to be broken, ascending within a
    #: cycle and by first member between cycles. Each cycle costs one
    #: transient shared number for the duration of a single write.
    cycles: tuple[tuple[int, ...], ...]


def order_numbering_writes(
    writes: Sequence[NumberingWrite],
) -> NumberingOrder:
    """Order ``writes`` so a channel is moved onto a number only once its
    previous holder has left, and name the cycles where that is impossible.

    Terminates on every input by construction: each pass either emits at least
    one write whose dependencies are all satisfied, or, when none is available,
    walks out from the earliest remaining write to a cycle and emits the
    earliest-submitted MEMBER of that cycle. Both branches emit at least one
    write and neither can emit a write twice, so the remaining set strictly
    shrinks on every pass, the loop runs at most ``len(writes)`` times, and
    every write is emitted exactly once. The walk itself terminates because it
    only ever steps to a position that is still remaining and there are
    finitely many of those, so it must revisit one.

    The only writes that land on a number the plan has not vacated are the
    cycle breaks — one per cycle, each a member of the cycle it breaks.
    Enforced by ``assert_ordering_invariant`` in
    ``backend/tests/unit/test_channel_number_apply.py``, exhaustively over
    every plan shape that fits in four channels and four numbers.

    Ties are broken by the submitted order, so the result is deterministic and
    a plan with no dependencies at all comes back untouched.
    """
    pending = list(writes)
    if not pending:
        return NumberingOrder(writes=(), cycles=())

    # Which writes are LEAVING each slot. More than one is possible: the lineup
    # may already have had several channels on a number (ic884.1 declined to
    # enforce uniqueness), and every one of them has to move before the slot is
    # genuinely free.
    leaving: dict[str, list[int]] = {}
    for position, write in enumerate(pending):
        if not write.moves:
            continue
        slot = _slot_key(write.before)
        if slot is None:
            continue
        leaving.setdefault(slot, []).append(position)

    # ``blockers[w]`` are the positions that must be written before ``w``.
    blockers: dict[int, set[int]] = {position: set() for position in range(len(pending))}
    blocking: dict[int, set[int]] = {position: set() for position in range(len(pending))}
    for position, write in enumerate(pending):
        slot = _slot_key(write.after)
        if slot is None:
            continue
        for holder in leaving.get(slot, ()):
            if holder == position:
                continue
            blockers[position].add(holder)
            blocking[holder].add(position)

    remaining = list(range(len(pending)))
    ordered: list[NumberingWrite] = []
    cycles: list[tuple[int, ...]] = []

    def release(position: int) -> None:
        for dependent in blocking[position]:
            blockers[dependent].discard(position)
        remaining.remove(position)
        ordered.append(pending[position])

    while remaining:
        ready = [position for position in remaining if not blockers[position]]
        if ready:
            for position in ready:
                release(position)
            continue

        # Nothing is ready, so every remaining write is waiting on another one.
        # Walk from the earliest remaining write until the walk repeats a
        # position: the repeat is where the cycle starts, and everything the
        # walk passed through BEFORE it is a chain leading into the cycle
        # rather than part of it.
        #
        # THE BREAK GOES ON A MEMBER OF THE CYCLE THE WALK FOUND, never on the
        # write the walk started from. Releasing a chain node instead leaves
        # the cycle whole — so a second forced release is still needed to break
        # it — and moves that node onto a number the write that was going to
        # vacate it has not vacated, which is the one thing this ordering
        # exists to prevent. The earliest-submitted member is the break, which
        # is the start itself whenever the start is in the cycle, so a plan
        # that is nothing but a cycle is ordered exactly as before.
        seen: list[int] = []
        cursor = remaining[0]
        while cursor not in seen:
            seen.append(cursor)
            # Any blocker still remaining continues the walk; the lowest keeps
            # it deterministic. ``blockers`` never names a released position —
            # ``release`` discards it from every dependent — so on this branch,
            # where no remaining position is free of blockers, this is always
            # non-empty and the walk cannot leave ``remaining``.
            cursor = min(blockers[cursor] & set(remaining))
        cycle = seen[seen.index(cursor):]
        cycles.append(tuple(sorted(pending[position].channel_id for position in cycle)))
        release(min(cycle))

    cycles.sort()
    return NumberingOrder(writes=tuple(ordered), cycles=tuple(cycles))


class NumberingCompensator:
    """Every channel number a run has actually changed, and how to put it back.

    NOT A TRANSACTION, and it cannot be made one: see the module docstring for
    the measured absence of any conditional update in Dispatcharr 0.28.x. What
    it offers is a compensating write per landed change, replayed newest first,
    and — when one of those writes fails too — an exact, deterministic
    instruction per channel that is still in the wrong place.

    The two facts it needs are recorded at the two moments they are known:
    :meth:`record_landed` the instant an upstream numbering write returns, and
    :meth:`record_failed` when one does not. It is the pairing of the two that
    makes :attr:`half_applied` true, which is the only condition under which
    compensating is the right thing to do — a run in which every numbering
    write landed has nothing to put back, and a run in which none did has
    nothing to put back either.
    """

    __slots__ = ("_landed", "_failed", "_first_before")

    def __init__(self) -> None:
        #: Landed numbering writes in the order they landed.
        self._landed: list[NumberingWrite] = []
        #: Numbering writes the run intended and did not land.
        self._failed: list[NumberingWrite] = []
        #: The number each channel held BEFORE this run touched it. Recorded
        #: once, because the second write's ``before`` is this run's own doing
        #: and restoring to it would leave the operator's lineup changed.
        self._first_before: dict[int, Optional[float]] = {}

    # -- recording ---------------------------------------------------------

    def record_landed(
        self,
        *,
        channel_id: int,
        name: str,
        before: Optional[float],
        after: Optional[float],
    ) -> None:
        """An upstream numbering write returned. Note what it changed."""
        if channel_id not in self._first_before:
            self._first_before[channel_id] = before
        self._landed.append(
            NumberingWrite(
                channel_id=channel_id, name=name, before=before, after=after
            )
        )

    def record_failed(
        self, *, channel_id: int, name: str, intended: Optional[float]
    ) -> None:
        """A numbering write this run meant to make did not land."""
        self._failed.append(
            NumberingWrite(
                channel_id=channel_id,
                name=name,
                before=self._first_before.get(channel_id),
                after=intended,
            )
        )

    # -- reading -----------------------------------------------------------

    @property
    def landed(self) -> tuple[NumberingWrite, ...]:
        return tuple(self._landed)

    @property
    def failed(self) -> tuple[NumberingWrite, ...]:
        return tuple(self._failed)

    @property
    def half_applied(self) -> bool:
        """Did part of this run's numbering land while part of it did not?"""
        return bool(self._landed) and bool(self._failed)

    def compensation_steps(self) -> list[NumberingWrite]:
        """The writes that put the landed changes back, newest change first.

        One per channel, whatever the run wrote to it — a channel moved twice
        is restored to the number it held BEFORE the run, never to the
        intermediate value this run itself put there. A write that changed
        nothing contributes no step: sending a PATCH that restores the value
        already in place would add a failure surface and record a change that
        never happened.

        Empty unless :attr:`half_applied`. A run whose numbering all landed is
        the run the operator asked for.

        NEWEST FIRST IS NOT ARBITRARY. What landed is a PREFIX of the order
        :func:`order_numbering_writes` chose, in which no channel was moved onto
        a number another write in the plan had not yet vacated. Walking that
        prefix backwards is therefore an order in which no channel is moved back
        onto a number another compensating write has not yet vacated: the safe
        order, run in reverse, is a safe order. Restoring in landing order would
        put channels back on numbers their neighbours had not yet left.
        """
        if not self.half_applied:
            return []
        steps: list[NumberingWrite] = []
        seen: set[int] = set()
        for write in reversed(self._landed):
            if write.channel_id in seen:
                continue
            seen.add(write.channel_id)
            restore = NumberingWrite(
                channel_id=write.channel_id,
                name=write.name,
                before=write.after,
                after=self._first_before.get(write.channel_id),
            )
            if not restore.moves:
                continue
            steps.append(restore)
        return steps

    def recovery_steps(
        self, unrepaired: Iterable[tuple[NumberingWrite, Any]]
    ) -> list[dict]:
        """The exact remaining step per channel the repair could not finish.

        ``unrepaired`` pairs a step from :meth:`compensation_steps` with why it
        did not go through. The result is operator-facing and deliberately
        prescriptive: it names the channel, the number it is sitting on now,
        the number it should be on, and the one action that closes the gap. An
        operator reading it has everything needed to finish the job by hand,
        which is the whole substitute for the guarantee that does not exist.
        """
        report: list[dict] = []
        for step, error in unrepaired:
            current = format_channel_number(step.before)
            if step.after is None:
                instruction = (
                    f'Clear the channel number on "{step.name}" (channel '
                    f"{step.channel_id}), which this run left on {current}; it "
                    "had no number before."
                )
            else:
                target = format_channel_number(step.after)
                instruction = (
                    f'Set "{step.name}" (channel {step.channel_id}) back to '
                    f"channel number {target}; this run left it on {current}."
                )
            report.append(
                {
                    "channelId": step.channel_id,
                    "channelName": step.name,
                    "currentNumber": step.before,
                    "targetNumber": step.after,
                    "step": instruction,
                    "error": str(error),
                }
            )
        return report
