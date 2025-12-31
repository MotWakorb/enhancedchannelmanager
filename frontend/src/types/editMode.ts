/**
 * Types for Edit Mode functionality
 *
 * Edit Mode allows staging changes locally before committing to the server.
 * Changes are queued as operations and can be reviewed before applying.
 */

import type { Channel, ChannelSnapshot } from './index';

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
  | { type: 'createChannel'; name: string; channelNumber?: number; groupId?: number }
  | { type: 'deleteChannel'; channelId: number }
  | { type: 'deleteChannelGroup'; groupId: number };

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
  totalOperations: number;
  channelsModified: number;
  streamsAdded: number;
  streamsRemoved: number;
  streamsReordered: number;
  channelNumberChanges: number;
  channelNameChanges: number;
  newChannels: number;
  deletedChannels: number;
  deletedGroups: number;
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
}

/**
 * Result of a commit operation
 */
export interface CommitResult {
  success: boolean;
  operationsApplied: number;
  operationsFailed: number;
  errors: Array<{
    operationId: string;
    error: string;
  }>;
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
  canLocalUndo: boolean;
  canLocalRedo: boolean;
  editModeDuration: number | null; // milliseconds since entering edit mode

  // Actions
  enterEditMode: () => void;
  exitEditMode: () => void;

  // Staging operations (local-only changes)
  stageUpdateChannel: (channelId: number, data: Partial<Channel>, description: string) => void;
  stageAddStream: (channelId: number, streamId: number, description: string) => void;
  stageRemoveStream: (channelId: number, streamId: number, description: string) => void;
  stageReorderStreams: (channelId: number, streamIds: number[], description: string) => void;
  stageBulkAssignNumbers: (channelIds: number[], startingNumber: number, description: string) => void;
  stageCreateChannel: (name: string, channelNumber?: number, groupId?: number) => number; // returns temp ID
  stageDeleteChannel: (channelId: number, description: string) => void;
  stageDeleteChannelGroup: (groupId: number, description: string) => void;
  addChannelToWorkingCopy: (channel: Channel) => void; // Add a newly created channel to working copy

  // Local undo/redo (within edit session)
  localUndo: () => void;
  localRedo: () => void;

  // Batch operations - groups multiple operations into a single undo entry
  startBatch: (description: string) => void;
  endBatch: () => void;

  // Commit/Discard
  getSummary: () => EditModeSummary;
  commit: () => Promise<CommitResult>;
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
 * Props for EditModeExitDialog component
 */
export interface EditModeExitDialogProps {
  isOpen: boolean;
  summary: EditModeSummary;
  onApply: () => void;
  onDiscard: () => void;
  onKeepEditing: () => void;
  isCommitting?: boolean;
}
