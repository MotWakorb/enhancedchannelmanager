/**
 * Types for Edit Mode functionality
 *
 * Edit Mode allows staging changes locally before committing to the server.
 * Changes are queued as operations and can be reviewed before applying.
 */

import type { Channel, ChannelSnapshot, ChannelGroup } from './index';
import type { PersistedStagedLedger, RestoreStagedLedgerInput } from '../utils/stagedLedgerStorage';
import type { AutomaticRename } from '../utils/channelNumberPlan';

export type { AutomaticRename };

/**
 * Bookkeeping a caller can attach to a staged channel update that is not part
 * of the Dispatcharr payload.
 */
export interface StageUpdateChannelOptions {
  /**
   * The channel number the operator was warned about and chose to use anyway
   * (bead enhancedchannelmanager-vdxbx). Recorded so the final-state preflight
   * can tell a deliberate duplicate from an accidental one and does not
   * re-litigate a decision the operator already made.
   */
  acknowledgedDuplicateNumber?: number;
}

/**
 * API operation specifications - discriminated union of all API calls
 * that can be staged during edit mode
 */
export type ApiCallSpec =
  /**
   * `acknowledgedDuplicateNumber` is the operator's recorded answer to "that
   * number is already used by another channel — use it anyway?" (bead
   * enhancedchannelmanager-vdxbx). It rides on the OPERATION rather than in a
   * registry beside it for three reasons: it survives the sessionStorage
   * ledger with no extra plumbing, undoing the operation withdraws the
   * acknowledgement automatically, and deleting an unrelated earlier action
   * cannot silently empty it. It is not part of `data`, because `data` is the
   * body sent to Dispatcharr and this is ECM's own bookkeeping.
   *
   * The value is the NUMBER that was acknowledged, not a boolean: an operator
   * who accepted a duplicate on 5 has said nothing about 6, so re-staging the
   * same channel onto a different occupied number asks again.
   */
  | { type: 'updateChannel'; channelId: number; data: Partial<Channel>; acknowledgedDuplicateNumber?: number }
  | { type: 'addStreamToChannel'; channelId: number; streamId: number }
  | { type: 'removeStreamFromChannel'; channelId: number; streamId: number }
  | { type: 'reorderChannelStreams'; channelId: number; streamIds: number[] }
  | { type: 'bulkAssignChannelNumbers'; channelIds: number[]; startingNumber?: number }
  | { type: 'createChannel'; name: string; channelNumber?: number; groupId?: number; newGroupName?: string; stagedGroupId?: number; logoId?: number; logoUrl?: string; tvgId?: string; tvcGuideStationId?: string; acknowledgedDuplicateNumber?: number }
  | { type: 'deleteChannel'; channelId: number }
  | { type: 'createGroup'; name: string; tempGroupId: number }
  | { type: 'deleteChannelGroup'; groupId: number }
  | { type: 'renameChannelGroup'; groupId: number; newName: string }
  /**
   * The three actions Edit Mode used to write straight through its own staging
   * area (bead enhancedchannelmanager-kz089). Each sat in an Edit Mode toolbar
   * next to actions that stage, was not counted in the change count, and was
   * not reverted by Cancel, Discard or Undo.
   */
  | { type: 'setProfileMembership'; profileId: number; channelId: number; enabled: boolean }
  | { type: 'restoreChannelGroup'; groupId: number }
  | { type: 'clearStreamStats'; streamIds: number[] };

/**
 * Working-copy representation of the staged operations whose effect does NOT
 * live on a Channel record (bead enhancedchannelmanager-kz089, fix round 2).
 *
 * `applyOperationToWorkingCopy` returns `Channel[]`, so profile membership,
 * hidden-group state and probe stats had nowhere to go: the three operations
 * were counted and summarised while the UI carried on showing the old value.
 * That is worse than the immediate write it replaced — the operator is told
 * something is pending and shown no evidence of it. The two that DID show
 * evidence did it by mutating component-local state, which `discard` and
 * `localUndo` cannot reach, so Discard dropped the count while the stats
 * stayed gone.
 *
 * Both halves are fixed by deriving this from `stagedOperations`, the one
 * collection stage / undo / redo / discard already maintain correctly. There is
 * no second source of truth to drift.
 */
export interface StagedSideEffects {
  /** `${profileId}:${channelId}` -> staged enabled state. */
  profileMembership: Map<string, boolean>;
  /** Hidden channel groups staged for restore. */
  restoredGroupIds: Set<number>;
  /** Streams whose probe stats are staged to be cleared. */
  clearedStreamIds: Set<number>;
}

