/**
 * Unit tests for useEditMode hook.
 *
 * Currently focused on the working-copy sync effect that reconciles
 * `displayChannels` against the API channels prop while edit mode is
 * active. The original effect only ADDED new channels (e.g., from CSV
 * import) and never removed channels that disappeared from the API,
 * which left ghost rows in the UI after operations like
 * /api/channels/merge that delete source channels server-side.
 *
 * The fix removes channels with persisted IDs (id >= 0) that no longer
 * appear in the API response, while preserving locally-created
 * not-yet-saved channels (negative temp IDs assigned by
 * stageCreateChannel — see useEditMode.ts ~line 295). Without the
 * persisted-ID guard, the local-add path regresses on every sync.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useEditMode } from './useEditMode';
import type { Channel } from '../types';

// Mock the API module — useEditMode only invokes it on commit, which these
// tests do not exercise, but the import resolves at module load time.
vi.mock('../services/api', () => ({
  updateChannel: vi.fn().mockResolvedValue({}),
  bulkUpdateChannels: vi.fn().mockResolvedValue({}),
  bulkCreateChannels: vi.fn().mockResolvedValue([]),
  bulkDeleteChannels: vi.fn().mockResolvedValue({}),
  createChannelGroup: vi.fn().mockResolvedValue({}),
  deleteChannelGroup: vi.fn().mockResolvedValue({}),
  renameChannelGroup: vi.fn().mockResolvedValue({}),
}));

vi.mock('../utils/idGenerator', () => ({
  generateId: vi.fn(() => 'test-id'),
}));

function makeChannel(id: number, name: string): Channel {
  return {
    id,
    channel_number: id,
    name,
    channel_group_id: null,
    tvg_id: null,
    tvc_guide_stationid: null,
    epg_data_id: null,
    streams: [],
    stream_profile_id: null,
    uuid: `uuid-${id}`,
    logo_id: null,
    auto_created: false,
    auto_created_by: null,
    auto_created_by_name: null,
  };
}

describe('useEditMode — workingCopy ↔ API sync', () => {
  const mockOnChannelsChange = vi.fn();
  const mockOnError = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('removes channels from workingCopy when they disappear from the API (merge cleanup)', () => {
    // Simulates the merge bug: user enters edit mode with [1, 2, 3] visible,
    // then a backend merge deletes channels 2 and 3 (folded into channel 1).
    // App.loadChannels() refreshes the `channels` prop to [1] and the working
    // copy must drop the now-stale ghosts.
    const initial: Channel[] = [makeChannel(1, 'A'), makeChannel(2, 'B'), makeChannel(3, 'C')];

    const { result, rerender } = renderHook(
      ({ channels }: { channels: Channel[] }) =>
        useEditMode({
          channels,
          onChannelsChange: mockOnChannelsChange,
          onError: mockOnError,
        }),
      { initialProps: { channels: initial } }
    );

    act(() => {
      result.current.enterEditMode();
    });
    expect(result.current.displayChannels.map((c) => c.id)).toEqual([1, 2, 3]);

    // Simulate the post-merge channel list refresh.
    rerender({ channels: [makeChannel(1, 'A')] });

    expect(result.current.displayChannels.map((c) => c.id)).toEqual([1]);
  });

  it('preserves locally-created channels (negative temp IDs) that are not in the API yet', () => {
    // The persisted-ID guard: a channel staged via stageCreateChannel has a
    // negative id (e.g., -1). It will never appear in the API response until
    // commit — it must survive any number of sync passes triggered by other
    // channels being added/removed.
    const initial: Channel[] = [makeChannel(1, 'A')];

    const { result, rerender } = renderHook(
      ({ channels }: { channels: Channel[] }) =>
        useEditMode({
          channels,
          onChannelsChange: mockOnChannelsChange,
          onError: mockOnError,
        }),
      { initialProps: { channels: initial } }
    );

    act(() => {
      result.current.enterEditMode();
    });

    // Stage a local-add channel — gets temp ID -1 in the working copy.
    let localTempId = 0;
    act(() => {
      localTempId = result.current.stageCreateChannel('Local Channel');
    });
    expect(localTempId).toBeLessThan(0);
    expect(result.current.displayChannels.map((c) => c.id).sort((a, b) => a - b)).toEqual([
      localTempId,
      1,
    ]);

    // Trigger a sync by changing the API channels list (simulate any refresh
    // — e.g., a different operation completed or a poll fired). The local
    // negative-ID channel must survive even though it is absent from the
    // API response.
    rerender({ channels: [makeChannel(1, 'A')] });

    const remainingIds = result.current.displayChannels.map((c) => c.id).sort((a, b) => a - b);
    expect(remainingIds).toEqual([localTempId, 1]);
  });
});
