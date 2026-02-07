/**
 * TDD Tests for useAutoCreationRules hook.
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
  createMockAutoCreationRule,
} from '../test/mocks/server';
import { useAutoCreationRules } from './useAutoCreationRules';
import type { AutoCreationRule, CreateRuleData, UpdateRuleData } from '../types/autoCreation';

// Setup MSW server
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers();
  resetMockDataStore();
});
afterAll(() => server.close());

describe('useAutoCreationRules', () => {
  describe('initial state', () => {
    it('starts with empty rules array', () => {
      const { result } = renderHook(() => useAutoCreationRules());
      expect(result.current.rules).toEqual([]);
    });

    it('starts with loading false', () => {
      const { result } = renderHook(() => useAutoCreationRules());
      expect(result.current.loading).toBe(false);
    });

    it('starts with error null', () => {
      const { result } = renderHook(() => useAutoCreationRules());
      expect(result.current.error).toBeNull();
    });
  });

  describe('fetchRules', () => {
    it('fetches rules from API', async () => {
      // Setup: Add rules to mock store
      const rule1 = createMockAutoCreationRule({ name: 'Rule 1' });
      const rule2 = createMockAutoCreationRule({ name: 'Rule 2' });
      mockDataStore.autoCreationRules.push(rule1, rule2);

      const { result } = renderHook(() => useAutoCreationRules());

      await act(async () => {
        await result.current.fetchRules();
      });

      expect(result.current.rules).toHaveLength(2);
      expect(result.current.rules[0].name).toBe('Rule 1');
      expect(result.current.rules[1].name).toBe('Rule 2');
    });

    it('sets loading true during fetch', async () => {
      const { result } = renderHook(() => useAutoCreationRules());

      // Start fetch and check that loading is eventually set and then cleared
      await act(async () => {
        await result.current.fetchRules();
      });

      // After fetch completes, loading should be false
      expect(result.current.loading).toBe(false);
    });

    it('handles fetch error', async () => {
      // Override handler to return error
      server.use(
        http.get('/api/auto-creation/rules', () => {
          return new HttpResponse(null, { status: 500 });
        })
      );

      const { result } = renderHook(() => useAutoCreationRules());

      await act(async () => {
        await result.current.fetchRules();
      });

      expect(result.current.error).toBeTruthy();
      expect(result.current.rules).toEqual([]);
    });

    it('clears previous error on successful fetch', async () => {
      const { result } = renderHook(() => useAutoCreationRules());

      // First, set an error state manually
      act(() => {
        result.current.setError('Previous error');
      });
      expect(result.current.error).toBe('Previous error');

      // Add a rule to ensure successful fetch
      mockDataStore.autoCreationRules.push(createMockAutoCreationRule());

      // Fetch should clear the error
      await act(async () => {
        await result.current.fetchRules();
      });

      expect(result.current.error).toBeNull();
    });
  });

  describe('createRule', () => {
    it('creates a new rule and adds it to the list', async () => {
      const { result } = renderHook(() => useAutoCreationRules());

      const newRuleData: CreateRuleData = {
        name: 'New Test Rule',
        conditions: [{ type: 'always' }],
        actions: [{ type: 'skip' }],
      };

      let createdRule: AutoCreationRule | undefined;
      await act(async () => {
        createdRule = await result.current.createRule(newRuleData);
      });

      expect(createdRule).toBeDefined();
      expect(createdRule!.name).toBe('New Test Rule');
      expect(createdRule!.id).toBeDefined();
      expect(result.current.rules).toContainEqual(expect.objectContaining({ name: 'New Test Rule' }));
    });

    it('returns undefined on create error', async () => {
      server.use(
        http.post('/api/auto-creation/rules', () => {
          return new HttpResponse(
            JSON.stringify({ detail: 'Validation error' }),
            { status: 400 }
          );
        })
      );

      const { result } = renderHook(() => useAutoCreationRules());

      let createdRule: AutoCreationRule | undefined;
      await act(async () => {
        createdRule = await result.current.createRule({
          name: 'Invalid Rule',
          conditions: [],
          actions: [],
        });
      });

      expect(createdRule).toBeUndefined();
      expect(result.current.error).toBeTruthy();
    });

    it('sets loading state during creation', async () => {
      const { result } = renderHook(() => useAutoCreationRules());

      // Start create and check that loading is eventually cleared
      await act(async () => {
        await result.current.createRule({
          name: 'New Rule',
          conditions: [{ type: 'always' }],
          actions: [{ type: 'skip' }],
        });
      });

      // After create completes, loading should be false
      expect(result.current.loading).toBe(false);
    });
  });

  describe('updateRule', () => {
    it('updates an existing rule', async () => {
      const existingRule = createMockAutoCreationRule({ name: 'Original Name' });
      mockDataStore.autoCreationRules.push(existingRule);

      const { result } = renderHook(() => useAutoCreationRules());

      // First fetch the rules
      await act(async () => {
        await result.current.fetchRules();
      });

      const updateData: UpdateRuleData = { name: 'Updated Name' };

      let updatedRule: AutoCreationRule | undefined;
      await act(async () => {
        updatedRule = await result.current.updateRule(existingRule.id, updateData);
      });

      expect(updatedRule).toBeDefined();
      expect(updatedRule!.name).toBe('Updated Name');
      expect(result.current.rules.find(r => r.id === existingRule.id)?.name).toBe('Updated Name');
    });

    it('returns undefined when updating non-existent rule', async () => {
      const { result } = renderHook(() => useAutoCreationRules());

      let updatedRule: AutoCreationRule | undefined;
      await act(async () => {
        updatedRule = await result.current.updateRule(99999, { name: 'Not Found' });
      });

      expect(updatedRule).toBeUndefined();
      expect(result.current.error).toBeTruthy();
    });

    it('updates rule in local state optimistically', async () => {
      const existingRule = createMockAutoCreationRule({ name: 'Original', enabled: true });
      mockDataStore.autoCreationRules.push(existingRule);

      const { result } = renderHook(() => useAutoCreationRules());

      await act(async () => {
        await result.current.fetchRules();
      });

      // Update should immediately reflect in state
      await act(async () => {
        await result.current.updateRule(existingRule.id, { enabled: false });
      });

      const localRule = result.current.rules.find(r => r.id === existingRule.id);
      expect(localRule?.enabled).toBe(false);
    });
  });

  describe('deleteRule', () => {
    it('deletes a rule and removes it from the list', async () => {
      const rule1 = createMockAutoCreationRule({ name: 'Rule 1' });
      const rule2 = createMockAutoCreationRule({ name: 'Rule 2' });
      mockDataStore.autoCreationRules.push(rule1, rule2);

      const { result } = renderHook(() => useAutoCreationRules());

      await act(async () => {
        await result.current.fetchRules();
      });

      expect(result.current.rules).toHaveLength(2);

      let success: boolean = false;
      await act(async () => {
        success = await result.current.deleteRule(rule1.id);
      });

      expect(success).toBe(true);
      expect(result.current.rules).toHaveLength(1);
      expect(result.current.rules.find(r => r.id === rule1.id)).toBeUndefined();
    });

    it('returns false when deleting non-existent rule', async () => {
      const { result } = renderHook(() => useAutoCreationRules());

      let success: boolean = true;
      await act(async () => {
        success = await result.current.deleteRule(99999);
      });

      expect(success).toBe(false);
      expect(result.current.error).toBeTruthy();
    });
  });

  describe('toggleRule', () => {
    it('toggles rule enabled state from true to false', async () => {
      const rule = createMockAutoCreationRule({ enabled: true });
      mockDataStore.autoCreationRules.push(rule);

      const { result } = renderHook(() => useAutoCreationRules());

      await act(async () => {
        await result.current.fetchRules();
      });

      expect(result.current.rules[0].enabled).toBe(true);

      await act(async () => {
        await result.current.toggleRule(rule.id);
      });

      expect(result.current.rules[0].enabled).toBe(false);
    });

    it('toggles rule enabled state from false to true', async () => {
      const rule = createMockAutoCreationRule({ enabled: false });
      mockDataStore.autoCreationRules.push(rule);

      const { result } = renderHook(() => useAutoCreationRules());

      await act(async () => {
        await result.current.fetchRules();
      });

      expect(result.current.rules[0].enabled).toBe(false);

      await act(async () => {
        await result.current.toggleRule(rule.id);
      });

      expect(result.current.rules[0].enabled).toBe(true);
    });

    it('returns the toggled rule', async () => {
      const rule = createMockAutoCreationRule({ enabled: true, name: 'Toggle Test' });
      mockDataStore.autoCreationRules.push(rule);

      const { result } = renderHook(() => useAutoCreationRules());

      await act(async () => {
        await result.current.fetchRules();
      });

      let toggledRule: AutoCreationRule | undefined;
      await act(async () => {
        toggledRule = await result.current.toggleRule(rule.id);
      });

      expect(toggledRule).toBeDefined();
      expect(toggledRule!.name).toBe('Toggle Test');
      expect(toggledRule!.enabled).toBe(false);
    });
  });

  describe('getRule', () => {
    it('returns a rule by ID from local state', async () => {
      const rule = createMockAutoCreationRule({ name: 'Find Me' });
      mockDataStore.autoCreationRules.push(rule);

      const { result } = renderHook(() => useAutoCreationRules());

      await act(async () => {
        await result.current.fetchRules();
      });

      const found = result.current.getRule(rule.id);
      expect(found).toBeDefined();
      expect(found!.name).toBe('Find Me');
    });

    it('returns undefined for non-existent rule', async () => {
      const { result } = renderHook(() => useAutoCreationRules());

      const found = result.current.getRule(99999);
      expect(found).toBeUndefined();
    });
  });

  describe('getRulesByPriority', () => {
    it('returns rules sorted by priority ascending', async () => {
      mockDataStore.autoCreationRules.push(
        createMockAutoCreationRule({ name: 'Low Priority', priority: 100 }),
        createMockAutoCreationRule({ name: 'High Priority', priority: 1 }),
        createMockAutoCreationRule({ name: 'Medium Priority', priority: 50 })
      );

      const { result } = renderHook(() => useAutoCreationRules());

      await act(async () => {
        await result.current.fetchRules();
      });

      const sorted = result.current.getRulesByPriority();
      expect(sorted[0].name).toBe('High Priority');
      expect(sorted[1].name).toBe('Medium Priority');
      expect(sorted[2].name).toBe('Low Priority');
    });
  });

  describe('getEnabledRules', () => {
    it('returns only enabled rules', async () => {
      mockDataStore.autoCreationRules.push(
        createMockAutoCreationRule({ name: 'Enabled 1', enabled: true }),
        createMockAutoCreationRule({ name: 'Disabled', enabled: false }),
        createMockAutoCreationRule({ name: 'Enabled 2', enabled: true })
      );

      const { result } = renderHook(() => useAutoCreationRules());

      await act(async () => {
        await result.current.fetchRules();
      });

      const enabled = result.current.getEnabledRules();
      expect(enabled).toHaveLength(2);
      expect(enabled.every(r => r.enabled)).toBe(true);
    });
  });

  describe('error handling', () => {
    it('provides setError for manual error setting', () => {
      const { result } = renderHook(() => useAutoCreationRules());

      act(() => {
        result.current.setError('Manual error');
      });

      expect(result.current.error).toBe('Manual error');
    });

    it('provides clearError to clear errors', () => {
      const { result } = renderHook(() => useAutoCreationRules());

      act(() => {
        result.current.setError('Some error');
      });
      expect(result.current.error).toBe('Some error');

      act(() => {
        result.current.clearError();
      });
      expect(result.current.error).toBeNull();
    });
  });

  describe('reorderRules', () => {
    it('updates priorities for multiple rules', async () => {
      const rule1 = createMockAutoCreationRule({ name: 'Rule 1', priority: 0 });
      const rule2 = createMockAutoCreationRule({ name: 'Rule 2', priority: 1 });
      const rule3 = createMockAutoCreationRule({ name: 'Rule 3', priority: 2 });
      mockDataStore.autoCreationRules.push(rule1, rule2, rule3);

      const { result } = renderHook(() => useAutoCreationRules());

      await act(async () => {
        await result.current.fetchRules();
      });

      // Reorder: [rule3, rule1, rule2]
      const newOrder = [rule3.id, rule1.id, rule2.id];
      await act(async () => {
        await result.current.reorderRules(newOrder);
      });

      const sorted = result.current.getRulesByPriority();
      expect(sorted[0].id).toBe(rule3.id);
      expect(sorted[1].id).toBe(rule1.id);
      expect(sorted[2].id).toBe(rule2.id);
    });
  });

  describe('duplicateRule', () => {
    it('creates a copy of an existing rule with modified name', async () => {
      const original = createMockAutoCreationRule({
        name: 'Original Rule',
        conditions: [{ type: 'stream_name_contains', value: 'ESPN' }],
        actions: [{ type: 'create_channel', name_template: '{stream_name}' }],
      });
      mockDataStore.autoCreationRules.push(original);

      const { result } = renderHook(() => useAutoCreationRules());

      await act(async () => {
        await result.current.fetchRules();
      });

      let duplicate: AutoCreationRule | undefined;
      await act(async () => {
        duplicate = await result.current.duplicateRule(original.id);
      });

      expect(duplicate).toBeDefined();
      expect(duplicate!.name).toContain('Original Rule');
      expect(duplicate!.name).toContain('Copy');
      expect(duplicate!.id).not.toBe(original.id);
      expect(result.current.rules).toHaveLength(2);
    });

    it('returns undefined when duplicating non-existent rule', async () => {
      const { result } = renderHook(() => useAutoCreationRules());

      let duplicate: AutoCreationRule | undefined;
      await act(async () => {
        duplicate = await result.current.duplicateRule(99999);
      });

      expect(duplicate).toBeUndefined();
    });
  });

  describe('autoFetch option', () => {
    it('automatically fetches rules when autoFetch is true', async () => {
      const rule = createMockAutoCreationRule({ name: 'Auto Fetched' });
      mockDataStore.autoCreationRules.push(rule);

      const { result } = renderHook(() => useAutoCreationRules({ autoFetch: true }));

      await waitFor(() => {
        expect(result.current.rules).toHaveLength(1);
      });

      expect(result.current.rules[0].name).toBe('Auto Fetched');
    });

    it('does not auto-fetch when autoFetch is false', async () => {
      const rule = createMockAutoCreationRule({ name: 'Should Not Appear' });
      mockDataStore.autoCreationRules.push(rule);

      const { result } = renderHook(() => useAutoCreationRules({ autoFetch: false }));

      // Wait a bit to ensure no auto-fetch happens
      await new Promise(resolve => setTimeout(resolve, 100));

      expect(result.current.rules).toEqual([]);
    });
  });

  describe('refetch after mutations', () => {
    it('updates local state after createRule without refetch', async () => {
      const { result } = renderHook(() => useAutoCreationRules());

      await act(async () => {
        await result.current.createRule({
          name: 'New Rule',
          conditions: [{ type: 'always' }],
          actions: [{ type: 'skip' }],
        });
      });

      // Rule should be in local state immediately
      expect(result.current.rules).toHaveLength(1);
      expect(result.current.rules[0].name).toBe('New Rule');
    });
  });
});
