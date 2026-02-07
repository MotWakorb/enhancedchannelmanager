/**
 * Main Auto-Creation tab component for managing auto-creation rules and executions.
 */
import { useState, useEffect, useCallback, useMemo } from 'react';
import type {
  AutoCreationRule,
  AutoCreationExecution,
  CreateRuleData,
  ExecutionLogEntry,
} from '../../types/autoCreation';
import { useAutoCreationRules } from '../../hooks/useAutoCreationRules';
import { useAutoCreationExecution } from '../../hooks/useAutoCreationExecution';
import { RuleBuilder } from './RuleBuilder';
import * as autoCreationApi from '../../services/autoCreationApi';
import { copyToClipboard } from '../../utils/clipboard';
import { useNotifications } from '../../contexts/NotificationContext';
import { ModalOverlay } from '../ModalOverlay';
import '../ModalBase.css';
import './AutoCreationTab.css';

type FilterMode = 'all' | 'enabled' | 'disabled';

export function AutoCreationTab() {
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
    getEnabledRules,
  } = useAutoCreationRules();

  const {
    executions,
    loading: executionsLoading,
    error: executionsError,
    isRunning: runningPipeline,
    fetchExecutions,
    runPipeline: runPipelineApi,
    rollback,
  } = useAutoCreationExecution();

  // Local state
  const [search, setSearch] = useState('');
  const [filterMode, setFilterMode] = useState<FilterMode>('all');
  const [showFilterMenu, setShowFilterMenu] = useState(false);
  const [runningSingleRule, setRunningSingleRule] = useState<number | null>(null);

  // Modal states
  const [showRuleBuilder, setShowRuleBuilder] = useState(false);
  const [editingRule, setEditingRule] = useState<AutoCreationRule | null>(null);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState<AutoCreationRule | null>(null);
  const [showRollbackConfirm, setShowRollbackConfirm] = useState<AutoCreationExecution | null>(null);
  const [showImportDialog, setShowImportDialog] = useState(false);
  const [showExportDialog, setShowExportDialog] = useState(false);
  const [showExecutionDetails, setShowExecutionDetails] = useState<AutoCreationExecution | null>(null);
  const [executionDetails, setExecutionDetails] = useState<AutoCreationExecution | null>(null);
  const [executionDetailsLoading, setExecutionDetailsLoading] = useState(false);
  const [logSearch, setLogSearch] = useState('');
  const [expandedLogEntries, setExpandedLogEntries] = useState<Set<number>>(new Set());

  // Import/Export state
  const [importYaml, setImportYaml] = useState('');
  const [exportYaml, setExportYaml] = useState('');
  const [importLoading, setImportLoading] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);

  const notifications = useNotifications();

  // Responsive state
  const [isMobile, setIsMobile] = useState(window.innerWidth <= 768);

  // Fetch rules and executions on mount
  useEffect(() => {
    fetchRules();
    fetchExecutions();
  }, [fetchRules, fetchExecutions]);

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

  // Handlers
  const handleCreateRule = useCallback(() => {
    setEditingRule(null);
    setShowRuleBuilder(true);
  }, []);

  const handleEditRule = useCallback((rule: AutoCreationRule) => {
    setEditingRule(rule);
    setShowRuleBuilder(true);
  }, []);

  const handleSaveRule = useCallback(async (data: CreateRuleData) => {
    try {
      if (editingRule) {
        await updateRule(editingRule.id, data);
      } else {
        await createRule(data);
      }
      setShowRuleBuilder(false);
      setEditingRule(null);
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Failed to save rule', 'Auto-Creation');
    }
  }, [editingRule, updateRule, createRule]);

  const handleCancelRuleBuilder = useCallback(() => {
    setShowRuleBuilder(false);
    setEditingRule(null);
  }, []);

  const handleDeleteClick = useCallback((rule: AutoCreationRule) => {
    setShowDeleteConfirm(rule);
  }, []);

  const handleConfirmDelete = useCallback(async () => {
    if (showDeleteConfirm) {
      try {
        await deleteRule(showDeleteConfirm.id);
        setShowDeleteConfirm(null);
      } catch (err) {
        notifications.error(err instanceof Error ? err.message : 'Failed to delete rule', 'Auto-Creation');
      }
    }
  }, [showDeleteConfirm, deleteRule]);

  const handleToggleEnabled = useCallback(async (rule: AutoCreationRule) => {
    try {
      await toggleRule(rule.id);
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Failed to toggle rule', 'Auto-Creation');
    }
  }, [toggleRule]);

  const handleDuplicate = useCallback(async (rule: AutoCreationRule) => {
    try {
      await duplicateRule(rule.id);
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Failed to duplicate rule', 'Auto-Creation');
    }
  }, [duplicateRule]);

  const handleRun = useCallback(async (dryRun: boolean = false, ruleIds?: number[]) => {
    try {
      const response = await runPipelineApi({ dryRun, ruleIds });

      if (response) {
        const created = response.channels_created ?? 0;
        const removed = response.channels_removed ?? 0;
        const moved = response.channels_moved ?? 0;
        const orphanParts: string[] = [];
        if (removed > 0) orphanParts.push(`removed ${removed} orphan${removed !== 1 ? 's' : ''}`);
        if (moved > 0) orphanParts.push(`moved ${moved} orphan${moved !== 1 ? 's' : ''}`);
        const orphanSuffix = orphanParts.length > 0 ? `, ${orphanParts.join(', ')}` : '';
        const msg = dryRun
          ? `Dry run complete - Would create ${created} channel${created !== 1 ? 's' : ''}${orphanSuffix ? `, would remove ${removed} orphan${removed !== 1 ? 's' : ''}` : ''}`
          : `Execution complete - Created ${created} channel${created !== 1 ? 's' : ''}${orphanSuffix}`;
        notifications.success(msg, 'Auto-Creation');
        // Refresh executions list and rule stats (match counts)
        await fetchExecutions();
        await fetchRules();
        // Notify other panes to refresh (channels/groups may have changed)
        if (!dryRun) {
          window.dispatchEvent(new CustomEvent('channels-changed'));
        }
      }
      // If response is undefined, the hook caught an error and set executionsError
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Pipeline failed', 'Auto-Creation');
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

  const handleRollbackClick = useCallback((execution: AutoCreationExecution) => {
    setShowRollbackConfirm(execution);
  }, []);

  const handleConfirmRollback = useCallback(async () => {
    if (showRollbackConfirm) {
      try {
        await rollback(showRollbackConfirm.id);
        setShowRollbackConfirm(null);
        await fetchExecutions();
      } catch (err) {
        notifications.error(err instanceof Error ? err.message : 'Failed to rollback', 'Auto-Creation');
      }
    }
  }, [showRollbackConfirm, rollback, fetchExecutions]);

  const handleViewDetails = useCallback(async (execution: AutoCreationExecution) => {
    setShowExecutionDetails(execution);
    setExecutionDetails(null);
    setLogSearch('');
    setExpandedLogEntries(new Set());
    setExecutionDetailsLoading(true);
    try {
      const details = await autoCreationApi.getExecutionDetails(execution.id);
      setExecutionDetails(details);
    } catch {
      // Fall back to the basic execution data we already have
      setExecutionDetails(null);
    } finally {
      setExecutionDetailsLoading(false);
    }
  }, []);

  const handleExport = useCallback(async () => {
    try {
      const yaml = await autoCreationApi.exportAutoCreationRulesYAML();
      setExportYaml(yaml);
      setShowExportDialog(true);
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Failed to export rules', 'Auto-Creation');
    }
  }, []);

  const handleImport = useCallback(async () => {
    setImportLoading(true);
    setImportError(null);

    try {
      const result = await autoCreationApi.importAutoCreationRulesYAML(importYaml);
      const importedCount = result.imported.length;
      await fetchRules();
      setImportYaml('');
      setShowImportDialog(false);
      notifications.success(`Imported ${importedCount} rule${importedCount !== 1 ? 's' : ''}`, 'Auto-Creation');
    } catch (err) {
      setImportError(err instanceof Error ? err.message : 'Failed to import rules');
    } finally {
      setImportLoading(false);
    }
  }, [importYaml, fetchRules]);

  const handleRetry = useCallback(() => {
    fetchRules();
    fetchExecutions();
  }, [fetchRules, fetchExecutions]);

  // Propagate hook errors to the toast
  useEffect(() => {
    if (executionsError) {
      notifications.error(executionsError, 'Auto-Creation');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [executionsError]);

  // Error state
  if (rulesError && !rulesLoading) {
    return (
      <div className={`auto-creation-tab ${isMobile ? 'mobile' : ''}`} data-testid="auto-creation-tab">
        <div className="loading-state">
          <span className="material-icons">error</span>
          <p>Failed to load auto-creation rules</p>
          <button className="btn-primary" onClick={handleRetry} aria-label="Retry">
            <span className="material-icons">refresh</span>
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className={`auto-creation-tab ${isMobile ? 'mobile' : ''}`} data-testid="auto-creation-tab">
      {/* Header */}
      <header className="tab-header">
        <h2>Auto-Creation Pipeline</h2>
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
        </div>
      </header>

      {/* Statistics Summary */}
      <div className="auto-creation-stats">
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
      <div className="auto-creation-content">
        {/* Rules Section */}
        <section className="rules-section">
          <div className="section-header">
            <h3>Rules</h3>
            <div className="section-controls">
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
                    <th className="col-drag"></th>
                    <th className="col-name">Name</th>
                    <th className="col-priority">Priority</th>
                    <th className="col-status">Status</th>
                    <th className="col-matches">Matches</th>
                    <th className="col-actions">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredRules.map(rule => (
                    <tr
                      key={rule.id}
                      data-testid="rule-row"
                      tabIndex={0}
                    >
                      <td className="col-drag">
                        <span className="drag-handle" data-testid="drag-handle">
                          <span className="material-icons">drag_indicator</span>
                        </span>
                      </td>
                      <td className="col-name">
                        <div className="rule-name">{rule.name}</div>
                        {rule.description && (
                          <div className="rule-description">{rule.description}</div>
                        )}
                      </td>
                      <td className="col-priority">{rule.priority}</td>
                      <td className="col-status">
                        <span className={`status-badge ${rule.enabled ? 'enabled' : 'disabled'}`}>
                          {rule.enabled ? 'Enabled' : 'Disabled'}
                        </span>
                      </td>
                      <td className="col-matches">{rule.match_count || 0}</td>
                      <td className="col-actions">
                        <div className="rule-actions-row">
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

          {executionsLoading ? (
            <div className="executions-loading">
              <span className="material-icons spinning">sync</span>
              Loading...
            </div>
          ) : executions.length === 0 ? (
            <div className="empty-state small">
              <span className="material-icons">history</span>
              <p>No executions yet</p>
            </div>
          ) : (
            <div className="executions-list" data-testid="executions-list">
              {executions.slice(0, 5).map(execution => (
                <div key={execution.id} className="execution-item" data-testid="execution-item">
                  <div className="execution-info">
                    <span className={`status-badge ${execution.status}`}>
                      {execution.status === 'rolled_back' ? 'Rolled Back' : execution.status}
                    </span>
                    <span className="execution-mode">
                      {execution.mode === 'dry_run' ? 'Dry Run' : 'Execute'}
                    </span>
                    <span className="execution-date">
                      {new Date(execution.started_at).toLocaleString()}
                    </span>
                    <span className="execution-stats">
                      {execution.streams_matched} matched, {execution.channels_created} created
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
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>

      {/* Rule Builder Modal */}
      {showRuleBuilder && (
        <ModalOverlay onClose={handleCancelRuleBuilder} role="dialog" aria-modal="true" aria-labelledby="rule-builder-title">
          <div className="modal-container modal-lg rule-builder-modal">
            <div className="modal-header">
              <h2 id="rule-builder-title">
                {editingRule ? 'Edit Rule' : 'Create Rule'}
              </h2>
              <button
                className="modal-close-btn"
                onClick={handleCancelRuleBuilder}
                aria-label="Close"
              >
                <span className="material-icons">close</span>
              </button>
            </div>
            <RuleBuilder
              rule={editingRule || undefined}
              onSave={handleSaveRule}
              onCancel={handleCancelRuleBuilder}
            />
          </div>
        </ModalOverlay>
      )}

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

      {/* Execution Details Dialog */}
      {showExecutionDetails && (() => {
        const details = executionDetails || showExecutionDetails;
        const log = details.execution_log || [];
        const filteredLog = logSearch
          ? log.filter((entry: ExecutionLogEntry) =>
              entry.stream_name.toLowerCase().includes(logSearch.toLowerCase())
            )
          : log;

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
                <span className={`status-badge ${details.status}`}>
                  {details.status}
                </span>
              </div>
              <div className="detail-row">
                <span className="detail-label">Mode:</span>
                <span>{details.mode === 'dry_run' ? 'Dry Run' : 'Execute'}</span>
              </div>
              <div className="detail-row">
                <span className="detail-label">Started:</span>
                <span>{new Date(details.started_at).toLocaleString()}</span>
              </div>
              {details.completed_at && (
                <div className="detail-row">
                  <span className="detail-label">Completed:</span>
                  <span>{new Date(details.completed_at).toLocaleString()}</span>
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
              {details.error_message && (
                <div className="detail-row error">
                  <span className="detail-label">Error:</span>
                  <span>{details.error_message}</span>
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
                          <button className="log-search-clear" onClick={() => setLogSearch('')}>
                            <span className="material-icons">close</span>
                          </button>
                        )}
                      </div>
                    )}

                    <div className="log-entries">
                      {filteredLog.map((entry: ExecutionLogEntry) => {
                        const isExpanded = expandedLogEntries.has(entry.stream_id);
                        const winnerRule = entry.rules_evaluated.find(r => r.was_winner);
                        const actionCount = entry.actions_executed.length;
                        const hasErrors = entry.actions_executed.some(a => !a.success);

                        return (
                          <div key={entry.stream_id} className={`log-entry ${isExpanded ? 'expanded' : ''}`}>
                            <div
                              className="log-entry-header"
                              onClick={() => toggleLogEntry(entry.stream_id)}
                              role="button"
                              tabIndex={0}
                              onKeyDown={e => e.key === 'Enter' && toggleLogEntry(entry.stream_id)}
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
                                {entry.rules_evaluated.filter(r => r.matched || r.conditions.length > 0).map((rule, ri) => (
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
                onClick={() => setShowImportDialog(false)}
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
                  onChange={e => setImportYaml(e.target.value)}
                  placeholder="Paste YAML content here..."
                  rows={10}
                  aria-label="YAML content"
                />
              </div>
              {importError && (
                <div className="import-error">{importError}</div>
              )}
            </div>
            <div className="modal-footer">
              <button
                className="btn-secondary"
                onClick={() => setShowImportDialog(false)}
              >
                Cancel
              </button>
              <button
                className="btn-primary"
                onClick={handleImport}
                disabled={!importYaml.trim() || importLoading}
                aria-label="Import"
              >
                {importLoading ? 'Importing...' : 'Import'}
              </button>
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
                    notifications.success('Copied YAML to clipboard', 'Auto-Creation');
                  } else {
                    notifications.error('Failed to copy to clipboard. Please check browser permissions.', 'Auto-Creation');
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
