/**
 * Tests for the "Allow multi-provider auto-sync on shared groups" toggle
 * (bd-dgs64, GH #591).
 *
 * Dispatcharr channel groups are global: a group with the same name on two
 * M3U providers shares one channel_group ID. The M3UGroupsModal frontend
 * guard (commit 030c1ef8) locks a group's auto-sync controls to a single
 * "owning" account once another account already auto-syncs that ID, to
 * avoid two providers silently double-creating channels for the same group.
 *
 * `allow_multi_provider_auto_sync` is the opt-out. It's an install-wide
 * duplicate-channel-risk toggle (not a per-user display preference), so it
 * follows the same admin-gated pattern as the GH #473 Runaway Safety Cap:
 *   - The checkbox renders on the General/Display Options page and
 *     populates from loaded settings.
 *   - Help text warns enabling it can create duplicate channels.
 *   - An admin can flip it and the new value is sent in the save payload.
 *   - A non-admin sees the control disabled (backend field-level admin gate
 *     would 403 a change attempt).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

let mockUser: { is_admin: boolean; username: string } = { is_admin: true, username: 'admin' };

vi.mock('../../services/api', () => ({
  getSettings: vi.fn(),
  saveSettings: vi.fn(),
  getChannelProfiles: vi.fn(),
  generateMCPApiKey: vi.fn(),
  revokeMCPApiKey: vi.fn(),
  getMCPStatus: vi.fn(),
  listAlertMethods: vi.fn(),
  getM3UAccounts: vi.fn(),
  getExportSections: vi.fn(),
  listSavedBackups: vi.fn(),
  testEmbyConnection: vi.fn(),
  testPlexConnection: vi.fn(),
  testJellyfinConnection: vi.fn(),
  getStreams: vi.fn().mockResolvedValue({ streams: [], total: 0 }),
  getProbeHistory: vi.fn().mockResolvedValue([]),
  getProbeProgress: vi.fn().mockResolvedValue(null),
  getStreamGroups: vi.fn().mockResolvedValue([]),
}));

vi.mock('../../services/channelPipelineApi', () => ({
  getChannelPipelineRules: vi.fn(),
  getChannelPipelineGroups: vi.fn(),
  generateAndFetchDebugBundle: vi.fn(),
}));

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
  useAuth: () => ({ user: mockUser }),
}));

vi.mock('../settings/NormalizationEngineSection', () => ({
  NormalizationEngineSection: () => <div data-testid="stub-normalization" />,
}));
vi.mock('../settings/TagEngineSection', () => ({
  TagEngineSection: () => <div data-testid="stub-tag-engine" />,
}));
vi.mock('../settings/AuthSettingsSection', () => ({
  AuthSettingsSection: () => <div data-testid="stub-auth" />,
}));
vi.mock('../settings/UserManagementSection', () => ({
  UserManagementSection: () => <div data-testid="stub-users" />,
}));
vi.mock('../settings/LinkedAccountsSection', () => ({
  LinkedAccountsSection: () => <div data-testid="stub-linked-accounts" />,
}));
vi.mock('../settings/TLSSettingsSection', () => ({
  TLSSettingsSection: () => <div data-testid="stub-tls" />,
}));
vi.mock('../settings/BackupRestoreSection', () => ({
  BackupRestoreSection: () => <div data-testid="stub-backup" />,
}));
vi.mock('../settings/MCPSettingsSection', () => ({
  MCPSettingsSection: () => <div data-testid="stub-mcp" />,
}));
vi.mock('../ScheduledTasksSection', () => ({
  ScheduledTasksSection: () => <div data-testid="stub-scheduled-tasks" />,
}));
vi.mock('../SettingsModal', () => ({
  SettingsModal: () => <div data-testid="stub-settings-modal" />,
}));
vi.mock('../DeleteOrphanedGroupsModal', () => ({
  DeleteOrphanedGroupsModal: () => <div data-testid="stub-delete-orphaned" />,
}));
vi.mock('../CustomSelect', () => ({
  CustomSelect: ({ value, onChange, options }: {
    value: string;
    onChange: (v: string) => void;
    options: { value: string; label: string }[];
  }) => (
    <select value={value} onChange={(e) => onChange(e.target.value)}>
      {options.map((o) => (
        <option key={o.value} value={o.value}>{o.label}</option>
      ))}
    </select>
  ),
}));

import * as api from '../../services/api';
import { SettingsTab } from '../tabs/SettingsTab';

function makeSettings(overrides: Partial<typeof settingsBase> = {}): Awaited<ReturnType<typeof api.getSettings>> {
  return { ...settingsBase, ...overrides } as Awaited<ReturnType<typeof api.getSettings>>;
}

const settingsBase = {
  configured: true,
  url: 'http://dispatcharr.test',
  auth_method: 'password' as const,
  username: 'admin',
  dispatcharr_api_key_configured: false,
  api_key_configured: false,
  theme: 'dark' as const,
  date_format: 'auto',
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
  gracenote_conflict_mode: 'ask' as const,
  default_channel_profile_ids: [],
  linked_m3u_accounts: [],
  allow_multi_provider_auto_sync: false,
  epg_auto_match_threshold: 80,
  custom_network_prefixes: [],
  custom_network_suffixes: [],
  stats_poll_interval: 10,
  user_timezone: '',
  backend_log_level: 'INFO',
  frontend_log_level: 'INFO',
  vlc_open_behavior: 'm3u_fallback' as const,
  stream_preview_mode: 'passthrough' as const,
  auto_creation_excluded_terms: [],
  auto_creation_excluded_groups: [],
  auto_creation_exclude_auto_sync_groups: false,
  max_auto_created_channels_per_run: 500,
  max_auto_creation_log_entries: 500,
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
  stream_sort_priority: ['resolution', 'bitrate', 'framerate'] as api.SortCriterion[],
  stream_sort_enabled: { resolution: true, bitrate: true, framerate: true, video_codec: false, m3u_priority: false, audio_channels: false, custom_streams: false } as api.SortEnabledMap,
  m3u_account_priorities: {},
  black_screen_detection_enabled: false,
  black_screen_sample_duration: 5,
  low_fps_threshold: 20,
  deprioritize_failed_streams: true,
  deprioritize_black_screen: true,
  deprioritize_low_fps: true,
  failed_stream_sort_order: ['failed', 'black_screen', 'low_fps'] as api.FailedStreamCategory[],
  strike_threshold: 3,
  normalize_on_channel_create: false,
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
  dedup_threshold: 0.80,
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
  ssrf_outbound_mode: 'lan_friendly' as const,
};

function renderOnGeneral() {
  return render(
    <SettingsTab
      onSaved={vi.fn()}
      initialSettingsPage="appearance"
    />
  );
}

describe('Multi-provider auto-sync toggle (bd-dgs64, GH #591)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUser = { is_admin: true, username: 'admin' };
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings());
    vi.mocked(api.saveSettings).mockResolvedValue({ status: 'ok', configured: true, server_changed: false });
    vi.mocked(api.getChannelProfiles).mockResolvedValue([]);
    vi.mocked(api.listAlertMethods).mockResolvedValue([]);
    vi.mocked(api.getM3UAccounts).mockResolvedValue([]);
  });

  it('renders checked when loaded settings have it enabled', async () => {
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings({ allow_multi_provider_auto_sync: true }));
    renderOnGeneral();

    // The checkbox is unconditionally rendered from the component's
    // `useState(false)` default, so `findByLabelText` resolves as soon as the
    // element exists in the DOM -- which can happen before the mocked
    // `getSettings()` promise has resolved and re-rendered it as checked.
    // Under CI's heavier scheduling (coverage instrumentation, loaded
    // runner) that ordering isn't guaranteed, so read `.checked` inside
    // `waitFor` and let it re-poll until the loaded value has actually
    // landed, instead of asserting on a single synchronous read.
    const checkbox = await screen.findByLabelText(/Allow multi-provider auto-sync/i) as HTMLInputElement;
    await waitFor(() => expect(checkbox.checked).toBe(true));
  });

  it('renders unchecked when loaded settings have it disabled (default)', async () => {
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings({ allow_multi_provider_auto_sync: false }));
    renderOnGeneral();

    const checkbox = await screen.findByLabelText(/Allow multi-provider auto-sync/i) as HTMLInputElement;
    expect(checkbox.checked).toBe(false);
  });

  it('warns that enabling it can create duplicate channels', async () => {
    renderOnGeneral();

    await screen.findByLabelText(/Allow multi-provider auto-sync/i);
    expect(screen.getByText(/duplicate channels/i)).toBeInTheDocument();
  });

  it('lets an admin enable it and sends the new value on save', async () => {
    renderOnGeneral();

    const checkbox = await screen.findByLabelText(/Allow multi-provider auto-sync/i) as HTMLInputElement;
    expect(checkbox.disabled).toBe(false);
    fireEvent.click(checkbox);

    fireEvent.click(screen.getByRole('button', { name: /Save Settings/i }));

    await waitFor(() => expect(api.saveSettings).toHaveBeenCalled());
    const payload = vi.mocked(api.saveSettings).mock.calls[0][0];
    expect(payload.allow_multi_provider_auto_sync).toBe(true);
  });

  it('disables the control for a non-admin (backend gate would 403 a change)', async () => {
    mockUser = { is_admin: false, username: 'viewer' };
    renderOnGeneral();

    const checkbox = await screen.findByLabelText(/Allow multi-provider auto-sync/i) as HTMLInputElement;
    expect(checkbox.disabled).toBe(true);
  });
});
