/**
 * Hook for managing channel pipeline rules state.
 *
 * Provides CRUD operations, toggling, and helper methods for rules.
 */
import { useState, useCallback, useEffect } from 'react';
import type {
  ChannelPipelineRule,
  CreateRuleData,
  UpdateRuleData,
  BulkUpdateRulesPatch,
  BulkUpdateRulesResponse,
} from '../types/channelPipeline';
import * as api from '../services/channelPipelineApi';

export interface UseChannelPipelineRulesOptions {
  /** Automatically fetch rules on mount */
  autoFetch?: boolean;
}

export interface UseChannelPipelineRulesResult {
  /** List of rules */
  rules: ChannelPipelineRule[];
  /** Loading state */
  loading: boolean;
  /** Error message */
  error: string | null;
  /** Fetch all rules */
  fetchRules: () => Promise<void>;
  /** Create a new rule */
  createRule: (data: CreateRuleData) => Promise<ChannelPipelineRule>;
  /** Update an existing rule */
  updateRule: (id: number, data: UpdateRuleData) => Promise<ChannelPipelineRule>;
  /** Delete a rule */
  deleteRule: (id: number) => Promise<void>;
  /** Toggle rule enabled state */
  toggleRule: (id: number) => Promise<ChannelPipelineRule>;
  /** Get a rule by ID from local state */
  getRule: (id: number) => ChannelPipelineRule | undefined;
  /** Get rules sorted by priority */
  getRulesByPriority: () => ChannelPipelineRule[];
  /** Get only enabled rules */
  getEnabledRules: () => ChannelPipelineRule[];
  /** Reorder rules (update priorities) */
  reorderRules: (orderedIds: number[]) => Promise<void>;
  /** Duplicate a rule */
  duplicateRule: (id: number) => Promise<ChannelPipelineRule>;
  /** Apply the same settings to multiple rules */
  bulkUpdateRules: (ruleIds: number[], patch: BulkUpdateRulesPatch) => Promise<BulkUpdateRulesResponse>;
  /** Set error manually */
  setError: (error: string | null) => void;
  /** Clear error */
  clearError: () => void;
}

