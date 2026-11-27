/**
 * Unit tests for useChangeHistory hook.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useChangeHistory } from './useChangeHistory';
import type { Channel, ChangeRecord } from '../types';
import { HttpError } from '../services/httpClient';

// Mock the API module
vi.mock('../services/api', () => ({
  updateChannel: vi.fn().mockResolvedValue({}),
  getChannel: vi.fn(),
  addStreamToChannel: vi.fn().mockResolvedValue({}),
  removeStreamFromChannel: vi.fn().mockResolvedValue({}),
  reorderChannelStreams: vi.fn().mockResolvedValue({}),
}));

// Mock ID generator for predictable IDs
vi.mock('../utils/idGenerator', () => ({
  generateId: vi.fn(() => `test-id-${Math.random().toString(36).substr(2, 9)}`),
}));

describe('useChangeHistory', () => {
  const mockChannels: Channel[] = [
    {
      id: 1,
      uuid: 'uuid-1',
      name: 'Channel 1',
      channel_number: 100,
      channel_group_id: 1,
      streams: [1, 2, 3],
      tvg_id: null,
      epg_data_id: null,
      tvc_guide_stationid: null,
      stream_profile_id: null,
      logo_id: null,
      auto_created: false,
      auto_created_by: null,
      auto_created_by_name: null,
    },
    {
      id: 2,
      uuid: 'uuid-2',
      name: 'Channel 2',
      channel_number: 200,
      channel_group_id: 1,
      streams: [4, 5],
      tvg_id: null,
      epg_data_id: null,
      tvc_guide_stationid: null,
      stream_profile_id: null,
      logo_id: null,
      auto_created: false,
      auto_created_by: null,
      auto_created_by_name: null,
    },
  ];

  const mockOnChannelsRestore = vi.fn();
  const mockOnError = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('initial state', () => {
    it('starts with empty history', () => {
      const { result } = renderHook(() =>
        useChangeHistory({
          channels: mockChannels,
          onChannelsRestore: mockOnChannelsRestore,
          onError: mockOnError,
        })
      );

      expect(result.current.canUndo).toBe(false);
      expect(result.current.canRedo).toBe(false);
      expect(result.current.undoCount).toBe(0);
      expect(result.current.redoCount).toBe(0);
      expect(result.current.hasUnsavedChanges).toBe(false);
      expect(result.current.lastChange).toBeNull();
    });
  });

  describe('initializeBaseline', () => {
    it('sets baseline from channels', () => {
      const { result } = renderHook(() =>
        useChangeHistory({
          channels: mockChannels,
          onChannelsRestore: mockOnChannelsRestore,
          onError: mockOnError,
        })
      );

      act(() => {
        result.current.initializeBaseline(mockChannels);
      });

      // After initialization, history should still be empty
      expect(result.current.undoCount).toBe(0);
      expect(result.current.hasUnsavedChanges).toBe(false);
    });
  });

  describe('recordChange', () => {
    it('records a change to history', () => {
      const { result } = renderHook(() =>
        useChangeHistory({
          channels: mockChannels,
          onChannelsRestore: mockOnChannelsRestore,
          onError: mockOnError,
        })
      );

      act(() => {
        result.current.recordChange({
          type: 'channel_name_update',
          description: 'Changed channel name',
          channelIds: [1],
          before: [{ id: 1, channel_number: 100, name: 'Old Name', channel_group_id: 1, streams: [1, 2, 3] }],
          after: [{ id: 1, channel_number: 100, name: 'New Name', channel_group_id: 1, streams: [1, 2, 3] }],
        });
      });

      expect(result.current.canUndo).toBe(true);
      expect(result.current.undoCount).toBe(1);
      expect(result.current.hasUnsavedChanges).toBe(true);
      expect(result.current.lastChange?.type).toBe('channel_name_update');
    });

    it('clears redo stack on new change', () => {
      const { result } = renderHook(() =>
        useChangeHistory({
          channels: mockChannels,
          onChannelsRestore: mockOnChannelsRestore,
          onError: mockOnError,
        })
      );

      // Record first change
      act(() => {
        result.current.recordChange({
          type: 'channel_name_update',
          description: 'First change',
          channelIds: [1],
          before: [{ id: 1, channel_number: 100, name: 'Old', channel_group_id: 1, streams: [] }],
          after: [{ id: 1, channel_number: 100, name: 'New', channel_group_id: 1, streams: [] }],
        });
      });

      // Note: Full undo/redo testing would require more complex setup with API mocks
      // For now, we verify the basic recording functionality
      expect(result.current.undoCount).toBe(1);
      expect(result.current.redoCount).toBe(0);
    });

    it('trims history when exceeding max size', () => {
      const { result } = renderHook(() =>
        useChangeHistory({
          channels: mockChannels,
          onChannelsRestore: mockOnChannelsRestore,
          onError: mockOnError,
        })
      );

      // Record more than MAX_HISTORY_SIZE (100) changes
      act(() => {
        for (let i = 0; i < 110; i++) {
          result.current.recordChange({
            type: 'channel_name_update',
            description: `Change ${i}`,
            channelIds: [1],
            before: [{ id: 1, channel_number: 100, name: `Name ${i}`, channel_group_id: 1, streams: [] }],
            after: [{ id: 1, channel_number: 100, name: `Name ${i + 1}`, channel_group_id: 1, streams: [] }],
          });
        }
      });

      expect(result.current.undoCount).toBe(100);
    });
  });

  describe('createSavePoint', () => {
    it('creates a save point', () => {
      const { result } = renderHook(() =>
        useChangeHistory({
          channels: mockChannels,
          onChannelsRestore: mockOnChannelsRestore,
          onError: mockOnError,
        })
      );

      act(() => {
        result.current.createSavePoint('My Checkpoint');
      });

      expect(result.current.savePoints).toHaveLength(1);
      expect(result.current.savePoints[0].name).toBe('My Checkpoint');
    });

    it('auto-generates name if not provided', () => {
      const { result } = renderHook(() =>
        useChangeHistory({
          channels: mockChannels,
          onChannelsRestore: mockOnChannelsRestore,
          onError: mockOnError,
        })
      );

      act(() => {
        result.current.createSavePoint();
      });

      expect(result.current.savePoints[0].name).toContain('Checkpoint');
    });

    it('marks changes as saved', () => {
      const { result } = renderHook(() =>
        useChangeHistory({
          channels: mockChannels,
          onChannelsRestore: mockOnChannelsRestore,
          onError: mockOnError,
        })
      );

      // Record a change
      act(() => {
        result.current.recordChange({
          type: 'channel_name_update',
          description: 'Change',
          channelIds: [1],
          before: [{ id: 1, channel_number: 100, name: 'Old', channel_group_id: 1, streams: [] }],
          after: [{ id: 1, channel_number: 100, name: 'New', channel_group_id: 1, streams: [] }],
        });
      });

      expect(result.current.hasUnsavedChanges).toBe(true);

      // Create save point
      act(() => {
        result.current.createSavePoint();
      });

      expect(result.current.hasUnsavedChanges).toBe(false);
    });
  });

  describe('deleteSavePoint', () => {
    it('removes a save point', () => {
      const { result } = renderHook(() =>
        useChangeHistory({
          channels: mockChannels,
          onChannelsRestore: mockOnChannelsRestore,
          onError: mockOnError,
        })
      );

      act(() => {
        result.current.createSavePoint('Test');
      });

      const savePointId = result.current.savePoints[0].id;

      act(() => {
        result.current.deleteSavePoint(savePointId);
      });

      expect(result.current.savePoints).toHaveLength(0);
    });
  });

  describe('clearHistory', () => {
    it('clears all history', () => {
      const { result } = renderHook(() =>
        useChangeHistory({
          channels: mockChannels,
          onChannelsRestore: mockOnChannelsRestore,
          onError: mockOnError,
        })
      );

      // Record changes and create save point
      act(() => {
        result.current.recordChange({
          type: 'channel_name_update',
          description: 'Change',
          channelIds: [1],
          before: [{ id: 1, channel_number: 100, name: 'Old', channel_group_id: 1, streams: [] }],
          after: [{ id: 1, channel_number: 100, name: 'New', channel_group_id: 1, streams: [] }],
        });
        result.current.createSavePoint();
      });

      // Clear history
      act(() => {
        result.current.clearHistory();
      });

      expect(result.current.undoCount).toBe(0);
      expect(result.current.redoCount).toBe(0);
      expect(result.current.savePoints).toHaveLength(0);
      expect(result.current.hasUnsavedChanges).toBe(false);
    });
  });

  describe('isOperationPending', () => {
    it('flips to true during undo and back to false when settled', async () => {
      const { updateChannel } = await import('../services/api');

      // Slow the API call so we can observe the in-flight state
      let resolveUpdate!: () => void;
      vi.mocked(updateChannel).mockImplementation(
        () => new Promise((resolve) => { resolveUpdate = resolve as () => void; })
      );

      const { result } = renderHook(() =>
        useChangeHistory({
          channels: mockChannels,
          onChannelsRestore: mockOnChannelsRestore,
          onError: mockOnError,
        })
      );

      // Record a channel_name_update change so undo has something to reverse
      act(() => {
        result.current.recordChange({
          type: 'channel_name_update',
          description: 'Name change',
          channelIds: [1],
          before: [{ id: 1, channel_number: 100, name: 'Old', channel_group_id: 1, streams: [] }],
          after: [{ id: 1, channel_number: 100, name: 'New', channel_group_id: 1, streams: [] }],
        });
      });

      expect(result.current.isOperationPending).toBe(false);

      // Start the undo without awaiting — observe in-flight state
      act(() => {
        result.current.undo();
      });

      expect(result.current.isOperationPending).toBe(true);

      // Settle the API call and confirm pending clears
      await act(async () => {
        resolveUpdate();
        await Promise.resolve();
      });

      expect(result.current.isOperationPending).toBe(false);
    });
  });
  describe('numbering baselines on undo and redo', () => {
    const conflictMessage = 'Channel was not renumbered: channel 1 is on channel number 150, '
      + 'and this request expected 300. Somebody else changed it.';

    const conflict = () => new HttpError(conflictMessage, 409, {
      message: conflictMessage,
      concurrentNumberChanges: [{ channelId: 1, baselineNumber: 300, serverNumber: 150 }],
    });

    function renumberRecord(): Omit<ChangeRecord, 'id' | 'timestamp'> {
      return {
        type: 'channel_number_update',
        description: 'Changed channel number from 100 to 300',
        channelIds: [1],
        before: [{ id: 1, channel_number: 100, name: 'Channel 1', channel_group_id: 1, streams: [] }],
        after: [{ id: 1, channel_number: 300, name: 'Channel 1', channel_group_id: 1, streams: [] }],
      };
    }

    function reorderRecord(): Omit<ChangeRecord, 'id' | 'timestamp'> {
      return {
        type: 'channel_reorder',
        description: 'Reordered 2 channels starting at 500',
        channelIds: [1, 2],
        before: [
          { id: 1, channel_number: 100, name: 'Channel 1', channel_group_id: 1, streams: [] },
          { id: 2, channel_number: null, name: 'Channel 2', channel_group_id: 1, streams: [] },
        ],
        after: [
          { id: 1, channel_number: 500, name: 'Channel 1', channel_group_id: 1, streams: [] },
          { id: 2, channel_number: 501, name: 'Channel 2', channel_group_id: 1, streams: [] },
        ],
      };
    }

    function renderHistory(onServerChanged = vi.fn()) {
      const rendered = renderHook(() =>
        useChangeHistory({
          channels: mockChannels,
          onChannelsRestore: mockOnChannelsRestore,
          onError: mockOnError,
          onServerChanged,
        })
      );
      return { ...rendered, onServerChanged };
    }

    // What the server holds, read by getChannel and moved by updateChannel.
    let server: Map<number, number | null>;

    beforeEach(async () => {
      const { updateChannel, getChannel } = await import('../services/api');
      server = new Map([[1, 300], [2, 200]]);
      vi.mocked(updateChannel).mockReset().mockImplementation(async (id, data) => {
        if ('channel_number' in data) server.set(id, data.channel_number ?? null);
        return {} as Channel;
      });
      vi.mocked(getChannel).mockReset().mockImplementation(async (id) => (
        { id, channel_number: server.get(id) ?? null } as Channel
      ));
    });

    it('undo of a renumber expects the number the change put the channel on', async () => {
      const { updateChannel } = await import('../services/api');
      const { result } = renderHistory();
      act(() => result.current.recordChange(renumberRecord()));

      await act(async () => { await result.current.undo(); });

      expect(updateChannel).toHaveBeenCalledWith(
        1, { channel_number: 100, name: 'Channel 1' }, { expectedNumber: 300 },
      );
    });

    it('redo of a renumber expects the number undo put back', async () => {
      const { updateChannel } = await import('../services/api');
      const { result } = renderHistory();
      act(() => result.current.recordChange(renumberRecord()));
      await act(async () => { await result.current.undo(); });
      vi.mocked(updateChannel).mockClear();

      await act(async () => { await result.current.redo(); });

      expect(updateChannel).toHaveBeenCalledWith(
        1, { channel_number: 300, name: 'Channel 1' }, { expectedNumber: 100 },
      );
    });

    function renameRecord(beforeNumber: number, afterNumber: number): Omit<ChangeRecord, 'id' | 'timestamp'> {
      return {
        type: 'channel_name_update',
        description: 'Renamed',
        channelIds: [1],
        before: [{ id: 1, channel_number: beforeNumber, name: 'Old', channel_group_id: 1, streams: [] }],
        after: [{ id: 1, channel_number: afterNumber, name: 'New', channel_group_id: 1, streams: [] }],
      };
    }

    it('undo and redo of a pure rename send only the name, so a renumber since cannot block them', async () => {
      const { updateChannel } = await import('../services/api');
      const { result } = renderHistory();
      act(() => result.current.recordChange(renameRecord(100, 100)));

      await act(async () => { await result.current.undo(); });
      await act(async () => { await result.current.redo(); });

      expect(vi.mocked(updateChannel).mock.calls).toEqual([
        [1, { name: 'Old' }, undefined],
        [1, { name: 'New' }, undefined],
      ]);
    });

    it('undo of a rename that also moved the number still carries a baseline', async () => {
      const { updateChannel } = await import('../services/api');
      const { result } = renderHistory();
      act(() => result.current.recordChange(renameRecord(100, 300)));

      await act(async () => { await result.current.undo(); });

      expect(updateChannel).toHaveBeenCalledWith(
        1, { channel_number: 100, name: 'Old' }, { expectedNumber: 300 },
      );
    });

    it('undo and redo of a range reorder carry each channel\'s recorded number', async () => {
      const { updateChannel } = await import('../services/api');
      server = new Map([[1, 500], [2, 501]]);
      const { result } = renderHistory();
      act(() => result.current.recordChange(reorderRecord()));

      await act(async () => { await result.current.undo(); });
      expect(vi.mocked(updateChannel).mock.calls).toEqual([
        [1, { channel_number: 100 }, { expectedNumber: 500 }],
        [2, { channel_number: null }, { expectedNumber: 501 }],
      ]);

      vi.mocked(updateChannel).mockClear();
      await act(async () => { await result.current.redo(); });
      expect(vi.mocked(updateChannel).mock.calls).toEqual([
        [1, { channel_number: 500 }, { expectedNumber: 100 }],
        [2, { channel_number: 501 }, { expectedNumber: null }],
      ]);
    });

    it('a multi-channel undo reads every channel before it writes any', async () => {
      const { updateChannel, getChannel } = await import('../services/api');
      server = new Map([[1, 500], [2, 501]]);
      const { result } = renderHistory();
      act(() => result.current.recordChange(reorderRecord()));

      await act(async () => { await result.current.undo(); });

      expect(vi.mocked(getChannel).mock.calls.map(([id]) => id)).toEqual([1, 2]);
      const lastRead = Math.max(...vi.mocked(getChannel).mock.invocationCallOrder);
      const firstWrite = Math.min(...vi.mocked(updateChannel).mock.invocationCallOrder);
      expect(lastRead).toBeLessThan(firstWrite);
    });

    it('a multi-channel undo with one moved channel writes nothing and drops the entry', async () => {
      const { updateChannel } = await import('../services/api');
      server = new Map([[1, 500], [2, 777]]);
      const { result, onServerChanged } = renderHistory();
      act(() => result.current.recordChange(reorderRecord()));

      await act(async () => { await result.current.undo(); });

      expect(updateChannel).not.toHaveBeenCalled();
      expect(mockOnError).toHaveBeenCalledWith(expect.stringMatching(
        /channel 2 is on channel number 777, and this step expected 501\. Somebody else changed it/,
      ));
      expect(result.current.undoCount).toBe(0);
      expect(result.current.redoCount).toBe(0);
      expect(onServerChanged).toHaveBeenCalledTimes(1);
    });

    it('a multi-channel redo with one moved channel writes nothing and drops the entry', async () => {
      const { updateChannel } = await import('../services/api');
      server = new Map([[1, 500], [2, 501]]);
      const { result, onServerChanged } = renderHistory();
      act(() => result.current.recordChange(reorderRecord()));
      await act(async () => { await result.current.undo(); });
      server.set(2, 9);
      vi.mocked(updateChannel).mockClear();

      await act(async () => { await result.current.redo(); });

      expect(updateChannel).not.toHaveBeenCalled();
      expect(mockOnError).toHaveBeenCalledWith(expect.stringMatching(
        /channel 2 is on channel number 9, and this step expected no number/,
      ));
      expect(result.current.redoCount).toBe(0);
      expect(onServerChanged).toHaveBeenCalledTimes(1);
    });

    it('the pre-read compares on the tenths grid, as the server does', async () => {
      const { updateChannel } = await import('../services/api');
      server = new Map([[1, 500.0], [2, 501.04]]);
      const { result } = renderHistory();
      act(() => result.current.recordChange(reorderRecord()));

      await act(async () => { await result.current.undo(); });

      expect(updateChannel).toHaveBeenCalledTimes(2);
    });

    it('a single-channel undo leaves the check to the server and makes no read', async () => {
      const { getChannel } = await import('../services/api');
      const { result } = renderHistory();
      act(() => result.current.recordChange(renumberRecord()));

      await act(async () => { await result.current.undo(); });

      expect(getChannel).not.toHaveBeenCalled();
    });

    it('a dropped undo invalidates the save points past the new end and recomputes unsaved', async () => {
      const { updateChannel } = await import('../services/api');
      const { result } = renderHistory();
      act(() => result.current.recordChange(renumberRecord()));
      act(() => result.current.createSavePoint('after first'));
      act(() => result.current.recordChange(renumberRecord()));
      act(() => result.current.createSavePoint('after second'));
      act(() => result.current.recordChange(renumberRecord()));
      vi.mocked(updateChannel).mockRejectedValueOnce(conflict());

      await act(async () => { await result.current.undo(); });

      expect(result.current.undoCount).toBe(2);
      expect(result.current.savePoints.map((sp) => sp.name)).toEqual(['after first', 'after second']);
      expect(result.current.hasUnsavedChanges).toBe(false);

      vi.mocked(updateChannel).mockRejectedValueOnce(conflict());
      await act(async () => { await result.current.undo(); });

      expect(result.current.undoCount).toBe(1);
      expect(result.current.savePoints.map((sp) => sp.name)).toEqual(['after first']);
      expect(result.current.hasUnsavedChanges).toBe(false);
    });

    it('a refused undo shows the server\'s reason, drops the entry, and reloads', async () => {
      const { updateChannel } = await import('../services/api');
      const { result, onServerChanged } = renderHistory();
      act(() => result.current.recordChange(renumberRecord()));
      act(() => result.current.recordChange(renumberRecord()));
      vi.mocked(updateChannel).mockRejectedValueOnce(conflict());

      await act(async () => { await result.current.undo(); });

      expect(mockOnError).toHaveBeenCalledWith(expect.stringContaining(conflictMessage));
      // Neither replayable as recorded nor done: off the undo stack, not on redo.
      expect(result.current.undoCount).toBe(1);
      expect(result.current.redoCount).toBe(0);
      expect(onServerChanged).toHaveBeenCalledTimes(1);
      expect(result.current.isOperationPending).toBe(false);
    });

    it('a refused redo shows the server\'s reason and drops the entry from redo only', async () => {
      const { updateChannel } = await import('../services/api');
      const { result, onServerChanged } = renderHistory();
      act(() => result.current.recordChange(renumberRecord()));
      await act(async () => { await result.current.undo(); });
      vi.mocked(updateChannel).mockRejectedValueOnce(conflict());

      await act(async () => { await result.current.redo(); });

      expect(mockOnError).toHaveBeenCalledWith(expect.stringContaining(conflictMessage));
      expect(result.current.undoCount).toBe(0);
      expect(result.current.redoCount).toBe(0);
      expect(onServerChanged).toHaveBeenCalledTimes(1);
    });

    it('any other undo failure keeps the entry where it was', async () => {
      const { updateChannel } = await import('../services/api');
      const { result, onServerChanged } = renderHistory();
      act(() => result.current.recordChange(renumberRecord()));
      vi.mocked(updateChannel).mockRejectedValueOnce(new HttpError('Internal server error', 500));

      await act(async () => { await result.current.undo(); });

      expect(mockOnError).toHaveBeenCalledWith('Failed to undo: Changed channel number from 100 to 300');
      expect(result.current.undoCount).toBe(1);
      expect(result.current.redoCount).toBe(0);
      expect(onServerChanged).not.toHaveBeenCalled();
    });

    it('a 409 that is not a numbering refusal keeps the entry where it was', async () => {
      const { updateChannel } = await import('../services/api');
      const { result, onServerChanged } = renderHistory();
      act(() => result.current.recordChange(renumberRecord()));
      vi.mocked(updateChannel).mockRejectedValueOnce(new HttpError('Conflict upstream', 409, 'Conflict upstream'));

      await act(async () => { await result.current.undo(); });

      expect(result.current.undoCount).toBe(1);
      expect(onServerChanged).not.toHaveBeenCalled();
    });

    it('a refused checkpoint revert shows the server\'s reason and reloads', async () => {
      const { updateChannel } = await import('../services/api');
      const { result, onServerChanged } = renderHistory();
      act(() => result.current.createSavePoint('start'));
      act(() => result.current.recordChange(renumberRecord()));
      vi.mocked(updateChannel).mockRejectedValueOnce(conflict());

      await act(async () => {
        await result.current.revertToSavePoint(result.current.savePoints[0].id);
      });

      expect(mockOnError).toHaveBeenCalledWith(expect.stringContaining(conflictMessage));
      expect(onServerChanged).toHaveBeenCalledTimes(1);
    });

    function renumberOf(id: number, from: number, to: number): Omit<ChangeRecord, 'id' | 'timestamp'> {
      return {
        type: 'channel_number_update',
        description: `Changed channel ${id} from ${from} to ${to}`,
        channelIds: [id],
        before: [{ id, channel_number: from, name: `Channel ${id}`, channel_group_id: 1, streams: [] }],
        after: [{ id, channel_number: to, name: `Channel ${id}`, channel_group_id: 1, streams: [] }],
      };
    }

    it('a checkpoint revert refused partway trims the entries it reverted and the refused one', async () => {
      const { updateChannel } = await import('../services/api');
      const { result } = renderHistory();
      act(() => result.current.recordChange(renumberOf(1, 100, 110)));
      act(() => result.current.createSavePoint('one'));
      act(() => result.current.recordChange(renumberOf(1, 110, 120)));
      act(() => result.current.recordChange(renumberOf(2, 200, 210)));
      act(() => result.current.createSavePoint('three'));
      act(() => result.current.recordChange(renumberOf(2, 210, 220)));
      // Reverting to 'one' undoes the last three: 220 -> 210 lands, then the
      // undo of 200 -> 210 is refused, and 110 -> 120 is never attempted.
      vi.mocked(updateChannel)
        .mockImplementationOnce(async () => ({} as Channel))
        .mockRejectedValueOnce(conflict());

      await act(async () => {
        await result.current.revertToSavePoint(result.current.savePoints[0].id);
      });

      expect(vi.mocked(updateChannel).mock.calls.map(([id, data]) => [id, data.channel_number])).toEqual([
        [2, 210], [2, 200],
      ]);
      expect(result.current.undoCount).toBe(2);
      expect(result.current.lastChange?.description).toBe('Changed channel 1 from 110 to 120');
      expect(result.current.redoCount).toBe(0);
      expect(result.current.savePoints.map((sp) => sp.name)).toEqual(['one']);
      expect(result.current.hasUnsavedChanges).toBe(true);
    });

    it('a checkpoint revert refused on its first entry leaves the earlier entries alone', async () => {
      const { updateChannel } = await import('../services/api');
      const { result, onServerChanged } = renderHistory();
      act(() => result.current.createSavePoint('start'));
      act(() => result.current.recordChange(renumberOf(1, 100, 110)));
      act(() => result.current.recordChange(renumberOf(2, 200, 210)));
      vi.mocked(updateChannel).mockRejectedValueOnce(conflict());

      await act(async () => {
        await result.current.revertToSavePoint(result.current.savePoints[0].id);
      });

      expect(result.current.undoCount).toBe(1);
      expect(result.current.lastChange?.description).toBe('Changed channel 1 from 100 to 110');
      expect(result.current.savePoints.map((sp) => sp.name)).toEqual(['start']);
      expect(result.current.hasUnsavedChanges).toBe(true);
      expect(onServerChanged).toHaveBeenCalledTimes(1);
    });
  });
});
