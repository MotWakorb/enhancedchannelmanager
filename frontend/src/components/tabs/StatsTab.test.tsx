/**
 * StatsTab — Active Channels stream-identity badge (bd-ox5q8).
 *
 * Covers the rendering contract for the live Active Channels card:
 *   * Renders ``[<provider>] - <stream_name>`` when the backend's
 *     ``/api/stats/channels`` enrichment surfaces both fields.
 *   * Falls back to bare ``stream_name`` when ``m3u_account_id`` is
 *     present but does not resolve to a known M3U account (provider
 *     side-load miss).
 *   * Falls back to bare ``stream_name`` when ``m3u_account_id`` is
 *     absent (pre-bd-ox5q8 backend / resolver miss).
 *   * Renders no badge when both ``stream_name`` and
 *     ``m3u_account_id`` are absent (degraded resolver output).
 *
 * The test mocks the API layer at the module level — same pattern as
 * UserStatsPanel.test.tsx. Recharts is mocked because the StatsTab
 * surrounds the channel-card section with chart components; we never
 * assert on the chart SVG.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { StatsTab } from './StatsTab';
import * as api from '../../services/api';
import type {
  ChannelStatsResponse,
  BandwidthSummary,
  ChannelWatchStats,
} from '../../types';

vi.mock('../../services/api');

// NotificationContext — child panels (BandwidthPanel, etc.) call
// useNotifications. Same pattern as BandwidthPanel.test.tsx.
const mockNotifications = {
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
};
vi.mock('../../contexts/NotificationContext', () => ({
  useNotifications: () => mockNotifications,
}));

// useAuth — UserStatsPanel (rendered inside StatsTab) requires it.
// Inject an admin so the panel renders fully; the badge logic under
// test is in StatsTab's own Active Channels section, not the panel.
vi.mock('../../hooks/useAuth', () => ({
  useAuth: () => ({
    user: { id: 1, username: 'admin', email: null, display_name: null, is_admin: true, is_active: true, auth_provider: 'local', external_id: null },
    authStatus: null,
    isLoading: false,
    isAuthenticated: true,
    login: vi.fn(),
    loginWithDispatcharr: vi.fn(),
    logout: vi.fn(),
    refreshUser: vi.fn(),
  }),
}));

// Recharts — never assert on the SVG.
vi.mock('recharts', () => {
  const Stub = ({ children }: { children?: React.ReactNode }) => <div>{children}</div>;
  return {
    LineChart: Stub,
    Line: () => <div />,
    AreaChart: Stub,
    Area: () => <div />,
    BarChart: Stub,
    Bar: () => <div />,
    XAxis: () => <div />,
    YAxis: () => <div />,
    Tooltip: () => <div />,
    ReferenceLine: () => <div />,
    CartesianGrid: () => <div />,
    ResponsiveContainer: Stub,
    Legend: () => <div />,
    Cell: () => <div />,
    PieChart: Stub,
    Pie: () => <div />,
    Treemap: Stub,
    Sankey: Stub,
    Scatter: () => <div />,
    ScatterChart: Stub,
    RadarChart: Stub,
    Radar: () => <div />,
    PolarGrid: () => <div />,
    PolarAngleAxis: () => <div />,
    PolarRadiusAxis: () => <div />,
  };
});

const baseChannel = {
  channel_id: 'uuid-1',
  channel_name: '300 | TNT',
  channel_number: 300,
  state: 'streaming',
  client_count: 1,
  clients: [],
};

const baseBandwidth: BandwidthSummary = {
  today: 0,
  this_week: 0,
  this_month: 0,
  this_year: 0,
  all_time: 0,
  today_in: 0,
  today_out: 0,
  week_in: 0,
  week_out: 0,
  month_in: 0,
  month_out: 0,
  year_in: 0,
  year_out: 0,
  all_time_in: 0,
  all_time_out: 0,
  today_peak_bitrate_in: 0,
  today_peak_bitrate_out: 0,
  week_peak_bitrate_in: 0,
  week_peak_bitrate_out: 0,
  daily_history: [],
} as unknown as BandwidthSummary;

const baseTopWatched: ChannelWatchStats[] = [];

const mockM3UAccounts = [
  // Bare-minimum shape — StatsTab reads id + name + profiles for
  // existing connection-counting; the bd-ox5q8 badge logic also reads
  // id + name. Other fields are unused in this test.
  { id: 6, name: 'Infinity', profiles: [] },
  { id: 7, name: 'OtherProvider', profiles: [] },
] as unknown as Awaited<ReturnType<typeof api.getM3UAccounts>>;

function buildChannelStatsResponse(
  extras: Partial<{
    stream_name: string | null;
    m3u_account_id: number | null;
    stream_id: number;
  }>,
): ChannelStatsResponse {
  return {
    count: 1,
    channels: [{ ...baseChannel, ...extras }],
  } as unknown as ChannelStatsResponse;
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getChannels).mockResolvedValue({
    results: [],
    next: null,
    count: 0,
  } as unknown as Awaited<ReturnType<typeof api.getChannels>>);
  vi.mocked(api.getStreamProfiles).mockResolvedValue([]);
  vi.mocked(api.getM3UAccounts).mockResolvedValue(mockM3UAccounts);
  vi.mocked(api.getSystemEvents).mockResolvedValue({
    events: [],
    count: 0,
    total: 0,
    offset: 0,
    limit: 50,
  } as unknown as Awaited<ReturnType<typeof api.getSystemEvents>>);
  vi.mocked(api.getBandwidthStats).mockResolvedValue(baseBandwidth);
  vi.mocked(api.getTopWatchedChannels).mockResolvedValue(baseTopWatched);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('StatsTab — Active Channels stream-identity badge (bd-ox5q8)', () => {
  it('renders [<provider>] - <stream_name> when both fields resolve', async () => {
    vi.mocked(api.getChannelStats).mockResolvedValue(
      buildChannelStatsResponse({
        stream_name: 'US: TNT',
        m3u_account_id: 6,
        stream_id: 555,
      }),
    );

    render(<StatsTab />);

    await waitFor(() => {
      expect(screen.getByText('[Infinity] - US: TNT')).toBeInTheDocument();
    });
  });

  it('falls back to bare stream name when m3u_account_id is null', async () => {
    vi.mocked(api.getChannelStats).mockResolvedValue(
      buildChannelStatsResponse({
        stream_name: 'Discovery',
        m3u_account_id: null,
        stream_id: 777,
      }),
    );

    render(<StatsTab />);

    await waitFor(() => {
      expect(screen.getByText('Discovery')).toBeInTheDocument();
    });
    // No bracketed provider prefix because m3u_account_id is null.
    expect(screen.queryByText(/\[.+\] - Discovery/)).not.toBeInTheDocument();
  });

  it('falls back to bare stream name when m3u_account_id does not match a known M3U account', async () => {
    vi.mocked(api.getChannelStats).mockResolvedValue(
      buildChannelStatsResponse({
        stream_name: 'ESPN',
        m3u_account_id: 999, // not in mockM3UAccounts
        stream_id: 111,
      }),
    );

    render(<StatsTab />);

    await waitFor(() => {
      expect(screen.getByText('ESPN')).toBeInTheDocument();
    });
    // streamLabel omits the bracketed prefix when provider name is null
    // — no leak of "[Provider 999] - ESPN".
    expect(screen.queryByText(/\[Provider/)).not.toBeInTheDocument();
    expect(screen.queryByText(/\[999\]/)).not.toBeInTheDocument();
  });

  it('renders no stream badge when both stream_name and m3u_account_id are null', async () => {
    vi.mocked(api.getChannelStats).mockResolvedValue(
      buildChannelStatsResponse({
        stream_name: null,
        m3u_account_id: null,
      }),
    );

    const { container } = render(<StatsTab />);

    await waitFor(() => {
      // Wait for the channel card to render.
      expect(container.querySelector('.channel-card')).toBeInTheDocument();
    });
    // No badge element rendered when neither identity field is present.
    expect(container.querySelector('.stream-name-badge')).toBeNull();
  });

  it('renders no stream badge when stream_name equals the channel display name', async () => {
    // Legacy behaviour preserved: when the badge would duplicate the
    // channel label, hide it so the row stays readable.
    vi.mocked(api.getChannelStats).mockResolvedValue(
      buildChannelStatsResponse({
        // Channel display name is "300 | TNT" (from baseChannel).
        stream_name: '300 | TNT',
        m3u_account_id: null,
      }),
    );

    const { container } = render(<StatsTab />);

    await waitFor(() => {
      expect(container.querySelector('.channel-card')).toBeInTheDocument();
    });
    expect(container.querySelector('.stream-name-badge')).toBeNull();
  });
});
