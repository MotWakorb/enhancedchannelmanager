import { useState, useCallback, useRef, useEffect } from 'react';
import type {
  Channel,
  ChangeRecord,
  SavePoint,
  HistoryState,
} from '../types';
import * as api from '../services/api';
import { HttpError } from '../services/httpClient';
import { numbersAgree } from '../utils/channelNumberConcurrency';
import { createSnapshot } from '../utils/channelSnapshot';
import { generateId } from '../utils/idGenerator';
import { logger } from '../utils/logger';

const MAX_HISTORY_SIZE = 100;

export interface UseChangeHistoryOptions {
  channels: Channel[];
  onChannelsRestore: (channels: Channel[]) => void;
  onError?: (message: string) => void;
  /**
   * Called when the server refused a numbering write because a channel moved
   * after this history recorded it. Some of a multi-channel change may already
   * have been written by then, so the caller should re-read the lineup rather
   * than trust the rolled-back view.
   */
  onServerChanged?: () => void;
}

type ChannelSnapshot = ChangeRecord['before'][number];

/** Channel reads in flight at once while checking a multi-channel step. */
const PRE_READ_CONCURRENCY = 8;

/** A history step found, before writing, to describe a channel that has moved. */
class MovedSinceRecordedError extends Error {}

/**
 * Whether `err` refuses a numbering write over a change made since the history
 * recorded it: the server's 409 with `concurrentNumberChanges`, or this hook's
 * own pre-read of a multi-channel step.
 */
function isNumberConflict(err: unknown): err is Error {
  if (err instanceof MovedSinceRecordedError) return true;
  if (!(err instanceof HttpError) || err.status !== 409) return false;
  const detail = err.detail as { concurrentNumberChanges?: unknown } | null | undefined;
  return Array.isArray(detail?.concurrentNumberChanges);
}

/**
 * The PATCH that moves one channel of a numbering step from `from` to `to`.
 *
 * A rename that left the number alone sends only the name, so a renumber made
 * since cannot block undoing it; anything that writes the number carries the
 * number `from` recorded as its baseline. `null` for a step that is not a
 * numbering step.
 */
function numberingPatch(
  type: ChangeRecord['type'],
  from: ChannelSnapshot,
  to: ChannelSnapshot,
): { data: Partial<Channel>; baseline?: api.ChannelNumberBaseline } | null {
  const baseline = { expectedNumber: from.channel_number ?? null };
  if (type === 'channel_reorder') {
    return { data: { channel_number: to.channel_number }, baseline };
  }
  if (type === 'channel_number_update' || type === 'channel_name_update') {
    if (numbersAgree(from.channel_number ?? null, to.channel_number ?? null)) {
      return { data: { name: to.name } };
    }
    return { data: { channel_number: to.channel_number, name: to.name }, baseline };
  }
  return null;
}

function formatNumber(value: number | null): string {
  return value === null ? 'no number' : String(value);
}

/**
 * Refuse a step that writes several channels' numbers when any of them has
 * moved, before the first write.
 *
 * Each PATCH is still checked by the server, but a refusal there lands after
 * the earlier channels were written. Reading them all first narrows that to the
 * window between these reads and the writes; nothing upstream offers a
 * conditional write across several channels, so it cannot be closed.
 */
async function refuseIfMoved(
  writes: { channelId: number; expectedNumber: number | null }[],
): Promise<void> {
  // One problem per write, in step order; `null` where the channel still holds
  // the number the step recorded.
  const problems: (string | null)[] = new Array(writes.length).fill(null);
  let next = 0;
  const readNext = async (): Promise<void> => {
    while (next < writes.length) {
      const index = next;
      next += 1;
      const { channelId, expectedNumber } = writes[index];
      let current: number | null;
      try {
        current = (await api.getChannel(channelId)).channel_number ?? null;
      } catch (err) {
        // Deleted upstream since the step was recorded: there is nothing to
        // put a number back on, and writing the rest would leave the step
        // half applied. Any other read failure is not a refusal.
        if (err instanceof HttpError && err.status === 404) {
          problems[index] = `channel ${channelId} no longer exists`;
          continue;
        }
        throw err;
      }
      if (!numbersAgree(current, expectedNumber)) {
        problems[index] = `channel ${channelId} is on channel number ${formatNumber(current)}, `
          + `and this step expected ${formatNumber(expectedNumber)}`;
      }
    }
  };
  await Promise.all(
    Array.from({ length: Math.min(PRE_READ_CONCURRENCY, writes.length) }, readNext),
  );
  const moved = problems.filter((problem): problem is string => problem !== null);
  if (moved.length > 0) {
    throw new MovedSinceRecordedError(
      `${moved.length === 1 ? 'Channel was' : 'Channels were'} not renumbered: `
      + `${moved.join('; ')}. Somebody else changed it. Nothing in this step was changed.`,
    );
  }
}

