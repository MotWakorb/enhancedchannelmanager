/**
 * User Watch-Time Panel (v0.17.0 — GH-62, bd-skqln.6).
 *
 * Renders as the 5th panel in Stats tab. Reads from the watch-time-by-user
 * read API shipped in bd-skqln.5:
 *   - GET /api/stats/watch-time?group_by=total      → user totals table
 *   - GET /api/stats/watch-time?group_by=day        → daily trend chart
 *   - GET /api/stats/watch-time/{user_id}           → channel drill-down
 *
 * Auth posture: watch-time stats are admin-only (PO directive 2026-05-13).
 *
 *   - When useAuth() reports a known non-admin user, render the
 *     admin-only notice and never call the API.
 *   - When useAuth() reports null user (auth-disabled mode), call the API
 *     anyway — the backend's admin-only filter is the source of truth and
 *     no-caller is treated as admin-equivalent. If a 403 comes back, fall
 *     back to the same admin-only notice.
 *
 * Accessibility (mandatory per bead acceptance):
 *   - Semantic h3 heading (matches sibling panels).
 *   - Data-table fallback for the chart, always present in the DOM as a
 *     visually-hidden <table> so screen readers can read the values. A
 *     toggle button lets sighted users reveal it on screen.
 *   - aria-live="polite" empty-state announce.
 *   - Drill-down rows are <button>s with accessible names tied to the
 *     username — keyboard-traversable.
 *   - Date-range <select> has an explicit <label>.
 *   - Theme-token text colors (var(--text-primary)) carry the WCAG AA
 *     4.5:1 contrast already audited at the theme layer (index.css).
 *
 * Out of scope (deferred): heat map by hour-of-day, device breakdown,
 * favorite-channel badge, chart-primitive extraction.
 */
import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  ResponsiveContainer,
} from 'recharts';
import { useAuth } from '../../hooks/useAuth';
import * as api from '../../services/api';
import { HttpError } from '../../services/httpClient';
import type {
  WatchTimeUserTotalRow,
  WatchTimeUserDayRow,
  WatchTimeChannelRow,
  WatchTimeTotalsResponse,
  WatchTimeDailyResponse,
} from '../../types';
import './UserStatsPanel.css';

type RangePreset = '7' | '30' | '90';

const RANGE_OPTIONS: Array<{ value: RangePreset; label: string }> = [
  { value: '7', label: 'Last 7 days' },
  { value: '30', label: 'Last 30 days' },
  { value: '90', label: 'Last 90 days' },
];

interface DailyTrendPoint {
  day: string;       // "YYYY-MM-DD"
  minutes: number;   // sum of watch-minutes across all users that day
}

function rangeIso(daysBack: number): { from: string; to: string } {
  const to = new Date();
  const from = new Date(to.getTime() - daysBack * 86_400_000);
  return { from: from.toISOString(), to: to.toISOString() };
}

function secondsToMinutes(seconds: number): number {
  return Math.round(seconds / 60);
}

function formatLastWatched(iso: string | null): string {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '—';
    return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
  } catch {
    return '—';
  }
}

function aggregateDailyMinutes(rows: WatchTimeUserDayRow[]): DailyTrendPoint[] {
  const byDay = new Map<string, number>();
  for (const r of rows) {
    byDay.set(r.day, (byDay.get(r.day) ?? 0) + r.watch_seconds);
  }
  return Array.from(byDay.entries())
    .map(([day, seconds]) => ({ day, minutes: secondsToMinutes(seconds) }))
    .sort((a, b) => a.day.localeCompare(b.day));
}

function isAdminOnly403(err: unknown): boolean {
  return err instanceof HttpError && err.status === 403;
}

