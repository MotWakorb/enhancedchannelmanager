/**
 * Display-level grouping of task notification pairs (bd-ib2w3).
 *
 * Each task run produces TWO database notifications:
 *  1. A progress notification created at task start
 *     (source = `task_<task_id>`, source_id = `progress_<epoch>`), updated in
 *     place while the task runs and finalized at completion.
 *  2. A separate result notification created at completion
 *     (source = `task`, source_id = `<task_id>`, title "Task Completed: …" /
 *     "Task Failed: …" / "Task Cancelled: …").
 *
 * Shown side by side these double the visual noise per run. This module
 * collapses each result notification with its matching finalized progress
 * notification into ONE display entry showing the final state, with the run's
 * start time available as detail. Purely display-level — the underlying
 * notifications are untouched; actions on a collapsed entry apply to both.
 *
 * ACTIVE progress notifications (task still running) are never collapsed —
 * they have no result yet and must keep their live progress bar.
 */
import type { Notification } from '../services/api';

/** One rendered row in the notifications panel. */
export interface NotificationDisplayEntry {
  /** Notification rendered as the row (for a pair: the completion/result entry). */
  primary: Notification;
  /** Paired progress/"started" notification collapsed into this row, or null. */
  collapsed: Notification | null;
}

const TASK_RESULT_SOURCE = 'task';
const TASK_PROGRESS_SOURCE_PREFIX = 'task_';

/**
 * Max age gap between a result notification and the progress notification it
 * pairs with. Bounds mispairing when one half of an older pair was deleted;
 * generous enough for any realistic task run duration.
 */
const PAIRING_WINDOW_MS = 24 * 60 * 60 * 1000;

interface ProgressMeta {
  progress?: { status?: string };
}

/** Statuses that mean the progress notification's run has ended. */
const FINAL_PROGRESS_STATUSES = new Set(['completed', 'failed', 'cancelled', 'idle']);

/** True if this notification is a per-run progress entry for the given task. */
function isProgressNotificationForTask(n: Notification, taskId: string): boolean {
  if (n.source === `${TASK_PROGRESS_SOURCE_PREFIX}${taskId}`) return true;
  // Legacy: the stream probe's progress notification is created by
  // stream_prober with source 'stream_probe' rather than the task engine's
  // 'task_stream_probe'. Restricted to that one task, and only when the
  // candidate carries a progress payload (i.e. is a genuine per-run entry).
  if (taskId === 'stream_probe' && n.source === 'stream_probe') {
    return (n.metadata as ProgressMeta | null)?.progress !== undefined;
  }
  return false;
}

/** True if the progress notification's run has ended (safe to collapse). */
export function isFinalizedProgressNotification(n: Notification): boolean {
  const progress = (n.metadata as ProgressMeta | null)?.progress;
  if (!progress) return true; // no live progress payload — nothing to keep visible
  return FINAL_PROGRESS_STATUSES.has(progress.status ?? '');
}

/**
 * Collapse task result + finalized progress notification pairs into single
 * display entries.
 *
 * Input must be in API order (newest first). For each result notification
 * (source === 'task'), the nearest OLDER unclaimed finalized progress
 * notification for the same task within the pairing window is collapsed into
 * it. Everything else passes through unchanged.
 */
export function collapseTaskNotificationPairs(
  notifications: Notification[],
): NotificationDisplayEntry[] {
  const claimed = new Set<number>();
  const entries: NotificationDisplayEntry[] = [];

  for (let i = 0; i < notifications.length; i++) {
    const n = notifications[i];
    if (claimed.has(n.id)) continue;

    if (n.source === TASK_RESULT_SOURCE && n.source_id) {
      const resultTime = new Date(n.created_at).getTime();
      // Find the nearest older unclaimed finalized progress entry for this task
      let match: Notification | null = null;
      for (let j = i + 1; j < notifications.length; j++) {
        const candidate = notifications[j];
        if (claimed.has(candidate.id)) continue;
        if (!isProgressNotificationForTask(candidate, n.source_id)) continue;
        if (!isFinalizedProgressNotification(candidate)) continue;
        const candidateTime = new Date(candidate.created_at).getTime();
        if (resultTime - candidateTime > PAIRING_WINDOW_MS) break; // older ones only get further away
        match = candidate;
        break;
      }
      if (match) claimed.add(match.id);
      entries.push({ primary: n, collapsed: match });
      continue;
    }

    entries.push({ primary: n, collapsed: null });
  }

  return entries;
}

/** Effective read state of a display entry: read only when BOTH halves are read. */
export function isEntryRead(entry: NotificationDisplayEntry): boolean {
  return entry.primary.read && (entry.collapsed?.read ?? true);
}

/**
 * How much to subtract from the server's unread count so a collapsed pair
 * counts once: one per pair where BOTH halves are unread. (Approximation —
 * only pairs within the fetched page can be assessed.)
 */
export function collapsedUnreadAdjustment(entries: NotificationDisplayEntry[]): number {
  return entries.filter(
    (e) => e.collapsed !== null && !e.primary.read && !e.collapsed.read,
  ).length;
}
