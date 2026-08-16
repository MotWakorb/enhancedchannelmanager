/**
 * Survival store for Edit Mode's staged ledger across a dead session
 * (epic enhancedchannelmanager-r93hq).
 *
 * WHAT THIS IS FOR. Edit Mode stages changes until Apply All. Navigation,
 * browser Back/Forward and Sign Out all run through the exit guard now, so
 * staged work cannot be discarded silently by any of them. One path is left
 * and it cannot be guarded, because the app is not what is leaving — the
 * SESSION is. A refresh fails, or /me answers 401/403, `useAuth` clears the
 * user, `ProtectedRoute` swaps the app for `<LoginPage />`, and the ledger,
 * which lives only in `useEditMode`'s React state, dies with it. Apply is not
 * available at that moment either: the session is already gone, so every
 * commit call would 401.
 *
 * THREE PROPERTIES, IN THE ORDER THEY MATTER.
 *
 * 1. THE LEDGER IS BOUND TO THE OPERATOR WHO STAGED IT. "Signing back in on
 *    the same tab" is not "the same person". A ledger stamped with a different
 *    operator key is not withheld, it is DESTROYED — leaving one operator's
 *    staged channel edits reachable in a shared workstation's tab is the risk
 *    itself, because the next operator would Apply them under their own
 *    credentials and the journal would attribute every change to them.
 *
 * 2. IT LIVES IN `sessionStorage`. The ledger dies with the tab rather than
 *    sitting on disk indefinitely. {@link STAGED_LEDGER_MAX_AGE_MS} bounds the
 *    remaining case — a tab left open across a suspend.
 *
 * 3. IT IS VALIDATED BEFORE IT IS RESTORED, NEVER AFTER. Staged operations
 *    name channel ids, group ids, stream ids and this session's own temp ids.
 *    Any of them can have moved while the session was dead. Restoring blindly
 *    and applying against wrong ids is far worse than losing the work, so
 *    {@link planLedgerRestore} answers with BOTH halves — what can be restored
 *    and what cannot, with a reason per operation. A partial restore the
 *    operator can read is fine; a silent one is not.
 *
 * WHAT IS DELIBERATELY NOT CHECKED, so the gaps are decisions and not
 * accidents: a stream id being ATTACHED by `addStreamToChannel` (the channel
 * list carries stream ids, not a stream inventory, so a vanished stream is
 * only visible at commit time, where the backend reports it); a hidden group
 * id in `restoreChannelGroup` (hidden groups are by definition absent from the
 * visible group list, so checking there would drop every restore); and profile
 * ids when the caller does not supply {@link LedgerRestoreContext.profileIds}.
 */
import type { Channel, ChannelGroup, StagedOperation, ApiCallSpec, User } from '../types';

/** sessionStorage key. Fixed, not per-operator: a foreign ledger has to be
 *  FINDABLE in order to be destroyed (see the identity guard above). */
export const STAGED_LEDGER_STORAGE_KEY = 'ecm.channelManager.stagedLedger';

/** Bumped whenever the persisted shape changes; a mismatch is discarded. */
export const STAGED_LEDGER_FORMAT_VERSION = 1;

/**
 * How long a persisted ledger stays offerable.
 *
 * `sessionStorage` already ends the ledger when the tab closes, so this bound
 * only governs a tab left open — a laptop suspended overnight, a browser
 * restored from a previous run. Twelve hours covers an interrupted working
 * session while making "restored days later", where essentially every id has
 * had time to move, impossible.
 */
export const STAGED_LEDGER_MAX_AGE_MS = 12 * 60 * 60 * 1000;

/** The persisted record. */
export interface PersistedStagedLedger {
  version: number;
  /** {@link operatorLedgerKey} of the operator who staged this work. */
  operatorKey: string;
  /** Epoch ms of the last write. */
  savedAt: number;
  operations: StagedOperation[];
  /**
   * Undo-entry structure, as operation ids.
   *
   * NOT derived, and not a duplicate of anything: `stagedOperations` records
   * WHAT will be applied, and the undo stack records how the operator GROUPED
   * it — a Set-profile-membership over twenty channels is twenty operations
   * and one undo entry. `useEditMode` reports `stagedOperationCount` as the
   * undo-entry count, so a restore that rebuilt one entry per operation would
   * report twenty changes where the operator made one.
   */
  undoGroups: string[][];
}

/**
 * What `useEditMode.restoreStagedLedger` needs to rebuild a session.
 *
 * `operations` is the survivor list {@link planLedgerRestore} produced, NOT the
 * persisted list: the caller has already decided what is still applicable, and
 * the hook does not second-guess that verdict.
 */
