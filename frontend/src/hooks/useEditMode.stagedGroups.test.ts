/**
 * Regression tests for Edit Mode's handling of groups that are still PENDING
 * inside the same commit (beads enhancedchannelmanager-udq1j and -vtapf), and
 * for the exit summary's arithmetic (bead enhancedchannelmanager-75k49).
 *
 * WHAT THE DRILL ACTUALLY SAW (2026-08-09-run18, ECM 0.18.1-0051)
 *
 * One Edit Mode session staged 8 channels into a NEW group "Drill Locals", 3
 * into a new "Drill Movies", then one more channel via Create in... ->
 * "Drill Locals" — a group that existed only as a negative staging id. The
 * wire protocol resolves new groups BY NAME (`groupsToCreate` up,
 * `groupIdMap` back), so that last channel carried `groupId: -1000` verbatim
 * and Dispatcharr answered:
 *
 *   400 {"channel_group_id":["Invalid pk \"-1000\" - object does not exist."]}
 *
 * 11 of 12 channels were created, the backend correctly reported
 * `success=False, applied=11, failed=1` — and the operator was shown NOTHING.
 *
 * WHY THE FAKE BACKEND BELOW REJECTS NEGATIVE IDS
 *
 * Run 17 shipped a fix that passed the whole suite and 400'd on the first real
 * click, because its test double happily accepted a `channel_group_id` the
 * live Dispatcharr API rejects. So `fakeBulkCommit` enforces the live
 * contract: any operation reaching it with a negative `channel_group_id`
 * FAILS, exactly as Dispatcharr does. A regression that puts a temp id back on
 * the wire fails these tests instead of agreeing with them.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useEditMode } from './useEditMode';
import type { Channel } from '../types';
import type { BulkCommitRequest, BulkCommitResponse } from '../services/api';

const bulkCommit = vi.fn();
const getChannels = vi.fn();

vi.mock('../services/api', () => ({
  bulkCommit: (req: BulkCommitRequest) => bulkCommit(req),
  getChannels: (args: unknown) => getChannels(args),
}));

vi.mock('../utils/idGenerator', () => {
  let n = 0;
  return { generateId: vi.fn(() => `op-${(n += 1)}`) };
});

const FIRST_REAL_GROUP_ID = 500;
const FIRST_REAL_CHANNEL_ID = 900;

/** Requests the hook posted, newest last. */
let requests: BulkCommitRequest[] = [];

/**
 * A bulk-commit double that behaves like Dispatcharr, not like a yes-man.
 *
 * Groups named in `groupsToCreate` get real ids; a `createChannel` naming one
 * resolves to that id; anything still carrying a negative group id is refused
 * with the same 400 the live API returns.
 */
function fakeBulkCommit(request: BulkCommitRequest): BulkCommitResponse {
  requests.push(request);
  const groupIdMap: Record<string, number> = { ...knownGroups };
  for (const group of request.groupsToCreate ?? []) {
    if (!(group.name in groupIdMap)) {
      groupIdMap[group.name] = FIRST_REAL_GROUP_ID + Object.keys(groupIdMap).length;
    }
  }
  Object.assign(knownGroups, groupIdMap);

  const tempIdMap: Record<number, number> = {};
  const errors: BulkCommitResponse['errors'] = [];
  let applied = 0;
  let failed = 0;

  for (const op of request.operations) {
    const resolvedGroupId =
      op.type === 'createChannel'
        ? (typeof op.newGroupName === 'string'
          ? groupIdMap[op.newGroupName]
          : (op.groupId as number | undefined))
        : op.type === 'updateChannel'
          ? (op.data as Partial<Channel> | undefined)?.channel_group_id
          : undefined;

    if (typeof resolvedGroupId === 'number' && resolvedGroupId < 0) {
      failed += 1;
      errors.push({
        operationId: `op-${op.type}`,
        operationType: op.type,
        error: `Channel creation failed: 400 - {"channel_group_id":["Invalid pk \\"${resolvedGroupId}\\" - object does not exist."]}`,
      });
      continue;
    }
    if (op.type === 'createChannel') {
      tempIdMap[op.tempId as number] = FIRST_REAL_CHANNEL_ID + Object.keys(tempIdMap).length;
    }
    applied += 1;
  }

  return {
    success: failed === 0,
    operationsApplied: applied,
    operationsFailed: failed,
    errors,
    tempIdMap,
    groupIdMap,
  };
}

let knownGroups: Record<string, number> = {};

function makeChannel(id: number, name: string, groupId: number | null = null): Channel {
  return {
    id,
    channel_number: id,
    name,
    channel_group_id: groupId,
    tvg_id: null,
    tvc_guide_stationid: null,
    epg_data_id: null,
    streams: [],
    stream_profile_id: null,
    uuid: `uuid-${id}`,
    logo_id: null,
    auto_created: false,
    auto_created_by: null,
    auto_created_by_name: null,
  };
}

