/**
 * Providers Panel — Stats v2 (v0.17.0 — GH-59, bd-skqln.18).
 *
 * Renders as the 6th panel in the Stats tab, after UserStatsPanel (skqln.6).
 * Surfaces four per-provider visualizations against the read APIs shipped
 * in skqln.16:
 *
 *   1. Channel events by provider — multi-series line chart
 *      ``GET /api/stats/providers/buffering?window=7d|30d|90d&bucket=hour|day``
 *
 *      Pre-bd-ov5vb (1x5v0 paired) this chart was labeled "Buffering
 *      events by provider" and rendered a single ``buffer_event_count``
 *      per (provider, time_bucket). Live verification on the PO's
 *      instance found that filter was returning zero on every poll
 *      because Dispatcharr's ``channel_buffering`` event is rare on
 *      real installs — the operationally-meaningful health signals are
 *      ``channel_reconnect`` / ``channel_error`` / ``stream_switch``,
 *      which the ingest layer now writes to dedicated columns on
 *      ``session_telemetry`` (migration 0012). The chart renders the
 *      pre-summed ``total_event_count`` (UX option A — single primary
 *      number); the per-type breakdown is surfaced in the data-table
 *      fallback (which screen readers already always render) and a
 *      hover tooltip on the chart points. Option A was chosen over
 *      "three columns" (option B) because it preserves the chart's
 *      visual real estate, keeps the legend coherent (one series per
 *      provider, not per provider×event_type), and avoids breaking
 *      the screenshot baseline more than necessary.
 *
 *   2. Time spent per provider — bar chart (bd-tknci, 2026-05-13)
 *      ``GET /api/stats/providers/watch-time?window=7d|30d|90d``
 *      One bar per provider with the provider name on X-axis and watch
 *      minutes on Y-axis. (Earlier shipped as a single-bucket stacked
 *      AreaChart — visually collapsed to one tall "Total" stack with no
 *      Y-axis label, so the PO couldn't tell which provider was which
 *      from the chart itself. The per-bar layout makes provider
 *      attribution legible without consulting the legend.)
 *
 *   3. Channels-by-provider heatmap — 2D grid of rows×cols
 *      ``GET /api/stats/providers/channel-heatmap?window=...&top_n=50``
 *      Renders via the Heatmap primitive from bd-skqln.17.
 *
 *   4. Bitrate by provider over time — multi-series line chart
 *      ``GET /api/stats/providers/bitrate?window=...&bucket=...``
 *
 * Auth posture: admin-only per PO directive 2026-05-13. When useAuth()
 * reports a known non-admin user, render the admin-only notice and never
 * call the API. When useAuth() reports null user (auth-disabled mode),
 * call the API anyway — the backend's admin-only filter is the source of
 * truth; a 403 falls back to the same admin-only notice. Same pattern as
 * UserStatsPanel.
 *
 * NULL provider_id surfaces as a clearly-labeled "Unknown" bucket — both
 * in the chart legend (synthetic series name "Unknown") and the data
 * table fallback. Tooltip on the legend chip explains the pre-cutover
 * attribution gap (operators need to see this).
 *
 * Accessibility (mandatory per bead acceptance):
 *   - Semantic h3 panel heading + h4 chart titles.
 *   - Data-table fallback for each chart, always present in the DOM as a
 *     visually-hidden <table>; a toggle reveals it visually.
 *   - aria-live="polite" empty-state announce for each chart.
 *   - Window and bucket selectors carry explicit aria-labels.
 *   - role="note" on the admin-only notice.
 *   - Heatmap row/col labels include the "Unknown" provider explicitly so
 *     color is never the sole channel of meaning.
 *   - WCAG AA contrast via theme tokens (var(--text-primary)).
 */
import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import {
  LineChart,
  Line,
  BarChart,
  Bar,
  Cell,
  XAxis,
  YAxis,
  Label,
  Tooltip,
  Legend,
  CartesianGrid,
  ResponsiveContainer,
} from 'recharts';
import { useAuth } from '../../hooks/useAuth';
import * as api from '../../services/api';
import { HttpError } from '../../services/httpClient';
import { Heatmap } from '../charts/Heatmap';
import { paletteColorAt } from '../../utils/chartPalette';
import { streamLabel } from '../../utils/formatting';
import type {
  ProviderStatsWindow,
  ProviderStatsBucket,
  ProviderBufferingRow,
  ProviderWatchTimeRow,
  ProviderHeatmapRow,
  ProviderBitrateRow,
} from '../../types';
import './ProvidersPanel.css';

