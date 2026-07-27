/**
 * y3m6o.1 review (Finding 4): Task History must render a task that reported
 * success=True but had failures (the PO-chosen "Completed with Warnings"
 * envelope for a channel-pipeline run with a failed action) as an AMBER warning
 * indicator — never solid green. A clean run stays green.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { TaskHistoryPanel } from './TaskHistoryPanel';
import * as api from '../services/api';
import type { TaskExecution } from '../services/api';

function exec(overrides: Partial<TaskExecution> = {}): TaskExecution {
  return {
    id: overrides.id ?? 1,
    task_id: 'auto_creation',
    started_at: '2026-07-21T00:00:00Z',
    completed_at: '2026-07-21T00:00:05Z',
    duration_seconds: 5,
    status: 'completed',
    success: true,
    message: null,
    error: null,
    total_items: 6,
    success_count: 4,
    failed_count: 0,
    skipped_count: 0,
    details: null,
    triggered_by: 'scheduled',
    ...overrides,
  };
}

describe('TaskHistoryPanel — completed-with-warnings honesty (Finding 4)', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders an amber warning indicator when a success run had failures', async () => {
    vi.spyOn(api, 'getTaskHistory').mockResolvedValue({
      history: [exec({ id: 1, status: 'completed', success: true, failed_count: 2 })],
    });

    render(<TaskHistoryPanel taskId="auto_creation" visible />);

    await waitFor(() => {
      expect(screen.getByTestId('status-completed-with-warnings')).toBeInTheDocument();
    });
    expect(screen.getByText(/completed with warnings/i)).toBeInTheDocument();
  });

  it('renders solid green (no warning indicator) for a clean success run', async () => {
    vi.spyOn(api, 'getTaskHistory').mockResolvedValue({
      history: [exec({ id: 2, status: 'completed', success: true, failed_count: 0 })],
    });

    render(<TaskHistoryPanel taskId="auto_creation" visible />);

    await waitFor(() => {
      expect(screen.getByText(/^completed$/i)).toBeInTheDocument();
    });
    expect(screen.queryByTestId('status-completed-with-warnings')).toBeNull();
  });
});
