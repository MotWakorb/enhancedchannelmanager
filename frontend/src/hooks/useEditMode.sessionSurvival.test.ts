/**
 * Staged Edit Mode work survives a dead session (epic
 * enhancedchannelmanager-r93hq, session-expiry follow-up).
 *
 * THE PATH THAT CANNOT BE GUARDED. Every other exit from Edit Mode asks first.
 * A session expiry does not exit Edit Mode — it removes the app: `useAuth`
 * clears the user, `ProtectedRoute` renders `<LoginPage />`, `App` unmounts,
 * and the ledger, which is React state inside `useEditMode`, is gone. There is
 * no dialog to raise, and Apply is impossible anyway because the session that
 * would authorise it is already dead.
 *
 * WHAT THIS FILE PROVES, IN THE ORDER IT MATTERS.
 *
 *  1. A ledger belongs to the operator who staged it, and a different operator
 *     is offered NOTHING. This is the single most dangerous failure available
 *     here: two operators share a workstation, A's staged channel edits are
 *     handed to B, and B Applies them under B's credentials with the journal
 *     attributing every change to B.
 *  2. Restoring rebuilds the SAME ledger — the change count, the working copy,
 *     the derived groups and side effects, the temp-id allocators — and the
 *     restored operations commit through the same bulk path with the same
 *     accounting. A restore that reaches a different commit path would be a
 *     second implementation of Apply, which is the thing this epic spent
 *     twenty-five commits removing.
 *  3. Nothing the epic established is weakened: Undo still reaches a restored
 *     operation, Discard still takes it back, and Discard also destroys the
 *     persisted copy so it cannot come back from the dead on the next mount.
 *
 * The staleness half — which operations are still applicable at all — is
 * `planLedgerRestore`, proven in `src/utils/stagedLedgerStorage.test.ts`. This
 * hook takes the plan's verdict; it does not second-guess it.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useEditMode } from './useEditMode';
import type { Channel, StagedOperation } from '../types';
import * as api from '../services/api';
import {
  STAGED_LEDGER_STORAGE_KEY,
  STAGED_LEDGER_FORMAT_VERSION,
  readStagedLedger,
  type PersistedStagedLedger,
} from '../utils/stagedLedgerStorage';

const OPERATOR_A = 'local#7';
const OPERATOR_B = 'dispatcharr#7';

function makeChannel(id: number, name: string, streams: number[] = []): Channel {
  return {
    id,
    channel_number: id,
    name,
    channel_group_id: null,
    tvg_id: null,
    tvc_guide_stationid: null,
    epg_data_id: null,
    streams,
    stream_profile_id: null,
    uuid: `uuid-${id}`,
    logo_id: null,
    auto_created: false,
    auto_created_by: null,
    auto_created_by_name: null,
  };
}

const CHANNELS = [makeChannel(1, 'Alpha', [11, 12]), makeChannel(2, 'Bravo')];

function setup(operatorKey = OPERATOR_A, channels = CHANNELS) {
  return renderHook(() =>
    useEditMode({ channels, onChannelsChange: vi.fn(), operatorKey }),
  );
}

/** What the store holds right now, or null. */
function storedLedger(): PersistedStagedLedger | null {
  const raw = window.sessionStorage.getItem(STAGED_LEDGER_STORAGE_KEY);
  return raw === null ? null : (JSON.parse(raw) as PersistedStagedLedger);
}

/** Write a ledger directly, as a previous session's tab would have left it. */
function seedLedger(operatorKey: string, operations: StagedOperation[], undoGroups: string[][]) {
  window.sessionStorage.setItem(STAGED_LEDGER_STORAGE_KEY, JSON.stringify({
    version: STAGED_LEDGER_FORMAT_VERSION,
    operatorKey,
    savedAt: Date.now(),
    operations,
    undoGroups,
  }));
}