const WINDOW_OPTIONS: Array<{ value: ProviderStatsWindow; label: string }> = [
  { value: '7d', label: 'Last 7 days' },
  { value: '30d', label: 'Last 30 days' },
  { value: '90d', label: 'Last 90 days' },
];

const BUCKET_OPTIONS: Array<{ value: ProviderStatsBucket; label: string }> = [
  { value: 'hour', label: 'Hour' },
  { value: 'day', label: 'Day' },
];

const UNKNOWN_LABEL = 'Unknown';
const UNKNOWN_TOOLTIP =
  'Provider attribution was not recorded for these observations (pre-cutover or unattributable).';

/** Sentinel key used to address the NULL ("Unknown") provider in series maps. */
const UNKNOWN_KEY = '__unknown__';

interface ProviderKey {
  /** Lookup key — string form of provider_id, or UNKNOWN_KEY for NULL. */
  key: string;
  /** ``null`` when this is the "Unknown" bucket. */
  id: number | null;
  /** Display label — provider name fallback used until skqln joins M3UAccount names. */
  label: string;
}

function providerKey(id: number | null): string {
  return id === null ? UNKNOWN_KEY : String(id);
}

/**
 * Resolve a provider_id to a display label.
 *
 * - NULL → "Unknown" (the synthetic NULL bucket from the backend).
 * - Mapped id → the M3U account's display name from ``nameMap``.
 * - Unmapped id (or ``nameMap`` undefined because the side-load
 *   failed) → ``"Provider <id>"`` fallback. The panel must remain
 *   usable when the M3U side-load fails — the provider id is still
 *   recognizable to the operator.
 *
 * bd-vjv7k (2026-05-13) — fixes "Provider 9" leaking into the dev UI by
 * side-loading account names from ``GET /api/providers``.
 */
function providerLabel(
  id: number | null,
  nameMap?: ReadonlyMap<number, string>,
): string {
  if (id === null) return UNKNOWN_LABEL;
  const name = nameMap?.get(id);
  return name ?? `Provider ${id}`;
}

function isAdminOnly403(err: unknown): boolean {
  return err instanceof HttpError && err.status === 403;
}

/** Collect the unique provider set across a series of rows. Preserves the
 * order rows arrive in; NULL ("Unknown") is placed last so it never gets
 * the primary palette slot.
 *
 * ``nameMap`` (bd-vjv7k) supplies M3U account display names. When absent
 * (initial render before /api/providers resolves, or the side-load
 * failed), labels fall back to ``Provider <id>``. */
function collectProviders(
  nameMap: ReadonlyMap<number, string> | undefined,
  ...rowSets: ReadonlyArray<ReadonlyArray<{ provider_id: number | null }>>
): ProviderKey[] {
  const seen = new Map<string, ProviderKey>();
  for (const rows of rowSets) {
    for (const r of rows) {
      const key = providerKey(r.provider_id);
      if (!seen.has(key)) {
        seen.set(key, {
          key,
          id: r.provider_id,
          label: providerLabel(r.provider_id, nameMap),
        });
      }
    }
  }
  // Move the Unknown bucket to the tail.
  const list = Array.from(seen.values());
  list.sort((a, b) => {
    if (a.id === null && b.id !== null) return 1;
    if (b.id === null && a.id !== null) return -1;
    return (a.id ?? 0) - (b.id ?? 0);
  });
  return list;
}

/** Pivot per-(provider, time_bucket) rows into time-series chart data:
 * one record per time bucket with one numeric field per provider key. */
function pivotByBucket<TRow extends { provider_id: number | null; time_bucket: string }>(
  rows: readonly TRow[],
  valueField: keyof TRow & string,
): Array<Record<string, string | number>> {
  const byBucket = new Map<string, Record<string, string | number>>();
  for (const r of rows) {
    const bucket = r.time_bucket;
    const entry = byBucket.get(bucket) ?? { time_bucket: bucket };
    const value = Number(r[valueField] ?? 0);
    entry[providerKey(r.provider_id)] = value;
    byBucket.set(bucket, entry);
  }
  return Array.from(byBucket.values()).sort((a, b) =>
    String(a.time_bucket).localeCompare(String(b.time_bucket)),
  );
}

