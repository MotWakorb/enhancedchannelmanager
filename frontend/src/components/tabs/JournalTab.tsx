import { logger } from '../../utils/logger';
import { useState, useEffect, useCallback, useRef } from 'react';
import type { JournalEntry, JournalCategory, JournalActionType, JournalStats, JournalQueryParams, MutationSource } from '../../types';
import * as api from '../../services/api';
import { CustomSelect } from '../CustomSelect';
import './JournalTab.css';
import { useNotifications } from '../../contexts/NotificationContext';
import { formatTimestamp, formatRelativeTime } from '../../utils/formatting';
import { RouteHeaderSlot } from '../RouteHeaderSlots';
import { DenseToolbar } from '../DenseToolbar';
import { HttpError } from '../../services/httpClient';

// Get icon for category
function getCategoryIcon(category: JournalCategory): string {
  switch (category) {
    case 'channel':
      return 'tv';
    case 'epg':
      return 'calendar_month';
    case 'm3u':
      return 'playlist_play';
    case 'watch':
      return 'visibility';
    case 'task':
      return 'schedule';
    case 'auto_creation':
      return 'auto_fix_high';
    case 'event_sync':
      return 'sync_alt';
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
    case 'start':
      return 'action-create';  // Green for watch start
    case 'stop':
      return 'action-delete';  // Red for watch stop
    default:
      return 'action-other';
  }
}

/**
 * Action types whose title-cased identifier is not the words an operator is
 * told to look for. The generic transform below turns `merge_unapplied` into
 * "Merge Unapplied", while the filter dropdown and the Pending Merges notice
 * both say "Merge Not Applied" — so an operator following the notice found a
 * badge with different words and no way to know they were the same thing.
 *
 * One label per action type, defined once and used by both the badge and the
 * filter option, so the two cannot drift again. Module-local rather than
 * exported: this file exports a component, and a second export trips the
 * react-refresh lint rule (see docs/frontend_lint.md).
 */
const ACTION_TYPE_LABELS: Record<string, string> = {
  merge_unapplied: 'Merge Not Applied',
  // Reads as a pair with the entry above, which is the point: both name a
  // merge that did not fully happen, and an operator scanning the Action
  // dropdown for merge trouble finds them together. The generic transform
  // produced "Bulk Merge Incomplete", a phrase no filter or doc ever used.
  bulk_merge_incomplete: 'Merge Incomplete',
};

// Format action type for display
function formatActionType(actionType: string): string {
  return (
    ACTION_TYPE_LABELS[actionType] ??
    actionType.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
  );
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
    case 'watch':
      return 'Watch';
    case 'task':
      return 'Task';
    case 'auto_creation':
      return 'Channel Pipeline';
    case 'event_sync':
      return 'Event Sync';
    default:
      return category;
  }
}

// Format mutation_source for display
function formatMutationSource(source: MutationSource | null | undefined): string {
  switch (source) {
    case 'ui':
      return 'UI';
    case 'mcp_ai':
      return 'AI';
    case 'scheduler':
      return 'Scheduler';
    case 'auto_creation':
      return 'Channel Pipeline';
    default:
      return '—';
  }
}

/**
 * The reserved key the backend writes into `before_value` for a field whose
 * before-state it could not read at all — a channel created earlier in the
 * same batch, a catalog read that failed, a payload field Dispatcharr does not
 * return (bead enhancedchannelmanager-kz089, `BEFORE_STATE_UNKNOWN_KEY` in
 * `backend/routers/channels.py`).
 *
 * The WIRE key is deliberately machine-shaped and stays as it is: it has to be
 * unmistakably not a Dispatcharr field name. What was wrong is that this tab
 * rendered `before_value` as raw JSON, so an operator opening a row read the
 * literal string `__before_state_unknown__` with nothing to tell them it meant
 * "ECM never saw these fields" rather than "these fields were called that".
 */
const BEFORE_STATE_UNKNOWN_KEY = '__before_state_unknown__';

/**
 * Split a `before_value` into the values ECM actually read and the names of
 * the fields it could not.
 *
 * "It held null" and "I never saw it" are different facts, and the whole point
 * of the reserved key is to keep them apart; presenting them apart is the same
 * requirement one layer up.
 */
function splitBeforeValue(
  beforeValue: Record<string, unknown>,
): { known: Record<string, unknown>; unknownFields: string[] } {
  const known: Record<string, unknown> = {};
  let unknownFields: string[] = [];
  for (const [key, value] of Object.entries(beforeValue)) {
    if (key === BEFORE_STATE_UNKNOWN_KEY) {
      unknownFields = Array.isArray(value) ? value.map(String) : [String(value)];
      continue;
    }
    known[key] = value;
  }
  return { known, unknownFields };
}

