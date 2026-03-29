"""Tests for _consolidate_operations in routers.channels."""

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
