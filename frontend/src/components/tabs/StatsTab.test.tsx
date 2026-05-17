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
//
// bd-tknci (2026-05-13): the ProvidersPanel (rendered inside StatsTab)
// now nests <Label> inside <YAxis> for the Y-axis title. Add ``Label``
// to the mock surface and let ``YAxis`` swallow children so the nested
// JSX renders without crashing the panel — we still don't assert on
// any of the chart internals here.
vi.mock('recharts', () => {
  const Stub = ({ children }: { children?: React.ReactNode }) => <div>{children}</div>;
  return {
    LineChart: Stub,
    Line: () => <div />,
    AreaChart: Stub,
    Area: () => <div />,
    BarChart: Stub,
    Bar: Stub,
    XAxis: Stub,
    YAxis: Stub,
    Label: () => <div />,
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
    emby_user_name: string | null;
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

// bd-fm23o (final bead of EPIC bd-2cenq — Emby user attribution): the
// Active Channels card renders ``(watching: <emby_user>)`` next to the
// stream-name badge when the backend's
// ``_enrich_channels_with_emby`` populated the field. The badge is
// purely additive — it appears alongside the existing stream-name
// badge — so the test verifies presence/absence without disturbing the
// pre-existing badge rendering.

describe('StatsTab — Active Channels Emby attribution badge (bd-fm23o)', () => {
  it('renders "(watching: <emby_user>)" next to the stream badge when emby_user_name is present', async () => {
    vi.mocked(api.getChannelStats).mockResolvedValue(
      buildChannelStatsResponse({
        stream_name: 'US: TNT',
        m3u_account_id: 6,
        stream_id: 555,
        emby_user_name: 'alice',
      }),
    );

    render(<StatsTab />);

    await waitFor(() => {
      // The stream identity badge still renders normally.
      expect(screen.getByText('[Infinity] - US: TNT')).toBeInTheDocument();
    });
    // The emby viewer suffix appears as its own badge.
    expect(screen.getByText('(watching: alice)')).toBeInTheDocument();
  });

  it('does NOT render the Emby viewer badge when emby_user_name is null', async () => {
    vi.mocked(api.getChannelStats).mockResolvedValue(
      buildChannelStatsResponse({
        stream_name: 'US: TNT',
        m3u_account_id: 6,
        stream_id: 555,
        emby_user_name: null,
      }),
    );

    const { container } = render(<StatsTab />);

    await waitFor(() => {
      expect(container.querySelector('.channel-card')).toBeInTheDocument();
    });
    // Stream-identity badge still rendered as a regression-lock that the
    // panel produced a card; the emby badge specifically must not appear.
    expect(screen.queryByText(/watching:/)).not.toBeInTheDocument();
    expect(container.querySelector('.channel-emby-viewer')).toBeNull();
  });
});

describe('StatsTab — provider badge sum invariant (bd-lhxfu)', () => {
  // The "live stats badges" sit in the page header (`.summary-stat`) and
  // each represents one M3U provider's `current/max` connection count.
  // Regression lock: the sum of all provider `current` values MUST equal
  // the Active Channels count. When the resolver can't attribute a
  // channel to a known provider, the sum used to silently undercount;
  // bd-lhxfu surfaces those rows in an explicit "Unknown" bucket so the
  // operator sees the gap instead of a phantom missing channel.

  // M3U accounts marked is_active so the badges actually render. The
  // bd-ox5q8 baseline tests only need id+name+profiles for the
  // streamLabel lookup; the badge logic additionally reads is_active +
  // name (to filter out "Custom") + max_streams (for the `current/max`
  // display). Each provider here has a generous max_streams so the
  // tests aren't sensitive to the per-provider cap.
  const accountsForBadges = [
    { id: 6, name: 'Infinity', profiles: [], is_active: true, max_streams: 10 },
    { id: 7, name: 'OtherProvider', profiles: [], is_active: true, max_streams: 4 },
  ] as unknown as Awaited<ReturnType<typeof api.getM3UAccounts>>;

  function badgeChannel(extras: Partial<{
    channel_id: string;
    channel_name: string;
    stream_name: string | null;
    m3u_account_id: number | null;
  }>) {
    return {
      ...baseChannel,
      ...extras,
    };
  }

  function readBadgeCounts(container: HTMLElement) {
    // Each `.summary-stat` block has a `.stat-label` + `.stat-value`.
    // The first two (Active Channels, Connected Clients) are page
    // totals — every other badge is one provider OR the Unknown bucket.
    const blocks = container.querySelectorAll('.summary-stat');
    const out: Record<string, string> = {};
    for (const block of Array.from(blocks)) {
      const label = block.querySelector('.stat-label')?.textContent?.trim() ?? '';
      const value = block.querySelector('.stat-value')?.textContent?.trim() ?? '';
      out[label] = value;
    }
    return out;
  }

  function parseCurrent(value: string): number {
    // "2/10" -> 2, "1" (Unknown bucket) -> 1.
    const slash = value.indexOf('/');
    return Number.parseInt(slash >= 0 ? value.slice(0, slash) : value, 10) || 0;
  }

  beforeEach(() => {
    vi.mocked(api.getM3UAccounts).mockResolvedValue(accountsForBadges);
  });

  it('appends an Unknown bucket and the badge sum equals activeChannels when a channel has no provider attribution', async () => {
    // 3 active channels: 2 attributed to Infinity, 1 unresolved.
    vi.mocked(api.getChannelStats).mockResolvedValue({
      count: 3,
      channels: [
        badgeChannel({ channel_id: 'uuid-1', m3u_account_id: 6 }),
        badgeChannel({ channel_id: 'uuid-2', m3u_account_id: 6 }),
        badgeChannel({ channel_id: 'uuid-3', m3u_account_id: null }),
      ],
    } as unknown as ChannelStatsResponse);

    const { container } = render(<StatsTab />);

    await waitFor(() => {
      expect(container.querySelector('.unknown-bucket')).toBeInTheDocument();
    });

    const badges = readBadgeCounts(container);
    expect(badges['Active Channels']).toBe('3');
    expect(badges['Infinity']).toBe('2/10');
    expect(badges['Unknown']).toBe('1');
    // Invariant: Infinity (2) + OtherProvider (0) + Unknown (1) == 3.
    const providerSum =
      parseCurrent(badges['Infinity'] ?? '0') +
      parseCurrent(badges['OtherProvider'] ?? '0') +
      parseCurrent(badges['Unknown'] ?? '0');
    expect(providerSum).toBe(parseCurrent(badges['Active Channels'] ?? '0'));
  });

  it('does not render the Unknown bucket when every active channel is attributed', async () => {
    vi.mocked(api.getChannelStats).mockResolvedValue({
      count: 2,
      channels: [
        badgeChannel({ channel_id: 'uuid-1', m3u_account_id: 6 }),
        badgeChannel({ channel_id: 'uuid-2', m3u_account_id: 7 }),
      ],
    } as unknown as ChannelStatsResponse);

    const { container } = render(<StatsTab />);

    await waitFor(() => {
      expect(container.querySelector('.summary-stat')).toBeInTheDocument();
    });

    expect(container.querySelector('.unknown-bucket')).toBeNull();
    const badges = readBadgeCounts(container);
    expect(badges['Active Channels']).toBe('2');
    expect(badges['Infinity']).toBe('1/10');
    expect(badges['OtherProvider']).toBe('1/4');
    const providerSum =
      parseCurrent(badges['Infinity'] ?? '0') +
      parseCurrent(badges['OtherProvider'] ?? '0');
    expect(providerSum).toBe(parseCurrent(badges['Active Channels'] ?? '0'));
  });

  it('routes channels attributed to an unknown account id (side-load gap) into the Unknown bucket', async () => {
    // The resolver attributed the active stream to account 999, but
    // that account is not in the side-loaded m3uAccounts list (e.g.,
    // account was just created or m3uAccounts hasn't refreshed). The
    // channel must still show up — in Unknown — so the badge sum holds.
    vi.mocked(api.getChannelStats).mockResolvedValue({
      count: 2,
      channels: [
        badgeChannel({ channel_id: 'uuid-1', m3u_account_id: 6 }),
        badgeChannel({ channel_id: 'uuid-2', m3u_account_id: 999 }),
      ],
    } as unknown as ChannelStatsResponse);

    const { container } = render(<StatsTab />);

    await waitFor(() => {
      expect(container.querySelector('.unknown-bucket')).toBeInTheDocument();
    });

    const badges = readBadgeCounts(container);
    expect(badges['Active Channels']).toBe('2');
    expect(badges['Infinity']).toBe('1/10');
    expect(badges['Unknown']).toBe('1');
  });
});

// bd-5kbyf (fix-forward for v0.17.1-0035): per-client Emby attribution in
// the Connected Clients list. The backend propagates emby_user_name to each
// client dict; StatsTab must render it with a "via Emby" badge and fall back
// gracefully when the field is null or absent.

const baseClientChannel = {
  channel_id: 'uuid-client',
  channel_name: '408 | ESPN',
  channel_number: 408,
  state: 'streaming',
  client_count: 1,
  stream_name: 'US: ESPN FHD',
  m3u_account_id: 6,
  stream_id: 9001,
  emby_user_name: null,
};

describe('StatsTab — per-client Emby attribution badge (bd-5kbyf)', () => {
  it('renders emby_user_name and "via Emby" badge when client.emby_user_name is set', async () => {
    vi.mocked(api.getChannelStats).mockResolvedValue({
      count: 1,
      channels: [
        {
          ...baseClientChannel,
          emby_user_name: 'MotWakorb',
          clients: [
            {
              client_id: 'cid-1',
              ip_address: '10.0.0.42',
              user_agent: 'Emby/1.0',
              connected_at: '2026-05-17T00:00:00Z',
              last_active: '2026-05-17T00:01:00Z',
              user_id: '0',
              username: null,
              emby_user_name: 'MotWakorb',
            },
          ],
        },
      ],
    } as unknown as ChannelStatsResponse);

    render(<StatsTab />);

    await waitFor(() => {
      // The resolved Emby username surfaces in the client row.
      expect(screen.getByText('MotWakorb')).toBeInTheDocument();
    });
    // The "via Emby" attribution badge is present.
    expect(screen.getByTitle('Identity resolved via Emby /Sessions cross-reference')).toBeInTheDocument();
    expect(screen.getByText('via Emby')).toBeInTheDocument();
    // The raw "User #0" fallback must NOT appear.
    expect(screen.queryByText('User #0')).not.toBeInTheDocument();
  });

  it('falls back to username (no via Emby badge) when emby_user_name is null', async () => {
    vi.mocked(api.getChannelStats).mockResolvedValue({
      count: 1,
      channels: [
        {
          ...baseClientChannel,
          clients: [
            {
              client_id: 'cid-2',
              ip_address: '10.0.0.10',
              user_agent: 'VLC/3.0',
              connected_at: '2026-05-17T00:00:00Z',
              last_active: '2026-05-17T00:01:00Z',
              user_id: '5',
              username: 'dispatcharr_user',
              emby_user_name: null,
            },
          ],
        },
      ],
    } as unknown as ChannelStatsResponse);

    render(<StatsTab />);

    await waitFor(() => {
      expect(screen.getByText('dispatcharr_user')).toBeInTheDocument();
    });
    // No badge — this is a Dispatcharr-side username, not Emby.
    expect(screen.queryByText('via Emby')).not.toBeInTheDocument();
  });

  it('falls back to User #<id> when both username and emby_user_name are null', async () => {
    vi.mocked(api.getChannelStats).mockResolvedValue({
      count: 1,
      channels: [
        {
          ...baseClientChannel,
          clients: [
            {
              client_id: 'cid-3',
              ip_address: '192.168.1.5',
              user_agent: 'Plex/2.0',
              connected_at: '2026-05-17T00:00:00Z',
              last_active: '2026-05-17T00:01:00Z',
              user_id: '7',
              username: null,
              emby_user_name: null,
            },
          ],
        },
      ],
    } as unknown as ChannelStatsResponse);

    render(<StatsTab />);

    await waitFor(() => {
      expect(screen.getByText('User #7')).toBeInTheDocument();
    });
    expect(screen.queryByText('via Emby')).not.toBeInTheDocument();
  });
});

// bd-r5f0c.5 (W5): Multi-viewer rendering in Connected Clients.
// The W9 backend now surfaces *_viewers[] lists per source on each client.
// These tests verify the new rendering paths WITHOUT disturbing the
// existing bd-5kbyf back-compat tests above.

const baseMultiViewerChannel = {
  channel_id: 'uuid-mv',
  channel_name: '200 | CNN',
  channel_number: 200,
  state: 'streaming',
  client_count: 1,
  stream_name: 'US: CNN FHD',
  m3u_account_id: 6,
  stream_id: 9002,
  emby_user_name: null,
  plex_user_name: null,
  jellyfin_user_name: null,
};

describe('StatsTab — multi-viewer per-client rendering (bd-r5f0c.5 / W5)', () => {
  // --- Single viewer via emby_viewers[] (W9 path) ---

  it('renders a single Emby viewer from emby_viewers[] list', async () => {
    vi.mocked(api.getChannelStats).mockResolvedValue({
      count: 1,
      channels: [
        {
          ...baseMultiViewerChannel,
          clients: [
            {
              client_id: 'cid-mv1',
              ip_address: '10.0.0.1',
              user_agent: 'Emby/1.0',
              connected_at: '2026-05-17T00:00:00Z',
              last_active: '2026-05-17T00:01:00Z',
              emby_viewers: [{ user_id: 'emby-uid-1', user_name: 'Alice' }],
            },
          ],
        },
      ],
    } as unknown as ChannelStatsResponse);

    render(<StatsTab />);

    await waitFor(() => {
      // Name from the viewers list
      expect(screen.getByText('Alice')).toBeInTheDocument();
    });
    // AttributionBadge renders "via Emby"
    expect(screen.getByText('via Emby')).toBeInTheDocument();
  });

  // --- 2 viewers comma-separated ---

  it('renders two Emby viewers as comma-separated names', async () => {
    vi.mocked(api.getChannelStats).mockResolvedValue({
      count: 1,
      channels: [
        {
          ...baseMultiViewerChannel,
          clients: [
            {
              client_id: 'cid-mv2',
              ip_address: '10.0.0.2',
              user_agent: 'Emby/1.0',
              connected_at: '2026-05-17T00:00:00Z',
              last_active: '2026-05-17T00:01:00Z',
              emby_viewers: [
                { user_id: null, user_name: 'Alice' },
                { user_id: null, user_name: 'Bob' },
              ],
            },
          ],
        },
      ],
    } as unknown as ChannelStatsResponse);

    render(<StatsTab />);

    await waitFor(() => {
      expect(screen.getByText('Alice, Bob')).toBeInTheDocument();
    });
    expect(screen.getByText('via Emby')).toBeInTheDocument();
  });

  // --- 3 viewers comma-separated (up to 3 shown inline per spec) ---

  it('renders three Emby viewers as comma-separated names', async () => {
    vi.mocked(api.getChannelStats).mockResolvedValue({
      count: 1,
      channels: [
        {
          ...baseMultiViewerChannel,
          clients: [
            {
              client_id: 'cid-mv3',
              ip_address: '10.0.0.3',
              user_agent: 'Emby/1.0',
              connected_at: '2026-05-17T00:00:00Z',
              last_active: '2026-05-17T00:01:00Z',
              emby_viewers: [
                { user_id: null, user_name: 'Alice' },
                { user_id: null, user_name: 'Bob' },
                { user_id: null, user_name: 'Carol' },
              ],
            },
          ],
        },
      ],
    } as unknown as ChannelStatsResponse);

    render(<StatsTab />);

    await waitFor(() => {
      expect(screen.getByText('Alice, Bob, Carol')).toBeInTheDocument();
    });
  });

  // --- 4+ viewers → rollup summary ---

  it('renders "(N viewers)" summary for 4+ Emby viewers in a details element', async () => {
    vi.mocked(api.getChannelStats).mockResolvedValue({
      count: 1,
      channels: [
        {
          ...baseMultiViewerChannel,
          clients: [
            {
              client_id: 'cid-mv4',
              ip_address: '10.0.0.4',
              user_agent: 'Emby/1.0',
              connected_at: '2026-05-17T00:00:00Z',
              last_active: '2026-05-17T00:01:00Z',
              emby_viewers: [
                { user_id: null, user_name: 'Alice' },
                { user_id: null, user_name: 'Bob' },
                { user_id: null, user_name: 'Carol' },
                { user_id: null, user_name: 'Dave' },
              ],
            },
          ],
        },
      ],
    } as unknown as ChannelStatsResponse);

    const { container } = render(<StatsTab />);

    await waitFor(() => {
      // The summary element shows "(4 viewers)"
      expect(screen.getByText('4 viewers')).toBeInTheDocument();
    });
    // Names hidden inside a <details> element
    expect(container.querySelector('details.viewer-details')).toBeInTheDocument();
  });

  // --- Plex single viewer ---

  it('renders a single Plex viewer from plex_viewers[] list', async () => {
    vi.mocked(api.getChannelStats).mockResolvedValue({
      count: 1,
      channels: [
        {
          ...baseMultiViewerChannel,
          clients: [
            {
              client_id: 'cid-plex1',
              ip_address: '10.0.0.5',
              user_agent: 'Plex/2.0',
              connected_at: '2026-05-17T00:00:00Z',
              last_active: '2026-05-17T00:01:00Z',
              plex_viewers: [{ user_id: null, user_name: 'PlexUser' }],
            },
          ],
        },
      ],
    } as unknown as ChannelStatsResponse);

    render(<StatsTab />);

    await waitFor(() => {
      expect(screen.getByText('PlexUser')).toBeInTheDocument();
    });
    expect(screen.getByText('via Plex')).toBeInTheDocument();
  });

  // --- Jellyfin single viewer ---

  it('renders a single Jellyfin viewer from jellyfin_viewers[] list', async () => {
    vi.mocked(api.getChannelStats).mockResolvedValue({
      count: 1,
      channels: [
        {
          ...baseMultiViewerChannel,
          clients: [
            {
              client_id: 'cid-jf1',
              ip_address: '10.0.0.6',
              user_agent: 'Jellyfin/10.0',
              connected_at: '2026-05-17T00:00:00Z',
              last_active: '2026-05-17T00:01:00Z',
              jellyfin_viewers: [{ user_id: 'jf-uid-1', user_name: 'JellyUser' }],
            },
          ],
        },
      ],
    } as unknown as ChannelStatsResponse);

    render(<StatsTab />);

    await waitFor(() => {
      expect(screen.getByText('JellyUser')).toBeInTheDocument();
    });
    expect(screen.getByText('via Jellyfin')).toBeInTheDocument();
  });

  // --- Mixed-source rendering ---

  it('renders separate attribution rows for Emby and Plex viewers on the same client', async () => {
    vi.mocked(api.getChannelStats).mockResolvedValue({
      count: 1,
      channels: [
        {
          ...baseMultiViewerChannel,
          clients: [
            {
              client_id: 'cid-mixed',
              ip_address: '10.0.0.7',
              user_agent: 'Proxy/1.0',
              connected_at: '2026-05-17T00:00:00Z',
              last_active: '2026-05-17T00:01:00Z',
              emby_viewers: [{ user_id: null, user_name: 'EmbyUser' }],
              plex_viewers: [{ user_id: null, user_name: 'PlexUser' }],
            },
          ],
        },
      ],
    } as unknown as ChannelStatsResponse);

    render(<StatsTab />);

    await waitFor(() => {
      expect(screen.getByText('EmbyUser')).toBeInTheDocument();
    });
    expect(screen.getByText('PlexUser')).toBeInTheDocument();
    // Both badges present
    expect(screen.getByText('via Emby')).toBeInTheDocument();
    expect(screen.getByText('via Plex')).toBeInTheDocument();
    // Attribution rows wrapper
    const { container } = render(<StatsTab />);
    await waitFor(() => {
      expect(container.querySelector('[data-testid="client-attribution-rows"]')).toBeInTheDocument();
    });
  });
});

// bd-r5f0c.5 (W5) / bd-g03fi: Channel-header viewer rollup.
// When total_viewers > 1 across all sources, the header shows "(N viewers)"
// instead of a single user's name.

describe('StatsTab — channel-header viewer rollup (bd-g03fi)', () => {
  it('shows "(2 viewers)" rollup when emby_viewers has 2 entries', async () => {
    vi.mocked(api.getChannelStats).mockResolvedValue({
      count: 1,
      channels: [
        {
          ...baseMultiViewerChannel,
          emby_viewers: [
            { user_id: null, user_name: 'Alice' },
            { user_id: null, user_name: 'Bob' },
          ],
          clients: [],
        },
      ],
    } as unknown as ChannelStatsResponse);

    const { container } = render(<StatsTab />);

    await waitFor(() => {
      expect(container.querySelector('[data-testid="channel-header-viewer-rollup"]')).toBeInTheDocument();
    });
    expect(screen.getByText('(2 viewers)')).toBeInTheDocument();
  });

  it('shows "(3 viewers)" rollup when viewers span Emby and Plex', async () => {
    vi.mocked(api.getChannelStats).mockResolvedValue({
      count: 1,
      channels: [
        {
          ...baseMultiViewerChannel,
          emby_viewers: [
            { user_id: null, user_name: 'Alice' },
            { user_id: null, user_name: 'Bob' },
          ],
          plex_viewers: [
            { user_id: null, user_name: 'Carol' },
          ],
          clients: [],
        },
      ],
    } as unknown as ChannelStatsResponse);

    render(<StatsTab />);

    await waitFor(() => {
      expect(screen.getByText('(3 viewers)')).toBeInTheDocument();
    });
  });

  it('shows single viewer name when only 1 viewer total (back-compat)', async () => {
    vi.mocked(api.getChannelStats).mockResolvedValue({
      count: 1,
      channels: [
        {
          ...baseMultiViewerChannel,
          emby_viewers: [{ user_id: null, user_name: 'OnlyUser' }],
          clients: [],
        },
      ],
    } as unknown as ChannelStatsResponse);

    const { container } = render(<StatsTab />);

    await waitFor(() => {
      expect(container.querySelector('[data-testid="channel-header-single-viewer"]')).toBeInTheDocument();
    });
    expect(screen.getByText('(watching: OnlyUser)')).toBeInTheDocument();
    // Should NOT show rollup
    expect(screen.queryByTestId('channel-header-viewer-rollup')).not.toBeInTheDocument();
  });
});
