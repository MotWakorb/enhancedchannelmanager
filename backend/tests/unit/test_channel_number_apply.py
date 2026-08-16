"""The order numbering writes go in, and what putting them back looks like.

Bead ``enhancedchannelmanager-ic884.3``. Properties, not reproductions: the
swap in the bead description is ONE instance of "a write wants the slot another
write is vacating", and the tests below are written against that property at
its boundaries — chains, three-cycles, the ``2**53`` regime change, tenths,
``None``, and a failure injected at the first, middle and last position.
"""

import itertools

import pytest

from channel_number_apply import (
    NumberingCompensator,
    NumberingWrite,
    _slot_key,
    order_numbering_writes,
)


def w(channel_id: int, before, after, name: str | None = None) -> NumberingWrite:
    return NumberingWrite(
        channel_id=channel_id,
        name=name or f"Channel {channel_id}",
        before=before,
        after=after,
    )


def ids(order) -> list[int]:
    return [write.channel_id for write in order.writes]


# -- The invariant, stated once and checked on every shape -----------------
#
# The reproductions below are EXAMPLES of it, not the specification. Stated as
# a property because the last round's fix held for the shape it was written
# against and not for the shape beside it: a chain feeding a cycle was broken
# at the chain's head, which is not a member of the cycle it claimed to break.


def _positions_in_order(writes, order) -> list[int]:
    """The submitted position of each emitted write, in emitted order.

    Two equal writes are interchangeable — identical ``before`` and ``after``
    means identical blockers — so consuming a pool per distinct write is exact
    rather than approximate.
    """
    pools: dict[NumberingWrite, list[int]] = {}
    for position, write in enumerate(writes):
        pools.setdefault(write, []).append(position)
    return [pools[write].pop(0) for write in order.writes]


def _premature_entries(writes, order) -> list[int]:
    """Positions that entered a slot a planned occupant had not yet left.

    Exactly the writes the ordering is allowed to make only as a deliberate
    cycle break: one per cycle, and each a member of the cycle it breaks.
    """
    leaving: dict[str, set[int]] = {}
    for position, write in enumerate(writes):
        if not write.moves:
            continue
        slot = _slot_key(write.before)
        if slot is None:
            continue
        leaving.setdefault(slot, set()).add(position)

    emitted: set[int] = set()
    premature: list[int] = []
    for position in _positions_in_order(writes, order):
        slot = _slot_key(writes[position].after)
        if slot is not None:
            still_holding = {
                holder
                for holder in leaving.get(slot, ())
                if holder != position and holder not in emitted
            }
            if still_holding:
                premature.append(position)
        emitted.add(position)
    return premature


def assert_ordering_invariant(writes) -> "object":
    """Order ``writes`` and assert every property the ordering promises.

    1. Every submitted write is emitted exactly once (termination plus
       completeness — the loop cannot stall and cannot duplicate).
    2. No write enters a slot before its planned occupant has left, EXCEPT one
       deliberate cycle-breaking write per reported cycle.
    3. Every one of those breaking writes is a MEMBER of the cycle it breaks.
    """
    order = order_numbering_writes(writes)

    assert sorted(_positions_in_order(writes, order)) == list(range(len(writes)))

    premature = _premature_entries(writes, order)
    assert len(premature) == len(order.cycles), (
        f"{len(premature)} write(s) entered an occupied slot but "
        f"{len(order.cycles)} cycle(s) were reported: "
        f"premature={[writes[p].channel_id for p in premature]}, "
        f"cycles={order.cycles}"
    )

    # One break per cycle, and each break inside the cycle it breaks. Matched
    # one-to-one so a break that happens to share a channel id with an
    # unrelated cycle cannot stand in for the real member.
    unmatched = list(order.cycles)
    for position in premature:
        channel_id = writes[position].channel_id
        for cycle in unmatched:
            if channel_id in cycle:
                unmatched.remove(cycle)
                break
        else:
            raise AssertionError(
                f"channel {channel_id} was written onto an occupied number but "
                f"belongs to none of the reported cycles {order.cycles}"
            )
    assert unmatched == []
    return order


