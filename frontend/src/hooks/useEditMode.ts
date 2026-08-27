import { useState, useCallback, useRef, useEffect, useMemo } from 'react';
import type {
  Channel,
  ChannelGroup,
  ChannelSnapshot,
  EditModeState,
  StagedOperation,
  UndoEntry,
  EditModeSummary,
  CommitResult,
  CommitProgress,
  CommitOptions,
  ValidationResult,
  UseEditModeReturn,
  ApiCallSpec,
  StagedSideEffects,
  StageUpdateChannelOptions,
} from '../types';
import { EMPTY_STAGED_SIDE_EFFECTS, profileMembershipKey } from '../types/editMode';
import {
  buildFinalNumberingPlan,
  deriveAutomaticRenames,
  validateFinalNumberingPlan,
  type NumberingPlanIssue,
} from '../utils/channelNumberPlan';
import {
  applyReconcileDecisions,
  detectNumberingConflicts,
  expectedServerNumber,
  type ReconcileDecision,
  type ReconcileResult,
} from '../utils/channelNumberConcurrency';
import * as api from '../services/api';
import { createSnapshot } from '../utils/channelSnapshot';
import { generateId } from '../utils/idGenerator';
import { logger } from '../utils/logger';
import {
  clearStagedLedger,
  readStagedLedger,
  saveStagedLedger,
  type PersistedStagedLedger,
  type RestoreStagedLedgerInput,
} from '../utils/stagedLedgerStorage';

// Compute which channels are modified by comparing working copy to baseline
function computeModifiedChannelIds(
  workingCopy: Channel[],
  baselineSnapshot: ChannelSnapshot[]
): Set<number> {
  const modified = new Set<number>();

  // Check each channel in working copy against baseline
  for (const channel of workingCopy) {
    // New channels (negative IDs) are always modified
    if (channel.id < 0) {
      modified.add(channel.id);
      continue;
    }

    const baseline = baselineSnapshot.find((s) => s.id === channel.id);
    if (!baseline) {
      // Channel exists in working copy but not baseline - it's new
      modified.add(channel.id);
      continue;
    }

    // Compare relevant fields
    if (
      channel.channel_number !== baseline.channel_number ||
      channel.name !== baseline.name ||
      channel.channel_group_id !== baseline.channel_group_id ||
      JSON.stringify(channel.streams) !== JSON.stringify(baseline.streams)
    ) {
      modified.add(channel.id);
    }
  }

  return modified;
}

/**
 * Working-copy view of the staged operations that do not touch a Channel record
 * (bead enhancedchannelmanager-kz089, fix round 2).
 *
 * Derived from `stagedOperations` rather than accumulated alongside it. That is
 * the whole point: the operation queue is the collection `stageOperation`,
 * `localUndo`, `localRedo` and `discard` already maintain, so deriving from it
 * gives all four correct behaviour with no second reducer to keep in step. The
 * two effects that previously showed on screen did so by mutating
 * component-local state, which none of those four could reach.
 *
 * Later operations win, so staging enable-then-disable for the same channel
 * reads as disabled, exactly as the server will see it.
 */
export function deriveStagedSideEffects(
  stagedOperations: StagedOperation[]
): StagedSideEffects {
  const profileMembership = new Map<string, boolean>();
  const restoredGroupIds = new Set<number>();
  const clearedStreamIds = new Set<number>();

  for (const operation of stagedOperations) {
    const { apiCall } = operation;
    switch (apiCall.type) {
      case 'setProfileMembership':
        profileMembership.set(
          profileMembershipKey(apiCall.profileId, apiCall.channelId),
          apiCall.enabled
        );
        break;
      case 'restoreChannelGroup':
        restoredGroupIds.add(apiCall.groupId);
        break;
      case 'clearStreamStats':
        for (const streamId of apiCall.streamIds) {
          clearedStreamIds.add(streamId);
        }
        break;
      default:
        break;
    }
  }

  return { profileMembership, restoredGroupIds, clearedStreamIds };
}

/**
 * The groups this Edit Mode session will create, keyed by their temp id
 * (bead enhancedchannelmanager-kz089, fix round 3).
 *
 * Derived from `stagedOperations` for the same reason
 * {@link deriveStagedSideEffects} is: the operation queue is the one collection
 * `stageOperation`, `localUndo`, `localRedo` and `discard` all maintain, so a
 * view derived from it cannot disagree with it. This used to be an accumulated
 * `Map` on the state, and `localUndo` returned `...prev` without recomputing
 * it — so undoing "Create group Sports" dropped the operation and left the
 * group in the filters, in "Create in...", and in `App.tsx`'s
 * `displayChannelGroups`.
 *
 * A group reaches this list two ways, and the second is why an accumulator was
 * hard to keep honest: a `createGroup` NAMES it, and a `createChannel` carrying
 * a `newGroupName` merely IMPLIES it. Both carry the temp id their caller
 * allocated (`ensureStagedGroupId`, which is idempotent by name), so both are
 * derivable and both resolve to the same id.
 *
 * First occurrence of a NAME wins, matching the allocator: `channel_count` is 0
 * on every entry because these groups do not exist yet and nothing downstream
 * reads the field for a staged group.
 */
export function deriveStagedGroups(
  stagedOperations: StagedOperation[]
): Map<number, ChannelGroup> {
  const groups = new Map<number, ChannelGroup>();
  const idByName = new Map<string, number>();

  const register = (name: string, tempGroupId: number) => {
    if (idByName.has(name)) return;
    idByName.set(name, tempGroupId);
    groups.set(tempGroupId, { id: tempGroupId, name, channel_count: 0 });
  };

  for (const { apiCall } of stagedOperations) {
    if (apiCall.type === 'createGroup') {
      register(apiCall.name, apiCall.tempGroupId);
    } else if (
      apiCall.type === 'createChannel' &&
      apiCall.newGroupName !== undefined &&
      apiCall.stagedGroupId !== undefined
    ) {
      register(apiCall.newGroupName, apiCall.stagedGroupId);
    }
  }

  return groups;
}

/**
 * The channels an operation's snapshots have to cover, read off the operation
 * itself.
 *
 * Every `stage*` wrapper used to hand `stageOperation` this list by hand, and
 * every one of them handed it exactly what the `apiCall` already says. That
 * duplication was harmless while staging was the only producer of operations;
 * restoring a persisted ledger is a second producer, and two producers
 * computing the same list independently is precisely how a derived value goes
 * out of step with the thing it derives from (bead …-kz089's whole fix round
 * 3). One function, both callers.
 */
export function affectedChannelIdsOf(apiCall: ApiCallSpec): number[] {
  switch (apiCall.type) {
    case 'updateChannel':
    case 'addStreamToChannel':
    case 'removeStreamFromChannel':
    case 'reorderChannelStreams':
    case 'deleteChannel':
    case 'setProfileMembership':
      return [apiCall.channelId];
    case 'bulkAssignChannelNumbers':
      return apiCall.channelIds;
    // A createChannel has no PRIOR channel to snapshot — the channel it makes
    // is picked up by the `nextTempId` arm of the after-snapshot filter — and
    // group and stream-stat operations touch no Channel record at all.
    case 'createChannel':
    case 'createGroup':
    case 'deleteChannelGroup':
    case 'renameChannelGroup':
    case 'restoreChannelGroup':
    case 'clearStreamStats':
      return [];
    default:
      return [];
  }
}

/** The working-copy channel a staged `createChannel` stands for, under `tempId`. */
function buildStagedChannel(
  apiCall: Extract<ApiCallSpec, { type: 'createChannel' }>,
  tempId: number,
): Channel {
  // An existing group's id, or the temp id allocated for `newGroupName`. That
  // allocation is idempotent by name (`ensureStagedGroupId`), so restaging the
  // same name lands on the same group with no map to consult.
  const channelGroupId: number | null =
    (apiCall.newGroupName ? apiCall.stagedGroupId : undefined)
    ?? apiCall.groupId ?? null;

  return {
    id: tempId,
    channel_number: apiCall.channelNumber ?? null,
    name: apiCall.name,
    channel_group_id: channelGroupId,
    tvg_id: apiCall.tvgId ?? null,
    tvc_guide_stationid: apiCall.tvcGuideStationId ?? null,
    epg_data_id: null,
    streams: [],
    stream_profile_id: null,
    uuid: `temp-${tempId}`,
    logo_id: apiCall.logoId ?? null,
    auto_created: false,
    auto_created_by: null,
    auto_created_by_name: null,
    _stagedLogoUrl: apiCall.logoUrl,
  };
}

// Initial state for edit mode
function createInitialState(): EditModeState {
  return {
    isActive: false,
    enteredAt: null,
    restoredFrom: null,
    baselineSnapshot: [],
    workingCopy: [],
    stagedOperations: [],
    localUndoStack: [],
    localRedoStack: [],
    modifiedChannelIds: new Set(),
    nextTempId: -1,
    tempIdMap: new Map(),
    currentBatch: null,
  };
}

export interface UseEditModeOptions {
  channels: Channel[];
  onChannelsChange: (channels: Channel[]) => void;
  onCommitComplete?: (createdGroupIds: number[]) => void;
  onError?: (message: string) => void;
  /**
   * Identity of the operator staging this work, from
   * {@link operatorLedgerKey}. Required, not optional: a persisted ledger with
   * no owner is a ledger any operator can pick up, and the whole survival
   * feature is unsafe without it.
   */
  operatorKey: string;
}

