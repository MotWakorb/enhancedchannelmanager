import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { TaskEditorModal } from './TaskEditorModal';
import type { TaskSchedule, TaskStatus } from '../services/api';
import * as api from '../services/api';
import * as channelPipelineApi from '../services/channelPipelineApi';

vi.mock('../services/api', async () => {
  const actual = await vi.importActual<typeof api>('../services/api');
  return {
    ...actual,
    getTaskParameterSchema: vi.fn(),
    getTaskSchedules: vi.fn(),
    createTaskSchedule: vi.fn().mockResolvedValue({ id: 1 }),
    updateTaskSchedule: vi.fn().mockResolvedValue({ id: 1 }),
  };
});

vi.mock('../services/channelPipelineApi', () => ({
  getChannelPipelineRules: vi.fn(),
}));

vi.mock('../contexts/NotificationContext', () => ({
  useNotifications: () => ({
    success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn(),
  }),
}));

vi.mock('../contexts/BackupDestinationPromptContext', () => ({
  useBackupDestinationPrompt: () => ({ promptBackupDestination: vi.fn() }),
}));

const schema = {
  task_id: 'auto_creation',
  description: 'Channel Pipeline parameters',
  parameters: [{
    name: 'rule_ids',
    type: 'number_array' as const,
    label: 'Rules',
    description: 'Exact rules to run',
    required: true,
    source: 'auto_creation_rules',
  }],
};

const runnableRule = {
  id: 1,
  name: 'Current',
  enabled: true,
  runnable: true,
  priority: 0,
} as never;

function task(schedules: TaskSchedule[] = []): TaskStatus {
  return {
    task_id: 'auto_creation',
    task_name: 'Channel Pipeline',
    task_description: 'Run selected rules',
    status: 'idle',
    enabled: true,
    progress: {} as TaskStatus['progress'],
    schedule: { schedule_type: 'manual' } as TaskStatus['schedule'],
    schedules,
    last_run: null,
    next_run: null,
    config: {},
  };
}

function schedule(parameters: Record<string, unknown>): TaskSchedule {
  return {
    id: 12,
    task_id: 'auto_creation',
    name: 'Selected rules',
    enabled: false,
    schedule_type: 'daily',
    interval_seconds: null,
    schedule_time: '03:00',
    timezone: 'UTC',
    days_of_week: null,
    day_of_month: null,
    week_parity: null,
    parameters,
    next_run_at: null,
    last_run_at: null,
    description: 'Daily at 03:00 UTC',
    created_at: null,
    updated_at: null,
  };
}

describe('TaskEditorModal scheduled Channel Pipeline rules', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getTaskParameterSchema).mockResolvedValue(schema);
    vi.mocked(api.getTaskSchedules).mockResolvedValue({ schedules: [] });
  });

  it('shows a loading state and cannot save before rules arrive', async () => {
    vi.mocked(channelPipelineApi.getChannelPipelineRules).mockReturnValue(new Promise(() => {}));
    render(<TaskEditorModal task={task()} openAddSchedule onClose={vi.fn()} onSaved={vi.fn()} />);

    const dialog = screen.getByRole('dialog', { name: 'Add Schedule' });
    expect(await within(dialog).findByText('Loading Channel Pipeline rules...')).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: /add schedule/i })).toBeDisabled();
  });

  it('shows an empty state and never treats no selection as run-all', async () => {
    vi.mocked(channelPipelineApi.getChannelPipelineRules).mockResolvedValue([]);
    render(<TaskEditorModal task={task()} openAddSchedule onClose={vi.fn()} onSaved={vi.fn()} />);

    const dialog = screen.getByRole('dialog', { name: 'Add Schedule' });
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('No runnable Channel Pipeline rules are available');
    expect(within(dialog).getByRole('button', { name: /add schedule/i })).toBeDisabled();
  });

  it('shows a load error and keeps save disabled', async () => {
    vi.mocked(channelPipelineApi.getChannelPipelineRules).mockRejectedValue(new Error('offline'));
    render(<TaskEditorModal task={task()} openAddSchedule onClose={vi.fn()} onSaved={vi.fn()} />);

    const dialog = screen.getByRole('dialog', { name: 'Add Schedule' });
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('Could not load Channel Pipeline rules');
    expect(within(dialog).getByRole('button', { name: /add schedule/i })).toBeDisabled();
  });

  it('identifies a deleted stored rule as stale and blocks update', async () => {
    const user = userEvent.setup();
    const stored = schedule({ rule_ids: [99] });
    stored.enabled = true;
    vi.mocked(api.getTaskSchedules).mockResolvedValue({ schedules: [stored] });
    vi.mocked(channelPipelineApi.getChannelPipelineRules).mockResolvedValue([
      runnableRule,
    ]);
    render(<TaskEditorModal task={task([stored])} onClose={vi.fn()} onSaved={vi.fn()} />);

    await user.click(await screen.findByRole('button', { name: 'Edit schedule' }));
    const dialog = screen.getByRole('dialog', { name: 'Edit Schedule' });
    await waitFor(() => expect(within(dialog).getByRole('alert')).toHaveTextContent('Selected rule IDs no longer available: 99'));
    expect(within(dialog).getByRole('button', { name: /update schedule/i })).toBeDisabled();

    await user.click(within(dialog).getByRole('checkbox', { name: /enable this schedule/i }));
    expect(within(dialog).getByRole('button', { name: /update schedule/i })).toBeEnabled();

    await user.click(within(dialog).getByRole('button', { name: /update schedule/i }));
    expect(api.updateTaskSchedule).toHaveBeenCalledWith(
      'auto_creation',
      12,
      expect.objectContaining({ enabled: false, parameters: { rule_ids: [99] } }),
    );
  });

  it('does not offer inactive or invalid enabled rules as runnable', async () => {
    vi.mocked(channelPipelineApi.getChannelPipelineRules).mockResolvedValue([
      runnableRule,
      { id: 2, name: 'Inactive', enabled: true, runnable: false, priority: 1 } as never,
      { id: 3, name: 'Invalid', enabled: true, runnable: false, priority: 2 } as never,
    ]);
    render(<TaskEditorModal task={task()} openAddSchedule onClose={vi.fn()} onSaved={vi.fn()} />);

    const dialog = screen.getByRole('dialog', { name: 'Add Schedule' });
    expect(await within(dialog).findByText('Current')).toBeInTheDocument();
    expect(within(dialog).queryByText('Inactive')).not.toBeInTheDocument();
    expect(within(dialog).queryByText('Invalid')).not.toBeInTheDocument();
  });

  it('surfaces schedule API save failures in the open modal', async () => {
    const user = userEvent.setup();
    vi.mocked(channelPipelineApi.getChannelPipelineRules).mockResolvedValue([runnableRule]);
    vi.mocked(api.createTaskSchedule).mockRejectedValue(new Error('scope rejected'));
    render(<TaskEditorModal task={task()} openAddSchedule onClose={vi.fn()} onSaved={vi.fn()} />);

    const dialog = screen.getByRole('dialog', { name: 'Add Schedule' });
    await user.click(await within(dialog).findByRole('checkbox', { name: 'Current' }));
    await user.click(within(dialog).getByRole('button', { name: /add schedule/i }));

    expect(await within(dialog).findByRole('alert')).toHaveTextContent('scope rejected');
  });
});