/** Reduce heatmap cells to a 2D grid: rows = providers, cols = channels.
 * Returns rectangular data with zeros for missing cells so the Heatmap
 * primitive renders a clean grid.
 *
 * ``nameMap`` (bd-vjv7k) is threaded through to ``collectProviders`` so
 * heatmap row labels read as the M3U account name instead of
 * ``Provider <id>``. */
function buildHeatmapGrid(
  rows: readonly ProviderHeatmapRow[],
  nameMap: ReadonlyMap<number, string> | undefined,
): {
  data: number[][];
  rowLabels: string[];
  columnLabels: string[];
  channelIds: string[];
} {
  if (rows.length === 0) {
    return { data: [], rowLabels: [], columnLabels: [], channelIds: [] };
  }
  // Preserve channel discovery order — backend already returns rows sorted by
  // bytes DESC so the first-seen channel is the most-trafficked, which keeps
  // the heatmap's leftmost columns visually prominent.
  const channelOrder: string[] = [];
  const channelNames = new Map<string, string>();
  for (const r of rows) {
    if (!channelNames.has(r.channel_id)) {
      channelOrder.push(r.channel_id);
      channelNames.set(r.channel_id, r.channel_name);
    }
  }
  const providers = collectProviders(nameMap, rows);

  // Build a lookup: (providerKey, channel_id) → bytes.
  const lookup = new Map<string, number>();
  for (const r of rows) {
    lookup.set(`${providerKey(r.provider_id)}::${r.channel_id}`, r.bytes);
  }

  const data: number[][] = providers.map((p) =>
    channelOrder.map((cid) => lookup.get(`${p.key}::${cid}`) ?? 0),
  );
  return {
    data,
    rowLabels: providers.map((p) => p.label),
    columnLabels: channelOrder.map((cid) => channelNames.get(cid) ?? cid),
    channelIds: channelOrder,
  };
}

function formatBitrateBps(bps: number): string {
  if (bps >= 1_000_000) return `${(bps / 1_000_000).toFixed(2)} Mbps`;
  if (bps >= 1_000) return `${(bps / 1_000).toFixed(1)} Kbps`;
  return `${bps} bps`;
}

function formatBytes(b: number): string {
  if (b >= 1_000_000_000) return `${(b / 1_000_000_000).toFixed(2)} GB`;
  if (b >= 1_000_000) return `${(b / 1_000_000).toFixed(1)} MB`;
  if (b >= 1_000) return `${(b / 1_000).toFixed(1)} KB`;
  return `${b} B`;
}

function secondsToMinutes(seconds: number): number {
  return Math.round(seconds / 60);
}