export function useChannelPipelineRules(
  options: UseChannelPipelineRulesOptions = {}
): UseChannelPipelineRulesResult {
  const { autoFetch = false } = options;

  const [rules, setRules] = useState<ChannelPipelineRule[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchRules = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const fetchedRules = await api.getChannelPipelineRules();
      setRules(fetchedRules);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch rules');
    } finally {
      setLoading(false);
    }
  }, []);

  const createRule = useCallback(async (data: CreateRuleData): Promise<ChannelPipelineRule> => {
    setLoading(true);
    try {
      const newRule = await api.createChannelPipelineRule(data);
      setRules(prev => [...prev, newRule]);
      return newRule;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to create rule';
      throw new Error(message, { cause: err });
    } finally {
      setLoading(false);
    }
  }, []);

  const updateRule = useCallback(async (id: number, data: UpdateRuleData): Promise<ChannelPipelineRule> => {
    setLoading(true);
    try {
      const updatedRule = await api.updateChannelPipelineRule(id, data);
      setRules(prev => prev.map(r => r.id === id ? updatedRule : r));
      return updatedRule;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to update rule';
      throw new Error(message, { cause: err });
    } finally {
      setLoading(false);
    }
  }, []);

  const deleteRule = useCallback(async (id: number): Promise<void> => {
    setLoading(true);
    try {
      await api.deleteChannelPipelineRule(id);
      setRules(prev => prev.filter(r => r.id !== id));
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to delete rule';
      throw new Error(message, { cause: err });
    } finally {
      setLoading(false);
    }
  }, []);

  const toggleRule = useCallback(async (id: number): Promise<ChannelPipelineRule> => {
    setLoading(true);
    try {
      const toggledRule = await api.toggleChannelPipelineRule(id);
      setRules(prev => prev.map(r => r.id === id ? toggledRule : r));
      return toggledRule;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to toggle rule';
      throw new Error(message, { cause: err });
    } finally {
      setLoading(false);
    }
  }, []);

  const getRule = useCallback((id: number): ChannelPipelineRule | undefined => {
    return rules.find(r => r.id === id);
  }, [rules]);

  const getRulesByPriority = useCallback((): ChannelPipelineRule[] => {
    return [...rules].sort((a, b) => a.priority - b.priority);
  }, [rules]);

  const getEnabledRules = useCallback((): ChannelPipelineRule[] => {
    return rules.filter(r => r.enabled);
  }, [rules]);

  /**
   * Reorder rules. `orderedIds` is the new order; each listed rule takes a
   * priority equal to its index, exactly as the server does.
   *
   * GH #755: this used to issue one `PUT /rules/{id}` per rule inside a single
   * `Promise.all`. One reorder on a 120-rule instance therefore put 120 writes
   * in flight at once, which exceeded uvicorn's `--limit-concurrency`
   * (`backend/entrypoint.sh`, `ECM_LIMIT_CONCURRENCY`, default 100); the
   * refused writes came back 503 and the operator got an error toast for an
   * operation that had partly succeeded. The amplification scaled with rule
   * count, so it is fixed by writing once, not by raising the limit.
   */
  const reorderRules = useCallback(async (orderedIds: number[]): Promise<void> => {
    setLoading(true);
    try {
      await api.reorderChannelPipelineRules(orderedIds);

      // Mirror the server's own semantics: listed rules take their index as
      // priority, rules outside the list keep the priority they already had.
      setRules(prev => {
        const newPriority = new Map(orderedIds.map((id, index) => [id, index]));
        return prev.map(rule => {
          const priority = newPriority.get(rule.id);
          return priority === undefined ? rule : { ...rule, priority };
        });
      });
    } catch (err) {
      // GH #755 (second defect): the list must never be left showing an order
      // the server does not have. The old code updated local state only after
      // the writes resolved, so a failure left the copy stranded at the bottom
      // until the operator reloaded the page. Resync first, then surface the
      // failure — the caller still gets its error toast.
      await fetchRules();
      const message = err instanceof Error ? err.message : 'Failed to reorder rules';
      throw new Error(message, { cause: err });
    } finally {
      setLoading(false);
    }
  }, [fetchRules]);

  const duplicateRule = useCallback(async (id: number): Promise<ChannelPipelineRule> => {
    const originalRule = rules.find(r => r.id === id);
    if (!originalRule) {
      throw new Error('Rule not found');
    }

    // Find a unique priority: max + 1 to avoid duplicates
    const maxPriority = rules.length > 0 ? Math.max(...rules.map(r => r.priority)) : -1;

    const duplicateData: CreateRuleData = {
      name: `${originalRule.name} (Copy)`,
      description: originalRule.description,
      enabled: false, // Disabled by default (safety: avoid duplicate processing)
      priority: maxPriority + 1,
      active_from: originalRule.active_from ?? null,
      active_until: originalRule.active_until ?? null,
      conditions: originalRule.conditions,
      actions: originalRule.actions,
      m3u_account_id: originalRule.m3u_account_id,
      target_group_id: originalRule.target_group_id,
      run_on_refresh: originalRule.run_on_refresh,
      stop_on_first_match: originalRule.stop_on_first_match,
      sort_field: originalRule.sort_field ?? null,
      sort_order: originalRule.sort_order,
      probe_on_sort: originalRule.probe_on_sort,
      sort_regex: originalRule.sort_regex ?? null,
      stream_sort_field: originalRule.stream_sort_field ?? null,
      stream_sort_order: originalRule.stream_sort_order,
      normalization_group_ids: originalRule.normalization_group_ids,
      skip_struck_streams: originalRule.skip_struck_streams,
      orphan_action: originalRule.orphan_action,
    };

    const newRule = await createRule(duplicateData);

    // Reorder so the duplicate appears right after the original
    const sorted = [...rules, newRule].sort((a, b) => a.priority - b.priority);
    const orderedIds = sorted.map(r => r.id);
    // Move the new rule to right after the original
    const origIndex = orderedIds.indexOf(id);
    const newIndex = orderedIds.indexOf(newRule.id);
    if (origIndex !== -1 && newIndex !== -1 && newIndex !== origIndex + 1) {
      orderedIds.splice(newIndex, 1);
      orderedIds.splice(origIndex + 1, 0, newRule.id);
    }
    await reorderRules(orderedIds);

    return newRule;
  }, [rules, createRule, reorderRules]);

  const bulkUpdateRules = useCallback(async (
    ruleIds: number[],
    patch: BulkUpdateRulesPatch
  ): Promise<BulkUpdateRulesResponse> => {
    setLoading(true);
    try {
      const result = await api.bulkUpdateChannelPipelineRules(ruleIds, patch);
      setRules(prev => {
        const byId = new Map(result.rules.map(r => [r.id, r]));
        return prev.map(r => byId.get(r.id) ?? r);
      });
      return result;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to bulk update rules';
      throw new Error(message, { cause: err });
    } finally {
      setLoading(false);
    }
  }, []);

  const clearError = useCallback(() => {
    setError(null);
  }, []);

  // Auto-fetch on mount if enabled
  useEffect(() => {
    if (autoFetch) {
      fetchRules();
    }
  }, [autoFetch, fetchRules]);

  return {
    rules,
    loading,
    error,
    fetchRules,
    createRule,
    updateRule,
    deleteRule,
    toggleRule,
    getRule,
    getRulesByPriority,
    getEnabledRules,
    reorderRules,
    duplicateRule,
    bulkUpdateRules,
    setError,
    clearError,
  };
}
