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

  // --- The run-start budget (bead dfkbn, review round 5, finding F3) ---------
  //
  // This branch was the riskiest new logic in the change and the only new logic
  // with no test, which is how a defect in it survived a whole review round. It
  // fires when a run is triggered but never begins: the trigger endpoint is
  // fire-and-forget, so the task keeps reporting the PREVIOUS run's finished
  // progress, and publishing that would show the last run's result as this
  // one's. Note every payload in the suite above carries `started_at: null`,
  // which makes the hook accept the first poll immediately — so none of those
  // tests can reach this code at all.

  it('does not publish a terminal state that still carries the previous run started_at', async () => {
    const RUN_1 = '2026-08-05T10:00:00Z';
    mockedGetTask.mockResolvedValue(
      makeTaskStatus({ status: 'completed', started_at: RUN_1 })
    );

    // Run 1 publishes normally and establishes the baseline.
    const { result, rerender } = renderHook(
      ({ runKey }) => useRestoreProgress({ taskId: 'dbas_restore', pollIntervalMs: 2, runKey }),
      { initialProps: { runKey: 1 } }
    );
    await waitFor(() => expect(result.current.isComplete).toBe(true));

    // Run 2 starts, but the task keeps reporting run 1's finished progress.
    rerender({ runKey: 2 });
    // The new run's view is empty IMMEDIATELY, in this very render.
    expect(result.current.isComplete).toBe(false);
    expect(result.current.progress).toBeNull();

    // ...and it stays that way while the stale payload keeps coming back.
    await new Promise((r) => setTimeout(r, 30));
    expect(result.current.isComplete).toBe(false);
    expect(result.current.isError).toBe(false);
  });

  it('gives up after the budget and says the run did not start', async () => {
    const RUN_1 = '2026-08-05T10:00:00Z';
    mockedGetTask.mockResolvedValue(
      makeTaskStatus({ status: 'completed', started_at: RUN_1 })
    );

    const { result, rerender } = renderHook(
      ({ runKey }) => useRestoreProgress({ taskId: 'dbas_restore', pollIntervalMs: 1, runKey }),
      { initialProps: { runKey: 1 } }
    );
    await waitFor(() => expect(result.current.isComplete).toBe(true));

    rerender({ runKey: 2 });

    // The budget expires and the hook says so, rather than replaying run 1.
    await waitFor(() => expect(result.current.isError).toBe(true), { timeout: 3000 });
    expect(result.current.error).toMatch(/did not start/i);
    // THE discriminator a consumer keys on: a synthesised give-up view carries
    // no payload, while a genuine backend terminal state always does.
    expect(result.current.progress).toBeNull();
    expect(result.current.isComplete).toBe(false);
    // It stopped, rather than burning the 30-minute cap.
    const calls = mockedGetTask.mock.calls.length;
    await new Promise((r) => setTimeout(r, 30));
    expect(mockedGetTask.mock.calls.length).toBe(calls);
  });

  it('a GENUINE backend failure keeps its payload, so it is not mistaken for a give-up', async () => {
    mockedGetTask.mockResolvedValue(
      makeTaskStatus({ status: 'failed', started_at: '2026-08-05T10:00:00Z' })
    );

    const { result } = renderHook(() =>
      useRestoreProgress({ taskId: 'dbas_restore', pollIntervalMs: 2, runKey: 1 })
    );

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.progress).not.toBeNull();
    expect(result.current.progress?.status).toBe('failed');
  });

  it('a new run publishes as soon as its own started_at appears', async () => {
    const RUN_1 = '2026-08-05T10:00:00Z';
    const RUN_2 = '2026-08-05T11:00:00Z';
    mockedGetTask.mockResolvedValue(
      makeTaskStatus({ status: 'completed', started_at: RUN_1 })
    );

    const { result, rerender } = renderHook(
      ({ runKey }) => useRestoreProgress({ taskId: 'dbas_restore', pollIntervalMs: 2, runKey }),
      { initialProps: { runKey: 1 } }
    );
    await waitFor(() => expect(result.current.isComplete).toBe(true));

    rerender({ runKey: 2 });
    mockedGetTask.mockResolvedValue(
      makeTaskStatus({ status: 'completed', started_at: RUN_2 })
    );

    await waitFor(() => expect(result.current.isComplete).toBe(true));
    expect(result.current.isError).toBe(false);
    expect(result.current.progress?.started_at).toBe(RUN_2);
  });
});
