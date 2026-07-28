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
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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
    provider_name: string | null;
    provider_hostname: string | null;
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
  vi.mocked(api.getAllLogos).mockResolvedValue([]);
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
  vi.mocked(api.getEPGGrid).mockResolvedValue([]);
  vi.mocked(api.getEPGData).mockResolvedValue([]);
  // ProviderStreamUsagePanel (bd-n5cwp) — always-mounted sibling of
  // ProvidersPanel; default to an empty response so its fetch doesn't
  // reject with an unmocked-call error in tests that don't care about it.
  vi.mocked(api.getProviderStreamUsage).mockResolvedValue({
    data: [],
    meta: { total_rows: 0 },
    pagination: null,
  });
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

// bd-gy5nd: backend now surfaces ``provider_name`` (M3U source name OR
// bare URL hostname) so the badge has a meaningful label even when the
// stream-id direct lookup misses. The Stats Tab prefers the
// backend-derived ``provider_name`` over the side-load
// ``m3uAccountNameMap`` lookup so the PO sees the actual upstream
// provider instead of "Unknown" on the channel badge.

describe('StatsTab — Active Channels provider badge (bd-gy5nd)', () => {
  it('renders backend-derived provider_name + stream_name when both resolve', async () => {
    vi.mocked(api.getChannelStats).mockResolvedValue(
      buildChannelStatsResponse({
        stream_name: 'TSN 5',
        m3u_account_id: 6,
        provider_name: 'Infinity TV',
        provider_hostname: 'infinity.gives',
        stream_id: 555,
      }),
    );

    render(<StatsTab />);

    await waitFor(() => {
      // Backend provider_name overrides the side-load name lookup
      // (mockM3UAccounts has id 6 → "Infinity"; backend says
      // "Infinity TV"). The backend wins.
      expect(screen.getByText('[Infinity TV] - TSN 5')).toBeInTheDocument();
    });
  });

  it('renders bare-hostname provider when no M3U match exists (the PO scenario)', async () => {
    // The PO's exact case: stream_id resolution missed (no
    // m3u_account_id) but the backend surfaces the URL hostname as
    // the provider so the badge label is meaningful. Pre-bd-gy5nd
    // this rendered no provider prefix → operator couldn't tell the
    // upstream from the badge.
    vi.mocked(api.getChannelStats).mockResolvedValue(
      buildChannelStatsResponse({
        stream_name: 'TSN 5',  // Dispatcharr's stream label.
        m3u_account_id: null,  // No M3U account matched.
        provider_name: 'infinity.gives',  // Bare hostname from URL.
        provider_hostname: 'infinity.gives',
      }),
    );

    render(<StatsTab />);

    await waitFor(() => {
      // Badge shows the bare hostname as the bracketed provider
      // prefix — the PO's exact "provider should not be Unknown"
      // contract.
      expect(screen.getByText('[infinity.gives] - TSN 5')).toBeInTheDocument();
    });
  });

  it('prefers backend provider_name over side-load m3uAccountNameMap lookup', async () => {
    // Backend resolves "Infinity TV"; side-load has id 6 mapped to
    // "Infinity" (older name). Backend wins so the operator sees the
    // current canonical name.
    vi.mocked(api.getChannelStats).mockResolvedValue(
      buildChannelStatsResponse({
        stream_name: 'Discovery',
        m3u_account_id: 6,
        provider_name: 'Infinity TV',
        provider_hostname: 'infinity.gives',
        stream_id: 200,
      }),
    );

    render(<StatsTab />);

    await waitFor(() => {
      // "Infinity TV" is backend-derived; "Infinity" is the side-load
      // name. The backend value wins.
      expect(screen.getByText('[Infinity TV] - Discovery')).toBeInTheDocument();
    });
    // Side-load name should NOT leak into the rendered badge text.
    expect(screen.queryByText('[Infinity] - Discovery')).not.toBeInTheDocument();
  });

  it('falls back to side-load lookup when backend omits provider_name (pre-bd-gy5nd backend)', async () => {
    // Defensive: when the response is from a pre-bd-gy5nd backend
    // (no provider_name field), the existing m3uAccountNameMap
    // lookup still resolves the badge.
    vi.mocked(api.getChannelStats).mockResolvedValue(
      buildChannelStatsResponse({
        stream_name: 'TSN 5',
        m3u_account_id: 6,
        // provider_name and provider_hostname intentionally omitted.
        stream_id: 555,
      }),
    );

    render(<StatsTab />);

    await waitFor(() => {
      // Side-load lookup: id 6 → "Infinity".
      expect(screen.getByText('[Infinity] - TSN 5')).toBeInTheDocument();
    });
  });
});

// bd-fm23o → bd-cat70 (fix-forward for v0.17.1-0056): the original
// bd-fm23o single-viewer "(watching: <emby_user>)" badge in the channel
// header was REMOVED in bd-cat70 because it duplicated the name already
// rendered (with the AttributionBadge) in the Connected Clients
// section below. The channel header now shows the "(N viewers)" rollup
// only when N > 1; single-viewer cases rely on the per-client display.

