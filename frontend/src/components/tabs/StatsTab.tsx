import { useState, useEffect, useCallback, useRef } from 'react';
import type { ChannelStatsResponse, SystemEvent, BandwidthSummary } from '../../types';
import * as api from '../../services/api';
import {
  LineChart,
  Line,
  AreaChart,
  Area,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ReferenceLine,
  ResponsiveContainer,
} from 'recharts';
import './StatsTab.css';

// Historical data point for charts
interface HistoricalDataPoint {
  timestamp: number;
  ffmpegSpeed: number;
  totalBytes: number;
  label: string; // Relative time label for X-axis
}

// Max number of data points to keep per channel
const MAX_HISTORY_POINTS = 60;

// Refresh interval options (in seconds)
const REFRESH_OPTIONS = [
  { value: 0, label: 'Manual' },
  { value: 2, label: '2s' },
  { value: 5, label: '5s' },
  { value: 10, label: '10s' },
  { value: 30, label: '30s' },
];

// Format duration from seconds or duration string to HH:MM:SS
function formatDuration(duration: string | number | undefined): string {
  if (!duration) return '-';

  let totalSeconds: number;

  if (typeof duration === 'string') {
    // Check if already in HH:MM:SS format
    if (duration.includes(':')) {
      return duration;
    }
    // Try to parse existing format like "1h 23m" or "45m 30s" or "30s"
    const hourMatch = duration.match(/(\d+)h/);
    const minMatch = duration.match(/(\d+)m/);
    const secMatch = duration.match(/(\d+)s/);

    const hours = hourMatch ? parseInt(hourMatch[1], 10) : 0;
    const mins = minMatch ? parseInt(minMatch[1], 10) : 0;
    const secs = secMatch ? parseInt(secMatch[1], 10) : 0;

    if (!hourMatch && !minMatch && !secMatch) {
      // Try parsing as seconds
      totalSeconds = parseFloat(duration);
      if (isNaN(totalSeconds)) return duration;
    } else {
      totalSeconds = hours * 3600 + mins * 60 + secs;
    }
  } else {
    totalSeconds = Math.floor(duration);
  }

  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const secs = Math.floor(totalSeconds % 60);

  // Format as HH:MM:SS
  const hh = hours.toString().padStart(2, '0');
  const mm = minutes.toString().padStart(2, '0');
  const ss = secs.toString().padStart(2, '0');

  return `${hh}:${mm}:${ss}`;
}

// Format bytes to human readable
function formatBytes(bytes: number | undefined): string {
  if (!bytes) return '-';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex++;
  }
  return `${value.toFixed(1)} ${units[unitIndex]}`;
}

// Format timestamp for events
function formatEventTime(isoString: string): string {
  const date = new Date(isoString);
  return date.toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
}

// Get speed class based on value (can be number or string)
function getSpeedClass(speed: number | string | undefined): string {
  if (speed === undefined || speed === null) return '';
  const numSpeed = typeof speed === 'number' ? speed : parseFloat(speed);
  if (isNaN(numSpeed)) return '';
  if (numSpeed >= 0.98) return 'speed-good';
  if (numSpeed >= 0.90) return 'speed-warning';
  return 'speed-bad';
}

// Format speed for display (can be number or string)
function formatSpeed(speed: number | string | undefined): string {
  if (speed === undefined || speed === null) return '-';
  const numSpeed = typeof speed === 'number' ? speed : parseFloat(speed);
  if (isNaN(numSpeed)) return String(speed);
  return `${numSpeed.toFixed(2)}x`;
}

// Format FPS for display
function formatFps(fps: number | undefined): string {
  if (fps === undefined || fps === null) return '-';
  return fps.toFixed(2);
}

// Get event type display info
function getEventTypeInfo(eventType: string): { icon: string; className: string; label: string } {
  const type = eventType.toLowerCase();
  if (type.includes('start') || type.includes('started')) {
    return { icon: 'play_circle', className: 'start', label: 'Start' };
  }
  if (type.includes('stop') || type.includes('stopped')) {
    return { icon: 'stop_circle', className: 'stop', label: 'Stop' };
  }
  if (type.includes('connect') && !type.includes('disconnect')) {
    return { icon: 'person_add', className: 'connect', label: 'Connect' };
  }
  if (type.includes('disconnect')) {
    return { icon: 'person_remove', className: 'disconnect', label: 'Disconnect' };
  }
  if (type.includes('buffer')) {
    return { icon: 'hourglass_empty', className: 'buffering', label: 'Buffering' };
  }
  if (type.includes('error')) {
    return { icon: 'error', className: 'error', label: 'Error' };
  }
  return { icon: 'info', className: '', label: eventType };
}

