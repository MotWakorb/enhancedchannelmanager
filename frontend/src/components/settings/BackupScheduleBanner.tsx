/**
 * BackupScheduleBanner — the "Backups are not scheduled yet" setup nudge
 * (beads ikv8z, enhancedchannelmanager-iij6s).
 *
 * Context: scheduled DBAS backup ships OFF by default (default_enabled=False)
 * and STAYS off by default — ECM does not write to an operator's disk on a
 * schedule nobody chose. That standing decision has exactly one enforcement
 * mechanism, and it is this banner. A less-engaged operator can otherwise
 * silently end up with ZERO backups, the SRE's #1 operational risk.
 *
 * WHY THE DISMISSAL IS A SNOOZE (bead iij6s). The banner used to be one-time:
 * dismissal wrote a permanent flag to localStorage, so one click by one
 * operator in one browser silenced the only signal that an install had no
 * backups, forever, on an install that had never taken one. The enforcement
 * mechanism for a product decision was one click deep. It is now a 30-day
 * snooze, and the control says "Remind me in 30 days" rather than being a bare
 * ✕ that reads as "gone".
 *
 * Two properties, and both are load-bearing:
 *   - The signal cannot be permanently silenced while the condition it reports
 *     is still true. The snooze expires; the banner returns.
 *   - An operator who has deliberately chosen no schedule still has recourse.
 *     One click buys a month of quiet, the button says so before it is clicked,
 *     and enabling a schedule ends the reminders outright.
 *
 * The window is client-side, so it is per browser profile. That is deliberate
 * rather than a shortcut: the only alternative is a server-side, admin-writable
 * suppression flag whose entire function is to hide a safety warning across
 * every browser at once, and a per-browser window errs toward showing the
 * warning — the safe direction for the thing this banner is enforcing.
 *
 * The condition is deliberately "no ENABLED schedule", not "no backups on
 * disk". An operator who takes one backup by hand and stops is exactly the
 * state this banner exists to catch, so an artifact in /config/backups/ must
 * not silence it.
 *
 * The "Set one up" CTA reuses the existing task-editor navigation contract
 * (sessionStorage `ecm:open-task-editor` + the matching window event that
 * App.tsx listens for) — the same path NotificationCenter uses to open a task.
 */
import { useEffect, useState } from 'react';
import * as api from '../../services/api';
import { logger } from '../../utils/logger';
import {
  OPEN_TASK_EDITOR_EVENT,
  OPEN_TASK_EDITOR_STORAGE_KEY,
  type OpenTaskEditorIntent,
} from '../../utils/openTaskEditor';
import './BackupScheduleBanner.css';

/**
 * localStorage key holding the epoch-milliseconds instant the snooze runs out.
 * Named for what it stores: an expiry, not a boolean. Deliberately a NEW key —
 * the legacy one below held a permanent `'1'`, and reusing it would have to
 * decide what `'1'` means as a timestamp on every install already carrying one.
 */
const SNOOZE_UNTIL_KEY = 'ecm:dbas-backup-banner-snoozed-until';
/**
 * The pre-iij6s permanent-dismissal flag. Not honoured — installs silenced
 * under the old behaviour are precisely the ones that need telling again — and
 * removed on sight so it does not sit in storage meaning nothing.
 */
const LEGACY_DISMISS_KEY = 'ecm:dbas-backup-banner-dismissed';
/** How long one dismissal buys. Quoted verbatim in the button's label. */
const SNOOZE_DAYS = 30;
const SNOOZE_MS = SNOOZE_DAYS * 24 * 60 * 60 * 1000;
/** The scheduled-backup task this banner is about. */
const BACKUP_TASK_ID = 'dbas_backup';

/**
 * True only while a readable snooze is still in the future. An unreadable or
 * missing value means "not snoozed" — a value nobody can interpret must fail
 * toward showing the warning, never toward hiding it forever.
 */
function isSnoozed(now: number): boolean {
  const raw = localStorage.getItem(SNOOZE_UNTIL_KEY);
  if (raw === null) return false;
  const until = Number(raw);
  return Number.isFinite(until) && until > now;
}

export function BackupScheduleBanner() {
  // `null` = still checking; `true`/`false` = whether to render.
  const [show, setShow] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;

    localStorage.removeItem(LEGACY_DISMISS_KEY);
    const snoozed = isSnoozed(Date.now());

    // The schedule check runs even while snoozed. It is one cheap GET, and it
    // is what lets an enabled schedule clear a stale snooze: without it, a
    // snooze taken today outlives the schedule that replaced it, and disabling
    // that schedule tomorrow leaves the install unscheduled AND un-warned.
    api
      .getTaskSchedules(BACKUP_TASK_ID)
      .then(({ schedules }) => {
        const hasEnabled = schedules.some((s) => s.enabled);
        if (hasEnabled) localStorage.removeItem(SNOOZE_UNTIL_KEY);
        if (cancelled) return;
        setShow(!hasEnabled && !snoozed);
      })
      .catch((err) => {
        // Fail quiet: a transient schedule-load error must not nag the operator
        // with a misleading "not scheduled" banner.
        logger.warn('Failed to check backup schedules for setup banner', err);
        if (!cancelled) setShow(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  if (!show) return null;

  const handleSnooze = () => {
    localStorage.setItem(SNOOZE_UNTIL_KEY, String(Date.now() + SNOOZE_MS));
    setShow(false);
  };

  const handleSetUp = () => {
    // Same contract NotificationCenter uses: stash intent + fire the event
    // App.tsx listens for to switch to Settings > Scheduled Tasks and open the
    // editor for this task.
    const intent: OpenTaskEditorIntent = { taskId: BACKUP_TASK_ID };
    sessionStorage.setItem(OPEN_TASK_EDITOR_STORAGE_KEY, JSON.stringify(intent));
    window.dispatchEvent(new CustomEvent(OPEN_TASK_EDITOR_EVENT, { detail: intent }));
  };

  return (
    <div className="backup-schedule-banner" data-testid="backup-schedule-banner" role="alert">
      <span className="material-icons backup-schedule-banner-icon" aria-hidden="true">
        event_busy
      </span>
      <div className="backup-schedule-banner-body">
        <span className="backup-schedule-banner-title">Backups are not scheduled yet</span>
        <span className="backup-schedule-banner-detail">
          ECM does not run automatic backups until you schedule them. A backup you took by hand
          only reflects the day you took it — a schedule is what keeps a current one on disk.
        </span>
        <div className="backup-schedule-banner-actions">
          <button
            type="button"
            className="backup-schedule-banner-cta"
            data-testid="backup-schedule-banner-cta"
            onClick={handleSetUp}
          >
            Set one up
            <span className="material-icons backup-schedule-banner-cta-icon" aria-hidden="true">
              arrow_forward
            </span>
          </button>
          <button
            type="button"
            className="backup-schedule-banner-dismiss"
            data-testid="backup-schedule-banner-dismiss"
            onClick={handleSnooze}
          >
            Remind me in {SNOOZE_DAYS} days
          </button>
        </div>
      </div>
    </div>
  );
}
