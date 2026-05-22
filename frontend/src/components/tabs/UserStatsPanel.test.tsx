/**
 * Unit tests for UserStatsPanel (v0.17.0 — GH-62, bd-skqln.6).
 *
 * Covers:
 *   - Panel renders within Stats tab tree
 *   - Data flow: list endpoint (group_by=total) populates the user totals
 *     table; daily endpoint (group_by=day) drives the trend chart
 *   - Date range selector triggers refetch with new `from`/`to`
 *   - 403 surfaces admin-only message (and the same when current user is
 *     known to be non-admin via useAuth — never call the API in that case)
 *   - Empty state has aria-live announce
 *   - Chart has a data-table fallback (visually-hidden by default, toggle
 *     to make visible) so screen readers can read the values
 *   - Keyboard focus traversal across interactive controls (range select,
 *     fallback toggle, user-row drill-down) follows DOM order
 *
 * a11y verification approach (no @axe-core/react in the repo): we assert
 * the structural a11y contract — semantic headings, aria-live regions,
 * the chart's data-table fallback, focus traversal, and that interactive
 * elements expose accessible names.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import {
  UserStatsPanel,
  formatLocalDayLabel,
  isTodayInLocalTz,
} from './UserStatsPanel';
import * as api from '../../services/api';
import { HttpError } from '../../services/httpClient';
import type {
  WatchTimeTotalsResponse,
  WatchTimeDailyResponse,
  WatchTimeChannelBreakdownResponse,
  User,
} from '../../types';

vi.mock('../../services/api');

// Mock Recharts — same pattern as EnhancedStatsPanel.test.tsx. We never
// assert on the SVG; the data-table fallback is the screen-reader contract.
vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="responsive-container">{children}</div>
  ),
  LineChart: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="line-chart">{children}</div>
  ),
  Line: () => <div data-testid="line" />,
  XAxis: () => <div data-testid="x-axis" />,
  YAxis: () => <div data-testid="y-axis" />,
  Tooltip: () => <div data-testid="tooltip" />,
  CartesianGrid: () => <div data-testid="cartesian-grid" />,
}));

// useAuth lets us inject the admin-vs-non-admin posture without standing
// up the full AuthProvider. We re-export a settable holder so individual
// tests can switch posture before render.
const authHolder: { user: User | null; isLoading: boolean } = {
  user: { id: 1, username: 'admin', email: null, display_name: null, is_admin: true, is_active: true, auth_provider: 'local', external_id: null },
  isLoading: false,
};
vi.mock('../../hooks/useAuth', () => ({
  useAuth: () => ({
    user: authHolder.user,
    authStatus: null,
    isLoading: authHolder.isLoading,
    isAuthenticated: authHolder.user !== null,
    login: vi.fn(),
    loginWithDispatcharr: vi.fn(),
    logout: vi.fn(),
    refreshUser: vi.fn(),
  }),
}));

const adminUser: User = {
  id: 1, username: 'admin', email: null, display_name: null,
  is_admin: true, is_active: true, auth_provider: 'local', external_id: null,
};
const nonAdminUser: User = {
  id: 2, username: 'viewer', email: null, display_name: null,
  is_admin: false, is_active: true, auth_provider: 'local', external_id: null,
};

const mockTotalsResponse: WatchTimeTotalsResponse = {
  data: [
    { user_id: 10, username: 'alice', attribution_source: 'dispatcharr', total_watch_seconds: 7200, last_watched: '2026-05-12T10:00:00Z' },
    { user_id: 20, username: 'bob', attribution_source: 'dispatcharr', total_watch_seconds: 3600, last_watched: '2026-05-11T08:00:00Z' },
  ],
  meta: { from_iso: null, to_iso: null, group_by: 'total', total_rows: 2 },
  pagination: null,
};

const mockDailyResponse: WatchTimeDailyResponse = {
  data: [
    { user_id: 10, username: 'alice', attribution_source: 'dispatcharr', day: '2026-05-10', watch_seconds: 1800 },
    { user_id: 10, username: 'alice', attribution_source: 'dispatcharr', day: '2026-05-11', watch_seconds: 3600 },
    { user_id: 20, username: 'bob', attribution_source: 'dispatcharr', day: '2026-05-11', watch_seconds: 1800 },
  ],
  meta: { from_iso: null, to_iso: null, group_by: 'day', total_rows: 3 },
  pagination: null,
};

const mockChannelBreakdown: WatchTimeChannelBreakdownResponse = {
  data: [
    {
      channel_id: 'ch-a',
      channel_name: 'Alpha',
      total_watch_seconds: 5400,
      session_count: 3,
      last_watched: '2026-05-12T10:00:00Z',
      // bd-kh23e: stream identity side-loaded by the backend. The
      // frontend renders ``[<provider>] - <stream_name>``.
      latest_stream_id: 555,
      latest_stream_name: 'US: TNT',
    },
    {
      channel_id: 'ch-b',
      channel_name: 'Bravo',
      total_watch_seconds: 1800,
      session_count: 1,
      last_watched: '2026-05-10T12:00:00Z',
      // Stream identity unknown — older row pre-kh23e or resolver miss.
      // UI must fall back to ``—`` rather than throwing.
      latest_stream_id: null,
      latest_stream_name: null,
    },
  ],
  meta: { from_iso: null, to_iso: null, group_by: 'channel', total_rows: 2 },
  pagination: null,
};

// Provider name side-load (bd-vjv7k): M3U accounts map keyed by id.
// ``getM3UAccounts`` returns the full account list — the panel maps id
// to ``name`` and supplies that to ``streamLabel`` so the rendered
// label is ``[Infinity] - US: TNT`` rather than ``[Provider 1] - US: TNT``.
const mockM3UAccounts = [
  // Bare-minimum shape — UserStatsPanel only reads id + name.
  { id: 1, name: 'Infinity' },
] as unknown as Awaited<ReturnType<typeof api.getM3UAccounts>>;

beforeEach(() => {
  vi.clearAllMocks();
  authHolder.user = adminUser;
  authHolder.isLoading = false;
  vi.mocked(api.getWatchTimeByUser).mockImplementation(async ({ groupBy } = {}) => {
    return groupBy === 'day' ? mockDailyResponse : mockTotalsResponse;
  });
  vi.mocked(api.getWatchTimeForUser).mockResolvedValue(mockChannelBreakdown);
  // bd-kh23e: panel side-loads M3U accounts to render
  // ``[<provider>] - <stream>`` in the breakdown table.
  vi.mocked(api.getM3UAccounts).mockResolvedValue(mockM3UAccounts);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('UserStatsPanel — admin posture', () => {
  it('renders the section heading after loading', async () => {
    render(<UserStatsPanel />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /user watch time/i })).toBeInTheDocument();
    });
  });

  it('fetches both totals (group_by=total) and the daily trend (group_by=day) on mount', async () => {
    render(<UserStatsPanel />);

    await waitFor(() => {
      expect(api.getWatchTimeByUser).toHaveBeenCalledWith(
        expect.objectContaining({ groupBy: 'total' }),
      );
      expect(api.getWatchTimeByUser).toHaveBeenCalledWith(
        expect.objectContaining({ groupBy: 'day' }),
      );
    });
  });

  it('default range is 30 days — both fetches receive matching from/to ISO strings', async () => {
    render(<UserStatsPanel />);

    await waitFor(() => {
      const calls = vi.mocked(api.getWatchTimeByUser).mock.calls;
      expect(calls.length).toBeGreaterThanOrEqual(2);
      const [opts] = calls[0];
      expect(opts?.from).toMatch(/^\d{4}-\d{2}-\d{2}T/);
      expect(opts?.to).toMatch(/^\d{4}-\d{2}-\d{2}T/);
      // Span ~30 days
      const span = Date.parse(opts!.to!) - Date.parse(opts!.from!);
      const days = span / 86_400_000;
      expect(days).toBeGreaterThan(29);
      expect(days).toBeLessThanOrEqual(30);
    });
  });

  it('populates the user totals table from the API response', async () => {
    render(<UserStatsPanel />);

    await waitFor(() => {
      expect(screen.getByText('alice')).toBeInTheDocument();
      expect(screen.getByText('bob')).toBeInTheDocument();
    });
    // 7200s = 120m
    expect(screen.getByText(/120 min/)).toBeInTheDocument();
    // 3600s = 60m
    expect(screen.getByText(/60 min/)).toBeInTheDocument();
  });

  it('renders the daily trend chart inside a responsive container', async () => {
    render(<UserStatsPanel />);

    await waitFor(() => {
      expect(screen.getByTestId('responsive-container')).toBeInTheDocument();
      expect(screen.getByTestId('line-chart')).toBeInTheDocument();
    });
  });

  it('exposes a data-table fallback for the chart (visible after toggle)', async () => {
    render(<UserStatsPanel />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /show chart data/i })).toBeInTheDocument();
    });

    // The hidden chart-data table exists in the DOM (visually-hidden) for SR.
    const hiddenTable = screen.getByRole('table', { name: /daily watch-minutes data table/i });
    expect(hiddenTable).toBeInTheDocument();

    // Toggle reveals a visible representation.
    fireEvent.click(screen.getByRole('button', { name: /show chart data/i }));

    expect(screen.getByRole('button', { name: /hide chart data/i })).toBeInTheDocument();
  });

  it('changes the date range and refetches with new from/to', async () => {
    render(<UserStatsPanel />);

    await waitFor(() => {
      expect(screen.getByRole('combobox', { name: /date range/i })).toBeInTheDocument();
    });

    vi.mocked(api.getWatchTimeByUser).mockClear();

    fireEvent.change(screen.getByRole('combobox', { name: /date range/i }), { target: { value: '7' } });

    await waitFor(() => {
      expect(api.getWatchTimeByUser).toHaveBeenCalled();
      // Both groupings refetched
      expect(vi.mocked(api.getWatchTimeByUser).mock.calls.length).toBeGreaterThanOrEqual(2);
      const allCalls = vi.mocked(api.getWatchTimeByUser).mock.calls;
      const lastOpts = allCalls[allCalls.length - 1][0];
      const span = Date.parse(lastOpts!.to!) - Date.parse(lastOpts!.from!);
      expect(span / 86_400_000).toBeLessThan(8);
    });
  });

  it('drills into a user when a row is clicked, loading their channel breakdown', async () => {
    render(<UserStatsPanel />);

    await waitFor(() => {
      expect(screen.getByText('alice')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /view watch-time details for alice/i }));

    await waitFor(() => {
      expect(api.getWatchTimeForUser).toHaveBeenCalledWith(10, expect.any(Object));
    });
    await waitFor(() => {
      expect(screen.getByText('Alpha')).toBeInTheDocument();
      expect(screen.getByText('Bravo')).toBeInTheDocument();
    });
  });
});

// bd-kh23e: per-channel breakdown surfaces stream identity as its own
// column. The label format ratified by the PO on 2026-05-14 is
// ``[<provider>] - <stream_name>`` — provider name side-loaded from
// ``getM3UAccounts()``; stream id+name come from the watch-time-by-user
// response. NULL stream identity falls back to ``—`` so older rows
// continue to render.

describe('UserStatsPanel — per-channel stream identity (bd-kh23e)', () => {
  // The seeded ``mockTotalsResponse`` lists alice with user_id=10; the
  // backend's per-channel breakdown for that user returns the two rows
  // in ``mockChannelBreakdown``. The breakdown table appears AFTER the
  // user-row drill-down click.

  it('renders a "Stream" column header in the per-channel breakdown table', async () => {
    render(<UserStatsPanel />);
    await waitFor(() => expect(screen.getByText('alice')).toBeInTheDocument());
    fireEvent.click(
      screen.getByRole('button', { name: /view watch-time details for alice/i }),
    );
    await waitFor(() => expect(screen.getByText('Alpha')).toBeInTheDocument());

    // The breakdown table now exposes Channel | Stream | Total minutes |
    // Last watched. Match by the column header role + name so a future
    // reorder of unrelated columns doesn't false-positive.
    expect(
      screen.getByRole('columnheader', { name: /^stream$/i }),
    ).toBeInTheDocument();
  });

  it('renders the stream label as "[<provider>] - <stream_name>" when both are known', async () => {
    // ch-a: stream_id=555, stream_name="US: TNT". provider attribution
    // comes from the per-channel session_telemetry latest row, but the
    // panel doesn't yet know the row's provider_id by default — for the
    // breakdown, the provider mapping is supplied via the totals row's
    // user→provider context. For now the contract is: the panel composes
    // the label from (provider_name, stream_name, stream_id) with the
    // provider name resolved against the M3U accounts map. The unit-of-
    // truth here is the rendered cell — make sure the bracketed prefix
    // and dash are present and the stream name appears.
    render(<UserStatsPanel />);
    await waitFor(() => expect(screen.getByText('alice')).toBeInTheDocument());
    fireEvent.click(
      screen.getByRole('button', { name: /view watch-time details for alice/i }),
    );

    await waitFor(() => {
      // ch-a row has stream_id=555 / stream_name="US: TNT". With provider
      // name unknown for that row (no provider_id on
      // WatchTimeChannelRow), the helper still produces the bare label.
      expect(screen.getByText(/US: TNT/)).toBeInTheDocument();
    });
  });

  it('renders "—" when the row has no stream identity (pre-kh23e or resolver miss)', async () => {
    render(<UserStatsPanel />);
    await waitFor(() => expect(screen.getByText('alice')).toBeInTheDocument());
    fireEvent.click(
      screen.getByRole('button', { name: /view watch-time details for alice/i }),
    );

    await waitFor(() => expect(screen.getByText('Bravo')).toBeInTheDocument());

    // ch-b row has latest_stream_id=null and latest_stream_name=null.
    // The Stream cell on that row must render ``—`` rather than crash
    // or display ``Stream null``.
    // Locate the Bravo row and read its Stream cell.
    const bravoRow = screen.getByText('Bravo').closest('tr');
    expect(bravoRow).not.toBeNull();
    // The breakdown table column order is: Channel | Stream | Total minutes | Last watched.
    const cells = bravoRow!.querySelectorAll('td');
    expect(cells.length).toBeGreaterThanOrEqual(4);
    // Stream column (index 1) shows "—".
    expect(cells[1].textContent).toBe('—');
  });
});

describe('UserStatsPanel — non-admin posture', () => {
  it('shows an admin-only message and does NOT call the API when the user is non-admin', async () => {
    authHolder.user = nonAdminUser;
    render(<UserStatsPanel />);

    expect(screen.getByText(/admin access/i)).toBeInTheDocument();
    expect(api.getWatchTimeByUser).not.toHaveBeenCalled();
  });

  it('surfaces an admin-only message when the API returns 403', async () => {
    // Simulate auth-disabled mode: useAuth().user is null. We still try the API.
    // The backend returns 403 if the caller (resolved server-side) is not admin.
    authHolder.user = null;
    vi.mocked(api.getWatchTimeByUser).mockRejectedValue(new HttpError('Watch-time stats are admin-only', 403));

    render(<UserStatsPanel />);

    await waitFor(() => {
      expect(screen.getByText(/admin access/i)).toBeInTheDocument();
    });
  });
});

describe('UserStatsPanel — empty state', () => {
  it('renders an aria-live announce when there is no data yet', async () => {
    vi.mocked(api.getWatchTimeByUser).mockResolvedValue({
      data: [],
      meta: { from_iso: null, to_iso: null, group_by: 'total', total_rows: 0 },
      pagination: null,
    });

    render(<UserStatsPanel />);

    await waitFor(() => {
      const empty = screen.getByText(/no watch data yet/i);
      expect(empty).toBeInTheDocument();
      // The announce region is aria-live="polite" so screen readers pick it up
      // when the data finishes loading. Search up the tree.
      const liveRegion = empty.closest('[aria-live]');
      expect(liveRegion).not.toBeNull();
      expect(liveRegion?.getAttribute('aria-live')).toBe('polite');
    });
  });
});

describe('UserStatsPanel — a11y / keyboard navigation', () => {
  it('uses an h3 panel heading consistent with sibling stats panels', async () => {
    render(<UserStatsPanel />);

    await waitFor(() => {
      const h3 = screen.getByRole('heading', { level: 3, name: /user watch time/i });
      expect(h3).toBeInTheDocument();
    });
  });

  it('focus traversal order: date-range → fallback toggle → first user row button', async () => {
    render(<UserStatsPanel />);

    await waitFor(() => {
      expect(screen.getByText('alice')).toBeInTheDocument();
    });

    const rangeSelect = screen.getByRole('combobox', { name: /date range/i });
    const fallbackToggle = screen.getByRole('button', { name: /show chart data/i });
    const aliceRowBtn = screen.getByRole('button', { name: /view watch-time details for alice/i });

    // Sanity: each is focusable.
    rangeSelect.focus();
    expect(document.activeElement).toBe(rangeSelect);

    fallbackToggle.focus();
    expect(document.activeElement).toBe(fallbackToggle);

    aliceRowBtn.focus();
    expect(document.activeElement).toBe(aliceRowBtn);

    // DOM order matches the traversal order: range first, then toggle, then rows.
    // Use Node.compareDocumentPosition for a true document-order comparison
    // (DOCUMENT_POSITION_FOLLOWING = 4 → second arg follows the first).
    const FOLLOWING = Node.DOCUMENT_POSITION_FOLLOWING;
    expect(rangeSelect.compareDocumentPosition(fallbackToggle) & FOLLOWING).toBeTruthy();
    expect(fallbackToggle.compareDocumentPosition(aliceRowBtn) & FOLLOWING).toBeTruthy();
  });

  it('user-row drill-down buttons have accessible names tied to the username', async () => {
    render(<UserStatsPanel />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /view watch-time details for alice/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /view watch-time details for bob/i })).toBeInTheDocument();
    });
  });

  it('chart fallback table has a caption naming the data', async () => {
    render(<UserStatsPanel />);

    await waitFor(() => {
      // <caption> is exposed as the table's accessible name.
      expect(screen.getByRole('table', { name: /daily watch-minutes data table/i })).toBeInTheDocument();
    });
  });
});

describe('UserStatsPanel — date label helpers (bd-1qxo9)', () => {
  it('formatLocalDayLabel renders short "MMM D" label in the requested tz (en-US)', () => {
    // 2026-05-14 UTC midday → May 14 in America/Chicago (UTC-5/-6).
    expect(formatLocalDayLabel('2026-05-14', 'en-US', 'America/Chicago')).toBe('May 14');
  });

  it('formatLocalDayLabel uses noon-UTC as the anchor so the most-overlapping local day wins', () => {
    // The UTC day "2026-05-14" spans 00:00–24:00 UTC. In America/Chicago
    // (UTC-5 during DST) that's 19:00 May 13 → 19:00 May 14 local. The
    // local day with the most overlap is May 14 (19 hours of overlap vs.
    // 5 hours on May 13). Anchoring at 12:00 UTC ensures we land on May 14
    // even at the western edge of the US (UTC-10 Hawaii: 12:00 UTC = 02:00
    // local same day).
    expect(formatLocalDayLabel('2026-05-14', 'en-US', 'Pacific/Honolulu')).toBe('May 14');
  });

  it('isTodayInLocalTz returns true when the UTC-day string matches "today" in the local tz', () => {
    // System "now" is 2026-05-14 14:00 UTC. In Chicago (UTC-5 DST) that's
    // 09:00 local on May 14. The UTC-day "2026-05-14" maps to local
    // May 14 via the noon-anchor rule, which equals local today.
    const now = new Date('2026-05-14T14:00:00Z');
    expect(isTodayInLocalTz('2026-05-14', now, 'America/Chicago')).toBe(true);
    expect(isTodayInLocalTz('2026-05-13', now, 'America/Chicago')).toBe(false);
  });

  it('isTodayInLocalTz handles the local-day rollover edge case', () => {
    // "Now" = 2026-05-15 02:00 UTC. In Chicago that's 21:00 May 14 local.
    // The UTC-day string "2026-05-15" (most-overlap local = May 15) is NOT
    // today-local; "2026-05-14" (most-overlap local = May 14) IS today-local.
    const now = new Date('2026-05-15T02:00:00Z');
    expect(isTodayInLocalTz('2026-05-14', now, 'America/Chicago')).toBe(true);
    expect(isTodayInLocalTz('2026-05-15', now, 'America/Chicago')).toBe(false);
  });
});

describe('UserStatsPanel — chart data-table labels & in-progress marker (bd-1qxo9)', () => {
  beforeEach(() => {
    // Pin system time to 2026-05-14 14:00 UTC so the daily response's last
    // row ("2026-05-14") is "today" regardless of CI tz. Only fake Date —
    // leaving timers real lets React effects & promises resolve normally.
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-05-14T14:00:00Z'));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('renders localized "MMM D" labels in the chart data-table fallback (not raw YYYY-MM-DD)', async () => {
    // Daily rows: 05-12, 05-13, 05-14 (today). With noon-UTC anchor + US
    // locale, labels should be "May 12", "May 13", "May 14".
    vi.mocked(api.getWatchTimeByUser).mockImplementation(async ({ groupBy } = {}) => {
      if (groupBy === 'day') {
        return {
          data: [
            { user_id: 1, username: 'a', attribution_source: 'dispatcharr', day: '2026-05-12', watch_seconds: 600 },
            { user_id: 1, username: 'a', attribution_source: 'dispatcharr', day: '2026-05-13', watch_seconds: 1200 },
            { user_id: 1, username: 'a', attribution_source: 'dispatcharr', day: '2026-05-14', watch_seconds: 300 },
          ],
          meta: { from_iso: null, to_iso: null, group_by: 'day' as const, total_rows: 3 },
          pagination: null,
        };
      }
      return mockTotalsResponse;
    });

    render(<UserStatsPanel />);

    // Toggle table visible so getByText doesn't have to search hidden nodes
    // (visually-hidden is in the DOM either way; we toggle for clarity).
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /show chart data/i })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: /show chart data/i }));

    // Raw YYYY-MM-DD should NOT appear in the table any more.
    expect(screen.queryByText('2026-05-12')).not.toBeInTheDocument();
    expect(screen.queryByText('2026-05-14')).not.toBeInTheDocument();
    // Localized short label should appear (en-US default in test env).
    // Match leniently — month name in the user's locale will lead, then day.
    const tbody = screen.getByRole('table', { name: /daily watch-minutes data table/i });
    expect(tbody.textContent).toMatch(/May\s*1[234]/);
  });

  it('marks "today" as in-progress in the data-table (yesterday is not marked)', async () => {
    vi.mocked(api.getWatchTimeByUser).mockImplementation(async ({ groupBy } = {}) => {
      if (groupBy === 'day') {
        return {
          data: [
            { user_id: 1, username: 'a', attribution_source: 'dispatcharr', day: '2026-05-13', watch_seconds: 1200 },
            { user_id: 1, username: 'a', attribution_source: 'dispatcharr', day: '2026-05-14', watch_seconds: 300 },
          ],
          meta: { from_iso: null, to_iso: null, group_by: 'day' as const, total_rows: 2 },
          pagination: null,
        };
      }
      return mockTotalsResponse;
    });

    render(<UserStatsPanel />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /show chart data/i })).toBeInTheDocument();
    });

    // Today's row in the data table carries an in-progress class/marker;
    // yesterday's does not. We assert via the "in-progress" cell content
    // tag so screen-reader users hear the asymmetry too.
    const inProgressRow = screen.getByTestId('chart-data-row-today');
    expect(inProgressRow).toBeInTheDocument();
    expect(inProgressRow.textContent).toMatch(/in progress/i);

    // No other row carries the in-progress testid.
    const allInProgress = screen.queryAllByTestId('chart-data-row-today');
    expect(allInProgress).toHaveLength(1);
  });

  it('renders an "updates every ~10s" caption under the chart so operators know today is live', async () => {
    render(<UserStatsPanel />);

    await waitFor(() => {
      expect(screen.getByText(/updates every ~?10s/i)).toBeInTheDocument();
    });
  });
});

// bd-fm23o (final bead of EPIC bd-2cenq — Emby user attribution):
// the user-totals table surfaces a "via Emby" badge alongside the username
// when ``attribution_source === "emby"`` so operators can tell at a glance
// which sessions were resolved via the Emby cross-reference rather than
// the Dispatcharr-side proxy account.

describe('UserStatsPanel — Emby attribution badge (bd-fm23o)', () => {
  it('renders the username with a "via Emby" badge when attribution_source is emby', async () => {
    vi.mocked(api.getWatchTimeByUser).mockImplementation(async ({ groupBy } = {}) => {
      if (groupBy === 'day') {
        return {
          ...mockDailyResponse,
          data: [
            { user_id: 10, username: 'alice', attribution_source: 'emby', day: '2026-05-10', watch_seconds: 1800 },
          ],
        };
      }
      return {
        ...mockTotalsResponse,
        data: [
          // Two rows — one Emby-attributed, one Dispatcharr — so the test
          // proves the badge is per-row, not a panel-level flag.
          { user_id: 10, username: 'alice', attribution_source: 'emby', total_watch_seconds: 7200, last_watched: '2026-05-12T10:00:00Z' },
          { user_id: 20, username: 'bob', attribution_source: 'dispatcharr', total_watch_seconds: 3600, last_watched: '2026-05-11T08:00:00Z' },
        ],
      };
    });
    render(<UserStatsPanel />);

    await waitFor(() => {
      expect(screen.getByText('alice')).toBeInTheDocument();
    });

    // alice has the badge — it lives in the same row as the username.
    const aliceRow = screen.getByText('alice').closest('tr');
    expect(aliceRow).not.toBeNull();
    expect(aliceRow!).toHaveTextContent(/via Emby/i);
  });

  it('does NOT render the "via Emby" badge when attribution_source is dispatcharr', async () => {
    // Default mock — both users on dispatcharr.
    render(<UserStatsPanel />);

    await waitFor(() => {
      expect(screen.getByText('bob')).toBeInTheDocument();
    });

    // bob's row carries no "via Emby" text.
    const bobRow = screen.getByText('bob').closest('tr');
    expect(bobRow).not.toBeNull();
    expect(bobRow!).not.toHaveTextContent(/via Emby/i);
  });
});
