/**
 * BackupScheduleBanner — one-time "Backups are not scheduled yet" setup nudge
 * (bead ikv8z).
 *
 * Context: scheduled DBAS backup ships OFF by default (default_enabled=False,
 * PO decision on bead 0i2vt.6). A less-engaged operator can therefore silently
 * end up with ZERO backups — the SRE's #1 operational risk. This banner makes
 * the unscheduled state visible at the top of the Backup section and links the
 * operator straight to the schedule editor.
 *
 * Behaviour (per the bead):
 *   - Shows ONLY when there is no ENABLED `dbas_backup` schedule.
 *   - One-time: dismiss persists in localStorage and the banner stays gone.
 *   - Re-appears only if still unscheduled AND not previously dismissed — once
 *     a schedule is enabled it never shows again regardless of dismissal state.
 *
 * The "Set one up" CTA reuses the existing task-editor navigation contract
 * (sessionStorage `ecm:open-task-editor` + the matching window event that
 * App.tsx listens for) — the same path NotificationCenter uses to open a task.
 */
import { useEffect, useState } from 'react';
import * as api from '../../services/api';
import { logger } from '../../utils/logger';
import './BackupScheduleBanner.css';

/** localStorage flag — set once the operator dismisses the banner. */
const DISMISS_KEY = 'ecm:dbas-backup-banner-dismissed';
/** The scheduled-backup task this banner is about. */
const BACKUP_TASK_ID = 'dbas_backup';

export function BackupScheduleBanner() {
  // `null` = still checking; `true`/`false` = whether to render.
  const [show, setShow] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;

    // Once dismissed, never re-check — the operator has acknowledged the nudge.
    if (localStorage.getItem(DISMISS_KEY) === '1') {
      setShow(false);
      return;
    }

    api
      .getTaskSchedules(BACKUP_TASK_ID)
      .then(({ schedules }) => {
        if (cancelled) return;
        const hasEnabled = schedules.some((s) => s.enabled);
        setShow(!hasEnabled);
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

  const handleDismiss = () => {
    localStorage.setItem(DISMISS_KEY, '1');
    setShow(false);
  };

  const handleSetUp = () => {
    // Same contract NotificationCenter uses: stash intent + fire the event
    // App.tsx listens for to switch to Settings > Scheduled Tasks and open the
    // editor for this task.
    sessionStorage.setItem(
      'ecm:open-task-editor',
      JSON.stringify({ taskId: BACKUP_TASK_ID }),
    );
    window.dispatchEvent(
      new CustomEvent('ecm:open-task-editor', { detail: { taskId: BACKUP_TASK_ID } }),
    );
  };

  return (
    <div className="backup-schedule-banner" data-testid="backup-schedule-banner" role="alert">
      <span className="material-icons backup-schedule-banner-icon" aria-hidden="true">
        event_busy
      </span>
      <div className="backup-schedule-banner-body">
        <span className="backup-schedule-banner-title">Backups are not scheduled yet</span>
        <span className="backup-schedule-banner-detail">
          ECM does not run automatic backups until you schedule them. Set one up so you always
          have a recent backup to restore from.
        </span>
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
      </div>
      <button
        type="button"
        className="backup-schedule-banner-dismiss"
        data-testid="backup-schedule-banner-dismiss"
        aria-label="Dismiss"
        title="Dismiss"
        onClick={handleDismiss}
      >
        <span className="material-icons" aria-hidden="true">
          close
        </span>
      </button>
    </div>
  );
}
