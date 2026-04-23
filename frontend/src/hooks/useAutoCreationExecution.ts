/**
 * Hook for managing auto-creation pipeline execution state.
 *
 * Provides run, rollback, and execution history operations.
 */
import { useState, useCallback, useEffect, useRef } from 'react';
import type {
  AutoCreationExecution,
  ExecutionStatus,
  RunPipelineResponse,
  RollbackResponse,
} from '../types/autoCreation';
import * as api from '../services/autoCreationApi';

// ExecutionStatus is referenced both at value-position (the TERMINAL list
// inside pollExecutionUntilTerminal) and at type-position; keeping a single
// type-only import is fine because TS erases the array element type at runtime.

export interface UseAutoCreationExecutionOptions {
  /** Auto-refresh interval in milliseconds (0 to disable) */
  autoRefreshInterval?: number;
}

export interface UseAutoCreationExecutionResult {
  /** List of executions */
  executions: AutoCreationExecution[];
  /** Total number of executions (for pagination) */
  total: number;
  /** Currently selected execution */
  currentExecution: AutoCreationExecution | null;
  /** Loading state */
  loading: boolean;
  /** Error message */
  error: string | null;
  /** Pipeline is currently running */
  isRunning: boolean;
  /** Fetch execution history */
  fetchExecutions: (params?: { limit?: number; offset?: number; status?: string }) => Promise<void>;
  /** Get a single execution by ID */
  getExecution: (id: number) => Promise<AutoCreationExecution | undefined>;
  /** Run the pipeline */
  runPipeline: (options?: { dryRun?: boolean; ruleIds?: number[] }) => Promise<RunPipelineResponse | undefined>;
  /** Rollback an execution */
  rollback: (id: number) => Promise<RollbackResponse | undefined>;
  /** Get the most recent execution */
  getLatestExecution: () => AutoCreationExecution | undefined;
  /** Get executions filtered by status */
  getExecutionsByStatus: (status: ExecutionStatus) => AutoCreationExecution[];
  /** Clear the current execution selection */
  clearCurrentExecution: () => void;
  /** Check if an execution can be rolled back */
  canRollback: (id: number) => boolean;
  /** Get total channels created across all executions */
  getTotalChannelsCreated: () => number;
  /** Get total streams matched across all executions */
  getTotalStreamsMatched: () => number;
  /** Set error manually */
  setError: (error: string | null) => void;
  /** Clear error */
  clearError: () => void;
  /** Start auto-refresh */
  startAutoRefresh: () => void;
  /** Stop auto-refresh */
  stopAutoRefresh: () => void;
}

