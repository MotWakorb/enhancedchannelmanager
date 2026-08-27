"""Tests for _consolidate_operations in routers.channels."""

import pytest

from channel_number_plan import build_final_numbering_state
from routers.channels import (
    _consolidate_operations,
    BulkUpdateChannelOp,
    BulkAddStreamOp,
    BulkRemoveStreamOp,
    BulkReorderStreamsOp,
    BulkAssignNumbersOp,
    BulkCreateChannelOp,
    BulkDeleteChannelOp,
    BulkCreateGroupOp,
    BulkDeleteGroupOp,
    BulkRenameGroupOp,
)


# -- 1. Multiple updateChannel for same channel -> single merged update --

def test_multiple_updates_same_channel_merged():
    ops = [
        BulkUpdateChannelOp(channelId=1, data={"name": "foo"}),
        BulkUpdateChannelOp(channelId=1, data={"logo": "bar.png"}),
        BulkUpdateChannelOp(channelId=1, data={"name": "baz"}),
    ]
    result = _consolidate_operations(ops)
    updates = [o for o in result if o.type == "updateChannel"]
    assert len(updates) == 1
    assert updates[0].channelId == 1
    # Later values overwrite earlier ones
    assert updates[0].data == {"name": "baz", "logo": "bar.png"}


def test_updates_different_channels_kept_separate():
    ops = [
        BulkUpdateChannelOp(channelId=1, data={"name": "a"}),
        BulkUpdateChannelOp(channelId=2, data={"name": "b"}),
    ]
    result = _consolidate_operations(ops)
    updates = [o for o in result if o.type == "updateChannel"]
    assert len(updates) == 2


# -- 2. Add + remove same stream cancels out --

def test_add_then_remove_same_stream_cancels():
    ops = [
        BulkAddStreamOp(channelId=1, streamId=10),
        BulkRemoveStreamOp(channelId=1, streamId=10),
    ]
    result = _consolidate_operations(ops)
    stream_ops = [o for o in result if o.type in ("addStreamToChannel", "removeStreamFromChannel")]
    assert len(stream_ops) == 0


def test_remove_then_add_same_stream_cancels():
    ops = [
        BulkRemoveStreamOp(channelId=1, streamId=10),
        BulkAddStreamOp(channelId=1, streamId=10),
    ]
    result = _consolidate_operations(ops)
    stream_ops = [o for o in result if o.type in ("addStreamToChannel", "removeStreamFromChannel")]
    assert len(stream_ops) == 0


def test_add_without_remove_preserved():
    ops = [
        BulkAddStreamOp(channelId=1, streamId=10),
    ]
    result = _consolidate_operations(ops)
    adds = [o for o in result if o.type == "addStreamToChannel"]
    assert len(adds) == 1
    assert adds[0].streamId == 10


def test_remove_without_add_preserved():
    ops = [
        BulkRemoveStreamOp(channelId=1, streamId=10),
    ]
    result = _consolidate_operations(ops)
    removes = [o for o in result if o.type == "removeStreamFromChannel"]
    assert len(removes) == 1


# -- 3. Multiple reorderChannelStreams for same channel -> only final kept --

def test_multiple_reorders_same_channel_keeps_last():
    ops = [
        BulkReorderStreamsOp(channelId=1, streamIds=[10, 20]),
        BulkReorderStreamsOp(channelId=1, streamIds=[20, 10, 30]),
    ]
    result = _consolidate_operations(ops)
    reorders = [o for o in result if o.type == "reorderChannelStreams"]
    assert len(reorders) == 1
    assert reorders[0].streamIds == [20, 10, 30]


def test_reorders_different_channels_kept_separate():
    ops = [
        BulkReorderStreamsOp(channelId=1, streamIds=[10, 20]),
        BulkReorderStreamsOp(channelId=2, streamIds=[30, 40]),
    ]
    result = _consolidate_operations(ops)
    reorders = [o for o in result if o.type == "reorderChannelStreams"]
    assert len(reorders) == 2


# -- 4. Operations on channels that will be deleted are removed --

