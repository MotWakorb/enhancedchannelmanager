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
 * SEAM: the restore-trigger endpoint / restore TaskScheduler is a LATER bead.
 * The backend orchestrator (`backend/dbas/restore_orchestrator.py`, bead .18)
 * exists but does not yet emit `_set_progress`, and no restore task is wired to
 * `task_scheduler`. This hook therefore takes the `taskId` as a prop/seam and
 * polls the EXISTING generic task status endpoint (`getTask` →
 * `/api/tasks/{id}`), reading the REAL `TaskProgress` shape
 * (`backend/task_scheduler.py` — `TaskProgress.to_dict()`). When the restore
 * task lands and emits its stage via `current_item`, this hook needs no change.
 */
import { useState, useCallback, useEffect, useRef } from 'react';
import * as api from '../services/api';
import type { TaskProgress } from '../services/api';
import {
  TOTAL_RESTORE_STAGES,
  stageNumberFromCurrentItem,
  stageLabelFromCurrentItem,
} from '../utils/restoreStages';

/** Task statuses that end polling — the job is done, no further updates come. */
const TERMINAL_STATUSES: ReadonlySet<string> = new Set([
  'completed',
  'failed',
  'cancelled',
]);

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
   * Task id to poll. `null` disables polling (no task running yet) — the seam
   * for the not-yet-wired restore-trigger endpoint.
   */
  taskId: string | null;
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
  /** Whether the job completed successfully. */
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
  const isComplete = status === 'completed';
  return {
    progress,
    stageNumber: stageNumberFromCurrentItem(progress.current_item),
    totalStages: TOTAL_RESTORE_STAGES,
    stageLabel: stageLabelFromCurrentItem(progress.current_item),
    itemCurrent: progress.current,
    itemTotal: progress.total,
    percentage: progress.percentage,
    status,
    isRunning: !TERMINAL_STATUSES.has(status),
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
  const { taskId, pollIntervalMs = DEFAULT_POLL_INTERVAL_MS, runKey = 0 } = options;

  // The view is stored WITH the run it belongs to, so a view can never outlive
  // its run.
  const [tracked, setTracked] = useState<{ runKey: number; view: RestoreProgressView }>({
    runKey,
    view: EMPTY_VIEW,
  });

  // Invalidation happens HERE, during render, not in an effect. Effects run
  // after the commit, so a consumer whose own effect reads `isComplete` in the
  // same pass would still see the previous run's `true` and act on it — which
  // is exactly the bug this exists to close. Deriving it makes the new run's
  // empty view visible in the very render where `runKey` changed.
  const view = tracked.runKey === runKey ? tracked.view : EMPTY_VIEW;

  // Holds the live abort controller so stopPolling() can tear down the loop.
  const abortRef = useRef<AbortController | null>(null);
  // `started_at` of the last progress payload this hook PUBLISHED. It is the
  // discriminator between "this run" and "the run before it": the scheduler
  // stamps a fresh one in its synchronous prologue, before the task body runs.
  const publishedStartedAtRef = useRef<string | null>(null);

  const stopPolling = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  useEffect(() => {
    if (!taskId) {
      setTracked({ runKey, view: EMPTY_VIEW });
      return;
    }

    const controller = new AbortController();
    abortRef.current = controller;
    const { signal } = controller;
    const startedAt = Date.now();

    // The run this loop is watching must be a DIFFERENT run from the last one
    // published. Until that is established, every payload belongs to the
    // previous run and must not be shown. With nothing published yet there is
    // no previous run to confuse this one with.
    const previousRunStartedAt = publishedStartedAtRef.current;
    let confirmedNewRun = previousRunStartedAt === null;
    let pollsAwaitingRun = 0;

    // Async setState fires inside this poll loop (not synchronously in the
    // effect body), so the set-state-in-effect lint rule does not apply.
    const poll = async (): Promise<void> => {
      while (!signal.aborted) {
        try {
          const taskStatus = await api.getTask(taskId);
          if (signal.aborted) return;
          const progress = taskStatus.progress;

          if (!confirmedNewRun) {
            if (progress.started_at && progress.started_at !== previousRunStartedAt) {
              confirmedNewRun = true;
            } else if (++pollsAwaitingRun >= MAX_RUN_START_POLLS) {
              // Say nothing started rather than replay the last run's result.
              setTracked({
                runKey,
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

          if (confirmedNewRun) {
            publishedStartedAtRef.current = progress.started_at;
            const nextView = viewFromProgress(progress);
            setTracked({ runKey, view: nextView });
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
            prev.runKey === runKey
              ? { runKey, view: { ...prev.view, error: message } }
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
  }, [taskId, pollIntervalMs, runKey]);

  return { ...view, stopPolling };
}