/** Key for {@link StagedSideEffects.profileMembership}. */
export function profileMembershipKey(profileId: number, channelId: number): string {
  return `${profileId}:${channelId}`;
}

/** A {@link StagedSideEffects} with nothing staged. */
export const EMPTY_STAGED_SIDE_EFFECTS: StagedSideEffects = {
  profileMembership: new Map(),
  restoredGroupIds: new Set(),
  clearedStreamIds: new Set(),
};

/**
 * A staged operation in the edit mode queue
 */
export interface StagedOperation {
  id: string;
  timestamp: number;
  description: string;
  apiCall: ApiCallSpec;
  // Snapshot of affected channel(s) before this operation
  beforeSnapshot: ChannelSnapshot[];
  // Snapshot of affected channel(s) after this operation (computed locally)
  afterSnapshot: ChannelSnapshot[];
}

/**
 * An undo entry that groups one or more operations together
 * This allows batch operations (like renumbering multiple channels) to be undone as a unit
 */
export interface UndoEntry {
  id: string;
  timestamp: number;
  description: string; // Summary description for the batch
  operations: StagedOperation[];
}

/**
 * Individual operation detail for the exit dialog
 */
export interface OperationDetail {
  id: string;
  type: string;
  description: string;
}

/**
 * Summary of changes for the exit dialog
 */
export interface EditModeSummary {
  /**
   * Number of staged operations. NOT the number the exit dialog quotes — a
   * staged `createChannel` carrying a `newGroupName` also produces a group,
   * and a single `updateChannel` can carry several field changes, so the
   * enumerated lines legitimately outnumber the operations. Use
   * {@link EditModeSummary.totalChanges} for anything shown to an operator.
   */
  totalOperations: number;
  /**
   * Sum of every enumerated bucket below. This is the number the exit dialog
   * shows, so the headline and the bulleted list can never disagree — drill
   * run 2026-08-09-run18 caught "24 pending changes" over lines summing to 26
   * (bead enhancedchannelmanager-75k49).
   */
  totalChanges: number;
  channelsModified: number;
  streamsAdded: number;
  streamsRemoved: number;
  streamsReordered: number;
  channelNumberChanges: number;
  channelNameChanges: number;
  epgChanges: number;
  gracenoteIdChanges: number;
  /** `logo_id` set or cleared via Edit Channel. */
  logoChanges: number;
  /** `stream_profile_id` set or cleared via Edit Channel. */
  streamProfileChanges: number;
  /** `channel_group_id` changed — a cross-group channel move. */
  groupMoves: number;
  /**
   * Catch-all so every staged `updateChannel` contributes at least one line.
   * A field added to the Edit Channel modal without a bucket here shows up as
   * "N other channel change" instead of vanishing from the summary.
   */
  otherChannelChanges: number;
  newChannels: number;
  deletedChannels: number;
  newGroups: number;
  deletedGroups: number;
  renamedGroups: number;
  /** Channels enabled/disabled in a channel profile (bead …-kz089). */
  profileVisibilityChanges: number;
  /** Hidden channel groups staged for restore (bead …-kz089). */
  restoredGroups: number;
  /** Streams whose probe stats are staged to be cleared (bead …-kz089). */
  clearedStreamStats: number;
  /**
   * Channel names a numbering change rewrote with nobody typing them (bead
   * enhancedchannelmanager-ic884.5).
   *
   * Not a count, because a count is not actionable: an operator asked to
   * approve a rename they did not ask for needs the before and after. These
   * renames are ALSO counted in {@link EditModeSummary.channelNameChanges} and
   * are not added to `totalChanges` again — they are the same staged change
   * described in more detail, not an extra one.
   */
  automaticRenames: AutomaticRename[];
  // Detailed list of all operations with descriptions
  operationDetails: OperationDetail[];
}

/**
 * Core edit mode state
 */
export interface EditModeState {
  // Whether edit mode is active
  isActive: boolean;

  // Timestamp when edit mode was entered
  enteredAt: number | null;

  /**
   * When this session's staged work came back from a persisted ledger, the
   * epoch-ms the ledger was last written; null for work staged here and now.
   *
   * Session provenance, not derivable from anything else: an operator landing
   * back in Edit Mode after re-authenticating has to be able to tell restored
   * staged changes from changes they just made, and the operation list looks
   * identical either way.
   */
  restoredFrom: number | null;

  // Snapshot of all channels when edit mode was entered (baseline)
  baselineSnapshot: ChannelSnapshot[];

  // Working copy of channels (modified locally)
  workingCopy: Channel[];

  // Queue of operations to commit
  stagedOperations: StagedOperation[];

  // Undo stack for local operations (within edit session)
  // Each entry may contain multiple operations that are undone together
  localUndoStack: UndoEntry[];