class TestOrderNumberingWrites:
    """A safe order exists whenever one exists, and every write is emitted once."""

    def test_empty_plan_orders_to_nothing(self):
        order = order_numbering_writes([])
        assert order.writes == ()
        assert order.cycles == ()

    def test_a_move_onto_a_free_number_keeps_its_place(self):
        order = order_numbering_writes([w(1, 5, 9), w(2, 6, 10)])
        assert ids(order) == [1, 2]
        assert order.cycles == ()

    def test_a_chain_runs_from_the_far_end_first(self):
        # 1 wants 6 (2's number), 2 wants 7 (3's number), 3 wants 8 (free).
        # Nobody may move onto a number still occupied by a channel that has
        # not moved yet, so the only safe order is 3, 2, 1.
        order = order_numbering_writes([w(1, 5, 6), w(2, 6, 7), w(3, 7, 8)])
        assert ids(order) == [3, 2, 1]
        assert order.cycles == ()

    def test_a_two_channel_swap_reports_one_unavoidable_cycle(self):
        order = order_numbering_writes([w(1, 5, 6), w(2, 6, 5)])
        assert sorted(ids(order)) == [1, 2]
        assert order.cycles == ((1, 2),)

    def test_a_three_channel_cycle_is_reported_as_one_cycle(self):
        order = order_numbering_writes([w(1, 5, 6), w(2, 6, 7), w(3, 7, 5)])
        assert sorted(ids(order)) == [1, 2, 3]
        assert order.cycles == ((1, 2, 3),)

    def test_a_cycle_and_a_chain_together_keep_the_chain_safe(self):
        # 10 -> 11 -> 12 is a chain onto a free 13; 1 <-> 2 is a swap.
        writes = [w(1, 5, 6), w(2, 6, 5), w(10, 11, 12), w(11, 12, 13)]
        order = order_numbering_writes(writes)
        assert sorted(ids(order)) == [1, 2, 10, 11]
        assert order.cycles == ((1, 2),)
        emitted = ids(order)
        assert emitted.index(11) < emitted.index(10)

    def test_canonical_equivalents_are_one_slot(self):
        # 7 and 7.0 are the same number, so channel 2 is waiting on channel 1.
        order = order_numbering_writes([w(1, 7.0, 8), w(2, 4, 7)])
        assert ids(order) == [1, 2]

    def test_tenths_are_distinct_slots(self):
        # 7.1 is not 7, so neither write waits on the other.
        order = order_numbering_writes([w(1, 7.0, 8), w(2, 4, 7.1)])
        assert ids(order) == [1, 2]
        assert order.cycles == ()

    def test_clearing_a_number_frees_its_slot(self):
        order = order_numbering_writes([w(1, 5, None), w(2, 9, 5)])
        assert ids(order) == [1, 2]

    def test_a_write_from_nothing_waits_on_nobody(self):
        order = order_numbering_writes([w(1, None, 5)])
        assert ids(order) == [1]
        assert order.cycles == ()

    def test_above_the_exact_integer_floor_ordering_still_terminates(self):
        floor = 2.0**53
        order = order_numbering_writes(
            [w(1, floor, floor + 2), w(2, floor + 2, floor)]
        )
        assert sorted(ids(order)) == [1, 2]
        assert order.cycles == ((1, 2),)

    def test_the_two_magnitude_regimes_never_share_a_slot(self):
        # A tick index below the floor must never be read as a float identity
        # at or above it.
        order = order_numbering_writes([w(1, 2.0**53, 4), w(2, 9, 2.0**53)])
        assert ids(order) == [1, 2]

    def test_a_write_that_does_not_move_is_still_emitted_once(self):
        order = order_numbering_writes([w(1, 5, 5), w(2, 6, 7)])
        assert sorted(ids(order)) == [1, 2]

    @pytest.mark.parametrize("size", [2, 3, 8, 40])
    def test_every_write_is_emitted_exactly_once_for_a_full_rotation(self, size):
        writes = [w(i, i, (i % size) + 1) for i in range(1, size + 1)]
        order = order_numbering_writes(writes)
        assert sorted(ids(order)) == list(range(1, size + 1))

    def test_duplicate_occupants_of_one_slot_do_not_stall_the_order(self):
        # The lineup already had two channels on 6 (ic884.1 declined to enforce
        # uniqueness). Both vacate it; a third wants it.
        writes = [w(1, 6, 20), w(2, 6, 21), w(3, 4, 6)]
        order = order_numbering_writes(writes)
        emitted = ids(order)
        assert sorted(emitted) == [1, 2, 3]
        assert emitted.index(3) == 2


