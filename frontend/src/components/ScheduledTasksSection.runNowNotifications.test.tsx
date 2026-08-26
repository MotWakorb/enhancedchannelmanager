/**
 * Regression (GH #720 Part B, y3m6o.3 round-11 frozen-gate): a manual "Run Now"
 * must NOT render a completed-with-warnings result (success=true, failed_count>0
 * — e.g. a coalesced/deferred profile reconcile) as a plain green "Task
 * Completed". It must show an amber WARNING notification surfacing result.message
 * (which carries "…profile reconcile deferred"). A clean run (failed_count===0)
 * still shows a success notification.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ScheduledTasksSection } from './ScheduledTasksSection';
import type { TaskStatus } from '../services/api';
import * as api from '../services/api';

const notify = {
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
  dismiss: vi.fn(),
  dismissAll: vi.fn(),
};

vi.mock('../contexts/NotificationContext', () => ({
  useNotifications: () => notify,
}));

// TaskHistoryPanel fetches its own data — stub it out; not under test here.
vi.mock('./TaskHistoryPanel', () => ({
  TaskHistoryPanel: () => null,
}));

vi.mock('../services/api', async () => {
  const actual = await vi.importActual<typeof api>('../services/api');
  return {
    ...actual,
    getTasks: vi.fn(),
    runTask: vi.fn(),
  };
});

function makeMonitorTask(): TaskStatus {
  return {
    task_id: 'm3u_change_monitor',
    task_name: 'M3U Change Monitor',
    task_description: 'Poll M3U accounts for changes',
    status: 'idle',
    enabled: true,
    progress: {
      total: 0, current: 0, status: 'idle', current_item: null,
      success_count: 0, failed_count: 0, skipped_count: 0,
    } as unknown as TaskStatus['progress'],
    schedule: { schedule_type: 'manual' } as unknown as TaskStatus['schedule'],
    schedules: [],
    last_run: null,
    next_run: null,
    config: {},
  } as unknown as TaskStatus;
}

function makeSyncTask(): TaskStatus {
  return {
    ...makeMonitorTask(),
    task_id: 'dbas_sync_7',
    task_name: 'Cross-Instance Sync: Living Room B',
    config: { confirm_apply: true },
  } as TaskStatus;
}

function runResult(overrides: Partial<Awaited<ReturnType<typeof api.runTask>>>) {
  return {
    success: true,
    message: '',
    started_at: '', completed_at: '',
    total_items: 1, success_count: 1, failed_count: 0, skipped_count: 0,
    ...overrides,
  };
}

describe('ScheduledTasksSection — Run Now completed-with-warnings', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (api.getTasks as ReturnType<typeof vi.fn>).mockResolvedValue({ tasks: [makeMonitorTask()] });
  });

  it('renders a deferred reconcile (success=true, failed_count>0) as an amber WARNING, not success', async () => {
    (api.runTask as ReturnType<typeof vi.fn>).mockResolvedValue(
      runResult({
        success: true,
        failed_count: 1,
        message: 'Checked 1 account(s), no external changes (profile reconcile deferred (another sweep in progress))',
      })
    );

    render(<ScheduledTasksSection />);
    const runBtn = await screen.findByRole('button', { name: /run now/i });
    fireEvent.click(runBtn);

    await waitFor(() => expect(notify.warning).toHaveBeenCalledTimes(1));
    expect(notify.warning.mock.calls[0][0]).toContain('profile reconcile deferred');
    expect(notify.success).not.toHaveBeenCalled();
    expect(notify.error).not.toHaveBeenCalled();
  });

  it('renders a clean run (failed_count===0) as a success notification', async () => {
    (api.runTask as ReturnType<typeof vi.fn>).mockResolvedValue(
      runResult({ success: true, failed_count: 0, success_count: 2 })
    );

    render(<ScheduledTasksSection />);
    const runBtn = await screen.findByRole('button', { name: /run now/i });
    fireEvent.click(runBtn);

    await waitFor(() => expect(notify.success).toHaveBeenCalledTimes(1));
    expect(notify.warning).not.toHaveBeenCalled();
    expect(notify.error).not.toHaveBeenCalled();
  });

  it('does not inherit parent apply config into a generic sync Run Now', async () => {
    (api.getTasks as ReturnType<typeof vi.fn>).mockResolvedValue({ tasks: [makeSyncTask()] });
    (api.runTask as ReturnType<typeof vi.fn>).mockResolvedValue(
      runResult({ success: true, message: 'Preview complete' })
    );

    render(<ScheduledTasksSection />);
    fireEvent.click(await screen.findByRole('button', { name: /run now/i }));

    await waitFor(() => expect(api.runTask).toHaveBeenCalledWith(
      'dbas_sync_7',
      undefined,
      undefined,
    ));
  });
});
