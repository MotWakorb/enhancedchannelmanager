/**
 * Main Channel Pipeline tab component for managing channel pipeline rules and executions.
 */
import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import type {
  ChannelPipelineRule,
  ChannelPipelineExecution,
  CreateRuleData,
  ExecutionLogEntry,
  ActionLogEntry,
  BulkUpdateRulesPatch,
  RestoreSnapshotResponse,
  FailedChannel,
} from '../../types/channelPipeline';
import type { CircuitBreakerState } from '../../services/channelPipelineApi';
import { useAuth } from '../../hooks/useAuth';
import { useChannelPipelineRules } from '../../hooks/useChannelPipelineRules';
import { useChannelPipelineExecution } from '../../hooks/useChannelPipelineExecution';
import { RuleBuilder } from './RuleBuilder';
import { EventSyncRuleEditor } from './EventSyncRuleEditor';
import { BulkRuleSettingsModal } from './BulkRuleSettingsModal';
import { CircuitBreakerBanner } from './CircuitBreakerBanner';
import * as channelPipelineApi from '../../services/channelPipelineApi';
import { copyToClipboard } from '../../utils/clipboard';
import { getDateLocale } from '../../utils/formatting';
import { useNotifications } from '../../contexts/NotificationContext';
import { ModalOverlay } from '../ModalOverlay';
import '../ModalBase.css';
import './ChannelPipelineTab.css';

type FilterMode = 'all' | 'enabled' | 'disabled';

const getStatusBadgeClass = (status: string) => {
  const map: Record<string, string> = {
    enabled: 'badge-success', completed: 'badge-success',
    disabled: '', failed: 'badge-error',
    running: 'badge-info', rolled_back: 'badge-warning',
    capped: 'badge-warning', abandoned: 'badge-error',
  };
  return `badge badge-sm badge-uppercase ${map[status] || ''}`;
};

/** Human-readable label for execution statuses that need one (others are fine as-is). */
const EXECUTION_STATUS_LABEL: Partial<Record<string, string>> = {
  rolled_back: 'Rolled Back',
  capped: 'Capped',
  abandoned: 'Abandoned',
};

/** Categorize a log action into a filter bucket. */
function getActionCategory(action: ActionLogEntry): string | null {
  const desc = action.description?.toLowerCase() || '';
  if (action.type === 'create_channel' || action.type === 'create_group') {
    return 'created';
  } else if (action.type === 'merge_streams' || action.type === 'merge_stream') {
    return desc.includes('no existing channel found') || desc.includes('stream skipped')
      ? 'skipped' : 'merged';
  } else if (action.type === 'skip' || desc.includes('skipped')) {
    return 'skipped';
  } else if (action.type === 'update_channel') {
    return 'updated';
  } else if (action.type === 'remove_from_channel') {
    return 'removed';
  } else if (action.type === 'set_stream_priority') {
    return 'updated';
  } else if ((action as ActionLogEntry & { action?: string }).action === 'excluded' || desc.includes('excluded:')) {
    return 'excluded';
  } else if (['assign_logo', 'assign_tvg_id', 'assign_epg', 'assign_profile', 'set_channel_number'].includes(action.type)) {
    return 'assigned';
  }
  return null;
}

