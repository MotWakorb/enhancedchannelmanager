/**
 * Unit tests for useNormalizePreview (bd-eio04.13).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { useNormalizePreview, NORMALIZE_PREVIEW_BATCH_SIZE } from './useNormalizePreview';

vi.mock('../services/api', () => ({
  getChannelsNormalizePreviewBatch: vi.fn(),
}));

// Import the mocked module AFTER vi.mock so we can adjust behavior per test.
import * as api from '../services/api';

const makePreview = (id: number, current: string, proposed: string) => ({
  channel_id: id,
  current_name: current,
  proposed_name: proposed,
  would_change: proposed !== current,
  transformations: [],
});

describe('useNormalizePreview', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('returns an empty map when no channels are provided', () => {
    const { result } = renderHook(() => useNormalizePreview([]));
    expect(result.current.previews.size).toBe(0);
    expect(api.getChannelsNormalizePreviewBatch).not.toHaveBeenCalled();
  });

  it('fetches a single batch for small channel lists', async () => {
    (api.getChannelsNormalizePreviewBatch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      results: [
        makePreview(1, 'ESPN HD', 'ESPN'),
        makePreview(2, 'CNN', 'CNN'),
      ],
    });

    const { result } = renderHook(() =>
      useNormalizePreview([
        { id: 1, name: 'ESPN HD' },
        { id: 2, name: 'CNN' },
      ])
    );

    await waitFor(() => expect(result.current.previews.size).toBe(2));
    expect(result.current.previews.get(1)?.would_change).toBe(true);
    expect(result.current.previews.get(1)?.proposed_name).toBe('ESPN');
    expect(result.current.previews.get(2)?.would_change).toBe(false);

    expect(api.getChannelsNormalizePreviewBatch).toHaveBeenCalledTimes(1);
  });

  it('is a no-op when enabled=false', () => {
    renderHook(() =>
      useNormalizePreview([{ id: 1, name: 'x' }], { enabled: false })
    );
    expect(api.getChannelsNormalizePreviewBatch).not.toHaveBeenCalled();
  });

  it('splits large lists into batches of NORMALIZE_PREVIEW_BATCH_SIZE', async () => {
    (api.getChannelsNormalizePreviewBatch as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce({ results: [] })
      .mockResolvedValueOnce({ results: [] });

    const channels = Array.from(
      { length: NORMALIZE_PREVIEW_BATCH_SIZE + 5 },
      (_, i) => ({ id: i + 1, name: `ch-${i + 1}` })
    );
    renderHook(() => useNormalizePreview(channels));

    await waitFor(() =>
      expect(api.getChannelsNormalizePreviewBatch).toHaveBeenCalledTimes(2)
    );

    const firstBatch = (api.getChannelsNormalizePreviewBatch as ReturnType<typeof vi.fn>)
      .mock.calls[0][0];
    expect(firstBatch).toHaveLength(NORMALIZE_PREVIEW_BATCH_SIZE);

    const secondBatch = (api.getChannelsNormalizePreviewBatch as ReturnType<typeof vi.fn>)
      .mock.calls[1][0];
    expect(secondBatch).toHaveLength(5);
  });

  it('does not re-fetch when only order changes (same id+name set)', async () => {
    (api.getChannelsNormalizePreviewBatch as ReturnType<typeof vi.fn>).mockResolvedValue({
      results: [],
    });

    const { rerender } = renderHook(
      ({ chs }: { chs: Array<{ id: number; name: string }> }) =>
        useNormalizePreview(chs),
      {
        initialProps: {
          chs: [
            { id: 1, name: 'A' },
            { id: 2, name: 'B' },
          ],
        },
      }
    );

    await waitFor(() =>
      expect(api.getChannelsNormalizePreviewBatch).toHaveBeenCalledTimes(1)
    );

    // Reorder only — signature unchanged.
    rerender({
      chs: [
        { id: 2, name: 'B' },
        { id: 1, name: 'A' },
      ],
    });

    // Small delay so any spurious effect would fire.
    await new Promise(r => setTimeout(r, 20));
    expect(api.getChannelsNormalizePreviewBatch).toHaveBeenCalledTimes(1);
  });

  it('re-fetches when a channel name changes', async () => {
    (api.getChannelsNormalizePreviewBatch as ReturnType<typeof vi.fn>).mockResolvedValue({
      results: [],
    });

    const { rerender } = renderHook(
      ({ chs }: { chs: Array<{ id: number; name: string }> }) =>
        useNormalizePreview(chs),
      {
        initialProps: { chs: [{ id: 1, name: 'Old' }] },
      }
    );

    await waitFor(() =>
      expect(api.getChannelsNormalizePreviewBatch).toHaveBeenCalledTimes(1)
    );

    rerender({ chs: [{ id: 1, name: 'New' }] });

    await waitFor(() =>
      expect(api.getChannelsNormalizePreviewBatch).toHaveBeenCalledTimes(2)
    );
  });

  it('swallows fetch errors and keeps previews empty', async () => {
    (api.getChannelsNormalizePreviewBatch as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error('boom')
    );

    const { result } = renderHook(() =>
      useNormalizePreview([{ id: 1, name: 'x' }])
    );

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.previews.size).toBe(0);
  });
});
