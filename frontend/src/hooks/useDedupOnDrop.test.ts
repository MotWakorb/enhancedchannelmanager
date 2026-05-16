/**
 * Unit tests for useDedupOnDrop (bd-u6ftw / BD-H).
 *
 * Covers the two branches that gate the dedup modal vs the existing
 * drag-drop creation path:
 *   1. Backend returns no candidate → fall-through path runs (proceed),
 *      modal stays closed, no returning-stream-id is registered.
 *   2. Backend returns a candidate → modal opens with that candidate;
 *      fall-through path does NOT run; onMerge invokes addStreamToChannel
 *      and reloads channels; onCancel registers the streamId in
 *      returningStreamIds for the cancel-pulse animation, then clears it
 *      after the highlight window.
 *
 * Also covers the prefers-reduced-motion short-circuit: when reduced motion
 * is enabled, onCancel must NOT register the streamId in returningStreamIds
 * (the animation is skipped per ADR-008 §D5 accessibility guidance).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useDedupOnDrop, DEDUP_RETURNING_HIGHLIGHT_MS } from './useDedupOnDrop';

// Mock the API surface used by the hook. We don't care about the rest of
// services/api for this hook — only the two endpoints it calls.
vi.mock('../services/api', () => ({
  getDedupCandidates: vi.fn(),
  addStreamToChannel: vi.fn(),
}));

import * as api from '../services/api';

// Mock prefers-reduced-motion. The hook reads it lazily via
// window.matchMedia; we override matchMedia per test below.
function mockMatchMedia(prefersReduced: boolean) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: prefersReduced && query.includes('prefers-reduced-motion'),
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}

describe('useDedupOnDrop', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockMatchMedia(false);
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  describe('no-candidate branch', () => {
    it('runs the fallback create path and does not open the modal', async () => {
      vi.mocked(api.getDedupCandidates).mockResolvedValue({
        stream_name: 'CNN HD',
        candidates: [],
        total: 0,
        page: 1,
        page_size: 50,
        total_pages: 0,
      });

      const fallback = vi.fn();
      const reload = vi.fn();
      const { result } = renderHook(() =>
        useDedupOnDrop({ reloadChannels: reload }),
      );

      await act(async () => {
        await result.current.handleSingleStreamDrop(
          { streamId: 42, streamName: 'CNN HD', targetGroupId: 7 },
          fallback,
        );
      });

      expect(api.getDedupCandidates).toHaveBeenCalledWith('CNN HD', 7);
      expect(fallback).toHaveBeenCalledTimes(1);
      expect(result.current.modalState).toBeNull();
      expect(result.current.returningStreamIds.size).toBe(0);
    });

    it('falls through when the candidates lookup itself errors', async () => {
      vi.mocked(api.getDedupCandidates).mockRejectedValue(new Error('network'));

      const fallback = vi.fn();
      const { result } = renderHook(() =>
        useDedupOnDrop({ reloadChannels: vi.fn() }),
      );

      await act(async () => {
        await result.current.handleSingleStreamDrop(
          { streamId: 1, streamName: 'X', targetGroupId: null },
          fallback,
        );
      });

      // A backend error must not block the existing drag-drop creation
      // path — that's the "unchanged behavior" contract from the bead.
      expect(fallback).toHaveBeenCalledTimes(1);
      expect(result.current.modalState).toBeNull();
    });
  });

  describe('candidate-found branch', () => {
    const candidate = {
      channel_id: '101',
      channel_name: 'CNN',
      confidence: 0.92,
    };

    beforeEach(() => {
      vi.mocked(api.getDedupCandidates).mockResolvedValue({
        stream_name: 'CNN HD',
        candidates: [candidate],
        total: 1,
        page: 1,
        page_size: 50,
        total_pages: 1,
      });
    });

    it('opens the modal with the candidate and skips the fallback path', async () => {
      const fallback = vi.fn();
      const { result } = renderHook(() =>
        useDedupOnDrop({ reloadChannels: vi.fn() }),
      );

      await act(async () => {
        await result.current.handleSingleStreamDrop(
          { streamId: 42, streamName: 'CNN HD', targetGroupId: 7 },
          fallback,
        );
      });

      expect(fallback).not.toHaveBeenCalled();
      expect(result.current.modalState).toEqual({
        streamId: 42,
        streamName: 'CNN HD',
        targetGroupId: 7,
        candidate,
        fallback,
      });
    });

    it('onMerge calls addStreamToChannel, reloads, and clears the modal', async () => {
      vi.mocked(api.addStreamToChannel).mockResolvedValue({} as never);

      const reload = vi.fn().mockResolvedValue(undefined);
      const { result } = renderHook(() => useDedupOnDrop({ reloadChannels: reload }));

      await act(async () => {
        await result.current.handleSingleStreamDrop(
          { streamId: 42, streamName: 'CNN HD', targetGroupId: 7 },
          vi.fn(),
        );
      });

      await act(async () => {
        await result.current.handleMerge('101');
      });

      expect(api.addStreamToChannel).toHaveBeenCalledWith(101, 42);
      expect(reload).toHaveBeenCalledTimes(1);
      expect(result.current.modalState).toBeNull();
    });

    it('onMerge rethrows on API failure so the modal can surface the error', async () => {
      vi.mocked(api.addStreamToChannel).mockRejectedValue(new Error('boom'));

      const { result } = renderHook(() =>
        useDedupOnDrop({ reloadChannels: vi.fn() }),
      );

      await act(async () => {
        await result.current.handleSingleStreamDrop(
          { streamId: 42, streamName: 'CNN HD', targetGroupId: 7 },
          vi.fn(),
        );
      });

      await expect(
        act(async () => {
          await result.current.handleMerge('101');
        }),
      ).rejects.toThrow('boom');

      // Modal stays open on failure — the operator needs to see the error
      // banner and retry or cancel.
      expect(result.current.modalState).not.toBeNull();
    });

    it('onCreateNew closes the modal and runs the fallback create path', async () => {
      const fallback = vi.fn();
      const { result } = renderHook(() =>
        useDedupOnDrop({ reloadChannels: vi.fn() }),
      );

      await act(async () => {
        await result.current.handleSingleStreamDrop(
          { streamId: 42, streamName: 'CNN HD', targetGroupId: 7 },
          fallback,
        );
      });

      await act(async () => {
        await result.current.handleCreateNew();
      });

      expect(fallback).toHaveBeenCalledTimes(1);
      expect(result.current.modalState).toBeNull();
    });

    it('onCancel adds streamId to returningStreamIds for the pulse window, then clears it', async () => {
      const { result } = renderHook(() =>
        useDedupOnDrop({ reloadChannels: vi.fn() }),
      );

      await act(async () => {
        await result.current.handleSingleStreamDrop(
          { streamId: 42, streamName: 'CNN HD', targetGroupId: 7 },
          vi.fn(),
        );
      });

      act(() => {
        result.current.handleCancel();
      });

      // Modal closes immediately; the streamId is registered for the
      // cancel-pulse highlight class.
      expect(result.current.modalState).toBeNull();
      expect(result.current.returningStreamIds.has(42)).toBe(true);

      // After the highlight window, the streamId is cleared so the class
      // is removed and a future drop can re-trigger the animation.
      act(() => {
        vi.advanceTimersByTime(DEDUP_RETURNING_HIGHLIGHT_MS + 50);
      });
      expect(result.current.returningStreamIds.has(42)).toBe(false);
    });

    it('onCancel does NOT register streamId when prefers-reduced-motion is set', async () => {
      mockMatchMedia(true);
      const { result } = renderHook(() =>
        useDedupOnDrop({ reloadChannels: vi.fn() }),
      );

      await act(async () => {
        await result.current.handleSingleStreamDrop(
          { streamId: 42, streamName: 'CNN HD', targetGroupId: 7 },
          vi.fn(),
        );
      });

      act(() => {
        result.current.handleCancel();
      });

      expect(result.current.modalState).toBeNull();
      // Reduced motion → snap back, no pulse class applied.
      expect(result.current.returningStreamIds.has(42)).toBe(false);
    });
  });
});
