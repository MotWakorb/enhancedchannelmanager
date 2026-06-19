/**
 * TDD tests for useRestoreProgress.
 *
 * The hook polls the generic task status endpoint (`getTask`), maps the real
 * `TaskProgress` payload to view state, stops on a terminal status, and clears
 * its polling timer on unmount (no leaked interval).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { useRestoreProgress } from './useRestoreProgress';
import type { TaskProgress, TaskStatus } from '../services/api';

vi.mock('../services/api', () => ({
  getTask: vi.fn(),
}));

import * as api from '../services/api';

const mockedGetTask = vi.mocked(api.getTask);

/** Build a TaskStatus whose progress carries the fields the hook reads. */
function makeTaskStatus(progress: Partial<TaskProgress>): TaskStatus {
  const fullProgress: TaskProgress = {
    total: 0,
    current: 0,
    percentage: 0,
    status: 'running',
    current_item: '',
    success_count: 0,
    failed_count: 0,
    skipped_count: 0,
    started_at: null,
    ...progress,
  };
  return {
    task_id: 'dbas_restore',
    task_name: 'DBAS Restore',
    task_description: '',
    status: (fullProgress.status as TaskStatus['status']) ?? 'running',
    enabled: true,
    progress: fullProgress,
    schedule: {} as TaskStatus['schedule'],
    schedules: [],
    last_run: null,
    next_run: null,
    config: {},
  };
}

describe('useRestoreProgress', () => {
  beforeEach(() => {
    mockedGetTask.mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('does not poll when taskId is null', () => {
    const { result } = renderHook(() => useRestoreProgress({ taskId: null }));
    expect(mockedGetTask).not.toHaveBeenCalled();
    expect(result.current.isRunning).toBe(false);
    expect(result.current.progress).toBeNull();
  });

  it('maps the progress payload to "Stage N of 13" + per-item counts', async () => {
    mockedGetTask.mockResolvedValue(
      makeTaskStatus({
        status: 'running',
        current_item: 'channel',
        current: 4,
        total: 10,
        percentage: 65,
      })
    );

    const { result } = renderHook(() =>
      useRestoreProgress({ taskId: 'dbas_restore', pollIntervalMs: 5 })
    );

    await waitFor(() => expect(result.current.progress).not.toBeNull());

    expect(result.current.stageNumber).toBe(9); // channel is the 9th stage
    expect(result.current.totalStages).toBe(13);
    expect(result.current.stageLabel).toBe('Channels');
    expect(result.current.itemCurrent).toBe(4);
    expect(result.current.itemTotal).toBe(10);
    expect(result.current.percentage).toBe(65);
    expect(result.current.isRunning).toBe(true);
  });

  it('advances through mocked status responses and stops on terminal status', async () => {
    mockedGetTask
      .mockResolvedValueOnce(makeTaskStatus({ status: 'running', current_item: 'm3u_account' }))
      .mockResolvedValueOnce(makeTaskStatus({ status: 'running', current_item: 'channel' }))
      .mockResolvedValue(makeTaskStatus({ status: 'completed', current_item: 'finalize', percentage: 100 }));

    const { result } = renderHook(() =>
      useRestoreProgress({ taskId: 'dbas_restore', pollIntervalMs: 5 })
    );

    await waitFor(() => expect(result.current.isComplete).toBe(true));

    expect(result.current.status).toBe('completed');
    expect(result.current.isRunning).toBe(false);
    expect(result.current.percentage).toBe(100);

    // Polling stopped: no further calls after terminal.
    const callsAtTerminal = mockedGetTask.mock.calls.length;
    await new Promise((r) => setTimeout(r, 30));
    expect(mockedGetTask.mock.calls.length).toBe(callsAtTerminal);
  });

  it('renders an error state on a failed status (not a frozen spinner)', async () => {
    mockedGetTask.mockResolvedValue(
      makeTaskStatus({ status: 'failed', current_item: 'channel' })
    );

    const { result } = renderHook(() =>
      useRestoreProgress({ taskId: 'dbas_restore', pollIntervalMs: 5 })
    );

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.isRunning).toBe(false);
    expect(result.current.status).toBe('failed');
  });

  it('clears its polling timer on unmount (no leaked interval)', async () => {
    // Keep the task running so the hook would keep polling if not torn down.
    mockedGetTask.mockResolvedValue(
      makeTaskStatus({ status: 'running', current_item: 'channel' })
    );

    const { unmount } = renderHook(() =>
      useRestoreProgress({ taskId: 'dbas_restore', pollIntervalMs: 5 })
    );

    await waitFor(() => expect(mockedGetTask).toHaveBeenCalled());

    act(() => unmount());
    const callsAfterUnmount = mockedGetTask.mock.calls.length;

    // Wait several poll intervals — a leaked timer would fire more calls.
    await new Promise((r) => setTimeout(r, 40));
    expect(mockedGetTask.mock.calls.length).toBe(callsAfterUnmount);
  });

  it('stopPolling() halts further polling', async () => {
    mockedGetTask.mockResolvedValue(
      makeTaskStatus({ status: 'running', current_item: 'channel' })
    );

    const { result } = renderHook(() =>
      useRestoreProgress({ taskId: 'dbas_restore', pollIntervalMs: 5 })
    );

    await waitFor(() => expect(mockedGetTask).toHaveBeenCalled());

    act(() => result.current.stopPolling());
    const callsAfterStop = mockedGetTask.mock.calls.length;

    await new Promise((r) => setTimeout(r, 40));
    expect(mockedGetTask.mock.calls.length).toBe(callsAfterStop);
  });
});