export interface RestoreStagedLedgerInput {
  operations: StagedOperation[];
  undoGroups: string[][];
  /** {@link PersistedStagedLedger.savedAt}, kept so the session can say so. */
  savedAt: number;
}

/**
 * Stable per-operator key, built from what the auth layer already exposes.
 *
 * Provider-qualified because `User.id` is a row id: nothing in the type says a
 * local id and a Dispatcharr id cannot collide, and the cost of assuming they
 * cannot is one operator's staged work in another's hands.
 *
 * An instance with no identity at all (`require_auth: false`, no operator row)
 * gets its own key rather than borrowing anyone's, so the comparison below is
 * one rule applied uniformly instead of a special case.
 */
export function operatorLedgerKey(
  user: Pick<User, 'id' | 'auth_provider'> | null | undefined,
): string {
  if (!user) return 'anonymous';
  return `${user.auth_provider}#${user.id}`;
}

/** sessionStorage, or null where the browser refuses it (private mode, policy). */
function store(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

/** Remove the persisted ledger. Safe to call when there is none. */
export function clearStagedLedger(): void {
  try {
    store()?.removeItem(STAGED_LEDGER_STORAGE_KEY);
  } catch {
    // A storage that will not delete is a storage we cannot use; nothing to do.
  }
}

export interface SaveStagedLedgerInput {
  operatorKey: string;
  operations: StagedOperation[];
  undoGroups: string[][];
  /** Injectable clock, so the age bound is testable without faking timers. */
  now?: number;
}

/**
 * Write the ledger, or remove it when there is nothing staged.
 *
 * Called on every change to the staged queue, so the last write always
 * precedes whatever killed the session.
 */
export function saveStagedLedger({
  operatorKey,
  operations,
  undoGroups,
  now = Date.now(),
}: SaveStagedLedgerInput): void {
  if (operations.length === 0) {
    clearStagedLedger();
    return;
  }
  const record: PersistedStagedLedger = {
    version: STAGED_LEDGER_FORMAT_VERSION,
    operatorKey,
    savedAt: now,
    operations,
    undoGroups,
  };
  try {
    store()?.setItem(STAGED_LEDGER_STORAGE_KEY, JSON.stringify(record));
  } catch {
    // Quota or a disabled store. The in-memory ledger is unaffected; the
    // operator simply does not get the survival guarantee this session.
  }
}

/** Shape check for one persisted operation. Anything short of this is refused. */
function isStagedOperation(value: unknown): value is StagedOperation {
  if (typeof value !== 'object' || value === null) return false;
  const operation = value as Record<string, unknown>;
  const apiCall = operation.apiCall as Record<string, unknown> | undefined;
  return (
    typeof operation.id === 'string' &&
    typeof operation.timestamp === 'number' &&
    typeof operation.description === 'string' &&
    typeof apiCall === 'object' && apiCall !== null && typeof apiCall.type === 'string' &&
    Array.isArray(operation.beforeSnapshot) &&
    Array.isArray(operation.afterSnapshot)
  );
}

/** Shape check for the undo grouping. */
function isUndoGroups(value: unknown): value is string[][] {
  return Array.isArray(value)
    && value.every((group) => Array.isArray(group) && group.every((id) => typeof id === 'string'));
}

/**
 * Read the ledger IF it belongs to `operatorKey`, is the current format, and
 * is inside the age bound. Every other outcome destroys the stored record and
 * returns null — a ledger this app will not offer is a ledger it must not
 * keep.
 */
export function readStagedLedger(
  operatorKey: string,
  now: number = Date.now(),
): PersistedStagedLedger | null {
  let raw: string | null;
  try {
    raw = store()?.getItem(STAGED_LEDGER_STORAGE_KEY) ?? null;
  } catch {
    return null;
  }
  if (raw === null) return null;

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    clearStagedLedger();
    return null;
  }

  if (typeof parsed !== 'object' || parsed === null) {
    clearStagedLedger();
    return null;
  }
  const record = parsed as Record<string, unknown>;

  const wellFormed =
    record.version === STAGED_LEDGER_FORMAT_VERSION &&
    typeof record.operatorKey === 'string' &&
    typeof record.savedAt === 'number' &&
    Array.isArray(record.operations) &&
    record.operations.length > 0 &&
    record.operations.every(isStagedOperation) &&
    isUndoGroups(record.undoGroups);

  if (!wellFormed) {
    clearStagedLedger();
    return null;
  }

  // The identity guard. Destroy, do not merely withhold.
  if (record.operatorKey !== operatorKey) {
    clearStagedLedger();
    return null;
  }

  if (now - (record.savedAt as number) > STAGED_LEDGER_MAX_AGE_MS) {
    clearStagedLedger();
    return null;
  }

  return record as unknown as PersistedStagedLedger;
}

