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

  const fetchExecutions = useCallback(async (params?: {
    limit?: number;
    offset?: number;
    status?: string;
  }): Promise<void> => {
    setLoading(true);
    setError(null);
    try {
      const response = await api.getAutoCreationExecutions(params);
      setExecutions(response.executions);
      setTotal(response.total);
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

  const runPipeline = useCallback(async (options?: {
    dryRun?: boolean;
    ruleIds?: number[];
  }): Promise<RunPipelineResponse | undefined> => {
    setIsRunning(true);
    setLoading(true);
    setError(null);
    try {
      const response = await api.runAutoCreationPipeline({
        dryRun: options?.dryRun,
        ruleIds: options?.ruleIds,
      });
      // Refresh executions to include the new one
      await fetchExecutions();
      return response;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to run pipeline');
      return undefined;
    } finally {
      setIsRunning(false);
      setLoading(false);
    }
  }, [fetchExecutions]);

  const rollback = useCallback(async (id: number): Promise<RollbackResponse | undefined> => {
    setLoading(true);
    setError(null);
    try {
      const response = await api.rollbackAutoCreationExecution(id);
      // Refresh executions to update status
      await fetchExecutions();
      return response;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to rollback execution');
      return undefined;
    } finally {
      setLoading(false);
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
