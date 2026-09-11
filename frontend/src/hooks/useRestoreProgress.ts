/**
 * Polling hook for a DBAS Phase-2 restore / dry-run progress surface.
 *
 * Mirrors the poll / abort / cleanup conventions of
 * `useChannelPipelineExecution.ts` (`pollExecutionUntilTerminal`): a steady
 * interval of cheap GETs against the task status endpoint, stop on a terminal
 * status, honor an AbortSignal both before each fetch and inside the inter-poll
 * sleep, and clear the interval on unmount so no timer leaks (there is a known
 * dnd/observer-leak discipline in this repo — the same applies to polling
 * timers).
 *
 * Both restore triggers return a server-issued run_id. Every progress response
 * must carry that identity; a timestamp or a fresh mount cannot establish it.
 * DbasRestoreCorrelation.test.tsx exercises the actual consumer seam.
 */
import { useState, useCallback, useEffect, useRef } from 'react';
import * as api from '../services/api';
import type { TaskProgress } from '../services/api';
import {
  TOTAL_RESTORE_STAGES,
  stageNumberFromCurrentItem,
  stageLabelFromCurrentItem,
} from '../utils/restoreStages';
import { isTerminalExecutionStatus } from '../utils/taskExecutionStatus';

/** Default poll cadence — small and steady; status reads are cheap GETs. */
const DEFAULT_POLL_INTERVAL_MS = 1000;

/** Safety cap so a stuck task never polls forever (matches channel pipeline hook). */
const MAX_POLL_DURATION_MS = 30 * 60 * 1000;

/**
 * How many polls to wait for a NEW run to appear before giving up on it.
 *
 * The restore trigger endpoint is fire-and-forget (`asyncio.create_task`), so
 * for a short window after it returns 200 the task still reports the PREVIOUS
 * run's progress. Publishing that would be publishing a stale terminal state.
 * If no new run shows up within this many polls, something stopped it from
 * starting at all (an ALREADY_RUNNING rejection, say), and saying so is far
 * better than rendering the last run's report as if it were this one's.
 */
const MAX_RUN_START_POLLS = 20;

export interface UseRestoreProgressOptions {
  /**
   * Task id to poll. `null` disables polling (no task running yet).
   */
  taskId: string | null;
  /** Server-issued identity returned by the successful restore trigger. */
  runId?: string | null;
  /** Poll cadence in ms (default 1000). */
  pollIntervalMs?: number;
  /**
   * Monotonic token identifying WHICH run is being watched. Bump it once per
   * started run.
   *
   * Required because `taskId` cannot do this job: every DBAS restore run uses
   * the same constant task id (`"dbas_restore"`), so a second run in one modal
   * session left the poll effect's deps unchanged, the loop never restarted,
   * and the previous run's terminal view stayed live. A consumer that finalizes
   * on `isComplete` then finalized instantly against the OLD run's result — in
   * the field that rendered a `preserve` preview while the operator had just
   * selected `overwrite`, immediately above an enabled Apply button.
   *
   * Leave it at its default when a surface only ever watches one run.
   */
  runKey?: number;
}

/** Normalized view state the progress component renders. */
export interface RestoreProgressView {
  /** Raw backend progress payload, or null before the first successful poll. */
  progress: TaskProgress | null;
  /** 1-based current stage ("Stage N of M"). */
  stageNumber: number;
  /** Total stages in the canonical sequence. */
  totalStages: number;
  /** Operator-facing label of the current stage. */
  stageLabel: string;
  /** Current item index WITHIN the active stage (from `progress.current`). */
  itemCurrent: number;
  /** Total items in the active stage (from `progress.total`). */
  itemTotal: number;
  /** Overall completion percentage (0-100) from the backend payload. */
  percentage: number;
  /** Raw task status string. */
  status: string;
  /** Whether the job is still running (not terminal, taskId present). */
  isRunning: boolean;
  /** Whether the job reached a failed/cancelled terminal status. */
  isError: boolean;
  /** Whether the job ran to completion (cleanly, or with warnings). */
  isComplete: boolean;
  /** Polling/fetch error message, if any (transient errors are not surfaced). */
  error: string | null;
}

const EMPTY_VIEW: RestoreProgressView = {
  progress: null,
  stageNumber: 1,
  totalStages: TOTAL_RESTORE_STAGES,
  stageLabel: stageLabelFromCurrentItem(null),
  itemCurrent: 0,
  itemTotal: 0,
  percentage: 0,
  status: 'idle',
  isRunning: false,
  isError: false,
  isComplete: false,
  error: null,
};

