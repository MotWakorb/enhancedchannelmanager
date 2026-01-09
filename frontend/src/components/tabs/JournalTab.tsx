import { useState, useEffect, useCallback } from 'react';
import type { JournalEntry, JournalCategory, JournalActionType, JournalStats, JournalQueryParams } from '../../types';
import * as api from '../../services/api';
import './JournalTab.css';

// Helper to format timestamp - always show actual date and time
function formatTimestamp(isoString: string): string {
  const date = new Date(isoString);
  const now = new Date();

  // Format: "Jan 8, 2026 2:35 PM" or "Jan 8 2:35 PM" if same year
  return date.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: date.getFullYear() !== now.getFullYear() ? 'numeric' : undefined,
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  });
}

// Get icon for category
function getCategoryIcon(category: JournalCategory): string {
  switch (category) {
    case 'channel':
      return 'tv';
    case 'epg':
      return 'calendar_month';
    case 'm3u':
      return 'playlist_play';
    default:
      return 'article';
  }
}

// Get color class for action type
function getActionClass(actionType: JournalActionType): string {
  switch (actionType) {
    case 'create':
      return 'action-create';
    case 'delete':
      return 'action-delete';
    case 'update':
      return 'action-update';
    case 'refresh':
      return 'action-refresh';
    default:
      return 'action-other';
  }
}