def test_update_on_deleted_channel_removed():
    ops = [
        BulkUpdateChannelOp(channelId=5, data={"name": "foo"}),
        BulkDeleteChannelOp(channelId=5),
    ]
    result = _consolidate_operations(ops)
    updates = [o for o in result if o.type == "updateChannel"]
    assert len(updates) == 0
    # Delete itself is preserved
    deletes = [o for o in result if o.type == "deleteChannel"]
    assert len(deletes) == 1


def test_add_stream_on_deleted_channel_removed():
    ops = [
        BulkAddStreamOp(channelId=5, streamId=10),
        BulkDeleteChannelOp(channelId=5),
    ]
    result = _consolidate_operations(ops)
    adds = [o for o in result if o.type == "addStreamToChannel"]
    assert len(adds) == 0


def test_reorder_on_deleted_channel_removed():
    ops = [
        BulkReorderStreamsOp(channelId=5, streamIds=[10, 20]),
        BulkDeleteChannelOp(channelId=5),
    ]
    result = _consolidate_operations(ops)
    reorders = [o for o in result if o.type == "reorderChannelStreams"]
    assert len(reorders) == 0


def test_assign_numbers_skips_deleted_channels():
    ops = [
        BulkAssignNumbersOp(channelIds=[1, 2, 3], startingNumber=100),
        BulkDeleteChannelOp(channelId=2),
    ]
    result = _consolidate_operations(ops)
    assigns = [o for o in result if o.type == "bulkAssignChannelNumbers"]
    all_ids = []
    for a in assigns:
        all_ids.extend(a.channelIds)
    assert 2 not in all_ids
    assert 1 in all_ids
    assert 3 in all_ids


# -- 5. Create + delete of same temp channel cancel out --

def test_create_then_delete_temp_channel_cancels():
    ops = [
        BulkCreateChannelOp(tempId=-1, name="New Channel"),
        BulkDeleteChannelOp(channelId=-1),
    ]
    result = _consolidate_operations(ops)
    creates = [o for o in result if o.type == "createChannel"]
    deletes = [o for o in result if o.type == "deleteChannel"]
    assert len(creates) == 0
    assert len(deletes) == 0


def test_delete_then_create_temp_channel_cancels():
    """Reverse-order create+delete of the same temp channel should also cancel.

    Companion to test_create_then_delete_temp_channel_cancels (forward order).
    Encodes the order-independent contract: the consolidator computes the
    created-temp-id set in a first pass (symmetric to channels_to_delete), so
    the delete is cancelled even when it precedes its matching create.
    """
    ops = [
        BulkDeleteChannelOp(channelId=-1),
        BulkCreateChannelOp(tempId=-1, name="New Channel"),
    ]
    result = _consolidate_operations(ops)
    creates = [o for o in result if o.type == "createChannel"]
    deletes = [o for o in result if o.type == "deleteChannel"]
    assert len(creates) == 0
    assert len(deletes) == 0


def test_delete_real_channel_not_cancelled():
    """Delete of a real (positive) channel ID is always preserved."""
    ops = [
        BulkDeleteChannelOp(channelId=42),
    ]
    result = _consolidate_operations(ops)
    deletes = [o for o in result if o.type == "deleteChannel"]
    assert len(deletes) == 1
    assert deletes[0].channelId == 42


# -- 6. Multiple bulkAssignChannelNumbers -> consolidated by consecutive ranges --

def test_multiple_assigns_consolidated_single_range():
    ops = [
        BulkAssignNumbersOp(channelIds=[1], startingNumber=100),
        BulkAssignNumbersOp(channelIds=[2], startingNumber=101),
        BulkAssignNumbersOp(channelIds=[3], startingNumber=102),
    ]
    result = _consolidate_operations(ops)
    assigns = [o for o in result if o.type == "bulkAssignChannelNumbers"]
    # Channels 1->100, 2->101, 3->102 are consecutive, should be one op
    assert len(assigns) == 1
    assert assigns[0].channelIds == [1, 2, 3]
    assert assigns[0].startingNumber == 100


