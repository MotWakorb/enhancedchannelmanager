"""The final channel-number state a bulk commit proposes, and what is wrong with it.

Bead ``enhancedchannelmanager-ic884.2``. The property under test is about the
COMBINED result of every staged operation, not about any one of them: each
operation in the collision cases below is individually legal against the
lineup the operator is looking at, and only the whole plan puts two channels on
one number.

Two things this must never do, both settled by bead
``enhancedchannelmanager-ic884.1``:

* report a duplicate the lineup already had. Dispatcharr declares
  ``channel_number`` as a non-unique float and permits duplicates; uniqueness
  is deliberately not enforced.
* report a duplicate the operator confirmed. That acknowledgement travels on
  the operation.
"""

from __future__ import annotations

import pytest

from channel_number_plan import (
    NumberingIssue,
    build_final_numbering_state,
    evaluate_final_numbering,
)


class Op:
    """Minimal stand-in for a parsed bulk operation.

    The planner reads attributes off whatever it is handed, exactly as the
    router hands it Pydantic models, so a plain object is a faithful double.
    """

    def __init__(self, type: str, **fields):
        self.type = type
        for key, value in fields.items():
            setattr(self, key, value)

    def __getattr__(self, name):  # pragma: no cover - absent optional fields
        return None


class Ack:
    """Stand-in for :class:`routers.channels.AcknowledgedDuplicate`."""

    def __init__(self, number, occupantChannelIds):
        self.number = number
        self.occupantChannelIds = list(occupantChannelIds)


def channel(cid: int, name: str, number):
    return {"id": cid, "name": name, "channel_number": number}


LINEUP = [channel(1, "ESPN", 5), channel(2, "TNT", 6), channel(3, "AMC", 7)]


def messages(issues: list[NumberingIssue]) -> str:
    return " ".join(issue.message for issue in issues)


class TestBuildFinalNumberingState:
    def test_reflects_a_staged_update(self):
        state = build_final_numbering_state(
            LINEUP, [Op("updateChannel", channelId=2, data={"channel_number": 9})]
        )
        assert state.number_of(2) == 9

    def test_includes_a_staged_create(self):
        state = build_final_numbering_state(
            LINEUP, [Op("createChannel", tempId=-1, name="New", channelNumber=50)]
        )
        assert state.number_of(-1) == 50

    def test_drops_a_staged_delete(self):
        state = build_final_numbering_state(LINEUP, [Op("deleteChannel", channelId=1)])
        assert state.number_of(1) is None
        assert 1 not in state.channel_ids()

    def test_expands_a_bulk_range(self):
        state = build_final_numbering_state(
            LINEUP,
            [Op("bulkAssignChannelNumbers", channelIds=[1, 2, 3], startingNumber=10)],
        )
        assert [state.number_of(i) for i in (1, 2, 3)] == [10, 11, 12]

    def test_later_operations_win(self):
        state = build_final_numbering_state(
            LINEUP,
            [
                Op("updateChannel", channelId=1, data={"channel_number": 20}),
                Op("updateChannel", channelId=1, data={"channel_number": 30}),
            ],
        )
        assert state.number_of(1) == 30

    def test_an_operation_naming_an_unknown_channel_is_ignored(self):
        state = build_final_numbering_state(
            LINEUP, [Op("updateChannel", channelId=999, data={"channel_number": 1})]
        )
        assert 999 not in state.channel_ids()


