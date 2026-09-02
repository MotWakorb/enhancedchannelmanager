/**
 * Unit tests for TaskEditorModal — bd-ia28g retention fields.
 *
 * Locks the three new retention inputs added to the Database Cleanup
 * task config UI:
 *
 * 1. Channel Pipeline execution BLOB retention (days) — default 30
 * 2. Health checks retention (days) — default 7
 * 3. Notifications retention (days) — default 30
 *
 * Per bd-p5b8i DBA spike re-attribution, these are the actual large tables
 * (77% / 14% / 1.3% of operator DB respectively). The backend prune blocks
 * live in backend/tasks/cleanup.py; this UI lets operators tune the
 * retention windows from Settings → Tasks → Database Cleanup.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TaskEditorModal } from './TaskEditorModal';
import type { TaskStatus } from '../services/api';
import * as api from '../services/api';

// Mock the API layer — the modal calls api.getTaskParameterSchema +
// api.updateTask on render/save. We don't need real network behavior to
// exercise the retention input controls.
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
    // Modal also polls schedules on mount; mock to silence the
    // unhandled-rejection noise from undici when no MSW server is up.
    // The real shape is { schedules: TaskSchedule[] }.
    getTaskSchedules: vi.fn().mockResolvedValue({ schedules: [] }),
  };
});

// The modal uses NotificationContext via useNotifications(); stub it out
// with a minimal shape so render doesn't blow up under the test renderer.
vi.mock('../contexts/NotificationContext', () => ({
  useNotifications: () => ({
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  }),
}));

// The modal uses BackupDestinationPromptContext to fire the backup-destination
// choice when a dbas_backup schedule is enabled/created (bead s5a3o). Stub it so
// these retention-field tests render without a provider.
vi.mock('../contexts/BackupDestinationPromptContext', () => ({
  useBackupDestinationPrompt: () => ({ promptBackupDestination: vi.fn() }),
}));

// channelPipelineApi is imported but only called when the task references
// auto_creation_rules in its parameter schema — our mock returns empty,
// so loaders.has('auto_creation_rules') is false. Still stub to be safe.
vi.mock('../services/channelPipelineApi', () => ({
  getChannelPipelineRules: vi.fn().mockResolvedValue([]),
}));

function makeCleanupTask(configOverrides: Record<string, unknown> = {}): TaskStatus {
  return {
    task_id: 'cleanup',
    task_name: 'Database Cleanup',
    task_description: 'Clean up old data',
    status: 'idle',
    enabled: true,
    progress: {
      total: 0,
      current: 0,
      status: 'idle',
      current_item: null,
      success_count: 0,
      failed_count: 0,
      skipped_count: 0,
    } as unknown as TaskStatus['progress'],
    schedule: { schedule_type: 'manual' } as unknown as TaskStatus['schedule'],
    schedules: [],
    last_run: null,
    next_run: null,
    config: {
      probe_history_days: 30,
      task_history_days: 30,
      journal_days: 90,
      auto_creation_blob_days: 30,
      health_checks_days: 7,
      notifications_days: 30,
      event_sync_review_retention_days: 0,
      vacuum_db: true,
      ...configOverrides,
    },
  };
}

function makeStreamProbeTask(): TaskStatus {
  return {
    ...makeCleanupTask(),
    task_id: 'stream_probe',
    task_name: 'Stream Probe',
    task_description: 'Probe streams',
    config: {},
  };
}

describe('TaskEditorModal — bd-ia28g retention fields', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('blocks every parent dismissal affordance while the task save is unresolved', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    vi.mocked(api.updateTask).mockReturnValue(new Promise(() => {}));
    render(<TaskEditorModal task={makeCleanupTask()} onClose={onClose} onSaved={vi.fn()} />);
    const dialog = await screen.findByRole('dialog', { name: 'Configure Task' });
    await user.click(within(dialog).getByRole('button', { name: 'Save Changes' }));

    const headerClose = within(dialog).getByRole('button', { name: 'Close' });
    const cancel = within(dialog).getByRole('button', { name: 'Cancel' });
    expect(headerClose).toBeDisabled();
    expect(cancel).toBeDisabled();
    await user.click(headerClose);
    await user.click(cancel);
    await user.keyboard('{Escape}');
    expect(onClose).not.toHaveBeenCalled();
    expect(dialog).toBeInTheDocument();
  });

  it('blocks Add Schedule header, Cancel, and Escape while create is unresolved', async () => {
    const user = userEvent.setup();
    vi.spyOn(api, 'createTaskSchedule').mockReturnValue(new Promise(() => {}));
    render(<TaskEditorModal task={makeCleanupTask()} onClose={vi.fn()} onSaved={vi.fn()} openAddSchedule />);
    const dialog = await screen.findByRole('dialog', { name: 'Add Schedule' });
    await user.click(within(dialog).getByRole('button', { name: /Add Schedule$/ }));
    const headerClose = within(dialog).getByRole('button', { name: 'Close' });
    const cancel = within(dialog).getByRole('button', { name: 'Cancel' });
    await waitFor(() => expect(cancel).toBeDisabled());
    expect(headerClose).toBeDisabled();
    await user.click(headerClose);
    await user.click(cancel);
    await user.keyboard('{Escape}');
    expect(screen.getByRole('dialog', { name: 'Add Schedule' })).toBe(dialog);
  });

  it('blocks Edit Schedule header, Cancel, and Escape while update is unresolved', async () => {
    const user = userEvent.setup();
    vi.mocked(api.getTaskSchedules).mockResolvedValue({ schedules: [{
      id: 7, task_id: 'cleanup', name: 'Daily', enabled: true, schedule_type: 'daily',
      schedule_time: '03:00', timezone: 'UTC', interval_seconds: null, days_of_week: null,
      day_of_month: null, week_parity: null, parameters: {}, next_run_at: null,
      last_run_at: null, description: '', created_at: '', updated_at: null,
    }] });
    vi.spyOn(api, 'updateTaskSchedule').mockReturnValue(new Promise(() => {}));
    render(<TaskEditorModal task={makeCleanupTask()} onClose={vi.fn()} onSaved={vi.fn()} />);
    await user.click(await screen.findByRole('button', { name: 'Edit schedule' }));
    const dialog = screen.getByRole('dialog', { name: 'Edit Schedule' });
    await user.click(within(dialog).getByRole('button', { name: /Update Schedule$/ }));
    const headerClose = within(dialog).getByRole('button', { name: 'Close' });
    const cancel = within(dialog).getByRole('button', { name: 'Cancel' });
    await waitFor(() => expect(cancel).toBeDisabled());
    expect(headerClose).toBeDisabled();
    await user.click(headerClose);
    await user.click(cancel);
    await user.keyboard('{Escape}');
    expect(screen.getByRole('dialog', { name: 'Edit Schedule' })).toBe(dialog);
  });

  it('merges task defaults into a partial Stream Probe schedule while stored values win', async () => {
    const user = userEvent.setup();
    vi.mocked(api.getTaskParameterSchema).mockResolvedValue({
      task_id: 'stream_probe',
      description: 'Stream Probe parameters',
      parameters: [
        { name: 'channel_groups', type: 'number_array', label: 'Channel Groups', description: 'Groups', default: [], source: 'channel_groups' },
        { name: 'allow_reorder_after_probe', type: 'boolean', label: 'Allow stream reordering', description: 'Reorder', default: true },
        { name: 'timeout', type: 'number', label: 'Timeout', description: 'Timeout', default: 30 },
        { name: 'max_concurrent', type: 'number', label: 'Max Concurrent', description: 'Concurrency', default: 3 },
      ],
    });
    vi.mocked(api.getChannelGroups).mockResolvedValue([
      { id: 7, name: 'Sports', channel_count: 4, is_auto_sync: false },
    ] as Awaited<ReturnType<typeof api.getChannelGroups>>);
    vi.mocked(api.getSettings).mockResolvedValue({
      stream_probe_timeout: 45,
      max_concurrent_probes: 9,
    } as Awaited<ReturnType<typeof api.getSettings>>);
    vi.mocked(api.getTaskSchedules).mockResolvedValue({ schedules: [{
      id: 8, task_id: 'stream_probe', name: 'Partial', enabled: true, schedule_type: 'daily',
      schedule_time: '03:00', timezone: 'UTC', interval_seconds: null, days_of_week: null,
      day_of_month: null, week_parity: null, parameters: { timeout: 77 }, next_run_at: null,
      last_run_at: null, description: '', created_at: '', updated_at: null,
    }] });
    const updateSchedule = vi.spyOn(api, 'updateTaskSchedule').mockResolvedValue(undefined as never);

    render(<TaskEditorModal task={makeStreamProbeTask()} onClose={vi.fn()} onSaved={vi.fn()} />);
    await user.click(await screen.findByRole('button', { name: 'Edit schedule' }));
    await user.click(within(screen.getByRole('dialog', { name: 'Edit Schedule' })).getByRole(
      'button', { name: /update schedule/i },
    ));

    await waitFor(() => expect(updateSchedule).toHaveBeenCalledWith(
      'stream_probe',
      8,
      expect.objectContaining({
        parameters: {
          channel_groups: [7],
          allow_reorder_after_probe: true,
          timeout: 77,
          max_concurrent: 9,
        },
      }),
    ));
  });

  it('renders the channel pipeline BLOB retention input with the config value', async () => {
    render(
      <TaskEditorModal
        task={makeCleanupTask({ auto_creation_blob_days: 45 })}
        onClose={() => {}}
        onSaved={() => {}}
      />
    );

    const label = await screen.findByText(/channel pipeline execution blob retention/i);
    expect(label).toBeInTheDocument();
    // The number input sibling holds the configured value.
    const input = label.parentElement?.querySelector('input[type="number"]');
    expect(input).not.toBeNull();
    expect((input as HTMLInputElement).value).toBe('45');
  });

  it('renders the health checks retention input with the config value', async () => {
    render(
      <TaskEditorModal
        task={makeCleanupTask({ health_checks_days: 14 })}
        onClose={() => {}}
        onSaved={() => {}}
      />
    );

    const label = await screen.findByText(/health checks retention/i);
    const input = label.parentElement?.querySelector('input[type="number"]');
    expect((input as HTMLInputElement).value).toBe('14');
  });

  it('renders the notifications retention input with the config value', async () => {
    render(
      <TaskEditorModal
        task={makeCleanupTask({ notifications_days: 60 })}
        onClose={() => {}}
        onSaved={() => {}}
      />
    );

    const label = await screen.findByText(/notifications retention/i);
    const input = label.parentElement?.querySelector('input[type="number"]');
    expect((input as HTMLInputElement).value).toBe('60');
  });

  it('passes the edited retention values through updateTask on save', async () => {
    // The full save round-trip is the contract operators actually care about:
    // the input edit must land in the payload sent to PATCH /api/tasks/{id}.
    // Without this test the inputs could render but silently not be wired
    // into the save handler.
    const user = userEvent.setup();
    render(
      <TaskEditorModal
        task={makeCleanupTask()}
        onClose={() => {}}
        onSaved={() => {}}
      />
    );

    const blobLabel = await screen.findByText(/channel pipeline execution blob retention/i);
    const blobInput = blobLabel.parentElement?.querySelector('input[type="number"]') as HTMLInputElement;
    // fireEvent.change sets the value atomically — userEvent.type on number
    // inputs is unreliable in jsdom (browser behavior diverges on number
    // field clear/type interaction; observed 30 + "90" → "3090" via
    // userEvent in this repo's setup).
    fireEvent.change(blobInput, { target: { value: '90' } });

    const saveBtn = await screen.findByRole('button', { name: /save changes/i });
    await user.click(saveBtn);

    expect(api.updateTask).toHaveBeenCalledWith(
      'cleanup',
      expect.objectContaining({
        config: expect.objectContaining({
          auto_creation_blob_days: 90,
        }),
      })
    );
  });

  it('renders Event Sync review retention disabled by default and saves days', async () => {
    const user = userEvent.setup();
    render(
      <TaskEditorModal
        task={makeCleanupTask()}
        onClose={() => {}}
        onSaved={() => {}}
      />
    );

    const input = await screen.findByRole('spinbutton', {
      name: /event sync pending review retention/i,
    });
    expect(input).toHaveValue(0);
    expect(input).toHaveAttribute('min', '0');
    expect(input).toHaveAttribute('max', '3650');
    fireEvent.change(input, { target: { value: '45' } });
    await user.click(screen.getByRole('button', { name: /save changes/i }));

    expect(api.updateTask).toHaveBeenCalledWith(
      'cleanup',
      expect.objectContaining({
        config: expect.objectContaining({ event_sync_review_retention_days: 45 }),
      }),
    );
  });
});
