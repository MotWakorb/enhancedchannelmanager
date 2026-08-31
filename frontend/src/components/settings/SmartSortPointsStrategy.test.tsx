import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '../../services/api';
import { SettingsTab } from '../tabs/SettingsTab';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/api')>();
  return {
    ...actual,
    getSettings: vi.fn(),
    saveSettings: vi.fn(),
    getChannelProfiles: vi.fn(),
    listAlertMethods: vi.fn(),
    getM3UAccounts: vi.fn(),
    getStreams: vi.fn(),
    getProbeHistory: vi.fn(),
    getProbeProgress: vi.fn(),
  };
});

vi.mock('../../contexts/NotificationContext', () => ({
  useNotifications: () => ({
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
    notify: vi.fn().mockReturnValue('toast-id'),
    dismiss: vi.fn(),
  }),
}));

vi.mock('../../hooks/useAuth', () => ({
  useAuth: () => ({ user: { is_admin: true, username: 'admin' } }),
}));

vi.mock('../CustomSelect', () => ({
  CustomSelect: ({ value, onChange, options, disabled, ariaLabel }: {
    value: string;
    onChange: (value: string) => void;
    options: { value: string; label: string }[];
    disabled?: boolean;
    ariaLabel?: string;
  }) => (
    <select
      aria-label={ariaLabel}
      disabled={disabled}
      value={value}
      onChange={(event) => onChange(event.target.value)}
    >
      {options.map((option) => (
        <option key={option.value} value={option.value}>{option.label}</option>
      ))}
    </select>
  ),
}));

vi.mock('../StickySectionNav', () => ({
  StickySectionNav: () => null,
}));

type SettingsResponse = Awaited<ReturnType<typeof api.getSettings>>;

const PRIORITY_CRITERIA = [
  'resolution',
  'bitrate',
  'framerate',
  'video_codec',
  'm3u_priority',
  'audio_channels',
  'custom_streams',
  'catchup',
] as api.SortCriterion[];

const ALL_POINT_RULES: api.StreamSortPointRule[] = [
  { criterion: 'resolution', operator: 'gte', value: 1080, points: 20 },
  { criterion: 'bitrate', operator: 'gte', value: 6_000_000, points: 25 },
  { criterion: 'framerate', operator: 'lt', value: 59.94, points: 2 },
  { criterion: 'video_codec', operator: 'gte', value: 'h265', points: 10 },
  { criterion: 'm3u_priority', operator: 'ne', value: -1, points: 3 },
  { criterion: 'audio_channels', operator: 'eq', value: 2, points: 4 },
  { criterion: 'custom_streams', operator: 'eq', value: true, points: 5 },
  { criterion: 'catchup', operator: 'eq', value: false, points: -5 },
  { criterion: 'failed', operator: 'eq', value: true, points: 40 },
  { criterion: 'black_screen', operator: 'eq', value: true, points: -30 },
  { criterion: 'low_fps', operator: 'eq', value: true, points: -20 },
];

function makeSettings(overrides: Partial<SettingsResponse> = {}): SettingsResponse {
  return {
    configured: true,
    url: 'http://dispatcharr.test',
    auth_method: 'password',
    username: 'admin',
    auto_rename_channel_number: false,
    include_channel_number_in_name: false,
    channel_number_separator: '-',
    remove_country_prefix: false,
    include_country_in_name: false,
    country_separator: '|',
    timezone_preference: 'both',
    show_stream_urls: true,
    hide_auto_sync_groups: false,
    hide_ungrouped_streams: true,
    hide_epg_urls: false,
    hide_m3u_urls: false,
    gracenote_conflict_mode: 'ask',
    theme: 'dark',
    date_format: 'auto',
    default_channel_profile_ids: [],
    linked_m3u_accounts: [[7, 11]],
    allow_multi_provider_auto_sync: false,
    epg_auto_match_threshold: 80,
    custom_network_prefixes: [],
    custom_network_suffixes: [],
    stats_poll_interval: 10,
    user_timezone: '',
    backend_log_level: 'INFO',
    frontend_log_level: 'INFO',
    vlc_open_behavior: 'm3u_fallback',
    stream_probe_timeout: 30,
    stream_probe_schedule_time: '03:00',
    bitrate_sample_duration: 10,
    parallel_probing_enabled: true,
    max_concurrent_probes: 8,
    profile_distribution_strategy: 'fill_first',
    skip_recently_probed_hours: 0,
    refresh_m3us_before_probe: true,
    auto_reorder_after_probe: false,
    push_stream_stats_to_dispatcharr: false,
    probe_retry_count: 1,
    probe_retry_delay: 2,
    stream_fetch_page_limit: 200,
    stream_sort_priority: PRIORITY_CRITERIA,
    stream_sort_enabled: {
      resolution: true,
      bitrate: true,
      framerate: true,
      video_codec: false,
      m3u_priority: false,
      audio_channels: false,
      custom_streams: false,
      catchup: false,
    },
    m3u_account_priorities: {},
    black_screen_detection_enabled: false,
    black_screen_sample_duration: 5,
    low_fps_threshold: 20,
    deprioritize_failed_streams: true,
    deprioritize_black_screen: true,
    deprioritize_low_fps: true,
    failed_stream_sort_order: ['failed', 'black_screen', 'low_fps'],
    strike_threshold: 3,
    normalize_on_channel_create: false,
    public_base_url: '',
    smtp_configured: false,
    smtp_host: '',
    smtp_port: 587,
    smtp_user: '',
    smtp_from_email: '',
    smtp_from_name: 'ECM Alerts',
    smtp_use_tls: true,
    smtp_use_ssl: false,
    discord_configured: false,
    discord_webhook_url: '',
    telegram_configured: false,
    telegram_bot_token: '',
    telegram_chat_id: '',
    mcp_api_key_configured: false,
    telemetry_client_errors_enabled: true,
    dedup_threshold: 0.8,
    dedup_m3u_toast_suppressed: false,
    emby_enabled: false,
    emby_base_url: '',
    emby_api_key_configured: false,
    plex_enabled: false,
    plex_base_url: '',
    plex_token_configured: false,
    jellyfin_enabled: false,
    jellyfin_base_url: '',
    jellyfin_api_key_configured: false,
    trusted_media_networks: [],
    ssrf_outbound_mode: 'lan_friendly',
    ...overrides,
  } as SettingsResponse;
}

