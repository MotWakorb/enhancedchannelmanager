import { useState, useEffect, useCallback } from 'react';
import type { M3UChangeLog, M3UChangeSummary, M3UChangeType, M3UAccount } from '../../types';
import * as api from '../../services/api';
import { CustomSelect } from '../CustomSelect';
import './M3UChangesTab.css';
import { useNotifications } from '../../contexts/NotificationContext';

// Helper to format timestamp
function formatTimestamp(isoString: string): string {
  const date = new Date(isoString);
  const now = new Date();

  return date.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: date.getFullYear() !== now.getFullYear() ? 'numeric' : undefined,
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  });
}

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

// Format relative time (e.g., "2 hours ago")
function formatRelativeTime(isoString: string): string {
  const date = new Date(isoString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / (1000 * 60));
  const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

  if (diffMins < 1) return 'just now';
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;
  return formatTimestamp(isoString);
}

export function M3UChangesTab() {
  // Data state
  const [changes, setChanges] = useState<M3UChangeLog[]>([]);
  const [summary, setSummary] = useState<M3UChangeSummary | null>(null);
  const [accounts, setAccounts] = useState<M3UAccount[]>([]);
  const [loading, setLoading] = useState(true);
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
  const [hoursFilter, setHoursFilter] = useState<number>(168); // Default 7 days

  // Sort state
  const [sortBy, setSortBy] = useState<string>('change_time');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc');

  // Expanded rows
  const [expandedId, setExpandedId] = useState<number | null>(null);

  // Expanded stream names (show all instead of first 20)
  const [expandedStreams, setExpandedStreams] = useState<Set<number>>(new Set());

  // Fetch M3U accounts for filter dropdown
  useEffect(() => {
    api.getM3UAccounts().then(setAccounts).catch(console.error);
  }, []);

  // Fetch changes
  const fetchChanges = useCallback(async () => {
    setLoading(true);
    try {
      const [changesRes, summaryRes] = await Promise.all([
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

      setChanges(changesRes.results);
      setTotalCount(changesRes.total);
      setTotalPages(changesRes.total_pages);
      setSummary(summaryRes);
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Failed to fetch changes', 'Changes');
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, accountFilter, changeTypeFilter, enabledFilter, hoursFilter, sortBy, sortOrder]);

  useEffect(() => {
    fetchChanges();
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
    { value: true, label: 'Enabled Only' },
    { value: false, label: 'Disabled Only' },
  ];

  // Hours filter options
  const hoursOptions = [
    { value: 24, label: 'Last 24 hours' },
    { value: 72, label: 'Last 3 days' },
    { value: 168, label: 'Last 7 days' },
    { value: 720, label: 'Last 30 days' },
    { value: 2160, label: 'Last 90 days' },
  ];

  // Page size options
  const pageSizeOptions = [
    { value: 25, label: '25' },
    { value: 50, label: '50' },
    { value: 100, label: '100' },
  ];

  return (
    <div className="m3u-changes-tab">
      <div className="changes-header">
        <div className="header-left">
          <h2>M3U Changes</h2>
          {summary && (
            <div className="header-stats">
              <span className="header-stat">
                <span className="material-icons">history</span>
                {summary.total_changes} changes
              </span>
            </div>
          )}
        </div>
        <div className="header-actions">
          <button
            className="btn-secondary"
            onClick={fetchChanges}
            disabled={loading}
          >
            <span className={`material-icons ${loading ? 'spinning' : ''}`}>refresh</span>
            Refresh
          </button>
        </div>
      </div>

      {/* Filters and Summary Row */}
      <div className="filters-summary-row">
        <div className="filters-bar">
          <div className="filter-select">
            <CustomSelect
              value={hoursFilter}
              onChange={(val) => setHoursFilter(val as number)}
              options={hoursOptions}
              placeholder="Time Range"
            />
          </div>
          <div className="filter-select">
            <CustomSelect
              value={accountFilter}
              onChange={(val) => setAccountFilter(val as number | '')}
              options={[
                { value: '', label: 'All Accounts' },
                ...accounts.map(a => ({ value: a.id, label: a.name })),
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
              value={enabledFilter}
              onChange={(val) => setEnabledFilter(val as boolean | '')}
              options={enabledOptions}
              placeholder="Filter by Status"
            />
          </div>
        </div>
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

      {/* Loading State */}
      {loading && changes.length === 0 && (
        <div className="loading-state">
          <span className="material-icons spinning">sync</span>
          <span>Loading changes...</span>
        </div>
      )}

      {/* Empty State */}
      {!loading && changes.length === 0 && (
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
            <span className="sortable" onClick={() => handleSort('change_time')}>
              Time
              {getSortIndicator('change_time') && (
                <span className="material-icons sort-icon">{getSortIndicator('change_time')}</span>
              )}
            </span>
            <span className="sortable" onClick={() => handleSort('m3u_account_id')}>
              M3U Account
              {getSortIndicator('m3u_account_id') && (
                <span className="material-icons sort-icon">{getSortIndicator('m3u_account_id')}</span>
              )}
            </span>
            <span className="sortable" onClick={() => handleSort('change_type')}>
              Type
              {getSortIndicator('change_type') && (
                <span className="material-icons sort-icon">{getSortIndicator('change_type')}</span>
              )}
            </span>
            <span className="sortable" onClick={() => handleSort('group_name')}>
              Group
              {getSortIndicator('group_name') && (
                <span className="material-icons sort-icon">{getSortIndicator('group_name')}</span>
              )}
            </span>
            <span className="sortable" onClick={() => handleSort('count')}>
              Streams
              {getSortIndicator('count') && (
                <span className="material-icons sort-icon">{getSortIndicator('count')}</span>
              )}
            </span>
            <span className="sortable" onClick={() => handleSort('enabled')}>
              Enabled
              {getSortIndicator('enabled') && (
                <span className="material-icons sort-icon">{getSortIndicator('enabled')}</span>
              )}
            </span>
            <span></span>
          </div>
          <div className="changes-list-content">
            {changes.map((change) => (
              <div key={change.id} className="change-wrapper">
                <div
                  className={`change-row ${expandedId === change.id ? 'expanded' : ''}`}
                  onClick={() => toggleExpand(change.id)}
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
                value={pageSize}
                onChange={(val) => {
                  setPageSize(val as number);
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
            >
              <span className="material-icons">first_page</span>
            </button>
            <button
              className="btn-secondary"
              onClick={() => setPage(p => Math.max(1, p - 1))}
              disabled={page === 1 || loading}
              title="Previous page"
            >
              <span className="material-icons">chevron_left</span>
            </button>
            <span className="page-info">
              Page {page} of {totalPages}
            </span>
            <button
              className="btn-secondary"
              onClick={() => setPage(p => Math.min(totalPages, p + 1))}
              disabled={page === totalPages || loading}
              title="Next page"
            >
              <span className="material-icons">chevron_right</span>
            </button>
            <button
              className="btn-secondary"
              onClick={() => setPage(totalPages)}
              disabled={page === totalPages || loading}
              title="Last page"
            >
              <span className="material-icons">last_page</span>
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
