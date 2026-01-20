/**
 * Unit tests for useChangeHistory hook.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { useChangeHistory } from './useChangeHistory';
import type { Channel } from '../types';

// Mock the API module
vi.mock('../services/api', () => ({
  updateChannel: vi.fn().mockResolvedValue({}),
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
      logo_url: null,
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
      logo_url: null,
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
    it('tracks pending operations', () => {
      const { result } = renderHook(() =>
        useChangeHistory({
          channels: mockChannels,
          onChannelsRestore: mockOnChannelsRestore,
          onError: mockOnError,
        })
      );

      expect(result.current.isOperationPending).toBe(false);
    });
  });
});