function renderOnChannelDefaults() {
  return render(<SettingsTab onSaved={vi.fn()} initialSettingsPage="channel-defaults" />);
}

function optionValues(select: HTMLElement): string[] {
  return within(select).getAllByRole('option').map((option) => (
    (option as HTMLOptionElement).value
  ));
}

describe('Smart Sort Priority and Points strategies', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings());
    vi.mocked(api.saveSettings).mockResolvedValue({
      status: 'ok',
      configured: true,
      server_changed: false,
    });
    vi.mocked(api.getChannelProfiles).mockResolvedValue([]);
    vi.mocked(api.listAlertMethods).mockResolvedValue([]);
    vi.mocked(api.getM3UAccounts).mockResolvedValue([]);
    vi.mocked(api.getStreams).mockResolvedValue({ count: 0, results: [] } as never);
    vi.mocked(api.getProbeHistory).mockResolvedValue([]);
    vi.mocked(api.getProbeProgress).mockResolvedValue({ in_progress: false } as never);
  });

  it('defaults omitted strategy settings to the unchanged Priority editor', async () => {
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings({
      stream_sort_strategy: undefined,
      stream_sort_point_rules: undefined,
    }));

    renderOnChannelDefaults();

    await waitFor(() => expect(api.getSettings).toHaveBeenCalled());
    expect(screen.getByRole('radio', { name: 'Priority' })).toBeChecked();
    expect(screen.getByRole('radio', { name: 'Points' })).not.toBeChecked();
    for (const label of ['Resolution', 'Bitrate', 'Framerate', 'Video Codec', 'M3U Priority', 'Audio Channels', 'Custom Streams', 'Catch-up']) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.getByText('Deprioritize Failed Streams')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Add rule' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('radio', { name: 'Points' }));
    expect(screen.getByRole('radio', { name: 'Points' })).toBeChecked();
    expect(screen.getByRole('button', { name: 'Add rule' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('radio', { name: 'Priority' }));
    expect(screen.getByRole('radio', { name: 'Priority' })).toBeChecked();
    expect(screen.getByText('Deprioritize Failed Streams')).toBeInTheDocument();
  });

  it('loads every Points condition with only compatible operators and metric inputs', async () => {
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings({
      stream_sort_strategy: 'points',
      stream_sort_point_rules: ALL_POINT_RULES,
    }));

    renderOnChannelDefaults();

    await screen.findByRole('button', { name: 'Add rule' });
    expect(screen.getByRole('radio', { name: 'Points' })).toBeChecked();
    expect(screen.getByText(/all matching rules add together/i)).toBeInTheDocument();
    expect(screen.getByText(/order is organizational only/i)).toBeInTheDocument();

    expect(optionValues(screen.getByLabelText('Rule 1 condition'))).toEqual([
      'resolution',
      'bitrate',
      'framerate',
      'video_codec',
      'm3u_priority',
      'audio_channels',
      'custom_streams',
      'catchup',
      'failed',
      'black_screen',
      'low_fps',
    ]);

    const orderedOperators = ['eq', 'ne', 'gt', 'gte', 'lt', 'lte'];
    for (let ruleNumber = 1; ruleNumber <= 6; ruleNumber += 1) {
      expect(optionValues(screen.getByLabelText(`Rule ${ruleNumber} operator`))).toEqual(orderedOperators);
    }
    for (let ruleNumber = 7; ruleNumber <= 11; ruleNumber += 1) {
      expect(optionValues(screen.getByLabelText(`Rule ${ruleNumber} operator`))).toEqual(['eq']);
    }

    expect(screen.getByLabelText('Rule 1 value')).toHaveAttribute('type', 'number');
    expect(screen.getByText('Value (vertical pixels)')).toBeInTheDocument();
    expect(screen.getByText('Value (kbps)')).toBeInTheDocument();
    expect(screen.getByLabelText('Rule 2 value')).toHaveValue(6000);
    expect(screen.getByText('Value (FPS)')).toBeInTheDocument();
    expect(optionValues(screen.getByLabelText('Rule 4 value'))).toEqual([
      'av1', 'hevc', 'h265', 'vp9', 'h264', 'avc', 'vp8', 'mpeg2video', 'mpeg2',
    ]);
    expect(optionValues(screen.getByLabelText('Rule 9 value'))).toEqual(['true', 'false']);
    expect(screen.getByLabelText('Rule 9 points')).toHaveValue(40);
    expect(screen.getByLabelText('Rule 10 points')).toHaveValue(-30);
    expect(screen.queryByText('Deprioritize Failed Streams')).not.toBeInTheDocument();
  });

  it('adds, edits, deletes, reorders, saves, and reloads rules without changing rule contents', async () => {
    const loadedRules: api.StreamSortPointRule[] = [
      { criterion: 'resolution', operator: 'gte', value: 1080, points: 20 },
      { criterion: 'failed', operator: 'eq', value: true, points: 30 },
      { criterion: 'bitrate', operator: 'lt', value: 2_000_000, points: -5 },
    ];
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings({
      stream_sort_strategy: 'points',
      stream_sort_point_rules: loadedRules,
    }));
    const firstRender = renderOnChannelDefaults();
    await screen.findByRole('button', { name: 'Add rule' });

    fireEvent.click(screen.getByRole('button', { name: 'Add rule' }));
    expect(screen.getAllByTestId('smart-sort-point-rule')).toHaveLength(4);
    fireEvent.click(screen.getByRole('button', { name: 'Delete rule 4' }));
    fireEvent.change(screen.getByLabelText('Rule 2 points'), { target: { value: '35' } });
    fireEvent.change(screen.getByLabelText('Rule 3 value'), { target: { value: '2500' } });
    fireEvent.click(screen.getByRole('button', { name: 'Move rule 1 down' }));
    fireEvent.click(screen.getByRole('button', { name: /Save Settings$/ }));

    await waitFor(() => expect(api.saveSettings).toHaveBeenCalledTimes(1));
    const payload = vi.mocked(api.saveSettings).mock.calls[0][0];
    expect(payload.stream_sort_strategy).toBe('points');
    expect(payload.stream_sort_point_rules).toEqual([
      { criterion: 'failed', operator: 'eq', value: true, points: 35 },
      { criterion: 'resolution', operator: 'gte', value: 1080, points: 20 },
      { criterion: 'bitrate', operator: 'lt', value: 2_500_000, points: -5 },
    ]);
    expect(payload.linked_m3u_accounts).toEqual([[7, 11]]);

    firstRender.unmount();
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings({
      stream_sort_strategy: payload.stream_sort_strategy,
      stream_sort_point_rules: payload.stream_sort_point_rules,
    }));
    renderOnChannelDefaults();

    await screen.findByRole('button', { name: 'Add rule' });
    expect(screen.getByLabelText('Rule 1 condition')).toHaveValue('failed');
    expect(screen.getByLabelText('Rule 1 points')).toHaveValue(35);
    expect(screen.getByLabelText('Rule 3 value')).toHaveValue(2500);
    expect(screen.getByLabelText('Rule 3 points')).toHaveValue(-5);
  });

  it('shows inline validation and refuses to serialize invalid numeric values or points', async () => {
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings({
      stream_sort_strategy: 'points',
      stream_sort_point_rules: [ALL_POINT_RULES[0]],
    }));
    renderOnChannelDefaults();
    await screen.findByRole('button', { name: 'Add rule' });

    fireEvent.change(screen.getByLabelText('Rule 1 value'), { target: { value: '' } });
    fireEvent.change(screen.getByLabelText('Rule 1 points'), { target: { value: '1.5' } });
    fireEvent.click(screen.getByRole('button', { name: /Save Settings$/ }));

    expect(await screen.findByText('Value must be a finite number.')).toBeInTheDocument();
    expect(screen.getByText('Points must be a signed whole number.')).toBeInTheDocument();
    expect(api.saveSettings).not.toHaveBeenCalled();
  });
});
