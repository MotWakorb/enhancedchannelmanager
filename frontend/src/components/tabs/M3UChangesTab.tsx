import { logger } from '../../utils/logger';
import { useState, useEffect, useCallback, useRef } from 'react';
import type { M3UChangeLog, M3UChangeSummary, M3UChangeType, M3UAccount } from '../../types';
import * as api from '../../services/api';
import { CustomSelect } from '../CustomSelect';
import './M3UChangesTab.css';
import { useNotifications } from '../../contexts/NotificationContext';
import { formatTimestamp, formatRelativeTime } from '../../utils/formatting';
import { RouteHeaderSlot } from '../RouteHeaderSlots';
import { DenseToolbar } from '../DenseToolbar';
import { HttpError } from '../../services/httpClient';

// Get icon for change type
function getChangeTypeIcon(changeType: M3UChangeType): string {
  switch (changeType) {
    case 'group_added':
      return 'create_new_folder';
    case 'group_removed':
      return 'folder_delete';
    case 'streams_added':
      return 'playlist_add';
    case 'streams_removed':
      return 'playlist_remove';
    default:
      return 'change_circle';
  }
}

// Get color class for change type
function getChangeTypeClass(changeType: M3UChangeType): string {
  switch (changeType) {
    case 'group_added':
    case 'streams_added':
      return 'change-added';
    case 'group_removed':
    case 'streams_removed':
      return 'change-removed';
    default:
      return 'change-other';
  }
}

// Format change type for display
function formatChangeType(changeType: M3UChangeType): string {
  switch (changeType) {
    case 'group_added':
      return 'Group Added';
    case 'group_removed':
      return 'Group Removed';
    case 'streams_added':
      return 'Streams Added';
    case 'streams_removed':
      return 'Streams Removed';
    default:
      return changeType;
  }
}

// bd-juy2e: relative-time formatting (with a recency-threshold fallback to
// an absolute date past 7 days) now comes from the shared
// utils/formatting.formatRelativeTime — this file used to carry its own
// copy-pasted implementation. Journal was the odd one out (always
// absolute); it now uses the same shared helper so both tables apply one
// consistent rule instead of two independently-maintained ones.

