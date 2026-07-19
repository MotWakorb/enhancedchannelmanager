/**
 * Unit tests for TaskEditorModal — Journal Noise Purge config section
 * (bead enhancedchannelmanager-uliyr). The PO-decided auto-purge policy
 * (two automated-noise journal buckets, 3-day default retention) must be
 * settings-visible: the task editor shows the retention-days input and the
 * two category toggles, and saves them back through the task config.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
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

function makeJournalNoisePurgeTask(): TaskStatus {
  return {
    task_id: 'journal_noise_purge',
    task_name: 'Journal Noise Purge',
    task_description: 'Purge automated-noise journal entries',
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
    schedule: { schedule_type: 'cron', cron_expression: '30 3 * * *' } as unknown as TaskStatus['schedule'],
    schedules: [],
    last_run: null,
    next_run: null,
    config: {
      retention_days: 3,
      purge_watch_events: true,
      purge_pipeline_rule_pairs: true,
    },
  };
}

describe('TaskEditorModal — Journal Noise Purge config section', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the retention input with the PO-decided 3-day default', async () => {
    render(
      <TaskEditorModal task={makeJournalNoisePurgeTask()} onClose={() => {}} onSaved={() => {}} />,
    );

    const input = await screen.findByLabelText(/noise retention \(days\)/i);
    expect(input).toHaveValue(3);
  });

  it('lists both automated-noise categories as visible toggles, checked by default', async () => {
    render(
      <TaskEditorModal task={makeJournalNoisePurgeTask()} onClose={() => {}} onSaved={() => {}} />,
    );

    expect(
      await screen.findByRole('checkbox', { name: /watch start\/stop events/i }),
    ).toBeChecked();
    expect(
      screen.getByRole('checkbox', { name: /channel pipeline rule create\/delete entries/i }),
    ).toBeChecked();
  });

  it('states that all other journal categories are untouched', async () => {
    render(
      <TaskEditorModal task={makeJournalNoisePurgeTask()} onClose={() => {}} onSaved={() => {}} />,
    );

    expect(
      await screen.findByText(/all other journal categories\s+are untouched/i),
    ).toBeInTheDocument();
  });

  it('states that operator-initiated rule create/delete entries are kept', async () => {
    render(
      <TaskEditorModal task={makeJournalNoisePurgeTask()} onClose={() => {}} onSaved={() => {}} />,
    );

    expect(
      await screen.findByText(/operator-initiated rule create\/delete entries are kept/i),
    ).toBeInTheDocument();
  });

  it('saves an edited retention window through the task config', async () => {
    render(
      <TaskEditorModal task={makeJournalNoisePurgeTask()} onClose={() => {}} onSaved={() => {}} />,
    );

    const input = await screen.findByLabelText(/noise retention \(days\)/i);
    fireEvent.change(input, { target: { value: '7' } });
    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => expect(api.updateTask).toHaveBeenCalled());
    const [taskId, config] = vi.mocked(api.updateTask).mock.calls[0];
    expect(taskId).toBe('journal_noise_purge');
    expect(config.config).toMatchObject({
      retention_days: 7,
      purge_watch_events: true,
      purge_pipeline_rule_pairs: true,
    });
  });
});