function viewFromProgress(progress: TaskProgress): RestoreProgressView {
  const status = progress.status;
  const isError = status === 'failed' || status === 'cancelled';
  // `completed_with_warnings` is a run that FINISHED and rolled nothing back —
  // a restore whose report is exactly as readable as a clean one's (bead bdmby;
  // the same rule `isTerminalExecutionStatus` already applies to the history row
  // both restore modals poll). Treating it as an error here would put the red
  // "Restore failed" header over a restore that succeeded with warnings.
  const isComplete = status === 'completed' || status === 'completed_with_warnings';
  return {
    progress,
    stageNumber: stageNumberFromCurrentItem(progress.current_item),
    totalStages: TOTAL_RESTORE_STAGES,
    stageLabel: stageLabelFromCurrentItem(progress.current_item),
    itemCurrent: progress.current,
    itemTotal: progress.total,
    percentage: progress.percentage,
    status,
    isRunning: !isTerminalExecutionStatus(status),
    isError,
    isComplete,
    error: null,
  };
}

export interface UseRestoreProgressResult extends RestoreProgressView {
  /** Force-stop polling (e.g. user dismissed the surface after completion). */
  stopPolling: () => void;
}

/**
 * Poll a restore/dry-run task's status until it reaches a terminal state.
 *
 * Returns a normalized {@link RestoreProgressView} that the shared
 * `RestoreProgress` component renders for both restore and dry-run modes.
 */
export function useRestoreProgress(
  options: UseRestoreProgressOptions
): UseRestoreProgressResult {
  const { taskId, runId, pollIntervalMs = DEFAULT_POLL_INTERVAL_MS, runKey = 0 } = options;

  // The view is stored WITH the run it belongs to, so a view can never outlive
  // its run.
  const [tracked, setTracked] = useState<{ runKey: number; runId?: string | null; view: RestoreProgressView }>({
    runKey,
    runId,
    view: EMPTY_VIEW,
  });

  // Invalidation happens HERE, during render, not in an effect. Effects run
  // after the commit, so a consumer whose own effect reads `isComplete` in the
  // same pass would still see the previous run's `true` and act on it — which
  // is exactly the bug this exists to close. Deriving it makes the new run's
  // empty view visible in the very render where `runKey` changed.
  const view = tracked.runKey === runKey && tracked.runId === runId ? tracked.view : EMPTY_VIEW;

  // Holds the live abort controller so stopPolling() can tear down the loop.
  const abortRef = useRef<AbortController | null>(null);

  const stopPolling = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  useEffect(() => {
    if (!taskId) {
      setTracked({ runKey, runId, view: EMPTY_VIEW });
      return;
    }

    const controller = new AbortController();
    abortRef.current = controller;
    const { signal } = controller;
    const startedAt = Date.now();

    // Check EVERY response, including after a matching running payload. A later
    // trigger can replace the singleton while this consumer is still polling.
    let pollsAwaitingRun = 0;
    let hasSeenRun = false;

    // Async setState fires inside this poll loop (not synchronously in the
    // effect body), so the set-state-in-effect lint rule does not apply.
    const poll = async (): Promise<void> => {
      while (!signal.aborted) {
        try {
          const taskStatus = await api.getTask(taskId);
          if (signal.aborted) return;
          const progress = taskStatus.progress;

          const matchesRun = !!runId && progress.run_id === runId;
          if (!matchesRun) {
            if (!hasSeenRun && ++pollsAwaitingRun >= MAX_RUN_START_POLLS) {
              // Say nothing started rather than replay the last run's result.
              setTracked({
                runKey,
                runId,
                view: {
                  ...EMPTY_VIEW,
                  status: 'failed',
                  isError: true,
                  error: 'The restore did not start. It may already be running — check Task History.',
                },
              });
              return;
            }
          }

          if (matchesRun) {
            hasSeenRun = true;
            const nextView = viewFromProgress(progress);
            setTracked({ runKey, runId, view: nextView });
            if (!nextView.isRunning) {
              // Terminal — stop polling, leave the final view in place.
              return;
            }
          }
        } catch (err) {
          if (signal.aborted) return;
          // Transient fetch failure — surface the message but keep polling
          // until the safety cap, mirroring the channel pipeline hook's retry.
          const message =
            err instanceof Error ? err.message : 'Failed to fetch restore progress';
          setTracked((prev) =>
            prev.runKey === runKey && prev.runId === runId
              ? { runKey, runId, view: { ...prev.view, error: message } }
              : prev
          );
        }

        if (Date.now() - startedAt > MAX_POLL_DURATION_MS) return;

        await new Promise<void>((resolve) => {
          const timer = window.setTimeout(resolve, pollIntervalMs);
          signal.addEventListener(
            'abort',
            () => {
              window.clearTimeout(timer);
              resolve();
            },
            { once: true }
          );
        });
      }
    };

    void poll();

    return () => {
      controller.abort();
      if (abortRef.current === controller) {
        abortRef.current = null;
      }
    };
  }, [taskId, runId, pollIntervalMs, runKey]);

  return { ...view, stopPolling };
}
