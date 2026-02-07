/**
 * TDD Tests for useAutoCreationExecution hook.
 *
 * These tests define the expected behavior of the hook BEFORE implementation.
 */
import { describe, it, expect, beforeAll, afterAll, afterEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import {
  server,
  mockDataStore,
  resetMockDataStore,
  createMockAutoCreationExecution,
} from '../test/mocks/server';
import { useAutoCreationExecution } from './useAutoCreationExecution';
import type {
  AutoCreationExecution,
  RunPipelineResponse,
  RollbackResponse,
} from '../types/autoCreation';

// Setup MSW server
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers();
  resetMockDataStore();
});
afterAll(() => server.close());

describe('useAutoCreationExecution', () => {
  describe('initial state', () => {
    it('starts with empty executions array', () => {
      const { result } = renderHook(() => useAutoCreationExecution());
      expect(result.current.executions).toEqual([]);
    });

    it('starts with loading false', () => {
      const { result } = renderHook(() => useAutoCreationExecution());
      expect(result.current.loading).toBe(false);
    });

    it('starts with error null', () => {
      const { result } = renderHook(() => useAutoCreationExecution());
      expect(result.current.error).toBeNull();
    });

    it('starts with no current execution', () => {
      const { result } = renderHook(() => useAutoCreationExecution());
      expect(result.current.currentExecution).toBeNull();
    });

    it('starts with isRunning false', () => {
      const { result } = renderHook(() => useAutoCreationExecution());
      expect(result.current.isRunning).toBe(false);
    });
  });

  describe('fetchExecutions', () => {
    it('fetches execution history from API', async () => {
      const exec1 = createMockAutoCreationExecution({ status: 'completed' });
      const exec2 = createMockAutoCreationExecution({ status: 'completed' });
      mockDataStore.autoCreationExecutions.push(exec1, exec2);

      const { result } = renderHook(() => useAutoCreationExecution());

      await act(async () => {
        await result.current.fetchExecutions();
      });

      expect(result.current.executions).toHaveLength(2);
    });

    it('supports pagination parameters', async () => {
      // Add many executions
      for (let i = 0; i < 10; i++) {
        mockDataStore.autoCreationExecutions.push(createMockAutoCreationExecution());
      }

      const { result } = renderHook(() => useAutoCreationExecution());

      await act(async () => {
        await result.current.fetchExecutions({ limit: 5, offset: 0 });
      });

      expect(result.current.executions.length).toBeLessThanOrEqual(5);
      expect(result.current.total).toBe(10);
    });

    it('supports status filter', async () => {
      mockDataStore.autoCreationExecutions.push(
        createMockAutoCreationExecution({ status: 'completed' }),
        createMockAutoCreationExecution({ status: 'failed' }),
        createMockAutoCreationExecution({ status: 'completed' })
      );

      const { result } = renderHook(() => useAutoCreationExecution());

      await act(async () => {
        await result.current.fetchExecutions({ status: 'completed' });
      });

      expect(result.current.executions.every(e => e.status === 'completed')).toBe(true);
    });

    it('sets loading state during fetch', async () => {
      const { result } = renderHook(() => useAutoCreationExecution());

      await act(async () => {
        await result.current.fetchExecutions();
      });

      // After fetch completes, loading should be false
      expect(result.current.loading).toBe(false);
    });
  });

  describe('getExecution', () => {
    it('fetches a single execution by ID', async () => {
      const execution = createMockAutoCreationExecution({ status: 'completed' });
      mockDataStore.autoCreationExecutions.push(execution);

      const { result } = renderHook(() => useAutoCreationExecution());

      let fetched: AutoCreationExecution | undefined;
      await act(async () => {
        fetched = await result.current.getExecution(execution.id);
      });

      expect(fetched).toBeDefined();
      expect(fetched!.id).toBe(execution.id);
    });

    it('returns undefined for non-existent execution', async () => {
      const { result } = renderHook(() => useAutoCreationExecution());

      let fetched: AutoCreationExecution | undefined;
      await act(async () => {
        fetched = await result.current.getExecution(99999);
      });

      expect(fetched).toBeUndefined();
      expect(result.current.error).toBeTruthy();
    });

    it('sets currentExecution when fetched', async () => {
      const execution = createMockAutoCreationExecution({ status: 'completed' });
      mockDataStore.autoCreationExecutions.push(execution);

      const { result } = renderHook(() => useAutoCreationExecution());

      await act(async () => {
        await result.current.getExecution(execution.id);
      });

      expect(result.current.currentExecution).toBeDefined();
      expect(result.current.currentExecution!.id).toBe(execution.id);
    });
  });

  describe('runPipeline', () => {
    it('runs the pipeline in execute mode', async () => {
      const { result } = renderHook(() => useAutoCreationExecution());

      let response: RunPipelineResponse | undefined;
      await act(async () => {
        response = await result.current.runPipeline({ dryRun: false });
      });

      expect(response).toBeDefined();
      expect(response!.success).toBe(true);
      expect(response!.mode).toBe('execute');
    });

    it('runs the pipeline in dry-run mode', async () => {
      const { result } = renderHook(() => useAutoCreationExecution());

      let response: RunPipelineResponse | undefined;
      await act(async () => {
        response = await result.current.runPipeline({ dryRun: true });
      });

      expect(response).toBeDefined();
      expect(response!.success).toBe(true);
      expect(response!.mode).toBe('dry_run');
      expect(response!.dry_run_results).toBeDefined();
    });

    it('supports filtering by rule IDs', async () => {
      const { result } = renderHook(() => useAutoCreationExecution());

      let response: RunPipelineResponse | undefined;
      await act(async () => {
        response = await result.current.runPipeline({
          dryRun: false,
          ruleIds: [1, 2, 3],
        });
      });

      expect(response).toBeDefined();
      expect(response!.success).toBe(true);
    });

    it('sets isRunning true during execution', async () => {
      const { result } = renderHook(() => useAutoCreationExecution());

      await act(async () => {
        await result.current.runPipeline({ dryRun: false });
      });

      // After pipeline completes, isRunning should be false
      expect(result.current.isRunning).toBe(false);
    });

    it('adds new execution to list after run', async () => {
      const { result } = renderHook(() => useAutoCreationExecution());

      await act(async () => {
        await result.current.runPipeline({ dryRun: false });
      });

      // Fetch to see the new execution
      await act(async () => {
        await result.current.fetchExecutions();
      });

      expect(result.current.executions.length).toBeGreaterThan(0);
    });

    it('returns execution stats', async () => {
      const { result } = renderHook(() => useAutoCreationExecution());

      let response: RunPipelineResponse | undefined;
      await act(async () => {
        response = await result.current.runPipeline({ dryRun: false });
      });

      expect(response!.streams_evaluated).toBeDefined();
      expect(response!.streams_matched).toBeDefined();
      expect(response!.channels_created).toBeDefined();
      expect(response!.channels_updated).toBeDefined();
      expect(response!.groups_created).toBeDefined();
    });
  });

  describe('rollback', () => {
    it('rolls back an execution', async () => {
      const execution = createMockAutoCreationExecution({
        status: 'completed',
        mode: 'execute',
      });
      mockDataStore.autoCreationExecutions.push(execution);

      const { result } = renderHook(() => useAutoCreationExecution());

      let response: RollbackResponse | undefined;
      await act(async () => {
        response = await result.current.rollback(execution.id);
      });

      expect(response).toBeDefined();
      expect(response!.success).toBe(true);
      expect(response!.entities_removed).toBeDefined();
      expect(response!.entities_restored).toBeDefined();
    });

    it('fails to rollback dry-run execution', async () => {
      const execution = createMockAutoCreationExecution({
        status: 'completed',
        mode: 'dry_run',
      });
      mockDataStore.autoCreationExecutions.push(execution);

      const { result } = renderHook(() => useAutoCreationExecution());

      let response: RollbackResponse | undefined;
      await act(async () => {
        response = await result.current.rollback(execution.id);
      });

      // Hook catches the API error and returns undefined
      expect(response).toBeUndefined();
      expect(result.current.error).toBeTruthy();
    });

    it('fails to rollback already rolled back execution', async () => {
      const execution = createMockAutoCreationExecution({
        status: 'rolled_back',
        mode: 'execute',
      });
      mockDataStore.autoCreationExecutions.push(execution);

      const { result } = renderHook(() => useAutoCreationExecution());

      let response: RollbackResponse | undefined;
      await act(async () => {
        response = await result.current.rollback(execution.id);
      });

      // Hook catches the API error and returns undefined
      expect(response).toBeUndefined();
      expect(result.current.error).toBeTruthy();
    });

    it('updates execution status after rollback', async () => {
      const execution = createMockAutoCreationExecution({
        status: 'completed',
        mode: 'execute',
      });
      mockDataStore.autoCreationExecutions.push(execution);

      const { result } = renderHook(() => useAutoCreationExecution());

      await act(async () => {
        await result.current.fetchExecutions();
      });

      await act(async () => {
        await result.current.rollback(execution.id);
      });

      // Refetch to see updated status
      await act(async () => {
        await result.current.fetchExecutions();
      });

      const updated = result.current.executions.find(e => e.id === execution.id);
      expect(updated?.status).toBe('rolled_back');
    });
  });

  describe('getLatestExecution', () => {
    it('returns the most recent execution', async () => {
      const older = createMockAutoCreationExecution({
        started_at: '2024-01-01T00:00:00Z',
      });
      const newer = createMockAutoCreationExecution({
        started_at: '2024-01-02T00:00:00Z',
      });
      mockDataStore.autoCreationExecutions.push(older, newer);

      const { result } = renderHook(() => useAutoCreationExecution());

      await act(async () => {
        await result.current.fetchExecutions();
      });

      const latest = result.current.getLatestExecution();
      expect(latest).toBeDefined();
      // Should be the one with later timestamp
      expect(new Date(latest!.started_at).getTime()).toBeGreaterThanOrEqual(
        new Date(older.started_at).getTime()
      );
    });

    it('returns undefined when no executions', () => {
      const { result } = renderHook(() => useAutoCreationExecution());

      const latest = result.current.getLatestExecution();
      expect(latest).toBeUndefined();
    });
  });

  describe('getExecutionsByStatus', () => {
    it('filters executions by status', async () => {
      mockDataStore.autoCreationExecutions.push(
        createMockAutoCreationExecution({ status: 'completed' }),
        createMockAutoCreationExecution({ status: 'failed' }),
        createMockAutoCreationExecution({ status: 'completed' }),
        createMockAutoCreationExecution({ status: 'rolled_back' })
      );

      const { result } = renderHook(() => useAutoCreationExecution());

      await act(async () => {
        await result.current.fetchExecutions();
      });

      const completed = result.current.getExecutionsByStatus('completed');
      expect(completed).toHaveLength(2);

      const failed = result.current.getExecutionsByStatus('failed');
      expect(failed).toHaveLength(1);

      const rolledBack = result.current.getExecutionsByStatus('rolled_back');
      expect(rolledBack).toHaveLength(1);
    });
  });

  describe('clearCurrentExecution', () => {
    it('clears the current execution', async () => {
      const execution = createMockAutoCreationExecution();
      mockDataStore.autoCreationExecutions.push(execution);

      const { result } = renderHook(() => useAutoCreationExecution());

      await act(async () => {
        await result.current.getExecution(execution.id);
      });

      expect(result.current.currentExecution).toBeDefined();

      act(() => {
        result.current.clearCurrentExecution();
      });

      expect(result.current.currentExecution).toBeNull();
    });
  });

  describe('error handling', () => {
    it('provides setError for manual error setting', () => {
      const { result } = renderHook(() => useAutoCreationExecution());

      act(() => {
        result.current.setError('Manual error');
      });

      expect(result.current.error).toBe('Manual error');
    });

    it('provides clearError to clear errors', () => {
      const { result } = renderHook(() => useAutoCreationExecution());

      act(() => {
        result.current.setError('Some error');
      });

      act(() => {
        result.current.clearError();
      });

      expect(result.current.error).toBeNull();
    });

    it('handles API errors gracefully', async () => {
      server.use(
        http.post('/api/auto-creation/run', () => {
          return new HttpResponse(
            JSON.stringify({ detail: 'Pipeline failed' }),
            { status: 500 }
          );
        })
      );

      const { result } = renderHook(() => useAutoCreationExecution());

      let response: RunPipelineResponse | undefined;
      await act(async () => {
        response = await result.current.runPipeline({ dryRun: false });
      });

      expect(response).toBeUndefined();
      expect(result.current.error).toBeTruthy();
    });
  });

  describe('execution stats helpers', () => {
    it('calculates total channels created across executions', async () => {
      mockDataStore.autoCreationExecutions.push(
        createMockAutoCreationExecution({ channels_created: 5 }),
        createMockAutoCreationExecution({ channels_created: 3 }),
        createMockAutoCreationExecution({ channels_created: 7 })
      );

      const { result } = renderHook(() => useAutoCreationExecution());

      await act(async () => {
        await result.current.fetchExecutions();
      });

      const totalCreated = result.current.getTotalChannelsCreated();
      expect(totalCreated).toBe(15);
    });

    it('calculates total streams matched across executions', async () => {
      mockDataStore.autoCreationExecutions.push(
        createMockAutoCreationExecution({ streams_matched: 10 }),
        createMockAutoCreationExecution({ streams_matched: 20 })
      );

      const { result } = renderHook(() => useAutoCreationExecution());

      await act(async () => {
        await result.current.fetchExecutions();
      });

      const totalMatched = result.current.getTotalStreamsMatched();
      expect(totalMatched).toBe(30);
    });
  });

  describe('canRollback', () => {
    it('returns true for completed execute mode execution', async () => {
      const execution = createMockAutoCreationExecution({
        status: 'completed',
        mode: 'execute',
      });
      mockDataStore.autoCreationExecutions.push(execution);

      const { result } = renderHook(() => useAutoCreationExecution());

      await act(async () => {
        await result.current.fetchExecutions();
      });

      expect(result.current.canRollback(execution.id)).toBe(true);
    });

    it('returns false for dry_run execution', async () => {
      const execution = createMockAutoCreationExecution({
        status: 'completed',
        mode: 'dry_run',
      });
      mockDataStore.autoCreationExecutions.push(execution);

      const { result } = renderHook(() => useAutoCreationExecution());

      await act(async () => {
        await result.current.fetchExecutions();
      });

      expect(result.current.canRollback(execution.id)).toBe(false);
    });

    it('returns false for already rolled back execution', async () => {
      const execution = createMockAutoCreationExecution({
        status: 'rolled_back',
        mode: 'execute',
      });
      mockDataStore.autoCreationExecutions.push(execution);

      const { result } = renderHook(() => useAutoCreationExecution());

      await act(async () => {
        await result.current.fetchExecutions();
      });

      expect(result.current.canRollback(execution.id)).toBe(false);
    });

    it('returns false for running execution', async () => {
      const execution = createMockAutoCreationExecution({
        status: 'running',
        mode: 'execute',
      });
      mockDataStore.autoCreationExecutions.push(execution);

      const { result } = renderHook(() => useAutoCreationExecution());

      await act(async () => {
        await result.current.fetchExecutions();
      });

      expect(result.current.canRollback(execution.id)).toBe(false);
    });
  });

  describe('autoRefresh option', () => {
    it('supports auto-refresh of execution list', async () => {
      const { result } = renderHook(() =>
        useAutoCreationExecution({ autoRefreshInterval: 1000 })
      );

      // Should have the option available
      expect(result.current.stopAutoRefresh).toBeDefined();
      expect(result.current.startAutoRefresh).toBeDefined();
    });
  });
});
