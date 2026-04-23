/**
 * TDD Tests for useAutoCreationExecution hook.
 *
 * These tests define the expected behavior of the hook BEFORE implementation.
 */
import { describe, it, expect, beforeAll, afterAll, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
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
    // bd-enfsy: contract is now POST returns 202 + execution_id and the hook
    // polls GET /executions/{id} until terminal. The hook resolves with the
    // terminal AutoCreationExecution row (or undefined on error/timeout).
    it('runs the pipeline in execute mode and resolves to terminal execution', async () => {
      const { result } = renderHook(() => useAutoCreationExecution());

      let response: RunPipelineResponse | undefined;
      await act(async () => {
        response = await result.current.runPipeline({ dryRun: false });
      });

      expect(response).toBeDefined();
      expect(response!.status).toBe('completed');
      expect(response!.mode).toBe('execute');
    });

    it('runs the pipeline in dry-run mode', async () => {
      const { result } = renderHook(() => useAutoCreationExecution());

      let response: RunPipelineResponse | undefined;
      await act(async () => {
        response = await result.current.runPipeline({ dryRun: true });
      });

      expect(response).toBeDefined();
      expect(response!.status).toBe('completed');
      expect(response!.mode).toBe('dry_run');
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
      expect(response!.status).toBe('completed');
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

      // bd-enfsy: the hook now refetches in finally — no need for a separate
      // fetchExecutions() call. The list should contain the new execution.
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

    it('refetches executions in finally block on POST error (bd-enfsy)', async () => {
      // The 202 enqueue itself fails — list should still be refetched so the
      // user sees fresh state without needing to reload the browser.
      mockDataStore.autoCreationExecutions.push(
        createMockAutoCreationExecution({ status: 'completed' })
      );
      server.use(
        http.post('/api/auto-creation/run', () => {
          return new HttpResponse(
            JSON.stringify({ detail: 'enqueue exploded' }),
            { status: 500 }
          );
        })
      );

      const { result } = renderHook(() => useAutoCreationExecution());

      let response: RunPipelineResponse | undefined;
      await act(async () => {
        response = await result.current.runPipeline({ dryRun: false });
      });

      // Hook surfaces the error and returns undefined…
      expect(response).toBeUndefined();
      expect(result.current.error).toBeTruthy();
      // …but the executions list must have been refreshed in the finally
      // block (bd-enfsy: previously refetch only happened in the try path).
      expect(result.current.executions.length).toBe(1);
    });

    it('updates the execution list incrementally during polling (bd-enfsy)', async () => {
      // First execution row starts as 'running' so the poller observes a
      // non-terminal status, then flips to 'completed' on the second poll.
      const { result } = renderHook(() => useAutoCreationExecution());

      let pollCount = 0;
      let assignedExecutionId = 0;
      // Override the run handler to enqueue a 'running' execution
      server.use(
        http.post('/api/auto-creation/run', () => {
          const exe = createMockAutoCreationExecution({
            status: 'running',
            mode: 'execute',
          });
          mockDataStore.autoCreationExecutions.unshift(exe);
          assignedExecutionId = exe.id;
          return HttpResponse.json(
            { execution_id: exe.id, status: 'running', message: 'started' },
            { status: 202 }
          );
        }),
        http.get('/api/auto-creation/executions/:id', ({ params }) => {
          const id = parseInt(params.id as string);
          const exe = mockDataStore.autoCreationExecutions.find(e => e.id === id);
          if (!exe) {
            return HttpResponse.json({ detail: 'not found' }, { status: 404 });
          }
          pollCount += 1;
          // Flip to 'completed' on the second poll
          if (pollCount >= 2 && exe.status === 'running') {
            exe.status = 'completed';
            exe.channels_created = 7;
          }
          return HttpResponse.json(exe);
        })
      );

      let response: RunPipelineResponse | undefined;
      await act(async () => {
        response = await result.current.runPipeline({ dryRun: false });
      });

      expect(response).toBeDefined();
      expect(response!.status).toBe('completed');
      expect(response!.channels_created).toBe(7);
      // Poller hit the endpoint at least twice (once saw running, once completed).
      expect(pollCount).toBeGreaterThanOrEqual(2);
      // Execution list contains the spliced-in row from the polling updates.
      const inList = result.current.executions.find(e => e.id === assignedExecutionId);
      expect(inList).toBeDefined();
      expect(inList!.status).toBe('completed');
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