export function ProvidersPanel() {
  const { user, isLoading: authLoading } = useAuth();
  const knownNonAdmin = user !== null && !user.is_admin;

  const [windowSel, setWindowSel] = useState<ProviderStatsWindow>('7d');
  const [bucketSel, setBucketSel] = useState<ProviderStatsBucket>('hour');

  const [buffering, setBuffering] = useState<ProviderBufferingRow[]>([]);
  const [watchTime, setWatchTime] = useState<ProviderWatchTimeRow[]>([]);
  const [heatmap, setHeatmap] = useState<ProviderHeatmapRow[]>([]);
  const [bitrate, setBitrate] = useState<ProviderBitrateRow[]>([]);

  // M3U account name lookup (bd-vjv7k). Side-loaded from /api/providers on
  // panel mount so the four stats datasets — which only carry numeric
  // provider_id — can render the operator-facing account display name.
  // ``undefined`` means "not yet resolved or fetch failed"; rendering
  // falls back to ``Provider <id>`` in that case so the panel never blocks
  // on this lookup succeeding.
  const [m3uNameMap, setM3uNameMap] = useState<ReadonlyMap<number, string> | undefined>(
    undefined,
  );

  const [loading, setLoading] = useState(true);
  const [adminOnly, setAdminOnly] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Per-chart "show data table" toggles. Each chart has an independent
  // toggle — the data tables are always in the DOM (visually-hidden) for
  // screen readers; the toggle only flips visual presentation.
  const [showTables, setShowTables] = useState({
    buffering: false,
    watchTime: false,
    heatmap: false,
    bitrate: false,
  });

  // Stale-response guard: if the user flips the window faster than the
  // network, only the most-recent token's result is honored.
  const requestTokenRef = useRef(0);

  const fetchAll = useCallback(async () => {
    if (knownNonAdmin) return;
    const myToken = ++requestTokenRef.current;
    setLoading(true);
    setError(null);

    // M3U account names side-load (bd-vjv7k). Isolated from the four
    // stats fetches: if /api/providers fails, we still render the panel
    // with the legacy "Provider <id>" fallback. ``allSettled`` keeps the
    // failure from rejecting the outer Promise.all.
    const accountsPromise = api.getM3UAccounts().then(
      (accounts) => {
        if (myToken !== requestTokenRef.current) return;
        const map = new Map<number, string>();
        for (const a of accounts) {
          map.set(a.id, a.name);
        }
        setM3uNameMap(map);
      },
      () => {
        // Swallow — labels fall back to "Provider <id>". No user-facing
        // error; the four primary panels still load.
        if (myToken !== requestTokenRef.current) return;
        setM3uNameMap(undefined);
      },
    );

    try {
      const [bufRes, watchRes, heatRes, bitRes] = await Promise.all([
        api.getProvidersBuffering({ window: windowSel, bucket: bucketSel }),
        api.getProvidersWatchTime({ window: windowSel }),
        api.getProvidersChannelHeatmap({ window: windowSel }),
        api.getProvidersBitrate({ window: windowSel, bucket: bucketSel }),
      ]);
      if (myToken !== requestTokenRef.current) return;
      setBuffering(bufRes.data);
      setWatchTime(watchRes.data);
      setHeatmap(heatRes.data);
      setBitrate(bitRes.data);
      setAdminOnly(false);
    } catch (err) {
      if (myToken !== requestTokenRef.current) return;
      if (isAdminOnly403(err)) {
        setAdminOnly(true);
      } else {
        setError(err instanceof Error ? err.message : 'Failed to load provider stats');
      }
      setBuffering([]);
      setWatchTime([]);
      setHeatmap([]);
      setBitrate([]);
    } finally {
      // Wait for the accounts side-load too so the loading state matches
      // what the user actually sees rendered. Already-handled errors
      // above mean this just blocks on the name-map resolution.
      await accountsPromise;
      if (myToken === requestTokenRef.current) {
        setLoading(false);
      }
    }
  }, [windowSel, bucketSel, knownNonAdmin]);

  useEffect(() => {
    if (knownNonAdmin) {
      setLoading(false);
      return;
    }
    fetchAll();
  }, [fetchAll, knownNonAdmin]);

  // Provider universe across all four datasets — drives chart legend +
  // palette assignments. Stable across re-renders for the same data.
  // Re-derives when the M3U name map resolves so legends pick up the
  // operator-facing names without a full refetch.
  const providers = useMemo(
    () => collectProviders(m3uNameMap, buffering, watchTime, heatmap, bitrate),
    [m3uNameMap, buffering, watchTime, heatmap, bitrate],
  );

  // bd-ov5vb / bd-1x5v0: chart values are the pre-summed
  // ``total_event_count`` (per-provider total of buffer + reconnect +
  // error + switch). Per-type breakdown lives in the data-table fallback
  // below for screen-reader users and in the chart tooltip (Recharts
  // built-in renders the underlying numeric value; the table reveals
  // the breakdown). Option A — see panel docstring rationale.
  const bufferingChart = useMemo(
    () => pivotByBucket(buffering, 'total_event_count'),
    [buffering],
  );
  const bitrateChart = useMemo(
    () => pivotByBucket(bitrate, 'bitrate_bps'),
    [bitrate],
  );

  // Watch-time is per-provider total (not time-bucketed). bd-tknci
  // (2026-05-13): switched from a single-bucket stacked AreaChart to a
  // per-provider BarChart so each provider gets its own visible bar with
  // the provider name on the X-axis. Values are converted to whole
  // minutes for human-friendly readability (the backend ships seconds;
  // the Y-axis label and data table both say "minutes"). NULL provider
  // surfaces as a labeled "Unknown" bar — same ordering rules as the
  // legend (Unknown last). Sort by minutes DESC so the dominant
  // provider is leftmost — operators care about ranking, not entry
  // order.
  const watchTimeChart = useMemo(() => {
    if (watchTime.length === 0) return [];
    return watchTime
      .map((r) => ({
        provider: providerLabel(r.provider_id, m3uNameMap),
        provider_id: r.provider_id,
        watch_minutes: secondsToMinutes(r.total_watch_seconds),
      }))
      .sort((a, b) => {
        // NULL ("Unknown") last; otherwise by minutes DESC.
        if (a.provider_id === null && b.provider_id !== null) return 1;
        if (b.provider_id === null && a.provider_id !== null) return -1;
        return b.watch_minutes - a.watch_minutes;
      });
  }, [watchTime, m3uNameMap]);

  const heatmapGrid = useMemo(() => buildHeatmapGrid(heatmap, m3uNameMap), [heatmap, m3uNameMap]);

  // Auth still resolving — stay quiet until we know the posture.
  if (authLoading) {
    return (
      <div className="providers-panel">
        <h3 className="section-title">Providers</h3>
        <div className="loading-state">Loading…</div>
      </div>
    );
  }

  // Known non-admin or backend 403: show the admin-only notice.
  if (knownNonAdmin || adminOnly) {
    return (
      <div className="providers-panel">
        <h3 className="section-title">Providers</h3>
        <div className="admin-only-state" role="note">
          Provider statistics require admin access.
        </div>
      </div>
    );
  }

  // Helper to render a chart-toolbar with the show/hide-data button.
  const renderToggle = (
    key: keyof typeof showTables,
    controlsId: string,
  ) => (
    <button
      type="button"
      className="chart-data-toggle"
      onClick={() => setShowTables((s) => ({ ...s, [key]: !s[key] }))}
      aria-expanded={showTables[key]}
      aria-controls={controlsId}
    >
      {showTables[key] ? 'Hide chart data' : 'Show chart data'}
    </button>
  );

  // Whether a provider was seen in a given row set — used to decide which
  // series to draw on a chart so we don't render lines for providers that
  // produced no rows for that endpoint.
  const seenInBuffering = new Set(buffering.map((r) => providerKey(r.provider_id)));
  const seenInBitrate = new Set(bitrate.map((r) => providerKey(r.provider_id)));
  // bd-tknci: the watch-time chart is now a per-provider bar chart that
  // iterates ``watchTimeChart`` directly (one row per provider). No need
  // to filter the cross-dataset ``providers`` list against a "seen in
  // watch-time" set — the bar chart's data already IS the watch-time set.

  return (
    <div className="providers-panel">
      <div className="panel-header">
        <h3 className="section-title">Providers</h3>
        <div className="panel-controls">
          <label className="range-control">
            <span className="range-label">Window</span>
            <select
              className="range-select"
              aria-label="Window"
              value={windowSel}
              onChange={(e) => setWindowSel(e.target.value as ProviderStatsWindow)}
            >
              {WINDOW_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
          </label>
          <label className="range-control">
            <span className="range-label">Bucket</span>
            <select
              className="range-select"
              aria-label="Bucket"
              value={bucketSel}
              onChange={(e) => setBucketSel(e.target.value as ProviderStatsBucket)}
            >
              {BUCKET_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
          </label>
        </div>
      </div>

      {error && (
        <div className="error-state" role="alert">{error}</div>
      )}

      {loading && (
        <div className="loading-state" role="status" aria-live="polite">
          Loading provider statistics…
        </div>
      )}

      {/* 1) Channel events by provider — multi-series line chart
            (bd-ov5vb + bd-1x5v0). Label changed from "Buffering" to
            "Channel events" because the broadened ingest now covers
            channel_reconnect / channel_error / stream_switch in
            addition to channel_buffering; the chart value is the
            pre-summed ``total_event_count``. Per-type breakdown lives
            in the data-table fallback below. */}
      <div className="chart-section">
        <div className="chart-toolbar">
          <h4 className="chart-title">Channel events by provider</h4>
          {renderToggle('buffering', 'providers-buffering-data-table')}
        </div>
        <p className="chart-description">
          Combined count of channel-health events per provider
          (reconnect, error, stream-switch, buffering). See the data
          table below for the per-type breakdown.
        </p>
        {buffering.length === 0 && !loading ? (
          <div className="empty-state" role="status" aria-live="polite">
            No channel-event data for this window.
          </div>
        ) : (
          <div className="chart-container" aria-hidden="true">
            <ResponsiveContainer width="100%" height={200}>
              <LineChart data={bufferingChart} margin={{ top: 10, right: 16, bottom: 8, left: 8 }}>
                <CartesianGrid stroke="var(--border-primary)" strokeDasharray="3 3" />
                <XAxis dataKey="time_bucket" tick={{ fontSize: 11, fill: 'var(--text-primary)' }} />
                <YAxis tick={{ fontSize: 11, fill: 'var(--text-primary)' }} width={40} />
                <Tooltip />
                <Legend />
                {providers.filter((p) => seenInBuffering.has(p.key)).map((p, idx) => (
                  <Line
                    key={p.key}
                    type="monotone"
                    dataKey={p.key}
                    name={p.label}
                    stroke={paletteColorAt(idx)}
                    strokeWidth={2}
                    dot={false}
                    isAnimationActive={false}
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
        <table
          id="providers-buffering-data-table"
          className={`chart-data-table ${showTables.buffering ? 'visible' : 'visually-hidden'}`}
        >
          <caption>Channel events by provider — data table</caption>
          <thead>
            <tr>
              <th scope="col">Time bucket</th>
              <th scope="col">Provider</th>
              {/* bd-ov5vb / bd-1x5v0: per-type breakdown surfaces the
                  four counters the broadened ingest writes to
                  ``session_telemetry`` (migration 0012). "Total" is the
                  pre-summed value the chart renders so a screen-reader
                  user sees the same number visually-sighted users
                  read off the line. */}
              <th scope="col">Reconnect</th>
              <th scope="col">Error</th>
              <th scope="col">Switch</th>
              <th scope="col">Buffer</th>
              <th scope="col">Total</th>
            </tr>
          </thead>
          <tbody>
            {buffering.length === 0 ? (
              <tr>
                <td colSpan={7}>No data</td>
              </tr>
            ) : (
              buffering.map((r, i) => {
                const isUnknown = r.provider_id === null;
                return (
                  <tr key={`${r.provider_id ?? 'null'}-${r.time_bucket}-${i}`}>
                    <td>{r.time_bucket}</td>
                    <td>
                      {providerLabel(r.provider_id, m3uNameMap)}
                      {isUnknown && (
                        <span className="unknown-tooltip" title={UNKNOWN_TOOLTIP} aria-label={UNKNOWN_TOOLTIP}>
                          {' (?)'}
                        </span>
                      )}
                    </td>
                    <td>{r.reconnect_event_count}</td>
                    <td>{r.error_event_count}</td>
                    <td>{r.switch_event_count}</td>
                    <td>{r.buffer_event_count}</td>
                    <td>{r.total_event_count}</td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {/* 2) Time spent per provider — bar chart (bd-tknci, 2026-05-13) */}
      <div className="chart-section">
        <div className="chart-toolbar">
          <h4 className="chart-title">Time spent per provider</h4>
          {renderToggle('watchTime', 'providers-watch-time-data-table')}
        </div>
        <p className="chart-description">
          Total minutes streamed from each provider across the selected
          time window. One bar per provider; the Y-axis is minutes.
        </p>
        {watchTime.length === 0 && !loading ? (
          <div className="empty-state" role="status" aria-live="polite">
            No watch-time data for this window.
          </div>
        ) : (
          <div className="chart-container" aria-hidden="true">
            <ResponsiveContainer width="100%" height={220}>
              <BarChart
                data={watchTimeChart}
                margin={{ top: 10, right: 16, bottom: 32, left: 8 }}
              >
                <CartesianGrid stroke="var(--border-primary)" strokeDasharray="3 3" />
                <XAxis
                  dataKey="provider"
                  tick={{ fontSize: 11, fill: 'var(--text-primary)' }}
                  interval={0}
                />
                <YAxis
                  tick={{ fontSize: 11, fill: 'var(--text-primary)' }}
                  width={70}
                  tickFormatter={(v: number) => `${v}`}
                  allowDecimals={false}
                >
                  <Label
                    value="Watch minutes"
                    angle={-90}
                    position="insideLeft"
                    style={{ textAnchor: 'middle', fill: 'var(--text-primary)', fontSize: 11 }}
                  />
                </YAxis>
                <Tooltip
                  formatter={(v: number) => [`${v} min`, 'Watch minutes']}
                  labelFormatter={(label: string) => `Provider: ${label}`}
                />
                <Bar
                  dataKey="watch_minutes"
                  name="Watch minutes"
                  isAnimationActive={false}
                >
                  {/* bd-tknci: per-bar fill from the categorical palette
                      so each provider keeps its own color, matching the
                      legend/series colors in the other charts. NULL
                      provider lands at the tail and gets the next
                      palette slot — the data ordering above places it
                      last regardless. */}
                  {watchTimeChart.map((entry, idx) => (
                    <Cell
                      key={`watch-time-bar-${entry.provider_id ?? 'null'}-${idx}`}
                      fill={paletteColorAt(idx)}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
        <table
          id="providers-watch-time-data-table"
          className={`chart-data-table ${showTables.watchTime ? 'visible' : 'visually-hidden'}`}
        >
          <caption>Time spent per provider — data table</caption>
          <thead>
            <tr>
              <th scope="col">Provider</th>
              <th scope="col">Total watch time</th>
            </tr>
          </thead>
          <tbody>
            {watchTime.length === 0 ? (
              <tr>
                <td colSpan={2}>No data</td>
              </tr>
            ) : (
              watchTime.map((r) => {
                const isUnknown = r.provider_id === null;
                return (
                  <tr key={r.provider_id ?? 'null'}>
                    <td>
                      {providerLabel(r.provider_id, m3uNameMap)}
                      {isUnknown && (
                        <span className="unknown-tooltip" title={UNKNOWN_TOOLTIP} aria-label={UNKNOWN_TOOLTIP}>
                          {' (?)'}
                        </span>
                      )}
                    </td>
                    <td>{secondsToMinutes(r.total_watch_seconds)} min</td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {/* 3) Channels-by-provider heatmap */}
      <div className="chart-section">
        <div className="chart-toolbar">
          <h4 className="chart-title">Channels by provider (top-N)</h4>
          {renderToggle('heatmap', 'providers-heatmap-data-table')}
        </div>
        {heatmap.length === 0 && !loading ? (
          <div className="empty-state" role="status" aria-live="polite">
            No channel/provider data for this window.
          </div>
        ) : (
          <div className="chart-container" aria-hidden="true">
            <Heatmap
              data={heatmapGrid.data}
              rowLabels={heatmapGrid.rowLabels}
              columnLabels={heatmapGrid.columnLabels}
              ariaLabel="Provider × channel byte heatmap"
            />
          </div>
        )}
        <table
          id="providers-heatmap-data-table"
          className={`chart-data-table ${showTables.heatmap ? 'visible' : 'visually-hidden'}`}
        >
          <caption>Channels by provider heatmap — data table</caption>
          <thead>
            <tr>
              <th scope="col">Provider</th>
              <th scope="col">Channel</th>
              {/* bd-kh23e: stream identity per cell — most-recently-
                  observed stream for this (provider, channel) pair in
                  the window. Renders as ``[<provider>] - <stream>``
                  with provider name from the M3U side-load. */}
              <th scope="col">Stream (latest)</th>
              <th scope="col">Bytes</th>
            </tr>
          </thead>
          <tbody>
            {heatmap.length === 0 ? (
              <tr>
                <td colSpan={4}>No data</td>
              </tr>
            ) : (
              heatmap.map((r, i) => {
                const isUnknown = r.provider_id === null;
                // Resolve the provider's display name for the bracketed
                // prefix. NULL provider → no prefix (the helper handles
                // that). Unmapped id → omit prefix too: ``[Provider 1] - X``
                // would leak when the M3U side-load is still in flight.
                const providerName =
                  r.provider_id !== null
                    ? m3uNameMap?.get(r.provider_id) ?? null
                    : null;
                return (
                  <tr key={`${r.provider_id ?? 'null'}-${r.channel_id}-${i}`}>
                    <td>
                      {providerLabel(r.provider_id, m3uNameMap)}
                      {isUnknown && (
                        <span className="unknown-tooltip" title={UNKNOWN_TOOLTIP} aria-label={UNKNOWN_TOOLTIP}>
                          {' (?)'}
                        </span>
                      )}
                    </td>
                    <td>{r.channel_name}</td>
                    <td>
                      {streamLabel(
                        providerName,
                        r.latest_stream_name,
                        r.latest_stream_id,
                      )}
                    </td>
                    <td>{formatBytes(r.bytes)}</td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {/* 4) Bitrate by provider over time — multi-series line chart */}
      <div className="chart-section">
        <div className="chart-toolbar">
          <h4 className="chart-title">Bitrate by provider</h4>
          {renderToggle('bitrate', 'providers-bitrate-data-table')}
        </div>
        {/* bd-zrk05 (2026-05-13): description + Y-axis label so the
            chart is self-documenting. Backend computes
            ``SUM(bytes_delta) * 8 * 1000 / SUM(poll_interval_ms)`` per
            (provider, time_bucket) — i.e. bits-per-second observed over
            the bucket interval. The Y-axis label says "Bitrate
            (auto-scaled)" because the tick formatter renders Mbps /
            Kbps / bps depending on magnitude. */}
        <p className="chart-description">
          Average observed bitrate per provider across the selected time
          window, derived from per-poll byte counts divided by elapsed
          poll-interval time. Y-axis units auto-scale (bps, Kbps, Mbps).
        </p>
        {bitrate.length === 0 && !loading ? (
          <div className="empty-state" role="status" aria-live="polite">
            No bitrate data for this window.
          </div>
        ) : (
          <div className="chart-container" aria-hidden="true">
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={bitrateChart} margin={{ top: 10, right: 16, bottom: 8, left: 8 }}>
                <CartesianGrid stroke="var(--border-primary)" strokeDasharray="3 3" />
                <XAxis dataKey="time_bucket" tick={{ fontSize: 11, fill: 'var(--text-primary)' }} />
                <YAxis
                  tick={{ fontSize: 11, fill: 'var(--text-primary)' }}
                  width={80}
                  tickFormatter={(v: number) => formatBitrateBps(v)}
                >
                  <Label
                    value="Bitrate (auto-scaled)"
                    angle={-90}
                    position="insideLeft"
                    style={{ textAnchor: 'middle', fill: 'var(--text-primary)', fontSize: 11 }}
                  />
                </YAxis>
                <Tooltip formatter={(v: number) => formatBitrateBps(v)} />
                <Legend />
                {providers.filter((p) => seenInBitrate.has(p.key)).map((p, idx) => (
                  <Line
                    key={p.key}
                    type="monotone"
                    dataKey={p.key}
                    name={p.label}
                    stroke={paletteColorAt(idx)}
                    strokeWidth={2}
                    dot={false}
                    isAnimationActive={false}
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
        <table
          id="providers-bitrate-data-table"
          className={`chart-data-table ${showTables.bitrate ? 'visible' : 'visually-hidden'}`}
        >
          <caption>Bitrate by provider — data table</caption>
          <thead>
            <tr>
              <th scope="col">Time bucket</th>
              <th scope="col">Provider</th>
              <th scope="col">Bitrate</th>
            </tr>
          </thead>
          <tbody>
            {bitrate.length === 0 ? (
              <tr>
                <td colSpan={3}>No data</td>
              </tr>
            ) : (
              bitrate.map((r, i) => {
                const isUnknown = r.provider_id === null;
                return (
                  <tr key={`${r.provider_id ?? 'null'}-${r.time_bucket}-${i}`}>
                    <td>{r.time_bucket}</td>
                    <td>
                      {providerLabel(r.provider_id, m3uNameMap)}
                      {isUnknown && (
                        <span className="unknown-tooltip" title={UNKNOWN_TOOLTIP} aria-label={UNKNOWN_TOOLTIP}>
                          {' (?)'}
                        </span>
                      )}
                    </td>
                    <td>{formatBitrateBps(r.bitrate_bps)}</td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
