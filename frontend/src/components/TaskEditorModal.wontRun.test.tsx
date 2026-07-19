/**
 * TaskEditorModal — "enabled but won't run" UX tests (bead vkktd.4).
 *
 * Locks:
 *   1. The rewritten enable hint copy ("The task and at least one schedule
 *      below must both be enabled...") — the old copy implied the task toggle
 *      alone was the whole story.
 *   2. The live inline warning when the task is enabled but no child schedule
 *      is (and its absence for MANUAL-only tasks / when a schedule is on).
 *   3. The auto-reconcile toast: saving an enabled task whose schedules are
 *      all disabled triggers the backend reconcile; the modal re-reads the
 *      schedules and announces "Also enabled ... schedule" — a silent
 *      reconcile is a trust problem.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import type { TaskStatus, TaskSchedule } from '../services/api';

vi.mock('../services/api', () => ({
  getTaskParameterSchema: vi.fn().mockResolvedValue({ parameters: [] }),
  getTaskSchedules: vi.fn().mockResolvedValue({ schedules: [] }),
  getChannelGroups: vi.fn().mockResolvedValue([]),
  getEPGSources: vi.fn().mockResolvedValue([]),
  getM3UAccounts: vi.fn().mockResolvedValue([]),
  getExportSections: vi.fn().mockResolvedValue([]),
  getSettings: vi.fn().mockResolvedValue({}),
  updateTask: vi.fn().mockResolvedValue(undefined),
  createTaskSchedule: vi.fn().mockResolvedValue({ id: 1 }),
  updateTaskSchedule: vi.fn().mockResolvedValue({ id: 1 }),
}));

vi.mock('../services/channelPipelineApi', () => ({
  getChannelPipelineRules: vi.fn().mockResolvedValue([]),
}));

const notify = { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() };
vi.mock('../contexts/NotificationContext', () => ({
  useNotifications: () => notify,
}));

vi.mock('../contexts/BackupDestinationPromptContext', () => ({
  useBackupDestinationPrompt: () => ({ promptBackupDestination: vi.fn() }),
}));

import * as api from '../services/api';
import { TaskEditorModal } from './TaskEditorModal';

function makeSchedule(overrides: Partial<TaskSchedule> = {}): TaskSchedule {
  return {
    id: 11,
    task_id: 'auto_creation',
    name: 'Hourly',
    enabled: false,
    schedule_type: 'interval',
    interval_seconds: 3600,
    schedule_time: null,
    timezone: null,
    days_of_week: null,
    day_of_month: null,
    week_parity: null,
    parameters: {},
    next_run_at: null,
    last_run_at: null,
    description: 'Every hour',
    created_at: '2026-07-01T00:00:00Z',
    updated_at: null,
    ...overrides,
  } as TaskSchedule;
}

function makeTask(overrides: Partial<TaskStatus> = {}): TaskStatus {
  return {
    task_id: 'auto_creation',
    task_name: 'Channel Pipeline',
    task_description: 'Runs pipeline rules',
    status: 'idle',
    enabled: true,
    progress: {
      total: 0, current: 0, status: 'idle', current_item: null,
      success_count: 0, failed_count: 0, skipped_count: 0,
    } as unknown as TaskStatus['progress'],
    schedule: { schedule_type: 'interval' } as unknown as TaskStatus['schedule'],
    schedules: [],
    last_run: null,
    next_run: null,
    config: {},
    ...overrides,
  };
}

function renderEditor(task: TaskStatus) {
  return render(<TaskEditorModal task={task} onClose={() => {}} onSaved={() => {}} />);
}

describe('TaskEditorModal — vkktd.4 wontRun UX', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows the rewritten enable hint copy', async () => {
    vi.mocked(api.getTaskSchedules).mockResolvedValue({ schedules: [] });
    renderEditor(makeTask());

    expect(
      await screen.findByText(/the task and at least one schedule below must both be enabled/i)
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/when disabled, no schedules will run/i)
    ).not.toBeInTheDocument();
  });

  it('shows the inline warning when the task is enabled and all schedules are disabled', async () => {
    vi.mocked(api.getTaskSchedules).mockResolvedValue({ schedules: [makeSchedule({ enabled: false })] });
    renderEditor(makeTask());

    const warning = await screen.findByTestId('schedule-wont-run-warning');
    expect(warning).toHaveTextContent(/will not run automatically/i);
    // Non-manual task with an existing schedule → promises the save reconcile.
    expect(warning).toHaveTextContent(/save and the most recent schedule will be enabled/i);
  });

  it('hides the warning when at least one schedule is enabled', async () => {
    vi.mocked(api.getTaskSchedules).mockResolvedValue({ schedules: [makeSchedule({ enabled: true })] });
    renderEditor(makeTask());

    // Wait for schedules to load, then assert no warning.
    await screen.findByText('Hourly');
    expect(screen.queryByTestId('schedule-wont-run-warning')).not.toBeInTheDocument();
  });

  it('hides the warning for MANUAL-only tasks with no schedules', async () => {
    vi.mocked(api.getTaskSchedules).mockResolvedValue({ schedules: [] });
    renderEditor(makeTask({
      task_id: 'cleanup',
      schedule: { schedule_type: 'manual' } as unknown as TaskStatus['schedule'],
    }));

    await screen.findByText(/no schedules configured/i);
    expect(screen.queryByTestId('schedule-wont-run-warning')).not.toBeInTheDocument();
  });

  it('shows the warning when unchecking is reverted (live with the checkbox)', async () => {
    vi.mocked(api.getTaskSchedules).mockResolvedValue({ schedules: [makeSchedule({ enabled: false })] });
    renderEditor(makeTask());

    await screen.findByTestId('schedule-wont-run-warning');

    // Disable the task → warning goes away (a disabled task is honest).
    fireEvent.click(screen.getByLabelText(/enable task/i));
    expect(screen.queryByTestId('schedule-wont-run-warning')).not.toBeInTheDocument();

    // Re-enable → warning returns.
    fireEvent.click(screen.getByLabelText(/enable task/i));
    expect(screen.getByTestId('schedule-wont-run-warning')).toBeInTheDocument();
  });

  it('toasts when saving auto-reconciled the existing schedule', async () => {
    // Before save: one disabled schedule. After save: backend reconciled it on.
    vi.mocked(api.getTaskSchedules)
      .mockResolvedValueOnce({ schedules: [makeSchedule({ enabled: false })] })
      .mockResolvedValue({ schedules: [makeSchedule({ enabled: true })] });
    renderEditor(makeTask());

    await screen.findByTestId('schedule-wont-run-warning');
    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => {
      expect(notify.info).toHaveBeenCalledWith(
        expect.stringMatching(/also enabled the "Hourly" schedule, so this task will actually run/i),
        'Channel Pipeline'
      );
    });
    expect(api.updateTask).toHaveBeenCalledWith('auto_creation', expect.objectContaining({ enabled: true }));
  });

  it('does not toast a reconcile when a schedule was already enabled', async () => {
    vi.mocked(api.getTaskSchedules).mockResolvedValue({ schedules: [makeSchedule({ enabled: true })] });
    renderEditor(makeTask());

    await screen.findByText('Hourly');
    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => expect(api.updateTask).toHaveBeenCalled());
    expect(notify.info).not.toHaveBeenCalled();
  });

  it('opens straight at Add Schedule when openAddSchedule is set (Fix-it path)', async () => {
    vi.mocked(api.getTaskSchedules).mockResolvedValue({ schedules: [] });
    render(
      <TaskEditorModal task={makeTask()} onClose={() => {}} onSaved={() => {}} openAddSchedule />
    );

    expect(await screen.findByRole('heading', { name: /add schedule/i })).toBeInTheDocument();
  });
});