function renderEditMode(channels: Channel[] = [], onError = vi.fn()) {
  const view = renderHook(() =>
    useEditMode({ channels, onChannelsChange: vi.fn(), onError })
  );
  act(() => {
    view.result.current.enterEditMode();
  });
  return { view, onError };
}

/** Every createChannel operation posted, across all requests. */
function createChannelOps() {
  return requests.flatMap((r) => r.operations.filter((op) => op.type === 'createChannel'));
}

function updateChannelOps() {
  return requests.flatMap((r) => r.operations.filter((op) => op.type === 'updateChannel'));
}

beforeEach(() => {
  vi.clearAllMocks();
  requests = [];
  knownGroups = {};
  bulkCommit.mockImplementation(async (req: BulkCommitRequest) => fakeBulkCommit(req));
  getChannels.mockResolvedValue({ results: [], next: null, count: 0 });
});

describe('useEditMode — a channel created into a group pending in the same batch', () => {
  it('sends the pending group BY NAME, never as its negative staging id', async () => {
    const { view } = renderEditMode();

    // 1. Stage a channel into a brand-new group — this is what allocates the
    //    group's negative temp id.
    let tempGroupId = 0;
    act(() => {
      view.result.current.stageCreateChannel('First', 1, undefined, 'Drill Locals');
    });
    tempGroupId = view.result.current.stagedGroups[0].id;
    expect(tempGroupId).toBeLessThan(0);

    // 2. Stage a second channel via "Create in... -> Drill Locals", i.e. by the
    //    pending group's id, with no newGroupName. This is the drill's trigger.
    act(() => {
      view.result.current.stageCreateChannel('Second', 2, tempGroupId);
    });

    let result!: Awaited<ReturnType<typeof view.result.current.commit>>;
    await act(async () => {
      result = await view.result.current.commit(undefined, { continueOnError: true });
    });

    const creates = createChannelOps();
    expect(creates).toHaveLength(2);
    for (const op of creates) {
      expect(op.newGroupName).toBe('Drill Locals');
      expect(op.groupId).toBeUndefined();
    }
    // One group, named once — not one per channel.
    expect(requests[0].groupsToCreate).toEqual([{ name: 'Drill Locals' }]);

    expect(result.operationsFailed).toBe(0);
    expect(result.operationsApplied).toBe(2);
    expect(result.success).toBe(true);
  });

  it('still resolves an already-real target group by id', async () => {
    const { view } = renderEditMode();

    act(() => {
      view.result.current.stageCreateChannel('Into real group', 1, 376);
    });

    await act(async () => {
      await view.result.current.commit(undefined, { continueOnError: true });
    });

    const [op] = createChannelOps();
    expect(op.groupId).toBe(376);
    expect(op.newGroupName).toBeUndefined();
  });

  it('resolves a channel MOVED into a pending group once the group has a real id', async () => {
    // The same defect through updateChannel: dragging an existing channel onto
    // a staged group's header staged `channel_group_id: -1000`.
    const { view } = renderEditMode([makeChannel(7, 'Existing')]);

    let tempGroupId = 0;
    act(() => {
      view.result.current.stageCreateChannel('Seed', 1, undefined, 'Drill Movies');
    });
    tempGroupId = view.result.current.stagedGroups[0].id;

    act(() => {
      view.result.current.stageUpdateChannel(
        7,
        { channel_group_id: tempGroupId },
        'Moved "Existing" to "Drill Movies"'
      );
    });

    let result!: Awaited<ReturnType<typeof view.result.current.commit>>;
    await act(async () => {
      result = await view.result.current.commit(undefined, { continueOnError: true });
    });

    const [update] = updateChannelOps();
    const data = update.data as Partial<Channel>;
    expect(data.channel_group_id).toBe(knownGroups['Drill Movies']);
    expect(data.channel_group_id).toBeGreaterThan(0);
    expect(result.operationsFailed).toBe(0);
  });

  it('refuses to commit at all when a staged group reference cannot be resolved', async () => {
    const onError = vi.fn();
    const { view } = renderEditMode([], onError);

    // -4242 was never staged in this session.
    act(() => {
      view.result.current.stageCreateChannel('Orphan', 1, -4242);
    });

    let result!: Awaited<ReturnType<typeof view.result.current.commit>>;
    await act(async () => {
      result = await view.result.current.commit(undefined, { continueOnError: true });
    });

    expect(bulkCommit).not.toHaveBeenCalled();
    expect(result.success).toBe(false);
    expect(result.operationsFailed).toBe(1);
    expect(onError).toHaveBeenCalledWith(expect.stringContaining('Nothing was applied'));
    // The operator's work survives — edit mode is still holding it.
    expect(view.result.current.isEditMode).toBe(true);
    expect(view.result.current.summary.newChannels).toBe(1);
  });

  it('reports a partially-applied commit instead of returning a clean result', async () => {
    // Independent of the -1000 bug: any per-operation failure has to come back
    // as a countable failure AND reach onError, or the operator is told a batch
    // succeeded when the server said otherwise.
    const onError = vi.fn();
    const { view } = renderEditMode([makeChannel(1, 'A')], onError);

    act(() => {
      view.result.current.stageAddStream(1, 55, 'Added stream');
    });

    bulkCommit.mockImplementation(async (req: BulkCommitRequest) => {
      requests.push(req);
      return {
        success: false,
        operationsApplied: 11,
        operationsFailed: 1,
        errors: [{
          operationId: 'op-11-createChannel',
          operationType: 'createChannel',
          error: 'Channel creation failed: 400 - Invalid pk "-1000"',
          channelName: 'TX | Dallas | PBS KERA',
        }],
        tempIdMap: {},
        groupIdMap: {},
      } satisfies BulkCommitResponse;
    });

    let result!: Awaited<ReturnType<typeof view.result.current.commit>>;
    await act(async () => {
      result = await view.result.current.commit(undefined, { continueOnError: true });
    });

    expect(result.success).toBe(false);
    expect(result.operationsApplied).toBe(11);
    expect(result.operationsFailed).toBe(1);
    expect(result.errors[0].channelName).toBe('TX | Dallas | PBS KERA');
    expect(onError).toHaveBeenCalledWith(expect.stringContaining('11 succeeded, 1 failed'));
  });
});

