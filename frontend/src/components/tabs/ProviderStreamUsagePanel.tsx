/**
 * Provider Stream Usage Panel (GH-482, bd-n5cwp).
 *
 * Renders in the Stats tab, after ProvidersPanel. Reads
 * ``GET /api/stats/providers/stream-usage`` — CONFIGURATION data (stream ->
 * channel assignments grouped by M3U provider), not viewing telemetry. The
 * reporter's ask: neither Dispatcharr nor ECM shows how many streams are
 * assigned to channels per provider, so operators can't tell which
 * providers are actually being used.
 *
 * Not admin-gated (unlike ProvidersPanel's viewing-telemetry family): this
 * is Dispatcharr-derived catalog/assignment data, the same trust tier as
 * the Active Channels section above — no auth check needed before calling
 * the API.
 *
 * Column semantics (see the backend module comment in
 * routers/stats.py for the full rationale):
 *   - Assigned streams (PRIMARY) — distinct streams assigned to >=1
 *     channel. A stream in 3 channels still counts once.
 *   - Total assignments (secondary) — SUM of channel-memberships,
 *     counting reuse across channels.
 *   - Total streams / Utilization — the provider's full catalog size and
 *     assigned/total as a percentage, for scale/context.
 *
 * Sortable by any column (click a header to sort by it; click again to
 * flip direction) — cheap client-side sort since the row count is one row
 * per M3U provider (typically single digits to a couple dozen).
 *
 * Accessibility: semantic h3 heading (matches sibling panels), a real
 * <table> with scoped column headers, aria-sort on the active sort column,
 * aria-live="polite" empty state.
 */
import { useState, useEffect, useCallback, useMemo } from 'react';
import * as api from '../../services/api';
import type { ProviderStreamUsageRow } from '../../types';
import './ProviderStreamUsagePanel.css';

type SortKey = 'provider_name' | 'assigned_streams' | 'total_assignments' | 'total_streams' | 'utilization_pct';
type SortDirection = 'asc' | 'desc';

const COLUMNS: Array<{ key: SortKey; label: string; align?: 'right' }> = [
  { key: 'provider_name', label: 'Provider' },
  { key: 'assigned_streams', label: 'Assigned streams', align: 'right' },
  { key: 'total_assignments', label: 'Total assignments', align: 'right' },
  { key: 'total_streams', label: 'Total streams', align: 'right' },
  { key: 'utilization_pct', label: 'Utilization', align: 'right' },
];

function formatPct(value: number): string {
  return `${value.toFixed(1)}%`;
}

export function ProviderStreamUsagePanel() {
  const [rows, setRows] = useState<ProviderStreamUsageRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Default matches the backend's own default ordering (most-utilized
  // providers first) so the initial render needs no client re-sort.
  const [sortKey, setSortKey] = useState<SortKey>('assigned_streams');
  const [sortDirection, setSortDirection] = useState<SortDirection>('desc');

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.getProviderStreamUsage();
      setRows(res.data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load provider stream usage');
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const handleSort = useCallback((key: SortKey) => {
    setSortKey(prevKey => {
      if (prevKey === key) {
        setSortDirection(prevDir => (prevDir === 'asc' ? 'desc' : 'asc'));
        return prevKey;
      }
      // New column: numeric columns default to desc (biggest first is the
      // interesting direction), the name column defaults to asc (A-Z).
      setSortDirection(key === 'provider_name' ? 'asc' : 'desc');
      return key;
    });
  }, []);

  const sortedRows = useMemo(() => {
    const sorted = [...rows].sort((a, b) => {
      let cmp: number;
      if (sortKey === 'provider_name') {
        cmp = a.provider_name.localeCompare(b.provider_name);
      } else {
        cmp = a[sortKey] - b[sortKey];
      }
      return sortDirection === 'asc' ? cmp : -cmp;
    });
    return sorted;
  }, [rows, sortKey, sortDirection]);

  return (
    <div className="provider-stream-usage-panel" id="stats-section-provider-stream-usage">
      <div className="panel-header">
        <h3 className="section-title">Provider Stream Usage</h3>
      </div>
      <p className="panel-description">
        How many of each provider's streams are actually assigned to a channel.
      </p>

      {error && (
        <div className="error-state" role="alert">{error}</div>
      )}

      {loading ? (
        <div className="loading-state">Loading…</div>
      ) : !error && rows.length === 0 ? (
        <div className="empty-state" role="status" aria-live="polite">
          No provider stream data available.
        </div>
      ) : rows.length > 0 ? (
        <table className="provider-stream-usage-table">
          <caption className="visually-hidden">
            Stream assignment counts per provider
          </caption>
          <thead>
            <tr>
              {COLUMNS.map(col => {
                const isActive = sortKey === col.key;
                return (
                  <th
                    key={col.key}
                    scope="col"
                    className={col.align === 'right' ? 'col-right' : undefined}
                    aria-sort={isActive ? (sortDirection === 'asc' ? 'ascending' : 'descending') : 'none'}
                  >
                    <button
                      type="button"
                      className="sort-header-btn"
                      onClick={() => handleSort(col.key)}
                      aria-label={`Sort by ${col.label}`}
                    >
                      {col.label}
                      {isActive && (
                        <span className="material-icons sort-icon" aria-hidden="true">
                          {sortDirection === 'asc' ? 'arrow_upward' : 'arrow_downward'}
                        </span>
                      )}
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {sortedRows.map(row => (
              <tr
                key={row.provider_id ?? 'unknown'}
                className={row.provider_id === null ? 'unknown-bucket' : undefined}
              >
                <td>{row.provider_name}</td>
                <td className="col-right">{row.assigned_streams.toLocaleString()}</td>
                <td className="col-right">{row.total_assignments.toLocaleString()}</td>
                <td className="col-right">{row.total_streams.toLocaleString()}</td>
                <td className="col-right">{formatPct(row.utilization_pct)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </div>
  );
}