describe('StatsTab — Active Channels Emby attribution badge (bd-fm23o → bd-cat70)', () => {
  it('does NOT render a "(watching: ...)" suffix in the header when only one Emby viewer is present (bd-cat70: dedup with Connected Clients)', async () => {
    vi.mocked(api.getChannelStats).mockResolvedValue(
      buildChannelStatsResponse({
        stream_name: 'US: TNT',
        m3u_account_id: 6,
        stream_id: 555,
        emby_user_name: 'alice',
      }),
    );

    const { container } = render(<StatsTab />);

    await waitFor(() => {
      // The stream identity badge still renders normally.
      expect(screen.getByText('[Infinity] - US: TNT')).toBeInTheDocument();
    });
    // bd-cat70: the channel-header "(watching: alice)" suffix is gone —
    // single-viewer name is shown only in the Connected Clients section
    // (with the AttributionBadge), not duplicated in the header.
    expect(screen.queryByText(/watching:/)).not.toBeInTheDocument();
    expect(container.querySelector('.channel-emby-viewer')).toBeNull();
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

  // enhancedchannelmanager-2896r.21 folded this strip onto the route header's
  // breadcrumb row, where a one-line budget forced the two page-total labels
  // down to "Active" / "Clients". The full wording moved to their `title`, so
  // PAGE_TOTAL_LABELS is what the DOM says and PAGE_TOTAL_TITLES is what the
  // operator reads on hover — both are asserted so the abbreviation can't
  // quietly become the only description.
  const PAGE_TOTAL_LABELS = ['Active', 'Clients'];
  const PAGE_TOTAL_TITLES = ['Active channels', 'Connected clients'];

  function readBadgeCounts(container: HTMLElement) {
    // Each `.summary-stat` block has a `.stat-label` + `.stat-value`.
    // The first two (Active, Clients) are page totals — every other
    // badge is one provider OR the Unknown bucket.
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

    // The page totals still carry their full meaning on hover even though the
    // visible labels are abbreviated for the header row.
    expect(
      Array.from(container.querySelectorAll('.summary-stat'))
        .slice(0, 2)
        .map((el) => el.getAttribute('title')),
    ).toEqual(PAGE_TOTAL_TITLES);

    const badges = readBadgeCounts(container);
    expect(badges['Active']).toBe('3');
    expect(badges['Infinity']).toBe('2/10');
    expect(badges['Unknown']).toBe('1');
    // Invariant: Infinity (2) + OtherProvider (0) + Unknown (1) == 3.
    const providerSum =
      parseCurrent(badges['Infinity'] ?? '0') +
      parseCurrent(badges['OtherProvider'] ?? '0') +
      parseCurrent(badges['Unknown'] ?? '0');
    expect(providerSum).toBe(parseCurrent(badges['Active'] ?? '0'));
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
    expect(badges['Active']).toBe('2');
    expect(badges['Infinity']).toBe('1/10');
    expect(badges['OtherProvider']).toBe('1/4');
    const providerSum =
      parseCurrent(badges['Infinity'] ?? '0') +
      parseCurrent(badges['OtherProvider'] ?? '0');
    expect(providerSum).toBe(parseCurrent(badges['Active'] ?? '0'));
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
    expect(badges['Active']).toBe('2');
    expect(badges['Infinity']).toBe('1/10');
    expect(badges['Unknown']).toBe('1');
  });

  it('keeps the page-total labels distinct from the provider badges', async () => {
    // Guards the abbreviation itself: "Active"/"Clients" must remain the only
    // non-provider entries, otherwise the provider-tile filters used by the
    // condensed-table tests (and by anyone reading the strip) silently break.
    vi.mocked(api.getChannelStats).mockResolvedValue({
      count: 1,
      channels: [badgeChannel({ channel_id: 'uuid-1', m3u_account_id: 6 })],
    } as unknown as ChannelStatsResponse);

    const { container } = render(<StatsTab />);

    await waitFor(() => {
      expect(container.querySelector('.summary-stat')).toBeInTheDocument();
    });

    const labels = Array.from(container.querySelectorAll('.summary-stat')).map(
      (el) => el.querySelector('.stat-label')?.textContent?.trim(),
    );
    expect(labels.slice(0, 2)).toEqual(PAGE_TOTAL_LABELS);
    expect(labels.slice(2)).toEqual(['Infinity', 'OtherProvider']);
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

// bd-mlcla: Option-B ambiguous-group rollup rendering. The backend
// reconciler stamps the "N viewers: <list>" label into the per-client
// *_user_name string (with the *_viewers list left empty) so the existing
// singular path renders the label verbatim — the operator sees the
// ambiguity instead of a possibly-wrong single name.
describe('StatsTab — Option-B ambiguous-group rollup (bd-mlcla)', () => {
  it('renders the "N viewers: ..." rollup label from emby_user_name', async () => {
    vi.mocked(api.getChannelStats).mockResolvedValue({
      count: 1,
      channels: [
        {
          ...baseClientChannel,
          emby_user_name: '2 viewers: alice, bob',
          clients: [
            {
              client_id: 'cid-amb-1',
              ip_address: '172.18.0.1',
              user_agent: 'JellyfinWeb/1.0',
              connected_at: '2026-05-20T00:00:00Z',
              last_active: '2026-05-20T00:01:00Z',
              user_id: '0',
              username: null,
              emby_user_name: '2 viewers: alice, bob',
            },
          ],
        },
      ],
    } as unknown as ChannelStatsResponse);

    render(<StatsTab />);

    await waitFor(() => {
      expect(screen.getByText('2 viewers: alice, bob')).toBeInTheDocument();
    });
    // The rollup is attributed via the media-server badge, and the raw
    // "User #0" fallback does not appear.
    expect(screen.getByText('via Emby')).toBeInTheDocument();
    expect(screen.queryByText('User #0')).not.toBeInTheDocument();
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

  it('shows NO header viewer suffix when only 1 viewer total (bd-cat70: dedup with Connected Clients)', async () => {
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
      expect(container.querySelector('.channel-card')).toBeInTheDocument();
    });
    // bd-cat70: single-viewer name is rendered in the Connected Clients
    // section (with AttributionBadge), NOT duplicated in the channel
    // header. The legacy "channel-header-single-viewer" testid was
    // removed along with that markup.
    expect(container.querySelector('[data-testid="channel-header-single-viewer"]')).toBeNull();
    expect(screen.queryByText('(watching: OnlyUser)')).not.toBeInTheDocument();
    // Rollup also absent when totalViewers === 1.
    expect(screen.queryByTestId('channel-header-viewer-rollup')).not.toBeInTheDocument();
  });
});

// bd-r5f0c.12 (W12): Per-client viewer dedup — same-user multi-stream.
// When a single user has multiple concurrent streams from the same source,
// the per-client viewer list renders "name (N)" instead of repeating the name.
// Grouping is by user_id (primary) with user_name fallback when user_id is null.

describe('StatsTab — per-client viewer dedup display (bd-r5f0c.12 / W12)', () => {
  // Helper to build a mock response with a single channel + single client
  const makeViewerMockResponse = (
    viewers: { user_id: string | null; user_name: string }[],
  ) => ({
    count: 1,
    channels: [
      {
        ...baseMultiViewerChannel,
        clients: [
          {
            client_id: 'cid-dedup',
            ip_address: '10.0.1.1',
            user_agent: 'Emby/1.0',
            connected_at: '2026-05-17T00:00:00Z',
            last_active: '2026-05-17T00:01:00Z',
            emby_viewers: viewers,
          },
        ],
      },
    ],
  });

  // Test 1: same user_id → "name (N)" suffix
  it('renders "name (N)" for same-user multi-stream', async () => {
    vi.mocked(api.getChannelStats).mockResolvedValue(
      makeViewerMockResponse([
        { user_id: '1', user_name: 'jkaisersoze' },
        { user_id: '1', user_name: 'jkaisersoze' },
      ]) as unknown as ChannelStatsResponse,
    );

    render(<StatsTab />);

    await waitFor(() => {
      expect(screen.getByText('jkaisersoze (2)')).toBeInTheDocument();
    });
    // Must NOT repeat the plain name twice separated by a comma
    expect(screen.queryByText('jkaisersoze, jkaisersoze')).not.toBeInTheDocument();
  });

  // Test 2: two same-user streams + one distinct user → "name (N), other"
  it('renders mixed users with one duplicate as "name (N), other"', async () => {
    vi.mocked(api.getChannelStats).mockResolvedValue(
      makeViewerMockResponse([
        { user_id: '1', user_name: 'jkaisersoze' },
        { user_id: '1', user_name: 'jkaisersoze' },
        { user_id: '2', user_name: 'alice' },
      ]) as unknown as ChannelStatsResponse,
    );

    render(<StatsTab />);

    await waitFor(() => {
      // Map preserves insertion order, so jkaisersoze first
      expect(screen.getByText('jkaisersoze (2), alice')).toBeInTheDocument();
    });
  });

  // Test 3: distinct users → comma-separated, no count suffixes (W5 regression)
  it('renders distinct users as comma-separated list without count suffixes', async () => {
    vi.mocked(api.getChannelStats).mockResolvedValue(
      makeViewerMockResponse([
        { user_id: '1', user_name: 'alice' },
        { user_id: '2', user_name: 'bob' },
      ]) as unknown as ChannelStatsResponse,
    );

    render(<StatsTab />);

    await waitFor(() => {
      expect(screen.getByText('alice, bob')).toBeInTheDocument();
    });
  });

  // Test 4: 4 unique users → "(N viewers)" rollup (W5 regression)
  it('renders 4 unique users as "(N viewers)" rollup with details expansion', async () => {
    vi.mocked(api.getChannelStats).mockResolvedValue(
      makeViewerMockResponse([
        { user_id: '1', user_name: 'alice' },
        { user_id: '2', user_name: 'bob' },
        { user_id: '3', user_name: 'carol' },
        { user_id: '4', user_name: 'dave' },
      ]) as unknown as ChannelStatsResponse,
    );

    const { container } = render(<StatsTab />);

    await waitFor(() => {
      expect(screen.getByText('4 viewers')).toBeInTheDocument();
    });
    // Names hidden inside a <details> element
    expect(container.querySelector('details.viewer-details')).toBeInTheDocument();
  });

  // Test 5: 4 streams from same user → "name (4)" inline, NOT rollup
  // (group count = 1, below the rollup threshold of 4 groups)
  it('renders 4 streams from the same user as "name (4)" inline (not rollup)', async () => {
    vi.mocked(api.getChannelStats).mockResolvedValue(
      makeViewerMockResponse([
        { user_id: '1', user_name: 'jkaisersoze' },
        { user_id: '1', user_name: 'jkaisersoze' },
        { user_id: '1', user_name: 'jkaisersoze' },
        { user_id: '1', user_name: 'jkaisersoze' },
      ]) as unknown as ChannelStatsResponse,
    );

    const { container } = render(<StatsTab />);

    await waitFor(() => {
      expect(screen.getByText('jkaisersoze (4)')).toBeInTheDocument();
    });
    // Only 1 group — must NOT use the rollup <details> element
    expect(container.querySelector('details.viewer-details')).not.toBeInTheDocument();
  });

  // Test 6: user_id null → group by user_name fallback (Plex edge case from W9)
  it('groups by user_name fallback when user_id is null', async () => {
    vi.mocked(api.getChannelStats).mockResolvedValue(
      makeViewerMockResponse([
        { user_id: null, user_name: 'alice' },
        { user_id: null, user_name: 'alice' },
      ]) as unknown as ChannelStatsResponse,
    );

    render(<StatsTab />);

    await waitFor(() => {
      expect(screen.getByText('alice (2)')).toBeInTheDocument();
    });
  });

  // Test 7: same user_id with differing user_name (e.g., Emby mid-session rename).
  // Implementation choice: first-seen user_name wins (Map insertion order).
  // The second entry increments the count but the first name is kept.
  it('groups by user_id even when user_name differs (first-seen name wins)', async () => {
    vi.mocked(api.getChannelStats).mockResolvedValue(
      makeViewerMockResponse([
        { user_id: '1', user_name: 'jkaisersoze' },
        { user_id: '1', user_name: 'jkaisersoze (alt)' }, // same user_id, different display name
      ]) as unknown as ChannelStatsResponse,
    );

    render(<StatsTab />);

    await waitFor(() => {
      // Only 1 group total — first-seen name 'jkaisersoze' kept, count = 2
      expect(screen.getByText('jkaisersoze (2)')).toBeInTheDocument();
    });
    // The alternate name string must not appear as a standalone rendered item
    expect(screen.queryByText('jkaisersoze (alt)')).not.toBeInTheDocument();
  });
});

// bd-w9c2a: Connected Clients section header must count actual upstream
// viewers, not ECM-side proxy connections. A media-server transcoding
// proxy (Emby / Plex / Jellyfin) is ONE ECM connection but can carry N
// upstream humans via the *_viewers[] lists. Counting raw clients.length
// undercounts in the deduped-upstream case.

describe('StatsTab — Connected Clients header viewer count (bd-w9c2a)', () => {
  // Read the rendered text out of the `.clients-header` element. The
  // header mixes a Material Icons <span> with a text node, so plain
  // getByText('Connected Clients (3)') wouldn't match — but the
  // node's textContent does.
  const readClientsHeaderText = (container: HTMLElement): string => {
    const header = container.querySelector('.clients-header');
    expect(header).not.toBeNull();
    // Trim and collapse whitespace so the test isn't sensitive to JSX
    // indentation in the source.
    return (header!.textContent || '').replace(/\s+/g, ' ').trim();
  };

  // PO's exact scenario (bd-w9c2a): 1 Dispatcharr + 1 Emby-proxy carrying
  // 2 upstream viewers. Pre-fix shows "(2)"; post-fix shows "(3)".
  it('counts 1 dispatcharr + 1 emby-proxy with 2 viewers as 3 humans', async () => {
    vi.mocked(api.getChannelStats).mockResolvedValue({
      count: 1,
      channels: [
        {
          ...baseMultiViewerChannel,
          client_count: 2,
          clients: [
            {
              client_id: 'cid-dispatcharr',
              ip_address: '10.0.2.1',
              user_agent: 'Dispatcharr/XC',
              connected_at: '2026-05-18T00:00:00Z',
              last_active: '2026-05-18T00:01:00Z',
              username: 'Hap',
            },
            {
              client_id: 'cid-emby-proxy',
              ip_address: '10.0.2.2',
              user_agent: 'Emby/1.0',
              connected_at: '2026-05-18T00:00:00Z',
              last_active: '2026-05-18T00:01:00Z',
              emby_viewers: [
                { user_id: 'emby-uid-1', user_name: 'MotWakorb' },
                { user_id: 'emby-uid-2', user_name: 'jkaisersoze' },
              ],
            },
          ],
        },
      ],
    } as unknown as ChannelStatsResponse);

    const { container } = render(<StatsTab />);

    await waitFor(() => {
      expect(container.querySelector('.clients-header')).toBeInTheDocument();
    });
    expect(readClientsHeaderText(container)).toContain('Connected Clients (3)');
  });

  // Boundary: single proxy connection with 5 emby viewers reads "(5)".
  it('counts 1 client with 5 emby_viewers as 5', async () => {
    vi.mocked(api.getChannelStats).mockResolvedValue({
      count: 1,
      channels: [
        {
          ...baseMultiViewerChannel,
          client_count: 1,
          clients: [
            {
              client_id: 'cid-emby-fanout',
              ip_address: '10.0.2.3',
              user_agent: 'Emby/1.0',
              connected_at: '2026-05-18T00:00:00Z',
              last_active: '2026-05-18T00:01:00Z',
              emby_viewers: [
                { user_id: 'u1', user_name: 'alice' },
                { user_id: 'u2', user_name: 'bob' },
                { user_id: 'u3', user_name: 'carol' },
                { user_id: 'u4', user_name: 'dave' },
                { user_id: 'u5', user_name: 'eve' },
              ],
            },
          ],
        },
      ],
    } as unknown as ChannelStatsResponse);

    const { container } = render(<StatsTab />);

    await waitFor(() => {
      expect(container.querySelector('.clients-header')).toBeInTheDocument();
    });
    expect(readClientsHeaderText(container)).toContain('Connected Clients (5)');
  });

  // Regression: pure-Dispatcharr case (no viewer lists) preserves the
  // existing "1 client = 1 human" semantic.
  it('counts 2 dispatcharr-only clients (no viewers) as 2', async () => {
    vi.mocked(api.getChannelStats).mockResolvedValue({
      count: 1,
      channels: [
        {
          ...baseMultiViewerChannel,
          client_count: 2,
          clients: [
            {
              client_id: 'cid-d1',
              ip_address: '10.0.2.4',
              user_agent: 'VLC/3.0',
              connected_at: '2026-05-18T00:00:00Z',
              last_active: '2026-05-18T00:01:00Z',
              username: 'alice',
            },
            {
              client_id: 'cid-d2',
              ip_address: '10.0.2.5',
              user_agent: 'VLC/3.0',
              connected_at: '2026-05-18T00:00:00Z',
              last_active: '2026-05-18T00:01:00Z',
              username: 'bob',
            },
          ],
        },
      ],
    } as unknown as ChannelStatsResponse);

    const { container } = render(<StatsTab />);

    await waitFor(() => {
      expect(container.querySelector('.clients-header')).toBeInTheDocument();
    });
    expect(readClientsHeaderText(container)).toContain('Connected Clients (2)');
  });

  // Mixed sources on a single client (rare): MAX across sources, not sum.
  // This matches the per-client renderer's source-precedence semantic
  // and avoids double-counting the same humans visible to multiple
  // resolvers concurrently.
  it('takes MAX across source viewer lists when a single client has both emby and plex viewers', async () => {
    vi.mocked(api.getChannelStats).mockResolvedValue({
      count: 1,
      channels: [
        {
          ...baseMultiViewerChannel,
          client_count: 1,
          clients: [
            {
              client_id: 'cid-mixed-src',
              ip_address: '10.0.2.6',
              user_agent: 'Proxy/1.0',
              connected_at: '2026-05-18T00:00:00Z',
              last_active: '2026-05-18T00:01:00Z',
              // 2 emby viewers + 1 plex viewer on same client → MAX=2 (not 3).
              emby_viewers: [
                { user_id: 'e1', user_name: 'alice' },
                { user_id: 'e2', user_name: 'bob' },
              ],
              plex_viewers: [{ user_id: 'p1', user_name: 'alice' }],
            },
          ],
        },
      ],
    } as unknown as ChannelStatsResponse);

    const { container } = render(<StatsTab />);

    await waitFor(() => {
      expect(container.querySelector('.clients-header')).toBeInTheDocument();
    });
    expect(readClientsHeaderText(container)).toContain('Connected Clients (2)');
  });
});

describe('StatsTab — real client device IP field (bd-7ncci)', () => {
  it('renders the Client IP field for an attributed connection', async () => {
    vi.mocked(api.getChannelStats).mockResolvedValue({
      count: 1,
      channels: [
        {
          ...baseMultiViewerChannel,
          clients: [
            {
              client_id: 'cid-ip1',
              ip_address: '172.18.0.1',
              user_agent: 'Emby/1.0',
              connected_at: '2026-05-20T00:00:00Z',
              last_active: '2026-05-20T00:01:00Z',
              emby_viewers: [
                { user_id: 'e1', user_name: 'amitb', client_ip: '47.203.164.8' },
              ],
              client_ip: '47.203.164.8',
            },
          ],
        },
      ],
    } as unknown as ChannelStatsResponse);

    render(<StatsTab />);

    await waitFor(() => {
      expect(screen.getByTestId('client-device-ip')).toBeInTheDocument();
    });
    // The real device IP is shown in the new field.
    expect(screen.getByTestId('client-device-ip')).toHaveTextContent(
      'Client IP: 47.203.164.8'
    );
    // The Dispatcharr connection IP is still rendered, unchanged.
    expect(screen.getByText('172.18.0.1')).toBeInTheDocument();
  });

  it('renders no Client IP field when the connection is not attributed', async () => {
    vi.mocked(api.getChannelStats).mockResolvedValue({
      count: 1,
      channels: [
        {
          ...baseMultiViewerChannel,
          clients: [
            {
              client_id: 'cid-ip2',
              ip_address: '10.0.0.5',
              user_agent: 'VLC/3.0',
              connected_at: '2026-05-20T00:00:00Z',
              last_active: '2026-05-20T00:01:00Z',
            },
          ],
        },
      ],
    } as unknown as ChannelStatsResponse);

    render(<StatsTab />);

    await waitFor(() => {
      expect(screen.getByText('10.0.0.5')).toBeInTheDocument();
    });
    // No Client IP field for an unattributed connection.
    expect(screen.queryByTestId('client-device-ip')).not.toBeInTheDocument();
  });

  it('renders the set of Client IPs for a server-proxy / rollup connection', async () => {
    vi.mocked(api.getChannelStats).mockResolvedValue({
      count: 1,
      channels: [
        {
          ...baseMultiViewerChannel,
          clients: [
            {
              client_id: 'cid-ip3',
              ip_address: '172.16.0.19',
              user_agent: 'Emby-Server/4.8',
              connected_at: '2026-05-20T00:00:00Z',
              last_active: '2026-05-20T00:01:00Z',
              emby_viewers: [
                { user_id: 'e1', user_name: 'amitb', client_ip: '47.203.164.8' },
                { user_id: 'e2', user_name: 'MotWakorb', client_ip: '172.16.0.2' },
              ],
              client_ips: ['47.203.164.8', '172.16.0.2'],
            },
          ],
        },
      ],
    } as unknown as ChannelStatsResponse);

    render(<StatsTab />);

    await waitFor(() => {
      expect(screen.getByTestId('client-device-ip')).toBeInTheDocument();
    });
    expect(screen.getByTestId('client-device-ip')).toHaveTextContent(
      'Client IP: 47.203.164.8, 172.16.0.2'
    );
  });
});

// bd-tkcmu: scroll-containment regression lock.
//
// Scrolling over the footer while on the Stats tab used to scroll the
// ENTIRE page off the viewport.  The fix is two-part:
//   1. index.css sets overflow:hidden on html/body/#root so the document
//      can never be a scroll container (app-shell defence, not testable
//      here — JSDOM doesn't honour CSS files).
//   2. .stats-tab sets overscroll-behavior:contain so wheel events don't
//      chain out of the component even when the inner .stats-content is
//      at a scroll boundary (belt-and-suspenders).
//
// This test is a structural assertion: it verifies that
//   • .stats-tab is the component root (the element that carries the CSS)
//   • .stats-content is its scroll-container child (the element with the
//     scroll containment rule)
// If either class is removed or renamed the test will fail, surfacing a
// regression at the layout level before a browser is involved.
//
// Manual repro (without the fix): open the Stats tab in any browser,
// scroll the page so the footer is visible, hover the mouse over the
// footer "API: Healthy" bar, then wheel-scroll — the entire UI scrolls
// off the page.  With the fix the page stays locked; only the
// .stats-content area scrolls.

describe('StatsTab — scroll-containment DOM structure (bd-tkcmu)', () => {
  beforeEach(() => {
    vi.mocked(api.getChannelStats).mockResolvedValue({
      count: 0,
      channels: [],
    } as unknown as ChannelStatsResponse);
  });

  it('renders .stats-tab as the root element and .stats-content as a descendant once loaded', async () => {
    const { container } = render(<StatsTab />);

    // Wait for the loading state to clear (all API calls must resolve).
    // The component shows .tab-loading while loading; once done it
    // renders .stats-content inside .stats-tab.
    await waitFor(() => {
      expect(container.querySelector('.stats-content')).toBeInTheDocument();
    });

    // The component root must carry the .stats-tab class (which owns
    // overflow:hidden + overscroll-behavior:contain in the CSS).
    const statsTab = container.querySelector('.stats-tab');
    expect(statsTab).toBeInTheDocument();

    // .stats-content must be inside .stats-tab (not at the same level or
    // outside) so the CSS containment chain is intact.
    const statsContent = statsTab?.querySelector('.stats-content');
    expect(statsContent).not.toBeNull();

    // Sanity-check: .stats-content must not be the stats-tab root itself.
    expect(statsTab).not.toBe(statsContent);
  });
});

// bead 09x38.15 item 9: ~10 sections stack inside .stats-content with no way
// to jump between them but scrolling.
//
// The original implementation was a "Jump to section" OverflowMenu carrying a
// hand-maintained item list. enhancedchannelmanager-2896r.21 deleted it: the
// shared StickySectionNav ("On this page") already covers this route, so the
// menu was a second control for the same job — and one that had to be kept in
// sync with the JSX by hand. The nav derives its entries from the sections
// actually mounted in `.stats-content`, so the "only offer sections that exist"
// contract these specs pin is now structural rather than hand-maintained.
describe('StatsTab — section jump nav (bead 09x38.15 item 9)', () => {
  // The nav scrolls its own container (never the document — see
  // StickySectionNav), and jsdom does not implement Element.scrollTo.
  beforeEach(() => {
    Element.prototype.scrollTo = vi.fn();
    Element.prototype.scrollIntoView = vi.fn();
    vi.mocked(api.getChannelStats).mockResolvedValue({
      count: 0,
      channels: [],
    } as unknown as ChannelStatsResponse);
    // No bandwidth data — the component swallows this into a `null` state
    // (see fetchData's .catch) rather than surfacing an error, so the
    // Bandwidth Usage summary section doesn't render.
    vi.mocked(api.getBandwidthStats).mockRejectedValue(new Error('unavailable'));
  });

  it('always lists the always-mounted panels, but omits conditional sections with no data', async () => {
    render(<StatsTab />);

    const nav = await screen.findByRole('navigation', { name: 'On this page' });

    for (const label of [
      'Bandwidth In/Out',
      'Enhanced Statistics',
      'Popularity Rankings',
      'Watch History',
      'User Watch Time',
      'Providers',
      'Provider Stream Usage',
    ]) {
      expect(within(nav).getByRole('button', { name: label })).toBeInTheDocument();
    }

    // No active channels / events / top-watched / bandwidth summary data
    // seeded — those sections don't render, so they must not appear either.
    for (const label of ['Active Channels', 'Recent Events', 'Top Watched Channels', 'Bandwidth Usage']) {
      expect(within(nav).queryByRole('button', { name: label })).not.toBeInTheDocument();
    }
  });

  it('lists Active Channels once channel-stats data is present, and clicking it scrolls the content container', async () => {
    vi.mocked(api.getChannelStats).mockResolvedValue({
      count: 1,
      channels: [{ channel_id: 'abc', channel_name: 'ESPN', channel_number: 1, state: 'streaming', client_count: 0, clients: [] }],
    } as unknown as ChannelStatsResponse);
    const user = userEvent.setup();
    const { container } = render(<StatsTab />);

    await waitFor(() => {
      expect(container.querySelector('#stats-section-active-channels')).toBeInTheDocument();
    });
    const nav = await screen.findByRole('navigation', { name: 'On this page' });
    const jump = await within(nav).findByRole('button', { name: 'Active Channels' });

    const statsContent = container.querySelector('.stats-content') as HTMLElement;
    const scrollTo = vi.fn();
    statsContent.scrollTo = scrollTo;
    await user.click(jump);

    // The scroll is confined to .stats-content: the fixed shell must not move.
    expect(scrollTo).toHaveBeenCalled();
    expect(Element.prototype.scrollIntoView).not.toHaveBeenCalled();
  });
});

// enhancedchannelmanager-2sfpt #1: a small channel logo renders beside the
// channel number in Active Channels rows, resolved from the channel's logo_id
// via the loaded logo map. Null/unknown logos render no <img> (no broken icon).
describe('StatsTab — Active Channels logo (#1)', () => {
  const CH_UUID = 'ecc0280e-af3f-47c3-b344-39f21c80b880';

  it('renders the channel logo img when the channel has a resolvable logo', async () => {
    vi.mocked(api.getChannels).mockResolvedValue({
      results: [{ uuid: CH_UUID, name: 'ESPN HD', channel_number: 5, logo_id: 42 }],
      next: null,
      count: 1,
    } as unknown as Awaited<ReturnType<typeof api.getChannels>>);
    vi.mocked(api.getAllLogos).mockResolvedValue([
      { id: 42, name: 'ESPN', url: 'http://logos/espn.png', cache_url: 'http://logos/cache/42', channel_count: 1, is_used: true },
    ]);
    vi.mocked(api.getChannelStats).mockResolvedValue({
      count: 1,
      channels: [{ channel_id: CH_UUID, channel_name: 'ESPN HD', channel_number: 5, state: 'streaming', client_count: 0, clients: [] }],
    } as unknown as ChannelStatsResponse);

    const { container } = render(<StatsTab />);

    await waitFor(() => {
      const img = container.querySelector('img.channel-logo-mini') as HTMLImageElement | null;
      expect(img).not.toBeNull();
      expect(img?.src).toContain('http://logos/cache/42');
    });
  });

  it('renders no logo img when the channel has no logo_id', async () => {
    vi.mocked(api.getChannels).mockResolvedValue({
      results: [{ uuid: CH_UUID, name: 'ESPN HD', channel_number: 5, logo_id: null }],
      next: null,
      count: 1,
    } as unknown as Awaited<ReturnType<typeof api.getChannels>>);
    vi.mocked(api.getAllLogos).mockResolvedValue([]);
    vi.mocked(api.getChannelStats).mockResolvedValue({
      count: 1,
      channels: [{ channel_id: CH_UUID, channel_name: 'ESPN HD', channel_number: 5, state: 'streaming', client_count: 0, clients: [] }],
    } as unknown as ChannelStatsResponse);

    const { container } = render(<StatsTab />);

    await waitFor(() => {
      expect(container.querySelector('.channel-card')).toBeInTheDocument();
    });
    expect(container.querySelector('img.channel-logo-mini')).toBeNull();
  });
});

// enhancedchannelmanager-2sfpt #2: Recent Events show the connecting /
// disconnecting username (resolved server-side), falling back to the client IP
// when no username is attributed.
describe('StatsTab — Recent Events username (#2)', () => {
  beforeEach(() => {
    vi.mocked(api.getChannelStats).mockResolvedValue({
      count: 0, channels: [],
    } as unknown as ChannelStatsResponse);
  });

  it('shows the resolved username on the event line', async () => {
    vi.mocked(api.getSystemEvents).mockResolvedValue({
      events: [
        { id: 1, event_type: 'client_connect', channel_name: 'ESPN', message: 'connected', ip_address: '10.0.0.5', username: 'alice' },
      ],
      count: 1, total: 1, offset: 0, limit: 50,
    } as unknown as Awaited<ReturnType<typeof api.getSystemEvents>>);

    const { container } = render(<StatsTab />);

    await waitFor(() => {
      const msg = container.querySelector('.event-message');
      expect(msg?.textContent).toContain('- alice');
    });
  });

  it('falls back to the IP when the event has no username', async () => {
    vi.mocked(api.getSystemEvents).mockResolvedValue({
      events: [
        { id: 2, event_type: 'client_disconnect', channel_name: 'ESPN', message: 'disconnected', ip_address: '203.0.113.9', username: null },
      ],
      count: 1, total: 1, offset: 0, limit: 50,
    } as unknown as Awaited<ReturnType<typeof api.getSystemEvents>>);

    const { container } = render(<StatsTab />);

    await waitFor(() => {
      const msg = container.querySelector('.event-message');
      expect(msg?.textContent).toContain('- 203.0.113.9');
    });
  });
});

// Currently Showing (EPG) row on Active Channels cards
// (enhancedchannelmanager-hoj54): joins the active stream (keyed by UUID)
// to its guide programs via epg_data_id → EPGData.tvg_id (preferred),
// channel.tvg_id (fallback), or channel UUID (dummy EPG sources), and
// renders the program airing right now.
describe('StatsTab — Currently Showing EPG row (hoj54)', () => {
  const CS_UUID = 'aaaabbbb-cccc-dddd-eeee-ffff00001111';

  // A program window straddling "now" so the row always matches.
  const nowMs = Date.now();
  const startIso = new Date(nowMs - 30 * 60 * 1000).toISOString();
  const endIso = new Date(nowMs + 30 * 60 * 1000).toISOString();

  function mockActiveChannel(channelFields: Record<string, unknown>) {
    vi.mocked(api.getChannels).mockResolvedValue({
      results: [{ uuid: CS_UUID, name: 'ESPN HD', channel_number: 5, logo_id: null, tvg_id: null, epg_data_id: null, ...channelFields }],
      next: null,
      count: 1,
    } as unknown as Awaited<ReturnType<typeof api.getChannels>>);
    vi.mocked(api.getChannelStats).mockResolvedValue({
      count: 1,
      channels: [{ channel_id: CS_UUID, channel_name: 'ESPN HD', channel_number: 5, state: 'streaming', client_count: 0, clients: [] }],
    } as unknown as ChannelStatsResponse);
  }

  it('renders the current program via the epg_data_id → tvg_id hop', async () => {
    mockActiveChannel({ epg_data_id: 10, tvg_id: 'stale.channel.tvg' });
    vi.mocked(api.getEPGData).mockResolvedValue([
      { id: 10, tvg_id: 'espn.us', name: 'ESPN', icon_url: null, epg_source: 1 },
    ]);
    vi.mocked(api.getEPGGrid).mockResolvedValue([
      { id: 1, tvg_id: 'espn.us', title: 'SportsCenter', start_time: startIso, end_time: endIso },
      { id: 2, tvg_id: 'espn.us', title: 'Later Show', start_time: endIso, end_time: new Date(nowMs + 90 * 60 * 1000).toISOString() },
    ] as unknown as Awaited<ReturnType<typeof api.getEPGGrid>>);

    render(<StatsTab />);

    await waitFor(() => {
      expect(screen.getByTestId('channel-now-playing')).toHaveTextContent('SportsCenter');
    });
    expect(screen.getByTestId('channel-now-playing')).not.toHaveTextContent('Later Show');
  });

  it('falls back to channel.tvg_id when there is no epg_data_id mapping', async () => {
    mockActiveChannel({ tvg_id: 'tnt.us' });
    vi.mocked(api.getEPGGrid).mockResolvedValue([
      { id: 3, tvg_id: 'tnt.us', title: 'Movie Night', start_time: startIso, end_time: endIso },
    ] as unknown as Awaited<ReturnType<typeof api.getEPGGrid>>);

    render(<StatsTab />);

    await waitFor(() => {
      expect(screen.getByTestId('channel-now-playing')).toHaveTextContent('Movie Night');
    });
  });

  it('matches dummy EPG programs keyed by the channel UUID', async () => {
    mockActiveChannel({});
    vi.mocked(api.getEPGGrid).mockResolvedValue([
      { id: 4, tvg_id: CS_UUID, title: 'Dummy Program', start_time: startIso, end_time: endIso },
    ] as unknown as Awaited<ReturnType<typeof api.getEPGGrid>>);

    render(<StatsTab />);

    await waitFor(() => {
      expect(screen.getByTestId('channel-now-playing')).toHaveTextContent('Dummy Program');
    });
  });

  it('renders no row when no program is airing right now', async () => {
    mockActiveChannel({ tvg_id: 'tnt.us' });
    // Program already ended — nothing is currently showing.
    vi.mocked(api.getEPGGrid).mockResolvedValue([
      { id: 5, tvg_id: 'tnt.us', title: 'Old Program', start_time: new Date(nowMs - 120 * 60 * 1000).toISOString(), end_time: new Date(nowMs - 60 * 60 * 1000).toISOString() },
    ] as unknown as Awaited<ReturnType<typeof api.getEPGGrid>>);

    const { container } = render(<StatsTab />);

    await waitFor(() => {
      expect(container.querySelector('.channel-card')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('channel-now-playing')).toBeNull();
  });

  it('renders no row when EPG guide data is unavailable', async () => {
    mockActiveChannel({ tvg_id: 'tnt.us' });
    vi.mocked(api.getEPGGrid).mockRejectedValue(new Error('grid down'));
    vi.mocked(api.getEPGData).mockRejectedValue(new Error('epg down'));

    const { container } = render(<StatsTab />);

    await waitFor(() => {
      expect(container.querySelector('.channel-card')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('channel-now-playing')).toBeNull();
  });
});

// bd-49obj / GH-481: Live Stats condensed provider table. With more than
// LIVE_STATS_CONDENSED_THRESHOLD (6) providers, the tile badges are
// replaced by a dense table so every provider stays visible instead of
// wrapping out of view. At/below the threshold, the existing tile
// presentation is unchanged.
describe('StatsTab — condensed Live Stats provider table (bd-49obj)', () => {
  function makeAccounts(count: number) {
    return Array.from({ length: count }, (_, i) => ({
      id: i + 1,
      name: `Provider ${i + 1}`,
      profiles: [],
      is_active: true,
      max_streams: 10,
    })) as unknown as Awaited<ReturnType<typeof api.getM3UAccounts>>;
  }

  function makeChannels(providerIds: number[]) {
    return providerIds.map((pid, i) => ({
      ...baseChannel,
      channel_id: `uuid-${i}`,
      m3u_account_id: pid,
    }));
  }

  it('renders the tile badges (not the table) at or below the threshold', async () => {
    const accounts = makeAccounts(6);
    vi.mocked(api.getM3UAccounts).mockResolvedValue(accounts);
    vi.mocked(api.getChannelStats).mockResolvedValue({
      count: 6,
      channels: makeChannels([1, 2, 3, 4, 5, 6]),
    } as unknown as ChannelStatsResponse);

    const { container } = render(<StatsTab />);

    await waitFor(() => {
      expect(container.querySelectorAll('.summary-stat').length).toBeGreaterThan(2);
    });
    expect(container.querySelector('[data-testid="provider-live-table-wrapper"]')).toBeNull();
  });

  it('renders every provider in a condensed table above the threshold (11 providers, GH-481 reporter count)', async () => {
    const accounts = makeAccounts(11);
    vi.mocked(api.getM3UAccounts).mockResolvedValue(accounts);
    vi.mocked(api.getChannelStats).mockResolvedValue({
      count: 11,
      channels: makeChannels([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]),
    } as unknown as ChannelStatsResponse);

    const { container } = render(<StatsTab />);

    const table = await waitFor(() => {
      const el = container.querySelector('[data-testid="provider-live-table-wrapper"]');
      expect(el).toBeInTheDocument();
      return el as HTMLElement;
    });

    // All 11 providers present as table rows — none wrapped out of view.
    for (let i = 1; i <= 11; i++) {
      expect(table).toHaveTextContent(`Provider ${i}`);
    }
    // Tile badges for individual providers are NOT rendered in this mode.
    // The two page-total tiles still are — those aren't providers. Their
    // labels were shortened to "Active"/"Clients" by 2896r.21 when the strip
    // moved onto the route header's breadcrumb row.
    const tileLabels = Array.from(container.querySelectorAll('.summary-stat')).map(
      (el) => el.querySelector('.stat-label')?.textContent?.trim() ?? '',
    );
    expect(tileLabels).toEqual(['Active', 'Clients']);

    // Each provider shows its current/max connection count in the table.
    expect(table).toHaveTextContent('1/10');
  });

  it('surfaces the Unknown bucket as a table row when providers exceed the threshold', async () => {
    const accounts = makeAccounts(7);
    vi.mocked(api.getM3UAccounts).mockResolvedValue(accounts);
    vi.mocked(api.getChannelStats).mockResolvedValue({
      count: 8,
      channels: [
        ...makeChannels([1, 2, 3, 4, 5, 6, 7]),
        { ...baseChannel, channel_id: 'uuid-unattributed', m3u_account_id: null },
      ],
    } as unknown as ChannelStatsResponse);

    const { container } = render(<StatsTab />);

    const table = await waitFor(() => {
      const el = container.querySelector('[data-testid="provider-live-table-wrapper"]');
      expect(el).toBeInTheDocument();
      return el as HTMLElement;
    });

    expect(table).toHaveTextContent('Unknown');
    expect(container.querySelector('.provider-live-table tr.unknown-bucket')).toBeInTheDocument();
  });
});
