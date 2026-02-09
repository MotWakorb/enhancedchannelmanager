/**
 * Watch History Panel (v0.11.0)
 * Displays a log of all channel viewing sessions
 */
import { useState, useEffect, useCallback } from 'react';
import type { WatchHistoryEntry, WatchHistoryResponse } from '../../types';
import * as api from '../../services/api';
import { useNotifications } from '../../contexts/NotificationContext';
import './WatchHistoryPanel.css';
import { formatDuration, formatRelativeTime } from '../../utils/formatting';

// Format timestamp to readable date/time, or "Still watching" if null
function formatTimestamp(isoString: string | null): string {
  if (!isoString) return 'Still watching';
  const date = new Date(isoString);
  return date.toLocaleString();
}

interface WatchHistoryPanelProps {
  refreshTrigger?: number;
}

export function WatchHistoryPanel({ refreshTrigger }: WatchHistoryPanelProps) {
  const notifications = useNotifications();
  const [data, setData] = useState<WatchHistoryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [pageSize] = useState(25);

  // Filters
  const [channelFilter, setChannelFilter] = useState('');
  const [ipFilter, setIpFilter] = useState('');
  const [daysFilter, setDaysFilter] = useState<number | undefined>(7);

  // Expanded row
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const fetchData = useCallback(async () => {
    try {
      setLoading(true);
      const result = await api.getWatchHistory({
        page,
        pageSize,
        channelId: channelFilter || undefined,
        ipAddress: ipFilter || undefined,
        days: daysFilter,
      });
      setData(result);
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Failed to load watch history', 'Watch History');
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, channelFilter, ipFilter, daysFilter]);

  useEffect(() => {
    fetchData();
  }, [fetchData, refreshTrigger]);

  // Reset to page 1 when filters change
  useEffect(() => {
    setPage(1);
  }, [channelFilter, ipFilter, daysFilter]);

  const handlePrevPage = () => {
    if (page > 1) setPage(page - 1);
  };

  const handleNextPage = () => {
    if (data && page < data.total_pages) setPage(page + 1);
  };

  const handleClearFilters = () => {
    setChannelFilter('');
    setIpFilter('');
    setDaysFilter(7);
    setPage(1);
  };

  const handleFilterByChannel = (channelId: string) => {
    setChannelFilter(channelId);
    setPage(1);
  };

  const handleFilterByIp = (ip: string) => {
    setIpFilter(ip);
    setPage(1);
  };

  const toggleExpanded = (id: number) => {
    setExpandedId(expandedId === id ? null : id);
  };

  if (loading && !data) {
    return (
      <div className="watch-history-panel">
        <div className="loading-state">Loading watch history...</div>
      </div>
    );
  }

  return (
    <div className="watch-history-panel">
      <div className="panel-header">
        <div className="header-left">
          <h3 className="section-title">Watch History</h3>
          {data && (
            <span className="total-count">{data.total} sessions</span>
          )}
        </div>
        <div className="header-right">
          <button className="refresh-btn" onClick={fetchData} disabled={loading}>
            <span className={`material-icons ${loading ? 'spinning' : ''}`}>refresh</span>
          </button>
        </div>
      </div>

      {/* Summary Stats */}
      {data && (
        <div className="summary-stats">
          <div className="stat-item">
            <span className="stat-value">{data.summary.unique_channels}</span>
            <span className="stat-label">Channels</span>
          </div>
          <div className="stat-item">
            <span className="stat-value">{data.summary.unique_ips}</span>
            <span className="stat-label">Viewers</span>
          </div>
          <div className="stat-item">
            <span className="stat-value">{formatDuration(data.summary.total_watch_seconds)}</span>
            <span className="stat-label">Total Time</span>
          </div>
          <div className="stat-item">
            <span className="stat-value">{data.total}</span>
            <span className="stat-label">Sessions</span>
          </div>
        </div>
      )}

      {/* Filters */}
      <div className="filters-bar">
        <div className="filter-group">
          <label>Time Period:</label>
          <select
            value={daysFilter || 'all'}
            onChange={(e) => setDaysFilter(e.target.value === 'all' ? undefined : Number(e.target.value))}
          >
            <option value="1">Last 24 hours</option>
            <option value="7">Last 7 days</option>
            <option value="30">Last 30 days</option>
            <option value="90">Last 90 days</option>
            <option value="all">All time</option>
          </select>
        </div>
        <div className="filter-group">
          <label>Channel:</label>
          <input
            type="text"
            placeholder="Filter by channel ID"
            value={channelFilter}
            onChange={(e) => setChannelFilter(e.target.value)}
          />
        </div>
        <div className="filter-group">
          <label>IP:</label>
          <input
            type="text"
            placeholder="Filter by IP"
            value={ipFilter}
            onChange={(e) => setIpFilter(e.target.value)}
          />
        </div>
        {(channelFilter || ipFilter || daysFilter !== 7) && (
          <button className="clear-filters-btn" onClick={handleClearFilters}>
            Clear Filters
          </button>
        )}
      </div>

      {/* History Table */}
      <div className="history-table-container">
        {data && data.history.length > 0 ? (
          <table className="history-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Channel</th>
                <th>Viewer IP</th>
                <th>Duration</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {data.history.map((entry) => (
                <>
                  <tr
                    key={entry.id}
                    className={`history-row ${expandedId === entry.id ? 'expanded' : ''} ${!entry.disconnected_at ? 'active' : ''}`}
                    onClick={() => toggleExpanded(entry.id)}
                  >
                    <td className="time-cell">
                      <span className="relative-time">{formatRelativeTime(entry.connected_at)}</span>
                    </td>
                    <td className="channel-cell">
                      <span className="channel-name">{entry.channel_name}</span>
                    </td>
                    <td className="ip-cell">
                      <span className="ip-address">{entry.ip_address}</span>
                    </td>
                    <td className="duration-cell">
                      <span className="duration">{formatDuration(entry.watch_seconds)}</span>
                    </td>
                    <td className="status-cell">
                      {entry.disconnected_at ? (
                        <span className="status completed">Completed</span>
                      ) : (
                        <span className="status watching">Watching</span>
                      )}
                    </td>
                  </tr>
                  {expandedId === entry.id && (
                    <tr className="expanded-row">
                      <td colSpan={5}>
                        <div className="expanded-content">
                          <div className="detail-grid">
                            <div className="detail-item">
                              <span className="detail-label">Connected</span>
                              <span className="detail-value">{formatTimestamp(entry.connected_at)}</span>
                            </div>
                            <div className="detail-item">
                              <span className="detail-label">Disconnected</span>
                              <span className="detail-value">{formatTimestamp(entry.disconnected_at)}</span>
                            </div>
                            <div className="detail-item">
                              <span className="detail-label">Channel ID</span>
                              <span className="detail-value channel-id">{entry.channel_id}</span>
                            </div>
                            <div className="detail-item">
                              <span className="detail-label">Date</span>
                              <span className="detail-value">{entry.date}</span>
                            </div>
                          </div>
                          <div className="action-buttons">
                            <button
                              className="filter-btn"
                              onClick={(e) => {
                                e.stopPropagation();
                                handleFilterByChannel(entry.channel_id);
                              }}
                            >
                              Filter by Channel
                            </button>
                            <button
                              className="filter-btn"
                              onClick={(e) => {
                                e.stopPropagation();
                                handleFilterByIp(entry.ip_address);
                              }}
                            >
                              Filter by IP
                            </button>
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="empty-state">
            {loading ? 'Loading...' : 'No watch history found for the selected filters.'}
          </div>
        )}
      </div>

      {/* Pagination */}
      {data && data.total_pages > 1 && (
        <div className="pagination">
          <button
            className="page-btn"
            onClick={handlePrevPage}
            disabled={page <= 1}
          >
            <span className="material-icons">chevron_left</span>
          </button>
          <span className="page-info">
            Page {page} of {data.total_pages}
          </span>
          <button
            className="page-btn"
            onClick={handleNextPage}
            disabled={page >= data.total_pages}
          >
            <span className="material-icons">chevron_right</span>
          </button>
        </div>
      )}
    </div>
  );
}
