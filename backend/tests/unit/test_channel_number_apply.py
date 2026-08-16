"""The order numbering writes go in, and what putting them back looks like.

Bead ``enhancedchannelmanager-ic884.3``. Properties, not reproductions: the
swap in the bead description is ONE instance of "a write wants the slot another
write is vacating", and the tests below are written against that property at
its boundaries — chains, three-cycles, the ``2**53`` regime change, tenths,
``None``, and a failure injected at the first, middle and last position.
"""

import pytest

from channel_number_apply import (
    NumberingCompensator,
    NumberingWrite,
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
