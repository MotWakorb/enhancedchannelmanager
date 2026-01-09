import { useState, useEffect, useCallback, useRef } from 'react';
import type { ChannelStatsResponse, SystemEvent } from '../../types';
import * as api from '../../services/api';
import './StatsTab.css';

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

export function StatsTab() {
  // Data state
  const [channelStats, setChannelStats] = useState<ChannelStatsResponse | null>(null);
  const [events, setEvents] = useState<SystemEvent[]>([]);
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
      const [statsResult, eventsResult] = await Promise.all([
        api.getChannelStats(),
        api.getSystemEvents({ limit: 50 }),
      ]);

      setChannelStats(statsResult);
      setEvents(eventsResult.results || []);
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
      </div>
    </div>
  );
}
