/**
 * Enhanced Statistics Panel (v0.11.0)
 * Displays unique viewers and per-channel bandwidth statistics
 */
import { useState, useEffect, useCallback } from 'react';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  LineChart,
  Line,
} from 'recharts';
import type {
  UniqueViewersSummary,
  ChannelBandwidthStats,
  ChannelUniqueViewers,
} from '../../types';
import * as api from '../../services/api';
import './EnhancedStatsPanel.css';

// Format bytes to human readable
function formatBytes(bytes: number): string {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex++;
  }
  return `${value.toFixed(1)} ${units[unitIndex]}`;
}

// Format seconds to human readable duration
function formatWatchTime(seconds: number): string {
  if (!seconds) return '0m';
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (hours > 0) {
    return `${hours}h ${minutes}m`;
  }
  return `${minutes}m`;
}

// Custom tooltip for charts
interface TooltipPayload {
  value: number;
  dataKey: string;
  payload: {
    date?: string;
    unique_count?: number;
    bytes?: number;
    label?: string;
  };
}

function ChartTooltip({ active, payload }: { active?: boolean; payload?: TooltipPayload[] }) {
  if (!active || !payload || payload.length === 0) return null;
  const data = payload[0].payload;
  return (
    <div className="enhanced-stats-tooltip">
      <div className="tooltip-label">{data.date || data.label}</div>
      {data.unique_count !== undefined && (
        <div className="tooltip-value">{data.unique_count} unique viewers</div>
      )}
      {data.bytes !== undefined && (
        <div className="tooltip-value">{formatBytes(data.bytes)}</div>
      )}
    </div>
  );
}

interface EnhancedStatsPanelProps {
  refreshTrigger?: number;
}

