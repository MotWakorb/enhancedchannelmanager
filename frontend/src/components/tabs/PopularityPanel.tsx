/**
 * Popularity Panel (v0.11.0)
 * Displays channel popularity rankings and trends
 */
import { useState, useEffect, useCallback } from 'react';
import type {
  ChannelPopularityScore,
  PopularityRankingsResponse,
} from '../../types';
import * as api from '../../services/api';
import { useNotifications } from '../../contexts/NotificationContext';
import './PopularityPanel.css';
import { formatBytes, formatWatchTime } from '../../utils/formatting';

// Get trend icon
function getTrendIcon(trend: string): string {
  switch (trend) {
    case 'up':
      return '↑';
    case 'down':
      return '↓';
    default:
      return '→';
  }
}

// Get trend class
function getTrendClass(trend: string): string {
  switch (trend) {
    case 'up':
      return 'trend-up';
    case 'down':
      return 'trend-down';
    default:
      return 'trend-stable';
  }
}

interface PopularityPanelProps {
  refreshTrigger?: number;
}

export function PopularityPanel({ refreshTrigger }: PopularityPanelProps) {
  const [rankings, setRankings] = useState<ChannelPopularityScore[]>([]);
  const [trendingUp, setTrendingUp] = useState<ChannelPopularityScore[]>([]);
  const [trendingDown, setTrendingDown] = useState<ChannelPopularityScore[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [calculating, setCalculating] = useState(false);
  const [activeView, setActiveView] = useState<'rankings' | 'trending'>('rankings');
  const [expandedChannel, setExpandedChannel] = useState<string | null>(null);
  const notifications = useNotifications();

  const fetchData = useCallback(async () => {
    try {
      setLoading(true);
      const [rankingsData, upData, downData] = await Promise.all([
        api.getPopularityRankings(50, 0),
        api.getTrendingChannels('up', 10),
        api.getTrendingChannels('down', 10),
      ]);
      setRankings(rankingsData.rankings);
      setTotal(rankingsData.total);
      setTrendingUp(upData);
      setTrendingDown(downData);
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Failed to load popularity data', 'Popularity');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData, refreshTrigger]);

  const handleCalculate = async () => {
    try {
      setCalculating(true);
      const result = await api.calculatePopularity(7);
      notifications.success(
        `Calculated ${result.channels_scored} channels (${result.channels_created} new, ${result.channels_updated} updated)`,
        'Popularity Calculated'
      );
      // Refresh data after calculation
      await fetchData();
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to calculate popularity';
      notifications.error(message, 'Calculation Failed');
    } finally {
      setCalculating(false);
    }
  };

  const toggleExpanded = (channelId: string) => {
    setExpandedChannel(expandedChannel === channelId ? null : channelId);
  };

  if (loading && rankings.length === 0) {
    return (
      <div className="popularity-panel">
        <div className="loading-state">Loading popularity data...</div>
      </div>
    );
  }

  return (
    <div className="popularity-panel">
      <div className="panel-header">
        <div className="header-left">
          <h3 className="section-title">Popularity Rankings</h3>
          <span className="total-count">{total} channels</span>
        </div>
        <div className="header-right">
          <div className="view-toggle">
            <button
              className={`toggle-btn ${activeView === 'rankings' ? 'active' : ''}`}
              onClick={() => setActiveView('rankings')}
            >
              Rankings
            </button>
            <button
              className={`toggle-btn ${activeView === 'trending' ? 'active' : ''}`}
              onClick={() => setActiveView('trending')}
            >
              Trending
            </button>
          </div>
          <button
            className="calculate-btn"
            onClick={handleCalculate}
            disabled={calculating}
          >
            {calculating ? 'Calculating...' : 'Recalculate'}
          </button>
        </div>
      </div>

      {activeView === 'rankings' && (
        <div className="rankings-section">
          {rankings.length > 0 ? (
            <div className="rankings-list">
              {rankings.map((channel) => (
                <div
                  key={channel.channel_id}
                  className={`ranking-item ${expandedChannel === channel.channel_id ? 'expanded' : ''}`}
                  onClick={() => toggleExpanded(channel.channel_id)}
                >
                  <div className="ranking-main">
                    <span className="rank">#{channel.rank}</span>
                    <div className="channel-info">
                      <span className="channel-name">{channel.channel_name}</span>
                      <span className={`trend ${getTrendClass(channel.trend)}`}>
                        {getTrendIcon(channel.trend)} {Math.abs(channel.trend_percent).toFixed(1)}%
                      </span>
                    </div>
                    <div className="score-bar-container">
                      <div className="score-bar-track">
                        <div
                          className="score-bar"
                          style={{ width: `${channel.score}%` }}
                        />
                      </div>
                      <span className="score-value">{channel.score.toFixed(1)}</span>
                    </div>
                  </div>
                  {expandedChannel === channel.channel_id && (
                    <div className="ranking-details">
                      <div className="detail-grid">
                        <div className="detail-item">
                          <span className="detail-label">Watch Count</span>
                          <span className="detail-value">{channel.watch_count_7d}</span>
                        </div>
                        <div className="detail-item">
                          <span className="detail-label">Watch Time</span>
                          <span className="detail-value">{formatWatchTime(channel.watch_time_7d)}</span>
                        </div>
                        <div className="detail-item">
                          <span className="detail-label">Unique Viewers</span>
                          <span className="detail-value">{channel.unique_viewers_7d}</span>
                        </div>
                        <div className="detail-item">
                          <span className="detail-label">Bandwidth</span>
                          <span className="detail-value">{formatBytes(channel.bandwidth_7d)}</span>
                        </div>
                      </div>
                      {channel.previous_rank && (
                        <div className="rank-change">
                          Previous rank: #{channel.previous_rank}
                          {channel.previous_rank > channel.rank! && (
                            <span className="rank-improved"> (↑ {channel.previous_rank - channel.rank!})</span>
                          )}
                          {channel.previous_rank < channel.rank! && (
                            <span className="rank-declined"> (↓ {channel.rank! - channel.previous_rank})</span>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <div className="empty-state">
              No popularity data available. Click "Recalculate" to generate rankings.
            </div>
          )}
        </div>
      )}

      {activeView === 'trending' && (
        <div className="trending-section">
          <div className="trending-columns">
            <div className="trending-column trending-up">
              <div className="column-header">
                <span className="trend-icon up">↑</span>
                Trending Up
              </div>
              {trendingUp.length > 0 ? (
                <div className="trending-list">
                  {trendingUp.map((channel) => (
                    <div key={channel.channel_id} className="trending-item">
                      <span className="trending-rank">#{channel.rank}</span>
                      <span className="trending-name">{channel.channel_name}</span>
                      <span className="trending-change up">+{channel.trend_percent.toFixed(1)}%</span>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="empty-trending">No channels trending up</div>
              )}
            </div>
            <div className="trending-column trending-down">
              <div className="column-header">
                <span className="trend-icon down">↓</span>
                Trending Down
              </div>
              {trendingDown.length > 0 ? (
                <div className="trending-list">
                  {trendingDown.map((channel) => (
                    <div key={channel.channel_id} className="trending-item">
                      <span className="trending-rank">#{channel.rank}</span>
                      <span className="trending-name">{channel.channel_name}</span>
                      <span className="trending-change down">{channel.trend_percent.toFixed(1)}%</span>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="empty-trending">No channels trending down</div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