export function M3UChangesTab({ initialHours = 168 }: { initialHours?: number }) {
  // Data state
  const [changes, setChanges] = useState<M3UChangeLog[]>([]);
  const [summary, setSummary] = useState<M3UChangeSummary | null>(null);
  const [accounts, setAccounts] = useState<M3UAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [requestState, setRequestState] = useState<'loading' | 'success' | 'error' | 'permission'>('loading');
  const changesRequestGenerationRef = useRef(0);
  const notifications = useNotifications();

  // Pagination state
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [totalCount, setTotalCount] = useState(0);
  const [pageSize, setPageSize] = useState(50);

  // Filter state
  const [accountFilter, setAccountFilter] = useState<number | ''>('');
  const [changeTypeFilter, setChangeTypeFilter] = useState<M3UChangeType | ''>('');
  const [enabledFilter, setEnabledFilter] = useState<boolean | ''>('');
  const [hoursFilter, setHoursFilter] = useState<number>(initialHours); // Default 7 days

  // Sort state
  const [sortBy, setSortBy] = useState<string>('change_time');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc');

  // Expanded rows
  const [expandedId, setExpandedId] = useState<number | null>(null);

  // Expanded stream names (show all instead of first 20)
  const [expandedStreams, setExpandedStreams] = useState<Set<number>>(new Set());

  // Fetch M3U accounts for filter dropdown
  useEffect(() => {
    api.getM3UAccounts().then(setAccounts).catch((err: unknown) => logger.error('Failed to load M3U accounts:', err));
  }, []);

  // Fetch changes
  const fetchChanges = useCallback(async () => {
    const requestGeneration = ++changesRequestGenerationRef.current;
    setLoading(true);
    setRequestState('loading');
    const [changesResult, summaryResult] = await Promise.allSettled([
        api.getM3UChanges({
          page,
          pageSize,
          m3uAccountId: accountFilter || undefined,
          changeType: changeTypeFilter || undefined,
          enabled: enabledFilter === '' ? undefined : enabledFilter,
          sortBy,
          sortOrder,
        }),
        api.getM3UChangesSummary({
          hours: hoursFilter,
          m3uAccountId: accountFilter || undefined,
        }),
      ]);
    if (requestGeneration !== changesRequestGenerationRef.current) return;
    try {
      if (changesResult.status === 'rejected' || summaryResult.status === 'rejected') {
        const errors = [changesResult, summaryResult]
          .filter((result): result is PromiseRejectedResult => result.status === 'rejected')
          .map(result => result.reason);
        const permissionError = errors.find(
          error => error instanceof HttpError && (error.status === 401 || error.status === 403),
        );
        throw permissionError ?? errors[0];
      }

      setChanges(changesResult.value.results);
      setTotalCount(changesResult.value.total);
      setTotalPages(changesResult.value.total_pages);
      setSummary(summaryResult.value);
      setRequestState('success');
    } catch (err) {
      if (requestGeneration !== changesRequestGenerationRef.current) return;
      const permission = err instanceof HttpError && (err.status === 401 || err.status === 403);
      if (permission) {
        setChanges([]);
        setSummary(null);
        setTotalCount(0);
        setTotalPages(1);
      }
      setRequestState(permission ? 'permission' : 'error');
      notifications.error(err instanceof Error ? err.message : 'Failed to fetch changes', 'Changes');
    } finally {
      if (requestGeneration === changesRequestGenerationRef.current) setLoading(false);
    }
  }, [page, pageSize, accountFilter, changeTypeFilter, enabledFilter, hoursFilter, sortBy, sortOrder, notifications]);

  useEffect(() => {
    void fetchChanges();
    return () => {
      changesRequestGenerationRef.current += 1;
    };
  }, [fetchChanges]);

  // Reset page when filters change
  useEffect(() => {
    setPage(1);
  }, [accountFilter, changeTypeFilter, enabledFilter]);

  // Get account name by ID
  const getAccountName = (accountId: number): string => {
    const account = accounts.find(a => a.id === accountId);
    return account?.name || `Account #${accountId}`;
  };

  // Toggle row expansion
  const toggleExpand = (id: number) => {
    setExpandedId(expandedId === id ? null : id);
  };

  // Toggle stream names expansion (show all vs first 20)
  const toggleStreamNames = (id: number, e: React.MouseEvent) => {
    e.stopPropagation(); // Don't toggle row expansion
    setExpandedStreams(prev => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  // Handle column sort
  const handleSort = (column: string) => {
    if (sortBy === column) {
      // Toggle order if same column
      setSortOrder(sortOrder === 'asc' ? 'desc' : 'asc');
    } else {
      // New column, default to desc
      setSortBy(column);
      setSortOrder('desc');
    }
    setPage(1); // Reset to first page when sorting
  };

  // Get sort indicator for column
  const getSortIndicator = (column: string) => {
    if (sortBy !== column) return null;
    return sortOrder === 'asc' ? 'arrow_upward' : 'arrow_downward';
  };

  const renderSortHeader = (column: string, label: string) => (
    <span>
      <button
        type="button"
        className="sortable"
        onClick={() => handleSort(column)}
        aria-label={`Sort by ${label}${sortBy === column ? `, currently ${sortOrder === 'asc' ? 'ascending' : 'descending'}` : ''}`}
      >
        {label}
        {getSortIndicator(column) && <span className="material-icons sort-icon" aria-hidden="true">{getSortIndicator(column)}</span>}
      </button>
    </span>
  );

  // Change type options for filter
  const changeTypeOptions = [
    { value: '', label: 'All Types' },
    { value: 'group_added', label: 'Groups Added' },
    { value: 'group_removed', label: 'Groups Removed' },
    { value: 'streams_added', label: 'Streams Added' },
    { value: 'streams_removed', label: 'Streams Removed' },
  ];

  // Enabled filter options
  const enabledOptions = [
    { value: '', label: 'All Groups' },
    { value: 'true', label: 'Enabled Only' },
    { value: 'false', label: 'Disabled Only' },
  ];

  // Hours filter options
  const hoursOptions = [
    { value: '24', label: 'Last 24 hours' },
    { value: '72', label: 'Last 3 days' },
    { value: '168', label: 'Last 7 days' },
    { value: '720', label: 'Last 30 days' },
    { value: '2160', label: 'Last 90 days' },
  ];

  // Page size options
  const pageSizeOptions = [
    { value: '25', label: '25' },
    { value: '50', label: '50' },
    { value: '100', label: '100' },
  ];

  return (
    <div className="m3u-changes-tab">
      <div className="changes-header">
        <div className="header-left">
          <span className="visually-hidden">M3U Changes</span>
          {summary && <RouteHeaderSlot name="status">
            <div className="header-stats">
              {/* bd-fzny4: this count is `total_changes` from the /changes/summary
                  endpoint, scoped to the selected Time Range filter below — a
                  different, smaller-scoped number than the footer's "N total
                  changes" (which counts all rows matching the table's own
                  account/type/enabled filters, with no time bound). Naming the
                  window explicitly here keeps the two from reading as a
                  data-consistency bug. */}
              <span
                className="header-stat"
                title="Changes detected within the selected Time Range filter below. The footer's total is unbounded by time and reflects the table's own filters instead."
              >
                <span className="material-icons">history</span>
                {summary.total_changes} changes ({(hoursOptions.find((o) => Number(o.value) === hoursFilter)?.label ?? 'selected range').toLowerCase()})
              </span>
            </div>
          </RouteHeaderSlot>}
        </div>
        <RouteHeaderSlot name="primary-action"><div className="header-actions">
          {requestState !== 'permission' && <button
            className="btn-secondary"
            onClick={fetchChanges}
            disabled={loading}
          >
            <span className={`material-icons ${loading ? 'spinning-cw' : ''}`}>refresh</span>
            Refresh
          </button>}
        </div></RouteHeaderSlot>
      </div>

      {/* Filters and Summary Row */}
      <RouteHeaderSlot name="controls">
      <div className="filters-summary-row">
        <DenseToolbar label="M3U change filters" filters={<>
          <div className="filter-select">
            <CustomSelect
              value={String(hoursFilter)}
              onChange={(val) => {
                const hours = parseInt(val as string);
                setHoursFilter(hours);
                window.history.replaceState(null, '', `#m3u-changes?hours=${hours}`);
              }}
              options={hoursOptions}
              placeholder="Time Range"
            />
          </div>
          <div className="filter-select">
            <CustomSelect
              value={String(accountFilter)}
              onChange={(val) => setAccountFilter(val === '' ? '' : parseInt(val as string))}
              options={[
                { value: '', label: 'All Accounts' },
                ...accounts.map(a => ({ value: String(a.id), label: a.name })),
              ]}
              placeholder="Filter by Account"
            />
          </div>
          <div className="filter-select">
            <CustomSelect
              value={changeTypeFilter}
              onChange={(val) => setChangeTypeFilter(val as M3UChangeType | '')}
              options={changeTypeOptions}
              placeholder="Filter by Type"
            />
          </div>
          <div className="filter-select">
            <CustomSelect
              value={String(enabledFilter)}
              onChange={(val) => setEnabledFilter(val === '' ? '' : val === 'true')}
              options={enabledOptions}
              placeholder="Filter by Status"
            />
          </div>
        </>} />
        {summary && (
          <div className="summary-cards">
            <div className="summary-card added">
              <div className="summary-icon">
                <span className="material-icons">add_circle</span>
              </div>
              <div className="summary-content">
                <span className="summary-value">{summary.groups_added}</span>
                <span className="summary-label">Groups Added</span>
              </div>
            </div>
            <div className="summary-card removed">
              <div className="summary-icon">
                <span className="material-icons">remove_circle</span>
              </div>
              <div className="summary-content">
                <span className="summary-value">{summary.groups_removed}</span>
                <span className="summary-label">Groups Removed</span>
              </div>
            </div>
            <div className="summary-card added">
              <div className="summary-icon">
                <span className="material-icons">playlist_add</span>
              </div>
              <div className="summary-content">
                <span className="summary-value">{summary.streams_added}</span>
                <span className="summary-label">Streams Added</span>
              </div>
            </div>
            <div className="summary-card removed">
              <div className="summary-icon">
                <span className="material-icons">playlist_remove</span>
              </div>
              <div className="summary-content">
                <span className="summary-value">{summary.streams_removed}</span>
                <span className="summary-label">Streams Removed</span>
              </div>
            </div>
          </div>
        )}
      </div>
      </RouteHeaderSlot>

      {/* Loading State */}
      {requestState === 'loading' && changes.length === 0 && (
        <div className="tab-loading">
          <span className="material-icons spinning">sync</span>
          <span>Loading changes...</span>
        </div>
      )}

      {/* Empty State */}
      {requestState === 'error' && <div className="tab-load-unavailable" role="alert">
        <p>{changes.length > 0
          ? 'M3U changes refresh failed — showing previously loaded changes.'
          : 'M3U changes could not be loaded.'}</p>
        <button className="btn-secondary" onClick={() => void fetchChanges()}>Retry</button>
      </div>}
      {requestState === 'permission' && <div className="tab-load-unavailable" role="alert">
        <p>You don't have permission to view M3U changes.</p>
      </div>}

      {requestState === 'success' && changes.length === 0 && (
        <div className="empty-state">
          <span className="material-icons">check_circle</span>
          <h3>No Changes Detected</h3>
          <p>No M3U playlist changes have been recorded yet. Changes will appear here after M3U refreshes.</p>
        </div>
      )}

      {/* Changes List */}
      {changes.length > 0 && (
        <div className="changes-list">
          <div className="list-header">
            {renderSortHeader('change_time', 'Time')}
            {renderSortHeader('m3u_account_id', 'M3U Account')}
            {renderSortHeader('change_type', 'Type')}
            {renderSortHeader('group_name', 'Group')}
            {renderSortHeader('count', 'Streams')}
            {renderSortHeader('enabled', 'Enabled')}
            <span></span>
          </div>
          <div className="changes-list-content">
            {changes.map((change) => (
              <div key={change.id} className="change-wrapper">
                <div
                  className={`change-row ${expandedId === change.id ? 'expanded' : ''}`}
                  onClick={() => toggleExpand(change.id)}
                  role="button"
                  tabIndex={0}
                  aria-expanded={expandedId === change.id}
                  onKeyDown={(e) => {
                    if (e.target !== e.currentTarget) return;
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      toggleExpand(change.id);
                    }
                  }}
                >
                  <span className="change-time" title={formatTimestamp(change.change_time)}>
                    {formatRelativeTime(change.change_time)}
                  </span>
                  <span className="change-account">
                    {getAccountName(change.m3u_account_id)}
                  </span>
                  <span className="change-type">
                    <span className={`type-badge ${getChangeTypeClass(change.change_type)}`}>
                      <span className="material-icons">{getChangeTypeIcon(change.change_type)}</span>
                      {formatChangeType(change.change_type)}
                    </span>
                  </span>
                  <span className="change-group">
                    {change.group_name || '—'}
                  </span>
                  <span className="change-count">
                    <span className={getChangeTypeClass(change.change_type)}>
                      {change.count}
                    </span>
                  </span>
                  <span className="change-enabled">
                    <span className={`enabled-badge ${change.enabled ? 'enabled' : 'disabled'}`}>
                      {change.enabled ? 'Yes' : 'No'}
                    </span>
                  </span>
                  <span className="change-expand">
                    <span className="material-icons">
                      {expandedId === change.id ? 'expand_less' : 'expand_more'}
                    </span>
                  </span>
                </div>
                {expandedId === change.id && (
                  <div className="change-details">
                    <div className="details-grid">
                      <div className="detail-section">
                        <h4>Change Details</h4>
                        <div className="detail-items">
                          <div className="detail-item">
                            <span className="detail-label">Change ID:</span>
                            <span className="detail-value">{change.id}</span>
                          </div>
                          <div className="detail-item">
                            <span className="detail-label">Time:</span>
                            <span className="detail-value">{formatTimestamp(change.change_time)}</span>
                          </div>
                          <div className="detail-item">
                            <span className="detail-label">Account:</span>
                            <span className="detail-value">{getAccountName(change.m3u_account_id)}</span>
                          </div>
                          <div className="detail-item">
                            <span className="detail-label">Group:</span>
                            <span className="detail-value">{change.group_name || 'N/A'}</span>
                          </div>
                          <div className="detail-item">
                            <span className="detail-label">Count:</span>
                            <span className="detail-value">{change.count}</span>
                          </div>
                          {change.snapshot_id && (
                            <div className="detail-item">
                              <span className="detail-label">Snapshot ID:</span>
                              <span className="detail-value">{change.snapshot_id}</span>
                            </div>
                          )}
                        </div>
                      </div>
                      {change.stream_names.length > 0 && (
                        <div className="detail-section">
                          <h4>Stream Names ({change.stream_names.length})</h4>
                          <div className="stream-names-list">
                            {(expandedStreams.has(change.id)
                              ? change.stream_names
                              : change.stream_names.slice(0, 20)
                            ).map((name, idx) => (
                              <span key={idx} className="stream-name-tag">{name}</span>
                            ))}
                            {change.stream_names.length > 20 && (
                              <span
                                className="stream-name-toggle"
                                onClick={(e) => toggleStreamNames(change.id, e)}
                              >
                                {expandedStreams.has(change.id)
                                  ? 'Show less'
                                  : `+${change.stream_names.length - 20} more`}
                              </span>
                            )}
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Pagination */}
      {totalPages > 0 && (
        <div className="pagination">
          <div className="pagination-left">
            <div className="page-size-select">
              <CustomSelect
                value={String(pageSize)}
                onChange={(val) => {
                  setPageSize(parseInt(val as string));
                  setPage(1);
                }}
                options={pageSizeOptions}
              />
            </div>
            <span className="page-size-label">per page</span>
          </div>
          <div className="pagination-center">
            <button
              className="btn-secondary"
              onClick={() => setPage(1)}
              disabled={page === 1 || loading}
              title="First page"
              aria-label="First page"
            >
              <span className="material-icons" aria-hidden="true">first_page</span>
            </button>
            <button
              className="btn-secondary"
              onClick={() => setPage(p => Math.max(1, p - 1))}
              disabled={page === 1 || loading}
              title="Previous page"
              aria-label="Previous page"
            >
              <span className="material-icons" aria-hidden="true">chevron_left</span>
            </button>
            <span className="page-info">
              Page {page} of {totalPages}
            </span>
            <button
              className="btn-secondary"
              onClick={() => setPage(p => Math.min(totalPages, p + 1))}
              disabled={page === totalPages || loading}
              title="Next page"
              aria-label="Next page"
            >
              <span className="material-icons" aria-hidden="true">chevron_right</span>
            </button>
            <button
              className="btn-secondary"
              onClick={() => setPage(totalPages)}
              disabled={page === totalPages || loading}
              title="Last page"
              aria-label="Last page"
            >
              <span className="material-icons" aria-hidden="true">last_page</span>
            </button>
          </div>
          <div className="pagination-right">
            <span className="entries-count">{totalCount} total changes</span>
          </div>
        </div>
      )}
    </div>
  );
}
