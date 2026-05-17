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
import { streamLabel } from '../../utils/formatting';
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

/**
 * Format a UTC day-string ("YYYY-MM-DD") as a short localized label.
 *
 * The backend buckets by UTC day (SQLite `date(observed_at/1000, 'unixepoch')`)
 * but renders to operators who think in their own local timezone. Two
 * approaches are possible:
 *
 *   (A) Relabel: keep server-side UTC bucketing, but show the
 *       most-overlapping local day. ← chosen
 *   (B) Client-side re-bucketing: fetch wider, re-bin per local day.
 *
 * (A) is correct enough: the UTC day "2026-05-14" spans 00:00–24:00 UTC,
 * which is at most one local-day boundary off (a few hours of the UTC
 * day fall in the previous local day). Anchoring at 12:00 UTC means we
 * convert to the local day that owns the *majority* of the bucket, for
 * every IANA-recognized tz between UTC-12 and UTC+14. Worst case the
 * label is off by one date for the bucket whose data is dominated by the
 * other local day — acceptable for a 30-day trend chart.
 *
 * `locale` and `timeZone` are optional injection points for unit tests.
 * In production we pass nothing and let the browser pick.
 */
// eslint-disable-next-line react-refresh/only-export-components -- pure helper co-located with the only component that uses it; splitting is mechanical churn
export function formatLocalDayLabel(
  utcDayStr: string,
  locale?: string,
  timeZone?: string,
): string {
  // Noon UTC is safely inside the bucket for every fixed tz offset between
  // -12 and +14 hours, so the date that Intl.DateTimeFormat resolves in
  // the operator's tz is the bucket's most-overlapping local day.
  const anchor = new Date(`${utcDayStr}T12:00:00Z`);
  if (Number.isNaN(anchor.getTime())) return utcDayStr;
  return new Intl.DateTimeFormat(locale, {
    month: 'short',
    day: 'numeric',
    timeZone,
  }).format(anchor);
}

/**
 * Returns true when the UTC day-string maps to the operator's current
 * local-tz date. Uses the same most-overlapping-day anchor as
 * `formatLocalDayLabel` so the "today" marker lines up with the label.
 *
 * `now` and `timeZone` are injection points for unit tests.
 */
// eslint-disable-next-line react-refresh/only-export-components -- pure helper co-located with the only component that uses it; splitting is mechanical churn
export function isTodayInLocalTz(
  utcDayStr: string,
  now: Date = new Date(),
  timeZone?: string,
): boolean {
  // Format both the bucket's anchor and "now" through the same Intl
  // formatter in the target tz, then compare the year/month/day parts.
  // This avoids any manual offset math (DST, half-hour tz, etc.).
  const anchor = new Date(`${utcDayStr}T12:00:00Z`);
  if (Number.isNaN(anchor.getTime())) return false;
  const fmt = new Intl.DateTimeFormat('en-CA', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    timeZone,
  });
  // en-CA renders ISO-ish "YYYY-MM-DD" — easy to compare as strings.
  return fmt.format(anchor) === fmt.format(now);
}

/** Tol "bright" palette yellow — used as the in-progress amber. Imported
 *  inline rather than from chartPalette so this file stays self-contained
 *  and the test mock doesn't need to know about the palette. */
const IN_PROGRESS_COLOR = '#ccbb44';
const COMPLETE_COLOR = '#14b8a6';

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

  // Enrich each point with its localized label and an in-progress flag for
  // today. Computed in a memo so re-renders don't redo Intl work per point.
  const dailyTrendDecorated = useMemo(() => {
    const now = new Date();
    return dailyTrend.map(p => ({
      ...p,
      label: formatLocalDayLabel(p.day),
      isToday: isTodayInLocalTz(p.day, now),
    }));
  }, [dailyTrend]);

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
            <LineChart data={dailyTrendDecorated} margin={{ top: 10, right: 16, bottom: 8, left: 8 }}>
              <CartesianGrid stroke="var(--border-primary)" strokeDasharray="3 3" />
              <XAxis
                dataKey="label"
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
                stroke={COMPLETE_COLOR}
                strokeWidth={2}
                // Today's dot renders amber; complete days render teal.
                // Recharts passes the row payload as `payload` to the dot
                // renderer — we key off `isToday` to color the marker.
                dot={(props: {
                  cx?: number;
                  cy?: number;
                  payload?: { isToday?: boolean };
                  key?: string | number;
                }) => {
                  const isToday = Boolean(props.payload?.isToday);
                  const fill = isToday ? IN_PROGRESS_COLOR : COMPLETE_COLOR;
                  const stroke = isToday ? IN_PROGRESS_COLOR : COMPLETE_COLOR;
                  return (
                    <circle
                      key={props.key ?? `${props.cx}-${props.cy}`}
                      cx={props.cx}
                      cy={props.cy}
                      r={isToday ? 4 : 3}
                      fill={fill}
                      stroke={stroke}
                      strokeWidth={isToday ? 2 : 1}
                    />
                  );
                }}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
        <p className="chart-caption">
          Today's value updates every ~10s as new watch data arrives.
          Metrics aggregated in UTC; labels show the local date with the
          most overlap.
        </p>
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
            {dailyTrendDecorated.length === 0 ? (
              <tr>
                <td colSpan={2}>No data</td>
              </tr>
            ) : (
              dailyTrendDecorated.map(point => (
                <tr
                  key={point.day}
                  data-testid={point.isToday ? 'chart-data-row-today' : undefined}
                  className={point.isToday ? 'in-progress-row' : ''}
                >
                  <td>
                    {point.label}
                    {point.isToday && (
                      <span className="in-progress-tag"> (in progress)</span>
                    )}
                  </td>
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
                  <td>
                    {row.username ?? `User #${row.user_id}`}
                    {/* bd-fm23o (final bead of EPIC bd-2cenq): "via Emby"
                        badge surfaces the Emby attribution chain so
                        operators know the username came from the
                        cross-referenced Emby session list, not the
                        Dispatcharr-side proxy account. Rendered only
                        when ``attribution_source === "emby"``. */}
                    {row.attribution_source === 'emby' && (
                      <span
                        className="badge attribution-source-badge"
                        title="Identity resolved via Emby /Sessions cross-reference"
                      >
                        via Emby
                      </span>
                    )}
                  </td>
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
                  <th scope="col">Stream</th>
                  <th scope="col">Total minutes</th>
                  <th scope="col">Last watched</th>
                </tr>
              </thead>
              <tbody>
                {channelBreakdown.map(ch => (
                  <tr key={ch.channel_id}>
                    <td>{ch.channel_name}</td>
                    <td>
                      {/* bd-kh23e: stream identity column. Provider name
                          lookup uses the channel-breakdown row's stream
                          identity only — the row doesn't carry a
                          provider_id of its own (the breakdown aggregates
                          across providers per channel), so the bracketed
                          prefix is omitted by ``streamLabel`` when no
                          provider name resolves. The cell still shows
                          the stream name (the most-informative half of
                          the label). When the breakdown gains a
                          provider_id field, the helper picks it up
                          without further code change. */}
                      {streamLabel(null, ch.latest_stream_name, ch.latest_stream_id)}
                    </td>
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
