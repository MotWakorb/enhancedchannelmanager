/**
 * Types for Edit Mode functionality
 *
 * Edit Mode allows staging changes locally before committing to the server.
 * Changes are queued as operations and can be reviewed before applying.
 */

import type { Channel, ChannelSnapshot, ChannelGroup } from './index';

/**
 * API operation specifications - discriminated union of all API calls
 * that can be staged during edit mode
 */
export type ApiCallSpec =
  | { type: 'updateChannel'; channelId: number; data: Partial<Channel> }
  | { type: 'addStreamToChannel'; channelId: number; streamId: number }
  | { type: 'removeStreamFromChannel'; channelId: number; streamId: number }
  | { type: 'reorderChannelStreams'; channelId: number; streamIds: number[] }
  | { type: 'bulkAssignChannelNumbers'; channelIds: number[]; startingNumber?: number }
  | { type: 'createChannel'; name: string; channelNumber?: number; groupId?: number; newGroupName?: string; stagedGroupId?: number; logoId?: number; logoUrl?: string; tvgId?: string; tvcGuideStationId?: string }
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

  // Staged groups (new groups being created, keyed by temp ID)
  stagedGroups: Map<number, ChannelGroup>;

  // Map of new group names to temp group IDs
  newGroupNameToTempId: Map<string, number>;

  // Next temp ID for groups (negative numbers, separate from channel temp IDs)
  nextTempGroupId: number;
}

/**
 * Validation issue found during pre-commit validation
 */
export interface ValidationIssue {
  type: 'missing_channel' | 'missing_stream' | 'invalid_operation';
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
  canLocalUndo: boolean;
  canLocalRedo: boolean;
  editModeEnteredAt: number | null; // timestamp when edit mode was entered

  // Actions
  enterEditMode: () => void;
  exitEditMode: () => void;

  // Staging operations (local-only changes)
  stageUpdateChannel: (channelId: number, data: Partial<Channel>, description: string) => void;
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