def test_multiple_assigns_split_into_non_consecutive_ranges():
    ops = [
        BulkAssignNumbersOp(channelIds=[1], startingNumber=100),
        BulkAssignNumbersOp(channelIds=[2], startingNumber=101),
        BulkAssignNumbersOp(channelIds=[3], startingNumber=200),
    ]
    result = _consolidate_operations(ops)
    assigns = [o for o in result if o.type == "bulkAssignChannelNumbers"]
    # 1->100, 2->101 consecutive; 3->200 separate
    assert len(assigns) == 2


def test_later_assign_overwrites_earlier():
    """If same channel is assigned twice, last number wins."""
    ops = [
        BulkAssignNumbersOp(channelIds=[1, 2], startingNumber=100),
        BulkAssignNumbersOp(channelIds=[1], startingNumber=500),
    ]
    result = _consolidate_operations(ops)
    assigns = [o for o in result if o.type == "bulkAssignChannelNumbers"]
    # Channel 1 -> 500, Channel 2 -> 101
    all_channel_numbers = {}
    for a in assigns:
        for i, cid in enumerate(a.channelIds):
            all_channel_numbers[cid] = a.startingNumber + i
    assert all_channel_numbers[1] == 500
    assert all_channel_numbers[2] == 101


# -- 7. Ordered operations preserved --

def test_group_operations_preserved_in_order():
    ops = [
        BulkCreateGroupOp(name="Sports"),
        BulkRenameGroupOp(groupId=10, newName="Entertainment"),
        BulkDeleteGroupOp(groupId=5),
    ]
    result = _consolidate_operations(ops)
    group_ops = [o for o in result if o.type in ("createGroup", "deleteChannelGroup", "renameChannelGroup")]
    assert len(group_ops) == 3
    assert group_ops[0].type == "createGroup"
    assert group_ops[0].name == "Sports"
    assert group_ops[1].type == "renameChannelGroup"
    assert group_ops[2].type == "deleteChannelGroup"


def test_create_channel_order_preserved():
    ops = [
        BulkCreateChannelOp(tempId=-1, name="First"),
        BulkCreateChannelOp(tempId=-2, name="Second"),
    ]
    result = _consolidate_operations(ops)
    creates = [o for o in result if o.type == "createChannel"]
    assert len(creates) == 2
    assert creates[0].name == "First"
    assert creates[1].name == "Second"


# -- Edge cases --

def test_empty_operations():
    result = _consolidate_operations([])
    assert result == []


def test_mixed_operations_all_types():
    """Smoke test: all operation types together don't crash."""
    ops = [
        BulkCreateGroupOp(name="News"),
        BulkCreateChannelOp(tempId=-1, name="CNN"),
        BulkUpdateChannelOp(channelId=1, data={"name": "Updated"}),
        BulkAddStreamOp(channelId=1, streamId=10),
        BulkReorderStreamsOp(channelId=1, streamIds=[10]),
        BulkAssignNumbersOp(channelIds=[1], startingNumber=100),
        BulkRenameGroupOp(groupId=1, newName="News 2"),
        BulkDeleteGroupOp(groupId=2),
        BulkDeleteChannelOp(channelId=99),
    ]
    result = _consolidate_operations(ops)
    assert len(result) > 0


# -- Consolidation must not LOSE anything the caller sent ------------------
#
# Fix round 2 of bead enhancedchannelmanager-vdxbx. The merged updateChannel
# used to be rebuilt from parts -- ``BulkUpdateChannelOp(channelId=cid,
# data=data)`` -- so every field that was not ``channelId`` or ``data`` was
# dropped on the floor. That is the SECOND field this function has lost: an
# earlier round found it silently discarding whole operation types it had not
# enumerated. A rebuild-from-parts has to remember every field, and forgetting
# is silent, so these tests hold the shape rather than any one field.