export function useEditMode({
  channels,
  onChannelsChange,
  onCommitComplete,
  onError,
  operatorKey,
}: UseEditModeOptions): UseEditModeReturn {
  const [state, setState] = useState<EditModeState>(createInitialState);
  const [isCommitting, setIsCommitting] = useState(false);

  // Track real channels for conflict detection
  const realChannelsRef = useRef<Channel[]>(channels);

  // Use a ref for next temp ID to avoid React batching issues when creating multiple channels in a loop
  const nextTempIdRef = useRef(-1);
  realChannelsRef.current = channels;

  // Use a ref for next temp group ID to avoid React batching issues
  const nextTempGroupIdRef = useRef(-1000);

  // Name -> temp group id for every group staged this session. Allocation lives
  // OUT here rather than inside a setState updater because callers need the id
  // synchronously (stageCreateGroup returns it, stageCreateChannel puts it on
  // the operation) and because React StrictMode runs updaters twice.
  const stagedGroupIdByNameRef = useRef<Map<string, number>>(new Map());

  /** Temp id for `name`, allocating one the first time the name is seen. */
  const ensureStagedGroupId = useCallback((name: string): number => {
    const existing = stagedGroupIdByNameRef.current.get(name);
    if (existing !== undefined) return existing;
    const tempGroupId = nextTempGroupIdRef.current;
    nextTempGroupIdRef.current -= 1;
    stagedGroupIdByNameRef.current.set(name, tempGroupId);
    return tempGroupId;
  }, []);

  /**
   * The groups this session will create, keyed by temp id — derived, never
   * accumulated (see {@link deriveStagedGroups}).
   *
   * Declared here rather than beside the other derived views at the bottom
   * because `validate` and `commit` close over it and their dependency arrays
   * are evaluated during render.
   */
  const stagedGroupsMap = useMemo(
    () => deriveStagedGroups(state.stagedOperations),
    [state.stagedOperations],
  );

  /**
   * A ledger this operator left behind in a previous session of this tab.
   *
   * Read once, during the first render, and BEFORE the persistence effect
   * below can run — the effect owns the stored copy from then on, and reading
   * later would race it. `readStagedLedger` destroys anything it refuses, so a
   * ledger belonging to somebody else is gone by the end of this line.
   */
  const [pendingRestore, setPendingRestore] = useState<PersistedStagedLedger | null>(
    () => readStagedLedger(operatorKey),
  );

  /**
   * True once THIS session has written a ledger.
   *
   * The persistence effect must not clear the store just because Edit Mode is
   * inactive: on mount it always is, and the pending offer above has to
   * survive a plain page reload (`sessionStorage` does; the React state does
   * not). So the effect only destroys a ledger it can see itself put there.
   */
  const ledgerWrittenRef = useRef(false);

  /**
   * Destroy this session's persisted ledger NOW, not on the next render.
   *
   * The persistence effect below also clears, and for a session that stays
   * mounted the two are the same thing. They are not the same thing when the
   * decision that emptied the staged queue ALSO tears the tree down: Discard
   * and Apply-All both run `completeDeferredExit()` in `App.tsx`, which fires
   * a deferred sign-out, and signing out flips auth state so `ProtectedRoute`
   * swaps the whole app for `LoginPage`. `App.tsx` says it plainly — "nothing
   * here gets another render to notice". The effect never runs, the ledger
   * outlives the decision, and the next sign-in as the same operator is
   * offered work they discarded, or work they already applied — which for a
   * create is a second create.
   *
   * So an operator DECISION clears synchronously and an unmount clears
   * nothing. That asymmetry is the point: a session that dies under the app —
   * an expired token, a 401 forcing logout, a closed tab — must leave the
   * ledger exactly where it is, because recovering it is what the whole
   * mechanism exists for. Only `discard`, `exitEditMode` and a commit that has
   * emptied the queue call this.
   */
  const forgetStagedLedger = useCallback(() => {
    if (!ledgerWrittenRef.current) return;
    clearStagedLedger();
    ledgerWrittenRef.current = false;
  }, []);

  // Enter edit mode - snapshot current state
  const enterEditMode = useCallback(() => {
    const snapshot = channels.map(createSnapshot);
    const workingCopy = channels.map((ch) => ({ ...ch, streams: [...ch.streams] }));

    // Reset temp ID refs
    nextTempIdRef.current = -1;
    nextTempGroupIdRef.current = -1000;
    stagedGroupIdByNameRef.current = new Map();

    // Starting fresh answers the offer: the operator chose new work over the
    // old ledger, so the ledger stops being pending and the effect below drops
    // it on the next write.
    setPendingRestore(null);

    setState({
      isActive: true,
      enteredAt: Date.now(),
      restoredFrom: null,
      baselineSnapshot: snapshot,
      workingCopy,
      stagedOperations: [],
      localUndoStack: [],
      localRedoStack: [],
      modifiedChannelIds: new Set(),
      nextTempId: -1,
      tempIdMap: new Map(),
      currentBatch: null,
    });
  }, [channels]);

  // Exit edit mode (discards changes)
  const exitEditMode = useCallback(() => {
    nextTempIdRef.current = -1; // Reset temp ID ref
    nextTempGroupIdRef.current = -1000;
    stagedGroupIdByNameRef.current = new Map();
    // Before the state reset, not after: see `forgetStagedLedger`. The caller
    // may unmount this tree in the same tick.
    forgetStagedLedger();
    setState(createInitialState());
  }, [forgetStagedLedger]);

  // Discard all staged changes
  const discard = useCallback(() => {
    nextTempIdRef.current = -1; // Reset temp ID ref
    nextTempGroupIdRef.current = -1000;
    stagedGroupIdByNameRef.current = new Map();
    // The operator said throw it away. `docs/user_guide/channels-streams/
    // channels-overview.md` promises "as if it never happened", and the
    // persistence effect cannot keep that promise when Discard is the answer
    // to a sign-out prompt (see `forgetStagedLedger`).
    forgetStagedLedger();
    setState(createInitialState());
  }, [forgetStagedLedger]);

  // Apply a staged operation to the working copy
  const applyOperationToWorkingCopy = useCallback(
    (
      workingCopy: Channel[],
      operation: StagedOperation
    ): Channel[] => {
      const { apiCall } = operation;

      switch (apiCall.type) {
        case 'updateChannel': {
          return workingCopy.map((ch) =>
            ch.id === apiCall.channelId
              ? { ...ch, ...apiCall.data }
              : ch
          );
        }

        case 'addStreamToChannel': {
          return workingCopy.map((ch) =>
            ch.id === apiCall.channelId && !ch.streams.includes(apiCall.streamId)
              ? { ...ch, streams: [...ch.streams, apiCall.streamId] }
              : ch
          );
        }

        case 'removeStreamFromChannel': {
          return workingCopy.map((ch) =>
            ch.id === apiCall.channelId
              ? { ...ch, streams: ch.streams.filter((id) => id !== apiCall.streamId) }
              : ch
          );
        }

        case 'reorderChannelStreams': {
          return workingCopy.map((ch) =>
            ch.id === apiCall.channelId
              ? { ...ch, streams: apiCall.streamIds }
              : ch
          );
        }

        case 'bulkAssignChannelNumbers': {
          const channelIdToNumber = new Map<number, number>();
          apiCall.channelIds.forEach((id, index) => {
            channelIdToNumber.set(id, (apiCall.startingNumber ?? 1) + index);
          });
          return workingCopy.map((ch) =>
            channelIdToNumber.has(ch.id)
              ? { ...ch, channel_number: channelIdToNumber.get(ch.id)! }
              : ch
          );
        }

        case 'createChannel': {
          // New channels handled separately
          return workingCopy;
        }

        case 'deleteChannel': {
          return workingCopy.filter((ch) => ch.id !== apiCall.channelId);
        }

        case 'createGroup': {
          // Group creation doesn't affect the working copy of channels
          // It's a separate entity handled at commit time
          return workingCopy;
        }

        case 'deleteChannelGroup': {
          // Group deletion doesn't affect the working copy of channels
          // It's a separate entity handled at commit time
          return workingCopy;
        }

        case 'renameChannelGroup': {
          // Group rename doesn't affect the working copy of channels
          // It's a separate entity handled at commit time
          return workingCopy;
        }

        case 'setProfileMembership':
        case 'restoreChannelGroup':
        case 'clearStreamStats': {
          // Profile membership, hidden-group state and probe stats live
          // outside the Channel record, so the CHANNEL working copy is
          // unchanged. Their working-copy representation is
          // `deriveStagedSideEffects`, which the panes read instead of the
          // server value — "counted but invisible" is a defect, not a
          // limitation (bead …-kz089, fix round 2).
          return workingCopy;
        }

        default:
          return workingCopy;
      }
    },
    []
  );

  /**
   * Rebuild an Edit Mode session from a persisted ledger.
   *
   * Every operation is REPLAYED against today's channel list rather than
   * deserialised into place: `beforeSnapshot` and `afterSnapshot` are
   * recomputed here, so Undo lands on the value the server holds now and not
   * on the value the dead session happened to see. That is the whole reason
   * the snapshots are not trusted from the store.
   *
   * Only `stagedOperations` and the undo GROUPING come out of the ledger.
   * Everything else — the staged groups, the staged side effects, the deleted
   * and renamed group views, the change summary, `modifiedChannelIds` — is
   * derived from the operation queue by the same code that derives it during
   * an ordinary session, so a restored session cannot disagree with a live one.
   *
   * The temp-id allocators are re-armed BELOW the ids the ledger already used.
   * Reallocating from -1 would hand a second staged channel an id the restored
   * ledger is already using, and both would go up in the same commit.
   */
  /**
   * Replay a list of staged operations over a channel list, rebuilding the
   * working copy and each operation's before/after snapshots as it goes.
   *
   * Shared by {@link restoreStagedLedger} and
   * {@link reconcileNumberingConflicts} (bead
   * enhancedchannelmanager-ic884.4), which need exactly the same thing for the
   * same reason: both hand the session a DIFFERENT operation list from the one
   * the working copy was built from, and a working copy that is not replayed
   * from the operations is a working copy that will disagree with them. Undo
   * has to land on the value the server holds now, not on a value some earlier
   * state happened to see.
   */
  const replayStagedOperations = useCallback((
    base: Channel[],
    operations: StagedOperation[],
  ) => {
    let workingCopy = base.map((ch) => ({ ...ch, streams: [...ch.streams] }));
    const rebuilt: StagedOperation[] = [];
    let lowestTempChannelId = 0;
    let lowestTempGroupId = -999;
    const groupIdByName = new Map<string, number>();

    for (const persisted of operations) {
      const { apiCall } = persisted;
      const affectedChannelIds = affectedChannelIdsOf(apiCall);

      const operation: StagedOperation = {
        ...persisted,
        beforeSnapshot: workingCopy
          .filter((ch) => affectedChannelIds.includes(ch.id))
          .map(createSnapshot),
        afterSnapshot: [],
      };

      workingCopy = applyOperationToWorkingCopy(workingCopy, operation);

      let tempChannelId: number | undefined;
      if (apiCall.type === 'createChannel') {
        // The id the dead session allocated, kept: other operations in this
        // same ledger reference it.
        tempChannelId = persisted.afterSnapshot[0]?.id;
        if (typeof tempChannelId !== 'number' || tempChannelId >= 0) {
          tempChannelId = lowestTempChannelId - 1;
        }
        lowestTempChannelId = Math.min(lowestTempChannelId, tempChannelId);
        workingCopy = [...workingCopy, buildStagedChannel(apiCall, tempChannelId)];
        if (apiCall.newGroupName && typeof apiCall.stagedGroupId === 'number') {
          lowestTempGroupId = Math.min(lowestTempGroupId, apiCall.stagedGroupId);
          if (!groupIdByName.has(apiCall.newGroupName)) {
            groupIdByName.set(apiCall.newGroupName, apiCall.stagedGroupId);
          }
        }
      } else if (apiCall.type === 'createGroup') {
        lowestTempGroupId = Math.min(lowestTempGroupId, apiCall.tempGroupId);
        if (!groupIdByName.has(apiCall.name)) {
          groupIdByName.set(apiCall.name, apiCall.tempGroupId);
        }
      }

      operation.afterSnapshot = workingCopy
        .filter((ch) => affectedChannelIds.includes(ch.id) || ch.id === tempChannelId)
        .map(createSnapshot);

      rebuilt.push(operation);
    }

    return { workingCopy, operations: rebuilt, lowestTempChannelId, lowestTempGroupId, groupIdByName };
  }, [applyOperationToWorkingCopy]);

  const restoreStagedLedger = useCallback(({ operations, undoGroups, savedAt }: RestoreStagedLedgerInput) => {
    const baselineSnapshot = channels.map(createSnapshot);
    const {
      workingCopy,
      operations: rebuilt,
      lowestTempChannelId,
      lowestTempGroupId,
      groupIdByName,
    } = replayStagedOperations(channels, operations);

    // Rebuild the undo stack from the persisted grouping, in the operations'
    // own order. A group naming only operations the staleness plan dropped
    // contributes no entry at all — an undo step that undoes nothing is a step
    // the operator can press to no effect.
    const operationById = new Map(rebuilt.map((operation) => [operation.id, operation]));
    const groupByOperationId = new Map<string, string[]>();
    for (const group of undoGroups) {
      for (const id of group) groupByOperationId.set(id, group);
    }
    const emittedGroups = new Set<string[]>();
    const localUndoStack: UndoEntry[] = [];
    const pushEntry = (entryOperations: StagedOperation[]) => {
      if (entryOperations.length === 0) return;
      localUndoStack.push({
        id: generateId(),
        timestamp: entryOperations[0].timestamp,
        description: entryOperations[0].description,
        operations: entryOperations,
      });
    };
    for (const operation of rebuilt) {
      const group = groupByOperationId.get(operation.id);
      if (group === undefined) {
        // Staged inside a batch the dead session never closed, so it belongs
        // to no group. It gets its own step rather than being dropped.
        pushEntry([operation]);
        continue;
      }
      if (emittedGroups.has(group)) continue;
      emittedGroups.add(group);
      pushEntry(group.map((id) => operationById.get(id)).filter((op): op is StagedOperation => op !== undefined));
    }

    nextTempIdRef.current = lowestTempChannelId - 1;
    nextTempGroupIdRef.current = lowestTempGroupId - 1;
    stagedGroupIdByNameRef.current = groupIdByName;
    setPendingRestore(null);

    setState({
      isActive: true,
      enteredAt: Date.now(),
      restoredFrom: savedAt,
      baselineSnapshot,
      workingCopy,
      stagedOperations: rebuilt,
      localUndoStack,
      localRedoStack: [],
      modifiedChannelIds: computeModifiedChannelIds(workingCopy, baselineSnapshot),
      nextTempId: lowestTempChannelId - 1,
      tempIdMap: new Map(),
      currentBatch: null,
    });
  }, [channels, replayStagedOperations]);

  /**
   * Rewrite the staged plan to carry the operator's per-conflict decisions, and
   * re-baseline the session on the lineup those decisions were made against
   * (bead enhancedchannelmanager-ic884.4).
   *
   * Three things move together, and they have to:
   *
   * * `stagedOperations` gains the acknowledgements and loses the numbers the
   *   operator surrendered — the operation queue stays the single source of
   *   truth, so the staged groups, the side effects, the change summary and
   *   the final-state preflight all follow with nothing else to update;
   * * the working copy is REPLAYED over `serverChannels`, so what the operator
   *   is looking at is the lineup Apply will actually produce rather than the
   *   one this session started from — which is the "what you were shown is
   *   what Apply produces" property, and it cannot be true if the screen still
   *   shows numbers somebody else has already changed;
   * * `baselineSnapshot` becomes `serverChannels`, because that is the state
   *   the operator has now seen and answered for. Leaving the old baseline in
   *   place would re-raise every conflict they just resolved; and a change
   *   made AFTER this fetch still differs from the new baseline, so it is
   *   still caught.
   *
   * An undo entry whose operations were all dropped contributes no step, for
   * the same reason it does not in a restore: a step that undoes nothing is a
   * step the operator can press to no effect.
   */
  const reconcileNumberingConflicts = useCallback((
    decisions: ReconcileDecision[],
    serverChannels: Channel[],
  ): ReconcileResult => {
    // Computed OUTSIDE the updater: a `setState` updater runs twice under
    // StrictMode, so anything it writes to an enclosing binding is written
    // twice, and the caller needs this value once.
    const outcome = applyReconcileDecisions(state.stagedOperations, decisions);
    setState((prev) => {
      if (!prev.isActive) return prev;
      const { workingCopy, operations: rebuilt } = replayStagedOperations(
        serverChannels,
        outcome.operations,
      );
      const survivingIds = new Set(rebuilt.map((operation) => operation.id));
      const rebuiltById = new Map(rebuilt.map((operation) => [operation.id, operation]));
      const pruneStack = (stack: UndoEntry[]): UndoEntry[] => stack
        .map((entry) => ({
          ...entry,
          operations: entry.operations
            .filter((operation) => survivingIds.has(operation.id))
            .map((operation) => rebuiltById.get(operation.id) ?? operation),
        }))
        .filter((entry) => entry.operations.length > 0);
      const baselineSnapshot = serverChannels.map(createSnapshot);
      return {
        ...prev,
        baselineSnapshot,
        workingCopy,
        stagedOperations: rebuilt,
        localUndoStack: pruneStack(prev.localUndoStack),
        localRedoStack: pruneStack(prev.localRedoStack),
        modifiedChannelIds: computeModifiedChannelIds(workingCopy, baselineSnapshot),
      };
    });
    return outcome;
  }, [state.stagedOperations, replayStagedOperations]);

  /** Refuse the offer, and destroy the ledger rather than leave it findable. */
  const dismissPendingRestore = useCallback(() => {
    clearStagedLedger();
    ledgerWrittenRef.current = false;
    setPendingRestore(null);
  }, []);

  type OperationToStage = {
    apiCall: ApiCallSpec;
    description: string;
    tempChannelId?: number;
  };

  // Stage related operations in one functional update so no render can observe
  // a bulk-created channel without the stream assignments staged beside it.
  const stageOperations = useCallback((
    operationsToStage: OperationToStage[],
    undoDescription?: string,
  ) => {
    const prepared = operationsToStage.map((entry) => ({
      ...entry,
      operationId: generateId(),
      undoId: generateId(),
      timestamp: Date.now(),
    }));
    setState((prev) => {
      if (!prev.isActive || prepared.length === 0) return prev;

      let workingCopy = prev.workingCopy;
      let nextTempId = prev.nextTempId;
      const modifiedChannelIds = new Set(prev.modifiedChannelIds);
      const staged: StagedOperation[] = [];

      for (const entry of prepared) {
        const { apiCall } = entry;
        const affectedChannelIds = affectedChannelIdsOf(apiCall);
        const tempChannelId = apiCall.type === 'createChannel'
          ? (entry.tempChannelId ?? nextTempId)
          : undefined;
        const operation: StagedOperation = {
          id: entry.operationId,
          timestamp: entry.timestamp,
          description: entry.description,
          apiCall,
          beforeSnapshot: workingCopy
            .filter((channel) => affectedChannelIds.includes(channel.id))
            .map(createSnapshot),
          afterSnapshot: [],
        };

        workingCopy = applyOperationToWorkingCopy(workingCopy, operation);
        if (apiCall.type === 'createChannel') {
          workingCopy = [...workingCopy, buildStagedChannel(apiCall, tempChannelId!)];
          nextTempId -= 1;
          modifiedChannelIds.add(tempChannelId!);
        }
        affectedChannelIds.forEach((id) => modifiedChannelIds.add(id));
        operation.afterSnapshot = workingCopy
          .filter((channel) => affectedChannelIds.includes(channel.id) || channel.id === tempChannelId)
          .map(createSnapshot);
        staged.push(operation);
      }

      return {
        ...prev,
        workingCopy,
        stagedOperations: [...prev.stagedOperations, ...staged],
        localUndoStack: prev.currentBatch !== null
          ? prev.localUndoStack
          : undoDescription === undefined
            ? [
                ...prev.localUndoStack,
                ...staged.map((operation, index): UndoEntry => ({
                  id: prepared[index].undoId,
                  timestamp: operation.timestamp,
                  description: operation.description,
                  operations: [operation],
                })),
              ]
            : [
                ...prev.localUndoStack,
                {
                  id: prepared[0].undoId,
                  timestamp: staged[0].timestamp,
                  description: undoDescription,
                  operations: staged,
                },
              ],
        localRedoStack: [],
        modifiedChannelIds,
        nextTempId,
        currentBatch: prev.currentBatch === null ? null : {
          ...prev.currentBatch,
          operations: [...prev.currentBatch.operations, ...staged],
        },
      };
    });
  }, [applyOperationToWorkingCopy]);

  const stageOperation = useCallback(
    (apiCall: ApiCallSpec, description: string) => {
      stageOperations([{ apiCall, description }]);
    },
    [stageOperations],
  );

  // Staging functions for each operation type
  const stageUpdateChannel = useCallback(
    (
      channelId: number,
      data: Partial<Channel>,
      description: string,
      options?: StageUpdateChannelOptions,
    ) => {
      stageOperation(
        {
          type: 'updateChannel',
          channelId,
          data,
          acknowledgedDuplicate: options?.acknowledgedDuplicate,
        },
        description,
      );
    },
    [stageOperation]
  );

  const stageAddStream = useCallback(
    (channelId: number, streamId: number, description: string) => {
      stageOperation({ type: 'addStreamToChannel', channelId, streamId }, description);
    },
    [stageOperation]
  );

  const stageRemoveStream = useCallback(
    (channelId: number, streamId: number, description: string) => {
      stageOperation({ type: 'removeStreamFromChannel', channelId, streamId }, description);
    },
    [stageOperation]
  );

  const stageReorderStreams = useCallback(
    (channelId: number, streamIds: number[], description: string) => {
      stageOperation({ type: 'reorderChannelStreams', channelId, streamIds }, description);
    },
    [stageOperation]
  );

  const stageBulkAssignNumbers = useCallback(
    (channelIds: number[], startingNumber: number, description: string) => {
      stageOperation({ type: 'bulkAssignChannelNumbers', channelIds, startingNumber }, description);
    },
    [stageOperation]
  );

  const stageCreateChannel = useCallback(
    (name: string, channelNumber?: number, groupId?: number, newGroupName?: string, logoId?: number, logoUrl?: string, tvgId?: string, tvcGuideStationId?: string): number => {
      // Use ref to get unique temp ID even when called in a loop (React batching issue)
      const tempId = nextTempIdRef.current;
      nextTempIdRef.current -= 1; // Decrement immediately for next call
      const stagedGroupId = newGroupName ? ensureStagedGroupId(newGroupName) : undefined;
      stageOperations([{
        apiCall: { type: 'createChannel', name, channelNumber, groupId, newGroupName, stagedGroupId, logoId, logoUrl, tvgId, tvcGuideStationId },
        description: `Create channel "${name}"`,
        tempChannelId: tempId,
      }]);
      return tempId;
    },
    [stageOperations, ensureStagedGroupId]
  );

  const stageCreateChannelWithStreams = useCallback(({
    name,
    streamIds,
    channelNumber,
    groupId,
    newGroupName,
    logoId,
    logoUrl,
    tvgId,
    tvcGuideStationId,
  }: Parameters<UseEditModeReturn['stageCreateChannelWithStreams']>[0]): number => {
    const uniqueStreamIds = [...new Set(streamIds)];
    if (uniqueStreamIds.length === 0) {
      throw new Error('A bulk-created channel requires at least one stream');
    }
    const tempChannelId = nextTempIdRef.current;
    nextTempIdRef.current -= 1;
    const stagedGroupId = newGroupName ? ensureStagedGroupId(newGroupName) : undefined;
    stageOperations([
      {
        apiCall: {
          type: 'createChannel',
          name,
          channelNumber,
          groupId,
          newGroupName,
          stagedGroupId,
          logoId,
          logoUrl,
          tvgId,
          tvcGuideStationId,
          expectedStreamIds: uniqueStreamIds,
        },
        description: `Create channel "${name}"`,
        tempChannelId,
      },
      ...uniqueStreamIds.map((streamId) => ({
        apiCall: { type: 'addStreamToChannel' as const, channelId: tempChannelId, streamId },
        description: `Assign stream to "${name}"`,
      })),
    ], `Create channel "${name}" with streams`);
    return tempChannelId;
  }, [ensureStagedGroupId, stageOperations]);

  const stageDeleteChannel = useCallback(
    (channelId: number, description: string) => {
      stageOperation({ type: 'deleteChannel', channelId }, description);
    },
    [stageOperation]
  );

  const stageCreateGroup = useCallback(
    (name: string): number => {
      const tempGroupId = ensureStagedGroupId(name);
      stageOperation(
        { type: 'createGroup', name, tempGroupId },
        `Create group "${name}"`,
      );
      return tempGroupId;
    },
    [stageOperation, ensureStagedGroupId]
  );

  const stageDeleteChannelGroup = useCallback(
    (groupId: number, description: string) => {
      stageOperation({ type: 'deleteChannelGroup', groupId }, description);
    },
    [stageOperation]
  );

  const stageRenameChannelGroup = useCallback(
    (groupId: number, newName: string, description: string) => {
      stageOperation({ type: 'renameChannelGroup', groupId, newName }, description);
    },
    [stageOperation]
  );

  /**
   * Stage a channel-profile visibility change per channel (bead
   * enhancedchannelmanager-kz089).
   *
   * One operation per channel rather than one for the selection: the wire op
   * is per channel (Dispatcharr's endpoint is per membership), and a per-
   * channel operation is what lets the change count, the exit dialog and
   * server-side consolidation treat it like every other staged channel edit.
   * The caller wraps the loop in startBatch/endBatch so Undo still reverses
   * the whole selection in one step.
   */
  const stageSetProfileMembership = useCallback(
    (profileId: number, channelIds: number[], enabled: boolean, description: string) => {
      for (const channelId of channelIds) {
        stageOperation({ type: 'setProfileMembership', profileId, channelId, enabled }, description);
      }
    },
    [stageOperation]
  );

  const stageRestoreChannelGroup = useCallback(
    (groupId: number, description: string) => {
      stageOperation({ type: 'restoreChannelGroup', groupId }, description);
    },
    [stageOperation]
  );

  const stageClearStreamStats = useCallback(
    (streamIds: number[], description: string) => {
      stageOperation({ type: 'clearStreamStats', streamIds }, description);
    },
    [stageOperation]
  );

  // Add a newly created channel to the working copy
  // This is used when a channel is created via API during edit mode
  const addChannelToWorkingCopy = useCallback(
    (channel: Channel) => {
      setState((prev) => {
        if (!prev.isActive) return prev;
        // Check if channel already exists
        if (prev.workingCopy.some((ch) => ch.id === channel.id)) {
          return prev;
        }
        return {
          ...prev,
          workingCopy: [...prev.workingCopy, { ...channel, streams: [...channel.streams] }],
        };
      });
    },
    []
  );

  // Start a batch of operations that will be grouped as a single undo entry
  const startBatch = useCallback((description: string) => {
    setState((prev) => {
      if (!prev.isActive) return prev;
      // If already in a batch, don't start a new one
      if (prev.currentBatch !== null) {
        logger.warn('startBatch called while already in a batch');
        return prev;
      }
      return {
        ...prev,
        currentBatch: {
          description,
          operations: [],
        },
      };
    });
  }, []);

  // End the current batch and create a single undo entry for all collected operations
  const endBatch = useCallback(() => {
    setState((prev) => {
      if (!prev.isActive || prev.currentBatch === null) return prev;

      // If no operations were staged during the batch, just clear it
      if (prev.currentBatch.operations.length === 0) {
        return {
          ...prev,
          currentBatch: null,
        };
      }

      // Create a single undo entry for all operations in the batch
      const undoEntry: UndoEntry = {
        id: generateId(),
        timestamp: Date.now(),
        description: prev.currentBatch.description,
        operations: prev.currentBatch.operations,
      };

      return {
        ...prev,
        localUndoStack: [...prev.localUndoStack, undoEntry],
        localRedoStack: [], // Clear redo on new batch
        currentBatch: null,
      };
    });
  }, []);

  // Local undo within edit session
  const localUndo = useCallback(() => {
    setState((prev) => {
      if (!prev.isActive || prev.localUndoStack.length === 0) return prev;

      const lastEntry = prev.localUndoStack[prev.localUndoStack.length - 1];

      // Undo all operations in the entry, in reverse order
      let newWorkingCopy = [...prev.workingCopy];
      let newStagedOperations = [...prev.stagedOperations];

      // Process operations in reverse order
      for (let i = lastEntry.operations.length - 1; i >= 0; i--) {
        const operation = lastEntry.operations[i];

        // Restore working copy from before snapshot
        for (const snapshot of operation.beforeSnapshot) {
          const index = newWorkingCopy.findIndex((ch) => ch.id === snapshot.id);
          if (index >= 0) {
            newWorkingCopy[index] = {
              ...newWorkingCopy[index],
              channel_number: snapshot.channel_number,
              name: snapshot.name,
              channel_group_id: snapshot.channel_group_id,
              streams: [...snapshot.streams],
            };
          } else if (operation.apiCall.type === 'deleteChannel') {
            // If undoing a delete, restore the channel from baseline
            const baselineChannel = prev.baselineSnapshot.find((s) => s.id === snapshot.id);
            if (baselineChannel) {
              // Find the original channel from baseline to get all fields
              const originalChannel = realChannelsRef.current.find((ch) => ch.id === snapshot.id);
              if (originalChannel) {
                newWorkingCopy = [
                  ...newWorkingCopy,
                  {
                    ...originalChannel,
                    channel_number: baselineChannel.channel_number,
                    name: baselineChannel.name,
                    channel_group_id: baselineChannel.channel_group_id,
                    streams: [...baselineChannel.streams],
                  },
                ];
              }
            }
          }
        }

        // If it was a create channel, remove the temp channel
        if (operation.apiCall.type === 'createChannel') {
          const tempId = operation.afterSnapshot[0]?.id;
          if (tempId && tempId < 0) {
            newWorkingCopy = newWorkingCopy.filter((ch) => ch.id !== tempId);
          }
        }

        // Remove from staged operations
        const operationIndex = newStagedOperations.findIndex(
          (op) => op.id === operation.id
        );
        if (operationIndex >= 0) {
          newStagedOperations = newStagedOperations.filter((_, idx) => idx !== operationIndex);
        }
      }

      // Recompute which channels are actually modified compared to baseline
      const newModifiedIds = computeModifiedChannelIds(newWorkingCopy, prev.baselineSnapshot);

      return {
        ...prev,
        workingCopy: newWorkingCopy,
        stagedOperations: newStagedOperations,
        localUndoStack: prev.localUndoStack.slice(0, -1),
        localRedoStack: [...prev.localRedoStack, lastEntry],
        modifiedChannelIds: newModifiedIds,
      };
    });
  }, []);

  // Local redo within edit session
  const localRedo = useCallback(() => {
    setState((prev) => {
      if (!prev.isActive || prev.localRedoStack.length === 0) return prev;

      const entry = prev.localRedoStack[prev.localRedoStack.length - 1];

      // Redo all operations in the entry, in order
      let newWorkingCopy = [...prev.workingCopy];
      let newStagedOperations = [...prev.stagedOperations];

      for (const operation of entry.operations) {
        // Apply operation to working copy
        newWorkingCopy = applyOperationToWorkingCopy(newWorkingCopy, operation);

        // Handle create channel
        if (operation.apiCall.type === 'createChannel') {
          const snapshot = operation.afterSnapshot[0];
          if (snapshot && snapshot.id < 0) {
            const newChannel: Channel = {
              id: snapshot.id,
              channel_number: snapshot.channel_number,
              name: snapshot.name,
              channel_group_id: snapshot.channel_group_id,
              tvg_id: null,
              tvc_guide_stationid: null,
              epg_data_id: null,
              streams: [...snapshot.streams],
              stream_profile_id: null,
              uuid: `temp-${snapshot.id}`,
              logo_id: null,
              auto_created: false,
              auto_created_by: null,
              auto_created_by_name: null,
            };
            newWorkingCopy = [...newWorkingCopy, newChannel];
          }
        }

        // Add back to staged operations
        newStagedOperations = [...newStagedOperations, operation];
      }

      // Recompute which channels are actually modified compared to baseline
      const newModifiedIds = computeModifiedChannelIds(newWorkingCopy, prev.baselineSnapshot);

      return {
        ...prev,
        workingCopy: newWorkingCopy,
        stagedOperations: newStagedOperations,
        localUndoStack: [...prev.localUndoStack, entry],
        localRedoStack: prev.localRedoStack.slice(0, -1),
        modifiedChannelIds: newModifiedIds,
      };
    });
  }, [applyOperationToWorkingCopy]);

  // Get summary of changes
  const getSummary = useCallback((): EditModeSummary => {
    const summary: EditModeSummary = {
      totalOperations: state.stagedOperations.length,
      totalChanges: 0, // Derived from the buckets below, once they are filled.
      channelsModified: state.modifiedChannelIds.size,
      streamsAdded: 0,
      streamsRemoved: 0,
      streamsReordered: 0,
      channelNumberChanges: 0,
      channelNameChanges: 0,
      epgChanges: 0,
      gracenoteIdChanges: 0,
      logoChanges: 0,
      streamProfileChanges: 0,
      groupMoves: 0,
      otherChannelChanges: 0,
      newChannels: 0,
      deletedChannels: 0,
      newGroups: 0,
      deletedGroups: 0,
      renamedGroups: 0,
      profileVisibilityChanges: 0,
      restoredGroups: 0,
      clearedStreamStats: 0,
      // Read off the SAME materialised final state the Apply preflight
      // validates and consolidation will send, so a rename nobody typed is
      // visible before Apply and every rename shown is one that will happen
      // (beads enhancedchannelmanager-ic884.5, …-ic884.2). One materialiser
      // feeding both is what stops the preview and the submission drifting.
      automaticRenames: deriveAutomaticRenames(
        buildFinalNumberingPlan(channels, state.stagedOperations),
      ),
      operationDetails: [],
    };

    // Every group this batch will create, deduplicated: those staged
    // explicitly (`createGroup`) and those implied by a createChannel's
    // `newGroupName`. One set so a group reached both ways is counted once.
    const newGroupNames = new Set<string>();
    // Those staged explicitly — they already have their own operationDetails
    // row, so no row is synthesised for them below.
    const explicitGroupNames = new Set<string>();

    for (const op of state.stagedOperations) {
      // Add to operation details
      summary.operationDetails.push({
        id: op.id,
        type: op.apiCall.type,
        description: op.description,
      });

      switch (op.apiCall.type) {
        case 'addStreamToChannel':
          summary.streamsAdded++;
          break;
        case 'removeStreamFromChannel':
          summary.streamsRemoved++;
          break;
        case 'reorderChannelStreams':
          summary.streamsReordered++;
          break;
        case 'updateChannel': {
          // Every field an Edit Channel save can carry needs a bucket here.
          // Drill run 2026-08-09-run18 exited with "10 pending changes: 9 EPG
          // assignments" because a cleared logo matched no branch and simply
          // disappeared from the summary (bead enhancedchannelmanager-75k49).
          const before = summary.channelNumberChanges + summary.channelNameChanges
            + summary.epgChanges + summary.gracenoteIdChanges + summary.logoChanges
            + summary.streamProfileChanges + summary.groupMoves;
          if (op.apiCall.data.channel_number !== undefined) {
            summary.channelNumberChanges++;
          }
          if (op.apiCall.data.name !== undefined) {
            summary.channelNameChanges++;
          }
          if (op.apiCall.data.tvg_id !== undefined || op.apiCall.data.epg_data_id !== undefined) {
            summary.epgChanges++;
          }
          if (op.apiCall.data.tvc_guide_stationid !== undefined) {
            summary.gracenoteIdChanges++;
          }
          if (op.apiCall.data.logo_id !== undefined) {
            summary.logoChanges++;
          }
          if (op.apiCall.data.stream_profile_id !== undefined) {
            summary.streamProfileChanges++;
          }
          if (op.apiCall.data.channel_group_id !== undefined) {
            summary.groupMoves++;
          }
          const after = summary.channelNumberChanges + summary.channelNameChanges
            + summary.epgChanges + summary.gracenoteIdChanges + summary.logoChanges
            + summary.streamProfileChanges + summary.groupMoves;
          if (after === before) {
            // An unrecognised field still has to be counted somewhere.
            summary.otherChannelChanges++;
          }
          break;
        }
        case 'bulkAssignChannelNumbers':
          summary.channelNumberChanges += op.apiCall.channelIds.length;
          break;
        case 'createChannel':
          summary.newChannels++;
          // Track new groups from createChannel operations
          if (op.apiCall.newGroupName) {
            newGroupNames.add(op.apiCall.newGroupName);
          }
          break;
        case 'deleteChannel':
          summary.deletedChannels++;
          break;
        case 'createGroup':
          newGroupNames.add(op.apiCall.name);
          explicitGroupNames.add(op.apiCall.name);
          break;
        case 'deleteChannelGroup':
          summary.deletedGroups++;
          break;
        case 'renameChannelGroup':
          summary.renamedGroups++;
          break;
        // The formerly-immediate actions (bead …-kz089). They have to appear
        // in the exit dialog for the same reason they had to be staged: an
        // operator deciding whether to Apply or Discard is entitled to see
        // everything the Apply will do.
        case 'setProfileMembership':
          summary.profileVisibilityChanges++;
          break;
        case 'restoreChannelGroup':
          summary.restoredGroups++;
          break;
        case 'clearStreamStats':
          summary.clearedStreamStats += op.apiCall.streamIds.length;
          break;
      }
    }

    summary.newGroups = newGroupNames.size;

    // Give every implied group a detail row, so the expanded list accounts for
    // the same groups the summary line counts.
    for (const groupName of newGroupNames) {
      if (explicitGroupNames.has(groupName)) continue;
      summary.operationDetails.unshift({
        id: `new-group-${groupName}`,
        type: 'createGroup',
        description: `Create group "${groupName}"`,
      });
    }

    // The headline the operator reads. Derived, never counted independently —
    // that is the whole point of bead enhancedchannelmanager-75k49.
    summary.totalChanges =
      summary.streamsAdded +
      summary.streamsRemoved +
      summary.streamsReordered +
      summary.channelNumberChanges +
      summary.channelNameChanges +
      summary.epgChanges +
      summary.gracenoteIdChanges +
      summary.logoChanges +
      summary.streamProfileChanges +
      summary.groupMoves +
      summary.otherChannelChanges +
      summary.newChannels +
      summary.deletedChannels +
      summary.newGroups +
      summary.deletedGroups +
      summary.renamedGroups +
      summary.profileVisibilityChanges +
      summary.restoredGroups +
      summary.clearedStreamStats;

    return summary;
    // `channels` is a dependency because the automatic-rename preview is
    // materialised against it: the same staged operation describes a different
    // final state once the server list underneath it moves.
  }, [state.stagedOperations, state.modifiedChannelIds, channels]);

  // Memoized summary - computed once per state change, not on every render call
  const summary = useMemo(() => getSummary(), [getSummary]);

  /**
   * The channel list as the server holds it RIGHT NOW.
   *
   * Every page, because a partial read would look exactly like a lineup in
   * which the missing channels no longer exist — and this list is what the
   * concurrency check and the final-state preflight both answer against, so an
   * incomplete one produces a confident wrong answer rather than an error.
   * A failure propagates: the caller has to decide what an unanswerable
   * question means, and this function must not decide it for them by returning
   * something that looks like an answer (bead enhancedchannelmanager-ic884.4).
   */
  const fetchServerChannels = useCallback(async (): Promise<Channel[]> => {
    const allChannels: Channel[] = [];
    let page = 1;
    let hasMore = true;
    while (hasMore) {
      const response = await api.getChannels({ page, pageSize: 500 });
      allChannels.push(...response.results);
      hasMore = response.next !== null;
      page++;
    }
    return allChannels;
  }, []);

  /**
   * Translate staged operations into the bulk-commit wire format.
   *
   * The wire protocol resolves not-yet-created groups BY NAME: `groupsToCreate`
   * goes up, the backend returns `groupIdMap`, and a `createChannel` carrying
   * `newGroupName` is pointed at the real id. A negative temp group id is
   * meaningless to Dispatcharr — drill run 2026-08-09-run18 staged a channel
   * into a group that was still pending in the same batch (Create in... ->
   * <pending group>), which put `groupId: -1000` on the wire verbatim and got
   * `400 {"channel_group_id":["Invalid pk \"-1000\" - object does not
   * exist."]}`. The channel was dropped and nothing was said (bead
   * enhancedchannelmanager-udq1j).
   *
   * So every reference to a staged group is rewritten here, by name:
   *
   *  - `createChannel.groupId < 0` becomes `newGroupName`, resolved in the
   *    backend's group-creation phase alongside the ops that named it directly;
   *  - `updateChannel.data.channel_group_id < 0` (a channel moved into a
   *    pending group) is left for {@link commit} to swap for the real id once
   *    `groupIdMap` comes back, because that op is sent after group creation;
   *  - a `createGroup` operation contributes its name to `groupsToCreate`
   *    instead of a separate `createGroup` wire op, so ALL group creation
   *    happens in the one phase that populates `groupIdMap`.
   *
   * A negative id that matches no staged group cannot be rewritten and is
   * returned in `unresolvedGroupRefs` for the caller to fail loudly on. That
   * path is a programming error, and the one thing it must never do is
   * silently reach Dispatcharr.
   */
  const buildBulkOperations = useCallback((
    consolidatedOps: StagedOperation[],
    stagedGroups: Map<number, ChannelGroup>,
    /**
     * The number this session believes each channel is on right now, from
     * {@link EditModeState.baselineSnapshot} or from the operator's reconcile
     * decision (bead enhancedchannelmanager-ic884.4). Travels beside `data`
     * rather than in it, because `data` is the body PATCHed to Dispatcharr and
     * this is ECM's own bookkeeping — the same arrangement, for the same
     * reason, as `acknowledgedDuplicate`.
     *
     * It closes the window between the browser READING the lineup and the
     * executor WRITING to it, which the browser's own check cannot reach. It
     * does not close it completely and nothing claims it does: there is no
     * conditional update in Dispatcharr 0.28.x to hang a real guarantee on, so
     * a change landing between the executor's own read and its PATCH is still
     * lost.
     *
     * DELIBERATELY NOT SENT FOR A RANGE ASSIGNMENT. One
     * `bulkAssignChannelNumbers` places many channels through a single upstream
     * call, so there is no per-channel write for a per-channel expectation to
     * guard. A range is covered by the browser's check only, and that gap is a
     * decision rather than an oversight.
     */
    baselineNumbers: Map<number, number | null> = new Map(),
  ) => {
    const bulkOperations: api.BulkOperation[] = [];
    const newGroupNames = new Set<string>();
    const unresolvedGroupRefs: { description: string; groupId: number }[] = [];

    /** Name of the staged group `groupId` refers to, or undefined if real/unknown. */
    const stagedGroupName = (groupId: number | null | undefined): string | undefined =>
      typeof groupId === 'number' && groupId < 0 ? stagedGroups.get(groupId)?.name : undefined;

    const noteGroupRef = (operation: StagedOperation, groupId: number | null | undefined) => {
      if (typeof groupId !== 'number' || groupId >= 0) return;
      const name = stagedGroupName(groupId);
      if (name === undefined) {
        unresolvedGroupRefs.push({ description: operation.description, groupId });
        return;
      }
      newGroupNames.add(name);
    };

    // Collect every group this batch has to create before any channel op runs
    for (const operation of consolidatedOps) {
      const { apiCall } = operation;
      if (apiCall.type === 'createChannel') {
        if (apiCall.newGroupName) {
          newGroupNames.add(apiCall.newGroupName);
        } else {
          noteGroupRef(operation, apiCall.groupId);
        }
      } else if (apiCall.type === 'updateChannel') {
        noteGroupRef(operation, apiCall.data.channel_group_id);
      } else if (apiCall.type === 'createGroup') {
        newGroupNames.add(apiCall.name);
      }
    }

    // Build operations
    for (const operation of consolidatedOps) {
      const { apiCall } = operation;

      switch (apiCall.type) {
        case 'updateChannel':
          bulkOperations.push({
            type: 'updateChannel',
            channelId: apiCall.channelId,
            data: apiCall.data,
            // See `baselineNumbers` above. Only where this operation actually
            // writes a number, and only for a real channel id: a temp id names
            // a channel this batch is creating, which nobody else can have
            // moved.
            ...(apiCall.data.channel_number !== undefined && apiCall.channelId >= 0
              ? {
                expectedNumber: {
                  number: expectedServerNumber(
                    operation,
                    apiCall.channelId,
                    baselineNumbers.get(apiCall.channelId) ?? null,
                  ),
                },
              }
              : {}),
            // ECM's own bookkeeping, beside `data` rather than in it because
            // `data` is the body PATCHed to Dispatcharr. It has to reach the
            // server or the server's copy of the final-state check answers a
            // different question than the browser's did, and reports an error
            // against a decision the operator has already made (bead
            // enhancedchannelmanager-vdxbx).
            acknowledgedDuplicate: apiCall.acknowledgedDuplicate,
          });
          break;

        case 'addStreamToChannel':
          bulkOperations.push({
            type: 'addStreamToChannel',
            channelId: apiCall.channelId,
            streamId: apiCall.streamId,
          });
          break;

        case 'removeStreamFromChannel':
          bulkOperations.push({
            type: 'removeStreamFromChannel',
            channelId: apiCall.channelId,
            streamId: apiCall.streamId,
          });
          break;

        case 'reorderChannelStreams':
          bulkOperations.push({
            type: 'reorderChannelStreams',
            channelId: apiCall.channelId,
            streamIds: apiCall.streamIds,
          });
          break;

        case 'bulkAssignChannelNumbers':
          bulkOperations.push({
            type: 'bulkAssignChannelNumbers',
            channelIds: apiCall.channelIds,
            startingNumber: apiCall.startingNumber,
          });
          break;

        case 'createChannel': {
          const tempId = operation.afterSnapshot[0]?.id ?? -1;
          logger.debug(`buildBulkOperations createChannel: name="${apiCall.name}", tvgId=${apiCall.tvgId}, tvcGuideStationId=${apiCall.tvcGuideStationId}`);
          // A staged target group travels as a NAME, never as its temp id.
          const pendingGroupName = apiCall.newGroupName ?? stagedGroupName(apiCall.groupId);
          bulkOperations.push({
            type: 'createChannel',
            tempId: tempId,
            name: apiCall.name,
            channelNumber: apiCall.channelNumber,
            groupId: pendingGroupName !== undefined ? undefined : apiCall.groupId,
            newGroupName: pendingGroupName,
            logoId: apiCall.logoId,
            logoUrl: apiCall.logoUrl,
            tvgId: apiCall.tvgId,
            tvcGuideStationId: apiCall.tvcGuideStationId,
            expectedStreamIds: apiCall.expectedStreamIds,
            // See the updateChannel arm: a created channel can land on an
            // occupied number just as an edited one can.
            acknowledgedDuplicate: apiCall.acknowledgedDuplicate,
          });
          break;
        }

        case 'deleteChannel':
          bulkOperations.push({
            type: 'deleteChannel',
            channelId: apiCall.channelId,
          });
          break;

        case 'createGroup':
          // Already collected into groupsToCreate above; emitting a second
          // createGroup wire op would run it in a later request whose
          // groupIdMap the channel ops can no longer see.
          break;

        case 'deleteChannelGroup':
          bulkOperations.push({
            type: 'deleteChannelGroup',
            groupId: apiCall.groupId,
          });
          break;

        case 'renameChannelGroup':
          bulkOperations.push({
            type: 'renameChannelGroup',
            groupId: apiCall.groupId,
            newName: apiCall.newName,
          });
          break;

        // Formerly-immediate actions, now staged (bead …-kz089). They carry no
        // group or temp-id references, so they need no rewriting here.
        case 'setProfileMembership':
          bulkOperations.push({
            type: 'setProfileMembership',
            profileId: apiCall.profileId,
            channelId: apiCall.channelId,
            enabled: apiCall.enabled,
          });
          break;

        case 'restoreChannelGroup':
          bulkOperations.push({
            type: 'restoreChannelGroup',
            groupId: apiCall.groupId,
          });
          break;

        case 'clearStreamStats':
          bulkOperations.push({
            type: 'clearStreamStats',
            streamIds: apiCall.streamIds,
          });
          break;
      }
    }

    const groupsToCreate = Array.from(newGroupNames).map((name) => ({ name }));
    return { bulkOperations, groupsToCreate, newGroupNames, unresolvedGroupRefs };
  }, []);

  // Validate staged operations without executing
  const validate = useCallback(async (): Promise<ValidationResult> => {
    if (!state.isActive || state.stagedOperations.length === 0) {
      return { passed: true, issues: [] };
    }

    const { bulkOperations, groupsToCreate, unresolvedGroupRefs } =
      buildBulkOperations(state.stagedOperations, stagedGroupsMap);

    if (unresolvedGroupRefs.length > 0) {
      return {
        passed: false,
        issues: unresolvedGroupRefs.map((ref) => ({
          type: 'invalid_operation' as const,
          severity: 'error' as const,
          message: `"${ref.description}" targets a channel group (${ref.groupId}) that is not staged in this session.`,
        })),
      };
    }

    try {
      const response = await api.bulkCommit({
        operations: bulkOperations,
        groupsToCreate: groupsToCreate.length > 0 ? groupsToCreate : undefined,
        validateOnly: true,
        consolidate: true,
      });

      return {
        passed: response.validationPassed ?? true,
        issues: response.validationIssues ?? [],
      };
    } catch (err) {
      logger.error('Validation failed:', err);
      return {
        passed: false,
        issues: [{
          type: 'invalid_operation',
          severity: 'error',
          message: 'Validation request failed: ' + (err instanceof Error ? err.message : 'Unknown error'),
        }],
      };
    }
  }, [state.isActive, state.stagedOperations, stagedGroupsMap, buildBulkOperations]);

  // Commit all staged operations to server
  const commit = useCallback(async (
    onProgress?: (progress: CommitProgress) => void,
    options?: CommitOptions
  ): Promise<CommitResult> => {
    if (!state.isActive || state.stagedOperations.length === 0) {
      return {
        success: true,
        operationsApplied: 0,
        operationsFailed: 0,
        errors: [],
        updatedChannels: channels,
      };
    }

    const result: CommitResult = {
      success: true,
      operationsApplied: 0,
      operationsFailed: 0,
      errors: [],
      updatedChannels: [],
    };

    const assignmentsByTempId = new Map<number, number[]>();
    const expectedCreates = new Map<number, StagedOperation>();
    const invalidReorders: { operation: StagedOperation; tempId: number }[] = [];
    for (const operation of state.stagedOperations) {
      const { apiCall } = operation;
      if (apiCall.type === 'createChannel') {
        const tempId = operation.afterSnapshot[0]?.id;
        assignmentsByTempId.set(tempId, []);
        if (apiCall.expectedStreamIds !== undefined) expectedCreates.set(tempId, operation);
        continue;
      }
      if (!('channelId' in apiCall) || !assignmentsByTempId.has(apiCall.channelId)) continue;
      const assigned = assignmentsByTempId.get(apiCall.channelId)!;
      if (apiCall.type === 'addStreamToChannel') {
        if (!assigned.includes(apiCall.streamId)) assigned.push(apiCall.streamId);
      } else if (apiCall.type === 'removeStreamFromChannel') {
        const index = assigned.indexOf(apiCall.streamId);
        if (index !== -1) assigned.splice(index, 1);
      } else if (apiCall.type === 'reorderChannelStreams') {
        const isPermutation =
          apiCall.streamIds.length === assigned.length
          && new Set(apiCall.streamIds).size === apiCall.streamIds.length
          && apiCall.streamIds.every((streamId) => assigned.includes(streamId));
        if (isPermutation) {
          assignmentsByTempId.set(apiCall.channelId, [...apiCall.streamIds]);
        } else {
          invalidReorders.push({ operation, tempId: apiCall.channelId });
        }
      }
    }
    const incompleteBulkCreates = Array.from(expectedCreates, ([tempId, operation]) => {
      const expectedStreamIds = operation.apiCall.type === 'createChannel'
        ? operation.apiCall.expectedStreamIds ?? []
        : [];
      const assigned = assignmentsByTempId.get(tempId) ?? [];
      const missing = expectedStreamIds.filter((streamId) => !assigned.includes(streamId));
      return { operation, tempId, missing };
    }).filter(({ missing }) => missing.length > 0);
    if (incompleteBulkCreates.length > 0 || invalidReorders.length > 0) {
      result.success = false;
      result.operationsFailed = incompleteBulkCreates.length + invalidReorders.length;
      result.errors = [
        ...incompleteBulkCreates.map(({ operation, tempId, missing }) => ({
        operationId: operation.id,
        operationType: 'createChannel',
        channelId: tempId,
        channelName: operation.apiCall.type === 'createChannel' ? operation.apiCall.name : undefined,
        error: `Bulk-created channel is missing ${missing.length} expected stream assignment${missing.length === 1 ? '' : 's'}; nothing was applied`,
        })),
        ...invalidReorders.map(({ operation, tempId }) => ({
          operationId: operation.id,
          operationType: 'reorderChannelStreams',
          channelId: tempId,
          error: 'Temp-channel stream order is not a permutation at this operation position; nothing was applied',
        })),
      ];
      result.updatedChannels = channels;
      onError?.('Nothing was applied: a bulk-created channel has an invalid stream plan. Your changes are still pending.');
      return result;
    }

    /**
     * Refuse the whole Apply when the PROPOSED FINAL STATE is illegal, before
     * anything is mutated (bead enhancedchannelmanager-ic884.2).
     *
     * Per-operation validation cannot see this. Every edit in "move ESPN off
     * 5, move TNT to 5, move AMC to 5" is individually legal against the
     * server the operator is looking at; only the three of them together put
     * two channels on one number. So the check is over the materialised final
     * state — server list plus every staged create, edit, delete and range —
     * and it runs here, above the first request, rather than inside the
     * per-batch loop, because the creates go up in their own call and a batch
     * that fails halfway has already written.
     *
     * The backend runs the same check on its own (`backend/
     * channel_number_plan.py`) for callers that never touch this hook. Neither
     * is the other's safety net: this one is what gives the operator the staged
     * operations to fix, which the server cannot name because it never saw them
     * as operations the operator recognises.
     */
    /**
     * The lineup as the server holds it at this instant, read BEFORE anything
     * is written (bead enhancedchannelmanager-ic884.4).
     *
     * Two questions depend on it and neither can be answered from the browser's
     * own copy:
     *
     * 1. Has anyone moved a channel this session is about to renumber, since
     *    this session's baseline was captured? Only the server knows.
     * 2. Has a channel APPEARED on a number this session proposes? The
     *    final-state preflight below is run against this list rather than
     *    against the `channels` prop for exactly that reason — a channel
     *    created by another operator after this session loaded is invisible in
     *    the prop and would sail through the duplicate check.
     *
     * A READ THAT FAILED IS NOT A CLEAN BILL OF HEALTH. Nothing is applied,
     * and the operator is told the check could not run. That is the opposite
     * of what the backend's own preflight does for this caller — it reports and
     * proceeds, because the browser holds the whole plan and has already
     * validated it — and the difference is the point: the browser can
     * re-validate its own plan without the server, and it cannot answer "did
     * somebody else change this?" without it at all.
     */
    let serverChannels: Channel[];
    try {
      serverChannels = await fetchServerChannels();
    } catch (err) {
      logger.error('[EditMode] Could not read the channel list before Apply:', err);
      result.success = false;
      result.validationPassed = false;
      result.validationIssues = [{
        type: 'invalid_operation',
        severity: 'error',
        message:
          'The channel list could not be read, so ECM could not check whether anyone else ' +
          'changed these channel numbers. Nothing was applied. Try again once Dispatcharr ' +
          'is reachable.',
      }];
      result.updatedChannels = channels;
      onError?.(result.validationIssues[0].message + ' Your changes are still pending.');
      return result;
    }

    const conflicts = detectNumberingConflicts({
      baseline: state.baselineSnapshot,
      server: serverChannels,
      operations: state.stagedOperations,
    });
    if (conflicts.length > 0) {
      logger.warn('[EditMode] Apply held for concurrent numbering changes:', conflicts);
      result.success = false;
      result.numberingConflicts = conflicts;
      result.serverChannels = serverChannels;
      result.updatedChannels = channels;
      return result;
    }

    const numberingIssues: NumberingPlanIssue[] = validateFinalNumberingPlan(
      // The LATEST server list plus the staged plan, not the working copy. The
      // working copy is already the answer, but it carries no record of which
      // number a channel started on, and "did this session put a channel
      // here?" is the whole difference between a duplicate the operator made
      // and one the lineup already had.
      buildFinalNumberingPlan(serverChannels, state.stagedOperations),
    );
    if (numberingIssues.length > 0) {
      logger.error('[EditMode] Final-state numbering preflight refused Apply:', numberingIssues);
      result.success = false;
      result.validationPassed = false;
      result.validationIssues = numberingIssues.map((issue) => ({
        type: issue.type,
        severity: issue.severity,
        message:
          issue.operationDescriptions.length > 0
            ? `${issue.message} (from: ${issue.operationDescriptions.join('; ')})`
            : issue.message,
        channelId: issue.channelIds[0],
      }));
      result.updatedChannels = channels;
      onError?.(
        `Nothing was applied: ${numberingIssues.length} numbering ` +
        `conflict${numberingIssues.length !== 1 ? 's' : ''} in the final result. ` +
        `${result.validationIssues[0].message} Your changes are still pending.`,
      );
      return result;
    }

    setIsCommitting(true);

    // Server-side consolidation: operations are sent raw, backend deduplicates
    const consolidatedOps = state.stagedOperations;

    /**
     * Correlation id for every bulk-commit request this Apply All makes
     * (bead enhancedchannelmanager-r9py9).
     *
     * The commit below is not one request: creates and their temp-channel
     * assignments go first in one call, then the remaining operations in
     * batches of 200. Each call used to mint its own journal batch, so one Edit
     * Mode session scattered across several unrelated batches and the operator
     * guide's "one Bulk Commit row plus the individual rows" could not be true.
     * Sent as `X-ECM-Batch-Id`; the backend stamps it on the summary AND on the
     * per-channel rows.
     *
     * Capped to the 50 characters the backend stores.
     */
    const commitBatchId = `ui-${generateId()}`.slice(0, 50);

    // Copy the temp ID map for tracking new channel IDs
    const tempIdMap = new Map<number, number>();
    // Map for new group names to their created IDs
    const newGroupIdMap = new Map<string, number>();

    // Collect new group names from createChannel operations
    const newGroupNames = new Set<string>();
    for (const operation of consolidatedOps) {
      if (operation.apiCall.type === 'createChannel') {
        if (operation.apiCall.newGroupName) {
          newGroupNames.add(operation.apiCall.newGroupName);
        }
      }
    }

    // Build summary of operation types for better progress feedback
    const opCounts = {
      createChannel: 0,
      updateChannel: 0,
      deleteChannel: 0,
      addStream: 0,
      removeStream: 0,
      reorderStreams: 0,
      assignNumbers: 0,
      createGroup: 0,
      deleteGroup: 0,
      renameGroup: 0,
    };

    for (const op of consolidatedOps) {
      switch (op.apiCall.type) {
        case 'createChannel': opCounts.createChannel++; break;
        case 'updateChannel': opCounts.updateChannel++; break;
        case 'deleteChannel': opCounts.deleteChannel++; break;
        case 'addStreamToChannel': opCounts.addStream++; break;
        case 'removeStreamFromChannel': opCounts.removeStream++; break;
        case 'reorderChannelStreams': opCounts.reorderStreams++; break;
        case 'bulkAssignChannelNumbers':
          opCounts.assignNumbers += op.apiCall.channelIds.length;
          break;
        case 'createGroup': opCounts.createGroup++; break;
        case 'deleteChannelGroup': opCounts.deleteGroup++; break;
        case 'renameChannelGroup': opCounts.renameGroup++; break;
      }
    }
    // Add groups from createChannel newGroupName
    opCounts.createGroup += newGroupNames.size;

    // Build human-readable summary of what's being done
    const summaryParts: string[] = [];
    if (opCounts.createGroup > 0) {
      summaryParts.push(`${opCounts.createGroup} group${opCounts.createGroup !== 1 ? 's' : ''}`);
    }
    if (opCounts.createChannel > 0) {
      summaryParts.push(`${opCounts.createChannel} channel${opCounts.createChannel !== 1 ? 's' : ''}`);
    }
    if (opCounts.updateChannel > 0) {
      summaryParts.push(`${opCounts.updateChannel} update${opCounts.updateChannel !== 1 ? 's' : ''}`);
    }
    if (opCounts.addStream > 0) {
      summaryParts.push(`${opCounts.addStream} stream assignment${opCounts.addStream !== 1 ? 's' : ''}`);
    }
    if (opCounts.removeStream > 0) {
      summaryParts.push(`${opCounts.removeStream} stream removal${opCounts.removeStream !== 1 ? 's' : ''}`);
    }
    if (opCounts.reorderStreams > 0) {
      summaryParts.push(`${opCounts.reorderStreams} stream reorder${opCounts.reorderStreams !== 1 ? 's' : ''}`);
    }
    if (opCounts.assignNumbers > 0) {
      summaryParts.push(`${opCounts.assignNumbers} number assignment${opCounts.assignNumbers !== 1 ? 's' : ''}`);
    }
    if (opCounts.deleteChannel > 0) {
      summaryParts.push(`${opCounts.deleteChannel} channel deletion${opCounts.deleteChannel !== 1 ? 's' : ''}`);
    }
    if (opCounts.deleteGroup > 0) {
      summaryParts.push(`${opCounts.deleteGroup} group deletion${opCounts.deleteGroup !== 1 ? 's' : ''}`);
    }
    if (opCounts.renameGroup > 0) {
      summaryParts.push(`${opCounts.renameGroup} group rename${opCounts.renameGroup !== 1 ? 's' : ''}`);
    }

    try {
      // Build bulk operations using helper
      const { bulkOperations, groupsToCreate, unresolvedGroupRefs } =
        buildBulkOperations(
          consolidatedOps,
          stagedGroupsMap,
          new Map(state.baselineSnapshot.map((entry) => [entry.id, entry.channel_number ?? null])),
        );

      // Refuse to start rather than post a temp group id Dispatcharr will
      // reject one operation at a time (bead enhancedchannelmanager-udq1j).
      // Nothing is committed and edit mode is left intact, so the operator's
      // staged work survives.
      if (unresolvedGroupRefs.length > 0) {
        logger.error('[EditMode] Unresolvable staged group references:', unresolvedGroupRefs);
        result.success = false;
        result.operationsFailed = unresolvedGroupRefs.length;
        result.errors.push(...unresolvedGroupRefs.map((ref) => ({
          operationId: `unresolved-group-${ref.groupId}`,
          operationType: 'createChannel',
          error: `Target channel group ${ref.groupId} is no longer staged in this session`,
          channelName: ref.description,
        })));
        onError?.(
          `Nothing was applied: ${unresolvedGroupRefs.length} change${unresolvedGroupRefs.length !== 1 ? 's' : ''} ` +
          'target a channel group that is no longer staged. Your changes are still pending.'
        );
        return result; // `finally` clears isCommitting
      }

      const totalOps = bulkOperations.length;
      const BATCH_SIZE = 200; // Process in batches of 200 for progress updates

      // Report progress helper
      const reportProgress = (current: number, total: number, description: string) => {
        onProgress?.({
          current,
          total,
          currentOperation: description,
        });
      };

      // Every stream operation on a temp channel stays with its create. Besides
      // resolving the temp id, this lets the backend validate the create's
      // expected final stream set against the complete consolidated plan before
      // creating anything. Existing-channel stream operations stay batched.
      const channelCreateOps = bulkOperations.filter(op => op.type === 'createChannel');
      const isTempChannelStreamOp = (op: api.BulkOperation) =>
        (op.type === 'addStreamToChannel'
          || op.type === 'removeStreamFromChannel'
          || op.type === 'reorderChannelStreams')
        && typeof op.channelId === 'number'
        && op.channelId < 0;
      const createPhaseOps = bulkOperations.filter(op =>
        op.type === 'createChannel'
        || isTempChannelStreamOp(op)
      );
      const otherOps = bulkOperations.filter(op =>
        op.type !== 'createChannel'
        && !isTempChannelStreamOp(op)
      );

      // Calculate total batches for other operations
      const numBatches = Math.ceil(otherOps.length / BATCH_SIZE);
      const estimatedFetchPages = 4;
      // Total steps: 1 (creates) + batches + fetch pages
      const totalSteps = 1 + numBatches + estimatedFetchPages;
      let completedSteps = 0;
      let totalApplied = 0;
      let totalFailed = 0;
      /**
       * True when any bulk-commit response reported an unclean outcome that its
       * own failure count does not describe.
       *
       * `success = totalFailed === 0` alone laundered exactly one case, and it
       * is the case the backend's accounting invariant now names: an operation
       * whose upstream write LANDED and which ECM could not finish recording is
       * counted in `operationsApplied`, not in `operationsFailed`, and the
       * response says `success: false`. Summing only the counters turned that
       * into a clean Apply All (bead enhancedchannelmanager-e9e5o, fix round 4).
       */
      let sawUncleanResponse = false;

      /**
       * Turn a response's `numberingRecovery` into errors the operator READS.
       *
       * The steps travel in the envelope and the journal and the container log
       * already; none of those is a place an operator is standing when Apply
       * comes back. This is the one outcome where neither the previous
       * numbering nor the proposed one is what they are left with, so the
       * exact remaining step per channel has to reach the dialog (bead
       * enhancedchannelmanager-ic884.3).
       *
       * Pushed as errors rather than as a new field so the existing "some
       * changes were not applied" surface renders them with no second code
       * path to keep in step.
       */
      const collectRecoverySteps = (response: api.BulkCommitResponse) => {
        for (const entry of response.numberingRecovery ?? []) {
          result.errors.push({
            operationId: `numbering-recovery-${entry.channelId}`,
            operationType: 'updateChannel',
            error: entry.step,
            channelId: entry.channelId,
            channelName: entry.channelName,
          });
        }
      };

      // Report initial progress
      reportProgress(0, totalSteps, `Preparing ${totalOps} operations...`);

      // Step 1: Execute creates first (with groups) to get real IDs
      if (createPhaseOps.length > 0 || groupsToCreate.length > 0) {
        reportProgress(0, totalSteps, `Creating ${channelCreateOps.length} channels and ${groupsToCreate.length} groups...`);
        const createResponse = await api.bulkCommit({
          operations: createPhaseOps,
          groupsToCreate: groupsToCreate.length > 0 ? groupsToCreate : undefined,
          continueOnError: options?.continueOnError,
          consolidate: true,
        }, commitBatchId);

        // Check for validation failures
        if (!createResponse.success && createResponse.validationIssues?.length) {
          result.success = false;
          result.validationIssues = createResponse.validationIssues;
          result.validationPassed = false;
          throw new Error('Validation failed');
        }

        // Store temp ID mappings
        for (const [tempIdStr, realId] of Object.entries(createResponse.tempIdMap)) {
          tempIdMap.set(Number(tempIdStr), realId);
        }
        for (const [groupName, groupId] of Object.entries(createResponse.groupIdMap)) {
          newGroupIdMap.set(groupName, groupId);
        }

        totalApplied += createResponse.operationsApplied;
        totalFailed += createResponse.operationsFailed;
        if (!createResponse.success) sawUncleanResponse = true;
        result.errors.push(...createResponse.errors);
        collectRecoverySteps(createResponse);
      }

      completedSteps = 1;
      reportProgress(completedSteps, totalSteps, `Created channels, processing ${otherOps.length} updates...`);

      // Step 2: Process other operations in batches
      // Replace temp IDs with real IDs in remaining operations
      const unresolvedGroupOps: api.BulkCommitError[] = [];
      const resolvedOps: api.BulkOperation[] = [];
      for (const op of otherOps) {
        const resolved = { ...op };
        if (resolved.channelId && (resolved.channelId as number) < 0) {
          const realId = tempIdMap.get(resolved.channelId as number);
          if (realId !== undefined) {
            resolved.channelId = realId;
          }
        }
        // A channel moved into a group this batch created carries that group's
        // temp id. Step 1 has just returned the real ids by name; swap them in
        // rather than posting the temp id (bead enhancedchannelmanager-udq1j).
        if (resolved.type === 'updateChannel') {
          const data = resolved.data as Partial<Channel> | undefined;
          const stagedGroupId = data?.channel_group_id;
          if (typeof stagedGroupId === 'number' && stagedGroupId < 0) {
            const groupName = stagedGroupsMap.get(stagedGroupId)?.name;
            const realGroupId = groupName !== undefined ? newGroupIdMap.get(groupName) : undefined;
            if (realGroupId === undefined) {
              // The group creation phase did not produce this group. Drop the
              // op — sending it would 400 — and report it as a failure.
              unresolvedGroupOps.push({
                operationId: `unresolved-group-${stagedGroupId}`,
                operationType: 'updateChannel',
                error: `Channel group "${groupName ?? stagedGroupId}" was not created, so this channel was not moved`,
                channelId: resolved.channelId as number | undefined,
              });
              continue;
            }
            resolved.data = { ...data, channel_group_id: realGroupId };
          }
        }
        resolvedOps.push(resolved);
      }
      if (unresolvedGroupOps.length > 0) {
        logger.error('[EditMode] Dropped %d operation(s) targeting an uncreated group', unresolvedGroupOps.length);
        totalFailed += unresolvedGroupOps.length;
        result.errors.push(...unresolvedGroupOps);
      }

      // Process in batches
      for (let i = 0; i < resolvedOps.length; i += BATCH_SIZE) {
        const batch = resolvedOps.slice(i, i + BATCH_SIZE);
        const batchNum = Math.floor(i / BATCH_SIZE) + 1;
        const batchEnd = Math.min(i + BATCH_SIZE, resolvedOps.length);

        reportProgress(
          completedSteps + batchNum,
          totalSteps,
          `Processing operations ${i + 1}-${batchEnd} of ${resolvedOps.length}...`
        );

        const batchResponse = await api.bulkCommit({
          operations: batch,
          continueOnError: options?.continueOnError,
          consolidate: true,
        }, commitBatchId);

        // Check for validation failures
        if (!batchResponse.success && batchResponse.validationIssues?.length) {
          result.validationIssues = batchResponse.validationIssues;
          result.validationPassed = false;
          // Don't throw - collect errors and continue if continueOnError
          if (!options?.continueOnError) {
            result.success = false;
            throw new Error('Validation failed');
          }
        }

        totalApplied += batchResponse.operationsApplied;
        totalFailed += batchResponse.operationsFailed;
        if (!batchResponse.success) sawUncleanResponse = true;
        result.errors.push(...batchResponse.errors);
        collectRecoverySteps(batchResponse);

        // If a batch completely fails and we're not continuing on error, stop
        if (!batchResponse.success && !options?.continueOnError) {
          result.success = false;
          break;
        }
      }

      completedSteps = 1 + numBatches;

      // Update result totals
      result.operationsApplied = totalApplied;
      result.operationsFailed = totalFailed;
      result.success = totalFailed === 0 && !sawUncleanResponse;

      logger.info(`[EditMode] Bulk commit completed: ${totalApplied} applied, ${totalFailed} failed`);

      // Fetch updated channels with per-page progress
      const allChannels: Channel[] = [];
      let page = 1;
      let hasMore = true;

      while (hasMore) {
        reportProgress(completedSteps + page, totalSteps, `Fetching channels (page ${page})...`);
        const response = await api.getChannels({ page, pageSize: 500 });
        allChannels.push(...response.results);
        hasMore = response.next !== null;
        page++;
      }

      // Final progress
      reportProgress(totalSteps, totalSteps, `Complete: ${totalApplied} operations applied`);

      result.updatedChannels = allChannels;

      // Determine if we had any success (partial or complete).
      //
      // Groups are created in `groupsToCreate`, which is a phase rather than an
      // operation, so it contributes nothing to `operationsApplied`. A batch of
      // NOTHING BUT staged groups — "Create new channel group", Done, Apply All
      // — therefore looked like a no-op, and edit mode would never stand down
      // even though the groups really had been created (bead
      // enhancedchannelmanager-vtapf).
      const hadSuccess = result.operationsApplied > 0 || newGroupIdMap.size > 0;
      const hadFailures = result.operationsFailed > 0 || (result.validationIssues && result.validationIssues.length > 0);

      // Always update channels if any operations succeeded
      // This ensures partial success is reflected in the UI
      if (hadSuccess) {
        onChannelsChange(allChannels);
        // The staged queue is gone, so the ledger that mirrors it has to go in
        // the same tick — Apply All from the exit dialog also completes a
        // deferred sign-out, and a surviving ledger would offer these
        // operations back to the operator's next session. Replaying a create
        // is not idempotent (see `forgetStagedLedger`).
        forgetStagedLedger();
        setState(createInitialState());
        const createdGroupIds = Array.from(newGroupIdMap.values());
        onCommitComplete?.(createdGroupIds);
      }

      if (hadFailures) {
        // Build a detailed error message
        let errorMessage: string;

        // Check if this was a validation failure (no operations were executed)
        if (result.validationIssues && result.validationIssues.length > 0 && !hadSuccess) {
          const issue = result.validationIssues[0];
          errorMessage = `Validation failed: ${issue.message}`;
          if (result.validationIssues.length > 1) {
            errorMessage += ` (+${result.validationIssues.length - 1} more issue${result.validationIssues.length > 2 ? 's' : ''})`;
          }
        } else if (hadSuccess) {
          // Partial success - some operations worked, some failed
          errorMessage = `Completed with errors: ${result.operationsApplied} succeeded, ${result.operationsFailed} failed`;
          if (result.errors.length > 0) {
            // Show up to 3 unique error messages
            const uniqueErrors = new Map<string, { error: string; count: number; example?: string }>();
            for (const err of result.errors) {
              const key = err.error;
              if (!uniqueErrors.has(key)) {
                const example = err.channelName || err.streamName || undefined;
                uniqueErrors.set(key, { error: err.error, count: 1, example });
              } else {
                uniqueErrors.get(key)!.count++;
              }
            }
            const errorList = Array.from(uniqueErrors.values()).slice(0, 3);
            const errorDetails = errorList.map(e => {
              let detail = e.error;
              if (e.count > 1) detail += ` (x${e.count})`;
              else if (e.example) detail += ` (${e.example})`;
              return detail;
            }).join('; ');
            errorMessage += `. Errors: ${errorDetails}`;
          }
        } else if (result.errors.length > 0) {
          // All operations failed
          errorMessage = `Failed to apply ${result.operationsFailed} operation(s)`;
          const firstError = result.errors[0];
          const details: string[] = [];
          if (firstError.channelName) details.push(`channel: ${firstError.channelName}`);
          if (firstError.streamName) details.push(`stream: ${firstError.streamName}`);
          const detailStr = details.length > 0 ? ` (${details.join(', ')})` : '';
          errorMessage += `: ${firstError.error}${detailStr}`;
        } else {
          errorMessage = `Failed to apply ${result.operationsFailed} operation(s)`;
        }
        onError?.(errorMessage);
      }
    } catch (err) {
      logger.error('Commit failed:', err);
      result.success = false;
      onError?.('Commit failed: ' + (err instanceof Error ? err.message : 'Unknown error'));
    } finally {
      setIsCommitting(false);
    }

    return result;
  }, [
    state.isActive,
    state.stagedOperations,
    state.baselineSnapshot,
    stagedGroupsMap,
    channels,
    fetchServerChannels,
    onChannelsChange,
    onCommitComplete,
    onError,
    buildBulkOperations,
    forgetStagedLedger,
  ]);

  /**
   * Keep the persisted ledger in step with the staged queue.
   *
   * Written on every change rather than on some "leaving" event, because the
   * event this exists for is not one the app is told about: a token refresh
   * fails, `useAuth` clears the user, `ProtectedRoute` swaps the app for the
   * login page, and this component is unmounted with no notice and no chance
   * to flush. The last write therefore has to already be on disk, whenever
   * that happens.
   *
   * The undo GROUPING travels with the operations because
   * `stagedOperationCount` — the number in the header, and the thing
   * `handleExitEditMode` tests before it will even open the Apply dialog — is
   * the undo-entry count, not the operation count. A restore that rebuilt one
   * entry per operation would report twenty changes where the operator made
   * one.
   */
  useEffect(() => {
    if (!state.isActive || state.stagedOperations.length === 0) {
      // Only destroy a ledger this session put there. A pending offer from a
      // PREVIOUS session must survive a plain reload, and on mount edit mode
      // is always inactive.
      if (ledgerWrittenRef.current) {
        clearStagedLedger();
        ledgerWrittenRef.current = false;
      }
      return;
    }
    saveStagedLedger({
      operatorKey,
      operations: state.stagedOperations,
      undoGroups: state.localUndoStack.map((entry) => entry.operations.map((operation) => operation.id)),
    });
    ledgerWrittenRef.current = true;
  }, [state.isActive, state.stagedOperations, state.localUndoStack, operatorKey]);

  // Expose the raw timestamp so consumers can compute duration locally
  // without causing re-renders of the entire component tree every second
  const editModeEnteredAt = state.isActive ? state.enteredAt : null;

  // Sync the working copy with the API channel list.
  // Adds channels that exist in the API but not in the working copy (e.g.,
  // from CSV import) AND removes channels that have disappeared from the API
  // (e.g., source channels deleted as a side effect of /api/channels/merge).
  // Removal is gated on `id >= 0` so locally-created not-yet-saved channels
  // (negative temp IDs from stageCreateChannel) are preserved across syncs.
  useEffect(() => {
    if (!state.isActive) return;

    const apiIds = new Set(channels.map((ch) => ch.id));
    const workingCopyIds = new Set(state.workingCopy.map((ch) => ch.id));
    const newChannels = channels.filter((ch) => !workingCopyIds.has(ch.id));
    const removedIds = state.workingCopy
      .filter((ch) => ch.id >= 0 && !apiIds.has(ch.id))
      .map((ch) => ch.id);

    if (newChannels.length === 0 && removedIds.length === 0) return;

    if (newChannels.length > 0) {
      logger.debug('[useEditMode] Syncing', newChannels.length, 'new channels from API into working copy');
    }
    if (removedIds.length > 0) {
      logger.debug('[useEditMode] Removing', removedIds.length, 'channels from working copy (no longer in API):', removedIds);
    }

    const removedIdSet = new Set(removedIds);
    setState((prev) => ({
      ...prev,
      workingCopy: [
        ...prev.workingCopy.filter((ch) => !removedIdSet.has(ch.id)),
        ...newChannels.map((ch) => ({ ...ch, streams: [...ch.streams] })),
      ],
    }));
    // state.workingCopy is read inside the effect; we use a functional setState
    // (setState(prev => ...)) so it reads the latest, but exhaustive-deps still
    // flags the outer read. Adding state.workingCopy would cause a render loop
    // (the effect's setState updates state.workingCopy which retriggers it).
    // eslint-disable-next-line react-hooks/exhaustive-deps -- see comment: workingCopy read uses functional updater to avoid a render loop
  }, [state.isActive, channels]);

  // Determine which channels to display
  const displayChannels = state.isActive ? state.workingCopy : channels;

  // Convert the derived staged-group map to the array consumers take.
  const stagedGroupsArray = state.isActive ? Array.from(stagedGroupsMap.values()) : [];

  // Compute set of group IDs that are staged for deletion (soft-deleted)
  const deletedGroupIds = useMemo(() => {
    if (!state.isActive) {
      logger.debug('[useEditMode] Edit mode not active, returning empty deletedGroupIds');
      return new Set<number>();
    }

    const ids = new Set<number>();
    for (const op of state.stagedOperations) {
      if (op.apiCall.type === 'deleteChannelGroup') {
        ids.add(op.apiCall.groupId);
        logger.debug('[useEditMode] Found deleteChannelGroup operation for group:', op.apiCall.groupId);
      }
    }
    logger.debug('[useEditMode] Computed deletedGroupIds:', Array.from(ids), 'from', state.stagedOperations.length, 'staged operations');
    return ids;
  }, [state.isActive, state.stagedOperations]);

  // Compute map of group IDs to their staged new names (for visual display during edit mode)
  const renamedGroupNames = useMemo(() => {
    if (!state.isActive) {
      return new Map<number, string>();
    }

    const renames = new Map<number, string>();
    // Process in order so later renames override earlier ones
    for (const op of state.stagedOperations) {
      if (op.apiCall.type === 'renameChannelGroup') {
        renames.set(op.apiCall.groupId, op.apiCall.newName);
      }
    }
    return renames;
  }, [state.isActive, state.stagedOperations]);

  // Working-copy view of the staged operations that do not touch a Channel
  // record. Leaving edit mode empties it, exactly like the two above.
  const stagedSideEffects = useMemo(
    () => (state.isActive
      ? deriveStagedSideEffects(state.stagedOperations)
      : EMPTY_STAGED_SIDE_EFFECTS),
    [state.isActive, state.stagedOperations],
  );

  return {
    // State
    isEditMode: state.isActive,
    isCommitting,
    stagedOperationCount: state.localUndoStack.length,
    modifiedChannelIds: state.modifiedChannelIds,
    displayChannels,
    stagedGroups: stagedGroupsArray,
    deletedGroupIds,
    renamedGroupNames,
    stagedSideEffects,
    canLocalUndo: state.localUndoStack.length > 0,
    canLocalRedo: state.localRedoStack.length > 0,
    editModeEnteredAt,
    pendingRestore,
    restoredFrom: state.isActive ? state.restoredFrom : null,

    // Actions
    enterEditMode,
    exitEditMode,
    restoreStagedLedger,
    dismissPendingRestore,

    // Staging operations
    stageUpdateChannel,
    stageAddStream,
    stageRemoveStream,
    stageReorderStreams,
    stageBulkAssignNumbers,
    stageCreateChannel,
    stageCreateChannelWithStreams,
    stageDeleteChannel,
    stageCreateGroup,
    stageDeleteChannelGroup,
    stageRenameChannelGroup,
    stageSetProfileMembership,
    stageRestoreChannelGroup,
    stageClearStreamStats,
    addChannelToWorkingCopy,

    // Local undo/redo
    localUndo,
    localRedo,

    // Batch operations
    startBatch,
    endBatch,

    // Commit/Discard
    summary,
    getSummary,
    validate,
    commit,
    discard,
    reconcileNumberingConflicts,
  };
}