class TestTheCycleBrokenIsTheCycleFound:
    """A cycle is broken at one of ITS OWN members, on every plan shape.

    Fix round 2. The break used to be applied to ``remaining[0]`` — the write
    the cycle WALK started from — which is a member of the cycle only when the
    walk starts inside it. Feed a chain into a cycle and the chain's head was
    released instead: it moved onto a number its planned occupant had not left
    (the one thing this ordering exists to prevent), the cycle survived
    untouched, and a second forced release was needed to break it.

    Every test here asserts the property through
    :func:`assert_ordering_invariant` and then, where the answer is
    interesting, the exact order as well.
    """

    def test_a_chain_feeding_a_cycle_breaks_the_cycle_not_the_chain(self):
        """The confirmed reproduction, and an example of the invariant.

        A wants 2, which B is leaving; B wants 3, which C is leaving; C wants
        2, which B is leaving. The cycle is B<->C and A is a chain into it.
        """
        a, b, c = w(1, 1, 2), w(2, 2, 3), w(3, 3, 2)
        order = assert_ordering_invariant([a, b, c])
        assert order.cycles == ((2, 3),)
        # B is the earliest-submitted member of the cycle, so B is the break —
        # B moves onto 3 while C is still there, which is the one transient
        # share this cycle costs. B leaving 2 then frees A and C together, and
        # the submitted order breaks that tie. Before the fix the break was A,
        # which put A on 2 while B was still on it and left B<->C to be broken
        # a second time.
        assert ids(order) == [2, 1, 3]
        assert order.writes[0].channel_id == 2

    def test_a_cycle_waiting_on_a_chain_lets_the_chain_go_first(self):
        """The other direction: a CYCLE member waits on a write outside it.

        Two channels sit on 200 (ic884.1 declined to enforce uniqueness). One
        of them, 30, is going to 900 and blocks nothing; the other, 20, is in a
        swap with 10. So 10 waits on both 20 and 30, and only 30 is free to go.
        """
        writes = [w(10, 100, 200), w(20, 200, 100), w(30, 200, 900)]
        order = assert_ordering_invariant(writes)
        assert order.cycles == ((10, 20),)
        emitted = ids(order)
        assert emitted[0] == 30
        assert sorted(emitted) == [10, 20, 30]

    def test_two_chains_converging_on_one_cycle_still_break_inside_it(self):
        """Both chain heads are submitted BEFORE either cycle member, so the
        walk starts outside the cycle from a position that is not even adjacent
        to it."""
        writes = [w(1, 10, 2), w(2, 11, 3), w(3, 2, 3), w(4, 3, 2)]
        order = assert_ordering_invariant(writes)
        assert order.cycles == ((3, 4),)
        assert ids(order)[0] == 3

    def test_multiple_disjoint_cycles_are_each_broken_once(self):
        writes = [w(1, 1, 2), w(2, 2, 1), w(3, 3, 4), w(4, 4, 3)]
        order = assert_ordering_invariant(writes)
        assert order.cycles == ((1, 2), (3, 4))

    def test_a_chain_into_each_of_two_disjoint_cycles(self):
        writes = [
            w(1, 100, 2),   # chain into the 2<->1 cycle
            w(2, 1, 2.5),   # not part of anything: 2.5 is free
            w(3, 2, 1),     # cycle with 4
            w(4, 1, 2),     # cycle with 3
            w(5, 200, 30),  # chain into the 30<->31 cycle
            w(6, 30, 31),
            w(7, 31, 30),
        ]
        order = assert_ordering_invariant(writes)
        assert order.cycles == ((3, 4), (6, 7))

    def test_a_non_moving_write_is_not_an_occupant_anybody_waits_for(self):
        """A self-edge — a write whose ``before`` and ``after`` are one slot —
        never leaves that slot, so waiting for it would wait forever. The
        duplicate it produces is the numbering preflight's business, not the
        ordering's."""
        writes = [w(1, 5, 5), w(2, 9, 5)]
        order = assert_ordering_invariant(writes)
        assert order.cycles == ()
        assert ids(order) == [1, 2]

    def test_a_canonical_self_edge_is_still_not_a_cycle(self):
        # 7 and 7.0 are one slot, so this write moves nothing.
        writes = [w(1, 7, 7.0), w(2, 4, 8)]
        order = assert_ordering_invariant(writes)
        assert order.cycles == ()

    def test_one_channel_written_twice_orders_its_own_two_writes(self):
        """The graph is over WRITES, not channels: the same channel may appear
        in two operations, and the second write's slot may be the first's."""
        writes = [w(1, 5, 6), w(1, 6, 7)]
        order = assert_ordering_invariant(writes)
        assert order.cycles == ()
        assert [(write.before, write.after) for write in order.writes] == [
            (6, 7),
            (5, 6),
        ]

    def test_one_channel_whose_two_writes_form_a_cycle_still_terminates(self):
        writes = [w(1, 5, 6), w(1, 6, 5)]
        order = assert_ordering_invariant(writes)
        assert order.cycles == ((1, 1),)

    def test_the_shapes_the_earlier_tests_pin_also_satisfy_the_invariant(self):
        """Every plan the reproduction-shaped tests above use, re-checked as a
        property rather than as an expected output."""
        for writes in (
            [w(1, 5, 9), w(2, 6, 10)],
            [w(1, 5, 6), w(2, 6, 7), w(3, 7, 8)],
            [w(1, 5, 6), w(2, 6, 5)],
            [w(1, 5, 6), w(2, 6, 7), w(3, 7, 5)],
            [w(1, 5, 6), w(2, 6, 5), w(10, 11, 12), w(11, 12, 13)],
            [w(1, 5, None), w(2, 9, 5)],
            [w(1, None, 5)],
            [w(1, 6, 20), w(2, 6, 21), w(3, 4, 6)],
            [w(1, 2.0**53, 2.0**53 + 2), w(2, 2.0**53 + 2, 2.0**53)],
        ):
            assert_ordering_invariant(writes)

    @pytest.mark.parametrize("size", [1, 2, 3, 4])
    def test_every_plan_over_n_channels_and_n_slots_holds_the_invariant(self, size):
        """EXHAUSTIVE over the shapes, which is what makes this a termination
        argument rather than an anecdote.

        ``size`` channels each starting on their own number and each ending on
        any of those numbers is every combination of chain, cycle, fixed point
        and convergence that fits in ``size`` slots — 1, 4, 27 and 256 plans.
        For each, every write is emitted exactly once (so the remaining set
        strictly shrank on every branch taken) and every premature entry is a
        member of a reported cycle.
        """
        numbers = list(range(1, size + 1))
        for combination in itertools.product(numbers, repeat=size):
            writes = [
                w(channel, channel, combination[channel - 1]) for channel in numbers
            ]
            assert_ordering_invariant(writes)

    def test_every_plan_with_duplicate_starting_numbers_holds_the_invariant(self):
        """The same sweep where two channels already share a number, which is
        the case ``leaving`` keeps a LIST for: a slot is free only once every
        one of its occupants has moved."""
        befores = [1, 1, 2, 2]
        for combination in itertools.product([1, 2, 3], repeat=4):
            writes = [
                w(channel + 1, befores[channel], combination[channel])
                for channel in range(4)
            ]
            assert_ordering_invariant(writes)


