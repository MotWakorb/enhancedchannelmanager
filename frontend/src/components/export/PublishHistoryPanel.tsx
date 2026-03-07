import { useState, useEffect, useCallback } from 'react';
import type { PublishHistoryEntry, PublishHistoryResponse, PublishConfig } from '../../types/export';
import * as exportApi from '../../services/exportApi';
import { useNotifications } from '../../contexts/NotificationContext';
import { CustomSelect } from '../CustomSelect';

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString();
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDuration(start: string, end: string | null): string {
  if (!end) return 'Running...';
  const ms = new Date(end).getTime() - new Date(start).getTime();
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

const STATUS_CLASSES: Record<string, string> = {
  success: 'status-success',
  failed: 'status-failed',
  running: 'status-running',
};

export function PublishHistoryPanel() {
  const notifications = useNotifications();
  const [data, setData] = useState<PublishHistoryResponse | null>(null);
  const [configs, setConfigs] = useState<PublishConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  // Filters
  const [filterConfigId, setFilterConfigId] = useState<string>('');
  const [filterStatus, setFilterStatus] = useState<string>('');
  const [page, setPage] = useState(1);

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [history, cfgs] = await Promise.all([
        exportApi.getPublishHistory({
          config_id: filterConfigId ? Number(filterConfigId) : undefined,
          status: filterStatus || undefined,
          page,
          per_page: 20,
        }),
        exportApi.getPublishConfigs(),
      ]);
      setData(history);
      setConfigs(cfgs);
    } catch (e) {
      notifications.error(e instanceof Error ? e.message : 'Failed to load history');
    } finally {
      setLoading(false);
    }
  }, [filterConfigId, filterStatus, page]);

  useEffect(() => { loadData(); }, [loadData]);

  const handleDeleteEntry = async (entry: PublishHistoryEntry) => {
    try {
      await exportApi.deleteHistoryEntry(entry.id);
      notifications.success('Entry deleted');
      loadData();
    } catch (e) {
      notifications.error(e instanceof Error ? e.message : 'Delete failed');
    }
  };

  const handleBulkDelete = async () => {
    try {
      const result = await exportApi.deleteHistoryBulk(30);
      notifications.success(`Deleted ${result.deleted} entries older than 30 days`);
      loadData();
    } catch (e) {
      notifications.error(e instanceof Error ? e.message : 'Bulk delete failed');
    }
  };

  const configOptions = [
    { value: '', label: 'All Configs' },
    ...configs.map(c => ({ value: String(c.id), label: c.name })),
  ];

  const statusOptions = [
    { value: '', label: 'All Statuses' },
    { value: 'success', label: 'Success' },
    { value: 'failed', label: 'Failed' },
    { value: 'running', label: 'Running' },
  ];

  const totalPages = data ? Math.ceil(data.total / 20) : 0;

  return (
    <div className="publish-history">
      <div className="profile-list-header">
        <h3>Publish History</h3>
        <button className="btn btn-sm" onClick={handleBulkDelete} title="Delete entries older than 30 days">
          <span className="material-icons">delete_sweep</span>
          Clean Old
        </button>
      </div>

      <div className="publish-history-filters">
        <CustomSelect
          value={filterConfigId}
          onChange={val => { setFilterConfigId(val); setPage(1); }}
          options={configOptions}
        />
        <CustomSelect
          value={filterStatus}
          onChange={val => { setFilterStatus(val); setPage(1); }}
          options={statusOptions}
        />
        <span className="publish-history-total">{data?.total || 0} entries</span>
      </div>

      {loading ? (
        <div className="export-loading">
          <span className="material-icons spinning">sync</span>
        </div>
      ) : !data || data.entries.length === 0 ? (
        <div className="profile-list-empty">
          <span className="material-icons">history</span>
          <p>No publish history yet</p>
        </div>
      ) : (
        <>
          <div className="publish-history-table">
            <div className="publish-history-header-row">
              <span>Time</span>
              <span>Config</span>
              <span>Status</span>
              <span>Channels</span>
              <span>Size</span>
              <span>Duration</span>
              <span></span>
            </div>
            {data.entries.map(entry => (
              <div key={entry.id} className="publish-history-entry">
                <div
                  className="publish-history-row"
                  onClick={() => setExpandedId(expandedId === entry.id ? null : entry.id)}
                >
                  <span className="publish-history-cell">{formatDate(entry.started_at)}</span>
                  <span className="publish-history-cell">{entry.config_name || `#${entry.config_id}`}</span>
                  <span className="publish-history-cell">
                    <span className={`status-badge ${STATUS_CLASSES[entry.status] || ''}`}>
                      {entry.status}
                    </span>
                  </span>
                  <span className="publish-history-cell">{entry.channels_count ?? '—'}</span>
                  <span className="publish-history-cell">
                    {entry.file_size_bytes != null ? formatSize(entry.file_size_bytes) : '—'}
                  </span>
                  <span className="publish-history-cell">
                    {formatDuration(entry.started_at, entry.completed_at)}
                  </span>
                  <span className="publish-history-cell publish-history-actions">
                    <button
                      className="btn btn-sm btn-icon"
                      onClick={(e) => { e.stopPropagation(); handleDeleteEntry(entry); }}
                      title="Delete"
                    >
                      <span className="material-icons">close</span>
                    </button>
                  </span>
                </div>
                {expandedId === entry.id && (
                  <div className="publish-history-details">
                    {entry.profile_name && <div>Profile: {entry.profile_name}</div>}
                    {entry.error_message && (
                      <div className="publish-history-error">Error: {entry.error_message}</div>
                    )}
                    {entry.details && (
                      <pre className="publish-history-details-json">
                        {JSON.stringify(entry.details, null, 2)}
                      </pre>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>

          {totalPages > 1 && (
            <div className="publish-history-pagination">
              <button className="btn btn-sm" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>
                <span className="material-icons">chevron_left</span>
              </button>
              <span>Page {page} of {totalPages}</span>
              <button className="btn btn-sm" disabled={page >= totalPages} onClick={() => setPage(p => p + 1)}>
                <span className="material-icons">chevron_right</span>
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
