/**
 * Unit tests for Auto-Creation API service.
 *
 * Tests validate that the API service functions correctly interact with
 * the mock handlers and return properly shaped data.
 */
import { describe, it, expect, beforeAll, afterAll, afterEach } from 'vitest';
import { server, mockDataStore, createMockAutoCreationRule, createMockAutoCreationExecution } from '../test/mocks/server';
import { http, HttpResponse } from 'msw';

// Import the API functions we're testing
import {
  // Rules CRUD
  getAutoCreationRules,
  getAutoCreationRule,
  createAutoCreationRule,
  updateAutoCreationRule,
  deleteAutoCreationRule,
  toggleAutoCreationRule,
  // Validation & Schema
  validateAutoCreationRule,
  getConditionSchema,
  getActionSchema,
  getTemplateVariables,
  // Execution
  runAutoCreationPipeline,
  getAutoCreationExecutions,
  getAutoCreationExecution,
  rollbackAutoCreationExecution,
  // YAML Import/Export
  exportAutoCreationRulesYAML,
  importAutoCreationRulesYAML,
  // Debug bundle
  startDebugBundle,
  pollDebugBundle,
  generateAndFetchDebugBundle,
} from './autoCreationApi';

// Start/stop the mock server for these tests
beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }));
afterEach(() => {
  server.resetHandlers();
  mockDataStore.autoCreationRules = [];
  mockDataStore.autoCreationExecutions = [];
});
afterAll(() => server.close());