// Parse user agent to get short description
function parseUserAgent(ua: string | undefined): string {
  if (!ua) return 'Unknown';

  // Check for common patterns
  if (ua.includes('VLC')) return 'VLC';
  if (ua.includes('Kodi')) return 'Kodi';
  if (ua.includes('mpv')) return 'mpv';
  if (ua.includes('ffmpeg') || ua.includes('FFmpeg')) return 'FFmpeg';
  if (ua.includes('Lavf')) return 'FFmpeg/Lavf';
  if (ua.includes('Android')) return 'Android';
  if (ua.includes('iPhone') || ua.includes('iPad')) return 'iOS';
  if (ua.includes('Chrome')) return 'Chrome';
  if (ua.includes('Firefox')) return 'Firefox';
  if (ua.includes('Safari')) return 'Safari';

  // Truncate if too long
  return ua.length > 30 ? ua.substring(0, 30) + '...' : ua;
}

// Format relative time for chart X-axis
function formatRelativeTime(timestamp: number, now: number): string {
  const diffSec = Math.floor((now - timestamp) / 1000);
  if (diffSec < 60) return `${diffSec}s`;
  const diffMin = Math.floor(diffSec / 60);
  return `${diffMin}m`;
}

// Prepare chart data with relative time labels
function prepareChartData(history: HistoricalDataPoint[]): HistoricalDataPoint[] {
  if (!history || history.length === 0) return [];
  const now = Date.now();
  return history.map(point => ({
    ...point,
    label: formatRelativeTime(point.timestamp, now),
  }));
}

// Prepare 7-day bandwidth chart data (always shows 7 days)
interface BandwidthChartPoint {
  date: string;
  dateStr: string;
  bytes: number;
  isToday: boolean;
}

function prepareBandwidthChartData(dailyHistory: Array<{ date: string; bytes_transferred: number }>): BandwidthChartPoint[] {
  // Create a map of existing data by date string
  const dataMap = new Map<string, number>();
  for (const record of dailyHistory) {
    dataMap.set(record.date, record.bytes_transferred);
  }

  // Get today's date at midnight local time
  const today = new Date();
  today.setHours(0, 0, 0, 0);

  // Format date as YYYY-MM-DD in local timezone
  const formatDate = (d: Date): string => {
    const year = d.getFullYear();
    const month = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
  };

  const todayStr = formatDate(today);

  // Generate last 7 days (from 6 days ago to today)
  const result: BandwidthChartPoint[] = [];
  for (let i = 6; i >= 0; i--) {
    const date = new Date(today.getTime() - i * 24 * 60 * 60 * 1000);
    const dateStr = formatDate(date);
    const dayLabel = date.toLocaleDateString('en-US', { weekday: 'short' });
    const bytes = dataMap.get(dateStr) || 0;

    result.push({
      date: dayLabel,
      dateStr: dateStr,
      bytes: bytes,
      isToday: dateStr === todayStr,
    });
  }

  return result;
}

// Custom tooltip for speed chart
function SpeedTooltip({ active, payload }: { active?: boolean; payload?: Array<{ value: number }> }) {
  if (active && payload && payload.length) {
    return (
      <div className="chart-tooltip">
        <span className="tooltip-value">{payload[0].value.toFixed(2)}x</span>
      </div>
    );
  }
  return null;
}

// Custom tooltip for data chart
function DataTooltip({ active, payload }: { active?: boolean; payload?: Array<{ value: number }> }) {
  if (active && payload && payload.length) {
    return (
      <div className="chart-tooltip">
        <span className="tooltip-value">{formatBytes(payload[0].value)}</span>
      </div>
    );
  }
  return null;
}