def test_a_merged_update_keeps_the_acknowledgement_the_caller_sent():
    """The frontend always sends ``consolidate: true``.

    So an acknowledgement dropped here is an acknowledgement the preflight
    never sees, and an operator who explicitly confirmed a duplicate has their
    legitimate commit refused on the default path.
    """
    ops = [
        BulkUpdateChannelOp(
            channelId=1,
            data={"channel_number": 5},
            acknowledgedDuplicate={"number": 5, "occupantChannelIds": [2]},
        ),
    ]
    result = _consolidate_operations(ops)
    updates = [o for o in result if o.type == "updateChannel"]
    assert len(updates) == 1
    assert updates[0].acknowledgedDuplicate is not None
    assert updates[0].acknowledgedDuplicate.number == 5
    assert updates[0].acknowledgedDuplicate.occupantChannelIds == [2]


def test_the_acknowledgement_follows_the_operation_that_set_the_final_number():
    """Consolidation merges later ``data`` over earlier, so the number that
    survives came from ONE operation. The acknowledgement that survives has to
    be that operation's, or the merged op says the operator consented to a
    placement they were never asked about."""
    ops = [
        BulkUpdateChannelOp(
            channelId=1,
            data={"channel_number": 5},
            acknowledgedDuplicate={"number": 5, "occupantChannelIds": [2]},
        ),
        BulkUpdateChannelOp(
            channelId=1,
            data={"channel_number": 9},
            acknowledgedDuplicate={"number": 9, "occupantChannelIds": [3]},
        ),
    ]
    updates = [o for o in _consolidate_operations(ops) if o.type == "updateChannel"]
    assert len(updates) == 1
    assert updates[0].data == {"channel_number": 9}
    assert updates[0].acknowledgedDuplicate.number == 9


def test_a_later_name_only_edit_does_not_withdraw_the_acknowledgement():
    ops = [
        BulkUpdateChannelOp(
            channelId=1,
            data={"channel_number": 5},
            acknowledgedDuplicate={"number": 5, "occupantChannelIds": [2]},
        ),
        BulkUpdateChannelOp(channelId=1, data={"name": "Renamed"}),
    ]
    updates = [o for o in _consolidate_operations(ops) if o.type == "updateChannel"]
    assert len(updates) == 1
    assert updates[0].data == {"channel_number": 5, "name": "Renamed"}
    assert updates[0].acknowledgedDuplicate is not None


def test_consolidating_an_update_preserves_every_field_except_data():
    """The structural guard, not another named field.

    Any field added to ``BulkUpdateChannelOp`` in future rides through
    consolidation unless somebody deliberately decides otherwise, and this
    fails the moment one does not.
    """
    op = BulkUpdateChannelOp(
        channelId=7,
        data={"channel_number": 5},
        acknowledgedDuplicate={"number": 5, "occupantChannelIds": [2]},
    )
    updates = [o for o in _consolidate_operations([op]) if o.type == "updateChannel"]
    assert len(updates) == 1
    assert updates[0].model_dump(exclude={"data"}) == op.model_dump(exclude={"data"})


def test_the_range_op_consolidation_rebuilds_carry_no_per_operation_bookkeeping():
    """``bulkAssignChannelNumbers`` is the one arm that genuinely CANNOT
    template off a single input op -- it regroups several into consecutive
    ranges -- so its output is still built from parts. That is only safe while
    the model has nothing else on it. Adding a field to
    ``BulkAssignNumbersOp`` has to be a decision, so it breaks this."""
    assert set(BulkAssignNumbersOp.model_fields) == {
        "type",
        "channelIds",
        "startingNumber",
    }


# -- Consolidation must not REORDER what the caller sent -------------------
#
# Fix round 3. The output is grouped by KIND — merged updates, then range
# assignments — so the order operations are emitted in says nothing about the
# order they were sent in. Between kinds that both place a channel on a number,
# that inverted last-write-wins: the browser previews the number the SUBMITTED
# order produces, and the server validated and applied the other one.

_LINEUP = [
    {"id": 1, "name": "ESPN", "channel_number": 5},
    {"id": 2, "name": "TNT", "channel_number": 6},
]


