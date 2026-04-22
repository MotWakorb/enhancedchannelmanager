/**
 * useNormalizePreview — bd-eio04.13
 *
 * Computes the would-normalize state for a list of channels by batching
 * calls to POST /api/channels/normalize-preview-batch. The hook:
 *   - issues one request per batch of NORMALIZE_PREVIEW_BATCH_SIZE channels
 *   - re-fetches when the (id, name) pairs change (so an inline rename
 *     triggers a re-preview without forcing the whole list to refresh)
 *   - exposes a Map<channelId, NormalizePreviewResult> for O(1) lookup
 *     from the row renderer
 *   - aborts in-flight requests on unmount and on input change
 *
 * Errors are swallowed: the indicator is a hint, not a correctness gate.
 * If the batch endpoint fails the UI simply shows no indicators.
 */
import { useEffect, useRef, useState } from 'react';
import { getChannelsNormalizePreviewBatch, type NormalizePreviewResult } from '../services/api';
import { logger } from '../utils/logger';

// Matches the backend cap (NORMALIZE_PREVIEW_BATCH_MAX in channels.py).
// Keep in sync — the server rejects batches larger than this.
export const NORMALIZE_PREVIEW_BATCH_SIZE = 100;

export interface UseNormalizePreviewOptions {
  /**
   * When false, the hook is a no-op (no network calls, empty map).
   * Lets callers disable the indicator globally (feature flag / setting).
   */
  enabled?: boolean;
}

export interface UseNormalizePreviewResult {
  /** channelId -> preview row. Missing entries = no preview yet or skipped. */
  previews: Map<number, NormalizePreviewResult>;
  /** True while a batch request is in flight. */
  loading: boolean;
}

/**
 * Compute normalize-preview for a stable list of channels.
 *
 * The input is keyed by (id, name) so that:
 *   - adding/removing channels refreshes the affected entries
 *   - renaming a channel inline refreshes that channel's preview
 *   - reordering alone (no name change) does NOT refetch
 */
export function useNormalizePreview(
  channels: ReadonlyArray<{ id: number; name: string }>,
  options: UseNormalizePreviewOptions = {},
): UseNormalizePreviewResult {
  const { enabled = true } = options;
  const [previews, setPreviews] = useState<Map<number, NormalizePreviewResult>>(new Map());
  const [loading, setLoading] = useState(false);

  // Stable signature of (id, name) pairs — comparing this rather than the
  // array reference avoids re-fetching when unrelated channel metadata
  // changes (logo, channel_number, etc). Sorted by id so that a pure
  // reorder of the visible list does NOT trigger a refetch.
  const signature = [...channels]
    .sort((a, b) => a.id - b.id)
    .map(c => `${c.id}:${c.name}`)
    .join('|');
  const lastSignatureRef = useRef<string>('');

  useEffect(() => {
    if (!enabled) {
      if (previews.size > 0) setPreviews(new Map());
      return;
    }
    if (channels.length === 0) {
      if (previews.size > 0) setPreviews(new Map());
      return;
    }
    if (signature === lastSignatureRef.current) return;
    lastSignatureRef.current = signature;

    const controller = new AbortController();
    let cancelled = false;

    setLoading(true);
    (async () => {
      try {
        const nextMap = new Map<number, NormalizePreviewResult>();
        for (let i = 0; i < channels.length; i += NORMALIZE_PREVIEW_BATCH_SIZE) {
          const slice = channels.slice(i, i + NORMALIZE_PREVIEW_BATCH_SIZE);
          const response = await getChannelsNormalizePreviewBatch(
            slice.map(c => ({ id: c.id, name: c.name })),
            { signal: controller.signal },
          );
          if (cancelled) return;
          for (const row of response.results) {
            nextMap.set(row.channel_id, row);
          }
        }
        if (!cancelled) setPreviews(nextMap);
      } catch (err) {
        // AbortError on unmount / signature change is expected; everything
        // else is a soft failure — the indicator is an advisory, not a gate.
        if ((err as { name?: string } | null)?.name !== 'AbortError') {
          logger.warn('[normalize-preview] batch failed', err);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
      controller.abort();
    };
    // `previews.size` is checked to decide whether to clear; re-running
    // on previews changes would loop. eslint's exhaustive-deps rule is
    // satisfied by signature + enabled alone.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature, enabled]);

  return { previews, loading };
}