export function StatsTab() {
  // Data state
  const [channelStats, setChannelStats] = useState<ChannelStatsResponse | null>(null);
  const [events, setEvents] = useState<SystemEvent[]>([]);
  const [bandwidthStats, setBandwidthStats] = useState<BandwidthSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  // Auto-refresh state
  const [refreshInterval, setRefreshInterval] = useState(5); // Default 5 seconds
  const refreshTimerRef = useRef<number | null>(null);
  const lastRefreshRef = useRef<Date>(new Date());

  // Expanded channel state
  const [expandedChannels, setExpandedChannels] = useState<Set<string | number>>(new Set());

  // Event filter state
  const [eventFilter, setEventFilter] = useState<string>('');

  // Build lookup maps for channel names by UUID and stream profiles by ID
  const channelNameMap = useRef<Map<string, { name: string; number: number | null }>>(new Map());
  const streamProfileMap = useRef<Map<string, string>>(new Map());

  // Historical data for charts (per channel)
  const channelHistory = useRef<Map<string, HistoricalDataPoint[]>>(new Map());

  // Load all channels for name lookup (paginated to get all)
  const loadAllChannels = useCallback(async () => {
    try {
      const map = new Map<string, { name: string; number: number | null }>();
      let page = 1;
      let hasMore = true;
      const pageSize = 500;

      while (hasMore) {
        const result = await api.getChannels({ page, pageSize });
        for (const ch of result.results || []) {
          if (ch.uuid) {
            map.set(ch.uuid, { name: ch.name, number: ch.channel_number });
          }
        }
        hasMore = result.next !== null;
        page++;
        // Safety limit to prevent infinite loops
        if (page > 20) break;
      }

      channelNameMap.current = map;
      console.log(`Loaded ${map.size} channels for lookup`);
    } catch (err) {
      console.error('Failed to load channels for name lookup:', err);
    }
  }, []);

  // Load stream profiles for lookup
  const loadStreamProfiles = useCallback(async () => {
    try {
      const profiles = await api.getStreamProfiles();
      const map = new Map<string, string>();
      for (const profile of profiles) {
        map.set(String(profile.id), profile.name);
      }
      streamProfileMap.current = map;
      console.log(`Loaded ${map.size} stream profiles for lookup`);
    } catch (err) {
      console.error('Failed to load stream profiles:', err);
    }
  }, []);

  // Fetch stats data
  const fetchData = useCallback(async (showLoading = false) => {
    if (showLoading) setLoading(true);
    setRefreshing(true);
    setError(null);

    try {
      const [statsResult, eventsResult, bandwidthResult] = await Promise.all([
        api.getChannelStats(),
        api.getSystemEvents({ limit: 50 }),
        api.getBandwidthStats().catch(() => null), // Don't fail if bandwidth stats unavailable
      ]);

      // Accumulate historical data for charts
      const now = Date.now();
      const activeChannelIds = new Set<string>();

      if (statsResult?.channels) {
        for (const channel of statsResult.channels) {
          const channelId = String(channel.channel_id);
          activeChannelIds.add(channelId);

          // Parse ffmpeg_speed (can be number or string like "1.02x")
          let speed = 0;
          if (channel.ffmpeg_speed !== undefined && channel.ffmpeg_speed !== null) {
            speed = typeof channel.ffmpeg_speed === 'number'
              ? channel.ffmpeg_speed
              : parseFloat(String(channel.ffmpeg_speed));
            if (isNaN(speed)) speed = 0;
          }

          // Get total bytes
          const totalBytes = channel.total_bytes || 0;

          // Create data point
          const dataPoint: HistoricalDataPoint = {
            timestamp: now,
            ffmpegSpeed: speed,
            totalBytes: totalBytes,
            label: '', // Will be computed when rendering
          };

          // Add to history
          const history = channelHistory.current.get(channelId) || [];
          history.push(dataPoint);

          // Trim to max points
          if (history.length > MAX_HISTORY_POINTS) {
            history.shift();
          }

          channelHistory.current.set(channelId, history);
        }
      }

      // Clean up history for channels that are no longer active
      for (const channelId of channelHistory.current.keys()) {
        if (!activeChannelIds.has(channelId)) {
          channelHistory.current.delete(channelId);
        }
      }

      setChannelStats(statsResult);
      setEvents(eventsResult.results || []);
      if (bandwidthResult) {
        setBandwidthStats(bandwidthResult);
      }
      lastRefreshRef.current = new Date();
    } catch (err) {
      console.error('Failed to fetch stats:', err);
      setError(err instanceof Error ? err.message : 'Failed to fetch stats');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  // Initial load - fetch lookups first, then stats
  useEffect(() => {
    const loadLookups = async () => {
      setLoading(true);
      // Load channels and stream profiles in parallel
      await Promise.all([
        loadAllChannels(),
        loadStreamProfiles(),
      ]);
      // Now load stats
      await fetchData(false);
      setLoading(false);
    };
    loadLookups();
  }, [loadAllChannels, loadStreamProfiles, fetchData]);

  // Auto-refresh timer
  useEffect(() => {
    if (refreshTimerRef.current) {
      clearInterval(refreshTimerRef.current);
      refreshTimerRef.current = null;
    }

    if (refreshInterval > 0) {
      refreshTimerRef.current = window.setInterval(() => {
        fetchData(false);
      }, refreshInterval * 1000);
    }

    return () => {
      if (refreshTimerRef.current) {
        clearInterval(refreshTimerRef.current);
      }
    };
  }, [refreshInterval, fetchData]);

  // Handle stop channel
  const handleStopChannel = async (channelId: string | number) => {
    if (!confirm('Are you sure you want to stop this channel?')) return;

    try {
      await api.stopChannel(channelId);
      fetchData(false);
    } catch (err) {
      console.error('Failed to stop channel:', err);
      alert('Failed to stop channel');
    }
  };

  // Toggle expanded channel
  const toggleExpanded = (channelId: string | number) => {
    setExpandedChannels(prev => {
      const next = new Set(prev);
      if (next.has(channelId)) {
        next.delete(channelId);
      } else {
        next.add(channelId);
      }
      return next;
    });
  };

  // Filter events
  const filteredEvents = eventFilter
    ? events.filter(e => e.event_type?.toLowerCase().includes(eventFilter.toLowerCase()))
    : events;

  // Calculate totals
  const totalClients = channelStats?.channels?.reduce((sum, ch) => sum + (ch.client_count || 0), 0) || 0;
  const activeChannels = channelStats?.count || 0;

  // Calculate connections per M3U
  const m3uConnections = (() => {
    const connections = new Map<string, { id: number; name: string; count: number }>();
    if (channelStats?.channels) {
      for (const ch of channelStats.channels) {
        if (ch.m3u_profile_id && ch.m3u_profile_name) {
          const key = String(ch.m3u_profile_id);
          const existing = connections.get(key);
          if (existing) {
            existing.count += 1;
          } else {
            connections.set(key, {
              id: ch.m3u_profile_id,
              name: ch.m3u_profile_name,
              count: 1,
            });
          }
        }
      }
    }
    // Sort by name for consistent display
    return Array.from(connections.values()).sort((a, b) => a.name.localeCompare(b.name));
  })();

  if (loading) {
    return (
      <div className="stats-tab">
        <div className="stats-loading">
          <span className="material-icons">sync</span>
          <p>Loading stats...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="stats-tab">
      {/* Header */}
      <div className="stats-header">
        <div className="header-left">
          <h2>Live Stats</h2>
          <div className="header-summary">
            <div className="summary-stat">
              <span className="material-icons">live_tv</span>
              <div>
                <div className="stat-value">{activeChannels}</div>
                <div className="stat-label">Active Channels</div>
              </div>
            </div>
            <div className="summary-stat">
              <span className="material-icons">people</span>
              <div>
                <div className="stat-value">{totalClients}</div>
                <div className="stat-label">Connected Clients</div>
              </div>
            </div>
            {m3uConnections.length > 0 && (
              <div className="m3u-connections">
                {m3uConnections.map((m3u) => (
                  <div key={m3u.id} className="m3u-stat">
                    <span className="m3u-count">{m3u.count}</span>
                    <span className="m3u-name">{m3u.name}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="header-actions">
          <div className={`refresh-indicator ${refreshInterval > 0 ? 'active' : ''}`}>
            <span className={`material-icons ${refreshing ? 'spinning' : ''}`}>
              {refreshing ? 'sync' : 'schedule'}
            </span>
            {refreshInterval > 0 ? `Auto-refresh: ${refreshInterval}s` : 'Manual refresh'}
          </div>

          <select
            className="refresh-select"
            value={refreshInterval}
            onChange={(e) => setRefreshInterval(Number(e.target.value))}
          >
            {REFRESH_OPTIONS.map(opt => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>

          <button
            className="btn-secondary"
            onClick={() => fetchData(false)}
            disabled={refreshing}
          >
            <span className="material-icons">refresh</span>
            Refresh
          </button>
        </div>
      </div>

      {/* Error state */}
      {error && (
        <div className="stats-error">
          <span className="material-icons">error</span>
          <p>{error}</p>
        </div>
      )}

      {/* Content */}
      <div className="stats-content">
        {/* No streams */}
        {!error && activeChannels === 0 && (
          <div className="no-streams">
            <span className="material-icons">tv_off</span>
            <p>No active streams</p>
            <p>Streams will appear here when clients start watching channels</p>
          </div>
        )}

        {/* Active Channels */}
        {activeChannels > 0 && (
          <div className="active-channels">
            <h3 className="section-title">Active Channels</h3>

            {channelStats?.channels?.map((channel) => {
              // Try to look up channel name from ECM's channel data by UUID
              const channelIdStr = String(channel.channel_id);
              const isUUID = channelIdStr.includes('-') && channelIdStr.length > 20;
              const lookupData = isUUID ? channelNameMap.current.get(channelIdStr) : null;

              // Determine the best name to display (priority: ECM lookup > channel_name > stream_name > fallback)
              const displayName = lookupData?.name
                || channel.channel_name
                || channel.stream_name
                || (isUUID ? `Channel ${channelIdStr.substring(0, 8)}...` : `Channel ${channelIdStr}`);

              // Determine channel number (priority: ECM lookup > API channel_number > none)
              const channelNum = lookupData?.number || channel.channel_number;
              const displayNumber = channelNum ? `Ch ${channelNum}` : null;

              // M3U source info
              const m3uSource = channel.m3u_profile_name || null;

              // Stream profile lookup
              const streamProfileName = channel.stream_profile
                ? streamProfileMap.current.get(channel.stream_profile)
                : null;

              return (
              <div key={channel.channel_id} className="channel-card">
                <div className="channel-card-header">
                  <div className="channel-info">
                    {displayNumber && (
                      <span className="channel-number" title={`ID: ${channelIdStr}`}>
                        {displayNumber}
                      </span>
                    )}
                    <span className="channel-name" title={`${displayName}${channel.stream_name && channel.stream_name !== displayName ? ` (Stream: ${channel.stream_name})` : ''}`}>
                      {displayName}
                    </span>
                    {m3uSource && (
                      <span className="m3u-source" title={`M3U Source: ${m3uSource}`}>
                        {m3uSource}
                      </span>
                    )}
                    {streamProfileName && (
                      <span className="stream-profile" title={`Stream Profile: ${streamProfileName}`}>
                        {streamProfileName}
                      </span>
                    )}
                    <span className={`channel-state ${channel.state?.toLowerCase() || ''}`}>
                      <span className="material-icons">
                        {channel.state === 'buffering' ? 'hourglass_empty' : 'play_arrow'}
                      </span>
                      {channel.state || 'Streaming'}
                    </span>
                  </div>

                  <div className="channel-actions">
                    <button
                      onClick={() => toggleExpanded(channel.channel_id)}
                      title={expandedChannels.has(channel.channel_id) ? 'Collapse' : 'Expand'}
                    >
                      <span className="material-icons">
                        {expandedChannels.has(channel.channel_id) ? 'expand_less' : 'expand_more'}
                      </span>
                    </button>
                    <button
                      className="stop-btn"
                      onClick={() => handleStopChannel(channel.channel_id)}
                      title="Stop channel"
                    >
                      <span className="material-icons">stop</span>
                    </button>
                  </div>
                </div>

                {/* Quick Stats */}
                <div className="channel-stats">
                  <div className="stat-item">
                    <span className="stat-label">Clients</span>
                    <span className="stat-value">{channel.client_count || 0}</span>
                  </div>
                  <div className="stat-item">
                    <span className="stat-label">Bitrate</span>
                    <span className="stat-value">{channel.avg_bitrate || '-'}</span>
                  </div>
                  <div className="stat-item">
                    <span className="stat-label">Speed</span>
                    <span className={`stat-value ${getSpeedClass(channel.ffmpeg_speed)}`}>
                      {formatSpeed(channel.ffmpeg_speed)}
                    </span>
                  </div>
                  <div className="stat-item">
                    <span className="stat-label">FPS</span>
                    <span className="stat-value">{formatFps(channel.source_fps || channel.actual_fps || channel.ffmpeg_fps)}</span>
                  </div>
                  <div className="stat-item">
                    <span className="stat-label">Resolution</span>
                    <span className="stat-value">{channel.resolution || '-'}</span>
                  </div>
                  <div className="stat-item">
                    <span className="stat-label">Uptime</span>
                    <span className="stat-value">{formatDuration(channel.uptime)}</span>
                  </div>
                  <div className="stat-item">
                    <span className="stat-label">Data</span>
                    <span className="stat-value">{channel.total_data || formatBytes(channel.total_bytes)}</span>
                  </div>
                </div>

                {/* Expanded Details */}
                {expandedChannels.has(channel.channel_id) && (
                  <div className="channel-details">
                    {/* Performance Graphs */}
                    {(() => {
                      const history = channelHistory.current.get(channelIdStr) || [];
                      const chartData = prepareChartData(history);
                      if (chartData.length < 2) {
                        return (
                          <div className="channel-graphs">
                            <div className="graph-container graph-placeholder">
                              <div className="graph-title">FFmpeg Speed</div>
                              <div className="graph-waiting">
                                <span className="material-icons">hourglass_empty</span>
                                <span>Collecting data...</span>
                              </div>
                            </div>
                            <div className="graph-container graph-placeholder">
                              <div className="graph-title">Data Transfer</div>
                              <div className="graph-waiting">
                                <span className="material-icons">hourglass_empty</span>
                                <span>Collecting data...</span>
                              </div>
                            </div>
                          </div>
                        );
                      }
                      return (
                        <div className="channel-graphs">
                          <div className="graph-container">
                            <div className="graph-title">FFmpeg Speed</div>
                            <ResponsiveContainer width="100%" height={160}>
                              <LineChart data={chartData} margin={{ top: 5, right: 5, bottom: 5, left: 5 }}>
                                <XAxis
                                  dataKey="label"
                                  tick={{ fontSize: 10, fill: 'var(--text-muted)' }}
                                  axisLine={{ stroke: 'var(--border-primary)' }}
                                  tickLine={false}
                                  interval="preserveStartEnd"
                                />
                                <YAxis
                                  domain={[0.8, 1.2]}
                                  tick={{ fontSize: 10, fill: 'var(--text-muted)' }}
                                  axisLine={{ stroke: 'var(--border-primary)' }}
                                  tickLine={false}
                                  tickFormatter={(v) => `${v}x`}
                                  width={35}
                                />
                                <Tooltip content={<SpeedTooltip />} />
                                <ReferenceLine
                                  y={1}
                                  stroke="var(--success)"
                                  strokeDasharray="3 3"
                                  strokeOpacity={0.5}
                                />
                                <Line
                                  type="monotone"
                                  dataKey="ffmpegSpeed"
                                  stroke="var(--accent-primary)"
                                  strokeWidth={2}
                                  dot={false}
                                  isAnimationActive={false}
                                />
                              </LineChart>
                            </ResponsiveContainer>
                          </div>
                          <div className="graph-container">
                            <div className="graph-title">Data Transfer</div>
                            <ResponsiveContainer width="100%" height={160}>
                              <AreaChart data={chartData} margin={{ top: 5, right: 5, bottom: 5, left: 5 }}>
                                <XAxis
                                  dataKey="label"
                                  tick={{ fontSize: 10, fill: 'var(--text-muted)' }}
                                  axisLine={{ stroke: 'var(--border-primary)' }}
                                  tickLine={false}
                                  interval="preserveStartEnd"
                                />
                                <YAxis
                                  tick={{ fontSize: 10, fill: 'var(--text-muted)' }}
                                  axisLine={{ stroke: 'var(--border-primary)' }}
                                  tickLine={false}
                                  tickFormatter={(v) => formatBytes(v)}
                                  width={55}
                                />
                                <Tooltip content={<DataTooltip />} />
                                <Area
                                  type="monotone"
                                  dataKey="totalBytes"
                                  stroke="var(--accent-secondary)"
                                  fill="var(--accent-primary)"
                                  fillOpacity={0.3}
                                  strokeWidth={2}
                                  isAnimationActive={false}
                                />
                              </AreaChart>
                            </ResponsiveContainer>
                          </div>
                        </div>
                      );
                    })()}

                    <div className="details-grid">
                      <div className="detail-group">
                        <div className="detail-group-title">Video</div>
                        <div className="detail-row">
                          <span className="label">Codec</span>
                          <span className="value">{channel.video_codec || '-'}</span>
                        </div>
                        <div className="detail-row">
                          <span className="label">Resolution</span>
                          <span className="value">{channel.resolution || '-'}</span>
                        </div>
                        <div className="detail-row">
                          <span className="label">FPS</span>
                          <span className="value">{formatFps(channel.source_fps || channel.actual_fps || channel.ffmpeg_fps)}</span>
                        </div>
                      </div>

                      <div className="detail-group">
                        <div className="detail-group-title">Audio</div>
                        <div className="detail-row">
                          <span className="label">Codec</span>
                          <span className="value">{channel.audio_codec || '-'}</span>
                        </div>
                        <div className="detail-row">
                          <span className="label">Channels</span>
                          <span className="value">{channel.audio_channels || '-'}</span>
                        </div>
                      </div>

                      <div className="detail-group">
                        <div className="detail-group-title">Stream</div>
                        <div className="detail-row">
                          <span className="label">Type</span>
                          <span className="value">{channel.stream_type || '-'}</span>
                        </div>
                        <div className="detail-row">
                          <span className="label">Buffer Index</span>
                          <span className="value">{channel.buffer_index ?? '-'}</span>
                        </div>
                        <div className="detail-row">
                          <span className="label">Avg Bitrate</span>
                          <span className="value">{channel.avg_bitrate || (channel.avg_bitrate_kbps ? `${channel.avg_bitrate_kbps.toFixed(2)} kbps` : '-')}</span>
                        </div>
                      </div>

                      <div className="detail-group">
                        <div className="detail-group-title">Performance</div>
                        <div className="detail-row">
                          <span className="label">FFmpeg Speed</span>
                          <span className="value">{formatSpeed(channel.ffmpeg_speed)}</span>
                        </div>
                        <div className="detail-row">
                          <span className="label">Total Data</span>
                          <span className="value">{channel.total_data || formatBytes(channel.total_bytes)}</span>
                        </div>
                        <div className="detail-row">
                          <span className="label">Stream ID</span>
                          <span className="value">{channel.stream_id || '-'}</span>
                        </div>
                      </div>
                    </div>
                  </div>
                )}

                {/* Clients */}
                {channel.clients && channel.clients.length > 0 && (
                  <div className="channel-clients">
                    <div className="clients-header">
                      <span className="material-icons">people</span>
                      Connected Clients ({channel.clients.length})
                    </div>
                    <div className="client-list">
                      {channel.clients.map((client, idx) => (
                        <div key={client.client_id || idx} className="client-item">
                          <div className="client-info">
                            <span className="client-ip">{client.ip_address || 'Unknown'}</span>
                            <span className="client-ua">{parseUserAgent(client.user_agent)}</span>
                          </div>
                          <div className="client-stats">
                            <span className="client-duration">
                              {formatDuration(client.connection_duration)}
                            </span>
                            {client.current_rate_KBps && (
                              <span className="client-rate">
                                {client.current_rate_KBps.toFixed(1)} KB/s
                              </span>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            );
            })}
          </div>
        )}

        {/* System Events */}
        {events.length > 0 && (
          <div className="events-section">
            <div className="events-header">
              <h3 className="section-title">Recent Events</h3>
              <div className="events-filter">
                <select
                  value={eventFilter}
                  onChange={(e) => setEventFilter(e.target.value)}
                >
                  <option value="">All Events</option>
                  <option value="start">Channel Start</option>
                  <option value="stop">Channel Stop</option>
                  <option value="connect">Client Connect</option>
                  <option value="disconnect">Client Disconnect</option>
                  <option value="buffer">Buffering</option>
                  <option value="error">Errors</option>
                </select>
              </div>
            </div>

            <div className="events-list">
              {filteredEvents.map((event) => {
                const typeInfo = getEventTypeInfo(event.event_type);
                return (
                  <div key={event.id} className="event-item">
                    <span className="event-time">
                      {formatEventTime(event.timestamp || event.created_at)}
                    </span>
                    <span className={`event-type ${typeInfo.className}`}>
                      <span className="material-icons">{typeInfo.icon}</span>
                      {typeInfo.label}
                    </span>
                    <span className="event-message">
                      {event.channel_name && `[${event.channel_name}] `}
                      {event.message || event.event_type}
                      {event.ip_address && ` - ${event.ip_address}`}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Bandwidth Usage Summary */}
        {bandwidthStats && (
          <div className="bandwidth-section">
            <h3 className="section-title">Bandwidth Usage</h3>
            <div className="bandwidth-summary">
              <div className="bandwidth-stat">
                <span className="bandwidth-label">Today</span>
                <span className="bandwidth-value">{formatBytes(bandwidthStats.today)}</span>
              </div>
              <div className="bandwidth-stat">
                <span className="bandwidth-label">This Week</span>
                <span className="bandwidth-value">{formatBytes(bandwidthStats.this_week)}</span>
              </div>
              <div className="bandwidth-stat">
                <span className="bandwidth-label">This Month</span>
                <span className="bandwidth-value">{formatBytes(bandwidthStats.this_month)}</span>
              </div>
              <div className="bandwidth-stat">
                <span className="bandwidth-label">This Year</span>
                <span className="bandwidth-value">{formatBytes(bandwidthStats.this_year)}</span>
              </div>
              <div className="bandwidth-stat">
                <span className="bandwidth-label">All Time</span>
                <span className="bandwidth-value">{formatBytes(bandwidthStats.all_time)}</span>
              </div>
            </div>
            {(() => {
              const chartData = prepareBandwidthChartData(bandwidthStats.daily_history || []);
              // Find max for scaling - ensure we have a reasonable minimum
              const maxBytes = Math.max(...chartData.map(d => d.bytes), 1024);

              // Custom bar shape to handle fill color
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              const CustomBar = (props: any) => {
                const { x = 0, y = 0, width = 0, height = 0, payload } = props;
                const fill = payload?.isToday ? '#14b8a6' : '#3b82f6';
                const radius = 4;
                // Render empty rect if no height
                if (height <= 0) {
                  return <rect x={x} y={y} width={width} height={0} fill="transparent" />;
                }
                return (
                  <path
                    d={`M${x},${y + height}
                        L${x},${y + radius}
                        Q${x},${y} ${x + radius},${y}
                        L${x + width - radius},${y}
                        Q${x + width},${y} ${x + width},${y + radius}
                        L${x + width},${y + height}
                        Z`}
                    fill={fill}
                  />
                );
              };

              return (
                <div className="bandwidth-chart">
                  <div className="chart-title">Last 7 Days</div>
                  <ResponsiveContainer width="100%" height={150}>
                    <BarChart
                      data={chartData}
                      margin={{ top: 10, right: 20, bottom: 5, left: 10 }}
                      barCategoryGap="15%"
                    >
                      <XAxis
                        dataKey="date"
                        tick={{ fontSize: 11, fill: 'var(--text-muted)' }}
                        axisLine={{ stroke: 'var(--border-primary)' }}
                        tickLine={false}
                      />
                      <YAxis
                        domain={[0, maxBytes * 1.1]}
                        tick={{ fontSize: 10, fill: 'var(--text-muted)' }}
                        axisLine={{ stroke: 'var(--border-primary)' }}
                        tickLine={false}
                        tickFormatter={(v) => formatBytes(v)}
                        width={65}
                      />
                      <Tooltip content={<DataTooltip />} />
                      <Bar
                        dataKey="bytes"
                        maxBarSize={60}
                        isAnimationActive={false}
                        shape={CustomBar}
                      />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              );
            })()}
          </div>
        )}
      </div>
    </div>
  );
}
