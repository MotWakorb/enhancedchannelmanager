/**
 * Tests for pendingMergesToast — the pure decoration logic that decides
 * whether an auto_creation notification should fire the BD-J Pending Merges
 * toast and, if so, what action label / message to render.
 *
 * Locks the BD-J contract from bd-gfxrz and the parent epic bd-1v4ht:
 *
 *   - When the operator has set dedup_m3u_toast_suppressed=true (BD-K
 *     Settings toggle), the decorator MUST return null — the toast does
 *     not fire even though the auto_creation notification still appears
 *     in NotificationCenter.
 *   - The decorator matches the title pattern emitted by the backend's
 *     run_auto_creation_after_refresh helper ("N pending merge[s] queued").
 *   - Unrelated auto_creation notifications (no pending-merges suffix) are
 *     ignored so they fall back to the existing un-decorated toast path.
 *   - Plural / singular grammar follows the count.
 */
import { describe, it, expect } from 'vitest';
import { decoratePendingMergesToast } from './pendingMergesToast';
import type { Notification } from '../services/api';

function makeNotification(overrides: Partial<Notification> = {}): Notification {
  return {
    id: 1,
    type: 'success',
    title: 'Auto-Creation: 0 created, 3 pending merges queued',
    message: 'Ran 5 rules after M3U refresh. 8/10 streams matched.',
    read: false,
    source: 'auto_creation',
    source_id: 'm3u_refresh',
    action_label: null,
    action_url: null,
    metadata: null,
    created_at: '2026-05-16T18:00:00Z',
    read_at: null,
    expires_at: null,
    ...overrides,
  };
}

describe('decoratePendingMergesToast — suppression (BD-K setting)', () => {
  it('returns null when dedup_m3u_toast_suppressed is true (no toast)', () => {
    const result = decoratePendingMergesToast({
      notification: makeNotification(),
      dedupM3uToastSuppressed: true,
    });
    expect(result).toBeNull();
  });

  it('returns the toast options when dedup_m3u_toast_suppressed is false', () => {
    const result = decoratePendingMergesToast({
      notification: makeNotification(),
      dedupM3uToastSuppressed: false,
    });
    expect(result).not.toBeNull();
    expect(result?.count).toBe(3);
  });
});

describe('decoratePendingMergesToast — title pattern matching', () => {
  it('matches the canonical "N pending merges queued" plural suffix', () => {
    const result = decoratePendingMergesToast({
      notification: makeNotification({
        title: 'Auto-Creation: 0 created, 5 pending merges queued',
      }),
      dedupM3uToastSuppressed: false,
    });
    expect(result?.count).toBe(5);
    expect(result?.message).toBe('5 streams queued for dedup review');
  });

  it('matches the singular "1 pending merge queued" form', () => {
    const result = decoratePendingMergesToast({
      notification: makeNotification({
        title: 'Auto-Creation: 1 pending merge queued',
      }),
      dedupM3uToastSuppressed: false,
    });
    expect(result?.count).toBe(1);
    // Singular grammar in the operator-facing toast body.
    expect(result?.message).toBe('1 stream queued for dedup review');
  });

  it('returns null for unrelated auto_creation notifications (no pending-merges marker)', () => {
    const result = decoratePendingMergesToast({
      notification: makeNotification({
        title: 'Auto-Creation: 3 created, 2 updated',
      }),
      dedupM3uToastSuppressed: false,
    });
    expect(result).toBeNull();
  });

  it('returns null when the title is missing', () => {
    const result = decoratePendingMergesToast({
      notification: makeNotification({ title: null }),
      dedupM3uToastSuppressed: false,
    });
    expect(result).toBeNull();
  });

  it('returns null when the count is zero (defensive — backend never emits this, but stay safe)', () => {
    const result = decoratePendingMergesToast({
      notification: makeNotification({
        title: 'Auto-Creation: 0 pending merges queued',
      }),
      dedupM3uToastSuppressed: false,
    });
    expect(result).toBeNull();
  });
});

describe('decoratePendingMergesToast — toast options shape', () => {
  it('returns the BD-J ratified action label "View"', () => {
    const result = decoratePendingMergesToast({
      notification: makeNotification(),
      dedupM3uToastSuppressed: false,
    });
    expect(result?.actionLabel).toBe('View');
  });

  it('returns "Pending Merges" as the toast title (page name)', () => {
    const result = decoratePendingMergesToast({
      notification: makeNotification(),
      dedupM3uToastSuppressed: false,
    });
    expect(result?.title).toBe('Pending Merges');
  });
});