/**
 * `prev` with new stacks, recomputing whether anything is unsaved since the
 * latest surviving save point.
 *
 * Save points past the new end of `past` are dropped. So is every save point at
 * or after `droppedFrom`, the lowest index in `prev.past` of an entry removed
 * without being undone: its snapshot holds that entry's channel at a number the
 * server no longer has, and reverting to it would undo nothing and then show
 * that number anyway.
 */
function withStacks(
  prev: HistoryState,
  past: ChangeRecord[],
  future: ChangeRecord[],
  droppedFrom: number = Infinity,
): HistoryState {
  const savePoints = prev.savePoints.filter(
    (sp) => sp.historyIndex <= past.length && sp.historyIndex < droppedFrom,
  );
  const saved = savePoints.length > 0 ? savePoints[savePoints.length - 1].historyIndex : 0;
  return { ...prev, past, future, savePoints, hasUnsavedChanges: past.length !== saved };
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
  onServerChanged,
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
      const numberWrites = change.before.flatMap((beforeSnapshot) => {
        const afterSnapshot = change.after.find((a) => a.id === beforeSnapshot.id);
        if (!afterSnapshot || !channelMapRef.current.has(beforeSnapshot.id)) return [];
        const patch = numberingPatch(change.type, afterSnapshot, beforeSnapshot);
        return patch?.baseline ? [{ channelId: beforeSnapshot.id, ...patch.baseline }] : [];
      });
      // One write is checked by the server before it lands; see refuseIfMoved.
      if (numberWrites.length > 1) await refuseIfMoved(numberWrites);
      for (const beforeSnapshot of change.before) {
        const afterSnapshot = change.after.find((a) => a.id === beforeSnapshot.id);
        if (!afterSnapshot) continue;

        // Check if channel still exists
        const channelExists = channelMapRef.current.has(beforeSnapshot.id);
        if (!channelExists) {
          logger.warn(
            `Channel ${beforeSnapshot.id} no longer exists, skipping undo for this channel`
          );
          continue;
        }

        switch (change.type) {
          case 'channel_number_update':
          case 'channel_name_update':
          case 'channel_reorder': {
            // The number this change left the channel on is the baseline: a
            // channel moved since then is refused rather than overwritten.
            const patch = numberingPatch(change.type, afterSnapshot, beforeSnapshot)!;
            await api.updateChannel(beforeSnapshot.id, patch.data, patch.baseline);
            break;
          }

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
        }
      }
    },
    []
  );

  // Execute forward operation for redo
  const executeForwardOperation = useCallback(
    async (change: ChangeRecord): Promise<void> => {
      const numberWrites = change.after.flatMap((afterSnapshot) => {
        const beforeSnapshot = change.before.find((b) => b.id === afterSnapshot.id);
        if (!beforeSnapshot || !channelMapRef.current.has(afterSnapshot.id)) return [];
        const patch = numberingPatch(change.type, beforeSnapshot, afterSnapshot);
        return patch?.baseline ? [{ channelId: afterSnapshot.id, ...patch.baseline }] : [];
      });
      if (numberWrites.length > 1) await refuseIfMoved(numberWrites);
      for (const afterSnapshot of change.after) {
        const beforeSnapshot = change.before.find((b) => b.id === afterSnapshot.id);
        if (!beforeSnapshot) continue;

        // Check if channel still exists
        const channelExists = channelMapRef.current.has(afterSnapshot.id);
        if (!channelExists) {
          logger.warn(
            `Channel ${afterSnapshot.id} no longer exists, skipping redo for this channel`
          );
          continue;
        }

        switch (change.type) {
          case 'channel_number_update':
          case 'channel_name_update':
          case 'channel_reorder': {
            // Undo left the channel on the before number, so that is the baseline.
            const patch = numberingPatch(change.type, beforeSnapshot, afterSnapshot)!;
            await api.updateChannel(afterSnapshot.id, patch.data, patch.baseline);
            break;
          }

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
      logger.error('Failed to undo change:', err);

      // Rollback UI to original state
      onChannelsRestore(originalChannels);

      if (isNumberConflict(err)) {
        // The entry's recorded numbers no longer describe the server, so it
        // can never be replayed as recorded: kept, it would refuse on every
        // press and block the entries behind it; moved to redo, it would claim
        // an undo that did not happen. It is dropped, and the lineup re-read.
        setHistory((prev) => {
          const dropped = prev.past.findIndex((entry) => entry.id === lastChange.id);
          return withStacks(
            prev,
            prev.past.filter((entry) => entry.id !== lastChange.id),
            prev.future,
            dropped === -1 ? Infinity : dropped,
          );
        });
        onError?.(`Could not undo "${lastChange.description}": ${err.message} It was removed from the undo history.`);
        onServerChanged?.();
      } else {
        onError?.(`Failed to undo: ${lastChange.description}`);
      }
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
    onServerChanged,
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
      logger.error('Failed to redo change:', err);

      // Rollback UI to original state
      onChannelsRestore(originalChannels);

      if (isNumberConflict(err)) {
        // Dropped for the reason a refused undo is: see `undo`.
        setHistory((prev) => withStacks(
          prev, prev.past, prev.future.filter((entry) => entry.id !== nextChange.id),
        ));
        onError?.(`Could not redo "${nextChange.description}": ${err.message} It was removed from the redo history.`);
        onServerChanged?.();
      } else {
        onError?.(`Failed to redo: ${nextChange.description}`);
      }
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
    onServerChanged,
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

      // Get changes to undo (in reverse order)
      const changesToRevert = history.past.slice(savePoint.historyIndex).reverse();
      let reverted = 0;

      try {
        // Execute reverse operations for each change
        for (const change of changesToRevert) {
          await executeReverseOperation(change);
          reverted += 1;
        }

        // Restore, from the save point's snapshot, only the channels the
        // reverted entries wrote. Any other channel may have changed outside
        // this history (an entry dropped after a refusal, or an edit undo does
        // not replay), so its snapshot is not what the server holds; the reload
        // below shows the server's copy of every channel.
        const touched = new Set(
          changesToRevert.flatMap((change) => [...change.before, ...change.after].map((snap) => snap.id)),
        );
        const restoredChannels = channels.map((ch) => {
          const snapshot = touched.has(ch.id)
            ? savePoint.channelSnapshot.find((s) => s.id === ch.id)
            : undefined;
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
        onServerChanged?.();

        setHistory((prev) => ({
          ...prev,
          past: prev.past.slice(0, savePoint.historyIndex),
          future: [], // Clear redo stack on revert
          hasUnsavedChanges: false,
        }));
      } catch (err) {
        logger.error('Failed to revert to save point:', err);

        // Rollback UI to original state
        onChannelsRestore(originalChannels);

        if (isNumberConflict(err)) {
          // The entries already reverted are undone on the server, and the
          // refused one can never be replayed as recorded, so all of them
          // leave the history. Older entries were never touched and stay.
          const gone = new Set(changesToRevert.slice(0, reverted + 1).map((change) => change.id));
          setHistory((prev) => {
            const indexes = prev.past.flatMap((entry, index) => (gone.has(entry.id) ? [index] : []));
            return withStacks(
              prev,
              prev.past.filter((entry) => !gone.has(entry.id)),
              [],
              indexes.length > 0 ? Math.min(...indexes) : Infinity,
            );
          });
          onError?.(`Could not revert to checkpoint "${savePoint.name}": ${err.message}`);
          onServerChanged?.();
        } else {
          onError?.(`Failed to revert to checkpoint: ${savePoint.name}`);
        }
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
      onServerChanged,
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
