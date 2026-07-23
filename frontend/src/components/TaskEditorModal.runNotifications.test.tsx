/**
 * Regression (GH #720 Part B, y3m6o.3 round-11 frozen-gate): the per-schedule
 * "Run now" (stream_probe) must NOT render a completed-with-warnings result
 * (success=true, failed_count>0 — e.g. a coalesced/deferred profile reconcile)
 * as a plain green "Task Completed". It must show an amber WARNING notification
 * surfacing result.message. A clean run (failed_count===0) still shows success.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { TaskEditorModal } from './TaskEditorModal';
import type { TaskStatus, TaskSchedule } from '../services/api';
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

vi.mock('../contexts/BackupDestinationPromptContext', () => ({
  useBackupDestinationPrompt: () => ({ promptBackupDestination: vi.fn() }),
}));

vi.mock('../services/channelPipelineApi', () => ({
  getChannelPipelineRules: vi.fn().mockResolvedValue([]),
}));

vi.mock('../services/api', async () => {
  const actual = await vi.importActual<typeof api>('../services/api');
  return {
    ...actual,
    getTaskParameterSchema: vi.fn().mockResolvedValue({ parameters: [] }),
    updateTask: vi.fn().mockResolvedValue(undefined),
    getChannelGroups: vi.fn().mockResolvedValue([]),
    getEPGSources: vi.fn().mockResolvedValue([]),
    getM3UAccounts: vi.fn().mockResolvedValue([]),
    getExportSections: vi.fn().mockResolvedValue([]),
    getSettings: vi.fn().mockResolvedValue({}),
    getTaskSchedules: vi.fn(),
    runTask: vi.fn(),
  };
});

function makeSchedule(): TaskSchedule {
  return {
    id: 7,
    task_id: 'stream_probe',
    name: 'Nightly probe',
    enabled: true,
    schedule_type: 'interval' as unknown as TaskSchedule['schedule_type'],
    interval_seconds: 3600,
    schedule_time: null,
    timezone: null,
    days_of_week: null,
    day_of_month: null,
    week_parity: null,
    parameters: {},
    next_run_at: null,
    last_run_at: null,
    description: 'Nightly probe',
    created_at: null,
    updated_at: null,
  };
}

function makeProbeTask(): TaskStatus {
  return {
    task_id: 'stream_probe',
    task_name: 'Stream Probe',
    task_description: 'Probe stream health',
    status: 'idle',
    enabled: true,
    show_notifications: true,
    progress: {
      total: 0, current: 0, status: 'idle', current_item: null,
      success_count: 0, failed_count: 0, skipped_count: 0,
    } as unknown as TaskStatus['progress'],
    schedule: { schedule_type: 'interval' } as unknown as TaskStatus['schedule'],
    schedules: [],
    last_run: null,
    next_run: null,
    config: {},
  } as unknown as TaskStatus;
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

describe('TaskEditorModal — per-schedule Run completed-with-warnings', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (api.getTaskSchedules as ReturnType<typeof vi.fn>).mockResolvedValue({ schedules: [makeSchedule()] });
  });

  it('renders a deferred reconcile (success=true, failed_count>0) as an amber WARNING, not success', async () => {
    (api.runTask as ReturnType<typeof vi.fn>).mockResolvedValue(
      runResult({
        success: true,
        failed_count: 1,
        message: 'Checked 1 account(s) (profile reconcile deferred (another sweep in progress))',
      })
    );

    render(<TaskEditorModal task={makeProbeTask()} onClose={() => {}} onSaved={() => {}} />);
    const runBtn = await screen.findByRole('button', { name: /run now with this schedule/i });
    fireEvent.click(runBtn);

    await waitFor(() => expect(notify.warning).toHaveBeenCalledTimes(1));
    expect(notify.warning.mock.calls[0][0]).toContain('profile reconcile deferred');
    expect(notify.success).not.toHaveBeenCalled();
    expect(notify.error).not.toHaveBeenCalled();
  });

  it('renders a clean run (failed_count===0) as a success notification', async () => {
    (api.runTask as ReturnType<typeof vi.fn>).mockResolvedValue(
      runResult({ success: true, failed_count: 0, success_count: 3 })
    );

    render(<TaskEditorModal task={makeProbeTask()} onClose={() => {}} onSaved={() => {}} />);
    const runBtn = await screen.findByRole('button', { name: /run now with this schedule/i });
    fireEvent.click(runBtn);

    await waitFor(() => expect(notify.success).toHaveBeenCalledTimes(1));
    expect(notify.warning).not.toHaveBeenCalled();
    expect(notify.error).not.toHaveBeenCalled();
  });
});