export function useAutoCreationExecution(
  options: UseAutoCreationExecutionOptions = {}
): UseAutoCreationExecutionResult {
  const { autoRefreshInterval = 0 } = options;

  const [executions, setExecutions] = useState<AutoCreationExecution[]>([]);
  const [total, setTotal] = useState(0);
  const [currentExecution, setCurrentExecution] = useState<AutoCreationExecution | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isRunning, setIsRunning] = useState(false);

  const refreshIntervalRef = useRef<number | null>(null);

  // Track whether we've done the initial fetch
  const hasFetchedRef = useRef(false);

  const fetchExecutions = useCallback(async (params?: {
    limit?: number;
    offset?: number;
    status?: string;
  }): Promise<void> => {
    // Only show loading spinner on initial fetch (no data yet)
    if (!hasFetchedRef.current) {
      setLoading(true);
    }
    setError(null);
    try {
      const response = await api.getAutoCreationExecutions(params);
      setExecutions(response.executions);
      setTotal(response.total);
      hasFetchedRef.current = true;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch executions');
    } finally {
      setLoading(false);
    }
  }, []);

  const getExecution = useCallback(async (id: number): Promise<AutoCreationExecution | undefined> => {
    setLoading(true);
    setError(null);
    try {
      const execution = await api.getAutoCreationExecution(id);
      setCurrentExecution(execution);
      return execution;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch execution');
      return undefined;
    } finally {
      setLoading(false);
    }
  }, []);

  /**
   * Poll the executions endpoint until the run reaches a terminal status
   * (bd-enfsy: 202 + poll background-task pattern). On every poll we also
   * splice the latest snapshot into the executions list so the UI updates
   * incrementally without waiting for the final fetchExecutions() call.
   *
   * Cancellation: callers (or component unmount) can abort the polling by
   * passing an AbortSignal — we honor it both before each fetch and inside
   * the inter-poll sleep.
   */
  const pollExecutionUntilTerminal = useCallback(async (
    executionId: number,
    signal?: AbortSignal,
  ): Promise<AutoCreationExecution | undefined> => {
    // Tunables — small and steady; backend status writes are cheap GETs.
    const POLL_INTERVAL_MS = 1000;
    const MAX_POLL_DURATION_MS = 30 * 60 * 1000; // 30 minutes safety cap
    const TERMINAL: ExecutionStatus[] = ['completed', 'failed', 'rolled_back'];
    const startedAt = Date.now();

    while (true) {
      if (signal?.aborted) return undefined;
      let snapshot: AutoCreationExecution | undefined;
      try {
        snapshot = await api.getAutoCreationExecution(executionId);
      } catch {
        // Transient fetch failure — fall through to retry until cap.
        snapshot = undefined;
      }
      if (snapshot) {
        // Splice into list so the UI sees the row update without a full refetch.
        setExecutions(prev => {
          const idx = prev.findIndex(e => e.id === snapshot!.id);
          if (idx === -1) return [snapshot!, ...prev];
          const next = prev.slice();
          next[idx] = snapshot!;
          return next;
        });
        if (TERMINAL.includes(snapshot.status)) {
          return snapshot;
        }
      }
      if (Date.now() - startedAt > MAX_POLL_DURATION_MS) {
        // Safety break — return the last snapshot we have (status still
        // 'running' / 'pending'); caller can surface a stale-poll warning.
        return snapshot;
      }
      await new Promise<void>((resolve) => {
        const t = window.setTimeout(resolve, POLL_INTERVAL_MS);
        signal?.addEventListener('abort', () => {
          window.clearTimeout(t);
          resolve();
        }, { once: true });
      });
    }
  }, []);

  const runPipeline = useCallback(async (options?: {
    dryRun?: boolean;
    ruleIds?: number[];
  }): Promise<RunPipelineResponse | undefined> => {
    setIsRunning(true);
    setError(null);
    let pipelineError: string | null = null;
    try {
      // bd-enfsy: backend returns 202 with execution_id and runs the pipeline
      // in a supervised background task. Poll until terminal so the caller
      // (and the existing isRunning UI flag) still sees a "done" signal.
      const enqueued = await api.runAutoCreationPipeline({
        dryRun: options?.dryRun,
        ruleIds: options?.ruleIds,
      });
      const terminal = await pollExecutionUntilTerminal(enqueued.execution_id);
      return terminal;
    } catch (err) {
      pipelineError = err instanceof Error ? err.message : 'Failed to run pipeline';
      setError(pipelineError);
      return undefined;
    } finally {
      // Always refetch — both on success and on error — so the UI list is
      // never stale after a POST attempt (bd-enfsy fix: previously only
      // refetched inside the try block, leaving the list stale on 504/500
      // until the user manually reloaded the browser). fetchExecutions
      // internally clears the hook's error state, so re-apply our pipeline
      // error after the refresh succeeds — otherwise the user's visible
      // error message disappears the instant the refetch completes.
      try {
        await fetchExecutions();
      } catch {
        // Ignore: already in finally, don't shadow the original error.
      }
      if (pipelineError) {
        setError(pipelineError);
      }
      setIsRunning(false);
    }
  }, [fetchExecutions, pollExecutionUntilTerminal]);

  const rollback = useCallback(async (id: number): Promise<RollbackResponse | undefined> => {
    setError(null);
    try {
      const response = await api.rollbackAutoCreationExecution(id);
      // Refresh executions to update status
      await fetchExecutions();
      return response;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to rollback execution');
      return undefined;
    }
  }, [fetchExecutions]);

  const getLatestExecution = useCallback((): AutoCreationExecution | undefined => {
    if (executions.length === 0) return undefined;
    return [...executions].sort((a, b) =>
      new Date(b.started_at).getTime() - new Date(a.started_at).getTime()
    )[0];
  }, [executions]);

  const getExecutionsByStatus = useCallback((status: ExecutionStatus): AutoCreationExecution[] => {
    return executions.filter(e => e.status === status);
  }, [executions]);

  const clearCurrentExecution = useCallback(() => {
    setCurrentExecution(null);
  }, []);

  const canRollback = useCallback((id: number): boolean => {
    const execution = executions.find(e => e.id === id);
    if (!execution) return false;
    return (
      execution.mode === 'execute' &&
      execution.status === 'completed'
    );
  }, [executions]);

  const getTotalChannelsCreated = useCallback((): number => {
    return executions.reduce((sum, e) => sum + e.channels_created, 0);
  }, [executions]);

  const getTotalStreamsMatched = useCallback((): number => {
    return executions.reduce((sum, e) => sum + e.streams_matched, 0);
  }, [executions]);

  const clearError = useCallback(() => {
    setError(null);
  }, []);

  const startAutoRefresh = useCallback(() => {
    if (autoRefreshInterval > 0 && !refreshIntervalRef.current) {
      refreshIntervalRef.current = window.setInterval(() => {
        fetchExecutions();
      }, autoRefreshInterval);
    }
  }, [autoRefreshInterval, fetchExecutions]);

  const stopAutoRefresh = useCallback(() => {
    if (refreshIntervalRef.current) {
      window.clearInterval(refreshIntervalRef.current);
      refreshIntervalRef.current = null;
    }
  }, []);

  // Setup auto-refresh
  useEffect(() => {
    if (autoRefreshInterval > 0) {
      startAutoRefresh();
    }
    return () => {
      stopAutoRefresh();
    };
  }, [autoRefreshInterval, startAutoRefresh, stopAutoRefresh]);

  return {
    executions,
    total,
    currentExecution,
    loading,
    error,
    isRunning,
    fetchExecutions,
    getExecution,
    runPipeline,
    rollback,
    getLatestExecution,
    getExecutionsByStatus,
    clearCurrentExecution,
    canRollback,
    getTotalChannelsCreated,
    getTotalStreamsMatched,
    setError,
    clearError,
    startAutoRefresh,
    stopAutoRefresh,
  };
}
