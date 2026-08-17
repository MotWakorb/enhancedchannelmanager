/**
 * Storage and lifecycle rules for the "Backups are not scheduled yet" snooze
 * (beads enhancedchannelmanager-iij6s, enhancedchannelmanager-pui76).
 *
 * The banner in `components/settings/BackupScheduleBanner.tsx` is the only
 * enforcement mechanism for the standing decision that scheduled DBAS backup
 * ships OFF and stays off. Everything that decides whether that signal is
 * silent lives here rather than in the component, for one reason: two of the
 * three rules below have to hold when the banner is NOT mounted, and a rule
 * that only runs while a component happens to be on screen is not a rule.
 *
 * Three invariants, each enforced by a function in this file:
 *
 *   1. A stored snooze can never place the next warning further out than
 *      SNOOZE_MS from now (`isBackupBannerSnoozed`). A value beyond that
 *      ceiling is not one `startBackupBannerSnooze` could have written, so it
 *      is not a snooze — it is treated as expired. This is stated as a
 *      property rather than a list of bad inputs on purpose: it disposes of
 *      every huge finite timestamp (1e308 and friends), a clock moved
 *      backwards, and a shrunk SNOOZE_DAYS, without enumerating any of them.
 *      All three failures resolve toward SHOWING the warning, which is the
 *      only safe direction for the thing this banner enforces.
 *
 *   2. Whenever a `dbas_backup` schedule is observed enabled, any stored
 *      snooze is void — regardless of what is mounted
 *      (`voidBackupBannerSnoozeIfScheduled`, called from the API layer where
 *      that fact enters the frontend, `services/api.ts`). The snooze used to
 *      be cleared by the banner's own effect, so the sequence "snooze the
 *      banner, leave the page, enable a schedule from Scheduled Tasks, disable
 *      it again, come back" left a stale snooze honoured against a condition
 *      that had genuinely returned.
 *
 *   3. Web Storage failure never hides the warning and never eats a click.
 *      Every access here is guarded, and the two directions fail opposite ways
 *      because the safe answer differs: a failed READ means "not snoozed" (show
 *      it — never hide on ignorance), while a failed WRITE still has to let the
 *      caller hide the banner for this session, because a control that visibly
 *      does nothing is worse than one whose effect is shorter than advertised.
 *      Storage throws in more places than it looks: private browsing modes,
 *      quota exhaustion, and site-data policies all raise from plain
 *      `getItem`/`setItem`/`removeItem`.
 *
 * Enforcement: `backupBannerSnooze.test.ts` (all three, unit),
 * `services/api.backupBannerSnooze.test.ts` (invariant 2 across the real API
 * seam), and `components/settings/BackupScheduleBanner.test.tsx` (all three as
 * rendered behaviour, including the off-page enable-then-disable lifecycle).
 */

/**
 * localStorage key holding the epoch-milliseconds instant the snooze runs out.
 * Named for what it stores: an expiry, not a boolean. Deliberately a NEW key —
 * the legacy one below held a permanent `'1'`, and reusing it would have to
 * decide what `'1'` means as a timestamp on every install already carrying one.
 */
export const SNOOZE_UNTIL_KEY = 'ecm:dbas-backup-banner-snoozed-until';

/**
 * The pre-iij6s permanent-dismissal flag. Not honoured — installs silenced
 * under the old behaviour are precisely the ones that need telling again — and
 * removed on sight so it does not sit in storage meaning nothing.
 */
export const LEGACY_DISMISS_KEY = 'ecm:dbas-backup-banner-dismissed';

/** How long one dismissal buys. Quoted verbatim in the button's label. */
export const SNOOZE_DAYS = 30;
export const SNOOZE_MS = SNOOZE_DAYS * 24 * 60 * 60 * 1000;

/** The scheduled-backup task this banner is about. */
export const BACKUP_TASK_ID = 'dbas_backup';

/** Read a key, or `null` when storage is unreadable for any reason. */
function readItem(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    // Unreadable storage is indistinguishable from "nothing stored", and both
    // must resolve toward showing the warning (invariant 3).
    return null;
  }
}

/** Write a key. Returns whether it actually persisted. */
function writeItem(key: string, value: string): boolean {
  try {
    localStorage.setItem(key, value);
    return true;
  } catch {
    return false;
  }
}

/** Remove a key, tolerating storage that refuses to cooperate. */
function dropItem(key: string): void {
  try {
    localStorage.removeItem(key);
  } catch {
    // Nothing to do and nothing to fail: the caller's next read of this key
    // goes through `readItem`, which fails toward showing the warning anyway.
  }
}

/**
 * True only while a stored snooze is both still in the future AND within one
 * snooze window of now — invariant 1. Anything else (missing, unparseable,
 * negative, past, or beyond the ceiling) is "not snoozed".
 */
export function isBackupBannerSnoozed(now: number): boolean {
  const raw = readItem(SNOOZE_UNTIL_KEY);
  if (raw === null) return false;
  const until = Number(raw);
  if (!Number.isFinite(until)) return false;
  return until > now && until <= now + SNOOZE_MS;
}

/**
 * Store a snooze expiring one window from `now`.
 *
 * Returns whether it persisted. `false` is not a failure the caller should
 * surface as an error — the caller still hides the banner for this session
 * (invariant 3) — but it does mean the banner returns on the next load rather
 * than in 30 days, which is worth a log line when a support case asks why.
 */
export function startBackupBannerSnooze(now: number): boolean {
  return writeItem(SNOOZE_UNTIL_KEY, String(now + SNOOZE_MS));
}

/** Drop any stored snooze. */
export function clearBackupBannerSnooze(): void {
  dropItem(SNOOZE_UNTIL_KEY);
}

/** Drop the legacy permanent-dismissal flag, which is never honoured. */
export function discardLegacyBackupBannerDismissal(): void {
  dropItem(LEGACY_DISMISS_KEY);
}

/** The only field of a schedule this module cares about. */
interface ObservedSchedule {
  enabled?: boolean;
}

/**
 * Invariant 2. Called wherever schedules for a task enter the frontend, with
 * whatever the backend just said about them; voids any stored snooze when the
 * backup task is observed to have at least one enabled schedule.
 *
 * Deliberately takes the task id rather than being called only on the backup
 * task's own path: the callers are generic API functions that do not know
 * which task they were asked about, and pushing the filter in here is what
 * keeps every one of them a single unconditional line that cannot be forgotten
 * to be guarded.
 *
 * The enabled test matches the banner's own condition exactly — at least one
 * enabled child schedule — so the thing that clears the snooze and the thing
 * that hides the banner can never disagree.
 */
export function voidBackupBannerSnoozeIfScheduled(
  taskId: string,
  schedules: readonly ObservedSchedule[] | null | undefined,
): void {
  if (taskId !== BACKUP_TASK_ID) return;
  if (!Array.isArray(schedules)) return;
  if (!schedules.some((s) => s?.enabled === true)) return;
  clearBackupBannerSnooze();
}
