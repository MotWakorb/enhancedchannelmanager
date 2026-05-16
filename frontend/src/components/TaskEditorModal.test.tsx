/**
 * Unit tests for TaskEditorModal — bd-ia28g retention fields.
 *
 * Locks the three new retention inputs added to the Database Cleanup
 * task config UI:
 *
 * 1. Auto-creation execution BLOB retention (days) — default 30
 * 2. Health checks retention (days) — default 7
 * 3. Notifications retention (days) — default 30
 *
 * Per bd-p5b8i DBA spike re-attribution, these are the actual large tables
 * (77% / 14% / 1.3% of operator DB respectively). The backend prune blocks
 * live in backend/tasks/cleanup.py; this UI lets operators tune the
 * retention windows from Settings → Tasks → Database Cleanup.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
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

// autoCreationApi is imported but only called when the task references
// auto_creation_rules in its parameter schema — our mock returns empty,
// so loaders.has('auto_creation_rules') is false. Still stub to be safe.
vi.mock('../services/autoCreationApi', () => ({
  getAutoCreationRules: vi.fn().mockResolvedValue([]),
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
      vacuum_db: true,
      ...configOverrides,
    },
  };
}

describe('TaskEditorModal — bd-ia28g retention fields', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the auto-creation BLOB retention input with the config value', async () => {
    render(
      <TaskEditorModal
        task={makeCleanupTask({ auto_creation_blob_days: 45 })}
        onClose={() => {}}
        onSaved={() => {}}
      />
    );

    const label = await screen.findByText(/auto-creation execution blob retention/i);
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

    const blobLabel = await screen.findByText(/auto-creation execution blob retention/i);
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
});
