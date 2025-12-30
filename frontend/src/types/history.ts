/**
 * Types for change tracking and undo/redo functionality
 */

// Types of changes that can be tracked
export type ChangeType =
  | 'channel_number_update'
  | 'channel_name_update'
  | 'channel_logo_update'
  | 'channel_metadata_update'
  | 'stream_add'
  | 'stream_remove'
  | 'stream_reorder'
  | 'channel_reorder';

// Snapshot of a single channel's state (for before/after comparison)
export interface ChannelSnapshot {
  id: number;
  channel_number: number | null;
  name: string;
  channel_group_id: number | null;
  streams: number[]; // ordered array of stream IDs
}

// A single change record in the history
export interface ChangeRecord {
  id: string; // Unique ID
  type: ChangeType;
  timestamp: number; // Date.now()
  description: string; // Human-readable description
  channelIds: number[]; // Channel(s) affected
  before: ChannelSnapshot[]; // State before the change
  after: ChannelSnapshot[]; // State after the change
}

// A user-created save point (checkpoint)
export interface SavePoint {
  id: string;
  name: string; // User-provided or auto-generated
  timestamp: number;
  historyIndex: number; // Index in past array at time of creation
  channelSnapshot: ChannelSnapshot[]; // Full snapshot of all channels
}

// The complete history state
export interface HistoryState {
  // Session baseline - initial state when page loaded
  baseline: ChannelSnapshot[];

  // Undo stack - changes that have been applied (most recent at end)
  past: ChangeRecord[];

  // Redo stack - changes that were undone (most recent at end)
  future: ChangeRecord[];

  // User-created save points
  savePoints: SavePoint[];

  // Flag to track if there are unsaved changes since last save point
  hasUnsavedChanges: boolean;
}

// Optional change info passed when updating channels
export interface ChangeInfo {
  type: ChangeType;
  description: string;
}
