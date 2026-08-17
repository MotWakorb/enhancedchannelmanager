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
 * The rules that make those properties hold — the ceiling on a stored snooze,
 * voiding one whenever an enabled schedule is observed anywhere in the app,
 * and surviving Web Storage that throws — live in
 * `utils/backupBannerSnooze.ts` and are documented there. They are not in this
 * file because two of the three have to hold while this component is NOT
 * mounted, and a rule that only runs while a component is on screen is not a
 * rule (bead enhancedchannelmanager-pui76, review round 2).
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
  BACKUP_TASK_ID,
  SNOOZE_DAYS,
  discardLegacyBackupBannerDismissal,
  isBackupBannerSnoozed,
  startBackupBannerSnooze,
} from '../../utils/backupBannerSnooze';
import {
  OPEN_TASK_EDITOR_EVENT,
  OPEN_TASK_EDITOR_STORAGE_KEY,
  type OpenTaskEditorIntent,
} from '../../utils/openTaskEditor';
import './BackupScheduleBanner.css';

export function BackupScheduleBanner() {
  // `null` = still checking; `true`/`false` = whether to render.
  const [show, setShow] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;

    discardLegacyBackupBannerDismissal();
    const snoozed = isBackupBannerSnoozed(Date.now());

    // The schedule check runs even while snoozed, because the snooze only ever
    // buys quiet against an unscheduled install and this is what confirms the
    // install is still unscheduled. Voiding a stale snooze is NOT this call's
    // job any more (bead pui76 round 2): `api.getTaskSchedules` does it, along
    // with every other path an enabled schedule enters the frontend through,
    // so the sequence "snooze here, enable and disable from Scheduled Tasks,
    // come back" no longer depends on this component having been mounted at
    // the right moment.
    api
      .getTaskSchedules(BACKUP_TASK_ID)
      .then(({ schedules }) => {
        if (cancelled) return;
        setShow(!schedules.some((s) => s.enabled) && !snoozed);
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
    // `setShow(false)` is unconditional and comes second on purpose: the
    // operator clicked, so the banner goes away for this session even where
    // Web Storage refuses the write and the quiet cannot outlive the tab. A
    // control that visibly does nothing is worse than one whose effect is
    // shorter than advertised.
    if (!startBackupBannerSnooze(Date.now())) {
      logger.warn(
        'Could not persist the backup-banner snooze; it will last this session only',
      );
    }
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