export function EnhancedStatsPanel({ refreshTrigger }: EnhancedStatsPanelProps) {
  const [uniqueViewers, setUniqueViewers] = useState<UniqueViewersSummary | null>(null);
  const [channelBandwidth, setChannelBandwidth] = useState<ChannelBandwidthStats[]>([]);
  const [channelViewers, setChannelViewers] = useState<ChannelUniqueViewers[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeView, setActiveView] = useState<'viewers' | 'bandwidth'>('viewers');
  const [bandwidthSortBy, setBandwidthSortBy] = useState<'bytes' | 'connections' | 'watch_time'>('bytes');

  const fetchData = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const [viewersData, bandwidthData, channelViewersData] = await Promise.all([
        api.getUniqueViewersSummary(7),
        api.getChannelBandwidthStats(7, 20, bandwidthSortBy),
        api.getUniqueViewersByChannel(7, 20),
      ]);
      setUniqueViewers(viewersData);
      setChannelBandwidth(bandwidthData);
      setChannelViewers(channelViewersData);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load enhanced stats');
    } finally {
      setLoading(false);
    }
  }, [bandwidthSortBy]);

  useEffect(() => {
    fetchData();
  }, [fetchData, refreshTrigger]);

  if (loading && !uniqueViewers) {
    return (
      <div className="enhanced-stats-panel">
        <div className="loading-state">Loading enhanced statistics...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="enhanced-stats-panel">
        <div className="error-state">{error}</div>
      </div>
    );
  }

  // Prepare chart data for daily unique viewers
  const dailyChartData = uniqueViewers?.daily_unique.map(d => ({
    date: new Date(d.date).toLocaleDateString('en-US', { weekday: 'short' }),
    unique_count: d.unique_count,
  })) || [];

  return (
    <div className="enhanced-stats-panel">
      <div className="panel-header">
        <h3 className="section-title">Enhanced Statistics</h3>
        <div className="view-toggle">
          <button
            className={`toggle-btn ${activeView === 'viewers' ? 'active' : ''}`}
            onClick={() => setActiveView('viewers')}
          >
            Unique Viewers
          </button>
          <button
            className={`toggle-btn ${activeView === 'bandwidth' ? 'active' : ''}`}
            onClick={() => setActiveView('bandwidth')}
          >
            Channel Bandwidth
          </button>
        </div>
      </div>

      {activeView === 'viewers' && uniqueViewers && (
        <div className="viewers-section">
          {/* Summary Stats */}
          <div className="stats-summary">
            <div className="stat-card">
              <span className="stat-value">{uniqueViewers.total_unique_viewers}</span>
              <span className="stat-label">Unique Viewers (7d)</span>
            </div>
            <div className="stat-card">
              <span className="stat-value">{uniqueViewers.today_unique_viewers}</span>
              <span className="stat-label">Today</span>
            </div>
            <div className="stat-card">
              <span className="stat-value">{uniqueViewers.total_connections}</span>
              <span className="stat-label">Total Connections</span>
            </div>
            <div className="stat-card">
              <span className="stat-value">{formatWatchTime(uniqueViewers.avg_watch_seconds)}</span>
              <span className="stat-label">Avg Watch Time</span>
            </div>
          </div>

          {/* Daily Unique Viewers Chart */}
          {dailyChartData.length > 0 && (
            <div className="chart-container">
              <div className="chart-title">Daily Unique Viewers</div>
              <ResponsiveContainer width="100%" height={150}>
                <LineChart data={dailyChartData} margin={{ top: 10, right: 20, bottom: 5, left: 10 }}>
                  <XAxis
                    dataKey="date"
                    tick={{ fontSize: 11, fill: 'var(--text-muted)' }}
                    axisLine={{ stroke: 'var(--border-primary)' }}
                    tickLine={false}
                  />
                  <YAxis
                    tick={{ fontSize: 10, fill: 'var(--text-muted)' }}
                    axisLine={{ stroke: 'var(--border-primary)' }}
                    tickLine={false}
                    width={40}
                  />
                  <Tooltip content={<ChartTooltip />} />
                  <Line
                    type="monotone"
                    dataKey="unique_count"
                    stroke="#14b8a6"
                    strokeWidth={2}
                    dot={{ fill: '#14b8a6', strokeWidth: 0, r: 3 }}
                    activeDot={{ r: 5 }}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Top Viewers */}
          {uniqueViewers.top_viewers.length > 0 && (
            <div className="top-viewers-section">
              <div className="subsection-title">Top Viewers by Connections</div>
              <div className="viewers-list">
                {uniqueViewers.top_viewers.map((viewer, index) => (
                  <div key={viewer.ip_address} className="viewer-item">
                    <span className="viewer-rank">#{index + 1}</span>
                    <span className="viewer-ip">{viewer.ip_address}</span>
                    <span className="viewer-stats">
                      {viewer.connection_count} connections, {formatWatchTime(viewer.total_watch_seconds)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Channels by Unique Viewers */}
          {channelViewers.length > 0 && (
            <div className="channel-viewers-section">
              <div className="subsection-title">Channels by Unique Viewers</div>
              <div className="channel-list">
                {channelViewers.map((channel, index) => (
                  <div key={channel.channel_id} className="channel-item">
                    <span className="channel-rank">#{index + 1}</span>
                    <span className="channel-name">{channel.channel_name}</span>
                    <span className="channel-viewers">{channel.unique_viewers} viewers</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {activeView === 'bandwidth' && (
        <div className="bandwidth-section">
          {/* Sort Toggle */}
          <div className="sort-controls">
            <span className="sort-label">Sort by:</span>
            <button
              className={`sort-btn ${bandwidthSortBy === 'bytes' ? 'active' : ''}`}
              onClick={() => setBandwidthSortBy('bytes')}
            >
              Bandwidth
            </button>
            <button
              className={`sort-btn ${bandwidthSortBy === 'connections' ? 'active' : ''}`}
              onClick={() => setBandwidthSortBy('connections')}
            >
              Connections
            </button>
            <button
              className={`sort-btn ${bandwidthSortBy === 'watch_time' ? 'active' : ''}`}
              onClick={() => setBandwidthSortBy('watch_time')}
            >
              Watch Time
            </button>
          </div>

          {/* Channel Bandwidth Chart */}
          {channelBandwidth.length > 0 && (
            <div className="chart-container">
              <div className="chart-title">
                {bandwidthSortBy === 'bytes' && 'Channel Bandwidth (7 days)'}
                {bandwidthSortBy === 'connections' && 'Channel Connections (7 days)'}
                {bandwidthSortBy === 'watch_time' && 'Channel Watch Time (7 days)'}
              </div>
              <ResponsiveContainer width="100%" height={200}>
                <BarChart
                  data={channelBandwidth.slice(0, 10)}
                  margin={{ top: 10, right: 20, bottom: 60, left: 10 }}
                >
                  <XAxis
                    dataKey="channel_name"
                    tick={{ fontSize: 10, fill: 'var(--text-muted)', angle: -45, textAnchor: 'end' }}
                    axisLine={{ stroke: 'var(--border-primary)' }}
                    tickLine={false}
                    height={60}
                  />
                  <YAxis
                    tick={{ fontSize: 10, fill: 'var(--text-muted)' }}
                    axisLine={{ stroke: 'var(--border-primary)' }}
                    tickLine={false}
                    tickFormatter={(v) => {
                      if (bandwidthSortBy === 'bytes') return formatBytes(v);
                      if (bandwidthSortBy === 'watch_time') return formatWatchTime(v);
                      return String(v);
                    }}
                    width={70}
                  />
                  <Tooltip
                    cursor={false}
                    content={({ active, payload }) => {
                      if (!active || !payload || payload.length === 0) return null;
                      const data = payload[0].payload as ChannelBandwidthStats;
                      return (
                        <div className="enhanced-stats-tooltip">
                          <div className="tooltip-label">{data.channel_name}</div>
                          <div className="tooltip-value">{formatBytes(data.total_bytes)}</div>
                          <div className="tooltip-detail">{data.total_connections} connections</div>
                          <div className="tooltip-detail">{formatWatchTime(data.total_watch_seconds)} watch time</div>
                        </div>
                      );
                    }}
                  />
                  <Bar
                    dataKey={
                      bandwidthSortBy === 'bytes' ? 'total_bytes' :
                      bandwidthSortBy === 'connections' ? 'total_connections' :
                      'total_watch_seconds'
                    }
                    fill={
                      bandwidthSortBy === 'bytes' ? '#3b82f6' :
                      bandwidthSortBy === 'connections' ? '#22c55e' :
                      '#f59e0b'
                    }
                    radius={[4, 4, 0, 0]}
                    isAnimationActive={false}
                    activeBar={{
                      fill: bandwidthSortBy === 'bytes' ? '#3b82f6' :
                            bandwidthSortBy === 'connections' ? '#22c55e' :
                            '#f59e0b'
                    }}
                  />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Channel Bandwidth List */}
          {channelBandwidth.length > 0 && (
            <div className="channel-bandwidth-list">
              <div className="list-header">
                <span className="col-rank">#</span>
                <span className="col-name">Channel</span>
                <span className="col-bandwidth">Bandwidth</span>
                <span className="col-connections">Connections</span>
                <span className="col-time">Watch Time</span>
              </div>
              {channelBandwidth.map((channel, index) => (
                <div key={channel.channel_id} className="bandwidth-item">
                  <span className="col-rank">{index + 1}</span>
                  <span className="col-name">{channel.channel_name}</span>
                  <span className="col-bandwidth">{formatBytes(channel.total_bytes)}</span>
                  <span className="col-connections">{channel.total_connections}</span>
                  <span className="col-time">{formatWatchTime(channel.total_watch_seconds)}</span>
                </div>
              ))}
            </div>
          )}

          {channelBandwidth.length === 0 && (
            <div className="empty-state">No channel bandwidth data available yet.</div>
          )}
        </div>
      )}
    </div>
  );
}