  // Redo stack for local operations (within edit session)
  localRedoStack: UndoEntry[];

  // IDs of channels that have been modified
  modifiedChannelIds: Set<number>;

  // Temporary IDs for new channels (negative numbers)
  nextTempId: number;

  // Map of temp IDs to real IDs after commit
  tempIdMap: Map<number, number>;

  // Current batch being built (null when not batching)
  currentBatch: {
    description: string;
    operations: StagedOperation[];
  } | null;

  // NOTE: the groups this session will create are NOT held here. They are
  // derived from `stagedOperations` by `deriveStagedGroups`, alongside
  // `deriveStagedSideEffects`, `deletedGroupIds` and `renamedGroupNames`
  // (bead enhancedchannelmanager-kz089, fix round 3). They used to be an
  // accumulated `Map` maintained beside the operation list, which `localUndo`
  // never recomputed: undoing "Create group Sports" dropped the operation and
  // the change count and left "Sports" in the group filters and the
  // group-selection views, because `App.tsx` renders the map straight into
  // `displayChannelGroups`. A field that cannot be reached by undo is a field
  // that will disagree with the operations sooner or later, so the field is
  // gone rather than patched.
}

/**
 * Validation issue found during pre-commit validation
 */
export interface ValidationIssue {
  /**
   * The two numbering types are raised by the final-state preflight
   * (`channelNumberPlan.ts` in the browser, `backend/channel_number_plan.py`
   * on the server) rather than by the per-operation checks, so they describe
   * the COMBINED result of the staged plan rather than any one operation.
   */
  type:
    | 'missing_channel'
    | 'missing_stream'
    | 'invalid_operation'
    | 'duplicate_channel_number'
    | 'invalid_channel_number';
  severity: 'error' | 'warning';
  message: string;
  operationIndex?: number;
  channelId?: number;
  channelName?: string;
  streamId?: number;
  streamName?: string;
}

/**
 * Result of a validation operation
 */
export interface ValidationResult {
  passed: boolean;
  issues: ValidationIssue[];
}

/**
 * Detailed error from a failed operation
 */
export interface CommitError {
  operationId: string;
  operationType?: string;
  error: string;
  channelId?: number;
  channelName?: string;
  streamId?: number;
  streamName?: string;
  entityName?: string;
}

/**
 * Options for commit operation
 */
export interface CommitOptions {
  /** If true, continue processing even when individual operations fail */
  continueOnError?: boolean;
  /** Skip validation step (use when user already confirmed to proceed) */
  skipValidation?: boolean;
}

/**
 * Result of a commit operation
 */
export interface CommitResult {
  success: boolean;
  operationsApplied: number;
  operationsFailed: number;
  errors: CommitError[];
  // Validation issues found during pre-validation
  validationIssues?: ValidationIssue[];
  // Whether validation passed (no errors, may have warnings)
  validationPassed?: boolean;
  // Updated channels after commit
  updatedChannels: Channel[];
}

/**
 * Props for edit mode context/hook return
 */
export interface UseEditModeReturn {
  // State
  isEditMode: boolean;
  isCommitting: boolean;
  stagedOperationCount: number;
  modifiedChannelIds: Set<number>;
  displayChannels: Channel[]; // working copy if in edit mode, else real channels
  stagedGroups: ChannelGroup[]; // new groups being staged (empty array if not in edit mode)
  renamedGroupNames: Map<number, string>; // groupId -> newName for staged renames
  deletedGroupIds: Set<number>; // group IDs staged for deletion
  /**
   * Working-copy view of the staged operations that do not touch a Channel
   * record. Derived from the operation queue, so Discard, Undo and Redo cover
   * it without a second reducer (bead …-kz089, fix round 2).
   */
  stagedSideEffects: StagedSideEffects;
  canLocalUndo: boolean;
  canLocalRedo: boolean;
  editModeEnteredAt: number | null; // timestamp when edit mode was entered

  /**
   * A staged ledger this operator left behind in a previous session of this
   * tab, waiting to be offered. Null when there is none, when it belonged to a
   * different operator, when it is past its age bound, or once the offer has
   * been taken or dismissed.
   *
   * The offer is deliberately NOT taken automatically: the operations may
   * reference channels, groups or streams that moved while the session was
   * dead, and an operator is entitled to read the account of what can and
   * cannot be restored before any of it becomes live staged work.
   */
  pendingRestore: PersistedStagedLedger | null;
  /** @see EditModeState.restoredFrom */
  restoredFrom: number | null;
  /**
   * Enter Edit Mode rebuilt from a persisted ledger. `operations` is the
   * survivor list the caller's staleness plan produced, never the raw
   * persisted list.
   */
  restoreStagedLedger: (input: RestoreStagedLedgerInput) => void;
  /** Refuse the pending offer and destroy the persisted ledger. */
  dismissPendingRestore: () => void;