// ---------------------------------------------------------- staleness planning

/** Why an operation could not be restored. */
export type LedgerDropReason =
  /** Its channel is no longer on the server. */
  | 'channel-missing'
  /** Its channel group is no longer on the server. */
  | 'group-missing'
  /** Its channel no longer carries the streams the operation describes. */
  | 'stream-detached'
  /** Its profile is no longer on the server. */
  | 'profile-missing'
  /** It references something this ledger creates, and that creation was dropped. */
  | 'depends-on-dropped';

/** One operation that could not be restored, in the operator's terms. */
export interface DroppedLedgerOperation {
  id: string;
  type: ApiCallSpec['type'];
  description: string;
  reason: LedgerDropReason;
  /** One sentence naming the thing that moved. */
  detail: string;
}

export interface LedgerRestoreContext {
  /** The channel list as the server reports it right now. */
  channels: Channel[];
  /** The visible channel groups as the server reports them right now. */
  channelGroups: ChannelGroup[];
  /** Channel profile ids, when the caller has them. Omitted means unchecked. */
  profileIds?: number[];
}

export interface LedgerRestorePlan {
  restorable: StagedOperation[];
  dropped: DroppedLedgerOperation[];
}

/** A single reference an operation makes to something that must still exist. */
interface Reference {
  kind: 'channel' | 'group' | 'profile';
  id: number;
}

/** References an operation makes, in the order they should be reported. */
function referencesOf(apiCall: ApiCallSpec): Reference[] {
  switch (apiCall.type) {
    case 'updateChannel': {
      const references: Reference[] = [{ kind: 'channel', id: apiCall.channelId }];
      const groupId = apiCall.data.channel_group_id;
      if (typeof groupId === 'number') references.push({ kind: 'group', id: groupId });
      return references;
    }
    case 'addStreamToChannel':
    case 'removeStreamFromChannel':
    case 'reorderChannelStreams':
    case 'deleteChannel':
      return [{ kind: 'channel', id: apiCall.channelId }];
    case 'bulkAssignChannelNumbers':
      return apiCall.channelIds.map((id) => ({ kind: 'channel' as const, id }));
    case 'createChannel': {
      // `newGroupName` wins over `groupId` exactly as `stageOperation` decides
      // it, so the reference checked here is the one that will be committed.
      const groupId = apiCall.newGroupName ? apiCall.stagedGroupId : apiCall.groupId;
      return typeof groupId === 'number' ? [{ kind: 'group', id: groupId }] : [];
    }
    case 'createGroup':
      return [];
    case 'deleteChannelGroup':
    case 'renameChannelGroup':
      return [{ kind: 'group', id: apiCall.groupId }];
    case 'setProfileMembership':
      return [
        { kind: 'channel', id: apiCall.channelId },
        { kind: 'profile', id: apiCall.profileId },
      ];
    case 'restoreChannelGroup':
      // Hidden groups are absent from the visible list by definition. See the
      // module header — this gap is a decision.
      return [];
    case 'clearStreamStats':
      return [];
    default:
      return [];
  }
}

/** The temp channel id a `createChannel` operation was staged under. */
function tempChannelIdOf(operation: StagedOperation): number | undefined {
  const id = operation.afterSnapshot[0]?.id;
  return typeof id === 'number' && id < 0 ? id : undefined;
}

/** The best human label for a channel id this ledger mentions. */
function channelLabel(operation: StagedOperation, id: number): string {
  const snapshot =
    operation.beforeSnapshot.find((entry) => entry.id === id) ??
    operation.afterSnapshot.find((entry) => entry.id === id);
  return snapshot?.name ? `"${snapshot.name}" (id ${id})` : `id ${id}`;
}

/**
 * Decide, operation by operation, what a persisted ledger can still be applied
 * against — and produce the account of what cannot.
 *
 * Order matters: the walk is front to back so that a dropped `createChannel`
 * or `createGroup` takes everything referencing its temp id with it. A staged
 * operation pointing at a temp id nothing creates would otherwise reach
 * Dispatcharr as a negative primary key, which is the exact failure bead
 * enhancedchannelmanager-udq1j was filed for.
 *
 * Stream membership is projected THROUGH the ledger rather than read off the
 * server, because staging add-then-remove for the same stream in one session
 * is legal: the removal is valid against the projection even though the server
 * has never seen the stream on that channel.
 */
