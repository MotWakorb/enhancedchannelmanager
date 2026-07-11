/**
 * StatsTab — per-channel stats drill-down (bead hq3de.g) and per-client
 * disconnect (bead hq3de.h). Reuses the same mock harness as
 * StatsTab.test.tsx (module-level api mock, stubbed recharts/useAuth).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { StatsTab } from './StatsTab';
import * as api from '../../services/api';
import type { ChannelStatsResponse, BandwidthSummary, ChannelWatchStats } from '../../types';

vi.mock('../../services/api');

const mockNotifications = { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() };
vi.mock('../../contexts/NotificationContext', () => ({
  useNotifications: () => mockNotifications,
}));

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

vi.mock('recharts', () => {
  const Stub = ({ children }: { children?: React.ReactNode }) => <div>{children}</div>;
  return {
    LineChart: Stub, Line: () => <div />, AreaChart: Stub, Area: () => <div />,
    BarChart: Stub, Bar: Stub, XAxis: Stub, YAxis: Stub, Label: () => <div />,
    Tooltip: () => <div />, ReferenceLine: () => <div />, CartesianGrid: () => <div />,
    ResponsiveContainer: Stub, Legend: () => <div />, Cell: () => <div />,
    PieChart: Stub, Pie: () => <div />, Treemap: Stub, Sankey: Stub,
    Scatter: () => <div />, ScatterChart: Stub, RadarChart: Stub, Radar: () => <div />,
    PolarGrid: () => <div />, PolarAngleAxis: () => <div />, PolarRadiusAxis: () => <div />,
  };
});

const baseBandwidth = {
  today: 0, this_week: 0, this_month: 0, this_year: 0, all_time: 0,
  today_in: 0, today_out: 0, week_in: 0, week_out: 0, month_in: 0, month_out: 0,
  year_in: 0, year_out: 0, all_time_in: 0, all_time_out: 0,
  today_peak_bitrate_in: 0, today_peak_bitrate_out: 0,
  week_peak_bitrate_in: 0, week_peak_bitrate_out: 0, daily_history: [],
} as unknown as BandwidthSummary;

const baseTopWatched: ChannelWatchStats[] = [];

// A real UUID shape — StatsTab's isUUID heuristic (`includes('-') &&
// length > 20`) gates whether the channelNameMap lookup engages at all, so
// a short fixture like 'uuid-1' would silently skip the lookup path this
// suite exists to test.
const CHANNEL_UUID = '11111111-2222-3333-4444-555555555555';

function buildStats(clients: Array<Record<string, unknown>> = []): ChannelStatsResponse {
  return {
    count: 1,
    channels: [{
      channel_id: CHANNEL_UUID,
      channel_name: '300 | TNT',
      channel_number: 300,
      state: 'streaming',
      client_count: clients.length,
      clients,
    }],
  } as unknown as ChannelStatsResponse;
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getChannels).mockResolvedValue({
    results: [{ id: 42, uuid: CHANNEL_UUID, name: '300 | TNT', channel_number: 300, logo_id: null, tvg_id: null, epg_data_id: null }],
    next: null,
    count: 1,
  } as unknown as Awaited<ReturnType<typeof api.getChannels>>);
  vi.mocked(api.getStreamProfiles).mockResolvedValue([]);
  vi.mocked(api.getAllLogos).mockResolvedValue([]);
  vi.mocked(api.getM3UAccounts).mockResolvedValue([]);
  vi.mocked(api.getSystemEvents).mockResolvedValue({
    events: [], count: 0, total: 0, offset: 0, limit: 50,
  } as unknown as Awaited<ReturnType<typeof api.getSystemEvents>>);
  vi.mocked(api.getBandwidthStats).mockResolvedValue(baseBandwidth);
  vi.mocked(api.getTopWatchedChannels).mockResolvedValue(baseTopWatched);
  vi.mocked(api.getEPGGrid).mockResolvedValue([]);
  vi.mocked(api.getEPGData).mockResolvedValue([]);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('StatsTab — per-channel drill-down (bead hq3de.g)', () => {
  it('opens the detail modal and resolves the integer Channel.id for the stats-detail call', async () => {
    vi.mocked(api.getChannelStats).mockResolvedValue(buildStats());
    vi.mocked(api.getChannelStatsDetail).mockResolvedValue({ status: 'active', bitrate: '5000' });
    vi.mocked(api.getChannelPopularity).mockResolvedValue(null);

    render(<StatsTab />);
    await waitFor(() => screen.getByText('300 | TNT'));

    fireEvent.click(screen.getByLabelText('View details for 300 | TNT'));

    expect(screen.getByText('Channel Details — 300 | TNT')).toBeInTheDocument();
    await waitFor(() => {
      expect(api.getChannelStatsDetail).toHaveBeenCalledWith(42);
    });
    expect(api.getChannelPopularity).toHaveBeenCalledWith(CHANNEL_UUID);
  });

  it('shows a hint instead of calling the detail endpoint when the channel has no resolved id', async () => {
    // No channels side-loaded (getChannels returns empty), so lookupData is
    // undefined for this UUID and channelId resolves to null. The row falls
    // back to a synthesized "Channel <uuid prefix>..." display name.
    vi.mocked(api.getChannels).mockResolvedValue({ results: [], next: null, count: 0 } as unknown as Awaited<ReturnType<typeof api.getChannels>>);
    vi.mocked(api.getChannelStats).mockResolvedValue(buildStats());
    vi.mocked(api.getChannelPopularity).mockResolvedValue(null);

    render(<StatsTab />);
    await waitFor(() => screen.getByText('300 | TNT'));

    fireEvent.click(screen.getByLabelText(/View details/));

    await waitFor(() => {
      expect(screen.getByText(/detail stats unavailable/i)).toBeInTheDocument();
    });
    expect(api.getChannelStatsDetail).not.toHaveBeenCalled();
  });

  it('renders the popularity score when available', async () => {
    vi.mocked(api.getChannelStats).mockResolvedValue(buildStats());
    vi.mocked(api.getChannelStatsDetail).mockResolvedValue({});
    vi.mocked(api.getChannelPopularity).mockResolvedValue({
      id: 1, channel_id: CHANNEL_UUID, channel_name: '300 | TNT', score: 87.5, rank: 3,
      watch_count_7d: 12, watch_time_7d: 3600, unique_viewers_7d: 5, bandwidth_7d: 1000,
      trend: 'up', trend_percent: 4.2, previous_score: 80, previous_rank: 4,
      calculated_at: '2026-01-01T00:00:00Z', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    });

    render(<StatsTab />);
    await waitFor(() => screen.getByText('300 | TNT'));
    fireEvent.click(screen.getByLabelText('View details for 300 | TNT'));

    await waitFor(() => {
      expect(screen.getByText('87.50')).toBeInTheDocument();
    });
  });

  it('closes the modal', async () => {
    vi.mocked(api.getChannelStats).mockResolvedValue(buildStats());
    vi.mocked(api.getChannelStatsDetail).mockResolvedValue({});
    vi.mocked(api.getChannelPopularity).mockResolvedValue(null);

    render(<StatsTab />);
    await waitFor(() => screen.getByText('300 | TNT'));
    fireEvent.click(screen.getByLabelText('View details for 300 | TNT'));
    expect(screen.getByText('Channel Details — 300 | TNT')).toBeInTheDocument();

    const closeButtons = screen.getAllByRole('button', { name: 'Close' });
    fireEvent.click(closeButtons[closeButtons.length - 1]);
    expect(screen.queryByText('Channel Details — 300 | TNT')).not.toBeInTheDocument();
  });
});

describe('StatsTab — per-client disconnect (bead hq3de.h)', () => {
  it('shows a disconnect button per client and calls stopClient on confirm', async () => {
    vi.mocked(api.getChannelStats).mockResolvedValue(buildStats([
      { client_id: 'c1', ip_address: '10.0.0.5', connection_duration: 120 },
    ]));
    vi.mocked(api.stopClient).mockResolvedValue({ success: true });
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);

    render(<StatsTab />);
    await waitFor(() => screen.getByText('300 | TNT'));

    fireEvent.click(screen.getByLabelText('Disconnect client'));

    expect(confirmSpy).toHaveBeenCalled();
    await waitFor(() => {
      expect(api.stopClient).toHaveBeenCalledWith(CHANNEL_UUID);
    });
  });

  it('does not disconnect when the confirm dialog is declined', async () => {
    vi.mocked(api.getChannelStats).mockResolvedValue(buildStats([
      { client_id: 'c1', ip_address: '10.0.0.5', connection_duration: 120 },
    ]));
    vi.spyOn(window, 'confirm').mockReturnValue(false);

    render(<StatsTab />);
    await waitFor(() => screen.getByText('300 | TNT'));

    fireEvent.click(screen.getByLabelText('Disconnect client'));

    expect(api.stopClient).not.toHaveBeenCalled();
  });
});