def _final_numbers(operations, lineup=None):
    """The final number per channel, read through the shared materialiser.

    The same function the preflight validates and — via its TypeScript twin —
    the same one the browser previews, so this is what "what the operator was
    shown" means rather than a second opinion about it.
    """
    state = build_final_numbering_state(_LINEUP if lineup is None else lineup, operations)
    return {p.channel_id: p.number for p in state.placements}


def _number_writers(operations) -> dict[int, list[str]]:
    """Channel id -> the type of every emitted operation that WRITES its number.

    The property, not the outcome. An assertion on the final materialised
    number passes while two operations race to produce it: the create writes 5,
    the range then writes 10, the materialiser says 10, and if the second write
    fails the channel is left on a 5 nobody asked for. Only counting the
    writers can see that.

    A ``createChannel`` counts only when it still carries a number, which is
    exactly the state stripping puts it in — the create is always emitted,
    because it is what makes the channel exist.
    """
    writers: dict[int, list[str]] = {}
    for op in operations:
        if op.type == "updateChannel":
            if isinstance(op.data, dict) and "channel_number" in op.data:
                writers.setdefault(op.channelId, []).append(op.type)
        elif op.type == "createChannel":
            if op.channelNumber is not None:
                writers.setdefault(op.tempId, []).append(op.type)
        elif op.type == "bulkAssignChannelNumbers":
            for cid in op.channelIds:
                writers.setdefault(cid, []).append(op.type)
    return writers


def assert_one_writer_per_channel(operations) -> dict[int, list[str]]:
    """Invariant: exactly one emitted operation writes any channel's number."""
    writers = _number_writers(operations)
    multiply_written = {cid: kinds for cid, kinds in writers.items() if len(kinds) > 1}
    assert multiply_written == {}, (
        f"more than one emitted operation writes these channels' numbers: "
        f"{multiply_written}"
    )
    return writers


def test_a_range_followed_by_an_edit_applies_the_edit():
    """The confirmed reproduction. Submitted order puts 20 last, so 20 wins."""
    ops = [
        BulkAssignNumbersOp(channelIds=[1], startingNumber=10),
        BulkUpdateChannelOp(channelId=1, data={"channel_number": 20}),
    ]
    assert _final_numbers(_consolidate_operations(ops)) == _final_numbers(ops) == {1: 20, 2: 6}


def test_an_edit_followed_by_a_range_applies_the_range():
    """The other direction, which the grouped output got right by accident.
    Pinned so a fix for the first cannot break it."""
    ops = [
        BulkUpdateChannelOp(channelId=1, data={"channel_number": 20}),
        BulkAssignNumbersOp(channelIds=[1], startingNumber=10),
    ]
    assert _final_numbers(_consolidate_operations(ops)) == _final_numbers(ops) == {1: 10, 2: 6}


def test_only_one_operation_writes_a_channels_number():
    """The property that makes emission order unable to change the answer.

    Two writes to one channel's number is also the thing "consolidate" exists
    to avoid, so a superseded update is dropped rather than left to be
    overwritten.
    """
    ops = [
        BulkUpdateChannelOp(channelId=1, data={"channel_number": 20}),
        BulkAssignNumbersOp(channelIds=[1], startingNumber=10),
    ]
    assert assert_one_writer_per_channel(_consolidate_operations(ops)) == {
        1: ["bulkAssignChannelNumbers"]
    }


def test_two_operations_writing_one_number_is_what_the_check_catches():
    """Anti-vacuity control for :func:`assert_one_writer_per_channel`.

    The submitted list has the defect the consolidated list must not, so a
    check that could not fail while a channel was written twice would fail
    here.
    """
    ops = [
        BulkUpdateChannelOp(channelId=1, data={"channel_number": 20}),
        BulkAssignNumbersOp(channelIds=[1], startingNumber=10),
    ]
    with pytest.raises(AssertionError):
        assert_one_writer_per_channel(ops)


def test_a_superseded_edit_keeps_the_rest_of_its_data():
    ops = [
        BulkUpdateChannelOp(channelId=1, data={"channel_number": 20, "name": "ESPN HD"}),
        BulkAssignNumbersOp(channelIds=[1], startingNumber=10),
    ]
    updates = [o for o in _consolidate_operations(ops) if o.type == "updateChannel"]
    assert len(updates) == 1
    assert updates[0].data == {"name": "ESPN HD"}