class TestEvaluateFinalNumbering:
    def test_a_clean_plan_passes(self):
        issues = evaluate_final_numbering(
            LINEUP,
            [
                Op("updateChannel", channelId=1, data={"channel_number": 100}),
                Op("updateChannel", channelId=2, data={"channel_number": 5}),
            ],
        )
        assert issues == []

    def test_blocks_a_collision_only_the_combination_creates(self):
        issues = evaluate_final_numbering(
            LINEUP,
            [
                Op("updateChannel", channelId=1, data={"channel_number": 100}),
                Op("updateChannel", channelId=2, data={"channel_number": 5}),
                Op("updateChannel", channelId=3, data={"channel_number": 5}),
            ],
        )
        assert len(issues) == 1
        assert issues[0].type == "duplicate_channel_number"
        assert sorted(issues[0].channel_ids) == [2, 3]

    def test_a_valid_swap_passes(self):
        issues = evaluate_final_numbering(
            LINEUP,
            [
                Op("updateChannel", channelId=1, data={"channel_number": 6}),
                Op("updateChannel", channelId=2, data={"channel_number": 5}),
            ],
        )
        assert issues == []

    def test_a_vacated_number_may_be_reused(self):
        issues = evaluate_final_numbering(
            LINEUP,
            [
                Op("deleteChannel", channelId=1),
                Op("updateChannel", channelId=2, data={"channel_number": 5}),
            ],
        )
        assert issues == []

    def test_a_channel_moved_away_causes_no_false_conflict(self):
        issues = evaluate_final_numbering(
            LINEUP,
            [
                Op("updateChannel", channelId=1, data={"channel_number": 999}),
                Op("createChannel", tempId=-1, name="Replacement", channelNumber=5),
            ],
        )
        assert issues == []

    def test_leaves_a_pre_existing_duplicate_alone(self):
        lineup = [channel(1, "ESPN", 5), channel(2, "ESPN HD", 5), channel(3, "AMC", 7)]
        issues = evaluate_final_numbering(
            lineup, [Op("updateChannel", channelId=3, data={"name": "AMC HD"})]
        )
        assert issues == []

    def test_re_asserting_a_channels_own_number_is_not_a_placement(self):
        lineup = [channel(1, "ESPN", 5), channel(2, "ESPN HD", 5)]
        issues = evaluate_final_numbering(
            lineup, [Op("updateChannel", channelId=1, data={"channel_number": 5.0})]
        )
        assert issues == []

    def test_accepts_an_acknowledged_duplicate(self):
        issues = evaluate_final_numbering(
            LINEUP,
            [
                Op(
                    "updateChannel",
                    channelId=2,
                    data={"channel_number": 5},
                    acknowledgedDuplicate=Ack(5, [1]),
                )
            ],
        )
        assert issues == []

    def test_an_acknowledgement_holds_at_any_canonical_spelling(self):
        issues = evaluate_final_numbering(
            LINEUP,
            [
                Op(
                    "updateChannel",
                    channelId=2,
                    data={"channel_number": 5},
                    acknowledgedDuplicate=Ack(5.0, [1]),
                )
            ],
        )
        assert issues == []

    def test_an_acknowledgement_of_a_different_number_does_not_carry(self):
        issues = evaluate_final_numbering(
            LINEUP,
            [
                Op(
                    "updateChannel",
                    channelId=2,
                    data={"channel_number": 5},
                    acknowledgedDuplicate=Ack(6, [1]),
                )
            ],
        )
        assert len(issues) == 1

    def test_an_unacknowledged_operation_joining_an_acknowledged_duplicate_still_blocks(self):
        issues = evaluate_final_numbering(
            LINEUP,
            [
                Op(
                    "updateChannel",
                    channelId=2,
                    data={"channel_number": 5},
                    acknowledgedDuplicate=Ack(5, [1]),
                ),
                Op("updateChannel", channelId=3, data={"channel_number": 5}),
            ],
        )
        assert len(issues) == 1
        # Only the operation nobody agreed to is named.
        assert issues[0].operation_indexes == [1]

    def test_an_acknowledged_create_is_accepted(self):
        issues = evaluate_final_numbering(
            LINEUP,
            [
                Op(
                    "createChannel",
                    tempId=-1,
                    name="Second ESPN",
                    channelNumber=5,
                    acknowledgedDuplicate=Ack(5, [1]),
                )
            ],
        )
        assert issues == []

    # ------------------------------------------------------------- binding
    #
    # Fix round 2 of bead enhancedchannelmanager-vdxbx. An acknowledgement
    # authorises exactly one collision: this channel, this number, this set of
    # occupants. It is not accumulated over a channel's history and it does not
    # outlive the occupants it named. The frontend planner holds the identical
    # four properties (``channelNumberPlan.test.ts``); the two must agree.

    def test_an_earlier_acknowledgement_does_not_authorise_a_later_placement(self):
        issues = evaluate_final_numbering(
            LINEUP,
            [
                Op(
                    "updateChannel",
                    channelId=2,
                    data={"channel_number": 5},
                    acknowledgedDuplicate=Ack(5, [1]),
                ),
                Op("updateChannel", channelId=2, data={"channel_number": 6}),
                Op("updateChannel", channelId=2, data={"channel_number": 5}),
            ],
        )
        assert len(issues) == 1
        assert issues[0].type == "duplicate_channel_number"

    def test_an_acknowledgement_survives_a_later_edit_that_does_not_replace_it(self):
        # The control: only a re-PLACEMENT withdraws consent.
        issues = evaluate_final_numbering(
            LINEUP,
            [
                Op(
                    "updateChannel",
                    channelId=2,
                    data={"channel_number": 5},
                    acknowledgedDuplicate=Ack(5, [1]),
                ),
                Op("updateChannel", channelId=2, data={"name": "TNT HD"}),
            ],
        )
        assert issues == []

    def test_an_acknowledgement_does_not_cover_occupants_it_never_named(self):
        lineup = [channel(3, "AMC", 5), channel(2, "TNT", 6)]
        issues = evaluate_final_numbering(
            lineup,
            [
                Op(
                    "updateChannel",
                    channelId=2,
                    data={"channel_number": 5},
                    acknowledgedDuplicate=Ack(5, [1]),
                )
            ],
        )
        assert len(issues) == 1
        assert sorted(issues[0].channel_ids) == [2, 3]

    def test_a_pile_up_everybody_consented_to_is_accepted(self):
        issues = evaluate_final_numbering(
            LINEUP,
            [
                Op(
                    "updateChannel",
                    channelId=2,
                    data={"channel_number": 5},
                    acknowledgedDuplicate=Ack(5, [1]),
                ),
                Op(
                    "updateChannel",
                    channelId=3,
                    data={"channel_number": 5},
                    acknowledgedDuplicate=Ack(5, [1, 2]),
                ),
            ],
        )
        assert issues == []

    def test_names_the_channels_and_the_operation(self):
        issues = evaluate_final_numbering(
            LINEUP, [Op("updateChannel", channelId=2, data={"channel_number": 5})]
        )
        assert "ESPN" in messages(issues)
        assert "TNT" in messages(issues)
        assert issues[0].operation_indexes == [0]

    def test_clearing_a_number_vacates_rather_than_occupies(self):
        lineup = [channel(1, "ESPN", 5), channel(2, "TNT", None), channel(3, "AMC", None)]
        issues = evaluate_final_numbering(
            lineup, [Op("updateChannel", channelId=1, data={"channel_number": None})]
        )
        assert issues == []

    def test_reports_once_per_number_however_many_pile_on(self):
        lineup = [channel(i, f"Ch{i}", i + 100) for i in range(1, 6)]
        issues = evaluate_final_numbering(
            lineup,
            [Op("updateChannel", channelId=i, data={"channel_number": 5}) for i in range(1, 6)],
        )
        assert len(issues) == 1
        assert len(issues[0].channel_ids) == 5

    @pytest.mark.parametrize("magnitude", [1, 1_000, 10_000_000, 2**40])
    def test_detects_a_conflict_at_every_magnitude(self, magnitude):
        lineup = [channel(1, "A", magnitude + 0.1), channel(2, "B", magnitude + 0.2)]
        issues = evaluate_final_numbering(
            lineup,
            [Op("updateChannel", channelId=2, data={"channel_number": magnitude + 0.1})],
        )
        assert len(issues) == 1

    @pytest.mark.parametrize("magnitude", [1, 1_000, 10_000_000, 2**40])
    def test_invents_no_conflict_at_every_magnitude(self, magnitude):
        lineup = [channel(1, "A", magnitude + 0.1), channel(2, "B", magnitude + 0.2)]
        issues = evaluate_final_numbering(
            lineup,
            [Op("updateChannel", channelId=2, data={"channel_number": magnitude + 0.3})],
        )
        assert issues == []

    def test_a_range_past_exact_integer_representability_is_refused(self):
        lineup = [channel(1, "A", None), channel(2, "B", None), channel(3, "C", None)]
        issues = evaluate_final_numbering(
            lineup,
            [
                Op(
                    "bulkAssignChannelNumbers",
                    channelIds=[1, 2, 3],
                    startingNumber=2.0**53 - 1,
                )
            ],
        )
        # Consecutive integers stop being distinct floats there, so the tail of
        # the range silently lands several channels on one number.
        assert issues

    def test_absurd_but_finite_numbers_stay_in_contract_and_still_compare(self):
        lineup = [channel(1, "A", 1e308), channel(2, "B", 6)]
        assert evaluate_final_numbering(
            lineup, [Op("updateChannel", channelId=2, data={"channel_number": 1e307})]
        ) == []
        assert (
            len(
                evaluate_final_numbering(
                    lineup, [Op("updateChannel", channelId=2, data={"channel_number": 1e308})]
                )
            )
            == 1
        )

    def test_terminates_on_a_large_plan(self):
        lineup = [channel(i, f"Ch{i}", i) for i in range(1, 2001)]
        ops = [
            Op("updateChannel", channelId=i, data={"channel_number": i + 5000})
            for i in range(1, 2001)
        ]
        assert evaluate_final_numbering(lineup, ops) == []

    def test_an_out_of_contract_final_number_is_refused(self):
        # Only reachable from a caller that bypassed the schema, which is
        # exactly the caller this check exists for.
        issues = evaluate_final_numbering(
            LINEUP, [Op("updateChannel", channelId=1, data={"channel_number": 1.05})]
        )
        assert len(issues) == 1
        assert issues[0].type == "invalid_channel_number"

    def test_an_out_of_contract_number_the_plan_did_not_touch_is_left_alone(self):
        # It came from Dispatcharr, which enforces nothing. Refusing the whole
        # Apply over a value the operator cannot reach from here would be a
        # dead end rather than a safeguard.
        lineup = [channel(1, "Legacy", 1.05), channel(2, "TNT", 6)]
        issues = evaluate_final_numbering(
            lineup, [Op("updateChannel", channelId=2, data={"channel_number": 8})]
        )
        assert issues == []