  // Actions
  enterEditMode: () => void;
  exitEditMode: () => void;

  // Staging operations (local-only changes)
  stageUpdateChannel: (
    channelId: number,
    data: Partial<Channel>,
    description: string,
    options?: StageUpdateChannelOptions,
  ) => void;
  stageAddStream: (channelId: number, streamId: number, description: string) => void;
  stageRemoveStream: (channelId: number, streamId: number, description: string) => void;
  stageReorderStreams: (channelId: number, streamIds: number[], description: string) => void;
  stageBulkAssignNumbers: (channelIds: number[], startingNumber: number, description: string) => void;
  stageCreateChannel: (name: string, channelNumber?: number, groupId?: number, newGroupName?: string, logoId?: number, logoUrl?: string, tvgId?: string, tvcGuideStationId?: string) => number; // returns temp ID
  stageDeleteChannel: (channelId: number, description: string) => void;
  /**
   * Stage a new channel group. Returns the negative temp id the group is known
   * by until commit, so the caller can select it in the group filter or move
   * channels into it without waiting for a round trip.
   */
  stageCreateGroup: (name: string) => number;
  stageDeleteChannelGroup: (groupId: number, description: string) => void;
  stageRenameChannelGroup: (groupId: number, newName: string, description: string) => void;
  /** Stage enabling/disabling channels in a channel profile (bead …-kz089). */
  stageSetProfileMembership: (profileId: number, channelIds: number[], enabled: boolean, description: string) => void;
  /** Stage un-hiding a channel group (bead …-kz089). */
  stageRestoreChannelGroup: (groupId: number, description: string) => void;
  /** Stage clearing probe stats for streams (bead …-kz089). */
  stageClearStreamStats: (streamIds: number[], description: string) => void;
  addChannelToWorkingCopy: (channel: Channel) => void; // Add a newly created channel to working copy

  // Local undo/redo (within edit session)
  localUndo: () => void;
  localRedo: () => void;

  // Batch operations - groups multiple operations into a single undo entry
  startBatch: (description: string) => void;
  endBatch: () => void;

  // Commit/Discard
  summary: EditModeSummary; // Memoized summary - use this instead of getSummary() for better performance
  getSummary: () => EditModeSummary; // Deprecated: use summary property instead
  /** Validate operations without executing - returns validation issues */
  validate: () => Promise<ValidationResult>;
  /** Commit with optional progress callback and options (continueOnError, skipValidation) */
  commit: (onProgress?: (progress: CommitProgress) => void, options?: CommitOptions) => Promise<CommitResult>;
  discard: () => void;

  // Check for conflicts with server
  checkForConflicts: () => Promise<boolean>;
}

/**
 * Props for EditModeToggle component
 */
export interface EditModeToggleProps {
  isEditMode: boolean;
  stagedCount: number;
  onEnter: () => void;
  onExit: () => void;
  disabled?: boolean;
}

/**
 * Props for EditModeBanner component
 */
export interface EditModeBannerProps {
  stagedCount: number;
  duration: number | null;
  onCancel: () => void;
}

/**
 * Progress info for commit operation
 */
export interface CommitProgress {
  current: number;
  total: number;
  currentOperation: string;
}

/**
 * Props for EditModeExitDialog component
 */
export interface EditModeExitDialogProps {
  isOpen: boolean;
  summary: EditModeSummary;
  onApply: () => void;
  onDiscard: () => void;
  onKeepEditing: () => void;
  isCommitting?: boolean;
  commitProgress?: CommitProgress | null;
  /**
   * Outcome of the commit the operator just ran, when it did NOT fully
   * succeed. The dialog stays open on this until it is acknowledged.
   *
   * Drill run 2026-08-09-run18 committed a batch the backend reported as
   * `success=False, applied=11, failed=1`, and the operator was shown nothing
   * at all — no toast, no banner, no notification — while Edit Mode exited as
   * if the batch had applied (bead enhancedchannelmanager-udq1j).
   */
  commitFailure?: CommitFailure | null;
  /** Dismiss {@link EditModeExitDialogProps.commitFailure} and close. */
  onAcknowledgeFailure?: () => void;
}

/**
 * A commit that applied some (or none) of its operations, reduced to what the
 * operator needs to read.
 */
export interface CommitFailure {
  applied: number;
  failed: number;
  /** Deduplicated error lines, most useful first. */
  messages: string[];
}
