/**
 * Bandwidth Panel (v0.11.0)
 * Displays inbound/outbound bandwidth statistics with charts
 */
import { useState, useEffect, useCallback } from 'react';
import type { BandwidthSummary } from '../../types';
import * as api from '../../services/api';
import './BandwidthPanel.css';
import { formatBytes, formatBitrate, formatDateLabel } from '../../utils/formatting';

interface BandwidthPanelProps {
  refreshTrigger?: number;
}

export function BandwidthPanel({ refreshTrigger }: BandwidthPanelProps) {
  const [data, setData] = useState<BandwidthSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const result = await api.getBandwidthStats();
      setData(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load bandwidth data');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData, refreshTrigger]);

  // Calculate max value for chart scaling
  const getMaxValue = (): number => {
    if (!data?.daily_history.length) return 1;
    let max = 0;
    for (const day of data.daily_history) {
      max = Math.max(max, day.bytes_in || 0, day.bytes_out || 0);
    }
    return max || 1;
  };

  // Calculate bar height as percentage
  const getBarHeight = (value: number, max: number): number => {
    if (!max || !value) return 2;
    return Math.max(2, (value / max) * 100);
  };

  // Calculate ratio percentage
  const getRatio = (): { inPercent: number; outPercent: number } => {
    if (!data) return { inPercent: 50, outPercent: 50 };
    const total = (data.week_in || 0) + (data.week_out || 0);
    if (!total) return { inPercent: 50, outPercent: 50 };
    const inPercent = Math.round((data.week_in / total) * 100);
    return { inPercent, outPercent: 100 - inPercent };
  };

  if (loading && !data) {
    return (
      <div className="bandwidth-panel">
        <div className="loading-state">Loading bandwidth data...</div>
      </div>
    );
  }

  const maxValue = getMaxValue();
  const ratio = getRatio();

  return (
    <div className="bandwidth-panel">
      <div className="panel-header">
        <div className="header-left">
          <h3 className="section-title">Bandwidth In/Out</h3>
        </div>
        <div className="header-right">
          <button className="refresh-btn" onClick={fetchData} disabled={loading}>
            <span className={`material-icons ${loading ? 'spinning' : ''}`}>refresh</span>
          </button>
        </div>
      </div>

      {error && <div className="error-message">{error}</div>}

      {data && (
        <>
          {/* Summary Grid - Inbound vs Outbound */}
          <div className="summary-grid">
            <div className="summary-card">
              <div className="summary-card-header">
                <span className="direction-icon inbound">↓</span>
                <span className="direction-label">Inbound (from providers)</span>
              </div>
              <div className="summary-stats">
                <div className="stat-item">
                  <div className="stat-value">{formatBytes(data.today_in || 0)}</div>
                  <div className="stat-label">Today</div>
                </div>
                <div className="stat-item">
                  <div className="stat-value">{formatBytes(data.week_in || 0)}</div>
                  <div className="stat-label">This Week</div>
                </div>
                <div className="stat-item">
                  <div className="stat-value">{formatBytes(data.all_time_in || 0)}</div>
                  <div className="stat-label">All Time</div>
                </div>
              </div>
            </div>

            <div className="summary-card">
              <div className="summary-card-header">
                <span className="direction-icon outbound">↑</span>
                <span className="direction-label">Outbound (to viewers)</span>
              </div>
              <div className="summary-stats">
                <div className="stat-item">
                  <div className="stat-value">{formatBytes(data.today_out || 0)}</div>
                  <div className="stat-label">Today</div>
                </div>
                <div className="stat-item">
                  <div className="stat-value">{formatBytes(data.week_out || 0)}</div>
                  <div className="stat-label">This Week</div>
                </div>
                <div className="stat-item">
                  <div className="stat-value">{formatBytes(data.all_time_out || 0)}</div>
                  <div className="stat-label">All Time</div>
                </div>
              </div>
            </div>
          </div>

          {/* Peak Bitrates */}
          <div className="peak-section">
            <div className="peak-header">Peak Bitrates (Today)</div>
            <div className="peak-grid">
              <div className="peak-card">
                <span className="peak-icon inbound">↓</span>
                <div className="peak-info">
                  <div className="peak-label">Peak Inbound</div>
                  <div className="peak-value">{formatBitrate(data.today_peak_bitrate_in || 0)}</div>
                </div>
              </div>
              <div className="peak-card">
                <span className="peak-icon outbound">↑</span>
                <div className="peak-info">
                  <div className="peak-label">Peak Outbound</div>
                  <div className="peak-value">{formatBitrate(data.today_peak_bitrate_out || 0)}</div>
                </div>
              </div>
            </div>
          </div>

          {/* Ratio Bar */}
          <div className="ratio-section">
            <div className="ratio-header">Bandwidth Ratio (This Week)</div>
            <div className="ratio-bar">
              <div
                className="ratio-segment inbound"
                style={{ width: `${ratio.inPercent}%` }}
              >
                {ratio.inPercent > 10 && `${ratio.inPercent}%`}
              </div>
              <div
                className="ratio-segment outbound"
                style={{ width: `${ratio.outPercent}%` }}
              >
                {ratio.outPercent > 10 && `${ratio.outPercent}%`}
              </div>
            </div>
            <div className="ratio-labels">
              <span>Inbound: {formatBytes(data.week_in || 0)}</span>
              <span>Outbound: {formatBytes(data.week_out || 0)}</span>
            </div>
          </div>

          {/* Daily Chart */}
          {data.daily_history && data.daily_history.length > 0 && (
            <div className="chart-section">
              <div className="chart-header">
                <span className="chart-title">Last 7 Days</span>
                <div className="chart-legend">
                  <div className="legend-item">
                    <span className="legend-dot inbound"></span>
                    <span>Inbound</span>
                  </div>
                  <div className="legend-item">
                    <span className="legend-dot outbound"></span>
                    <span>Outbound</span>
                  </div>
                </div>
              </div>
              <div className="chart-container">
                <div className="bar-chart">
                  {data.daily_history.map((day) => (
                    <div key={day.date} className="chart-day">
                      <div className="bars-container">
                        <div
                          className="bar inbound"
                          style={{ height: `${getBarHeight(day.bytes_in || 0, maxValue)}%` }}
                          title={`Inbound: ${formatBytes(day.bytes_in || 0)}`}
                        />
                        <div
                          className="bar outbound"
                          style={{ height: `${getBarHeight(day.bytes_out || 0, maxValue)}%` }}
                          title={`Outbound: ${formatBytes(day.bytes_out || 0)}`}
                        />
                      </div>
                      <div className="day-label">{formatDateLabel(day.date)}</div>
                      <div className="day-value">{formatBytes(day.bytes_out || day.bytes_transferred || 0)}</div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </>
      )}

      {!data && !loading && !error && (
        <div className="empty-state">No bandwidth data available yet.</div>
      )}
    </div>
  );
}