export function ChannelPipelineTab() {
  const { user } = useAuth();
  const isAdmin = Boolean(user?.is_admin);

  // State from hooks
  const {
    rules,
    loading: rulesLoading,
    error: rulesError,
    fetchRules,
    createRule,
    updateRule,
    deleteRule,
    toggleRule,
    duplicateRule,
    reorderRules,
    getEnabledRules,
    bulkUpdateRules,
  } = useChannelPipelineRules();

  const {
    executions,
    loading: executionsLoading,
    error: executionsError,
    isRunning: runningPipeline,
    fetchExecutions,
    runPipeline: runPipelineApi,
    rollback,
  } = useChannelPipelineExecution();

  // Circuit-breaker state (bd-fqur1: abandoned/capped run surfacing)
  const [circuitBreaker, setCircuitBreaker] = useState<CircuitBreakerState | null>(null);

  // Local state
  const [search, setSearch] = useState('');
  const [filterMode, setFilterMode] = useState<FilterMode>('all');
  const [showFilterMenu, setShowFilterMenu] = useState(false);
  const [runningSingleRule, setRunningSingleRule] = useState<number | null>(null);

  // Modal states
  const [showRuleBuilder, setShowRuleBuilder] = useState(false);
  const [editingRule, setEditingRule] = useState<ChannelPipelineRule | null>(null);
  // Rule kind chosen when creating (epic ti939): null = chooser step shown.
  // When editing, the kind is derived from the rule's event_sync_config.
  const [createRuleKind, setCreateRuleKind] = useState<'standard' | 'event_sync' | null>(null);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState<ChannelPipelineRule | null>(null);
  const [showRollbackConfirm, setShowRollbackConfirm] = useState<ChannelPipelineExecution | null>(null);
  // Snapshot-restore state (ADR-010 §D5 / uc51o.7)
  const [showRevertConfirm, setShowRevertConfirm] = useState<ChannelPipelineExecution | null>(null);
  const [revertLoading, setRevertLoading] = useState(false);
  const [revertResult, setRevertResult] = useState<RestoreSnapshotResponse | null>(null);
  const [showImportDialog, setShowImportDialog] = useState(false);
  const [showExportDialog, setShowExportDialog] = useState(false);
  const [showExecutionDetails, setShowExecutionDetails] = useState<ChannelPipelineExecution | null>(null);
  const [executionDetails, setExecutionDetails] = useState<ChannelPipelineExecution | null>(null);
  const [executionDetailsLoading, setExecutionDetailsLoading] = useState(false);
  const [logSearch, setLogSearch] = useState('');
  const [logFilters, setLogFilters] = useState<Set<string>>(new Set());
  const [expandedLogEntries, setExpandedLogEntries] = useState<Set<number>>(new Set());

  // Import/Export state
  const [importYaml, setImportYaml] = useState('');
  const [exportYaml, setExportYaml] = useState('');
  const [importLoading, setImportLoading] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  const [importConflicts, setImportConflicts] = useState<string[]>([]);
  const [importNewCount, setImportNewCount] = useState(0);

  const notifications = useNotifications();

  /** Multi-select rules for bulk settings edit */
  const [selectedRuleIds, setSelectedRuleIds] = useState<Set<number>>(new Set());
  const [showBulkRuleModal, setShowBulkRuleModal] = useState(false);

  // Drag-and-drop state for rule reordering
  const dragRuleId = useRef<number | null>(null);
  const dragAllowed = useRef(false);
  const [dragOverIndex, setDragOverIndex] = useState<number | null>(null);

  // Responsive state
  const [isMobile, setIsMobile] = useState(window.innerWidth <= 768);

  const fetchCircuitBreaker = useCallback(async () => {
    try {
      const state = await channelPipelineApi.getCircuitBreakerState();
      setCircuitBreaker(state);
    } catch {
      // Non-fatal — banner is informational only; don't surface a toast
    }
  }, []);

  // Fetch rules, executions, and circuit-breaker state on mount
  useEffect(() => {
    fetchRules();
    fetchExecutions();
    fetchCircuitBreaker();
  }, [fetchRules, fetchExecutions, fetchCircuitBreaker]);

  // Handle responsive layout
  useEffect(() => {
    const handleResize = () => {
      setIsMobile(window.innerWidth <= 768);
    };
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  // Filter and sort rules
  const filteredRules = useMemo(() => {
    let result = [...rules];

    // Filter by enabled status
    if (filterMode === 'enabled') {
      result = result.filter(r => r.enabled);
    } else if (filterMode === 'disabled') {
      result = result.filter(r => !r.enabled);
    }

    // Filter by search
    if (search) {
      const searchLower = search.toLowerCase();
      result = result.filter(r =>
        r.name.toLowerCase().includes(searchLower) ||
        r.description?.toLowerCase().includes(searchLower)
      );
    }

    // Sort by priority
    result.sort((a, b) => a.priority - b.priority);

    return result;
  }, [rules, filterMode, search]);

  // Statistics
  const stats = useMemo(() => {
    const totalRules = rules.length;
    const enabledRules = rules.filter(r => r.enabled).length;
    const totalMatches = rules.reduce((sum, r) => sum + (r.match_count || 0), 0);
    return { totalRules, enabledRules, totalMatches };
  }, [rules]);

  // Check if any enabled rules exist
  const hasEnabledRules = useMemo(() => getEnabledRules().length > 0, [getEnabledRules]);

  /** Stable identity for bulk modal props — `Array.from(selectedRuleIds)` in JSX is a new array every render. */
  const bulkModalSelectedRuleIds = useMemo(
    () => Array.from(selectedRuleIds).sort((a, b) => a - b),
    [selectedRuleIds],
  );

  // Handlers
  const handleCreateRule = useCallback(() => {
    setEditingRule(null);
    setCreateRuleKind(null);
    setShowRuleBuilder(true);
  }, []);

  const handleEditRule = useCallback((rule: ChannelPipelineRule) => {
    setEditingRule(rule);
    setCreateRuleKind(null);
    setShowRuleBuilder(true);
  }, []);

  const handleSaveRule = useCallback(async (data: CreateRuleData) => {
    try {
      if (editingRule) {
        await updateRule(editingRule.id, data);
      } else {
        // New rule: assign priority = max existing + 1 (append at end)
        const maxPriority = rules.length > 0 ? Math.max(...rules.map(r => r.priority)) : -1;
        await createRule({ ...data, priority: maxPriority + 1 });
      }
      setShowRuleBuilder(false);
      setEditingRule(null);
      setCreateRuleKind(null);
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Failed to save rule', 'Channel Pipeline');
    }
  }, [editingRule, updateRule, createRule, rules, notifications]);

  const handleCancelRuleBuilder = useCallback(() => {
    setShowRuleBuilder(false);
    setEditingRule(null);
    setCreateRuleKind(null);
  }, []);

  const handleDeleteClick = useCallback((rule: ChannelPipelineRule) => {
    setShowDeleteConfirm(rule);
  }, []);

  const handleConfirmDelete = useCallback(async () => {
    if (showDeleteConfirm) {
      try {
        const deletedId = showDeleteConfirm.id;
        await deleteRule(deletedId);
        setSelectedRuleIds(prev => {
          const next = new Set(prev);
          next.delete(deletedId);
          return next;
        });
        setShowDeleteConfirm(null);
      } catch (err) {
        notifications.error(err instanceof Error ? err.message : 'Failed to delete rule', 'Channel Pipeline');
      }
    }
  }, [showDeleteConfirm, deleteRule, notifications]);

  const handleToggleEnabled = useCallback(async (rule: ChannelPipelineRule) => {
    try {
      await toggleRule(rule.id);
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Failed to toggle rule', 'Channel Pipeline');
    }
  }, [toggleRule, notifications]);

  const handleDuplicate = useCallback(async (rule: ChannelPipelineRule) => {
    try {
      await duplicateRule(rule.id);
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Failed to duplicate rule', 'Channel Pipeline');
    }
  }, [duplicateRule, notifications]);

  const selectAllCheckboxRef = useRef<HTMLInputElement>(null);

  const visibleSelectedCount = useMemo(
    () => filteredRules.filter(r => selectedRuleIds.has(r.id)).length,
    [filteredRules, selectedRuleIds],
  );

  useEffect(() => {
    const el = selectAllCheckboxRef.current;
    if (el) {
      el.indeterminate =
        visibleSelectedCount > 0 && visibleSelectedCount < filteredRules.length;
    }
  }, [visibleSelectedCount, filteredRules.length]);

  const toggleRuleSelected = useCallback((ruleId: number) => {
    setSelectedRuleIds(prev => {
      const next = new Set(prev);
      if (next.has(ruleId)) next.delete(ruleId);
      else next.add(ruleId);
      return next;
    });
  }, []);

  const toggleSelectAllVisible = useCallback(() => {
    setSelectedRuleIds(prev => {
      const ids = filteredRules.map(r => r.id);
      if (ids.length === 0) return prev;
      const allSelected = ids.every(id => prev.has(id));
      if (allSelected) {
        const next = new Set(prev);
        ids.forEach(id => next.delete(id));
        return next;
      }
      return new Set([...prev, ...ids]);
    });
  }, [filteredRules]);

  const handleBulkRuleSettingsApply = useCallback(
    async (ids: number[], patch: BulkUpdateRulesPatch) => {
      const n = ids.length;
      try {
        await bulkUpdateRules(ids, patch);
        setSelectedRuleIds(new Set());
        notifications.success(`Updated ${n} rule${n !== 1 ? 's' : ''}`, 'Channel Pipeline');
      } catch (err) {
        notifications.error(
          err instanceof Error ? err.message : 'Bulk update failed',
          'Channel Pipeline',
        );
        throw err;
      }
    },
    [bulkUpdateRules, notifications],
  );

  const handleDragStart = useCallback((e: React.DragEvent, ruleId: number) => {
    if (!dragAllowed.current) {
      e.preventDefault();
      return;
    }
    dragRuleId.current = ruleId;
    e.dataTransfer.effectAllowed = 'move';
    const row = (e.target as HTMLElement).closest('tr');
    if (row) row.classList.add('dragging');
  }, []);

  const handleHandleMouseDown = useCallback(() => {
    dragAllowed.current = true;
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent, index: number) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    setDragOverIndex(index);
  }, []);

  const handleDragEnd = useCallback(() => {
    dragRuleId.current = null;
    dragAllowed.current = false;
    setDragOverIndex(null);
    document.querySelectorAll('.rules-list tr.dragging').forEach(el => el.classList.remove('dragging'));
  }, []);

  const handleDrop = useCallback(async (e: React.DragEvent, toIndex: number) => {
    e.preventDefault();
    setDragOverIndex(null);
    const ruleId = dragRuleId.current;
    dragRuleId.current = null;
    document.querySelectorAll('.rules-list tr.dragging').forEach(el => el.classList.remove('dragging'));
    if (ruleId == null) return;
    const currentOrder = filteredRules.map(r => r.id);
    const fromIndex = currentOrder.indexOf(ruleId);
    if (fromIndex === -1 || fromIndex === toIndex) return;
    currentOrder.splice(fromIndex, 1);
    currentOrder.splice(toIndex, 0, ruleId);
    try {
      await reorderRules(currentOrder);
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Failed to reorder rules', 'Channel Pipeline');
    }
  }, [filteredRules, reorderRules, notifications]);

  const handleRun = useCallback(async (dryRun: boolean = false, ruleIds?: number[]) => {
    try {
      // bd-enfsy: runPipelineApi now polls the backend until the execution
      // reaches a terminal status, then resolves with the ChannelPipelineExecution
      // row. Orphan-reconciliation counters (channels_removed / channels_moved)
      // are derived from results during the run but not persisted on the row,
      // so the success message only quotes the persisted channels_created
      // figure now. Detail-level orphan counts are still visible via the
      // Execution History pane (which fetches the full execution).
      const response = await runPipelineApi({ dryRun, ruleIds });

      if (response) {
        const created = response.channels_created ?? 0;
        const status = response.status;
        const succeeded = status === 'completed';
        const msg = succeeded
          ? (dryRun
            ? `Dry run complete - Would create ${created} channel${created !== 1 ? 's' : ''}`
            : `Execution complete - Created ${created} channel${created !== 1 ? 's' : ''}`)
          : `Pipeline ${status}`;
        if (succeeded) {
          notifications.success(msg, 'Channel Pipeline');
          // Surface disabled-normalization-group warnings so the operator
          // notices that normalization silently applied nothing, even on an
          // otherwise-clean run (enhancedchannelmanager-e8p1h).
          if (response.warnings && response.warnings.length > 0) {
            const ruleNames = response.warnings.map(w => w.rule_name).join(', ');
            notifications.warning(
              `Normalization applied no changes: ${ruleNames} ` +
                `reference disabled normalization groups. Enable them under ` +
                `Settings > Normalization, then re-run.`,
              'Channel Pipeline',
            );
          }
        } else {
          notifications.error(
            response.error_message || msg,
            'Channel Pipeline',
          );
        }
        // Refresh executions list and rule stats (match counts). The hook
        // already refetches executions in its finally block, but rule stats
        // (last_run_at / match_count) live on a separate endpoint.
        await fetchExecutions();
        await fetchRules();
        // Notify other panes to refresh (channels/groups may have changed)
        if (!dryRun && succeeded) {
          window.dispatchEvent(new CustomEvent('channels-changed'));
        }
      }
      // If response is undefined, the hook caught an error and set executionsError
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Pipeline failed', 'Channel Pipeline');
    }
  }, [runPipelineApi, fetchExecutions, fetchRules, notifications]);

  const handleRunSingleRule = useCallback(async (ruleId: number, dryRun: boolean) => {
    setRunningSingleRule(ruleId);
    try {
      await handleRun(dryRun, [ruleId]);
    } finally {
      setRunningSingleRule(null);
    }
  }, [handleRun]);

  const handleRollbackClick = useCallback((execution: ChannelPipelineExecution) => {
    setShowRollbackConfirm(execution);
  }, []);

  const handleConfirmRollback = useCallback(async () => {
    if (showRollbackConfirm) {
      try {
        await rollback(showRollbackConfirm.id);
        setShowRollbackConfirm(null);
        await fetchExecutions();
      } catch (err) {
        notifications.error(err instanceof Error ? err.message : 'Failed to rollback', 'Channel Pipeline');
      }
    }
  }, [showRollbackConfirm, rollback, fetchExecutions, notifications]);

  // Snapshot-restore handlers (ADR-010 §D5/§D8, uc51o.7)
  const handleRevertClick = useCallback((execution: ChannelPipelineExecution) => {
    setRevertResult(null);
    setShowRevertConfirm(execution);
  }, []);

  const handleConfirmRevert = useCallback(async () => {
    if (!showRevertConfirm) return;
    setRevertLoading(true);
    try {
      const result = await channelPipelineApi.restoreChannelPipelineSnapshot(showRevertConfirm.id);
      setRevertResult(result);
      await fetchExecutions();
    } catch (err) {
      notifications.error(
        err instanceof Error ? err.message : 'Failed to restore snapshot',
        'Channel Pipeline',
      );
      setShowRevertConfirm(null);
    } finally {
      setRevertLoading(false);
    }
  }, [showRevertConfirm, fetchExecutions, notifications]);

  const handleViewDetails = useCallback(async (execution: ChannelPipelineExecution) => {
    setShowExecutionDetails(execution);
    setExecutionDetails(null);
    setLogSearch('');
    setLogFilters(new Set());
    setExpandedLogEntries(new Set());
    setExecutionDetailsLoading(true);
    try {
      const details = await channelPipelineApi.getExecutionDetails(execution.id);
      setExecutionDetails(details);
      // Pre-select all filter categories so user clicks to *remove*
      const allCats = new Set<string>();
      for (const entry of (details.execution_log || [])) {
        for (const action of entry.actions_executed) {
          if (!action.success) allCats.add('errors');
          const cat = getActionCategory(action);
          if (cat) allCats.add(cat);
        }
      }
      if (allCats.size > 1) setLogFilters(allCats);
    } catch {
      // Fall back to the basic execution data we already have
      setExecutionDetails(null);
    } finally {
      setExecutionDetailsLoading(false);
    }
  }, []);

  const handleExport = useCallback(async () => {
    try {
      const yaml = await channelPipelineApi.exportChannelPipelineRulesYAML();
      setExportYaml(yaml);
      setShowExportDialog(true);
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Failed to export rules', 'Channel Pipeline');
    }
  }, [notifications]);

  const [debugBundleLoading, setDebugBundleLoading] = useState(false);
  const handleDebugBundle = useCallback(async () => {
    setDebugBundleLoading(true);
    try {
      const { blob, filename } = await channelPipelineApi.generateAndFetchDebugBundle();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      notifications.success('Debug bundle downloaded', 'Channel Pipeline');
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Failed to generate debug bundle', 'Channel Pipeline');
    } finally {
      setDebugBundleLoading(false);
    }
  }, [notifications]);

  const handleImport = useCallback(async (overwrite = false) => {
    setImportLoading(true);
    setImportError(null);
    if (!overwrite) setImportConflicts([]);

    try {
      const result = await channelPipelineApi.importChannelPipelineRulesYAML(importYaml, overwrite);

      // Check for "already exists" conflicts when not overwriting
      const conflictErrors = result.errors.filter(e =>
        e.errors?.some((msg: string) => msg.includes('already exists'))
      );
      const otherErrors = result.errors.filter(e =>
        !e.errors?.some((msg: string) => msg.includes('already exists'))
      );

      if (!overwrite && conflictErrors.length > 0) {
        // Show confirmation with conflict list
        setImportConflicts(conflictErrors.map(e => e.rule_name));
        setImportNewCount(result.imported.length);
        if (otherErrors.length > 0) {
          setImportError(`${otherErrors.length} rule(s) had validation errors`);
        }
        return;
      }

      // Success
      const created = result.imported.filter(r => r.action === 'created').length;
      const updated = result.imported.filter(r => r.action === 'updated').length;
      const parts: string[] = [];
      if (created > 0) parts.push(`${created} created`);
      if (updated > 0) parts.push(`${updated} updated`);
      const summary = parts.length > 0 ? parts.join(', ') : 'No changes';

      await fetchRules();
      setImportYaml('');
      setImportConflicts([]);
      setImportNewCount(0);
      setShowImportDialog(false);

      if (otherErrors.length > 0) {
        notifications.warning(`${summary}. ${otherErrors.length} rule(s) had errors.`, 'Channel Pipeline');
      } else {
        notifications.success(`Imported rules: ${summary}`, 'Channel Pipeline');
      }
    } catch (err) {
      setImportError(err instanceof Error ? err.message : 'Failed to import rules');
    } finally {
      setImportLoading(false);
    }
  }, [importYaml, fetchRules, notifications]);

  const handleRetry = useCallback(() => {
    fetchRules();
    fetchExecutions();
  }, [fetchRules, fetchExecutions]);

  // Propagate hook errors to the toast
  useEffect(() => {
    if (executionsError) {
      notifications.error(executionsError, 'Channel Pipeline');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [executionsError]);

  // Error state
  if (rulesError && !rulesLoading) {
    return (
      <div className={`channel-pipeline-tab ${isMobile ? 'mobile' : ''}`} data-testid="channel-pipeline-tab">
        <div className="loading-state">
          <span className="material-icons">error</span>
          <p>Failed to load channel pipeline rules</p>
          <button className="btn-primary" onClick={handleRetry} aria-label="Retry">
            <span className="material-icons">refresh</span>
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className={`channel-pipeline-tab ${isMobile ? 'mobile' : ''}`} data-testid="channel-pipeline-tab">
      {/* Header */}
      <header className="tab-header">
        <h2>Channel Pipeline</h2>
        <div className="header-actions">
          <button
            className="btn-primary"
            onClick={handleCreateRule}
            aria-label="Create rule"
          >
            <span className="material-icons">add</span>
            Create Rule
          </button>
          <button
            className="btn-secondary"
            onClick={() => handleRun(false)}
            disabled={!hasEnabledRules || runningPipeline}
            aria-label="Run"
          >
            {runningPipeline ? (
              <>
                <span className="material-icons spinning">sync</span>
                Running...
              </>
            ) : (
              <>
                <span className="material-icons">play_arrow</span>
                Run
              </>
            )}
          </button>
          <button
            className="btn-secondary"
            onClick={() => handleRun(true)}
            disabled={!hasEnabledRules || runningPipeline}
            aria-label="Dry run"
          >
            <span className="material-icons">visibility</span>
            Dry Run
          </button>
          <button
            className="btn-secondary"
            onClick={() => setShowImportDialog(true)}
            aria-label="Import"
          >
            <span className="material-icons">upload</span>
            Import
          </button>
          <button
            className="btn-secondary"
            onClick={handleExport}
            aria-label="Export"
          >
            <span className="material-icons">download</span>
            Export
          </button>
          <button
            className="btn-secondary"
            onClick={handleDebugBundle}
            disabled={debugBundleLoading}
            aria-label="Debug Bundle"
          >
            <span className="material-icons">{debugBundleLoading ? 'hourglass_empty' : 'bug_report'}</span>
            {debugBundleLoading ? 'Generating...' : 'Debug Bundle'}
          </button>
        </div>
      </header>

      {/* Circuit-breaker banner — shown when run-on-refresh auto-fire is suppressed */}
      {circuitBreaker && (
        <CircuitBreakerBanner
          state={circuitBreaker}
          isAdmin={isAdmin}
          onReset={fetchCircuitBreaker}
        />
      )}

      {/* Statistics Summary */}
      <div className="channel-pipeline-stats">
        <div className="stat-item">
          <span className="stat-value">{stats.totalRules}</span>
          <span className="stat-label">{stats.totalRules === 1 ? 'Rule' : 'Rules'}</span>
        </div>
        <div className="stat-item">
          <span className="stat-value">{stats.enabledRules}</span>
          <span className="stat-label">Enabled</span>
        </div>
        <div className="stat-item">
          <span className="stat-value">{stats.totalMatches}</span>
          <span className="stat-label">Matches</span>
        </div>
      </div>

      {/* Main Content */}
      <div className="channel-pipeline-content">
        {/* Rules Section */}
        <section className="rules-section">
          <div className="section-header">
            <h3>Rules</h3>
            <div className="section-controls">
              <button
                type="button"
                className="btn-secondary rules-bulk-edit-btn"
                disabled={selectedRuleIds.size === 0 || rulesLoading}
                onClick={() => setShowBulkRuleModal(true)}
                title={selectedRuleIds.size === 0 ? 'Select one or more rules' : 'Edit settings for selected rules'}
                aria-label="Bulk edit selected rules"
              >
                <span className="material-icons">tune</span>
                Bulk edit
                {selectedRuleIds.size > 0 ? ` (${selectedRuleIds.size})` : ''}
              </button>
              <input
                type="text"
                className="search-input"
                placeholder="Search rules..."
                value={search}
                onChange={e => setSearch(e.target.value)}
                aria-label="Search rules"
              />
              <div className="filter-wrapper">
                <button
                  className="action-btn"
                  onClick={() => setShowFilterMenu(!showFilterMenu)}
                  aria-label="Filter"
                  aria-expanded={showFilterMenu}
                >
                  <span className="material-icons">filter_list</span>
                </button>
                {showFilterMenu && (
                  <div className="filter-menu">
                    <button
                      className={filterMode === 'all' ? 'active' : ''}
                      onClick={() => { setFilterMode('all'); setShowFilterMenu(false); }}
                    >
                      All
                    </button>
                    <button
                      className={filterMode === 'enabled' ? 'active' : ''}
                      onClick={() => { setFilterMode('enabled'); setShowFilterMenu(false); }}
                    >
                      Enabled Only
                    </button>
                    <button
                      className={filterMode === 'disabled' ? 'active' : ''}
                      onClick={() => { setFilterMode('disabled'); setShowFilterMenu(false); }}
                    >
                      Disabled Only
                    </button>
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Rules List */}
          {rulesLoading ? (
            <div className="rules-skeleton" data-testid="rules-skeleton">
              {[1, 2, 3].map(i => (
                <div key={i} className="skeleton-row" />
              ))}
            </div>
          ) : filteredRules.length === 0 ? (
            <div className="empty-state">
              <span className="material-icons">rule</span>
              <p>No rules found</p>
              <button className="btn-primary" onClick={handleCreateRule}>
                Create your first rule
              </button>
            </div>
          ) : (
            <div className="rules-list" data-testid="rules-list">
              <table>
                <thead>
                  <tr>
                    <th className="col-select" scope="col">
                      <input
                        ref={selectAllCheckboxRef}
                        type="checkbox"
                        checked={
                          filteredRules.length > 0 && visibleSelectedCount === filteredRules.length
                        }
                        onChange={toggleSelectAllVisible}
                        aria-label="Select all visible rules"
                        title="Select all visible rules"
                      />
                    </th>
                    <th className="col-drag"></th>
                    <th className="col-name">Name</th>
                    <th className="col-priority">Priority</th>
                    <th className="col-status">Status</th>
                    <th className="col-matches">Matches</th>
                    <th className="col-actions">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredRules.map((rule, index) => (
                    <tr
                      key={rule.id}
                      data-testid="rule-row"
                      tabIndex={0}
                      draggable
                      className={`${dragOverIndex === index ? 'drag-over' : ''} ${selectedRuleIds.has(rule.id) ? 'selected' : ''}`.trim()}
                      onDragStart={(e) => handleDragStart(e, rule.id)}
                      onDragEnd={handleDragEnd}
                      onDragOver={(e) => handleDragOver(e, index)}
                      onDrop={(e) => handleDrop(e, index)}
                    >
                      <td
                        className="col-select"
                        onMouseDown={(e) => e.stopPropagation()}
                        onClick={(e) => e.stopPropagation()}
                      >
                        <input
                          type="checkbox"
                          checked={selectedRuleIds.has(rule.id)}
                          onChange={() => toggleRuleSelected(rule.id)}
                          aria-label={`Select ${rule.name}`}
                        />
                      </td>
                      <td className="col-drag">
                        <span
                          className="drag-handle"
                          data-testid="drag-handle"
                          onMouseDown={handleHandleMouseDown}
                        >
                          <span className="material-icons">drag_indicator</span>
                        </span>
                      </td>
                      <td className="col-name">
                        <div className="rule-name">
                          {rule.name}
                          {rule.event_sync_config && (
                            <span
                              className="badge badge-sm badge-info rule-kind-badge"
                              title="Event Sync rule — attaches streams on MANUAL pipeline runs only (never on unattended refresh); attaches are journaled and reversible via rollback"
                            >
                              Event Sync
                            </span>
                          )}
                        </div>
                        {rule.description && (
                          <div className="rule-description">{rule.description}</div>
                        )}
                      </td>
                      <td className="col-priority">{index + 1}</td>
                      <td className="col-status">
                        <span className={getStatusBadgeClass(rule.enabled ? 'enabled' : 'disabled')}>
                          {rule.enabled ? 'Enabled' : 'Disabled'}
                        </span>
                      </td>
                      <td className="col-matches">{rule.match_count || 0}</td>
                      <td className="col-actions">
                        <div className="rule-actions-row">
                          {/* event_sync rules hide the per-rule Run/Test
                              icons — they execute via the pipeline-level
                              manual Run or the single-rule API; the preview
                              lives inside the editor. */}
                          {!rule.event_sync_config && (
                            <>
                              <button
                                className="action-btn"
                                onClick={() => handleRunSingleRule(rule.id, false)}
                                disabled={runningSingleRule === rule.id || runningPipeline}
                                aria-label={`Run ${rule.name}`}
                                title="Run rule"
                              >
                                <span className={`material-icons ${runningSingleRule === rule.id ? 'spinning' : ''}`}>
                                  {runningSingleRule === rule.id ? 'sync' : 'play_arrow'}
                                </span>
                              </button>
                              <button
                                className="action-btn"
                                onClick={() => handleRunSingleRule(rule.id, true)}
                                disabled={runningSingleRule === rule.id || runningPipeline}
                                aria-label={`Test ${rule.name}`}
                                title="Test (dry run)"
                              >
                                <span className="material-icons">visibility</span>
                              </button>
                            </>
                          )}
                          <button
                            className="action-btn"
                            onClick={() => handleToggleEnabled(rule)}
                            aria-label={`Toggle ${rule.name} enabled`}
                            title={rule.enabled ? 'Disable' : 'Enable'}
                          >
                            <span className="material-icons">
                              {rule.enabled ? 'toggle_on' : 'toggle_off'}
                            </span>
                          </button>
                          <button
                            className="action-btn"
                            onClick={() => handleEditRule(rule)}
                            aria-label="Edit"
                            title="Edit"
                          >
                            <span className="material-icons">edit</span>
                          </button>
                          <button
                            className="action-btn"
                            onClick={() => handleDuplicate(rule)}
                            aria-label="Duplicate"
                            title="Duplicate"
                          >
                            <span className="material-icons">content_copy</span>
                          </button>
                          <button
                            className="action-btn danger"
                            onClick={() => handleDeleteClick(rule)}
                            aria-label="Delete"
                            title="Delete"
                          >
                            <span className="material-icons">delete</span>
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {/* Execution History Section */}
        <section className="execution-section">
          <div className="section-header">
            <h3>Execution History</h3>
          </div>

          {executionsLoading && executions.length === 0 ? (
            <div className="executions-loading">
              <span className="material-icons spinning">sync</span>
              Loading...
            </div>
          ) : !runningPipeline && executions.length === 0 ? (
            <div className="empty-state small">
              <span className="material-icons">history</span>
              <p>No executions yet</p>
            </div>
          ) : (
            <div className="executions-list" data-testid="executions-list">
              {runningPipeline && (
                <div className="execution-item execution-running" data-testid="execution-running">
                  <div className="execution-info">
                    <span className={getStatusBadgeClass('running')}>
                      <span className="material-icons spinning" style={{ fontSize: '12px', marginRight: '4px' }}>sync</span>
                      Running
                    </span>
                    <span className="execution-mode">
                      {runningSingleRule ? 'Single Rule' : 'Pipeline'}
                    </span>
                    <span className="execution-date">
                      {new Date().toLocaleString(getDateLocale())}
                    </span>
                    <span className="execution-stats">
                      Processing...
                    </span>
                  </div>
                </div>
              )}
              {executions.slice(0, runningPipeline ? 4 : 5).map(execution => (
                <div key={execution.id} className="execution-item" data-testid="execution-item">
                  <div className="execution-info">
                    <span className={getStatusBadgeClass(execution.status)}>
                      {EXECUTION_STATUS_LABEL[execution.status] ?? execution.status}
                    </span>
                    <span className="execution-mode">
                      {execution.mode === 'dry_run' ? 'Dry Run' : 'Execute'}
                    </span>
                    <span className="execution-date">
                      {new Date(execution.started_at).toLocaleString(getDateLocale())}
                    </span>
                    <span className="execution-stats">
                      {execution.streams_matched} matched
                      {execution.channels_updated > 0 && `, ${execution.channels_updated} merged`}
                      {execution.channels_created > 0 && `, ${execution.channels_created} created`}
                      {execution.streams_skipped > 0 && `, ${execution.streams_skipped} skipped`}
                      {execution.streams_excluded > 0 && `, ${execution.streams_excluded} excluded`}
                    </span>
                  </div>
                  <div className="execution-actions">
                    <button
                      className="action-btn"
                      onClick={() => handleViewDetails(execution)}
                      aria-label="View details"
                      title="View details"
                    >
                      <span className="material-icons">info</span>
                    </button>
                    {execution.status === 'completed' && execution.mode === 'execute' && (
                      <button
                        className="action-btn"
                        onClick={() => handleRollbackClick(execution)}
                        aria-label="Rollback"
                        title="Rollback"
                      >
                        <span className="material-icons">undo</span>
                      </button>
                    )}
                    {/* Snapshot-restore affordance (ADR-010 uc51o.7): shown only
                        when has_snapshot=true; hidden for dry runs, legacy runs,
                        and already-reverted executions so the operator always
                        knows what will happen. */}
                    {execution.has_snapshot && execution.status === 'completed' && execution.mode === 'execute' && (
                      <button
                        className="action-btn action-btn-revert"
                        onClick={() => handleRevertClick(execution)}
                        aria-label="Undo this run"
                        title="Undo this run — restores pre-run channel state from snapshot"
                        data-testid="revert-btn"
                      >
                        <span className="material-icons">settings_backup_restore</span>
                      </button>
                    )}
                    {!execution.has_snapshot && execution.mode === 'execute' && execution.status === 'completed' && (
                      <span
                        className="execution-no-snapshot"
                        title="No pre-run snapshot — only legacy rollback is available for this run"
                        data-testid="no-snapshot-indicator"
                      >
                        <span className="material-icons">history_toggle_off</span>
                      </span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>

      {/* Bulk rule settings */}
      <BulkRuleSettingsModal
        isOpen={showBulkRuleModal}
        onClose={() => setShowBulkRuleModal(false)}
        selectedRuleIds={bulkModalSelectedRuleIds}
        rules={rules}
        onApply={handleBulkRuleSettingsApply}
      />

      {/* Rule Builder Modal */}
      {showRuleBuilder && (() => {
        // Editing derives the kind from the rule; creating uses the chooser.
        const isEventSync = editingRule
          ? Boolean(editingRule.event_sync_config)
          : createRuleKind === 'event_sync';
        const showKindChooser = !editingRule && createRuleKind === null;
        const title = showKindChooser
          ? 'Create Rule'
          : `${editingRule ? 'Edit' : 'Create'}${isEventSync ? ' Event Sync' : ''} Rule`;
        return (
          <ModalOverlay onClose={handleCancelRuleBuilder} role="dialog" aria-modal="true" aria-labelledby="rule-builder-title">
            <div className="modal-container modal-lg rule-builder-modal">
              <div className="modal-header">
                <h2 id="rule-builder-title">{title}</h2>
                <button
                  className="modal-close-btn"
                  onClick={handleCancelRuleBuilder}
                  aria-label="Close"
                >
                  <span className="material-icons">close</span>
                </button>
              </div>
              {showKindChooser ? (
                <div className="rule-kind-chooser" data-testid="rule-kind-chooser">
                  <button
                    type="button"
                    className="rule-kind-option"
                    onClick={() => setCreateRuleKind('standard')}
                  >
                    <span className="material-icons" aria-hidden="true">rule</span>
                    <span className="rule-kind-option-text">
                      <strong>Standard rule</strong>
                      <span>Conditions + actions: create channels, merge streams, sort, assign.</span>
                    </span>
                  </button>
                  <button
                    type="button"
                    className="rule-kind-option"
                    onClick={() => setCreateRuleKind('event_sync')}
                  >
                    <span className="material-icons" aria-hidden="true">sync_alt</span>
                    <span className="rule-kind-option-text">
                      <strong>Event Sync rule</strong>
                      <span>
                        One channel per live event across providers — match
                        secondary streams to a master group&apos;s channels.
                        Preview first; manual runs attach.
                      </span>
                    </span>
                  </button>
                </div>
              ) : isEventSync ? (
                <EventSyncRuleEditor
                  rule={editingRule || undefined}
                  onSave={handleSaveRule}
                  onCancel={handleCancelRuleBuilder}
                />
              ) : (
                <RuleBuilder
                  rule={editingRule || undefined}
                  onSave={handleSaveRule}
                  onCancel={handleCancelRuleBuilder}
                />
              )}
            </div>
          </ModalOverlay>
        );
      })()}

      {/* Delete Confirmation Dialog */}
      {showDeleteConfirm && (
        <ModalOverlay onClose={() => setShowDeleteConfirm(null)} role="dialog" aria-modal="true">
          <div className="modal-container modal-sm">
            <div className="modal-header">
              <h2>Confirm Delete</h2>
            </div>
            <div className="modal-body">
              <p>Are you sure you want to delete &quot;{showDeleteConfirm.name}&quot;?</p>
            </div>
            <div className="modal-footer">
              <button
                className="btn-secondary"
                onClick={() => setShowDeleteConfirm(null)}
              >
                Cancel
              </button>
              <button
                className="btn-danger"
                onClick={handleConfirmDelete}
                aria-label="Confirm"
              >
                Delete
              </button>
            </div>
          </div>
        </ModalOverlay>
      )}

      {/* Rollback Confirmation Dialog */}
      {showRollbackConfirm && (
        <ModalOverlay onClose={() => setShowRollbackConfirm(null)} role="dialog" aria-modal="true">
          <div className="modal-container modal-sm">
            <div className="modal-header">
              <h2>Confirm Rollback</h2>
            </div>
            <div className="modal-body">
              <p>
                Are you sure you want to rollback this execution?
                This will delete {showRollbackConfirm.channels_created} created channels.
              </p>
            </div>
            <div className="modal-footer">
              <button
                className="btn-secondary"
                onClick={() => setShowRollbackConfirm(null)}
              >
                Cancel
              </button>
              <button
                className="btn-danger"
                onClick={handleConfirmRollback}
                aria-label="Confirm"
              >
                Rollback
              </button>
            </div>
          </div>
        </ModalOverlay>
      )}

      {/* Snapshot-Restore Confirmation Dialog (ADR-010 §D5 mandatory warning, uc51o.7) */}
      {showRevertConfirm && !revertResult && (
        <ModalOverlay
          onClose={() => { setShowRevertConfirm(null); setRevertResult(null); }}
          role="dialog"
          aria-modal="true"
          aria-labelledby="revert-confirm-title"
        >
          <div className="modal-container modal-sm">
            <div className="modal-header">
              <h2 id="revert-confirm-title">Undo This Run</h2>
            </div>
            <div className="modal-body">
              <div className="revert-warning-banner" data-testid="revert-warning">
                <span className="material-icons revert-warning-icon">warning</span>
                <p>
                  <strong>This will overwrite the current stream assignments</strong> of all
                  channels with the state captured before this run on{' '}
                  <strong>{new Date(showRevertConfirm.started_at).toLocaleString(getDateLocale())}</strong>.
                </p>
              </div>
              <p className="revert-warning-detail">
                Any changes made since that snapshot — manual edits, automatic merges, or
                Dispatcharr updates — <strong>will be lost</strong>. This cannot be undone.
              </p>
            </div>
            <div className="modal-footer">
              <button
                className="btn-secondary"
                onClick={() => { setShowRevertConfirm(null); setRevertResult(null); }}
                disabled={revertLoading}
              >
                Cancel
              </button>
              <button
                className="btn-danger"
                onClick={handleConfirmRevert}
                disabled={revertLoading}
                aria-label="Confirm revert"
                data-testid="revert-confirm-btn"
              >
                {revertLoading ? (
                  <>
                    <span className="material-icons spinning" style={{ fontSize: '16px', marginRight: '4px' }}>sync</span>
                    Reverting...
                  </>
                ) : (
                  'Undo This Run'
                )}
              </button>
            </div>
          </div>
        </ModalOverlay>
      )}

      {/* Snapshot-Restore Result Summary (ADR-010 §D8 step 6 — partial failures never silent) */}
      {showRevertConfirm && revertResult && (
        <ModalOverlay
          onClose={() => { setShowRevertConfirm(null); setRevertResult(null); }}
          role="dialog"
          aria-modal="true"
          aria-labelledby="revert-result-title"
        >
          <div className="modal-container modal-sm">
            <div className="modal-header">
              <h2 id="revert-result-title">Revert Complete</h2>
            </div>
            <div className="modal-body">
              {/* Partial-failure warning: never show as plain success when channels failed */}
              {revertResult.failed_channels.length > 0 && (
                <div className="revert-partial-failure" data-testid="revert-partial-failure">
                  <span className="material-icons revert-warning-icon">warning</span>
                  <p>
                    Revert completed with <strong>{revertResult.failed_channels.length} failure{revertResult.failed_channels.length !== 1 ? 's' : ''}</strong>.
                    The channels listed below could not be restored.
                  </p>
                </div>
              )}
              <div className="revert-result-stats" data-testid="revert-result-stats">
                <div className="detail-row">
                  <span className="detail-label">Channels restored:</span>
                  <span data-testid="revert-restored-count">{revertResult.restored_channels}</span>
                </div>
                {revertResult.removed_channels > 0 && (
                  <div className="detail-row">
                    <span className="detail-label">Created channels removed:</span>
                    <span data-testid="revert-removed-count">{revertResult.removed_channels}</span>
                  </div>
                )}
                {revertResult.failed_channels.length > 0 && (
                  <div className="detail-row">
                    <span className="detail-label">Failed:</span>
                    <span className="revert-failed-count" data-testid="revert-failed-count">
                      {revertResult.failed_channels.length}
                    </span>
                  </div>
                )}
              </div>
              {revertResult.failed_channels.length > 0 && (
                <div className="revert-failed-channels" data-testid="revert-failed-channels">
                  <h4 className="revert-failed-title">Failed channels</h4>
                  <ul className="revert-failed-list">
                    {revertResult.failed_channels.map((ch: FailedChannel) => (
                      <li key={ch.id} className="revert-failed-item">
                        <span className="revert-failed-name">{ch.name}</span>
                        <span className="revert-failed-error">{ch.error}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
            <div className="modal-footer">
              <button
                className="btn-primary"
                onClick={() => { setShowRevertConfirm(null); setRevertResult(null); }}
              >
                Close
              </button>
            </div>
          </div>
        </ModalOverlay>
      )}

      {/* Execution Details Dialog */}
      {showExecutionDetails && (() => {
        const details = executionDetails || showExecutionDetails;
        const log = details.execution_log || [];

        // Categorize each entry by its action types for filtering
        const getEntryCategories = (entry: ExecutionLogEntry): Set<string> => {
          const cats = new Set<string>();
          for (const action of entry.actions_executed) {
            if (!action.success) cats.add('errors');
            const cat = getActionCategory(action);
            if (cat) cats.add(cat);
          }
          return cats;
        };

        // Count entries per filter category
        const filterCounts: Record<string, number> = {};
        for (const entry of log) {
          const cats = getEntryCategories(entry);
          cats.forEach(c => { filterCounts[c] = (filterCounts[c] || 0) + 1; });
        }

        const filterDefs: { key: string; label: string; icon: string }[] = [
          { key: 'created', label: 'Created', icon: 'add_circle' },
          { key: 'merged', label: 'Merged', icon: 'merge' },
          { key: 'updated', label: 'Updated', icon: 'edit' },
          { key: 'removed', label: 'Removed', icon: 'link_off' },
          { key: 'excluded', label: 'Excluded', icon: 'block' },
          { key: 'skipped', label: 'Skipped', icon: 'skip_next' },
          { key: 'assigned', label: 'Assigned', icon: 'label' },
          { key: 'errors', label: 'Errors', icon: 'error' },
        ];

        const activeFilters = filterDefs.filter(f => filterCounts[f.key] > 0);

        const toggleFilter = (key: string) => {
          setLogFilters(prev => {
            const next = new Set(prev);
            if (next.has(key)) next.delete(key);
            else next.add(key);
            return next;
          });
        };

        const filteredLog = log.filter((entry: ExecutionLogEntry) => {
          // Text search filter
          if (logSearch && !entry.stream_name.toLowerCase().includes(logSearch.toLowerCase())) {
            return false;
          }
          // Action type filters (OR logic: show if entry matches any active filter)
          if (logFilters.size > 0) {
            const cats = getEntryCategories(entry);
            let matchesFilter = false;
            logFilters.forEach(f => { if (cats.has(f)) matchesFilter = true; });
            if (!matchesFilter) return false;
          }
          return true;
        });

        const toggleLogEntry = (streamId: number) => {
          setExpandedLogEntries(prev => {
            const next = new Set(prev);
            if (next.has(streamId)) next.delete(streamId);
            else next.add(streamId);
            return next;
          });
        };

        return (
        <ModalOverlay onClose={() => setShowExecutionDetails(null)} role="dialog" aria-modal="true">
          <div className="modal-container modal-lg">
            <div className="modal-header">
              <h2>Execution Details</h2>
              <button
                className="modal-close-btn"
                onClick={() => setShowExecutionDetails(null)}
                aria-label="Close"
              >
                <span className="material-icons">close</span>
              </button>
            </div>
            <div className="modal-body">
              {/* Summary Section */}
              <div className="detail-row">
                <span className="detail-label">Status:</span>
                <span className={getStatusBadgeClass(details.status)}>
                  {details.status}
                </span>
              </div>
              <div className="detail-row">
                <span className="detail-label">Mode:</span>
                <span>{details.mode === 'dry_run' ? 'Dry Run' : 'Execute'}</span>
              </div>
              <div className="detail-row">
                <span className="detail-label">Started:</span>
                <span>{new Date(details.started_at).toLocaleString(getDateLocale())}</span>
              </div>
              {details.completed_at && (
                <div className="detail-row">
                  <span className="detail-label">Completed:</span>
                  <span>{new Date(details.completed_at).toLocaleString(getDateLocale())}</span>
                </div>
              )}
              {details.duration_seconds != null && (
                <div className="detail-row">
                  <span className="detail-label">Duration:</span>
                  <span>{details.duration_seconds.toFixed(1)}s</span>
                </div>
              )}
              <div className="detail-row">
                <span className="detail-label">Streams Evaluated:</span>
                <span>{details.streams_evaluated}</span>
              </div>
              <div className="detail-row">
                <span className="detail-label">Streams Matched:</span>
                <span>{details.streams_matched}</span>
              </div>
              <div className="detail-row">
                <span className="detail-label">Channels Created:</span>
                <span>{details.channels_created}</span>
              </div>
              {details.channels_updated > 0 && (
                <div className="detail-row">
                  <span className="detail-label">Channels Updated:</span>
                  <span>{details.channels_updated}</span>
                </div>
              )}
              {details.groups_created > 0 && (
                <div className="detail-row">
                  <span className="detail-label">Groups Created:</span>
                  <span>{details.groups_created}</span>
                </div>
              )}
              {details.streams_excluded > 0 && (
                <div className="detail-row">
                  <span className="detail-label">Streams Excluded:</span>
                  <span>{details.streams_excluded}</span>
                </div>
              )}
              {details.error_message && (
                <div className="detail-row error">
                  <span className="detail-label">Error:</span>
                  <span>{details.error_message}</span>
                </div>
              )}

              {/* Disabled-normalization-group warning (enhancedchannelmanager-e8p1h).
                  Surfaced prominently because these rules silently normalize
                  nothing — the run looks clean but names never get cleaned up. */}
              {details.warnings && details.warnings.length > 0 && (
                <div className="norm-warning-banner" role="alert">
                  <span className="material-icons norm-warning-icon">warning</span>
                  <div className="norm-warning-content">
                    <p className="norm-warning-title">
                      Normalization applied no changes — disabled groups referenced
                    </p>
                    <p className="norm-warning-detail">
                      The rule{details.warnings.length > 1 ? 's' : ''} below
                      reference normalization groups that are disabled or no longer
                      exist, so stream names were not normalized and
                      merge-into-channel matching likely missed most streams.
                      Enable the listed group(s) under Settings &gt; Normalization,
                      then re-run.
                    </p>
                    <ul className="norm-warning-list">
                      {details.warnings.map(w => (
                        <li key={w.rule_id}>
                          <strong>{w.rule_name}</strong>
                          {' → '}
                          {w.disabled_groups
                            .map(g =>
                              g.missing
                                ? `#${g.id} (missing)`
                                : (g.name ?? `#${g.id}`),
                            )
                            .join(', ')}
                        </li>
                      ))}
                    </ul>
                  </div>
                </div>
              )}

              {/* Execution Log Section */}
              <div className="execution-log-section">
                <div className="execution-log-header">
                  <h3>Execution Log</h3>
                  {log.length > 0 && (
                    <span className="log-count">
                      {filteredLog.length === log.length
                        ? `${log.length} matched streams`
                        : `${filteredLog.length} of ${log.length} matched streams`}
                    </span>
                  )}
                </div>

                {executionDetailsLoading ? (
                  <div className="log-loading">
                    <span className="material-icons spinning">sync</span>
                    Loading execution log...
                  </div>
                ) : log.length === 0 ? (
                  <div className="log-empty">
                    No execution log available for this run.
                  </div>
                ) : (
                  <>
                    {log.length > 3 && (
                      <div className="log-search-bar">
                        <span className="material-icons">search</span>
                        <input
                          type="text"
                          placeholder="Search streams..."
                          value={logSearch}
                          onChange={e => setLogSearch(e.target.value)}
                          className="log-search-input"
                        />
                        {logSearch && (
                          <button className="log-search-clear" onClick={() => setLogSearch('')} aria-label="Clear search" title="Clear search">
                            <span className="material-icons" aria-hidden="true">close</span>
                          </button>
                        )}
                      </div>
                    )}

                    {activeFilters.length > 1 && (
                      <div className="log-filter-chips">
                        {activeFilters.map(f => (
                          <button
                            key={f.key}
                            className={`log-filter-chip ${logFilters.has(f.key) ? 'active' : ''} ${f.key === 'errors' ? 'chip-errors' : ''}`}
                            onClick={() => toggleFilter(f.key)}
                          >
                            <span className="material-icons">{f.icon}</span>
                            {f.label}
                            <span className="log-filter-count">{filterCounts[f.key]}</span>
                          </button>
                        ))}
                        {logFilters.size > 0 && (
                          <button
                            className="log-filter-clear"
                            onClick={() => setLogFilters(new Set())}
                          >
                            Clear
                          </button>
                        )}
                      </div>
                    )}

                    <div className="log-entries">
                      {filteredLog.map((entry: ExecutionLogEntry) => {
                        const isExpanded = expandedLogEntries.has(entry.stream_id);
                        const rulesEvaluated = entry.rules_evaluated ?? [];
                        const actionsExecuted = entry.actions_executed ?? [];
                        const winnerRule = rulesEvaluated.find(r => r.was_winner);
                        const actionCount = actionsExecuted.length;
                        const hasErrors = actionsExecuted.some(a => !a.success);

                        return (
                          <div key={entry.stream_id} className={`log-entry ${isExpanded ? 'expanded' : ''}`}>
                            <div
                              className="log-entry-header"
                              onClick={() => toggleLogEntry(entry.stream_id)}
                              role="button"
                              tabIndex={0}
                              aria-expanded={isExpanded}
                              onKeyDown={(e) => {
                                if (e.key === 'Enter' || e.key === ' ') {
                                  e.preventDefault();
                                  toggleLogEntry(entry.stream_id);
                                }
                              }}
                            >
                              <span className="material-icons log-chevron">
                                {isExpanded ? 'expand_more' : 'chevron_right'}
                              </span>
                              <span className="log-stream-name">{entry.stream_name}</span>
                              <span className="log-entry-meta">
                                {winnerRule && (
                                  <span className="log-rule-badge">{winnerRule.rule_name}</span>
                                )}
                                <span className={`log-action-count ${hasErrors ? 'has-errors' : ''}`}>
                                  {actionCount} action{actionCount !== 1 ? 's' : ''}
                                </span>
                              </span>
                            </div>

                            {isExpanded && (
                              <div className="log-entry-body">
                                {/* Condition evaluations */}
                                {rulesEvaluated
                                  .filter(r => r.matched || r.conditions.length > 0)
                                  .map((rule, ri) => (
                                  <div key={ri} className="log-rule-section">
                                    <div className="log-rule-title">
                                      <span className={`material-icons ${rule.matched ? 'condition-pass' : 'condition-fail'}`}>
                                        {rule.matched ? 'check_circle' : 'cancel'}
                                      </span>
                                      <span>{rule.rule_name}</span>
                                      {rule.was_winner && <span className="log-winner-badge">winner</span>}
                                    </div>
                                    <div className="log-conditions">
                                      {rule.conditions.map((cond, ci) => (
                                        <div key={ci}>
                                          {ci > 0 && cond.connector && (
                                            <div className="log-condition-connector">
                                              <span className={`log-connector-label ${cond.connector === 'or' ? 'connector-or' : ''}`}>
                                                {(cond.connector || 'and').toUpperCase()}
                                              </span>
                                            </div>
                                          )}
                                          <div className={`log-condition ${cond.matched ? 'pass' : 'fail'}`}>
                                            <span className={`material-icons condition-icon ${cond.matched ? 'condition-pass' : 'condition-fail'}`}>
                                              {cond.matched ? 'check' : 'close'}
                                            </span>
                                            <span className="log-condition-type">{cond.type}</span>
                                            {cond.value && <span className="log-condition-value">= "{cond.value}"</span>}
                                            {cond.details && <span className="log-condition-details">{cond.details}</span>}
                                          </div>
                                        </div>
                                      ))}
                                    </div>
                                  </div>
                                ))}

                                {/* Actions executed */}
                                {entry.actions_executed.length > 0 && (
                                  <div className="log-actions-section">
                                    <div className="log-actions-title">Actions</div>
                                    {entry.actions_executed.map((action, ai) => {
                                      const isSkip = action.description?.toLowerCase().includes('skipped');
                                      const isStop = action.type === 'stop_processing';
                                      const iconClass = !action.success ? 'action-error'
                                        : isStop ? 'action-stop'
                                        : isSkip ? 'action-skipped'
                                        : 'action-success';
                                      const icon = !action.success ? 'error'
                                        : isStop ? 'stop_circle'
                                        : isSkip ? 'skip_next'
                                        : 'check_circle';
                                      return (
                                        <div key={ai} className={`log-action ${iconClass}`}>
                                          <span className={`material-icons action-icon ${iconClass}`}>
                                            {icon}
                                          </span>
                                          <span className="log-action-desc">{action.description}</span>
                                          {action.error && <span className="log-action-error-msg">{action.error}</span>}
                                          {action.details && action.details.length > 0 && (
                                            <div className="log-action-details">
                                              {action.details.map((detail, di) => {
                                                const isTip = detail.includes('To create separate channels');
                                                return (
                                                  <span key={di} className={`log-action-detail ${isTip ? 'log-action-tip' : ''}`}>
                                                    {isTip && <span className="material-icons tip-icon">lightbulb</span>}
                                                    {detail}
                                                  </span>
                                                );
                                              })}
                                            </div>
                                          )}
                                        </div>
                                      );
                                    })}
                                  </div>
                                )}
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </>
                )}
              </div>
            </div>
          </div>
        </ModalOverlay>
        );
      })()}

      {/* Import Dialog */}
      {showImportDialog && (
        <ModalOverlay onClose={() => setShowImportDialog(false)} role="dialog" aria-modal="true">
          <div className="modal-container modal-md">
            <div className="modal-header">
              <h2>Import Rules</h2>
              <button
                className="modal-close-btn"
                onClick={() => { setShowImportDialog(false); setImportConflicts([]); setImportNewCount(0); }}
                aria-label="Close"
              >
                <span className="material-icons">close</span>
              </button>
            </div>
            <div className="modal-body">
              <div className="modal-form-group">
                <label htmlFor="import-yaml">YAML Content</label>
                <textarea
                  id="import-yaml"
                  value={importYaml}
                  onChange={e => { setImportYaml(e.target.value); setImportConflicts([]); setImportNewCount(0); }}
                  placeholder="Paste YAML content here..."
                  rows={10}
                  aria-label="YAML content"
                />
              </div>
              {importConflicts.length > 0 && (
                <div className="import-conflicts">
                  <div className="import-conflicts-header">
                    <span className="material-icons" style={{ color: 'var(--warning)', fontSize: '18px', verticalAlign: 'middle' }}>warning</span>
                    {' '}{importConflicts.length} rule{importConflicts.length !== 1 ? 's' : ''} already exist{importConflicts.length === 1 ? 's' : ''}
                    {importNewCount > 0 && ` (${importNewCount} new rule${importNewCount !== 1 ? 's' : ''} imported successfully)`}
                  </div>
                  <ul className="import-conflicts-list">
                    {importConflicts.map(name => (
                      <li key={name}>{name}</li>
                    ))}
                  </ul>
                  <p className="import-conflicts-prompt">Import again to overwrite these rules?</p>
                </div>
              )}
              {importError && (
                <div className="import-error">{importError}</div>
              )}
            </div>
            <div className="modal-footer">
              <button
                className="btn-secondary"
                onClick={() => { setShowImportDialog(false); setImportConflicts([]); setImportNewCount(0); }}
              >
                Cancel
              </button>
              {importConflicts.length > 0 ? (
                <button
                  className="btn-primary"
                  onClick={() => handleImport(true)}
                  disabled={importLoading}
                  aria-label="Overwrite and Import"
                  style={{ background: 'var(--warning)', color: '#000' }}
                >
                  {importLoading ? 'Importing...' : `Overwrite ${importConflicts.length} Rule${importConflicts.length !== 1 ? 's' : ''}`}
                </button>
              ) : (
                <button
                  className="btn-primary"
                  onClick={() => handleImport(false)}
                  disabled={!importYaml.trim() || importLoading}
                  aria-label="Import"
                >
                  {importLoading ? 'Importing...' : 'Import'}
                </button>
              )}
            </div>
          </div>
        </ModalOverlay>
      )}

      {/* Export Dialog */}
      {showExportDialog && (
        <ModalOverlay onClose={() => setShowExportDialog(false)} role="dialog" aria-modal="true">
          <div className="modal-container modal-md">
            <div className="modal-header">
              <h2>Export Rules (YAML)</h2>
              <button
                className="modal-close-btn"
                onClick={() => setShowExportDialog(false)}
                aria-label="Close"
              >
                <span className="material-icons">close</span>
              </button>
            </div>
            <div className="modal-body">
              <textarea
                value={exportYaml}
                readOnly
                rows={15}
                aria-label="Exported YAML"
              />
              <button
                className="btn-secondary"
                onClick={async () => {
                  const success = await copyToClipboard(exportYaml, 'YAML rules');
                  if (success) {
                    notifications.success('Copied YAML to clipboard', 'Channel Pipeline');
                  } else {
                    notifications.error('Failed to copy to clipboard. Please check browser permissions.', 'Channel Pipeline');
                  }
                }}
              >
                <span className="material-icons">content_copy</span>
                Copy to Clipboard
              </button>
            </div>
            <div className="modal-footer">
              <button
                className="btn-primary"
                onClick={() => setShowExportDialog(false)}
              >
                Close
              </button>
            </div>
          </div>
        </ModalOverlay>
      )}
    </div>
  );
}
