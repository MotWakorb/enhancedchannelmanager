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

/**
 * Bead enhancedchannelmanager-fexq1. The badge used to INFER the middle state
 * in the browser from `success === true && failed_count > 0`, because the
 * persisted row had no way to say it. That inference cannot see a degraded run
 * with clean counts — a DBAS restore where every row applied and not one
 * channel could play — which is exactly the run the drill found stored as
 * `status: "failed"` while its own alert said "Completed with Warnings".
 *
 * The server now names the severity. The count-based derivation is KEPT as a
 * fallback: history rows written by an earlier build are still in the database
 * and must keep rendering amber.
 */
describe('TaskHistoryPanel — the server names the severity (fexq1)', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders a warning for a degraded run whose counts are clean', async () => {
    vi.spyOn(api, 'getTaskHistory').mockResolvedValue({
      history: [
        exec({
          id: 3,
          task_id: 'dbas_restore',
          status: 'completed_with_warnings',
          success: true,
          failed_count: 0,
          message: '12 channel(s) have NO playable stream',
        }),
      ],
    });

    render(<TaskHistoryPanel taskId="dbas_restore" visible />);

    await waitFor(() => {
      expect(screen.getByTestId('status-completed-with-warnings')).toBeInTheDocument();
    });
    expect(screen.getByText(/completed with warnings/i)).toBeInTheDocument();
    // And it is NOT rendered as a failure.
    expect(screen.queryByText(/^failed$/i)).toBeNull();
  });

  it('still renders a genuine failure as a failure', async () => {
    vi.spyOn(api, 'getTaskHistory').mockResolvedValue({
      history: [
        exec({ id: 4, status: 'failed', success: false, failed_count: 3 }),
      ],
    });

    render(<TaskHistoryPanel taskId="auto_creation" visible />);

    await waitFor(() => {
      expect(screen.getByText(/^failed$/i)).toBeInTheDocument();
    });
    expect(screen.queryByTestId('status-completed-with-warnings')).toBeNull();
  });
});