export function UserStatsPanel() {
  const { user, isLoading: authLoading } = useAuth();
  // When the auth context knows the user and they're not admin, short-circuit.
  // user === null (auth-disabled mode) is treated as admin-equivalent and we
  // let the backend's admin-only filter speak.
  const knownNonAdmin = user !== null && !user.is_admin;

  const [rangeDays, setRangeDays] = useState<RangePreset>('30');
  const [totals, setTotals] = useState<WatchTimeUserTotalRow[]>([]);
  const [daily, setDaily] = useState<WatchTimeUserDayRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [adminOnly, setAdminOnly] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showChartTable, setShowChartTable] = useState(false);
  const [selectedUser, setSelectedUser] = useState<WatchTimeUserTotalRow | null>(null);
  const [channelBreakdown, setChannelBreakdown] = useState<WatchTimeChannelRow[]>([]);
  const [breakdownLoading, setBreakdownLoading] = useState(false);

  // Track a request token so a stale response from an old range can't clobber
  // a fresh one.
  const requestTokenRef = useRef(0);

  const fetchData = useCallback(async () => {
    if (knownNonAdmin) return;
    const myToken = ++requestTokenRef.current;
    setLoading(true);
    setError(null);
    const { from, to } = rangeIso(Number(rangeDays));
    try {
      const [totalsRes, dailyRes] = await Promise.all([
        api.getWatchTimeByUser({ from, to, groupBy: 'total' }) as Promise<WatchTimeTotalsResponse>,
        api.getWatchTimeByUser({ from, to, groupBy: 'day' }) as Promise<WatchTimeDailyResponse>,
      ]);
      if (myToken !== requestTokenRef.current) return;
      setTotals(totalsRes.data);
      setDaily(dailyRes.data);
      setAdminOnly(false);
    } catch (err) {
      if (myToken !== requestTokenRef.current) return;
      if (isAdminOnly403(err)) {
        setAdminOnly(true);
      } else {
        setError(err instanceof Error ? err.message : 'Failed to load watch-time stats');
      }
      setTotals([]);
      setDaily([]);
    } finally {
      if (myToken === requestTokenRef.current) {
        setLoading(false);
      }
    }
  }, [rangeDays, knownNonAdmin]);

  useEffect(() => {
    if (knownNonAdmin) {
      setLoading(false);
      return;
    }
    fetchData();
  }, [fetchData, knownNonAdmin]);

  const handleSelectUser = useCallback(async (row: WatchTimeUserTotalRow) => {
    setSelectedUser(row);
    setBreakdownLoading(true);
    setChannelBreakdown([]);
    const { from, to } = rangeIso(Number(rangeDays));
    try {
      const res = await api.getWatchTimeForUser(row.user_id, { from, to });
      setChannelBreakdown(res.data);
    } catch (err) {
      if (isAdminOnly403(err)) {
        setAdminOnly(true);
      } else {
        setError(err instanceof Error ? err.message : 'Failed to load channel breakdown');
      }
    } finally {
      setBreakdownLoading(false);
    }
  }, [rangeDays]);

  const dailyTrend = useMemo(() => aggregateDailyMinutes(daily), [daily]);

  // Auth still resolving — stay quiet until we know the posture.
  if (authLoading) {
    return (
      <div className="user-stats-panel">
        <h3 className="section-title">User Watch Time</h3>
        <div className="loading-state">Loading…</div>
      </div>
    );
  }

  // Known non-admin: short-circuit before any API call.
  if (knownNonAdmin || adminOnly) {
    return (
      <div className="user-stats-panel">
        <h3 className="section-title">User Watch Time</h3>
        <div className="admin-only-state" role="note">
          User watch-time statistics require admin access.
        </div>
      </div>
    );
  }

  return (
    <div className="user-stats-panel">
      <div className="panel-header">
        <h3 className="section-title">User Watch Time</h3>
        <div className="panel-controls">
          <label className="range-control">
            <span className="range-label">Date range</span>
            <select
              className="range-select"
              aria-label="Date range"
              value={rangeDays}
              onChange={(e) => setRangeDays(e.target.value as RangePreset)}
            >
              {RANGE_OPTIONS.map(opt => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
          </label>
        </div>
      </div>

      {/* Daily trend chart */}
      <div className="chart-section">
        <div className="chart-toolbar">
          <h4 className="chart-title">Daily watch-minutes</h4>
          <button
            type="button"
            className="chart-data-toggle"
            onClick={() => setShowChartTable(prev => !prev)}
            aria-expanded={showChartTable}
            aria-controls="user-stats-chart-data-table"
          >
            {showChartTable ? 'Hide chart data' : 'Show chart data'}
          </button>
        </div>
        <div className="chart-container" aria-hidden="true">
          <ResponsiveContainer width="100%" height={180}>
            <LineChart data={dailyTrend} margin={{ top: 10, right: 16, bottom: 8, left: 8 }}>
              <CartesianGrid stroke="var(--border-primary)" strokeDasharray="3 3" />
              <XAxis
                dataKey="day"
                tick={{ fontSize: 11, fill: 'var(--text-primary)' }}
                axisLine={{ stroke: 'var(--border-primary)' }}
                tickLine={false}
              />
              <YAxis
                tick={{ fontSize: 11, fill: 'var(--text-primary)' }}
                axisLine={{ stroke: 'var(--border-primary)' }}
                tickLine={false}
                width={40}
              />
              <Tooltip />
              <Line
                type="monotone"
                dataKey="minutes"
                stroke="#14b8a6"
                strokeWidth={2}
                dot={{ fill: '#14b8a6', r: 3 }}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
        {/* Data-table fallback for the chart — always in the DOM for SR users,
            visually-hidden by default. */}
        <table
          id="user-stats-chart-data-table"
          className={`chart-data-table ${showChartTable ? 'visible' : 'visually-hidden'}`}
        >
          <caption>Daily watch-minutes data table</caption>
          <thead>
            <tr>
              <th scope="col">Day</th>
              <th scope="col">Watch minutes</th>
            </tr>
          </thead>
          <tbody>
            {dailyTrend.length === 0 ? (
              <tr>
                <td colSpan={2}>No data</td>
              </tr>
            ) : (
              dailyTrend.map(point => (
                <tr key={point.day}>
                  <td>{point.day}</td>
                  <td>{point.minutes}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Error banner (non-403) */}
      {error && (
        <div className="error-state" role="alert">{error}</div>
      )}

      {/* Empty state — aria-live so SR users hear the result of the fetch. */}
      {!loading && totals.length === 0 && !error && (
        <div className="empty-state" role="status" aria-live="polite">
          No watch data yet for this range.
        </div>
      )}

      {/* User totals table */}
      {totals.length > 0 && (
        <div className="user-totals-section">
          <table className="user-totals-table">
            <caption className="visually-hidden">Watch-time totals by user</caption>
            <thead>
              <tr>
                <th scope="col">User</th>
                <th scope="col">Total minutes</th>
                <th scope="col">Last watched</th>
                <th scope="col"><span className="visually-hidden">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {totals.map(row => (
                <tr key={row.user_id} className={selectedUser?.user_id === row.user_id ? 'selected' : ''}>
                  <td>{row.username ?? `User #${row.user_id}`}</td>
                  <td>{secondsToMinutes(row.total_watch_seconds)} min</td>
                  <td>{formatLastWatched(row.last_watched)}</td>
                  <td>
                    <button
                      type="button"
                      className="drill-down-btn"
                      aria-label={`View watch-time details for ${row.username ?? `user ${row.user_id}`}`}
                      onClick={() => handleSelectUser(row)}
                    >
                      Details
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Drill-down breakdown */}
      {selectedUser && (
        <div className="channel-breakdown-section">
          <h4 className="breakdown-title">
            Channel breakdown — {selectedUser.username ?? `User #${selectedUser.user_id}`}
          </h4>
          {breakdownLoading ? (
            <div className="loading-state">Loading channel breakdown…</div>
          ) : channelBreakdown.length === 0 ? (
            <div className="empty-state" role="status" aria-live="polite">
              No channel watch data for this user in the selected range.
            </div>
          ) : (
            <table className="channel-breakdown-table">
              <caption className="visually-hidden">
                Channel breakdown for {selectedUser.username ?? `user ${selectedUser.user_id}`}
              </caption>
              <thead>
                <tr>
                  <th scope="col">Channel</th>
                  <th scope="col">Total minutes</th>
                  <th scope="col">Last watched</th>
                </tr>
              </thead>
              <tbody>
                {channelBreakdown.map(ch => (
                  <tr key={ch.channel_id}>
                    <td>{ch.channel_name}</td>
                    <td>{secondsToMinutes(ch.total_watch_seconds)} min</td>
                    <td>{formatLastWatched(ch.last_watched)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}