// Format action type for display
function formatActionType(actionType: string): string {
  return actionType.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

// Format category for display
function formatCategory(category: JournalCategory): string {
  switch (category) {
    case 'm3u':
      return 'M3U';
    case 'epg':
      return 'EPG';
    case 'channel':
      return 'Channel';
  }
}

export function JournalTab() {
  // Data state
  const [entries, setEntries] = useState<JournalEntry[]>([]);
  const [stats, setStats] = useState<JournalStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Pagination state
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [totalCount, setTotalCount] = useState(0);
  const [pageSize, setPageSize] = useState(50);

  // Filter state
  const [category, setCategory] = useState<JournalCategory | ''>('');
  const [actionType, setActionType] = useState<JournalActionType | ''>('');
  const [search, setSearch] = useState('');
  const [searchInput, setSearchInput] = useState('');

  // Expanded row state
  const [expandedId, setExpandedId] = useState<number | null>(null);

  // Load entries
  const loadEntries = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params: JournalQueryParams = {
        page,
        page_size: pageSize,
      };
      if (category) params.category = category;
      if (actionType) params.action_type = actionType;
      if (search) params.search = search;

      const result = await api.getJournalEntries(params);
      setEntries(result.results);
      setTotalPages(result.total_pages);
      setTotalCount(result.count);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load journal entries');
    } finally {
      setLoading(false);
    }
  }, [page, category, actionType, search]);

  // Load stats
  const loadStats = useCallback(async () => {
    try {
      const result = await api.getJournalStats();
      setStats(result);
    } catch (err) {
      console.error('Failed to load journal stats:', err);
    }
  }, []);

  useEffect(() => {
    loadEntries();
  }, [loadEntries]);

  useEffect(() => {
    loadStats();
  }, [loadStats]);

  // Debounced search
  useEffect(() => {
    const timer = setTimeout(() => {
      setSearch(searchInput);
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [searchInput]);

  // Reset page when filters or page size change
  useEffect(() => {
    setPage(1);
  }, [category, actionType, pageSize]);

  const handlePageSizeChange = (newSize: number) => {
    setPageSize(newSize);
  };

  const handleRefresh = () => {
    loadEntries();
    loadStats();
  };

  const handleToggleExpand = (id: number) => {
    setExpandedId(expandedId === id ? null : id);
  };

  // Render loading state
  if (loading && entries.length === 0) {
    return (
      <div className="journal-tab">
        <div className="loading-state">
          <span className="material-icons spinning">sync</span>
          <p>Loading journal...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="journal-tab">
      {/* Header with inline stats */}
      <div className="journal-header">
        <div className="header-left">
          <h2>Journal</h2>
          {stats && (
            <div className="header-stats">
              <span className="header-stat" title="Channel entries">
                <span className="material-icons">tv</span>
                {stats.by_category.channel || 0}
              </span>
              <span className="header-stat" title="EPG entries">
                <span className="material-icons">calendar_month</span>
                {stats.by_category.epg || 0}
              </span>
              <span className="header-stat" title="M3U entries">
                <span className="material-icons">playlist_play</span>
                {stats.by_category.m3u || 0}
              </span>
              <span className="header-total" title="Total journal entries">({stats.total_entries.toLocaleString()} total)</span>
            </div>
          )}
        </div>
        <div className="header-actions">
          <button className="btn-secondary" onClick={handleRefresh} disabled={loading}>
            <span className="material-icons">refresh</span>
            Refresh
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="filters-bar">
        <div className="search-box">
          <span className="material-icons">search</span>
          <input
            type="text"
            placeholder="Search entries..."
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
          />
        </div>
        <select
          value={category}
          onChange={(e) => setCategory(e.target.value as JournalCategory | '')}
          className="filter-select"
        >
          <option value="">All Categories</option>
          <option value="channel">Channel</option>
          <option value="epg">EPG</option>
          <option value="m3u">M3U</option>
        </select>
        <select
          value={actionType}
          onChange={(e) => setActionType(e.target.value as JournalActionType | '')}
          className="filter-select"
        >
          <option value="">All Actions</option>
          <option value="create">Create</option>
          <option value="update">Update</option>
          <option value="delete">Delete</option>
          <option value="refresh">Refresh</option>
          <option value="stream_add">Stream Add</option>
          <option value="stream_remove">Stream Remove</option>
          <option value="stream_reorder">Stream Reorder</option>
          <option value="reorder">Reorder</option>
        </select>
      </div>

      {/* Error Banner */}
      {error && (
        <div className="error-banner">
          <span className="material-icons">error</span>
          <span>{error}</span>
          <button onClick={() => setError(null)}>&times;</button>
        </div>
      )}

      {/* Entries List */}
      {entries.length === 0 ? (
        <div className="empty-state">
          <span className="material-icons">history</span>
          <h3>No journal entries</h3>
          <p>Changes to channels, EPG sources, and M3U accounts will appear here.</p>
        </div>
      ) : (
        <>
          <div className="entries-list">
            <div className="list-header">
              <span>Time</span>
              <span>Category</span>
              <span>Action</span>
              <span>Entity</span>
              <span>Description</span>
              <span></span>
            </div>
            {entries.map((entry) => (
              <div key={entry.id} className="entry-wrapper">
                <div
                  className={`entry-row ${expandedId === entry.id ? 'expanded' : ''}`}
                  onClick={() => handleToggleExpand(entry.id)}
                >
                  <span className="entry-time" title={entry.timestamp}>
                    {formatTimestamp(entry.timestamp)}
                  </span>
                  <span className="entry-category">
                    <span className={`category-badge category-${entry.category}`}>
                      <span className="material-icons">{getCategoryIcon(entry.category)}</span>
                      {formatCategory(entry.category)}
                    </span>
                  </span>
                  <span className="entry-action">
                    <span className={`action-badge ${getActionClass(entry.action_type)}`}>
                      {formatActionType(entry.action_type)}
                    </span>
                  </span>
                  <span className="entry-entity" title={entry.entity_name}>
                    {entry.entity_name}
                  </span>
                  <span className="entry-description" title={entry.description}>
                    {entry.description}
                  </span>
                  <span className="entry-expand">
                    {(entry.before_value || entry.after_value) && (
                      <span className="material-icons">
                        {expandedId === entry.id ? 'expand_less' : 'expand_more'}
                      </span>
                    )}
                  </span>
                </div>
                {expandedId === entry.id && (entry.before_value || entry.after_value) && (
                  <div className="entry-details">
                    <div className="details-grid">
                      {entry.before_value && (
                        <div className="detail-section">
                          <h4>Before</h4>
                          <pre>{JSON.stringify(entry.before_value, null, 2)}</pre>
                        </div>
                      )}
                      {entry.after_value && (
                        <div className="detail-section">
                          <h4>After</h4>
                          <pre>{JSON.stringify(entry.after_value, null, 2)}</pre>
                        </div>
                      )}
                    </div>
                    <div className="detail-meta">
                      <span>
                        <span className="material-icons">
                          {entry.user_initiated ? 'person' : 'smart_toy'}
                        </span>
                        {entry.user_initiated ? 'User initiated' : 'Automatic'}
                      </span>
                      {entry.batch_id && (
                        <span>
                          <span className="material-icons">layers</span>
                          Batch: {entry.batch_id}
                        </span>
                      )}
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>

          {/* Pagination */}
          <div className="pagination">
            <div className="pagination-left">
              <span className="entries-count">
                {Math.min((page - 1) * pageSize + 1, totalCount)}-{Math.min(page * pageSize, totalCount)} of {totalCount.toLocaleString()} entries
              </span>
            </div>
            <div className="pagination-center">
              <button
                className="btn-secondary"
                onClick={() => setPage(1)}
                disabled={page === 1 || loading}
              >
                <span className="material-icons">first_page</span>
              </button>
              <button
                className="btn-secondary"
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page === 1 || loading}
              >
                <span className="material-icons">chevron_left</span>
              </button>
              <span className="page-info">
                Page {page} of {totalPages}
              </span>
              <button
                className="btn-secondary"
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page === totalPages || loading}
              >
                <span className="material-icons">chevron_right</span>
              </button>
              <button
                className="btn-secondary"
                onClick={() => setPage(totalPages)}
                disabled={page === totalPages || loading}
              >
                <span className="material-icons">last_page</span>
              </button>
            </div>
            <div className="pagination-right">
              <select
                value={pageSize}
                onChange={(e) => handlePageSizeChange(Number(e.target.value))}
                className="page-size-select"
              >
                <option value={25}>25</option>
                <option value={50}>50</option>
                <option value={100}>100</option>
                <option value={250}>250</option>
              </select>
              <span className="page-size-label">per page</span>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
