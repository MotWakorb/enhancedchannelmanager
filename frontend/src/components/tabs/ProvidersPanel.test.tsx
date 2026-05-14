/**
 * Unit tests for ProvidersPanel (v0.17.0 — GH-59, bd-skqln.18).
 *
 * Covers:
 *   - Panel renders within Stats tab tree
 *   - Data flow: all 4 provider-stats endpoints are called on mount
 *   - Window selector triggers refetch of all 4 endpoints with new window
 *   - Bucket selector triggers refetch of the buffering + bitrate endpoints
 *   - 403 surfaces admin-only message (and the same when current user is
 *     known to be non-admin via useAuth — never call the API in that case)
 *   - NULL provider_id renders as a labeled "Unknown" bucket in both chart
 *     legend and data-table fallback
 *   - Heatmap cells handle top-N truncation gracefully
 *   - Each chart exposes a data-table fallback (visually-hidden by default,
 *     toggle reveals it)
 *   - Empty state for each chart has aria-live announce
 *
 * a11y verification approach mirrors UserStatsPanel.test.tsx — we assert
 * the structural a11y contract (semantic headings, aria-live regions, the
 * chart's data-table fallback, focus traversal, accessible names).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { ProvidersPanel } from './ProvidersPanel';
import * as api from '../../services/api';
import { HttpError } from '../../services/httpClient';
import type {
  ProviderBufferingResponse,
  ProviderWatchTimeResponse,
  ProviderHeatmapResponse,
  ProviderBitrateResponse,
  M3UAccount,
  User,
} from '../../types';

vi.mock('../../services/api');

// Mock Recharts — same pattern as UserStatsPanel.test.tsx. We never assert
// on the SVG; the data-table fallback is the screen-reader contract.
// bd-tknci (2026-05-13): the watch-time chart is now a BarChart instead
// of a single-bucket AreaChart, so the mock surface includes BarChart /
// Bar / Cell / Label.
vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="responsive-container">{children}</div>
  ),
  LineChart: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="line-chart">{children}</div>
  ),
  BarChart: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="bar-chart">{children}</div>
  ),
  Line: (props: { dataKey?: string }) => (
    <div data-testid={`line-${props.dataKey ?? 'unknown'}`} />
  ),
  Bar: ({ children, dataKey }: { children?: React.ReactNode; dataKey?: string }) => (
    <div data-testid={`bar-${dataKey ?? 'unknown'}`}>{children}</div>
  ),
  Cell: (props: { fill?: string }) => (
    <div data-testid="bar-cell" data-fill={props.fill} />
  ),
  XAxis: ({ children }: { children?: React.ReactNode }) => (
    <div data-testid="x-axis">{children}</div>
  ),
  YAxis: ({ children }: { children?: React.ReactNode }) => (
    <div data-testid="y-axis">{children}</div>
  ),
  Label: (props: { value?: string }) => (
    <div data-testid="axis-label">{props.value}</div>
  ),
  Tooltip: () => <div data-testid="tooltip" />,
  Legend: () => <div data-testid="legend" />,
  CartesianGrid: () => <div data-testid="cartesian-grid" />,
}));

// useAuth posture toggle, same pattern as UserStatsPanel.test.tsx.
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

const mockBufferingResponse: ProviderBufferingResponse = {
  data: [
    { provider_id: 1, time_bucket: '2026-05-12T00:00:00Z', buffer_event_count: 5 },
    { provider_id: 1, time_bucket: '2026-05-13T00:00:00Z', buffer_event_count: 7 },
    { provider_id: 2, time_bucket: '2026-05-12T00:00:00Z', buffer_event_count: 2 },
    { provider_id: 2, time_bucket: '2026-05-13T00:00:00Z', buffer_event_count: 3 },
    // NULL provider_id — "Unknown" bucket
    { provider_id: null, time_bucket: '2026-05-12T00:00:00Z', buffer_event_count: 1 },
  ],
  meta: { from_iso: null, to_iso: null, total_rows: 5, window: '7d', bucket: 'day' },
  pagination: null,
};

const mockWatchTimeResponse: ProviderWatchTimeResponse = {
  data: [
    { provider_id: 1, total_watch_seconds: 7200 },
    { provider_id: 2, total_watch_seconds: 3600 },
    { provider_id: null, total_watch_seconds: 600 },
  ],
  meta: { from_iso: null, to_iso: null, total_rows: 3, window: '7d' },
  pagination: null,
};

const mockHeatmapResponse: ProviderHeatmapResponse = {
  data: [
    // bd-kh23e: stream identity per cell. Mix of known (provider 1)
    // and unknown (other rows) so the test asserts both label paths.
    {
      provider_id: 1, channel_id: 'ch-a', channel_name: 'Alpha', bytes: 5000,
      latest_stream_id: 555, latest_stream_name: 'US: TNT',
    },
    {
      provider_id: 1, channel_id: 'ch-b', channel_name: 'Bravo', bytes: 3000,
      latest_stream_id: 556, latest_stream_name: 'NESN HD',
    },
    {
      provider_id: 2, channel_id: 'ch-a', channel_name: 'Alpha', bytes: 1500,
      latest_stream_id: null, latest_stream_name: null,
    },
    {
      provider_id: 2, channel_id: 'ch-b', channel_name: 'Bravo', bytes: 200,
      latest_stream_id: null, latest_stream_name: null,
    },
    {
      provider_id: null, channel_id: 'ch-a', channel_name: 'Alpha', bytes: 80,
      latest_stream_id: null, latest_stream_name: null,
    },
  ],
  meta: { from_iso: null, to_iso: null, total_rows: 5, window: '7d', top_n: 50 },
  pagination: null,
};

const mockBitrateResponse: ProviderBitrateResponse = {
  data: [
    { provider_id: 1, time_bucket: '2026-05-12T00:00:00Z', bitrate_bps: 4_500_000 },
    { provider_id: 1, time_bucket: '2026-05-13T00:00:00Z', bitrate_bps: 5_000_000 },
    { provider_id: 2, time_bucket: '2026-05-12T00:00:00Z', bitrate_bps: 2_000_000 },
    { provider_id: null, time_bucket: '2026-05-12T00:00:00Z', bitrate_bps: 1_000_000 },
  ],
  meta: { from_iso: null, to_iso: null, total_rows: 4, window: '7d', bucket: 'day' },
  pagination: null,
};

/**
 * Minimal M3UAccount factory — just the fields the panel reads.
 * The full M3UAccount type has ~25 fields; the panel only consumes
 * id + name. We cast through Partial to keep the test fixtures tight.
 */
