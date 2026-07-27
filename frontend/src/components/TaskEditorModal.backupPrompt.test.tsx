/**
 * Integration test for the schedule-enable backup-destination trigger (bead s5a3o).
 *
 * Enabling/creating a backup schedule is trigger (A): creating a dbas_backup
 * schedule pops the backup-destination first-run choice — but only for the
 * dbas_backup task, and only if the operator has not already answered. Proves
 * the TaskEditorModal → provider wiring through the real provider.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import type { TaskStatus } from '../services/api';

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
  // Used by the SecurityFirstRunModal the provider renders.
  saveSecurityMode: vi.fn().mockResolvedValue({ ssrf_outbound_mode: 'lan_friendly' }),
}));

vi.mock('../services/channelPipelineApi', () => ({
  getChannelPipelineRules: vi.fn().mockResolvedValue([]),
}));

const notify = { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() };
vi.mock('../contexts/NotificationContext', () => ({
  useNotifications: () => notify,
}));

import { SECURITY_FIRST_RUN_KEY } from './SecurityFirstRunModal';
import { BackupDestinationPromptProvider } from '../contexts/BackupDestinationPromptContext';
import { TaskEditorModal } from './TaskEditorModal';

function makeTask(taskId: string): TaskStatus {
  return {
    task_id: taskId,
    task_name: taskId,
    task_description: '',
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
  };
}

function renderEditor(taskId: string) {
  return render(
    <BackupDestinationPromptProvider>
      <TaskEditorModal task={makeTask(taskId)} onClose={() => {}} onSaved={() => {}} />
    </BackupDestinationPromptProvider>,
  );
}

/** Open the "Add Schedule" sub-editor and commit it with the daily defaults. */
async function addASchedule() {
  fireEvent.click(await screen.findByRole('button', { name: /add schedule/i }));
  // Now both the open button and the editor's save button read "Add Schedule";
  // the editor footer save is the last one in DOM order.
  const buttons = screen.getAllByRole('button', { name: /add schedule/i });
  fireEvent.click(buttons[buttons.length - 1]);
}

describe('TaskEditorModal — backup-destination trigger (s5a3o)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it('opens the choice when a dbas_backup schedule is created (flag unset)', async () => {
    renderEditor('dbas_backup');
    await addASchedule();
    expect(await screen.findByTestId('security-first-run-modal')).toBeInTheDocument();
  });

  it('does NOT open the choice for a non-backup task', async () => {
    renderEditor('cleanup');
    await addASchedule();
    await waitFor(() => expect(notify.success).not.toHaveBeenCalled());
    expect(screen.queryByTestId('security-first-run-modal')).not.toBeInTheDocument();
  });

  it('does NOT open the choice when the operator has already answered (flag set)', async () => {
    localStorage.setItem(SECURITY_FIRST_RUN_KEY, '1');
    renderEditor('dbas_backup');
    await addASchedule();
    await waitFor(() => expect((screen.queryAllByRole('button', { name: /add schedule/i })).length).toBe(1));
    expect(screen.queryByTestId('security-first-run-modal')).not.toBeInTheDocument();
  });
});