def test_a_superseded_edit_does_not_carry_its_consent_forward():
    """An acknowledgement is consent to ONE placement. If a later range
    assignment is what actually places the channel, the placement the operator
    confirmed is not the one that happens, and forwarding the confirmation
    would manufacture consent for a collision nobody was shown."""
    ops = [
        BulkUpdateChannelOp(
            channelId=1,
            data={"channel_number": 20, "name": "ESPN HD"},
            acknowledgedDuplicate={"number": 20, "occupantChannelIds": [2]},
        ),
        BulkAssignNumbersOp(channelIds=[1], startingNumber=10),
    ]
    updates = [o for o in _consolidate_operations(ops) if o.type == "updateChannel"]
    assert len(updates) == 1
    assert updates[0].acknowledgedDuplicate is None
    assert updates[0].expectedNumber is None


def test_a_superseded_edit_with_nothing_else_to_write_is_dropped():
    ops = [
        BulkUpdateChannelOp(channelId=1, data={"channel_number": 20}),
        BulkAssignNumbersOp(channelIds=[1], startingNumber=10),
    ]
    assert [o for o in _consolidate_operations(ops) if o.type == "updateChannel"] == []


def test_the_number_scoped_fields_are_pinned_against_the_model():
    """Dropping a superseded ``channel_number`` has to drop the bookkeeping
    that describes that write and nothing else. This function has already lost
    a field twice by having to remember one, so a field added to
    ``BulkUpdateChannelOp`` breaks this until somebody classifies it.

    Imported inside the test rather than at module scope so that the
    behavioural tests around it fail on BEHAVIOUR rather than all failing
    together on a missing name.
    """
    from routers.channels import _NUMBER_SCOPED_UPDATE_FIELDS

    assert set(BulkUpdateChannelOp.model_fields) == {
        "type",
        "channelId",
        "data",
    } | set(_NUMBER_SCOPED_UPDATE_FIELDS)


def test_a_range_sent_before_the_create_it_names_places_nothing():
    """Both materialisers treat an operation naming a channel that does not
    exist yet as a no-op, so consolidation must not turn one into a placement
    by emitting the create first."""
    ops = [
        BulkAssignNumbersOp(channelIds=[-1], startingNumber=10),
        BulkCreateChannelOp(tempId=-1, name="New", channelNumber=3),
    ]
    assert _final_numbers(_consolidate_operations(ops)) == _final_numbers(ops)
    assert _final_numbers(ops)[-1] == 3


def test_a_range_after_the_create_it_names_still_places_it():
    ops = [
        BulkCreateChannelOp(tempId=-1, name="New", channelNumber=3),
        BulkAssignNumbersOp(channelIds=[-1], startingNumber=10),
    ]
    consolidated = _consolidate_operations(ops)
    assert _final_numbers(consolidated) == _final_numbers(ops)
    assert _final_numbers(ops)[-1] == 10
    # The number, not just the number the materialiser ends up with. The
    # create used to be emitted unchanged beside the range that supersedes it.
    assert assert_one_writer_per_channel(consolidated) == {
        -1: ["bulkAssignChannelNumbers"]
    }


# -- A created channel keeps exactly one number-writing operation ----------
#
# Fix round 4. ``channel_number_owner`` was consulted for merged updates and
# for range assignments and NOT for creates, which were appended to
# ``ordered_ops`` and copied straight into the output. So a create whose number
# a later operation supersedes was still emitted carrying it: the channel was
# created on the superseded number, the temp id resolved, and the owner then
# wrote the real one. Two writes, and if the second failed the new channel was
# left on an intermediate nobody asked for — the exact middle state ic884.3
# exists to prevent.


