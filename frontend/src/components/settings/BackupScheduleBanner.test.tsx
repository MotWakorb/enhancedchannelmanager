/**
 * Tests for BackupScheduleBanner (bead ikv8z).
 *
 * Contract:
 *   - Scheduled DBAS backup ships OFF by default. To stop an operator silently
 *     ending up with zero backups, surface a one-time banner when no enabled
 *     `dbas_backup` schedule exists: "Backups are not scheduled yet. Set one up".
 *   - The banner is one-time: dismissing it persists, and it stays gone on the
 *     next mount AS LONG AS backups are still unscheduled... but the dismissal
 *     is keyed so it can never permanently hide a real "no backups" risk — once
 *     dismissed it does not nag, matching the bead's "dismiss/persist" rule.
 *   - When an enabled schedule exists, the banner never renders (regardless of
 *     dismissal state).
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

const DISMISS_KEY = 'ecm:dbas-backup-banner-dismissed';

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

describe('BackupScheduleBanner', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it('shows the banner when no backup schedule exists', async () => {
    (api.getTaskSchedules as Mock).mockResolvedValue({ schedules: [] });
    render(<BackupScheduleBanner />);
    await waitFor(() =>
      expect(screen.getByTestId('backup-schedule-banner')).toBeInTheDocument(),
    );
    expect(screen.getByText(/not scheduled yet/i)).toBeInTheDocument();
    // No "SSRF"/"RFC1918"-style jargon — plain language only.
    expect(screen.getByTestId('backup-schedule-banner')).toHaveTextContent(/set one up/i);
  });

  it('shows the banner when only DISABLED schedules exist', async () => {
    (api.getTaskSchedules as Mock).mockResolvedValue({
      schedules: [schedule({ enabled: false })],
    });
    render(<BackupScheduleBanner />);
    await waitFor(() =>
      expect(screen.getByTestId('backup-schedule-banner')).toBeInTheDocument(),
    );
  });

  it('does NOT show the banner when an enabled schedule exists', async () => {
    (api.getTaskSchedules as Mock).mockResolvedValue({
      schedules: [schedule({ enabled: true })],
    });
    render(<BackupScheduleBanner />);
    // Let the fetch resolve, then assert the banner never appears.
    await waitFor(() => expect(api.getTaskSchedules).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.queryByTestId('backup-schedule-banner')).not.toBeInTheDocument(),
    );
  });

  it('hides the banner after dismiss and persists the dismissal', async () => {
    (api.getTaskSchedules as Mock).mockResolvedValue({ schedules: [] });
    render(<BackupScheduleBanner />);
    await waitFor(() =>
      expect(screen.getByTestId('backup-schedule-banner')).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByTestId('backup-schedule-banner-dismiss'));
    expect(screen.queryByTestId('backup-schedule-banner')).not.toBeInTheDocument();
    expect(localStorage.getItem(DISMISS_KEY)).toBe('1');
  });

  it('stays hidden on remount once dismissed (still unscheduled)', async () => {
    localStorage.setItem(DISMISS_KEY, '1');
    (api.getTaskSchedules as Mock).mockResolvedValue({ schedules: [] });
    render(<BackupScheduleBanner />);
    // Dismissed acknowledgement short-circuits before any schedule fetch — the
    // operator has already seen and dismissed the nudge.
    await waitFor(() =>
      expect(screen.queryByTestId('backup-schedule-banner')).not.toBeInTheDocument(),
    );
    expect(api.getTaskSchedules).not.toHaveBeenCalled();
  });

  it('"Set one up" navigates to the dbas_backup schedule editor', async () => {
    (api.getTaskSchedules as Mock).mockResolvedValue({ schedules: [] });
    const dispatchSpy = vi.spyOn(window, 'dispatchEvent');
    render(<BackupScheduleBanner />);
    await waitFor(() =>
      expect(screen.getByTestId('backup-schedule-banner')).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByTestId('backup-schedule-banner-cta'));

    expect(sessionStorage.getItem('ecm:open-task-editor')).toBe(
      JSON.stringify({ taskId: 'dbas_backup' }),
    );
    expect(dispatchSpy).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'ecm:open-task-editor' }),
    );
  });
});
