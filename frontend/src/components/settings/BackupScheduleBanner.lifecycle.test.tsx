/**
 * The off-page enable-then-disable lifecycle (bead
 * enhancedchannelmanager-pui76, review round 2).
 *
 * The sequence that defeated the old implementation, verbatim from the review:
 *
 *   1. Snooze the banner on Settings → Backup & Restore.
 *   2. Leave the page.
 *   3. Enable the dbas_backup schedule somewhere else (Scheduled Tasks).
 *   4. Disable it again.
 *   5. Come back to Backup & Restore.
 *
 * The banner never mounted while the schedule was enabled, so it never removed
 * the snooze; it saw only the final disabled state and honoured a stale snooze
 * against a condition that had genuinely returned — an install with no backups
 * and no warning. The existing suite could not catch it: its enabled-schedule
 * test mounts the banner while ALREADY enabled, which is step 3 with the
 * banner conveniently on screen, and that is the one arrangement in which the
 * old code worked.
 *
 * Layer: consumer integration. `services/api` is deliberately NOT mocked here
 * — only `fetch` is — because the fix lives in `services/api`, and step 3 is
 * "some other page called the task API", not "the banner rendered". Mocking
 * the API module would delete the very seam under test.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react';
import * as api from '../../services/api';
import { SNOOZE_UNTIL_KEY } from '../../utils/backupBannerSnooze';
import { BackupScheduleBanner } from './BackupScheduleBanner';

const DAY_MS = 24 * 60 * 60 * 1000;

function jsonResponse(payload: unknown) {
  return {
    ok: true,
    status: 200,
    statusText: 'OK',
    json: async () => payload,
  } as unknown as Response;
}

/** Answer every request with this payload until the next call. */
function serve(payload: unknown) {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(payload)));
}

function backupSchedule(enabled: boolean) {
  return { id: 1, task_id: 'dbas_backup', name: 'Nightly backup', enabled };
}

const banner = () => screen.queryByTestId('backup-schedule-banner');

/** Mount the banner against a given schedule list and wait for it to settle. */
async function visitBackupPage(schedules: ReturnType<typeof backupSchedule>[]) {
  serve({ schedules });
  render(<BackupScheduleBanner />);
  await waitFor(() => expect(global.fetch).toHaveBeenCalled());
}

describe('BackupScheduleBanner — off-page schedule lifecycle', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('warns again after the schedule is enabled and disabled while the banner is off screen', async () => {
    // 1. Snooze it.
    await visitBackupPage([]);
    await waitFor(() => expect(banner()).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('backup-schedule-banner-dismiss'));
    expect(banner()).not.toBeInTheDocument();
    expect(localStorage.getItem(SNOOZE_UNTIL_KEY)).not.toBeNull();

    // 2. Leave the page. Nothing of this component is mounted from here on.
    cleanup();

    // 3. Enable the schedule from somewhere else entirely. Scheduled Tasks
    //    polls getTasks; the task editor saves through updateTaskSchedule.
    //    Either observation is enough, and both are exercised.
    serve({ tasks: [{ task_id: 'dbas_backup', schedules: [backupSchedule(true)] }] });
    await api.getTasks();

    // 4. Disable it again, still off-page.
    serve(backupSchedule(false));
    await api.updateTaskSchedule('dbas_backup', 1, { enabled: false });

    // 5. Come back. The install is unscheduled again, so it must be warned
    //    again — the snooze died the moment the schedule was seen enabled.
    await visitBackupPage([backupSchedule(false)]);
    await waitFor(() => expect(banner()).toBeInTheDocument());
  });

  it('keeps the snooze when the off-page schedule was never enabled', async () => {
    // The complement, so the test above cannot pass by simply never snoozing.
    await visitBackupPage([]);
    await waitFor(() => expect(banner()).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('backup-schedule-banner-dismiss'));
    cleanup();

    serve({ tasks: [{ task_id: 'dbas_backup', schedules: [backupSchedule(false)] }] });
    await api.getTasks();

    await visitBackupPage([backupSchedule(false)]);
    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(banner()).not.toBeInTheDocument();
  });

  it('an enabled schedule seen on any other task leaves the snooze intact', async () => {
    await visitBackupPage([]);
    await waitFor(() => expect(banner()).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('backup-schedule-banner-dismiss'));
    cleanup();

    serve({ tasks: [{ task_id: 'epg_refresh', schedules: [{ id: 4, enabled: true }] }] });
    await api.getTasks();

    await visitBackupPage([backupSchedule(false)]);
    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(banner()).not.toBeInTheDocument();
  });

  it('a snooze taken on this page still expires on its own', async () => {
    // Guards the other direction of the same fix: voiding on observation must
    // not be the ONLY way a snooze ends, or a permanently-unscheduled install
    // that is never looked at elsewhere stays quiet forever.
    localStorage.setItem(SNOOZE_UNTIL_KEY, String(Date.now() - 1));
    await visitBackupPage([backupSchedule(false)]);
    await waitFor(() => expect(banner()).toBeInTheDocument());
    expect(Number(localStorage.getItem(SNOOZE_UNTIL_KEY))).toBeLessThan(Date.now() + DAY_MS);
  });
});