describe('Auto-Creation API Service', () => {
  // ===========================================================================
  // Rules CRUD
  // ===========================================================================

  describe('getAutoCreationRules', () => {
    it('fetches all rules', async () => {
      mockDataStore.autoCreationRules.push(
        createMockAutoCreationRule({ name: 'Rule 1' }),
        createMockAutoCreationRule({ name: 'Rule 2' }),
      );

      // getAutoCreationRules extracts the rules array from { rules: [...] }
      const result = await getAutoCreationRules();

      expect(result).toHaveLength(2);
      expect(result[0].name).toBe('Rule 1');
      expect(result[1].name).toBe('Rule 2');
    });

    it('returns empty array when no rules exist', async () => {
      const result = await getAutoCreationRules();

      expect(result).toHaveLength(0);
    });

    it('handles network errors', async () => {
      server.use(
        http.get('/api/auto-creation/rules', () => {
          return HttpResponse.error();
        })
      );

      await expect(getAutoCreationRules()).rejects.toThrow();
    });
  });

  describe('getAutoCreationRule', () => {
    it('fetches single rule by ID', async () => {
      const rule = createMockAutoCreationRule({ id: 1, name: 'Test Rule' });
      mockDataStore.autoCreationRules.push(rule);

      const result = await getAutoCreationRule(1);

      expect(result.id).toBe(1);
      expect(result.name).toBe('Test Rule');
    });

    it('throws 404 when rule not found', async () => {
      await expect(getAutoCreationRule(999)).rejects.toThrow('Rule not found');
    });
  });

  describe('createAutoCreationRule', () => {
    it('creates a new rule', async () => {
      const result = await createAutoCreationRule({
        name: 'New Rule',
        conditions: [{ type: 'always' }],
        actions: [{ type: 'skip' }],
      });

      expect(result.name).toBe('New Rule');
      expect(result.id).toBeDefined();
      expect(mockDataStore.autoCreationRules).toHaveLength(1);
    });

    it('creates rule with all fields', async () => {
      const result = await createAutoCreationRule({
        name: 'Full Rule',
        description: 'A detailed description',
        enabled: false,
        priority: 10,
        conditions: [{ type: 'stream_name_contains', value: 'ESPN' }],
        actions: [{ type: 'create_channel', name_template: '{stream_name}' }],
        m3u_account_id: 1,
        target_group_id: 2,
        run_on_refresh: true,
        stop_on_first_match: false,
      });

      expect(result.name).toBe('Full Rule');
      expect(result.description).toBe('A detailed description');
      expect(result.enabled).toBe(false);
      expect(result.priority).toBe(10);
    });

    it('handles validation errors', async () => {
      server.use(
        http.post('/api/auto-creation/rules', () => {
          return HttpResponse.json(
            { detail: 'Invalid conditions' },
            { status: 400 }
          );
        })
      );

      await expect(createAutoCreationRule({
        name: 'Bad Rule',
        conditions: [],
        actions: [],
      })).rejects.toThrow('Invalid conditions');
    });
  });

  describe('updateAutoCreationRule', () => {
    it('updates an existing rule', async () => {
      const rule = createMockAutoCreationRule({ id: 1, name: 'Old Name' });
      mockDataStore.autoCreationRules.push(rule);

      const result = await updateAutoCreationRule(1, { name: 'New Name' });

      expect(result.name).toBe('New Name');
    });

    it('updates rule conditions', async () => {
      const rule = createMockAutoCreationRule({ id: 1, conditions: [{ type: 'always' }] });
      mockDataStore.autoCreationRules.push(rule);

      const result = await updateAutoCreationRule(1, {
        conditions: [{ type: 'stream_name_contains', value: 'ESPN' }],
      });

      expect(result.conditions).toHaveLength(1);
      expect(result.conditions[0].type).toBe('stream_name_contains');
    });

    it('throws 404 when rule not found', async () => {
      await expect(updateAutoCreationRule(999, { name: 'Test' })).rejects.toThrow('Rule not found');
    });
  });

  describe('deleteAutoCreationRule', () => {
    it('deletes a rule', async () => {
      const rule = createMockAutoCreationRule({ id: 1 });
      mockDataStore.autoCreationRules.push(rule);

      await deleteAutoCreationRule(1);

      expect(mockDataStore.autoCreationRules).toHaveLength(0);
    });

    it('throws 404 when rule not found', async () => {
      await expect(deleteAutoCreationRule(999)).rejects.toThrow('Rule not found');
    });
  });

  describe('toggleAutoCreationRule', () => {
    it('toggles rule enabled state', async () => {
      const rule = createMockAutoCreationRule({ id: 1, enabled: true });
      mockDataStore.autoCreationRules.push(rule);

      const result = await toggleAutoCreationRule(1);

      expect(result.enabled).toBe(false);
    });

    it('toggles disabled rule to enabled', async () => {
      const rule = createMockAutoCreationRule({ id: 1, enabled: false });
      mockDataStore.autoCreationRules.push(rule);

      const result = await toggleAutoCreationRule(1);

      expect(result.enabled).toBe(true);
    });
  });

  // ===========================================================================
  // Validation & Schema
  // ===========================================================================

  describe('validateAutoCreationRule', () => {
    it('validates valid rule', async () => {
      const result = await validateAutoCreationRule({
        conditions: [{ type: 'always' }],
        actions: [{ type: 'skip' }],
      });

      expect(result.valid).toBe(true);
      expect(result.errors).toHaveLength(0);
    });

    it('returns errors for invalid rule', async () => {
      const result = await validateAutoCreationRule({
        conditions: [],
        actions: [{ type: 'skip' }],
      });

      expect(result.valid).toBe(false);
      expect(result.errors.length).toBeGreaterThan(0);
    });
  });

  describe('getConditionSchema', () => {
    it('fetches condition types schema', async () => {
      // getConditionSchema extracts the conditions array from { conditions: [...] }
      const result = await getConditionSchema();

      expect(result).toBeDefined();
      expect(result.length).toBeGreaterThan(0);

      // Check for expected condition types
      const types = result.map(c => c.type);
      expect(types).toContain('stream_name_contains');
      expect(types).toContain('always');
      expect(types).toContain('and');
    });

    it('includes condition metadata', async () => {
      const result = await getConditionSchema();

      const containsCondition = result.find(c => c.type === 'stream_name_contains');
      expect(containsCondition).toBeDefined();
      expect(containsCondition?.label).toBeDefined();
      expect(containsCondition?.description).toBeDefined();
      expect(containsCondition?.category).toBe('stream');
    });
  });

  describe('getActionSchema', () => {
    it('fetches action types schema', async () => {
      // getActionSchema extracts the actions array from { actions: [...] }
      const result = await getActionSchema();

      expect(result).toBeDefined();
      expect(result.length).toBeGreaterThan(0);

      // Check for expected action types
      const types = result.map(a => a.type);
      expect(types).toContain('create_channel');
      expect(types).toContain('skip');
    });

    it('includes action parameters schema', async () => {
      const result = await getActionSchema();

      const createChannelAction = result.find(a => a.type === 'create_channel');
      expect(createChannelAction).toBeDefined();
      expect(createChannelAction?.params).toBeDefined();
      expect(createChannelAction?.params.length).toBeGreaterThan(0);
    });
  });

  describe('getTemplateVariables', () => {
    it('fetches available template variables', async () => {
      // getTemplateVariables extracts the variables array from { variables: [...] }
      const result = await getTemplateVariables();

      expect(result).toBeDefined();
      expect(result.length).toBeGreaterThan(0);

      // Check for expected variables
      const names = result.map(v => v.name);
      expect(names).toContain('{stream_name}');
      expect(names).toContain('{quality}');
    });

    it('includes variable descriptions and examples', async () => {
      const result = await getTemplateVariables();

      const streamNameVar = result.find(v => v.name === '{stream_name}');
      expect(streamNameVar).toBeDefined();
      expect(streamNameVar?.description).toBeDefined();
      expect(streamNameVar?.example).toBeDefined();
    });
  });

  // ===========================================================================
  // Execution
  // ===========================================================================

  describe('runAutoCreationPipeline', () => {
    // bd-enfsy: POST returns 202 + { execution_id, status: 'running' } now —
    // pipeline runs in a supervised background task and the caller polls
    // GET /executions/{id} until status is terminal.
    it('enqueues pipeline in execute mode and returns execution_id', async () => {
      const result = await runAutoCreationPipeline({ dryRun: false });

      expect(result.execution_id).toBeDefined();
      expect(result.status).toBe('running');
    });

    it('enqueues pipeline in dry-run mode', async () => {
      const result = await runAutoCreationPipeline({ dryRun: true });

      expect(result.execution_id).toBeDefined();
      expect(result.status).toBe('running');
    });

    it('can enqueue specific rules', async () => {
      let capturedBody: { rule_ids?: number[] } = {};
      server.use(
        http.post('/api/auto-creation/run', async ({ request }) => {
          capturedBody = await request.json() as { rule_ids?: number[] };
          return HttpResponse.json(
            { execution_id: 1, status: 'running', message: 'started' },
            { status: 202 }
          );
        })
      );

      await runAutoCreationPipeline({ ruleIds: [1, 2, 3] });

      expect(capturedBody.rule_ids).toEqual([1, 2, 3]);
    });

    it('handles pipeline enqueue errors', async () => {
      server.use(
        http.post('/api/auto-creation/run', () => {
          return HttpResponse.json(
            { detail: 'Pipeline failed' },
            { status: 500 }
          );
        })
      );

      await expect(runAutoCreationPipeline({})).rejects.toThrow('Pipeline failed');
    });
  });

  describe('getAutoCreationExecutions', () => {
    it('fetches execution history', async () => {
      mockDataStore.autoCreationExecutions.push(
        createMockAutoCreationExecution({ id: 1 }),
        createMockAutoCreationExecution({ id: 2 }),
      );

      const result = await getAutoCreationExecutions();

      expect(result.executions).toHaveLength(2);
      expect(result.total).toBe(2);
    });

    it('supports pagination', async () => {
      for (let i = 0; i < 10; i++) {
        mockDataStore.autoCreationExecutions.push(createMockAutoCreationExecution());
      }

      const result = await getAutoCreationExecutions({ limit: 5, offset: 0 });

      expect(result.executions).toHaveLength(5);
      expect(result.total).toBe(10);
    });

    it('supports filtering by status', async () => {
      mockDataStore.autoCreationExecutions.push(
        createMockAutoCreationExecution({ status: 'completed' }),
        createMockAutoCreationExecution({ status: 'failed' }),
        createMockAutoCreationExecution({ status: 'completed' }),
      );

      let capturedUrl = '';
      server.use(
        http.get('/api/auto-creation/executions', ({ request }) => {
          capturedUrl = request.url;
          const url = new URL(request.url);
          const status = url.searchParams.get('status');
          const executions = mockDataStore.autoCreationExecutions.filter(
            e => !status || e.status === status
          );
          return HttpResponse.json({
            executions,
            total: executions.length,
          });
        })
      );

      const result = await getAutoCreationExecutions({ status: 'completed' });

      expect(capturedUrl).toContain('status=completed');
      expect(result.executions).toHaveLength(2);
    });
  });

  describe('getAutoCreationExecution', () => {
    it('fetches single execution', async () => {
      const execution = createMockAutoCreationExecution({ id: 1, streams_evaluated: 100 });
      mockDataStore.autoCreationExecutions.push(execution);

      const result = await getAutoCreationExecution(1);

      expect(result.id).toBe(1);
      expect(result.streams_evaluated).toBe(100);
    });

    it('throws 404 when execution not found', async () => {
      await expect(getAutoCreationExecution(999)).rejects.toThrow('Execution not found');
    });
  });

  describe('rollbackAutoCreationExecution', () => {
    it('rolls back an execution', async () => {
      const execution = createMockAutoCreationExecution({ id: 1, mode: 'execute', status: 'completed' });
      mockDataStore.autoCreationExecutions.push(execution);

      const result = await rollbackAutoCreationExecution(1);

      expect(result.success).toBe(true);
      expect(result.entities_removed).toBeGreaterThanOrEqual(0);
      expect(result.entities_restored).toBeGreaterThanOrEqual(0);
    });

    it('fails for dry-run execution', async () => {
      const execution = createMockAutoCreationExecution({ id: 1, mode: 'dry_run', status: 'completed' });
      mockDataStore.autoCreationExecutions.push(execution);

      await expect(rollbackAutoCreationExecution(1)).rejects.toThrow();
    });

    it('fails for already rolled back execution', async () => {
      const execution = createMockAutoCreationExecution({ id: 1, mode: 'execute', status: 'rolled_back' });
      mockDataStore.autoCreationExecutions.push(execution);

      await expect(rollbackAutoCreationExecution(1)).rejects.toThrow();
    });

    it('throws 404 when execution not found', async () => {
      await expect(rollbackAutoCreationExecution(999)).rejects.toThrow('Execution not found');
    });
  });

  // ===========================================================================
  // YAML Import/Export
  // ===========================================================================

  describe('exportAutoCreationRulesYAML', () => {
    it('exports rules as YAML string', async () => {
      const result = await exportAutoCreationRulesYAML();

      expect(typeof result).toBe('string');
      // Mock handler returns plain text YAML with 'rules:' key
      expect(result).toContain('rules:');
    });

    it('handles export errors', async () => {
      server.use(
        http.get('/api/auto-creation/export/yaml', () => {
          return HttpResponse.json(
            { detail: 'Export failed' },
            { status: 500 }
          );
        })
      );

      await expect(exportAutoCreationRulesYAML()).rejects.toThrow();
    });
  });

  describe('importAutoCreationRulesYAML', () => {
    it('imports rules from YAML', async () => {
      const yamlContent = `
version: 1
rules:
  - name: Imported Rule
    enabled: true
    conditions:
      - type: always
    actions:
      - type: skip
`;

      const result = await importAutoCreationRulesYAML(yamlContent);

      expect(result.success).toBe(true);
      expect(result.imported).toHaveLength(1);
      expect(result.errors).toHaveLength(0);
    });

    it('supports overwrite flag', async () => {
      let capturedBody: { overwrite?: boolean } = {};
      server.use(
        http.post('/api/auto-creation/import/yaml', async ({ request }) => {
          capturedBody = await request.json() as { overwrite?: boolean };
          return HttpResponse.json({
            success: true,
            imported: [],
            skipped: [],
            errors: [],
          });
        })
      );

      await importAutoCreationRulesYAML('version: 1\nrules: []', true);

      expect(capturedBody.overwrite).toBe(true);
    });

    it('handles invalid YAML', async () => {
      server.use(
        http.post('/api/auto-creation/import/yaml', () => {
          return HttpResponse.json(
            { detail: 'Invalid YAML format' },
            { status: 400 }
          );
        })
      );

      await expect(importAutoCreationRulesYAML('invalid yaml {')).rejects.toThrow();
    });

    it('returns import errors', async () => {
      server.use(
        http.post('/api/auto-creation/import/yaml', () => {
          return HttpResponse.json({
            success: false,
            imported: [],
            skipped: [],
            errors: ['Invalid rule format at line 5'],
          });
        })
      );

      const result = await importAutoCreationRulesYAML('version: 1\nrules:\n  - invalid');

      expect(result.success).toBe(false);
      expect(result.errors).toHaveLength(1);
    });
  });

  // ===========================================================================
  // Debug bundle (bd-cns7j 202+poll)
  // ===========================================================================

  describe('debug bundle', () => {
    it('startDebugBundle POSTs and returns the job_id', async () => {
      server.use(
        http.post('/api/auto-creation/debug-bundle', () => {
          return HttpResponse.json(
            { job_id: 'job-abc', status: 'running' },
            { status: 202 }
          );
        })
      );

      const result = await startDebugBundle();
      expect(result.job_id).toBe('job-abc');
      expect(result.status).toBe('running');
    });

    it('pollDebugBundle returns Blob + filename when the artifact is ready', async () => {
      const tarBytes = new Uint8Array([0x1f, 0x8b, 0x08, 0x00]);
      server.use(
        http.get('/api/auto-creation/debug-bundle/job-ready', () => {
          return new HttpResponse(tarBytes, {
            status: 200,
            headers: {
              'Content-Type': 'application/gzip',
              'Content-Disposition': 'attachment; filename="ecm-debug-bundle-test.tar.gz"',
            },
          });
        })
      );

      const { blob, filename } = await pollDebugBundle('job-ready');
      expect(filename).toBe('ecm-debug-bundle-test.tar.gz');
      expect(blob.size).toBe(tarBytes.length);
    });

    it('pollDebugBundle surfaces the backend error message on failed status', async () => {
      server.use(
        http.get('/api/auto-creation/debug-bundle/job-failed', () => {
          return HttpResponse.json({
            job_id: 'job-failed',
            status: 'failed',
            error: 'RuntimeError: dispatcharr unreachable',
          });
        })
      );

      await expect(pollDebugBundle('job-failed')).rejects.toThrow(
        'dispatcharr unreachable'
      );
    });

    it('pollDebugBundle throws on 404 unknown job id', async () => {
      server.use(
        http.get('/api/auto-creation/debug-bundle/missing', () => {
          return HttpResponse.json({ detail: 'not found' }, { status: 404 });
        })
      );

      await expect(pollDebugBundle('missing')).rejects.toThrow(/not found/i);
    });

    it('pollDebugBundle polls "running" then resolves on the next ready response', async () => {
      let calls = 0;
      server.use(
        http.get('/api/auto-creation/debug-bundle/job-flip', () => {
          calls++;
          if (calls === 1) {
            return HttpResponse.json({ job_id: 'job-flip', status: 'running' });
          }
          return new HttpResponse(new Uint8Array([1, 2, 3]), {
            status: 200,
            headers: {
              'Content-Type': 'application/gzip',
              'Content-Disposition': 'attachment; filename="bundle.tar.gz"',
            },
          });
        })
      );

      // Speed up the inter-poll sleep so the test doesn't wait a full second.
      const realSetTimeout = window.setTimeout;
      const stubSetTimeout: typeof window.setTimeout = ((fn: (...args: unknown[]) => void) => {
        return realSetTimeout(fn, 0);
      }) as typeof window.setTimeout;
      const restore = window.setTimeout;
      window.setTimeout = stubSetTimeout;
      try {
        const { blob, filename } = await pollDebugBundle('job-flip');
        expect(filename).toBe('bundle.tar.gz');
        expect(blob.size).toBe(3);
        expect(calls).toBe(2);
      } finally {
        window.setTimeout = restore;
      }
    });

    it('generateAndFetchDebugBundle wires enqueue → poll → download', async () => {
      let postCount = 0;
      server.use(
        http.post('/api/auto-creation/debug-bundle', () => {
          postCount++;
          return HttpResponse.json(
            { job_id: 'job-flow', status: 'running' },
            { status: 202 }
          );
        }),
        http.get('/api/auto-creation/debug-bundle/job-flow', () => {
          return new HttpResponse(new Uint8Array([0xff]), {
            status: 200,
            headers: {
              'Content-Type': 'application/gzip',
              'Content-Disposition': 'attachment; filename="ecm-debug-bundle.tar.gz"',
            },
          });
        })
      );

      const { blob, filename } = await generateAndFetchDebugBundle();
      expect(postCount).toBe(1);
      expect(filename).toBe('ecm-debug-bundle.tar.gz');
      expect(blob.size).toBe(1);
    });
  });
});
