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
  EventSyncExecutionSummary,
} from '../../types/channelPipeline';
import {
  isNormalizationWarning,
  isNonReversibleProfileChangesWarning,
} from '../../types/channelPipeline';
import type { CircuitBreakerState } from '../../services/channelPipelineApi';
import { useAuth } from '../../hooks/useAuth';
import { useChannelPipelineRules } from '../../hooks/useChannelPipelineRules';
import { useChannelPipelineExecution } from '../../hooks/useChannelPipelineExecution';
import { RuleBuilder } from './RuleBuilder';
import { EventSyncRuleEditor } from './EventSyncRuleEditor';
import { EventSyncReviewQueue } from './EventSyncReviewQueue';
import { EventSyncExclusionsPanel } from './EventSyncExclusionsPanel';
import { BulkRuleSettingsModal } from './BulkRuleSettingsModal';
import { CircuitBreakerBanner } from './CircuitBreakerBanner';
import { AutoCreationGateBanner } from './AutoCreationGateBanner';
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
    // y3m6o.1 (0152): a run in which an action failed is amber/warning — some
    // channels may have succeeded, so it is not a full error, but it is
    // clearly distinct from green ``completed``.
    completed_with_errors: 'badge-warning',
  };
  return `badge badge-sm badge-uppercase ${map[status] || ''}`;
};

/** Human-readable label for execution statuses that need one (others are fine as-is). */
const EXECUTION_STATUS_LABEL: Partial<Record<string, string>> = {
  rolled_back: 'Rolled Back',
  capped: 'Capped',
  abandoned: 'Abandoned',
  completed_with_errors: 'Completed with Errors',
};

/**
 * Aggregated event_sync run counters across an execution's per-rule summaries
 * (enhancedchannelmanager-7wuhd). One execution can span several event_sync
 * rules; the executions UI shows the run-level totals.
 */
interface EventSyncTotals {
  secondary_streams: number;
  attached: number;
  already_attached: number;
  ambiguous_skipped: number;
  unmatched: number;
  parse_failed: number;
  attach_errors: number;
  review_enqueued: number;
  cap_overage: number;
  capped: boolean;
}

/** Sum the per-rule event_sync summaries; null when there is no event_sync activity. */
function aggregateEventSyncSummary(
  summaries: EventSyncExecutionSummary[] | undefined | null,
): EventSyncTotals | null {
  if (!summaries || summaries.length === 0) return null;
  const totals: EventSyncTotals = {
    secondary_streams: 0, attached: 0, already_attached: 0,
    ambiguous_skipped: 0, unmatched: 0, parse_failed: 0,
    attach_errors: 0, review_enqueued: 0, cap_overage: 0, capped: false,
  };
  for (const s of summaries) {
    totals.secondary_streams += s.secondary_streams ?? 0;
    totals.attached += s.attached ?? 0;
    totals.already_attached += s.already_attached ?? 0;
    totals.ambiguous_skipped += s.ambiguous_skipped ?? 0;
    totals.unmatched += s.unmatched ?? 0;
    totals.parse_failed += s.parse_failed ?? 0;
    totals.attach_errors += s.attach_errors ?? 0;
    totals.review_enqueued += s.review_enqueued ?? 0;
    totals.cap_overage += s.cap_overage ?? 0;
    totals.capped = totals.capped || Boolean(s.capped);
  }
  return totals;
}

/**
 * The PO's key case: an idempotent run where everything the rule targets is
 * already attached — nothing new, nothing wrong. Surfaced as an explicit
 * success line so it never reads as "nothing evaluated / nothing matched".
 */
function isFullyInSync(t: EventSyncTotals): boolean {
  return t.attached === 0 && t.already_attached > 0 &&
    t.ambiguous_skipped === 0 && t.unmatched === 0 && t.parse_failed === 0;
}

/**
 * Event-sync-aware execution summary rows (enhancedchannelmanager-7wuhd).
 * Replaces the standard evaluated/matched/created block for event_sync runs;
 * vocabulary matches the Event Sync preview panel taxonomy. Dry-run swaps
 * "Attached" for "Would Attach".
 */