export function planLedgerRestore(
  operations: StagedOperation[],
  context: LedgerRestoreContext,
): LedgerRestorePlan {
  const channelById = new Map(context.channels.map((channel) => [channel.id, channel]));
  const groupIds = new Set(context.channelGroups.map((group) => group.id));
  const profileIds = context.profileIds ? new Set(context.profileIds) : null;

  /** Temp ids this ledger still creates, after earlier drops. */
  const stagedChannelIds = new Set<number>();
  const stagedGroupIds = new Set<number>();
  /** Stream list per channel, projected through the surviving operations. */
  const projectedStreams = new Map<number, number[]>();

  const streamsOf = (channelId: number): number[] => {
    const existing = projectedStreams.get(channelId);
    if (existing) return existing;
    const seeded = [...(channelById.get(channelId)?.streams ?? [])];
    projectedStreams.set(channelId, seeded);
    return seeded;
  };

  const restorable: StagedOperation[] = [];
  const dropped: DroppedLedgerOperation[] = [];

  const drop = (
    operation: StagedOperation,
    reason: LedgerDropReason,
    detail: string,
  ) => {
    dropped.push({
      id: operation.id,
      type: operation.apiCall.type,
      description: operation.description,
      reason,
      detail,
    });
  };

  for (const operation of operations) {
    const { apiCall } = operation;

    // --- referential validity -------------------------------------------
    let failure: { reason: LedgerDropReason; detail: string } | null = null;
    for (const reference of referencesOf(apiCall)) {
      if (reference.id < 0) {
        const created = reference.kind === 'group'
          ? stagedGroupIds.has(reference.id)
          : stagedChannelIds.has(reference.id);
        if (!created) {
          failure = {
            reason: 'depends-on-dropped',
            detail: `It targets a ${reference.kind} this session was going to create, which could not be restored.`,
          };
          break;
        }
        continue;
      }
      if (reference.kind === 'channel' && !channelById.has(reference.id)) {
        failure = {
          reason: 'channel-missing',
          detail: `Channel ${channelLabel(operation, reference.id)} no longer exists.`,
        };
        break;
      }
      if (reference.kind === 'group' && !groupIds.has(reference.id)) {
        failure = {
          reason: 'group-missing',
          detail: `Channel group ${reference.id} no longer exists.`,
        };
        break;
      }
      if (reference.kind === 'profile' && profileIds !== null && !profileIds.has(reference.id)) {
        failure = {
          reason: 'profile-missing',
          detail: `Channel profile ${reference.id} no longer exists.`,
        };
        break;
      }
    }

    // --- stream membership, against the projection ------------------------
    if (failure === null && apiCall.type === 'removeStreamFromChannel') {
      if (!streamsOf(apiCall.channelId).includes(apiCall.streamId)) {
        failure = {
          reason: 'stream-detached',
          detail: `Stream ${apiCall.streamId} is no longer assigned to channel ${channelLabel(operation, apiCall.channelId)}.`,
        };
      }
    }
    if (failure === null && apiCall.type === 'reorderChannelStreams') {
      const current = streamsOf(apiCall.channelId);
      const sameSet = current.length === apiCall.streamIds.length
        && apiCall.streamIds.every((id) => current.includes(id));
      if (!sameSet) {
        failure = {
          reason: 'stream-detached',
          detail: `The streams on channel ${channelLabel(operation, apiCall.channelId)} changed, so this reordering would drop or invent one.`,
        };
      }
    }

    if (failure !== null) {
      drop(operation, failure.reason, failure.detail);
      continue;
    }

    // --- accepted: record what it creates and how it moves the projection -
    switch (apiCall.type) {
      case 'createChannel': {
        const tempId = tempChannelIdOf(operation);
        if (tempId !== undefined) stagedChannelIds.add(tempId);
        if (apiCall.newGroupName && typeof apiCall.stagedGroupId === 'number') {
          stagedGroupIds.add(apiCall.stagedGroupId);
        }
        break;
      }
      case 'createGroup':
        stagedGroupIds.add(apiCall.tempGroupId);
        break;
      case 'addStreamToChannel': {
        const streams = streamsOf(apiCall.channelId);
        if (!streams.includes(apiCall.streamId)) streams.push(apiCall.streamId);
        break;
      }
      case 'removeStreamFromChannel':
        projectedStreams.set(
          apiCall.channelId,
          streamsOf(apiCall.channelId).filter((id) => id !== apiCall.streamId),
        );
        break;
      case 'reorderChannelStreams':
        projectedStreams.set(apiCall.channelId, [...apiCall.streamIds]);
        break;
      default:
        break;
    }

    restorable.push(operation);
  }

  return { restorable, dropped };
}
