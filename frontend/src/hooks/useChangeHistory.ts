import { useState, useCallback, useRef, useEffect } from 'react';
import type {
  Channel,
  ChangeRecord,
  ChannelSnapshot,
  SavePoint,
  HistoryState,
} from '../types';
import * as api from '../services/api';

const MAX_HISTORY_SIZE = 100;

// Helper to create a snapshot from a channel
function createSnapshot(channel: Channel): ChannelSnapshot {
  return {
    id: channel.id,
    channel_number: channel.channel_number,
    name: channel.name,
    channel_group_id: channel.channel_group_id,
    streams: [...channel.streams],
  };
}

// Generate a unique ID
function generateId(): string {
  return `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
}

export interface UseChangeHistoryOptions {
  channels: Channel[];
  onChannelsRestore: (channels: Channel[]) => void;
  onError?: (message: string) => void;
}

export interface UseChangeHistoryReturn {
  // State
  canUndo: boolean;
  canRedo: boolean;
  undoCount: number;
  redoCount: number;
  savePoints: SavePoint[];
  hasUnsavedChanges: boolean;
  lastChange: ChangeRecord | null;
  isOperationPending: boolean;

  // Actions
  recordChange: (change: Omit<ChangeRecord, 'id' | 'timestamp'>) => void;
  undo: () => Promise<void>;
  redo: () => Promise<void>;

  // Save Points
  createSavePoint: (name?: string) => void;
  revertToSavePoint: (savePointId: string) => Promise<void>;
  deleteSavePoint: (savePointId: string) => void;

  // Session management
  initializeBaseline: (channels: Channel[]) => void;
  clearHistory: () => void;
}

export function useChangeHistory({
  channels,
  onChannelsRestore,
  onError,
}: UseChangeHistoryOptions): UseChangeHistoryReturn {
  const [history, setHistory] = useState<HistoryState>({
    baseline: [],
    past: [],
    future: [],
    savePoints: [],
    hasUnsavedChanges: false,
  });

  const [isOperationPending, setIsOperationPending] = useState(false);

  // Track channel state by ID for quick lookups
  const channelMapRef = useRef<Map<number, Channel>>(new Map());

  // Update channel map when channels change
  useEffect(() => {
    channelMapRef.current = new Map(channels.map((ch) => [ch.id, ch]));
  }, [channels]);

  // Initialize baseline on first load
  const initializeBaseline = useCallback((initialChannels: Channel[]) => {
    const baseline = initialChannels.map(createSnapshot);

    setHistory({
      baseline,
      past: [],
      future: [],
      savePoints: [],
      hasUnsavedChanges: false,
    });
  }, []);

  // Record a change
  const recordChange = useCallback(
    (change: Omit<ChangeRecord, 'id' | 'timestamp'>) => {
      const record: ChangeRecord = {
        ...change,
        id: generateId(),
        timestamp: Date.now(),
      };

      setHistory((prev) => {
        let newPast = [...prev.past, record];

        // Trim oldest entries if exceeding limit
        if (newPast.length > MAX_HISTORY_SIZE) {
          const trimmedCount = newPast.length - MAX_HISTORY_SIZE;
          newPast = newPast.slice(-MAX_HISTORY_SIZE);

          // Update save point indices
          const updatedSavePoints = prev.savePoints
            .map((sp) => ({ ...sp, historyIndex: sp.historyIndex - trimmedCount }))
            .filter((sp) => sp.historyIndex >= 0);

          return {
            ...prev,
            past: newPast,
            future: [], // Clear redo stack on new change
            savePoints: updatedSavePoints,
            hasUnsavedChanges: true,
          };
        }

        return {
          ...prev,
          past: newPast,
          future: [], // Clear redo stack on new change
          hasUnsavedChanges: true,
        };
      });
    },
    []
  );

  // Execute reverse operation for undo
  const executeReverseOperation = useCallback(
    async (change: ChangeRecord): Promise<void> => {
      for (const beforeSnapshot of change.before) {
        const afterSnapshot = change.after.find((a) => a.id === beforeSnapshot.id);
        if (!afterSnapshot) continue;

        // Check if channel still exists
        const channelExists = channelMapRef.current.has(beforeSnapshot.id);
        if (!channelExists) {
          console.warn(
            `Channel ${beforeSnapshot.id} no longer exists, skipping undo for this channel`
          );
          continue;
        }

        switch (change.type) {
          case 'channel_number_update':
          case 'channel_name_update':
            await api.updateChannel(beforeSnapshot.id, {
              channel_number: beforeSnapshot.channel_number,
              name: beforeSnapshot.name,
            });
            break;

          case 'stream_add': {
            // Find streams that were added and remove them
            const addedStreamIds = afterSnapshot.streams.filter(
              (id) => !beforeSnapshot.streams.includes(id)
            );
            for (const streamId of addedStreamIds) {
              await api.removeStreamFromChannel(beforeSnapshot.id, streamId);
            }
            break;
          }

          case 'stream_remove': {
            // Find streams that were removed and add them back
            const removedStreamIds = beforeSnapshot.streams.filter(
              (id) => !afterSnapshot.streams.includes(id)
            );
            for (const streamId of removedStreamIds) {
              await api.addStreamToChannel(beforeSnapshot.id, streamId);
            }
            // Restore original order
            if (beforeSnapshot.streams.length > 0) {
              await api.reorderChannelStreams(beforeSnapshot.id, beforeSnapshot.streams);
            }
            break;
          }

          case 'stream_reorder':
            await api.reorderChannelStreams(beforeSnapshot.id, beforeSnapshot.streams);
            break;

          case 'channel_reorder':
            await api.updateChannel(beforeSnapshot.id, {
              channel_number: beforeSnapshot.channel_number,
            });
            break;
        }
      }
    },
    []
  );

  // Execute forward operation for redo
  const executeForwardOperation = useCallback(
    async (change: ChangeRecord): Promise<void> => {
      for (const afterSnapshot of change.after) {
        const beforeSnapshot = change.before.find((b) => b.id === afterSnapshot.id);
        if (!beforeSnapshot) continue;

        // Check if channel still exists
        const channelExists = channelMapRef.current.has(afterSnapshot.id);
        if (!channelExists) {
          console.warn(
            `Channel ${afterSnapshot.id} no longer exists, skipping redo for this channel`
          );
          continue;
        }

        switch (change.type) {
          case 'channel_number_update':
          case 'channel_name_update':
            await api.updateChannel(afterSnapshot.id, {
              channel_number: afterSnapshot.channel_number,
              name: afterSnapshot.name,
            });
            break;

          case 'stream_add': {
            const addedStreamIds = afterSnapshot.streams.filter(
              (id) => !beforeSnapshot.streams.includes(id)
            );
            for (const streamId of addedStreamIds) {
              await api.addStreamToChannel(afterSnapshot.id, streamId);
            }
            break;
          }

          case 'stream_remove': {
            const removedStreamIds = beforeSnapshot.streams.filter(
              (id) => !afterSnapshot.streams.includes(id)
            );
            for (const streamId of removedStreamIds) {
              await api.removeStreamFromChannel(afterSnapshot.id, streamId);
            }
            break;
          }

          case 'stream_reorder':
            await api.reorderChannelStreams(afterSnapshot.id, afterSnapshot.streams);
            break;

          case 'channel_reorder':
            await api.updateChannel(afterSnapshot.id, {
              channel_number: afterSnapshot.channel_number,
            });
            break;
        }
      }
    },
    []
  );

  // Undo last change
  const undo = useCallback(async () => {
    if (isOperationPending) return;
    const lastChange = history.past[history.past.length - 1];
    if (!lastChange) return;

    setIsOperationPending(true);

    // Store original channels for rollback
    const originalChannels = [...channels];

    // Optimistically update UI from before snapshots
    const updatedChannels = channels.map((ch) => {
      const snapshot = lastChange.before.find((s) => s.id === ch.id);
      if (snapshot) {
        return {
          ...ch,
          channel_number: snapshot.channel_number,
          name: snapshot.name,
          channel_group_id: snapshot.channel_group_id,
          streams: [...snapshot.streams],
        };
      }
      return ch;
    });
    onChannelsRestore(updatedChannels);

    try {
      await executeReverseOperation(lastChange);

      // Success - update history
      setHistory((prev) => ({
        ...prev,
        past: prev.past.slice(0, -1),
        future: [...prev.future, lastChange],
        hasUnsavedChanges: prev.past.length > 1,
      }));
    } catch (err) {
      console.error('Failed to undo change:', err);

      // Rollback UI to original state
      onChannelsRestore(originalChannels);

      onError?.(`Failed to undo: ${lastChange.description}`);
    } finally {
      setIsOperationPending(false);
    }
  }, [
    isOperationPending,
    history.past,
    channels,
    onChannelsRestore,
    executeReverseOperation,
    onError,
  ]);

  // Redo previously undone change
  const redo = useCallback(async () => {
    if (isOperationPending) return;
    const nextChange = history.future[history.future.length - 1];
    if (!nextChange) return;

    setIsOperationPending(true);

    // Store original channels for rollback
    const originalChannels = [...channels];

    // Optimistically update UI from after snapshots
    const updatedChannels = channels.map((ch) => {
      const snapshot = nextChange.after.find((s) => s.id === ch.id);
      if (snapshot) {
        return {
          ...ch,
          channel_number: snapshot.channel_number,
          name: snapshot.name,
          channel_group_id: snapshot.channel_group_id,
          streams: [...snapshot.streams],
        };
      }
      return ch;
    });
    onChannelsRestore(updatedChannels);

    try {
      await executeForwardOperation(nextChange);

      // Success - update history
      setHistory((prev) => ({
        ...prev,
        past: [...prev.past, nextChange],
        future: prev.future.slice(0, -1),
        hasUnsavedChanges: true,
      }));
    } catch (err) {
      console.error('Failed to redo change:', err);

      // Rollback UI to original state
      onChannelsRestore(originalChannels);

      onError?.(`Failed to redo: ${nextChange.description}`);
    } finally {
      setIsOperationPending(false);
    }
  }, [
    isOperationPending,
    history.future,
    channels,
    onChannelsRestore,
    executeForwardOperation,
    onError,
  ]);

  // Create save point
  const createSavePoint = useCallback(
    (name?: string) => {
      const savePoint: SavePoint = {
        id: generateId(),
        name: name || `Checkpoint ${history.savePoints.length + 1}`,
        timestamp: Date.now(),
        historyIndex: history.past.length,
        channelSnapshot: channels.map(createSnapshot),
      };

      setHistory((prev) => ({
        ...prev,
        savePoints: [...prev.savePoints, savePoint],
        hasUnsavedChanges: false,
      }));
    },
    [channels, history.past.length, history.savePoints.length]
  );

  // Revert to save point
  const revertToSavePoint = useCallback(
    async (savePointId: string) => {
      if (isOperationPending) return;

      const savePoint = history.savePoints.find((sp) => sp.id === savePointId);
      if (!savePoint) return;

      const changesToUndo = history.past.length - savePoint.historyIndex;

      // Confirm if reverting many changes
      if (changesToUndo > 5) {
        const confirmed = window.confirm(
          `This will undo ${changesToUndo} changes. Are you sure?`
        );
        if (!confirmed) return;
      }

      setIsOperationPending(true);

      // Store original channels for rollback
      const originalChannels = [...channels];

      try {
        // Get changes to undo (in reverse order)
        const changesToRevert = history.past.slice(savePoint.historyIndex).reverse();

        // Execute reverse operations for each change
        for (const change of changesToRevert) {
          await executeReverseOperation(change);
        }

        // Restore channels from save point snapshot
        const restoredChannels = channels.map((ch) => {
          const snapshot = savePoint.channelSnapshot.find((s) => s.id === ch.id);
          if (snapshot) {
            return {
              ...ch,
              channel_number: snapshot.channel_number,
              name: snapshot.name,
              channel_group_id: snapshot.channel_group_id,
              streams: [...snapshot.streams],
            };
          }
          return ch;
        });

        onChannelsRestore(restoredChannels);

        setHistory((prev) => ({
          ...prev,
          past: prev.past.slice(0, savePoint.historyIndex),
          future: [], // Clear redo stack on revert
          hasUnsavedChanges: false,
        }));
      } catch (err) {
        console.error('Failed to revert to save point:', err);

        // Rollback UI to original state
        onChannelsRestore(originalChannels);

        onError?.(`Failed to revert to checkpoint: ${savePoint.name}`);
      } finally {
        setIsOperationPending(false);
      }
    },
    [
      isOperationPending,
      history.past,
      history.savePoints,
      channels,
      onChannelsRestore,
      executeReverseOperation,
      onError,
    ]
  );

  // Delete save point
  const deleteSavePoint = useCallback((savePointId: string) => {
    setHistory((prev) => ({
      ...prev,
      savePoints: prev.savePoints.filter((sp) => sp.id !== savePointId),
    }));
  }, []);

  // Clear all history
  const clearHistory = useCallback(() => {
    setHistory((prev) => ({
      ...prev,
      past: [],
      future: [],
      savePoints: [],
      hasUnsavedChanges: false,
    }));
  }, []);

  return {
    canUndo: history.past.length > 0,
    canRedo: history.future.length > 0,
    undoCount: history.past.length,
    redoCount: history.future.length,
    savePoints: history.savePoints,
    hasUnsavedChanges: history.hasUnsavedChanges,
    lastChange: history.past[history.past.length - 1] || null,
    isOperationPending,
    recordChange,
    undo,
    redo,
    createSavePoint,
    revertToSavePoint,
    deleteSavePoint,
    initializeBaseline,
    clearHistory,
  };
}