export function JournalTab() {
  const notifications = useNotifications();

  // Data state
  const [entries, setEntries] = useState<JournalEntry[]>([]);
  const [stats, setStats] = useState<JournalStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [requestState, setRequestState] = useState<'loading' | 'success' | 'error' | 'permission'>('loading');
  const entriesRequestFailedRef = useRef(false);
  const entriesRequestGenerationRef = useRef(0);

  // Pagination state
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [totalCount, setTotalCount] = useState(0);
  const [pageSize, setPageSize] = useState(50);

  // Filter state
  const [category, setCategory] = useState<JournalCategory | ''>('');
  const [actionType, setActionType] = useState<JournalActionType | ''>('');
  const [mutationSource, setMutationSource] = useState<MutationSource | ''>('');
  const [search, setSearch] = useState('');
  const [searchInput, setSearchInput] = useState('');

  // Expanded row state
  const [expandedId, setExpandedId] = useState<number | null>(null);

  // Journal purge control (enhancedchannelmanager-hq3de.a) — matches the
  // DELETE /api/journal/purge default of 90 days.
  const [purgeDays, setPurgeDays] = useState(90);
  const [purging, setPurging] = useState(false);

  // Load entries
  const loadEntries = useCallback(async () => {
    const requestGeneration = ++entriesRequestGenerationRef.current;
    setLoading(true);
    setRequestState('loading');
    entriesRequestFailedRef.current = false;
    try {
      const params: JournalQueryParams = {
        page,
        page_size: pageSize,
      };
      if (category) params.category = category;
      if (actionType) params.action_type = actionType;
      if (mutationSource) params.mutation_source = mutationSource;
      if (search) params.search = search;

      const result = await api.getJournalEntries(params);
      if (requestGeneration !== entriesRequestGenerationRef.current) return;
      setEntries(result.results);
      setTotalPages(result.total_pages);
      setTotalCount(result.count);
      setRequestState('success');
    } catch (err) {
      if (requestGeneration !== entriesRequestGenerationRef.current) return;
      entriesRequestFailedRef.current = true;
      const permission = err instanceof HttpError && (err.status === 401 || err.status === 403);
      if (permission) {
        setEntries([]);
        setTotalCount(0);
        setTotalPages(1);
        setStats(null);
      }
      setRequestState(permission ? 'permission' : 'error');
      notifications.error(err instanceof Error ? err.message : 'Failed to load journal entries', 'Journal');
    } finally {
      if (requestGeneration === entriesRequestGenerationRef.current) setLoading(false);
    }
  }, [page, pageSize, category, actionType, mutationSource, search, notifications]);

  // Load stats
  const loadStats = useCallback(async () => {
    try {
      const result = await api.getJournalStats();
      if (!entriesRequestFailedRef.current) setStats(result);
    } catch (err) {
      setStats(null);
      logger.error('Failed to load journal stats:', err);
    }
  }, []);

  useEffect(() => {
    void loadEntries();
    return () => {
      entriesRequestGenerationRef.current += 1;
    };
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
  }, [category, actionType, mutationSource, pageSize]);

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

  const handlePurge = async () => {
    if (purgeDays < 1) {
      notifications.error('Enter a day count of 1 or more', 'Journal');
      return;
    }
    if (!confirm(`Delete journal entries older than ${purgeDays} day${purgeDays === 1 ? '' : 's'}? This cannot be undone.`)) {
      return;
    }
    setPurging(true);
    try {
      const result = await api.purgeJournalEntries(purgeDays);
      notifications.success(`Purged ${result.deleted} journal entr${result.deleted === 1 ? 'y' : 'ies'} older than ${purgeDays} days`, 'Journal');
      loadEntries();
      loadStats();
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Failed to purge journal entries', 'Journal');
    } finally {
      setPurging(false);
    }
  };

  // Render loading state
  return (
    <div className="journal-tab">
      {/* Header with inline stats */}
      <div className="journal-header">
        <div className="header-left">
          <span className="visually-hidden">Journal</span>
          {stats && <RouteHeaderSlot name="status">
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
              <span className="header-stat" title="Watch start entries">
                <span className="material-icons">play_circle</span>
                {stats.by_action_type.start || 0}
              </span>
              <span className="header-stat" title="Watch stop entries">
                <span className="material-icons">stop_circle</span>
                {stats.by_action_type.stop || 0}
              </span>
              <span className="header-stat" title="Channel Pipeline entries">
                <span className="material-icons">auto_fix_high</span>
                {stats.by_category.auto_creation || 0}
              </span>
              <span className="header-stat" title="Event Sync entries">
                <span className="material-icons">sync_alt</span>
                {stats.by_category.event_sync || 0}
              </span>
              <span className="header-total" title="Total journal entries">({stats.total_entries.toLocaleString()} total)</span>
            </div>
          </RouteHeaderSlot>}
        </div>
        {requestState !== 'permission' && <RouteHeaderSlot name="primary-action">
          <button className="btn-primary" onClick={handleRefresh} disabled={loading}>
            <span className="material-icons">refresh</span>
            Refresh
          </button>
        </RouteHeaderSlot>}
      </div>

      {/* Filters */}
      <RouteHeaderSlot name="controls">
      <DenseToolbar label="Journal entry controls" search={
        <div className="search-box">
          <span className="material-icons">search</span>
          <input
            type="text"
            placeholder="Search entries..."
            value={searchInput}
            disabled={requestState === 'loading' && entries.length === 0}
            onChange={(e) => setSearchInput(e.target.value)}
          />
          {searchInput && (
            <button
              type="button"
              className="clear-search"
              onClick={() => setSearchInput('')}
              title="Clear search"
              aria-label="Clear search"
              disabled={requestState === 'loading' && entries.length === 0}
            >
              <span className="material-icons" aria-hidden="true">close</span>
            </button>
          )}
        </div>
      } filters={<>
        <CustomSelect
          value={category}
          onChange={(val) => setCategory(val as JournalCategory | '')}
          className="journal-filter-select"
          disabled={requestState === 'loading' && entries.length === 0}
          options={[
            { value: '', label: 'All Categories' },
            { value: 'channel', label: 'Channel' },
            { value: 'epg', label: 'EPG' },
            { value: 'm3u', label: 'M3U' },
            { value: 'task', label: 'Task' },
            { value: 'watch', label: 'Watch' },
            { value: 'auto_creation', label: 'Channel Pipeline' },
            { value: 'event_sync', label: 'Event Sync' },
          ]}
        />
        <CustomSelect
          value={actionType}
          onChange={(val) => setActionType(val as JournalActionType | '')}
          className="journal-filter-select"
          disabled={requestState === 'loading' && entries.length === 0}
          options={[
            { value: '', label: 'All Actions' },
            { value: 'create', label: 'Create' },
            { value: 'update', label: 'Update' },
            { value: 'delete', label: 'Delete' },
            { value: 'start', label: 'Start' },
            { value: 'stop', label: 'Stop' },
            { value: 'refresh', label: 'Refresh' },
            { value: 'stream_add', label: 'Stream Add' },
            { value: 'stream_remove', label: 'Stream Remove' },
            { value: 'stream_reorder', label: 'Stream Reorder' },
            { value: 'reorder', label: 'Reorder' },
            {
              value: 'merge_unapplied',
              label: ACTION_TYPE_LABELS.merge_unapplied,
            },
            {
              value: 'bulk_merge_incomplete',
              label: ACTION_TYPE_LABELS.bulk_merge_incomplete,
            },
          ]}
        />
        <CustomSelect
          value={mutationSource}
          onChange={(val) => setMutationSource(val as MutationSource | '')}
          className="journal-filter-select"
          disabled={requestState === 'loading' && entries.length === 0}
          options={[
            { value: '', label: 'All Sources' },
            { value: 'ui', label: 'UI' },
            { value: 'mcp_ai', label: 'AI' },
            { value: 'scheduler', label: 'Scheduler' },
            { value: 'auto_creation', label: 'Channel Pipeline' },
          ]}
        />
      </>} secondaryActions={requestState !== 'permission' ? <div className="journal-purge-control">
        <input
          type="number"
          className="journal-purge-days-input"
          min={1}
          value={purgeDays}
          onChange={(e) => setPurgeDays(parseInt(e.target.value, 10) || 0)}
          aria-label="Days to keep"
          disabled={purging || (requestState === 'loading' && entries.length === 0)}
        />
        <span className="journal-purge-label">days</span>
        <button
          className="btn-secondary journal-purge-btn"
          onClick={handlePurge}
          disabled={purging || purgeDays < 1 || (requestState === 'loading' && entries.length === 0)}
          title="Delete journal entries older than the given number of days"
        >
          <span className={`material-icons ${purging ? 'spinning' : ''}`}>{purging ? 'sync' : 'delete_sweep'}</span>
          {purging ? 'Purging…' : 'Purge Old Entries'}
        </button>
      </div> : undefined} />
      </RouteHeaderSlot>

      {requestState === 'loading' && entries.length === 0 && <div className="tab-loading">Loading journal entries…</div>}
      {requestState === 'error' && <div className="tab-load-unavailable" role="alert">
        <p>{entries.length > 0
          ? 'Journal refresh failed — showing previously loaded entries.'
          : 'Journal entries could not be loaded.'}</p>
        <button className="btn-secondary" onClick={() => void loadEntries()}>Retry</button>
      </div>}
      {requestState === 'permission' && <div className="tab-load-unavailable" role="alert">
        <p>You don't have permission to view journal entries.</p>
      </div>}

      {/* Entries List */}
      {requestState === 'success' && entries.length === 0 ? (
        <div className="empty-state">
          <span className="material-icons">history</span>
          <h3>No journal entries</h3>
          <p>Changes to channels, EPG sources, and M3U accounts will appear here.</p>
        </div>
      ) : requestState === 'success' || entries.length > 0 ? (
        <>
          <div className="entries-list">
            <div className="list-header">
              <span>Time</span>
              <span>Category</span>
              <span>Action</span>
              <span>Entity</span>
              <span>Description</span>
              <span>Source</span>
              <span></span>
            </div>
            {entries.map((entry) => (
              <div key={entry.id} className="entry-wrapper">
                <div
                  className={`entry-row ${expandedId === entry.id ? 'expanded' : ''}`}
                  onClick={() => handleToggleExpand(entry.id)}
                  role="button"
                  tabIndex={0}
                  aria-expanded={expandedId === entry.id}
                  onKeyDown={(e) => {
                    if (e.target !== e.currentTarget) return;
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      handleToggleExpand(entry.id);
                    }
                  }}
                >
                  {/* bd-juy2e: Journal used to always show the absolute
                      timestamp; M3U Changes used a recency-threshold
                      relative time for the same kind of data (event
                      timestamps), which read as inconsistent across tabs.
                      Both now go through the same shared formatRelativeTime
                      rule — the absolute time is still one hover away. */}
                  <span className="entry-time" title={formatTimestamp(entry.timestamp)}>
                    {formatRelativeTime(entry.timestamp)}
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
                  <span className="entry-source">
                    <span className={`source-badge source-${entry.mutation_source ?? 'unknown'}`}>
                      {formatMutationSource(entry.mutation_source)}
                    </span>
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
                      {entry.before_value && (() => {
                        const { known, unknownFields } = splitBeforeValue(
                          entry.before_value as Record<string, unknown>,
                        );
                        return (
                          <div className="detail-section">
                            <h4>Before</h4>
                            {Object.keys(known).length > 0 && (
                              <pre>{JSON.stringify(known, null, 2)}</pre>
                            )}
                            {unknownFields.length > 0 && (
                              <p
                                className="detail-unknown-before"
                                data-testid="journal-before-unknown"
                              >
                                ECM could not read the previous value of{' '}
                                {unknownFields.join(', ')} — the change was recorded, but what
                                these held beforehand is not known.
                              </p>
                            )}
                          </div>
                        );
                      })()}
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
                aria-label="First page"
                title="First page"
              >
                <span className="material-icons" aria-hidden="true">first_page</span>
              </button>
              <button
                className="btn-secondary"
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page === 1 || loading}
                aria-label="Previous page"
                title="Previous page"
              >
                <span className="material-icons" aria-hidden="true">chevron_left</span>
              </button>
              <span className="page-info">
                Page {page} of {totalPages}
              </span>
              <button
                className="btn-secondary"
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page === totalPages || loading}
                aria-label="Next page"
                title="Next page"
              >
                <span className="material-icons" aria-hidden="true">chevron_right</span>
              </button>
              <button
                className="btn-secondary"
                onClick={() => setPage(totalPages)}
                disabled={page === totalPages || loading}
                aria-label="Last page"
                title="Last page"
              >
                <span className="material-icons" aria-hidden="true">last_page</span>
              </button>
            </div>
            <div className="pagination-right">
              <CustomSelect
                value={String(pageSize)}
                onChange={(val) => handlePageSizeChange(Number(val))}
                className="page-size-select"
                options={[
                  { value: '25', label: '25' },
                  { value: '50', label: '50' },
                  { value: '100', label: '100' },
                  { value: '250', label: '250' },
                ]}
              />
              <span className="page-size-label">per page</span>
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}