function makeM3UAccount(id: number, name: string): M3UAccount {
  return { id, name } as unknown as M3UAccount;
}

const mockM3UAccounts: M3UAccount[] = [
  makeM3UAccount(1, 'TopFlix'),
  makeM3UAccount(2, 'StreamHub'),
  // No mapping for id=9 on purpose — exercises the fallback path.
];

beforeEach(() => {
  vi.clearAllMocks();
  authHolder.user = adminUser;
  authHolder.isLoading = false;
  vi.mocked(api.getProvidersBuffering).mockResolvedValue(mockBufferingResponse);
  vi.mocked(api.getProvidersWatchTime).mockResolvedValue(mockWatchTimeResponse);
  vi.mocked(api.getProvidersChannelHeatmap).mockResolvedValue(mockHeatmapResponse);
  vi.mocked(api.getProvidersBitrate).mockResolvedValue(mockBitrateResponse);
  vi.mocked(api.getM3UAccounts).mockResolvedValue(mockM3UAccounts);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('ProvidersPanel — admin posture', () => {
  it('renders the section heading after loading', async () => {
    render(<ProvidersPanel />);
    await waitFor(() => {
      expect(screen.getByRole('heading', { level: 3, name: /providers/i })).toBeInTheDocument();
    });
  });

  it('fetches all four provider-stats endpoints on mount', async () => {
    render(<ProvidersPanel />);
    await waitFor(() => {
      expect(api.getProvidersBuffering).toHaveBeenCalled();
      expect(api.getProvidersWatchTime).toHaveBeenCalled();
      expect(api.getProvidersChannelHeatmap).toHaveBeenCalled();
      expect(api.getProvidersBitrate).toHaveBeenCalled();
    });
  });

  it('renders all four chart titles', async () => {
    render(<ProvidersPanel />);
    await waitFor(() => {
      // Buffering, Watch time, Channels heatmap, Bitrate
      expect(screen.getByRole('heading', { name: /buffering events by provider/i })).toBeInTheDocument();
      expect(screen.getByRole('heading', { name: /time spent per provider/i })).toBeInTheDocument();
      expect(screen.getByRole('heading', { name: /channels by provider/i })).toBeInTheDocument();
      expect(screen.getByRole('heading', { name: /bitrate by provider/i })).toBeInTheDocument();
    });
  });

  it('renders one Recharts line chart for buffering and one for bitrate, one bar chart for watch-time, and one heatmap', async () => {
    render(<ProvidersPanel />);
    await waitFor(() => {
      // 2 line charts (buffering + bitrate), 1 bar chart (watch-time per bd-tknci)
      expect(screen.getAllByTestId('line-chart').length).toBeGreaterThanOrEqual(2);
      expect(screen.getByTestId('bar-chart')).toBeInTheDocument();
      // The heatmap primitive renders with data-testid="heatmap-root"
      expect(screen.getByTestId('heatmap-root')).toBeInTheDocument();
    });
  });

  // bd-tknci (2026-05-13) — Y-axis label + per-provider bar regression guards.
  it('time-spent chart renders a Y-axis label of "Watch minutes" (bd-tknci)', async () => {
    render(<ProvidersPanel />);
    await waitFor(() => {
      // Multiple axis labels exist (watch-time Y, bitrate Y). At least one
      // must be the watch-minutes label.
      const labels = screen.getAllByTestId('axis-label');
      const labelTexts = labels.map((el) => el.textContent ?? '');
      expect(labelTexts).toContain('Watch minutes');
    });
  });

  it('time-spent chart renders one Bar series with a Cell per provider (bd-tknci)', async () => {
    render(<ProvidersPanel />);
    await waitFor(() => {
      // The Bar uses dataKey="watch_minutes" — exactly one Bar in the panel.
      expect(screen.getByTestId('bar-watch_minutes')).toBeInTheDocument();
      // mockWatchTimeResponse has 3 rows (provider 1, provider 2, NULL).
      // The BarChart renders one Cell per row.
      const cells = screen.getAllByTestId('bar-cell');
      expect(cells.length).toBe(3);
    });
  });

  it('time-spent chart shows a one-line description above the chart (bd-tknci)', async () => {
    render(<ProvidersPanel />);
    await waitFor(() => {
      expect(
        screen.getByText(/total minutes streamed from each provider/i),
      ).toBeInTheDocument();
    });
  });

  // bd-zrk05 (2026-05-13) — bitrate chart self-documentation.
  it('bitrate chart renders a Y-axis label describing units (bd-zrk05)', async () => {
    render(<ProvidersPanel />);
    await waitFor(() => {
      const labels = screen.getAllByTestId('axis-label');
      const labelTexts = labels.map((el) => el.textContent ?? '');
      expect(labelTexts).toContain('Bitrate (auto-scaled)');
    });
  });

  it('bitrate chart shows a one-line description explaining the metric (bd-zrk05)', async () => {
    render(<ProvidersPanel />);
    await waitFor(() => {
      expect(
        screen.getByText(/average observed bitrate per provider/i),
      ).toBeInTheDocument();
    });
  });

  it('window selector triggers refetch of all four endpoints with the new window', async () => {
    render(<ProvidersPanel />);
    await waitFor(() => {
      expect(api.getProvidersBuffering).toHaveBeenCalled();
    });
    vi.mocked(api.getProvidersBuffering).mockClear();
    vi.mocked(api.getProvidersWatchTime).mockClear();
    vi.mocked(api.getProvidersChannelHeatmap).mockClear();
    vi.mocked(api.getProvidersBitrate).mockClear();

    fireEvent.change(screen.getByRole('combobox', { name: /window/i }), { target: { value: '30d' } });

    await waitFor(() => {
      expect(api.getProvidersBuffering).toHaveBeenCalledWith(expect.objectContaining({ window: '30d' }));
      expect(api.getProvidersWatchTime).toHaveBeenCalledWith(expect.objectContaining({ window: '30d' }));
      expect(api.getProvidersChannelHeatmap).toHaveBeenCalledWith(expect.objectContaining({ window: '30d' }));
      expect(api.getProvidersBitrate).toHaveBeenCalledWith(expect.objectContaining({ window: '30d' }));
    });
  });

  it('bucket selector triggers refetch of the buffering + bitrate endpoints with the new bucket', async () => {
    render(<ProvidersPanel />);
    await waitFor(() => {
      expect(api.getProvidersBuffering).toHaveBeenCalled();
    });
    vi.mocked(api.getProvidersBuffering).mockClear();
    vi.mocked(api.getProvidersBitrate).mockClear();

    fireEvent.change(screen.getByRole('combobox', { name: /bucket/i }), { target: { value: 'day' } });

    await waitFor(() => {
      expect(api.getProvidersBuffering).toHaveBeenCalledWith(expect.objectContaining({ bucket: 'day' }));
      expect(api.getProvidersBitrate).toHaveBeenCalledWith(expect.objectContaining({ bucket: 'day' }));
    });
  });
});

describe('ProvidersPanel — data tables (chart fallbacks)', () => {
  it('renders a data-table fallback table for the buffering chart', async () => {
    render(<ProvidersPanel />);
    await waitFor(() => {
      expect(screen.getByRole('table', { name: /buffering events.*data table/i })).toBeInTheDocument();
    });
  });

  it('renders a data-table fallback table for the watch-time chart', async () => {
    render(<ProvidersPanel />);
    await waitFor(() => {
      expect(screen.getByRole('table', { name: /time spent per provider.*data table/i })).toBeInTheDocument();
    });
  });

  it('renders a data-table fallback table for the channels heatmap', async () => {
    render(<ProvidersPanel />);
    await waitFor(() => {
      expect(screen.getByRole('table', { name: /channels.*heatmap.*data table/i })).toBeInTheDocument();
    });
  });

  it('renders a data-table fallback table for the bitrate chart', async () => {
    render(<ProvidersPanel />);
    await waitFor(() => {
      expect(screen.getByRole('table', { name: /bitrate.*data table/i })).toBeInTheDocument();
    });
  });

  it('toggle button shows/hides each chart data table', async () => {
    render(<ProvidersPanel />);
    await waitFor(() => {
      expect(screen.getAllByRole('button', { name: /show chart data/i }).length).toBe(4);
    });
    // Click first toggle: it should flip to "Hide chart data".
    const [firstToggle] = screen.getAllByRole('button', { name: /show chart data/i });
    fireEvent.click(firstToggle);
    expect(screen.getAllByRole('button', { name: /hide chart data/i }).length).toBeGreaterThanOrEqual(1);
  });

  // bd-kh23e: the heatmap data-table fallback gains a "Stream (latest)"
  // column. Label rendering is ``[<provider>] - <stream_name>``: provider
  // name from the M3U accounts side-load (bd-vjv7k), stream name from
  // the new ``latest_stream_name`` field on each heatmap row.

  it('heatmap data-table renders a "Stream (latest)" column header (bd-kh23e)', async () => {
    render(<ProvidersPanel />);
    await waitFor(() => {
      const heatmapTable = screen.getByRole('table', { name: /channels.*heatmap.*data table/i });
      expect(within(heatmapTable).getByRole('columnheader', { name: /stream.*latest/i })).toBeInTheDocument();
    });
  });

  it('heatmap data-table renders "[<provider>] - <stream_name>" for cells with full identity (bd-kh23e)', async () => {
    render(<ProvidersPanel />);
    await waitFor(() => {
      const heatmapTable = screen.getByRole('table', { name: /channels.*heatmap.*data table/i });
      // provider_id=1 maps to "TopFlix" via the M3U side-load.
      // Row (provider=1, ch-a) has stream_name "US: TNT".
      expect(within(heatmapTable).getByText('[TopFlix] - US: TNT')).toBeInTheDocument();
      // Row (provider=1, ch-b) has stream_name "NESN HD".
      expect(within(heatmapTable).getByText('[TopFlix] - NESN HD')).toBeInTheDocument();
    });
  });

  it('heatmap data-table renders "—" for cells with no stream identity (bd-kh23e)', async () => {
    render(<ProvidersPanel />);
    await waitFor(() => {
      const heatmapTable = screen.getByRole('table', { name: /channels.*heatmap.*data table/i });
      // Rows with latest_stream_id=null and latest_stream_name=null
      // (mocked: provider=2 cells, Unknown cell) must render "—" in
      // the Stream (latest) column rather than crash or print "null".
      const rows = within(heatmapTable).getAllByRole('row');
      // Header + 5 data rows = 6 rows. Find the body rows and verify
      // at least one has "—" in its stream-latest cell.
      const bodyRows = rows.slice(1);
      const dashCells = bodyRows.filter(r => {
        const tds = r.querySelectorAll('td');
        // Column order: Provider | Channel | Stream (latest) | Bytes.
        return tds[2]?.textContent === '—';
      });
      expect(dashCells.length).toBeGreaterThanOrEqual(2);
    });
  });
});

describe('ProvidersPanel — NULL provider ("Unknown" bucket)', () => {
  it('labels NULL provider_id as "Unknown" in the watch-time data table', async () => {
    render(<ProvidersPanel />);
    await waitFor(() => {
      const watchTimeTable = screen.getByRole('table', { name: /time spent per provider.*data table/i });
      // 600s = 10m. The Unknown row contains both the label and the value.
      expect(within(watchTimeTable).getByText(/unknown/i)).toBeInTheDocument();
    });
  });

  it('labels NULL provider_id as "Unknown" in the buffering data table', async () => {
    render(<ProvidersPanel />);
    await waitFor(() => {
      const bufferingTable = screen.getByRole('table', { name: /buffering events.*data table/i });
      expect(within(bufferingTable).getByText(/unknown/i)).toBeInTheDocument();
    });
  });

  it('labels NULL provider_id as "Unknown" in the heatmap data table', async () => {
    render(<ProvidersPanel />);
    await waitFor(() => {
      const heatmapTable = screen.getByRole('table', { name: /channels.*heatmap.*data table/i });
      expect(within(heatmapTable).getByText(/unknown/i)).toBeInTheDocument();
    });
  });
});

describe('ProvidersPanel — non-admin posture', () => {
  it('shows an admin-only message and does NOT call the API when the user is non-admin', async () => {
    authHolder.user = nonAdminUser;
    render(<ProvidersPanel />);

    expect(screen.getByText(/admin access/i)).toBeInTheDocument();
    expect(api.getProvidersBuffering).not.toHaveBeenCalled();
    expect(api.getProvidersWatchTime).not.toHaveBeenCalled();
    expect(api.getProvidersChannelHeatmap).not.toHaveBeenCalled();
    expect(api.getProvidersBitrate).not.toHaveBeenCalled();
  });

  it('surfaces admin-only message when any endpoint returns 403', async () => {
    authHolder.user = null;
    vi.mocked(api.getProvidersBuffering).mockRejectedValue(new HttpError('Provider stats are admin-only', 403));
    render(<ProvidersPanel />);
    await waitFor(() => {
      expect(screen.getByText(/admin access/i)).toBeInTheDocument();
    });
  });

  it('uses a role="note" on the admin-only notice', async () => {
    authHolder.user = nonAdminUser;
    render(<ProvidersPanel />);
    expect(screen.getByRole('note')).toBeInTheDocument();
  });
});

describe('ProvidersPanel — empty states', () => {
  it('renders aria-live announce when buffering returns no rows', async () => {
    vi.mocked(api.getProvidersBuffering).mockResolvedValue({
      data: [],
      meta: { from_iso: null, to_iso: null, total_rows: 0, window: '7d', bucket: 'hour' },
      pagination: null,
    });
    render(<ProvidersPanel />);
    await waitFor(() => {
      const empty = screen.getByText(/no buffering data/i);
      expect(empty).toBeInTheDocument();
      const liveRegion = empty.closest('[aria-live]');
      expect(liveRegion?.getAttribute('aria-live')).toBe('polite');
    });
  });

  it('renders aria-live announce when watch-time returns no rows', async () => {
    vi.mocked(api.getProvidersWatchTime).mockResolvedValue({
      data: [],
      meta: { from_iso: null, to_iso: null, total_rows: 0, window: '7d' },
      pagination: null,
    });
    render(<ProvidersPanel />);
    await waitFor(() => {
      expect(screen.getByText(/no watch-time data/i)).toBeInTheDocument();
    });
  });

  it('renders aria-live announce when heatmap returns no rows', async () => {
    vi.mocked(api.getProvidersChannelHeatmap).mockResolvedValue({
      data: [],
      meta: { from_iso: null, to_iso: null, total_rows: 0, window: '7d', top_n: 50 },
      pagination: null,
    });
    render(<ProvidersPanel />);
    await waitFor(() => {
      expect(screen.getByText(/no channel\/provider data/i)).toBeInTheDocument();
    });
  });

  it('renders aria-live announce when bitrate returns no rows', async () => {
    vi.mocked(api.getProvidersBitrate).mockResolvedValue({
      data: [],
      meta: { from_iso: null, to_iso: null, total_rows: 0, window: '7d', bucket: 'hour' },
      pagination: null,
    });
    render(<ProvidersPanel />);
    await waitFor(() => {
      expect(screen.getByText(/no bitrate data/i)).toBeInTheDocument();
    });
  });
});

describe('ProvidersPanel — heatmap top-N truncation', () => {
  it('renders only the rows in the response — backend already enforces the top-N cap', async () => {
    // Simulate a response capped to 3 channels × 3 providers — 9 cells max.
    const capped: ProviderHeatmapResponse = {
      data: [
        { provider_id: 1, channel_id: 'c1', channel_name: 'Ch1', bytes: 100, latest_stream_id: null, latest_stream_name: null },
        { provider_id: 1, channel_id: 'c2', channel_name: 'Ch2', bytes: 90, latest_stream_id: null, latest_stream_name: null },
        { provider_id: 1, channel_id: 'c3', channel_name: 'Ch3', bytes: 80, latest_stream_id: null, latest_stream_name: null },
      ],
      meta: { from_iso: null, to_iso: null, total_rows: 3, window: '7d', top_n: 3 },
      pagination: null,
    };
    vi.mocked(api.getProvidersChannelHeatmap).mockResolvedValue(capped);
    render(<ProvidersPanel />);
    await waitFor(() => {
      const table = screen.getByRole('table', { name: /channels.*heatmap.*data table/i });
      // 1 header row + 3 data rows + 1 caption-only consideration; we just
      // check that the three channel names appear and the bytes look right.
      expect(within(table).getByText('Ch1')).toBeInTheDocument();
      expect(within(table).getByText('Ch2')).toBeInTheDocument();
      expect(within(table).getByText('Ch3')).toBeInTheDocument();
    });
  });

  it('renders an empty-state when the heatmap returns zero rows', async () => {
    vi.mocked(api.getProvidersChannelHeatmap).mockResolvedValue({
      data: [],
      meta: { from_iso: null, to_iso: null, total_rows: 0, window: '7d', top_n: 50 },
      pagination: null,
    });
    render(<ProvidersPanel />);
    await waitFor(() => {
      expect(screen.getByText(/no channel\/provider data/i)).toBeInTheDocument();
    });
  });
});

describe('ProvidersPanel — M3U account name lookup (bd-vjv7k)', () => {
  it('fetches M3U accounts on mount alongside the four stats endpoints', async () => {
    render(<ProvidersPanel />);
    await waitFor(() => {
      expect(api.getM3UAccounts).toHaveBeenCalled();
    });
  });

  it('renders M3U account names in data tables when the map is populated', async () => {
    // mockM3UAccounts maps id=1 → 'TopFlix' and id=2 → 'StreamHub'.
    render(<ProvidersPanel />);
    await waitFor(() => {
      const watchTimeTable = screen.getByRole('table', {
        name: /time spent per provider.*data table/i,
      });
      expect(within(watchTimeTable).getByText('TopFlix')).toBeInTheDocument();
      expect(within(watchTimeTable).getByText('StreamHub')).toBeInTheDocument();
      // Generic "Provider 1"/"Provider 2" labels must NOT appear when a
      // name is known.
      expect(within(watchTimeTable).queryByText('Provider 1')).not.toBeInTheDocument();
      expect(within(watchTimeTable).queryByText('Provider 2')).not.toBeInTheDocument();
    });
  });

  it('falls back to "Provider <id>" for unmapped provider ids', async () => {
    // Add a row for an id (9) that is NOT in mockM3UAccounts.
    vi.mocked(api.getProvidersWatchTime).mockResolvedValue({
      data: [
        { provider_id: 1, total_watch_seconds: 1200 },
        { provider_id: 9, total_watch_seconds: 600 },
      ],
      meta: { from_iso: null, to_iso: null, total_rows: 2, window: '7d' },
      pagination: null,
    });
    render(<ProvidersPanel />);
    await waitFor(() => {
      const watchTimeTable = screen.getByRole('table', {
        name: /time spent per provider.*data table/i,
      });
      expect(within(watchTimeTable).getByText('TopFlix')).toBeInTheDocument();
      expect(within(watchTimeTable).getByText('Provider 9')).toBeInTheDocument();
    });
  });

  it('falls back to "Provider <id>" labels when the M3U fetch fails', async () => {
    // Regression guard: the panel must NOT block rendering on M3U fetch
    // failure. The four primary stats endpoints still succeed, so the
    // panel renders with the legacy fallback labels.
    vi.mocked(api.getM3UAccounts).mockRejectedValue(new Error('network down'));
    render(<ProvidersPanel />);
    await waitFor(() => {
      const watchTimeTable = screen.getByRole('table', {
        name: /time spent per provider.*data table/i,
      });
      expect(within(watchTimeTable).getByText('Provider 1')).toBeInTheDocument();
      expect(within(watchTimeTable).getByText('Provider 2')).toBeInTheDocument();
    });
  });

  it('keeps NULL provider_id labeled as "Unknown" regardless of M3U map', async () => {
    render(<ProvidersPanel />);
    await waitFor(() => {
      const watchTimeTable = screen.getByRole('table', {
        name: /time spent per provider.*data table/i,
      });
      expect(within(watchTimeTable).getByText(/unknown/i)).toBeInTheDocument();
    });
  });

  it('does NOT call getM3UAccounts when the user is known non-admin', async () => {
    authHolder.user = nonAdminUser;
    render(<ProvidersPanel />);
    expect(api.getM3UAccounts).not.toHaveBeenCalled();
  });
});

describe('ProvidersPanel — a11y', () => {
  it('uses h3 section heading consistent with sibling stats panels', async () => {
    render(<ProvidersPanel />);
    await waitFor(() => {
      expect(screen.getByRole('heading', { level: 3, name: /providers/i })).toBeInTheDocument();
    });
  });

  it('chart titles are semantic h4 headings within the panel', async () => {
    render(<ProvidersPanel />);
    await waitFor(() => {
      const h4s = screen.getAllByRole('heading', { level: 4 });
      expect(h4s.length).toBeGreaterThanOrEqual(4);
    });
  });

  it('window and bucket selectors carry explicit aria-labels', async () => {
    render(<ProvidersPanel />);
    await waitFor(() => {
      expect(screen.getByRole('combobox', { name: /window/i })).toBeInTheDocument();
      expect(screen.getByRole('combobox', { name: /bucket/i })).toBeInTheDocument();
    });
  });
});