describe('useEditMode — stageCreateGroup (bd-vtapf)', () => {
  it('stages the group locally, with an undo entry and a temp id', () => {
    const { view } = renderEditMode();

    let tempGroupId = 0;
    act(() => {
      tempGroupId = view.result.current.stageCreateGroup('Drill Empty Group');
    });

    expect(tempGroupId).toBeLessThan(0);
    expect(view.result.current.stagedGroups).toEqual([
      { id: tempGroupId, name: 'Drill Empty Group', channel_count: 0 },
    ]);
    // It is in the ledger: the undo counter moved and the summary names it.
    expect(view.result.current.stagedOperationCount).toBe(1);
    expect(view.result.current.summary.newGroups).toBe(1);
    expect(view.result.current.summary.operationDetails).toContainEqual(
      expect.objectContaining({ description: 'Create group "Drill Empty Group"' })
    );
  });

  it('is undone by Discard — nothing reaches Dispatcharr', () => {
    const { view } = renderEditMode();

    act(() => {
      view.result.current.stageCreateGroup('Drill Empty Group');
    });
    act(() => {
      view.result.current.discard();
    });

    expect(bulkCommit).not.toHaveBeenCalled();
    expect(view.result.current.stagedGroups).toEqual([]);
    expect(view.result.current.isEditMode).toBe(false);
  });

  it('creates the group in the same phase channels are resolved against', async () => {
    const { view } = renderEditMode();

    let tempGroupId = 0;
    act(() => {
      tempGroupId = view.result.current.stageCreateGroup('Drill Empty Group');
    });
    act(() => {
      view.result.current.stageCreateChannel('Member', 1, tempGroupId);
    });

    let result!: Awaited<ReturnType<typeof view.result.current.commit>>;
    await act(async () => {
      result = await view.result.current.commit(undefined, { continueOnError: true });
    });

    expect(requests[0].groupsToCreate).toEqual([{ name: 'Drill Empty Group' }]);
    // No standalone createGroup wire op: it would run in a later request whose
    // groupIdMap the channel ops can no longer see.
    expect(requests.flatMap((r) => r.operations).some((op) => op.type === 'createGroup')).toBe(false);
    expect(createChannelOps()[0].newGroupName).toBe('Drill Empty Group');
    expect(result.operationsFailed).toBe(0);
  });

  it('exits edit mode after a batch of nothing but groups', async () => {
    // Groups travel in `groupsToCreate`, which is a phase and not an
    // operation, so it contributes nothing to operationsApplied. "Create new
    // channel group" -> Done -> Apply All is a whole batch of them.
    const { view } = renderEditMode();

    act(() => {
      view.result.current.stageCreateGroup('Drill Empty Group');
    });

    let result!: Awaited<ReturnType<typeof view.result.current.commit>>;
    await act(async () => {
      result = await view.result.current.commit(undefined, { continueOnError: true });
    });

    expect(requests[0].groupsToCreate).toEqual([{ name: 'Drill Empty Group' }]);
    expect(result.success).toBe(true);
    // The group is real now — nothing must still be staged against a temp id.
    expect(view.result.current.isEditMode).toBe(false);
    expect(view.result.current.stagedGroups).toEqual([]);
  });

  it('does not double-count a group reached both explicitly and by newGroupName', () => {
    const { view } = renderEditMode();

    act(() => {
      view.result.current.stageCreateGroup('Drill Locals');
    });
    act(() => {
      view.result.current.stageCreateChannel('First', 1, undefined, 'Drill Locals');
    });

    expect(view.result.current.stagedGroups).toHaveLength(1);
    expect(view.result.current.summary.newGroups).toBe(1);
  });
});

