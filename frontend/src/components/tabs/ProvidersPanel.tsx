/**
 * Providers Panel — Stats v2 (v0.17.0 — GH-59, bd-skqln.18).
 *
 * Renders as the 6th panel in the Stats tab, after UserStatsPanel (skqln.6).
 * Surfaces four per-provider visualizations against the read APIs shipped
 * in skqln.16:
 *
 *   1. Buffering events by provider — multi-series line chart
 *      ``GET /api/stats/providers/buffering?window=7d|30d|90d&bucket=hour|day``
 *
 *   2. Time spent per provider — stacked area chart
 *      ``GET /api/stats/providers/watch-time?window=7d|30d|90d``
 *      (Single window value per provider — rendered as a small stacked-bar
 *      style area chart with one bucket; the visual intent of "time spent
 *      stacked across providers" is preserved through stacking and the
 *      data table.)
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
  AreaChart,
  Area,
  XAxis,
  YAxis,
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

function providerLabel(id: number | null): string {
  return id === null ? UNKNOWN_LABEL : `Provider ${id}`;
}

function isAdminOnly403(err: unknown): boolean {
  return err instanceof HttpError && err.status === 403;
}

/** Collect the unique provider set across a series of rows. Preserves the
 * order rows arrive in; NULL ("Unknown") is placed last so it never gets
 * the primary palette slot. */
function collectProviders(
  ...rowSets: ReadonlyArray<ReadonlyArray<{ provider_id: number | null }>>
): ProviderKey[] {
  const seen = new Map<string, ProviderKey>();
  for (const rows of rowSets) {
    for (const r of rows) {
      const key = providerKey(r.provider_id);
      if (!seen.has(key)) {
        seen.set(key, { key, id: r.provider_id, label: providerLabel(r.provider_id) });
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
 * primitive renders a clean grid. */
function buildHeatmapGrid(rows: readonly ProviderHeatmapRow[]): {
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
  const providers = collectProviders(rows);

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
  const providers = useMemo(
    () => collectProviders(buffering, watchTime, heatmap, bitrate),
    [buffering, watchTime, heatmap, bitrate],
  );

  const bufferingChart = useMemo(
    () => pivotByBucket(buffering, 'buffer_event_count'),
    [buffering],
  );
  const bitrateChart = useMemo(
    () => pivotByBucket(bitrate, 'bitrate_bps'),
    [bitrate],
  );

  // Watch-time is per-provider total (not time-bucketed). For the stacked
  // area chart we render a single "Total" bucket; the visual stacking
  // still communicates relative share. The data-table fallback carries
  // the precise per-provider numbers.
  const watchTimeChart = useMemo(() => {
    if (watchTime.length === 0) return [];
    const single: Record<string, string | number> = { bucket: 'Total' };
    for (const r of watchTime) {
      single[providerKey(r.provider_id)] = r.total_watch_seconds;
    }
    return [single];
  }, [watchTime]);

  const heatmapGrid = useMemo(() => buildHeatmapGrid(heatmap), [heatmap]);

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
  const seenInWatchTime = new Set(watchTime.map((r) => providerKey(r.provider_id)));

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

      {/* 1) Buffering events by provider — multi-series line chart */}
      <div className="chart-section">
        <div className="chart-toolbar">
          <h4 className="chart-title">Buffering events by provider</h4>
          {renderToggle('buffering', 'providers-buffering-data-table')}
        </div>
        {buffering.length === 0 && !loading ? (
          <div className="empty-state" role="status" aria-live="polite">
            No buffering data for this window.
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
          <caption>Buffering events by provider — data table</caption>
          <thead>
            <tr>
              <th scope="col">Time bucket</th>
              <th scope="col">Provider</th>
              <th scope="col">Buffer events</th>
            </tr>
          </thead>
          <tbody>
            {buffering.length === 0 ? (
              <tr>
                <td colSpan={3}>No data</td>
              </tr>
            ) : (
              buffering.map((r, i) => {
                const isUnknown = r.provider_id === null;
                return (
                  <tr key={`${r.provider_id ?? 'null'}-${r.time_bucket}-${i}`}>
                    <td>{r.time_bucket}</td>
                    <td>
                      {providerLabel(r.provider_id)}
                      {isUnknown && (
                        <span className="unknown-tooltip" title={UNKNOWN_TOOLTIP} aria-label={UNKNOWN_TOOLTIP}>
                          {' (?)'}
                        </span>
                      )}
                    </td>
                    <td>{r.buffer_event_count}</td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {/* 2) Time spent per provider — stacked area chart */}
      <div className="chart-section">
        <div className="chart-toolbar">
          <h4 className="chart-title">Time spent per provider</h4>
          {renderToggle('watchTime', 'providers-watch-time-data-table')}
        </div>
        {watchTime.length === 0 && !loading ? (
          <div className="empty-state" role="status" aria-live="polite">
            No watch-time data for this window.
          </div>
        ) : (
          <div className="chart-container" aria-hidden="true">
            <ResponsiveContainer width="100%" height={200}>
              <AreaChart data={watchTimeChart} margin={{ top: 10, right: 16, bottom: 8, left: 8 }}>
                <CartesianGrid stroke="var(--border-primary)" strokeDasharray="3 3" />
                <XAxis dataKey="bucket" tick={{ fontSize: 11, fill: 'var(--text-primary)' }} />
                <YAxis tick={{ fontSize: 11, fill: 'var(--text-primary)' }} width={50} />
                <Tooltip />
                <Legend />
                {providers.filter((p) => seenInWatchTime.has(p.key)).map((p, idx) => (
                  <Area
                    key={p.key}
                    type="monotone"
                    dataKey={p.key}
                    name={p.label}
                    stackId="1"
                    stroke={paletteColorAt(idx)}
                    fill={paletteColorAt(idx)}
                    fillOpacity={0.5}
                    isAnimationActive={false}
                  />
                ))}
              </AreaChart>
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
                      {providerLabel(r.provider_id)}
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
              <th scope="col">Bytes</th>
            </tr>
          </thead>
          <tbody>
            {heatmap.length === 0 ? (
              <tr>
                <td colSpan={3}>No data</td>
              </tr>
            ) : (
              heatmap.map((r, i) => {
                const isUnknown = r.provider_id === null;
                return (
                  <tr key={`${r.provider_id ?? 'null'}-${r.channel_id}-${i}`}>
                    <td>
                      {providerLabel(r.provider_id)}
                      {isUnknown && (
                        <span className="unknown-tooltip" title={UNKNOWN_TOOLTIP} aria-label={UNKNOWN_TOOLTIP}>
                          {' (?)'}
                        </span>
                      )}
                    </td>
                    <td>{r.channel_name}</td>
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
        {bitrate.length === 0 && !loading ? (
          <div className="empty-state" role="status" aria-live="polite">
            No bitrate data for this window.
          </div>
        ) : (
          <div className="chart-container" aria-hidden="true">
            <ResponsiveContainer width="100%" height={200}>
              <LineChart data={bitrateChart} margin={{ top: 10, right: 16, bottom: 8, left: 8 }}>
                <CartesianGrid stroke="var(--border-primary)" strokeDasharray="3 3" />
                <XAxis dataKey="time_bucket" tick={{ fontSize: 11, fill: 'var(--text-primary)' }} />
                <YAxis
                  tick={{ fontSize: 11, fill: 'var(--text-primary)' }}
                  width={70}
                  tickFormatter={(v: number) => formatBitrateBps(v)}
                />
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
                      {providerLabel(r.provider_id)}
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