class TestConsentIsASubsetTestOnPurpose:
    """Fix round 3 adjudication: ``standing <= acknowledged``, not equality.

    An external reviewer read the subset test as a hole and asked for
    equality. It is not a hole, and equality would be a defect of its own. The
    acknowledgement names what the operator was SHOWN — recorded from the
    occupants the dialog rendered, not from a fresh lookup — while ``standing``
    is what actually materialised. So:

    * ``standing`` NOT a subset is the dangerous direction: occupants stand on
      the number that the operator was never told about. Still refused.
    * ``standing`` a strict subset is the harmless one: the collision the
      operator agreed to got SMALLER before Apply. Accepting it is the point.

    These are pins, and the reasoning is in the names so the next reviewer does
    not re-litigate it.
    """

    def test_a_shrunk_collision_the_operator_already_accepted_stays_accepted(self):
        """X on 5; A joins acknowledging [X]; B joins acknowledging [X, A]; A
        then leaves for 6. B is left sharing 5 with X, and X is exactly who B
        was shown. Equality would refuse an operator whose situation strictly
        improved."""
        lineup = [channel(9, "X", 5), channel(1, "A", 1), channel(2, "B", 2)]
        issues = evaluate_final_numbering(lineup, [
            Op("updateChannel", channelId=1, data={"channel_number": 5},
               acknowledgedDuplicate=Ack(5, [9])),
            Op("updateChannel", channelId=2, data={"channel_number": 5},
               acknowledgedDuplicate=Ack(5, [9, 1])),
            Op("updateChannel", channelId=1, data={"channel_number": 6}),
        ])
        assert issues == [], messages(issues)

    def test_an_occupant_the_operator_was_never_shown_is_still_refused(self):
        """The direction that matters. Consent to {X} while {X, A} stand makes
        ``standing`` not a subset, so it is refused — which is what makes the
        subset test a check rather than a rubber stamp."""
        lineup = [channel(9, "X", 5), channel(1, "A", 1), channel(2, "B", 2)]
        issues = evaluate_final_numbering(lineup, [
            Op("updateChannel", channelId=1, data={"channel_number": 5},
               acknowledgedDuplicate=Ack(5, [9])),
            Op("updateChannel", channelId=2, data={"channel_number": 5},
               acknowledgedDuplicate=Ack(5, [9])),
        ])
        assert len(issues) == 1, messages(issues)
        assert issues[0].type == "duplicate_channel_number"

    def test_a_dialog_that_named_a_later_arrival_still_authorises(self):
        """Why equality would refuse a fully informed operator, in the case the
        reviewer's own scenario does not reach. A's dialog named B because B
        was already on 5 when A was staged; a later re-staging of B makes B the
        LAST arrival, so ``standing`` for A is {X} while its acknowledgement
        names {X, B}. Under equality that mismatch refuses a plan every step of
        which the operator was shown."""
        lineup = [channel(9, "X", 5), channel(1, "A", 1), channel(2, "B", 2)]
        issues = evaluate_final_numbering(lineup, [
            Op("updateChannel", channelId=2, data={"channel_number": 5},
               acknowledgedDuplicate=Ack(5, [9])),
            Op("updateChannel", channelId=1, data={"channel_number": 5},
               acknowledgedDuplicate=Ack(5, [9, 2])),
            Op("updateChannel", channelId=2, data={"channel_number": 5},
               acknowledgedDuplicate=Ack(5, [9, 1])),
        ])
        assert issues == [], messages(issues)