function EventSyncSummaryDetails(
  { totals, isDryRun }: { totals: EventSyncTotals; isDryRun: boolean },
) {
  const attachLabel = isDryRun ? 'Would Attach' : 'Attached';
  return (
    <>
      {isFullyInSync(totals) && (
        <div
          className="event-sync-sync-banner"
          role="status"
          data-testid="event-sync-fully-in-sync"
        >
          <span className="material-icons event-sync-sync-icon" aria-hidden="true">
            check_circle
          </span>
          <span>
            Fully in sync — {totals.already_attached} stream
            {totals.already_attached === 1 ? '' : 's'} already attached, nothing
            new to do this run.
          </span>
        </div>
      )}
      <div className="detail-row">
        <span className="detail-label">Secondary Streams Evaluated:</span>
        <span>{totals.secondary_streams}</span>
      </div>
      <div className="detail-row">
        <span className="detail-label">{attachLabel}:</span>
        <span>{totals.attached}</span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Already Attached:</span>
        <span>{totals.already_attached}</span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Ambiguous &rarr; Review:</span>
        <span>{totals.ambiguous_skipped}</span>
      </div>
      <div className="detail-row">
        <span className="detail-label">Unmatched:</span>
        <span>{totals.unmatched}</span>
      </div>
      <div className={`detail-row${totals.parse_failed > 0 ? ' warning' : ''}`}>
        <span className="detail-label">Parse Failures:</span>
        <span>{totals.parse_failed}</span>
      </div>
      {totals.review_enqueued > 0 && (
        <div className="detail-row">
          <span className="detail-label">Queued for Review:</span>
          <span>{totals.review_enqueued}</span>
        </div>
      )}
      {totals.attach_errors > 0 && (
        <div className="detail-row error">
          <span className="detail-label">Attach Errors:</span>
          <span>{totals.attach_errors}</span>
        </div>
      )}
      {totals.cap_overage > 0 && (
        <div className="detail-row warning">
          <span className="detail-label">Capped (deferred to next run):</span>
          <span>{totals.cap_overage}</span>
        </div>
      )}
      {/* Parse failures are the operator's broken-pattern drift detector —
          surface them prominently with the same visual as the norm-warning
          banner, not just a counter row. */}
      {totals.parse_failed > 0 && (
        <div className="norm-warning-banner" role="alert" data-testid="event-sync-parse-failure-banner">
          <span className="material-icons norm-warning-icon">warning</span>
          <div className="norm-warning-content">
            <p className="norm-warning-title">
              {totals.parse_failed} secondary stream
              {totals.parse_failed === 1 ? '' : 's'} could not be parsed
            </p>
            <p className="norm-warning-detail">
              Their names did not match the rule&apos;s parse pattern, so no
              title or start time could be read and they were skipped. A parse
              failure count this size usually means a broken pattern — check the
              Event Sync preview&apos;s test panel.
            </p>
          </div>
        </div>
      )}
    </>
  );
}

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
  // Run/Test confirm for event_sync rules (utswf + bead y8yby). The per-rule
  // Run on an event_sync row WRITES (attaches streams), unlike the in-editor
  // Preview which is dry-run only — so the live run gets a one-step "this will
  // attach" confirm. The Test (dry-run) icon stays confirm-free EXCEPT when the
  // rule has refresh_providers_before_run on: then Test triggers a real
  // Dispatcharr provider refresh before the dry preview (no longer zero-write),
  // so it also routes through a confirm. Standard rules keep no-confirm parity.
  const [showEventSyncRunConfirm, setShowEventSyncRunConfirm] =
    useState<{ rule: ChannelPipelineRule; dryRun: boolean } | null>(null);
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

  // S4a: the Event Sync editor owns the dirty-state discard guard and registers
  // its guarded-close here, so Escape (overlay onClose) and the header × route
  // through the same confirm instead of dropping a half-configured rule.
  const eventSyncCloseRef = useRef<(() => void) | null>(null);
  const registerEventSyncClose = useCallback((fn: (() => void) | null) => {
    eventSyncCloseRef.current = fn;
  }, []);

  // m1s38.3: the standard rule builder likewise registers its guarded close so
  // Escape/× route through its dirty-state discard confirm (previously only the
  // Event Sync editor did — the standard builder's ×/Escape discarded silently).
  const standardCloseRef = useRef<(() => void) | null>(null);
  const registerStandardClose = useCallback((fn: (() => void) | null) => {
    standardCloseRef.current = fn;
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
        // y3m6o.1 (0152): a run whose actions partly failed is a non-green
        // WARNING outcome, not a clean success and not a hard failure — some
        // channels were still modified. It surfaces an amber toast with the
        // run's error_message (the failed-action summary) and, like a clean
        // run, still refreshes the channel/group panes below.
        const completedWithErrors = status === 'completed_with_errors';
        // Event Sync runs attach streams rather than create channels, so the
        // "Created N channels" figure is always 0 and reads as "nothing
        // happened". When every targeted rule is an event_sync rule, point the
        // operator at the Execution History, where the run's
        // `event_sync: X attached, …` summary line lives (utswf). We don't
        // fabricate the attach count — it isn't on the client-side response.
        const targetedRules =
          ruleIds !== undefined ? rules.filter(r => ruleIds.includes(r.id)) : [];
        const isEventSyncRun =
          ruleIds !== undefined &&
          targetedRules.length > 0 &&
          targetedRules.every(r => r.event_sync_config);
        const msg = succeeded
          ? (isEventSyncRun
            ? (dryRun
              ? 'Event Sync dry run complete — see Execution History for match details'
              : 'Event Sync run complete — streams attached where matched; see Execution History for attach details')
            : (dryRun
              ? `Dry run complete - Would create ${created} channel${created !== 1 ? 's' : ''}`
              : `Execution complete - Created ${created} channel${created !== 1 ? 's' : ''}`))
          : `Pipeline ${status}`;
        // y3m6o.1 review (Blocker 3): the `warnings` array is heterogeneous —
        // disabled-normalization-group warnings AND non_reversible-profile-change
        // warnings share the same column. Discriminate by type so each gets its
        // OWN operator copy. The prior code blindly read `w.rule_name` on every
        // warning, so a non_reversible warning (which has no rule_name) produced
        // a FALSE "Normalization applied no changes: undefined ..." toast on an
        // otherwise-clean run that merely changed profile membership.
        const allWarnings = response.warnings ?? [];
        const normalizationWarnings = allWarnings.filter(isNormalizationWarning);
        const nonReversibleWarnings = allWarnings.filter(
          isNonReversibleProfileChangesWarning,
        );
        if (succeeded) {
          notifications.success(msg, 'Channel Pipeline');
        } else if (completedWithErrors) {
          notifications.warning(
            response.error_message ||
              'Pipeline completed with errors — some actions failed. See Execution History.',
            'Channel Pipeline',
          );
        } else {
          notifications.error(
            response.error_message || msg,
            'Channel Pipeline',
          );
        }
        // Surface disabled-normalization-group warnings so the operator notices
        // that normalization silently applied nothing, even on an otherwise-clean
        // run (enhancedchannelmanager-e8p1h). Fires for any non-error terminal
        // state (success or completed_with_errors).
        if (normalizationWarnings.length > 0) {
          const ruleNames = normalizationWarnings
            .map(w => w.rule_name)
            .join(', ');
          notifications.warning(
            `Normalization applied no changes: ${ruleNames} ` +
              `reference disabled normalization groups. Enable them under ` +
              `Settings > Normalization, then re-run.`,
            'Channel Pipeline',
          );
        }
        // Disclose non-reversible channel-profile membership changes — the
        // warning carries an operator-ready `message`. This MUST surface on a
        // clean `completed` run that only changed membership (the happy path the
        // previous code mislabeled), so it is emitted independently of status.
        for (const w of nonReversibleWarnings) {
          notifications.warning(w.message, 'Channel Pipeline');
        }
        // Refresh executions list and rule stats (match counts). The hook
        // already refetches executions in its finally block, but rule stats
        // (last_run_at / match_count) live on a separate endpoint.
        await fetchExecutions();
        await fetchRules();
        // Notify other panes to refresh (channels/groups may have changed).
        // completed_with_errors still mutated channels, so it refreshes too.
        if (!dryRun && (succeeded || completedWithErrors)) {
          window.dispatchEvent(new CustomEvent('channels-changed'));
        }
      }
      // If response is undefined, the hook caught an error and set executionsError
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Pipeline failed', 'Channel Pipeline');
    }
  }, [runPipelineApi, fetchExecutions, fetchRules, notifications, rules]);

  const handleRunSingleRule = useCallback(async (ruleId: number, dryRun: boolean) => {
    setRunningSingleRule(ruleId);
    try {
      await handleRun(dryRun, [ruleId]);
    } finally {
      setRunningSingleRule(null);
    }
  }, [handleRun]);

  // Live-run confirm (utswf): an event_sync live run attaches streams, so the
  // row Run icon routes through a confirm. Reuses handleRunSingleRule — the
  // run/attach semantics are unchanged.
  const handleConfirmEventSyncRun = useCallback(async () => {
    const pending = showEventSyncRunConfirm;
    if (!pending) return;
    setShowEventSyncRunConfirm(null);
    await handleRunSingleRule(pending.rule.id, pending.dryRun);
  }, [showEventSyncRunConfirm, handleRunSingleRule]);

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
            aria-label="Pipeline Debug Bundle"
            title="Download a debug bundle scoped to Channel Pipeline rules and execution history. For a whole-app bundle, see Settings → General."
          >
            <span className="material-icons">{debugBundleLoading ? 'hourglass_empty' : 'bug_report'}</span>
            {debugBundleLoading ? 'Generating...' : 'Pipeline Debug Bundle'}
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

      {/* vkktd.4: run-on-refresh rules exist but the auto_creation task gate is
          off — the rules will silently never fire. Opt-in discoverability. */}
      <AutoCreationGateBanner rules={rules} />

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

      {/* Event Sync review queue (ti939.3.2) — only meaningful when an
          event_sync rule exists; the component self-fetches its rows. */}
      {rules.some(r => r.event_sync_config) && (
        <section className="event-sync-review-section">
          <EventSyncReviewQueue />
          {/* ti939.3.5: standing never-attach exclusions — renders nothing
              while the list is empty (the normal state). */}
          <EventSyncExclusionsPanel />
        </section>
      )}

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
                              title={
                                rule.event_sync_config.auto_run
                                  ? 'Event Sync rule — attaches streams on manual pipeline runs AND automatically after each M3U refresh (auto-run is ON); attaches are journaled and reversible via rollback'
                                  : 'Event Sync rule — attaches streams on MANUAL pipeline runs only (never on unattended refresh); attaches are journaled and reversible via rollback'
                              }
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
                          {/* Per-rule Run/Test icons for every rule kind.
                              Standard rules run directly; an event_sync live
                              run WRITES (attaches streams), so it routes
                              through a one-step confirm (utswf). The Test
                              (dry-run) icon is confirm-free for both kinds —
                              the richer event_sync preview still lives in the
                              editor. */}
                          <button
                            className="action-btn"
                            onClick={() =>
                              rule.event_sync_config
                                ? setShowEventSyncRunConfirm({ rule, dryRun: false })
                                : handleRunSingleRule(rule.id, false)
                            }
                            disabled={runningSingleRule === rule.id || runningPipeline}
                            aria-label={`Run ${rule.name}`}
                            title={rule.event_sync_config ? 'Run rule (attaches streams)' : 'Run rule'}
                          >
                            <span className={`material-icons ${runningSingleRule === rule.id ? 'spinning' : ''}`}>
                              {runningSingleRule === rule.id ? 'sync' : 'play_arrow'}
                            </span>
                          </button>
                          <button
                            className="action-btn"
                            onClick={() =>
                              // bead y8yby: a Test on an event_sync rule with
                              // refresh_providers_before_run on is NOT zero-write
                              // (it refreshes providers first) — route it through
                              // a confirm. Otherwise Test runs immediately.
                              rule.event_sync_config?.refresh_providers_before_run
                                ? setShowEventSyncRunConfirm({ rule, dryRun: true })
                                : handleRunSingleRule(rule.id, true)
                            }
                            disabled={runningSingleRule === rule.id || runningPipeline}
                            aria-label={`Test ${rule.name}`}
                            title={
                              rule.event_sync_config?.refresh_providers_before_run
                                ? 'Test (dry run — refreshes this rule’s M3U providers first, not a preview of current data)'
                                : 'Test (dry run)'
                            }
                          >
                            <span className="material-icons">visibility</span>
                          </button>
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
                      {(() => {
                        // enhancedchannelmanager-7wuhd: a PURE event_sync run's
                        // standard counters are structurally 0 ("0 matched" is
                        // the bug); show event_sync counts instead.
                        const esTotals = aggregateEventSyncSummary(execution.event_sync_summary);
                        if (execution.is_event_sync && esTotals) {
                          const attachWord = execution.mode === 'dry_run' ? 'would attach' : 'attached';
                          const parts = [`${esTotals.attached} ${attachWord}`];
                          if (esTotals.already_attached > 0) parts.push(`${esTotals.already_attached} already attached`);
                          if (esTotals.ambiguous_skipped > 0) parts.push(`${esTotals.ambiguous_skipped} ambiguous`);
                          if (esTotals.unmatched > 0) parts.push(`${esTotals.unmatched} unmatched`);
                          if (esTotals.parse_failed > 0) parts.push(`${esTotals.parse_failed} parse failed`);
                          return parts.join(', ');
                        }
                        return (
                          <>
                            {execution.streams_matched} matched
                            {execution.channels_updated > 0 && `, ${execution.channels_updated} merged`}
                            {execution.channels_created > 0 && `, ${execution.channels_created} created`}
                            {execution.streams_skipped > 0 && `, ${execution.streams_skipped} skipped`}
                            {execution.streams_excluded > 0 && `, ${execution.streams_excluded} excluded`}
                          </>
                        );
                      })()}
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
                    {(execution.status === 'completed' || execution.status === 'completed_with_errors') && execution.mode === 'execute' && (
                      <button
                        className="action-btn danger"
                        onClick={() => handleRollbackClick(execution)}
                        aria-label="Rollback"
                        title={
                          'Rollback — deletes the channel(s) this run created and reverts the ' +
                          "channel(s) it modified, using this run's own recorded changes. This is " +
                          'the legacy per-run undo; it does not use the full pre-run snapshot ' +
                          '(see "Undo this run").' +
                          (execution.has_non_reversible_profile_changes
                            ? ' Note: channel-profile membership changed this run will NOT be restored.'
                            : '')
                        }
                      >
                        <span className="material-icons">undo</span>
                      </button>
                    )}
                    {/* Snapshot-restore affordance (ADR-010 uc51o.7): shown only
                        when has_snapshot=true; hidden for dry runs, legacy runs,
                        and already-reverted executions so the operator always
                        knows what will happen. */}
                    {execution.has_snapshot && (execution.status === 'completed' || execution.status === 'completed_with_errors') && execution.mode === 'execute' && (
                      <button
                        className="action-btn action-btn-revert"
                        onClick={() => handleRevertClick(execution)}
                        aria-label="Undo this run"
                        title={
                          'Undo this run — restores affected channels to their exact stream state ' +
                          'from the pre-run snapshot, overwriting any changes made since ' +
                          '(including edits made after this run). Unlike Rollback, this is a ' +
                          "full snapshot restore, not just this run's own changes." +
                          (execution.has_non_reversible_profile_changes
                            ? ' Note: channel-profile membership is not captured by the snapshot and will NOT be restored.'
                            : '')
                        }
                        data-testid="revert-btn"
                      >
                        <span className="material-icons">settings_backup_restore</span>
                      </button>
                    )}
                    {!execution.has_snapshot && execution.mode === 'execute' && (execution.status === 'completed' || execution.status === 'completed_with_errors') && (
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
        // For either editor, dismissors (overlay Escape / header ×) route
        // through that editor's registered dirty guard; the kind chooser (no
        // edits to lose) closes directly.
        const requestClose = () => {
          if (showKindChooser) {
            handleCancelRuleBuilder();
            return;
          }
          const registeredClose = isEventSync ? eventSyncCloseRef.current : standardCloseRef.current;
          if (registeredClose) {
            registeredClose();
          } else {
            handleCancelRuleBuilder();
          }
        };
        return (
          <ModalOverlay onClose={requestClose} role="dialog" aria-modal="true" aria-labelledby="rule-builder-title">
            {/* Both rule editors carry dense two-pane forms — give either one
                modal-xxl (1000px). Only the kind chooser keeps modal-lg. */}
            <div className={`modal-container ${showKindChooser ? 'modal-lg' : 'modal-xxl'} rule-builder-modal`}>
              <div className="modal-header">
                <h2 id="rule-builder-title">{title}</h2>
                <button
                  className="modal-close-btn"
                  onClick={requestClose}
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
                  onRegisterClose={registerEventSyncClose}
                />
              ) : (
                <RuleBuilder
                  rule={editingRule || undefined}
                  onSave={handleSaveRule}
                  onCancel={handleCancelRuleBuilder}
                  onRegisterClose={registerStandardClose}
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

      {/* Event Sync Live-Run Confirmation Dialog (utswf). Only the LIVE run
          hits this — the Test (dry-run) icon runs immediately. Attaches are
          journaled and reversible via rollback, so this is a light "you're
          about to write" gate, not a hard warning. */}
      {showEventSyncRunConfirm && (
        <ModalOverlay
          onClose={() => setShowEventSyncRunConfirm(null)}
          role="dialog"
          aria-modal="true"
          aria-labelledby="event-sync-run-confirm-title"
        >
          <div className="modal-container modal-sm" data-testid="event-sync-run-confirm">
            <div className="modal-header">
              <h2 id="event-sync-run-confirm-title">
                {showEventSyncRunConfirm.dryRun
                  ? 'Test Event Sync rule (refreshes providers)'
                  : 'Run Event Sync rule'}
              </h2>
            </div>
            <div className="modal-body">
              {showEventSyncRunConfirm.dryRun ? (
                <>
                  <p data-testid="event-sync-test-refresh-warning">
                    Testing &quot;{showEventSyncRunConfirm.rule.name}&quot; will
                    first <strong>refresh this rule&apos;s M3U providers</strong>{' '}
                    — a real write to Dispatcharr&apos;s stream list — and then
                    run a dry-run match. This is <strong>not</strong> a
                    zero-write preview of current data.
                  </p>
                  <p>
                    No streams are attached by the Test itself. To preview
                    against current data without any refresh, turn off
                    &quot;Refresh this rule&apos;s M3U providers before
                    running&quot; in the rule editor, or use the editor&apos;s
                    Preview.
                  </p>
                </>
              ) : (
                <>
                  <p>
                    Running &quot;{showEventSyncRunConfirm.rule.name}&quot; will{' '}
                    <strong>attach streams</strong> to their master channels
                    where they match. Attaches are journaled and reversible via
                    rollback.
                  </p>
                  {showEventSyncRunConfirm.rule.event_sync_config
                    ?.refresh_providers_before_run && (
                    <p data-testid="event-sync-run-refresh-note">
                      This rule will first <strong>refresh its M3U
                      providers</strong> (a write to Dispatcharr&apos;s stream
                      list), then run.
                    </p>
                  )}
                  <p>
                    To see what would attach without writing, use the Test (dry
                    run) action instead.
                  </p>
                </>
              )}
            </div>
            <div className="modal-footer">
              <button
                className="btn-secondary"
                onClick={() => setShowEventSyncRunConfirm(null)}
              >
                Cancel
              </button>
              <button
                className="btn-primary"
                onClick={handleConfirmEventSyncRun}
                aria-label={showEventSyncRunConfirm.dryRun ? 'Confirm test' : 'Confirm run'}
                data-testid="event-sync-run-confirm-btn"
              >
                {showEventSyncRunConfirm.dryRun ? 'Refresh & test' : 'Run & attach'}
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
                This is the legacy per-run undo: it deletes the{' '}
                <strong>{showRollbackConfirm.channels_created}</strong> channel(s) this run
                created and reverts any channels it modified, using this run's own recorded
                changes — <strong>not</strong> the full pre-run snapshot.
              </p>
              <p className="revert-warning-detail">
                For a complete restore to the exact pre-run state (including edits made after
                this run), cancel and use &quot;Undo this run&quot; instead.
              </p>
              {showRollbackConfirm.has_non_reversible_profile_changes && (
                <p className="revert-warning-detail" data-testid="rollback-profile-disclosure">
                  <span className="material-icons revert-warning-icon">warning</span>{' '}
                  This run changed <strong>channel-profile membership</strong>, which has no
                  reversible previous state. Rollback will <strong>not</strong> restore it —
                  only the stream and field changes this run made are reverted. Profile
                  membership must be corrected manually.
                </p>
              )}
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
              <p className="revert-warning-detail">
                Unlike Rollback, this restores every affected channel to the pre-run snapshot —
                not just the changes this run itself made.
              </p>
              {showRevertConfirm.has_non_reversible_profile_changes && (
                <p className="revert-warning-detail" data-testid="revert-profile-disclosure">
                  <span className="material-icons revert-warning-icon">warning</span>{' '}
                  Note: this run changed <strong>channel-profile membership</strong>, which the
                  pre-run snapshot does not capture. Undo restores stream assignments but will
                  <strong> not</strong> restore channel-profile membership — correct it manually
                  if needed.
                </p>
              )}
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
        // enhancedchannelmanager-7wuhd: run-level event_sync totals drive the
        // event_sync-aware summary block (null for a standard-only run).
        const eventSyncTotals = aggregateEventSyncSummary(details.event_sync_summary);

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
              {/* Mode-aware summary (enhancedchannelmanager-7wuhd). The
                  standard evaluated/matched/created counters are structurally 0
                  for event_sync runs, so a PURE event_sync run hides them and
                  shows the event_sync block instead. A MIXED run (is_event_sync
                  false but event_sync activity present) stacks both. */}
              {!details.is_event_sync && (
                <>
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
                </>
              )}
              {eventSyncTotals && (
                <EventSyncSummaryDetails
                  totals={eventSyncTotals}
                  isDryRun={details.mode === 'dry_run'}
                />
              )}
              {details.error_message && (
                <div className="detail-row error">
                  <span className="detail-label">Error:</span>
                  <span>{details.error_message}</span>
                </div>
              )}

              {/* y3m6o.1 review (Blocker 3): the warnings array is heterogeneous
                  — split by type so `disabled_groups.map()` only runs for the
                  normalization variant (the non_reversible variant has no
                  disabled_groups and would crash the render). */}
              {(() => {
                const warns = details.warnings ?? [];
                const normWarnings = warns.filter(isNormalizationWarning);
                const nonReversible = warns.filter(
                  isNonReversibleProfileChangesWarning,
                );
                return (
                  <>
                    {/* Disabled-normalization-group warning
                        (enhancedchannelmanager-e8p1h). Surfaced prominently
                        because these rules silently normalize nothing — the run
                        looks clean but names never get cleaned up. */}
                    {normWarnings.length > 0 && (
                      <div className="norm-warning-banner" role="alert">
                        <span className="material-icons norm-warning-icon">warning</span>
                        <div className="norm-warning-content">
                          <p className="norm-warning-title">
                            Normalization applied no changes — disabled groups referenced
                          </p>
                          <p className="norm-warning-detail">
                            The rule{normWarnings.length > 1 ? 's' : ''} below
                            reference normalization groups that are disabled or no longer
                            exist, so stream names were not normalized and
                            merge-into-channel matching likely missed most streams.
                            Enable the listed group(s) under Settings &gt; Normalization,
                            then re-run.
                          </p>
                          <ul className="norm-warning-list">
                            {normWarnings.map(w => (
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

                    {/* Non-reversible channel-profile membership change
                        (y3m6o.1 Finding 3). Rollback/Undo will not restore it —
                        disclose using the warning's operator-ready message. */}
                    {nonReversible.map((w, i) => (
                      <div
                        className="norm-warning-banner"
                        role="alert"
                        key={`non-reversible-${i}`}
                      >
                        <span className="material-icons norm-warning-icon">info</span>
                        <div className="norm-warning-content">
                          <p className="norm-warning-title">
                            Channel-profile membership changed on {w.count}{' '}
                            channel{w.count !== 1 ? 's' : ''} (not reversible)
                          </p>
                          <p className="norm-warning-detail">{w.message}</p>
                        </div>
                      </div>
                    ))}
                  </>
                );
              })()}

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