def test_a_create_superseded_by_a_range_is_emitted_without_its_number():
    ops = [
        BulkCreateChannelOp(tempId=-1, name="New", channelNumber=5),
        BulkAssignNumbersOp(channelIds=[-1], startingNumber=10),
    ]
    consolidated = _consolidate_operations(ops)
    creates = [o for o in consolidated if o.type == "createChannel"]
    assert len(creates) == 1, "the create must still be emitted — it creates the channel"
    assert creates[0].channelNumber is None
    assert creates[0].name == "New"
    assert creates[0].tempId == -1
    assert assert_one_writer_per_channel(consolidated) == {
        -1: ["bulkAssignChannelNumbers"]
    }
    assert _final_numbers(consolidated) == _final_numbers(ops)


def test_a_create_superseded_by_an_edit_is_emitted_without_its_number():
    ops = [
        BulkCreateChannelOp(tempId=-1, name="New", channelNumber=5),
        BulkUpdateChannelOp(channelId=-1, data={"channel_number": 10}),
    ]
    consolidated = _consolidate_operations(ops)
    creates = [o for o in consolidated if o.type == "createChannel"]
    assert len(creates) == 1
    assert creates[0].channelNumber is None
    assert assert_one_writer_per_channel(consolidated) == {-1: ["updateChannel"]}
    assert _final_numbers(consolidated) == _final_numbers(ops)
    # And the create is still emitted BEFORE the operation that numbers it, so
    # the temp id has something to resolve to.
    kinds = [o.type for o in consolidated]
    assert kinds.index("createChannel") < kinds.index("updateChannel")


def test_a_create_superseded_by_a_range_drops_its_consent_too():
    """An acknowledgement on a create is consent to place THAT channel on THAT
    number beside THOSE occupants. When the number comes off, the placement it
    consented to is not the one that happens, so carrying it forward would
    manufacture consent for a collision nobody was shown — the same reasoning
    that governs a superseded update."""
    ops = [
        BulkCreateChannelOp(
            tempId=-1,
            name="New",
            channelNumber=5,
            acknowledgedDuplicate={"number": 5, "occupantChannelIds": [1]},
        ),
        BulkAssignNumbersOp(channelIds=[-1], startingNumber=10),
    ]
    creates = [o for o in _consolidate_operations(ops) if o.type == "createChannel"]
    assert len(creates) == 1
    assert creates[0].acknowledgedDuplicate is None


def test_a_create_that_owns_its_number_keeps_everything():
    """The control. Nothing supersedes this create, so nothing comes off it —
    a strip that fired unconditionally would pass every test above and lose
    every number an operator typed into the create dialog."""
    ops = [
        BulkAssignNumbersOp(channelIds=[-1], startingNumber=10),
        BulkCreateChannelOp(
            tempId=-1,
            name="New",
            channelNumber=5,
            acknowledgedDuplicate={"number": 5, "occupantChannelIds": [1]},
        ),
    ]
    consolidated = _consolidate_operations(ops)
    creates = [o for o in consolidated if o.type == "createChannel"]
    assert len(creates) == 1
    assert creates[0].channelNumber == 5
    assert creates[0].acknowledgedDuplicate is not None
    assert assert_one_writer_per_channel(consolidated) == {-1: ["createChannel"]}


def test_a_lone_create_keeps_its_number():
    ops = [BulkCreateChannelOp(tempId=-1, name="New", channelNumber=5)]
    consolidated = _consolidate_operations(ops)
    assert consolidated[0].channelNumber == 5
    assert assert_one_writer_per_channel(consolidated) == {-1: ["createChannel"]}


def test_a_superseded_create_keeps_everything_that_is_not_the_number():
    """Stripping is scoped to the number and its bookkeeping. The channel this
    operation creates must be the channel the operator described."""
    ops = [
        BulkCreateChannelOp(
            tempId=-1,
            name="New",
            channelNumber=5,
            groupId=7,
            logoId=9,
            logoUrl="http://example.invalid/logo.png",
            tvgId="tvg-1",
            tvcGuideStationId="gracenote-1",
            normalize=True,
        ),
        BulkAssignNumbersOp(channelIds=[-1], startingNumber=10),
    ]
    created = [o for o in _consolidate_operations(ops) if o.type == "createChannel"][0]
    assert created.model_dump() == {
        **ops[0].model_dump(),
        "channelNumber": None,
        "acknowledgedDuplicate": None,
    }