describe('useEditMode — exit summary arithmetic (bd-75k49)', () => {
  it('totalChanges equals the sum of the lines the dialog renders', () => {
    // The drill's own batch: 12 channels into 2 new groups, one stream each.
    const { view } = renderEditMode();

    act(() => {
      for (let i = 0; i < 12; i += 1) {
        const groupName = i < 9 ? 'Drill Locals' : 'Drill Movies';
        const tempId = view.result.current.stageCreateChannel(`Ch ${i}`, i + 1, undefined, groupName);
        view.result.current.stageAddStream(tempId, 100 + i, 'Assign stream');
      }
    });

    const s = view.result.current.summary;
    expect(s.newChannels).toBe(12);
    expect(s.streamsAdded).toBe(12);
    expect(s.newGroups).toBe(2);
    // 12 + 12 + 2. The old headline said 24 over lines summing to 26.
    expect(s.totalChanges).toBe(26);
    expect(s.totalChanges).toBe(s.newChannels + s.streamsAdded + s.newGroups);
  });

  it('names a cleared logo instead of dropping it from the summary', () => {
    const { view } = renderEditMode([makeChannel(1, 'A')]);

    act(() => {
      for (let i = 0; i < 9; i += 1) {
        view.result.current.stageUpdateChannel(1, { epg_data_id: i }, 'EPG');
      }
      view.result.current.stageUpdateChannel(1, { logo_id: null }, 'Removed logo');
    });

    const s = view.result.current.summary;
    expect(s.epgChanges).toBe(9);
    expect(s.logoChanges).toBe(1);
    // Previously: "10 pending changes: 9 EPG assignments" — the 10th unnamed.
    expect(s.totalChanges).toBe(10);
  });

  it('names a group move and a stream-profile change', () => {
    const { view } = renderEditMode([makeChannel(1, 'A'), makeChannel(2, 'B')]);

    act(() => {
      view.result.current.stageUpdateChannel(1, { channel_group_id: 12 }, 'Moved');
      view.result.current.stageUpdateChannel(2, { stream_profile_id: 6 }, 'Profile');
    });

    const s = view.result.current.summary;
    expect(s.groupMoves).toBe(1);
    expect(s.streamProfileChanges).toBe(1);
    expect(s.totalChanges).toBe(2);
  });

  it('counts an unrecognised field rather than losing it', () => {
    const { view } = renderEditMode([makeChannel(1, 'A')]);

    act(() => {
      view.result.current.stageUpdateChannel(
        1,
        { hidden_from_output: true } as Partial<Channel>,
        'Hidden'
      );
    });

    const s = view.result.current.summary;
    expect(s.otherChannelChanges).toBe(1);
    expect(s.totalChanges).toBe(1);
  });

  it('keeps the headline equal to the sum for a mixed batch', () => {
    const { view } = renderEditMode([makeChannel(1, 'A'), makeChannel(2, 'B')]);

    act(() => {
      view.result.current.stageCreateChannel('New', 3, undefined, 'Fresh Group');
      view.result.current.stageAddStream(1, 10, 'add');
      view.result.current.stageRemoveStream(2, 11, 'remove');
      view.result.current.stageReorderStreams(1, [10], 'reorder');
      view.result.current.stageUpdateChannel(1, { name: 'A2', channel_number: 9 }, 'rename+renumber');
      view.result.current.stageDeleteChannel(2, 'delete');
      view.result.current.stageRenameChannelGroup(4, 'Renamed', 'rename group');
      view.result.current.stageDeleteChannelGroup(5, 'delete group');
    });

    const s = view.result.current.summary;
    const renderedLines =
      s.channelNumberChanges + s.channelNameChanges + s.streamsAdded + s.streamsRemoved +
      s.streamsReordered + s.epgChanges + s.gracenoteIdChanges + s.logoChanges +
      s.streamProfileChanges + s.groupMoves + s.otherChannelChanges + s.newChannels +
      s.newGroups + s.deletedChannels + s.deletedGroups + s.renamedGroups;
    expect(s.totalChanges).toBe(renderedLines);
  });
});