class TestLandingOnAnEmptyNumberConsentsToNothing:
    """The defect the adjudication turned up, in the same expression.

    The consent walk asked every contributor for an acknowledgement, including
    the FIRST one to arrive on a number nobody was on. No dialog fires when a
    number is free, so no acknowledgement can exist, so the operator was told
    to "confirm the duplicate where you staged it" at a place where there had
    never been a duplicate to confirm. A dead end, reachable by moving one
    channel onto a free number and a second one on top of it.
    """

    def test_two_channels_onto_a_free_number_with_the_second_confirmed(self):
        lineup = [channel(1, "A", 1), channel(2, "B", 2)]
        issues = evaluate_final_numbering(lineup, [
            Op("updateChannel", channelId=2, data={"channel_number": 5}),
            Op("updateChannel", channelId=1, data={"channel_number": 5},
               acknowledgedDuplicate=Ack(5, [2])),
        ])
        assert issues == [], messages(issues)

    def test_the_second_arrival_still_has_to_have_named_the_first(self):
        """The anti-vacuity control. Nothing above weakens the check: only the
        arrival that landed on an empty number is excused."""
        lineup = [channel(1, "A", 1), channel(2, "B", 2)]
        issues = evaluate_final_numbering(lineup, [
            Op("updateChannel", channelId=2, data={"channel_number": 5}),
            Op("updateChannel", channelId=1, data={"channel_number": 5}),
        ])
        assert len(issues) == 1, messages(issues)
        assert issues[0].type == "duplicate_channel_number"

    def test_an_acknowledgement_naming_the_wrong_number_is_not_consent(self):
        """The other anti-vacuity control: the slot still has to match."""
        lineup = [channel(1, "A", 1), channel(2, "B", 2)]
        issues = evaluate_final_numbering(lineup, [
            Op("updateChannel", channelId=2, data={"channel_number": 5}),
            Op("updateChannel", channelId=1, data={"channel_number": 5},
               acknowledgedDuplicate=Ack(9, [2])),
        ])
        assert len(issues) == 1, messages(issues)
