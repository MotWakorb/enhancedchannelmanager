/**
 * Hook for managing auto-creation rules state.
 *
 * Provides CRUD operations, toggling, and helper methods for rules.
 */
import { useState, useCallback, useEffect } from 'react';
import type { AutoCreationRule, CreateRuleData, UpdateRuleData } from '../types/autoCreation';
import * as api from '../services/autoCreationApi';

export interface UseAutoCreationRulesOptions {
  /** Automatically fetch rules on mount */
  autoFetch?: boolean;
}

export interface UseAutoCreationRulesResult {
  /** List of rules */
  rules: AutoCreationRule[];
  /** Loading state */
  loading: boolean;
  /** Error message */
  error: string | null;
  /** Fetch all rules */
  fetchRules: () => Promise<void>;
  /** Create a new rule */
  createRule: (data: CreateRuleData) => Promise<AutoCreationRule>;
  /** Update an existing rule */
  updateRule: (id: number, data: UpdateRuleData) => Promise<AutoCreationRule>;
  /** Delete a rule */
  deleteRule: (id: number) => Promise<void>;
  /** Toggle rule enabled state */
  toggleRule: (id: number) => Promise<AutoCreationRule>;
  /** Get a rule by ID from local state */
  getRule: (id: number) => AutoCreationRule | undefined;
  /** Get rules sorted by priority */
  getRulesByPriority: () => AutoCreationRule[];
  /** Get only enabled rules */
  getEnabledRules: () => AutoCreationRule[];
  /** Reorder rules (update priorities) */
  reorderRules: (orderedIds: number[]) => Promise<void>;
  /** Duplicate a rule */
  duplicateRule: (id: number) => Promise<AutoCreationRule>;
  /** Set error manually */
  setError: (error: string | null) => void;
  /** Clear error */
  clearError: () => void;
}

export function useAutoCreationRules(
  options: UseAutoCreationRulesOptions = {}
): UseAutoCreationRulesResult {
  const { autoFetch = false } = options;

  const [rules, setRules] = useState<AutoCreationRule[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchRules = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const fetchedRules = await api.getAutoCreationRules();
      setRules(fetchedRules);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch rules');
    } finally {
      setLoading(false);
    }
  }, []);

  const createRule = useCallback(async (data: CreateRuleData): Promise<AutoCreationRule> => {
    setLoading(true);
    try {
      const newRule = await api.createAutoCreationRule(data);
      setRules(prev => [...prev, newRule]);
      return newRule;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to create rule';
      throw new Error(message);
    } finally {
      setLoading(false);
    }
  }, []);

  const updateRule = useCallback(async (id: number, data: UpdateRuleData): Promise<AutoCreationRule> => {
    setLoading(true);
    try {
      const updatedRule = await api.updateAutoCreationRule(id, data);
      setRules(prev => prev.map(r => r.id === id ? updatedRule : r));
      return updatedRule;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to update rule';
      throw new Error(message);
    } finally {
      setLoading(false);
    }
  }, []);

  const deleteRule = useCallback(async (id: number): Promise<void> => {
    setLoading(true);
    try {
      await api.deleteAutoCreationRule(id);
      setRules(prev => prev.filter(r => r.id !== id));
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to delete rule';
      throw new Error(message);
    } finally {
      setLoading(false);
    }
  }, []);

  const toggleRule = useCallback(async (id: number): Promise<AutoCreationRule> => {
    setLoading(true);
    try {
      const toggledRule = await api.toggleAutoCreationRule(id);
      setRules(prev => prev.map(r => r.id === id ? toggledRule : r));
      return toggledRule;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to toggle rule';
      throw new Error(message);
    } finally {
      setLoading(false);
    }
  }, []);

  const getRule = useCallback((id: number): AutoCreationRule | undefined => {
    return rules.find(r => r.id === id);
  }, [rules]);

  const getRulesByPriority = useCallback((): AutoCreationRule[] => {
    return [...rules].sort((a, b) => a.priority - b.priority);
  }, [rules]);

  const getEnabledRules = useCallback((): AutoCreationRule[] => {
    return rules.filter(r => r.enabled);
  }, [rules]);

  const reorderRules = useCallback(async (orderedIds: number[]): Promise<void> => {
    setLoading(true);
    try {
      // Update each rule's priority based on position in orderedIds
      const updates = orderedIds.map((id, index) =>
        api.updateAutoCreationRule(id, { priority: index })
      );
      await Promise.all(updates);

      // Update local state
      setRules(prev => {
        const ruleMap = new Map(prev.map(r => [r.id, r]));
        return orderedIds
          .map((id, index) => {
            const rule = ruleMap.get(id);
            if (rule) {
              return { ...rule, priority: index };
            }
            return null;
          })
          .filter((r): r is AutoCreationRule => r !== null);
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to reorder rules';
      throw new Error(message);
    } finally {
      setLoading(false);
    }
  }, []);

  const duplicateRule = useCallback(async (id: number): Promise<AutoCreationRule> => {
    const originalRule = rules.find(r => r.id === id);
    if (!originalRule) {
      throw new Error('Rule not found');
    }

    // Find a unique priority: max + 1 to avoid duplicates
    const maxPriority = rules.length > 0 ? Math.max(...rules.map(r => r.priority)) : -1;

    const duplicateData: CreateRuleData = {
      name: `${originalRule.name} (Copy)`,
      description: originalRule.description,
      enabled: false, // Disabled by default
      priority: maxPriority + 1,
      conditions: originalRule.conditions,
      actions: originalRule.actions,
      m3u_account_id: originalRule.m3u_account_id,
      target_group_id: originalRule.target_group_id,
      run_on_refresh: originalRule.run_on_refresh,
      stop_on_first_match: originalRule.stop_on_first_match,
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
    setError,
    clearError,
  };
}
