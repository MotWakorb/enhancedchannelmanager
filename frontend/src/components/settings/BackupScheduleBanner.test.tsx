/**
 * Tests for BackupScheduleBanner (beads ikv8z, enhancedchannelmanager-iij6s).
 *
 * Contract:
 *   - Scheduled DBAS backup ships OFF by default and STAYS off by default (PO
 *     decision D2). This banner is the whole enforcement mechanism for that
 *     decision, so its dismissal cannot be permanent: one click by one operator
 *     in one browser used to silence the only signal that an install has no
 *     backups, forever (bead iij6s).
 *   - Dismissal is therefore a SNOOZE. It hides the banner for a fixed window
 *     and the banner returns afterwards while the install is still unscheduled.
 *   - The control says so: "Remind me in 30 days", not a bare ✕.
 *   - An enabled schedule hides the banner and clears any stored snooze, so a
 *     schedule that is later disabled brings the warning straight back rather
 *     than being suppressed by a stale snooze.
 *   - The legacy permanent-dismissal flag is not honoured and is cleaned up —
 *     installs silenced under the old behaviour get told again.
 *   - "Set one up" routes the operator to the schedule editor for dbas_backup.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';

vi.mock('../../services/api', () => ({
  getTaskSchedules: vi.fn(),
}));

import * as api from '../../services/api';
import { BackupScheduleBanner } from './BackupScheduleBanner';

type Mock = ReturnType<typeof vi.fn>;

const SNOOZE_KEY = 'ecm:dbas-backup-banner-snoozed-until';
const LEGACY_DISMISS_KEY = 'ecm:dbas-backup-banner-dismissed';
const DAY_MS = 24 * 60 * 60 * 1000;

function schedule(overrides: Partial<api.TaskSchedule> = {}): api.TaskSchedule {
  return {
    id: 1,
    task_id: 'dbas_backup',
    name: 'Nightly backup',
    enabled: true,
    schedule_type: 'daily',
    interval_seconds: null,
    schedule_time: '03:00',
    timezone: 'UTC',
    days_of_week: null,
    day_of_month: null,
    week_parity: null,
    parameters: {},
    next_run_at: null,
    last_run_at: null,
    description: '',
    created_at: null,
    updated_at: null,
    ...overrides,
  };
}

const banner = () => screen.queryByTestId('backup-schedule-banner');

async function renderUnscheduled() {
  (api.getTaskSchedules as Mock).mockResolvedValue({ schedules: [] });
  render(<BackupScheduleBanner />);
  await waitFor(() => expect(api.getTaskSchedules).toHaveBeenCalled());
}

describe('BackupScheduleBanner', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it('shows the banner when no backup schedule exists', async () => {
    await renderUnscheduled();
    await waitFor(() => expect(banner()).toBeInTheDocument());
    expect(screen.getByText(/not scheduled yet/i)).toBeInTheDocument();
    // No "SSRF"/"RFC1918"-style jargon — plain language only.
    expect(banner()).toHaveTextContent(/set one up/i);
  });

  it('shows the banner when only DISABLED schedules exist', async () => {
    (api.getTaskSchedules as Mock).mockResolvedValue({
      schedules: [schedule({ enabled: false })],
    });
    render(<BackupScheduleBanner />);
    await waitFor(() => expect(banner()).toBeInTheDocument());
  });

  it('does NOT show the banner when an enabled schedule exists', async () => {
    (api.getTaskSchedules as Mock).mockResolvedValue({
      schedules: [schedule({ enabled: true })],
    });
    render(<BackupScheduleBanner />);
    await waitFor(() => expect(api.getTaskSchedules).toHaveBeenCalled());
    await waitFor(() => expect(banner()).not.toBeInTheDocument());
  });

  it('warns that a hand-taken backup is not a substitute for a schedule', async () => {
    // PO decision D4 routes operators to the manual button first, so the state
    // this banner has to catch is "one stale backup, no schedule".
    await renderUnscheduled();
    await waitFor(() => expect(banner()).toBeInTheDocument());
    expect(banner()).toHaveTextContent(/by hand/i);
  });

  it('labels the dismissal with what it actually does', async () => {
    await renderUnscheduled();
    await waitFor(() => expect(banner()).toBeInTheDocument());
    // A bare ✕ reads as "gone". This control is a snooze and has to say so
    // before it is clicked, not in a tooltip after.
    expect(screen.getByTestId('backup-schedule-banner-dismiss')).toHaveTextContent(
      /remind me in 30 days/i,
    );
  });

  it('snoozes rather than permanently dismissing (bead iij6s)', async () => {
    await renderUnscheduled();
    await waitFor(() => expect(banner()).toBeInTheDocument());

    const before = Date.now();
    fireEvent.click(screen.getByTestId('backup-schedule-banner-dismiss'));
    expect(banner()).not.toBeInTheDocument();

    const storedUntil = Number(localStorage.getItem(SNOOZE_KEY));
    expect(Number.isFinite(storedUntil)).toBe(true);
    // ~30 days out, generously bounded so the assertion is about the window
    // being finite and roughly a month, not about clock precision.
    expect(storedUntil).toBeGreaterThan(before + 29 * DAY_MS);
    expect(storedUntil).toBeLessThan(before + 31 * DAY_MS);
  });

  it('stays hidden for the snooze window while still unscheduled', async () => {
    localStorage.setItem(SNOOZE_KEY, String(Date.now() + 5 * DAY_MS));
    await renderUnscheduled();
    await waitFor(() => expect(banner()).not.toBeInTheDocument());
  });

  it('comes back once the snooze expires and the install is still unscheduled', async () => {
    // This is the property bead iij6s exists for: the signal cannot be
    // permanently silenced while the condition it reports is still true.
    localStorage.setItem(SNOOZE_KEY, String(Date.now() - 1));
    await renderUnscheduled();
    await waitFor(() => expect(banner()).toBeInTheDocument());
  });

  it('does not honour the legacy permanent dismissal, and clears it', async () => {
    localStorage.setItem(LEGACY_DISMISS_KEY, '1');
    await renderUnscheduled();
    await waitFor(() => expect(banner()).toBeInTheDocument());
    expect(localStorage.getItem(LEGACY_DISMISS_KEY)).toBeNull();
  });

  it('treats an unreadable snooze value as expired rather than as forever', async () => {
    localStorage.setItem(SNOOZE_KEY, 'never');
    await renderUnscheduled();
    await waitFor(() => expect(banner()).toBeInTheDocument());
  });

  it('clears a stored snooze once an enabled schedule exists', async () => {
    // Otherwise a snooze taken today outlives the schedule that replaced it,
    // and disabling that schedule tomorrow leaves the install silently
    // unscheduled and silently un-warned.
    localStorage.setItem(SNOOZE_KEY, String(Date.now() + 5 * DAY_MS));
    (api.getTaskSchedules as Mock).mockResolvedValue({
      schedules: [schedule({ enabled: true })],
    });
    render(<BackupScheduleBanner />);
    await waitFor(() => expect(localStorage.getItem(SNOOZE_KEY)).toBeNull());
    expect(banner()).not.toBeInTheDocument();
  });

  it('stays quiet when the schedule check fails', async () => {
    (api.getTaskSchedules as Mock).mockRejectedValue(new Error('offline'));
    render(<BackupScheduleBanner />);
    await waitFor(() => expect(api.getTaskSchedules).toHaveBeenCalled());
    await waitFor(() => expect(banner()).not.toBeInTheDocument());
  });

  it('"Set one up" navigates to the dbas_backup schedule editor', async () => {
    const dispatchSpy = vi.spyOn(window, 'dispatchEvent');
    await renderUnscheduled();
    await waitFor(() => expect(banner()).toBeInTheDocument());

    fireEvent.click(screen.getByTestId('backup-schedule-banner-cta'));

    expect(sessionStorage.getItem('ecm:open-task-editor')).toBe(
      JSON.stringify({ taskId: 'dbas_backup' }),
    );
    expect(dispatchSpy).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'ecm:open-task-editor' }),
    );
  });
});