beforeEach(() => {
  window.sessionStorage.clear();
  vi.spyOn(api, 'getChannels').mockResolvedValue({ count: 0, next: null, previous: null, results: [] });
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ================================================= the ledger reaches the store

describe('staged work is persisted as it is staged', () => {
  it('round-trips v3 bulk-create intent and assignments through a dead session', () => {
    const view = setup();
    act(() => view.result.current.enterEditMode());
    act(() => {
      view.result.current.stageCreateChannelWithStreams({
        name: 'Gamma',
        streamIds: [21, 22],
        channelNumber: 3,
      });
    });

    const stored = storedLedger()!;
    expect(stored.version).toBe(3);
    expect(stored.operations[0].apiCall).toEqual(expect.objectContaining({
      type: 'createChannel',
      expectedStreamIds: [21, 22],
    }));
    act(() => view.unmount());

    const returning = setup();
    const pending = returning.result.current.pendingRestore!;
    expect(pending.operations).toHaveLength(3);
    expect(pending.operations[0].apiCall).toEqual(expect.objectContaining({
      expectedStreamIds: [21, 22],
    }));

    act(() => returning.result.current.restoreStagedLedger(pending));
    expect(returning.result.current.summary.newChannels).toBe(1);
    expect(returning.result.current.summary.streamsAdded).toBe(2);
    expect(storedLedger()!.operations[0].apiCall).toEqual(expect.objectContaining({
      expectedStreamIds: [21, 22],
    }));
  });

  it('writes the operation queue to sessionStorage under the operator key', () => {
    const view = setup();
    act(() => view.result.current.enterEditMode());
    act(() => view.result.current.stageUpdateChannel(1, { name: 'Alpha HD' }, 'Rename "Alpha"'));

    const stored = storedLedger();
    expect(stored).not.toBeNull();
    expect(stored!.operatorKey).toBe(OPERATOR_A);
    expect(stored!.operations).toHaveLength(1);
    expect(stored!.operations[0].apiCall).toEqual({
      type: 'updateChannel', channelId: 1, data: { name: 'Alpha HD' },
    });
  });

  it('persists the undo grouping, so a batched change is still ONE change on return', () => {
    const view = setup();
    act(() => view.result.current.enterEditMode());
    act(() => {
      view.result.current.startBatch('Enable 2 channels in "Default"');
      view.result.current.stageSetProfileMembership(1, [1, 2], true, 'Enable in "Default"');
      view.result.current.endBatch();
    });

    expect(view.result.current.stagedOperationCount).toBe(1);
    const stored = storedLedger()!;
    expect(stored.operations).toHaveLength(2);
    expect(stored.undoGroups).toHaveLength(1);
    expect(stored.undoGroups[0]).toHaveLength(2);
  });

  it('removes the persisted ledger when the operator discards', () => {
    const view = setup();
    act(() => view.result.current.enterEditMode());
    act(() => view.result.current.stageUpdateChannel(1, { name: 'Alpha HD' }, 'Rename'));
    expect(storedLedger()).not.toBeNull();

    act(() => view.result.current.discard());
    expect(storedLedger()).toBeNull();
  });

  it('removes the persisted ledger when the last staged change is undone', () => {
    const view = setup();
    act(() => view.result.current.enterEditMode());
    act(() => view.result.current.stageUpdateChannel(1, { name: 'Alpha HD' }, 'Rename'));
    act(() => view.result.current.localUndo());
    expect(storedLedger()).toBeNull();
  });
});

// ======================= a decision outlives the tree that was asked to make it

/**
 * The three tests below are about ONE mechanism, and it is not persistence —
 * it is the difference between a decision and an accident.
 *
 * `App.tsx` defers a sign-out behind the Edit Mode exit dialog, and both
 * answers — Discard and Apply All — call `completeDeferredExit()`, which
 * invokes the stored sign-out callback in the same tick. Signing out flips
 * auth state, `ProtectedRoute` renders `<LoginPage />` instead of `<App />`,
 * and `App.tsx` states the consequence outright: "nothing here gets another
 * render to notice". A ledger cleared by a passive effect is therefore not
 * cleared at all on those two paths. The operator's next sign-in in the same
 * tab is offered work they explicitly threw away, or work that has already
 * been applied — and re-applying a create makes a SECOND channel.
 *
 * Each test unmounts inside the same `act` as the decision, which is what a
 * `ProtectedRoute` swap does: no further render, no effect flush.
 *
 * The third test is the counterweight and matters just as much. An unmount
 * that follows NO decision — an expired token, a 401, a closed tab — must
 * leave the ledger untouched, because offering it back is the entire point of
 * the mechanism. A "fix" that cleared on unmount would pass the first two and
 * destroy the feature.
 */
describe('an operator decision survives the unmount it triggers', () => {
  it('Discard destroys the ledger before a deferred sign-out can tear the tree down', () => {
    const view = setup();
    act(() => view.result.current.enterEditMode());
    act(() => view.result.current.stageUpdateChannel(1, { name: 'Alpha HD' }, 'Rename "Alpha"'));
    expect(storedLedger()).not.toBeNull();

    act(() => {
      view.result.current.discard();
      view.unmount();
    });

    expect(storedLedger()).toBeNull();
    expect(setup(OPERATOR_A).result.current.pendingRestore).toBeNull();
  });

  it('a create Applied through to the server is not offered back to the next session', async () => {
    const bulkCommit = vi.spyOn(api, 'bulkCommit').mockResolvedValue({
      success: true, operationsApplied: 1, operationsFailed: 0, errors: [],
      tempIdMap: { '-1': 99 }, groupIdMap: {},
    });
    vi.spyOn(api, 'getChannels').mockResolvedValue({
      count: 0, next: null, previous: null, results: [],
    } as never);

    const view = setup();
    act(() => view.result.current.enterEditMode());
    act(() => { view.result.current.stageCreateChannel('Gamma', 3); });
    expect(storedLedger()).not.toBeNull();

    // Read the store the instant `commit` returns — before act flushes the
    // effect — because that is the only moment the sign-out leaves.
    let ledgerWhenCommitReturned: string | null = 'not read';
    await act(async () => {
      await view.result.current.commit();
      ledgerWhenCommitReturned = window.sessionStorage.getItem(STAGED_LEDGER_STORAGE_KEY);
      view.unmount();
    });

    expect(bulkCommit).toHaveBeenCalled();
    expect(ledgerWhenCommitReturned).toBeNull();
    expect(storedLedger()).toBeNull();
    expect(setup(OPERATOR_A).result.current.pendingRestore).toBeNull();
  });

  it('a session that dies with NO decision keeps its ledger, and offers it back', () => {
    const view = setup();
    act(() => view.result.current.enterEditMode());
    act(() => view.result.current.stageUpdateChannel(1, { name: 'Alpha HD' }, 'Rename "Alpha"'));

    // The expiry path: no Discard, no Apply, the tree simply goes away.
    act(() => view.unmount());

    expect(storedLedger()).not.toBeNull();
    const returning = setup(OPERATOR_A);
    expect(returning.result.current.pendingRestore).not.toBeNull();
    expect(returning.result.current.pendingRestore!.operations).toHaveLength(1);
  });
});

// ========================================== THE IDENTITY GUARD, AT HOOK LEVEL

describe('a restored ledger is bound to the operator who staged it', () => {
  const previousSession = (): StagedOperation[] => ([{
    id: 'op-a1',
    timestamp: Date.now(),
    description: 'Rename "Alpha"',
    apiCall: { type: 'updateChannel', channelId: 1, data: { name: 'Alpha HD' } },
    beforeSnapshot: [],
    afterSnapshot: [],
  }]);

  it('offers operator A their own ledger', () => {
    seedLedger(OPERATOR_A, previousSession(), [['op-a1']]);
    const view = setup(OPERATOR_A);
    expect(view.result.current.pendingRestore?.operations).toHaveLength(1);
  });

  it('offers operator B NOTHING, and destroys A\'s ledger rather than leaving it in the tab', () => {
    seedLedger(OPERATOR_A, previousSession(), [['op-a1']]);

    const view = setup(OPERATOR_B);

    expect(view.result.current.pendingRestore).toBeNull();
    expect(window.sessionStorage.getItem(STAGED_LEDGER_STORAGE_KEY)).toBeNull();
    // And nothing of A's work is reachable through the hook either.
    expect(view.result.current.stagedOperationCount).toBe(0);
    expect(view.result.current.isEditMode).toBe(false);
  });
});

// ============================================== restoring rebuilds the session

describe('restoring a ledger rebuilds the Edit Mode session it came from', () => {
  /** Two channel edits and a new group, as a previous session left them. */
  function previousSession(): { operations: StagedOperation[]; undoGroups: string[][] } {
    const operations: StagedOperation[] = [
      {
        id: 'op-1', timestamp: 1, description: 'Create group "Drill Locals"',
        apiCall: { type: 'createGroup', name: 'Drill Locals', tempGroupId: -1000 },
        beforeSnapshot: [], afterSnapshot: [],
      },
      {
        id: 'op-2', timestamp: 2, description: 'Create channel "Local 1"',
        apiCall: {
          type: 'createChannel', name: 'Local 1', channelNumber: 900,
          newGroupName: 'Drill Locals', stagedGroupId: -1000,
        },
        beforeSnapshot: [],
        afterSnapshot: [{ id: -1, channel_number: 900, name: 'Local 1', channel_group_id: -1000, streams: [] }],
      },
      {
        id: 'op-3', timestamp: 3, description: 'Rename "Alpha"',
        apiCall: { type: 'updateChannel', channelId: 1, data: { name: 'Alpha HD' } },
        beforeSnapshot: [], afterSnapshot: [],
      },
      {
        id: 'op-4', timestamp: 4, description: 'Clear stream stats',
        apiCall: { type: 'clearStreamStats', streamIds: [11, 12] },
        beforeSnapshot: [], afterSnapshot: [],
      },
    ];
    return { operations, undoGroups: [['op-1'], ['op-2'], ['op-3'], ['op-4']] };
  }

  function restored() {
    const { operations, undoGroups } = previousSession();
    seedLedger(OPERATOR_A, operations, undoGroups);
    const view = setup(OPERATOR_A);
    act(() => {
      view.result.current.restoreStagedLedger({
        operations,
        undoGroups,
        savedAt: 1_700_000_000_000,
      });
    });
    return view;
  }

  it('enters Edit Mode with the change count the operator left behind', () => {
    const view = restored();
    expect(view.result.current.isEditMode).toBe(true);
    expect(view.result.current.stagedOperationCount).toBe(4);
    expect(view.result.current.summary.totalOperations).toBe(4);
  });

  it('marks the session as restored, so the work does not read as changes just made', () => {
    const view = restored();
    expect(view.result.current.restoredFrom).toBe(1_700_000_000_000);

    const fresh = setup(OPERATOR_A);
    act(() => fresh.result.current.enterEditMode());
    expect(fresh.result.current.restoredFrom).toBeNull();
  });

  it('rebuilds the working copy: the staged rename and the staged new channel are both visible', () => {
    const view = restored();
    const displayed = view.result.current.displayChannels;
    expect(displayed.find((channel) => channel.id === 1)!.name).toBe('Alpha HD');
    const created = displayed.find((channel) => channel.id === -1);
    expect(created).toBeDefined();
    expect(created!.name).toBe('Local 1');
    expect(created!.channel_group_id).toBe(-1000);
  });

  it('rebuilds the DERIVED views from the restored operations alone', () => {
    const view = restored();
    // Derived, not persisted: the group and the side effects come back because
    // the operation queue came back.
    expect(view.result.current.stagedGroups).toEqual([
      { id: -1000, name: 'Drill Locals', channel_count: 0 },
    ]);
    expect([...view.result.current.stagedSideEffects.clearedStreamIds]).toEqual([11, 12]);
  });

  it('re-arms the temp-id allocators below the ids the ledger already used', () => {
    const view = restored();
    let nextTempId = 0;
    act(() => { nextTempId = view.result.current.stageCreateChannel('Local 2', 901); });
    // -1 is taken by the restored createChannel; a fresh allocation must not
    // collide with it, or two staged channels share an id on the wire.
    expect(nextTempId).toBe(-2);

    let nextGroupId = 0;
    act(() => { nextGroupId = view.result.current.stageCreateGroup('Drill Movies'); });
    expect(nextGroupId).toBe(-1001);

    // Restaging the SAME group name resolves to the id the ledger already has.
    let sameGroupId = 0;
    act(() => { sameGroupId = view.result.current.stageCreateGroup('Drill Locals'); });
    expect(sameGroupId).toBe(-1000);
  });

  it('recomputes each operation\'s before/after snapshots against TODAY\'s channels', () => {
    // Channel 1 was renamed on the server while the session was dead. Undoing
    // the restored rename must land on the server's current name, not on the
    // name the dead session snapshotted.
    const { operations, undoGroups } = previousSession();
    seedLedger(OPERATOR_A, operations, undoGroups);
    const view = setup(OPERATOR_A, [makeChannel(1, 'Alpha (renamed upstream)', [11, 12]), CHANNELS[1]]);
    act(() => view.result.current.restoreStagedLedger({ operations, undoGroups, savedAt: Date.now() }));

    expect(view.result.current.displayChannels.find((c) => c.id === 1)!.name).toBe('Alpha HD');
    act(() => view.result.current.localUndo()); // undo op-4, clear stats
    act(() => view.result.current.localUndo()); // undo op-3, the rename
    expect(view.result.current.displayChannels.find((c) => c.id === 1)!.name)
      .toBe('Alpha (renamed upstream)');
  });

  it('leaves Undo, Redo and Discard reaching the restored operations', () => {
    const view = restored();
    expect(view.result.current.canLocalUndo).toBe(true);

    act(() => view.result.current.localUndo());
    expect(view.result.current.stagedOperationCount).toBe(3);
    expect([...view.result.current.stagedSideEffects.clearedStreamIds]).toEqual([]);

    act(() => view.result.current.localRedo());
    expect(view.result.current.stagedOperationCount).toBe(4);
    expect([...view.result.current.stagedSideEffects.clearedStreamIds]).toEqual([11, 12]);

    act(() => view.result.current.discard());
    expect(view.result.current.isEditMode).toBe(false);
    expect(storedLedger()).toBeNull();
  });

  it('re-persists the restored ledger under the CURRENT operator', () => {
    const view = restored();
    act(() => view.result.current.stageUpdateChannel(2, { name: 'Bravo HD' }, 'Rename "Bravo"'));
    const stored = storedLedger()!;
    expect(stored.operatorKey).toBe(OPERATOR_A);
    expect(stored.operations).toHaveLength(5);
  });

  it('clears the offer once it has been taken', () => {
    const view = restored();
    expect(view.result.current.pendingRestore).toBeNull();
  });

  it('dismissing the offer destroys the persisted ledger', () => {
    const { operations, undoGroups } = previousSession();
    seedLedger(OPERATOR_A, operations, undoGroups);
    const view = setup(OPERATOR_A);
    expect(view.result.current.pendingRestore).not.toBeNull();

    act(() => view.result.current.dismissPendingRestore());

    expect(view.result.current.pendingRestore).toBeNull();
    expect(window.sessionStorage.getItem(STAGED_LEDGER_STORAGE_KEY)).toBeNull();
    expect(view.result.current.isEditMode).toBe(false);
  });

  it('restores only what it is given, so a dropped operation cannot come back', () => {
    const { operations, undoGroups } = previousSession();
    seedLedger(OPERATOR_A, operations, undoGroups);
    const view = setup(OPERATOR_A);
    // Two survivors of a four-operation ledger, as `planLedgerRestore` decided.
    const survivors = [operations[0], operations[2]];
    act(() => view.result.current.restoreStagedLedger({
      operations: survivors, undoGroups, savedAt: Date.now(),
    }));

    expect(view.result.current.stagedOperationCount).toBe(2);
    expect(view.result.current.displayChannels.some((channel) => channel.id === -1)).toBe(false);
    expect([...view.result.current.stagedSideEffects.clearedStreamIds]).toEqual([]);
    // The undo grouping is pruned with it — an entry naming only dropped
    // operations must not survive as an empty step the operator can press.
    expect(view.result.current.canLocalUndo).toBe(true);
  });
});

// ============================== restored work commits through the SAME path

describe('restored operations commit through the ordinary bulk path', () => {
  it('sends them in one Apply All with the ordinary group-by-name resolution', async () => {
    const operations: StagedOperation[] = [
      {
        id: 'op-1', timestamp: 1, description: 'Create group "Drill Locals"',
        apiCall: { type: 'createGroup', name: 'Drill Locals', tempGroupId: -1000 },
        beforeSnapshot: [], afterSnapshot: [],
      },
      {
        id: 'op-2', timestamp: 2, description: 'Rename "Alpha"',
        apiCall: { type: 'updateChannel', channelId: 1, data: { name: 'Alpha HD' } },
        beforeSnapshot: [], afterSnapshot: [],
      },
    ];
    seedLedger(OPERATOR_A, operations, [['op-1'], ['op-2']]);

    const bulkCommit = vi.spyOn(api, 'bulkCommit').mockResolvedValue({
      success: true,
      operationsApplied: 1,
      operationsFailed: 0,
      errors: [],
      tempIdMap: {},
      groupIdMap: { 'Drill Locals': 501 },
    });

    const view = setup(OPERATOR_A);
    act(() => view.result.current.restoreStagedLedger({
      operations, undoGroups: [['op-1'], ['op-2']], savedAt: Date.now(),
    }));

    await act(async () => { await view.result.current.commit(); });

    // Never a temp group id on the wire: the group travels by NAME through the
    // same `groupsToCreate` phase every other Apply All uses.
    const requests = bulkCommit.mock.calls.map((call) => call[0]);
    expect(requests.length).toBeGreaterThan(0);
    expect(requests[0].groupsToCreate).toEqual([{ name: 'Drill Locals' }]);
    expect(JSON.stringify(requests)).not.toContain('-1000');
    expect(requests.some((request) =>
      request.operations.some((operation) => operation.type === 'updateChannel'))).toBe(true);
  });

  it('destroys the persisted ledger once the commit lands', async () => {
    const operations: StagedOperation[] = [{
      id: 'op-1', timestamp: 1, description: 'Rename "Alpha"',
      apiCall: { type: 'updateChannel', channelId: 1, data: { name: 'Alpha HD' } },
      beforeSnapshot: [], afterSnapshot: [],
    }];
    seedLedger(OPERATOR_A, operations, [['op-1']]);
    vi.spyOn(api, 'bulkCommit').mockResolvedValue({
      success: true, operationsApplied: 1, operationsFailed: 0, errors: [],
      tempIdMap: {}, groupIdMap: {},
    });

    const view = setup(OPERATOR_A);
    act(() => view.result.current.restoreStagedLedger({
      operations, undoGroups: [['op-1']], savedAt: Date.now(),
    }));
    await act(async () => { await view.result.current.commit(); });

    expect(readStagedLedger(OPERATOR_A)).toBeNull();
    expect(window.sessionStorage.getItem(STAGED_LEDGER_STORAGE_KEY)).toBeNull();
  });
});
