/**
 * Pending-merges toast decoration (BD-J / bd-gfxrz, ADR-008 §D1).
 *
 * The bulk-M3U dedup hook (BD-F) emits a post-refresh notification via the
 * existing in-app notification subsystem. NotificationCenter already
 * auto-toasts notifications from the `auto_creation` source; this module
 * decorates that toast for the dedup-specific case so the operator can
 * jump directly to the Pending Merges page via the toast action.
 *
 * Separation of concerns:
 *   - The pure decoration logic (does this notification reference pending
 *     merges? what action label? respect the suppress setting?) lives here,
 *     fully testable in isolation.
 *   - The wiring (read settings, dispatch DOM event, call notify with the
 *     decorated options) lives in NotificationCenter.
 *
 * Settings contract: respects `dedup_m3u_toast_suppressed` from BD-B / BD-K.
 * When true, the function returns `null` to indicate "suppress this toast".
 *
 * One toast per refresh: matching is keyed on the notification id at the
 * call site (NotificationCenter already de-dupes by id), so even though
 * the bulk-M3U pipeline may emit multiple `auto_creation` notifications
 * over its life cycle, only the one carrying the pending_merges_added
 * count gets the decorated action.
 */
import type { Notification } from '../services/api';

/**
 * Matches the "N pending merge(s) queued" suffix that
 * `run_auto_creation_after_refresh` appends to the post-refresh notification
 * title (`backend/tasks/auto_creation.py`).
 *
 * Capture group 1: the integer count. Used to compose the friendly toast
 * message ("3 streams queued for dedup review"). The match is case-
 * insensitive and tolerant of single vs plural ("merge" / "merges") because
 * the backend uses both depending on the count.
 */
const PENDING_MERGES_TITLE_PATTERN = /(\d+)\s+pending\s+merges?\s+queued/i;

export interface PendingMergesToastInputs {
  notification: Notification;
  dedupM3uToastSuppressed: boolean;
}

export interface PendingMergesToastOptions {
  /** Count of newly-queued pending merges parsed from the notification title. */
  count: number;
  /** Toast title — the page name the operator is being routed to. */
  title: string;
  /** Toast body — operator-facing summary copy. */
  message: string;
  /** Action label rendered on the toast button. */
  actionLabel: string;
}

/**
 * Inspect a notification and decide whether to decorate it as a pending-
 * merges toast. Returns `null` when the notification is not relevant or
 * when the operator has suppressed this toast in Settings (BD-K).
 *
 * Caller is responsible for:
 *   - Calling this only on notifications from `source === 'auto_creation'`.
 *   - Wiring the action button to dispatch the `ecm:open-pending-merges`
 *     event (or equivalent navigation).
 *
 * The function does NOT mutate the input notification.
 */
export function decoratePendingMergesToast({
  notification,
  dedupM3uToastSuppressed,
}: PendingMergesToastInputs): PendingMergesToastOptions | null {
  if (dedupM3uToastSuppressed) {
    return null;
  }

  // The dedup-specific marker is the pending-merges count in the title.
  // Other auto_creation notifications (errors, "no changes", pure
  // create/update counts) are left to NotificationCenter's existing
  // un-decorated toast path.
  const title = notification.title ?? '';
  const match = PENDING_MERGES_TITLE_PATTERN.exec(title);
  if (!match) {
    return null;
  }

  const count = Number(match[1]);
  if (!Number.isFinite(count) || count <= 0) {
    return null;
  }

  return {
    count,
    title: 'Pending Merges',
    message: `${count} stream${count === 1 ? '' : 's'} queued for dedup review`,
    actionLabel: 'View',
  };
}