class TestNumberingCompensator:
    """A half-applied plan can be walked back, and says so when it cannot."""

    def test_a_clean_run_is_not_half_applied(self):
        comp = NumberingCompensator()
        comp.record_landed(channel_id=1, name="ESPN", before=5, after=6)
        comp.record_landed(channel_id=2, name="TNT", before=6, after=5)
        assert comp.half_applied is False
        assert comp.compensation_steps() == []

    def test_a_run_that_landed_nothing_has_nothing_to_compensate(self):
        comp = NumberingCompensator()
        comp.record_failed(channel_id=1, name="ESPN", intended=6)
        assert comp.half_applied is False
        assert comp.compensation_steps() == []

    def test_a_failure_after_a_landed_write_is_half_applied(self):
        comp = NumberingCompensator()
        comp.record_landed(channel_id=1, name="ESPN", before=5, after=6)
        comp.record_failed(channel_id=2, name="TNT", intended=5)
        assert comp.half_applied is True

    def test_compensation_replays_the_inverse_in_reverse_order(self):
        comp = NumberingCompensator()
        comp.record_landed(channel_id=1, name="A", before=5, after=6)
        comp.record_landed(channel_id=2, name="B", before=6, after=7)
        comp.record_failed(channel_id=3, name="C", intended=8)
        steps = comp.compensation_steps()
        assert [(s.channel_id, s.before, s.after) for s in steps] == [
            (2, 7, 6),
            (1, 6, 5),
        ]

    def test_a_channel_written_twice_is_restored_to_its_earliest_value(self):
        comp = NumberingCompensator()
        comp.record_landed(channel_id=1, name="A", before=5, after=6)
        comp.record_landed(channel_id=1, name="A", before=6, after=7)
        comp.record_failed(channel_id=2, name="B", intended=9)
        steps = comp.compensation_steps()
        assert [(s.channel_id, s.after) for s in steps] == [(1, 5)]

    def test_a_write_that_changed_nothing_needs_no_compensation(self):
        comp = NumberingCompensator()
        comp.record_landed(channel_id=1, name="A", before=5, after=5.0)
        comp.record_landed(channel_id=2, name="B", before=6, after=7)
        comp.record_failed(channel_id=3, name="C", intended=8)
        steps = comp.compensation_steps()
        assert [s.channel_id for s in steps] == [2]

    def test_restoring_an_unassigned_number_is_a_step_not_a_skip(self):
        comp = NumberingCompensator()
        comp.record_landed(channel_id=1, name="A", before=None, after=6)
        comp.record_failed(channel_id=2, name="B", intended=9)
        steps = comp.compensation_steps()
        assert [(s.channel_id, s.after) for s in steps] == [(1, None)]

    @pytest.mark.parametrize("failure_position", [0, 1, 2])
    def test_a_failure_at_any_position_compensates_only_what_landed(
        self, failure_position
    ):
        comp = NumberingCompensator()
        plan = [(1, 5, 6), (2, 6, 7), (3, 7, 8)]
        for position, (channel_id, before, after) in enumerate(plan):
            if position == failure_position:
                comp.record_failed(
                    channel_id=channel_id, name=f"C{channel_id}", intended=after
                )
                continue
            comp.record_landed(
                channel_id=channel_id, name=f"C{channel_id}", before=before, after=after
            )
        landed_ids = [
            channel_id
            for position, (channel_id, _, _) in enumerate(plan)
            if position != failure_position
        ]
        assert comp.half_applied is (len(landed_ids) > 0)
        assert [s.channel_id for s in comp.compensation_steps()] == list(
            reversed(landed_ids)
        )

    def test_recovery_steps_name_the_channel_and_the_exact_remaining_step(self):
        comp = NumberingCompensator()
        comp.record_landed(channel_id=12, name="ESPN", before=5, after=6)
        comp.record_failed(channel_id=13, name="TNT", intended=5)
        step = comp.compensation_steps()[0]
        report = comp.recovery_steps([(step, "boom")])
        assert len(report) == 1
        entry = report[0]
        assert entry["channelId"] == 12
        assert entry["channelName"] == "ESPN"
        assert entry["currentNumber"] == 6
        assert entry["targetNumber"] == 5
        assert "ESPN" in entry["step"]
        assert "5" in entry["step"]
        assert entry["error"] == "boom"

    def test_recovery_step_for_an_unassigned_target_says_clear(self):
        comp = NumberingCompensator()
        comp.record_landed(channel_id=12, name="ESPN", before=None, after=6)
        comp.record_failed(channel_id=13, name="TNT", intended=5)
        step = comp.compensation_steps()[0]
        entry = comp.recovery_steps([(step, "boom")])[0]
        assert entry["targetNumber"] is None
        assert "clear" in entry["step"].lower()

    def test_nothing_in_the_module_claims_a_guarantee_the_api_cannot_keep(self):
        """Invariant 4, pinned rather than trusted to review.

        Dispatcharr 0.28.x has no conditional update and no revision token
        (measured 2026-08-15 against ``GET /api/schema/?format=json``: zero
        occurrences of ``If-Match``, ``If-None-Match``, ``If-Unmodified-Since``,
        ``ETag`` or ``412``, and no version or modified-at field on ``Channel``
        or ``PatchedChannel``). Nothing here may describe itself with a word
        that promises all-or-nothing, because nothing here can deliver it.
        """
        import channel_number_apply

        texts = [channel_number_apply.__doc__ or ""]
        for module_member in vars(channel_number_apply).values():
            doc = getattr(module_member, "__doc__", None)
            if isinstance(doc, str):
                texts.append(doc)
            for attribute in vars(module_member).values() if isinstance(
                module_member, type
            ) else ():
                nested = getattr(attribute, "__doc__", None)
                if isinstance(nested, str):
                    texts.append(nested)
        joined = " ".join(texts).lower()
        assert "atomic" not in joined
        assert "rollback" not in joined
        assert "transactional" not in joined
