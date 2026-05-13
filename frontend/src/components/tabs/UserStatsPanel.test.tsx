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
import { UserStatsPanel } from './UserStatsPanel';
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
    { user_id: 10, username: 'alice', total_watch_seconds: 7200, last_watched: '2026-05-12T10:00:00Z' },
    { user_id: 20, username: 'bob', total_watch_seconds: 3600, last_watched: '2026-05-11T08:00:00Z' },
  ],
  meta: { from_iso: null, to_iso: null, group_by: 'total', total_rows: 2 },
  pagination: null,
};

const mockDailyResponse: WatchTimeDailyResponse = {
  data: [
    { user_id: 10, username: 'alice', day: '2026-05-10', watch_seconds: 1800 },
    { user_id: 10, username: 'alice', day: '2026-05-11', watch_seconds: 3600 },
    { user_id: 20, username: 'bob', day: '2026-05-11', watch_seconds: 1800 },
  ],
  meta: { from_iso: null, to_iso: null, group_by: 'day', total_rows: 3 },
  pagination: null,
};

const mockChannelBreakdown: WatchTimeChannelBreakdownResponse = {
  data: [
    { channel_id: 'ch-a', channel_name: 'Alpha', total_watch_seconds: 5400, session_count: 3, last_watched: '2026-05-12T10:00:00Z' },
    { channel_id: 'ch-b', channel_name: 'Bravo', total_watch_seconds: 1800, session_count: 1, last_watched: '2026-05-10T12:00:00Z' },
  ],
  meta: { from_iso: null, to_iso: null, group_by: 'channel', total_rows: 2 },
  pagination: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  authHolder.user = adminUser;
  authHolder.isLoading = false;
  vi.mocked(api.getWatchTimeByUser).mockImplementation(async ({ groupBy } = {}) => {
    return groupBy === 'day' ? mockDailyResponse : mockTotalsResponse;
  });
  vi.mocked(api.getWatchTimeForUser).mockResolvedValue(mockChannelBreakdown);
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
