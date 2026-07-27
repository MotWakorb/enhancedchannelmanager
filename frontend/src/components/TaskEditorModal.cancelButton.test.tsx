/**
 * Unit tests for TaskEditorModal — Cancel button (bead
 * enhancedchannelmanager-09x38.3 audit follow-up). Footer had only the
 * mutating "Save Changes" primary button — header X-close was the only
 * escape hatch. Add a Cancel secondary so the footer follows the documented
 * Cancel+Primary pattern.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { TaskEditorModal } from './TaskEditorModal';
import type { TaskStatus } from '../services/api';
import * as api from '../services/api';

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
    getTaskSchedules: vi.fn().mockResolvedValue({ schedules: [] }),
  };
});

vi.mock('../contexts/NotificationContext', () => ({
  useNotifications: () => ({
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  }),
}));

vi.mock('../contexts/BackupDestinationPromptContext', () => ({
  useBackupDestinationPrompt: () => ({ promptBackupDestination: vi.fn() }),
}));

vi.mock('../services/channelPipelineApi', () => ({
  getChannelPipelineRules: vi.fn().mockResolvedValue([]),
}));

function makeCleanupTask(): TaskStatus {
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
    },
  };
}

describe('TaskEditorModal — Cancel secondary button', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders a Cancel button alongside the primary Save Changes button', async () => {
    render(<TaskEditorModal task={makeCleanupTask()} onClose={() => {}} onSaved={() => {}} />);

    expect(await screen.findByRole('button', { name: /save changes/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
  });

  it('clicking Cancel closes the modal without calling updateTask', async () => {
    const onClose = vi.fn();
    render(<TaskEditorModal task={makeCleanupTask()} onClose={onClose} onSaved={() => {}} />);

    await screen.findByRole('button', { name: /save changes/i });
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(api.updateTask).not.toHaveBeenCalled();
  });
});