def test_the_number_scoped_create_fields_are_pinned_against_the_model():
    """The same pin as for ``BulkUpdateChannelOp``, for the same reason: a
    field added to ``BulkCreateChannelOp`` has to be classified as describing
    the number write or not, rather than silently riding through a strip.

    Imported inside the test so the behavioural tests around it fail on
    BEHAVIOUR rather than all failing together on a missing name. An
    import-level red, and labelled as one.
    """
    from routers.channels import _NUMBER_SCOPED_CREATE_FIELDS

    assert set(BulkCreateChannelOp.model_fields) == {
        "type",
        "tempId",
        "name",
        "groupId",
        "newGroupName",
        "logoId",
        "logoUrl",
        "tvgId",
        "tvcGuideStationId",
        "normalize",
        "expectedStreamIds",
    } | set(_NUMBER_SCOPED_CREATE_FIELDS)


def test_a_create_and_delete_that_cancel_leave_no_writer_behind():
    ops = [
        BulkCreateChannelOp(tempId=-1, name="New", channelNumber=5),
        BulkDeleteChannelOp(channelId=-1),
        BulkAssignNumbersOp(channelIds=[-1], startingNumber=10),
    ]
    consolidated = _consolidate_operations(ops)
    assert [o.type for o in consolidated] == []
    assert assert_one_writer_per_channel(consolidated) == {}


@pytest.mark.parametrize(
    "trailing",
    [
        BulkAssignNumbersOp(channelIds=[-1, -2], startingNumber=10),
        BulkUpdateChannelOp(channelId=-1, data={"channel_number": 10}),
    ],
)
def test_a_mixture_of_kinds_over_real_and_temp_ids_writes_each_number_once(trailing):
    """The invariant across the whole mixture, not one demonstrated door:
    creates, edits and ranges over both temp and real ids, in an order that
    makes every kind win somewhere."""
    ops = [
        BulkCreateChannelOp(tempId=-1, name="A", channelNumber=5),
        BulkCreateChannelOp(tempId=-2, name="B", channelNumber=6),
        BulkUpdateChannelOp(channelId=1, data={"channel_number": 20, "name": "ESPN HD"}),
        BulkAssignNumbersOp(channelIds=[1, 2], startingNumber=30),
        BulkUpdateChannelOp(channelId=2, data={"channel_number": 40}),
        trailing,
    ]
    consolidated = _consolidate_operations(ops)
    assert_one_writer_per_channel(consolidated)
    assert _final_numbers(consolidated) == _final_numbers(ops)


# -- An omitted range start is 1 everywhere, and 0 means 0 -----------------

def test_an_omitted_range_start_consolidates_to_one():
    """``or 0`` turned an omission into an explicit 0. The frontend
    materialiser, the backend materialiser and the executor all default to 1,
    so the browser previewed channel 7 on 1 and the server applied 0."""
    ops = [BulkAssignNumbersOp(channelIds=[1])]
    ranges = [o for o in _consolidate_operations(ops) if o.type == "bulkAssignChannelNumbers"]
    assert len(ranges) == 1
    assert ranges[0].startingNumber == 1
    assert _final_numbers(_consolidate_operations(ops)) == _final_numbers(ops) == {1: 1, 2: 6}


def test_an_explicit_zero_range_start_is_honoured():
    """0 is a valid channel number (ic884.1 settled non-negative), so an
    explicit 0 is a real request rather than a stand-in for "unset". ``or``
    could not tell the two apart; ``is None`` can."""
    ops = [BulkAssignNumbersOp(channelIds=[1, 2], startingNumber=0)]
    ranges = [o for o in _consolidate_operations(ops) if o.type == "bulkAssignChannelNumbers"]
    assert len(ranges) == 1
    assert ranges[0].startingNumber == 0
    assert _final_numbers(_consolidate_operations(ops)) == _final_numbers(ops) == {1: 0, 2: 1}
