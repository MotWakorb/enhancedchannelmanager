/**
 * bead qsqfv - the Public Base URL field on Settings > Email.
 *
 * The backend now builds the emailed password-reset link from
 * `public_base_url` when it is set, and only falls back to the
 * caller-controlled `X-Forwarded-Host` / `Host` headers when it is not. That
 * makes the field the operator's only way to close a P1 account-takeover
 * vector, so it has to be reachable and round-trip correctly.
 *
 * These tests pin the UI half: the stored value loads into the field, an
 * operator's edit reaches the save payload trimmed, and the badge tells them
 * which of the two modes their install is in.
 *
 * Scaffolding follows ./SettingsTab.notificationRedaction.test.tsx.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';

const notificationMocks = vi.hoisted(() => ({
  notify: vi.fn().mockReturnValue('toast-id'),
}));

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
  getStreams: vi.fn(),
  getProbeHistory: vi.fn(),
  getProbeProgress: vi.fn(),
  getM3UDigestSettings: vi.fn(),
  updateM3UDigestSettings: vi.fn(),
  sendTestM3UDigest: vi.fn(),
  testSmtpConnection: vi.fn(),
  testDiscordWebhook: vi.fn(),
  testTelegramBot: vi.fn(),
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
    notify: notificationMocks.notify,
    dismiss: vi.fn(),
  }),
}));

vi.mock('../../hooks/useAuth', () => ({
  useAuth: () => ({ user: { is_admin: true, username: 'admin' } }),
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
vi.mock('../settings/AlertMethodsSection', () => ({
  AlertMethodsSection: () => <div data-testid="stub-alert-methods" />,
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
vi.mock('../ModalOverlay', () => ({
  ModalOverlay: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock('../CustomSelect', () => ({
  CustomSelect: ({ value, onChange, options }: {
    value: string;
    onChange: (v: string) => void;
    options: { value: string; label: string }[];
  }) => (
    <select value={value} onChange={(e) => onChange(e.target.value)}>
      {options.map((o: { value: string; label: string }) => (
        <option key={o.value} value={o.value}>{o.label}</option>
      ))}
    </select>
  ),
}));

import * as api from '../../services/api';
import { SettingsTab } from './SettingsTab';


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
  smtp_configured: true,
  public_base_url: '',
  smtp_host: 'smtp.test',
  smtp_port: 587,
  smtp_user: '',
  smtp_from_email: 'ecm@example.com',
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

function makeSettings(overrides: Partial<typeof settingsBase> = {}): Awaited<ReturnType<typeof api.getSettings>> {
  return { ...settingsBase, ...overrides } as Awaited<ReturnType<typeof api.getSettings>>;
}

function renderEmailPage() {
  return render(<SettingsTab onSaved={vi.fn()} initialSettingsPage="email" />);
}

/** The badge next to the heading, which reads Configured or Not set. */
function publicBaseUrlBadge(): HTMLElement {
  const header = screen.getByRole('heading', { name: 'Public Base URL' }).parentElement!;
  return within(header).getByText(/Configured|Not set/);
}

async function saveSettingsPage() {
  fireEvent.click(screen.getByRole('button', { name: /Save Settings/i }));
  await waitFor(() => expect(api.saveSettings).toHaveBeenCalled());
}

describe('SettingsTab public base URL (bead qsqfv)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.saveSettings).mockResolvedValue({ status: 'ok', configured: true, server_changed: false });
    vi.mocked(api.getChannelProfiles).mockResolvedValue([]);
    vi.mocked(api.listAlertMethods).mockResolvedValue([]);
    vi.mocked(api.getM3UAccounts).mockResolvedValue([]);
    vi.mocked(api.getStreams).mockResolvedValue({ count: 0, next: null, previous: null, results: [] });
  });

  it('loads the stored value and reports it as configured', async () => {
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings({
      public_base_url: 'https://ecm.example.com',
    }));

    renderEmailPage();

    await waitFor(() => {
      expect(screen.getByLabelText('Public Base URL')).toHaveValue('https://ecm.example.com');
    });
    expect(publicBaseUrlBadge()).toHaveTextContent('Configured');
  });

  it('shows Not set when the install is still on header-derived links', async () => {
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings({ public_base_url: '' }));

    renderEmailPage();

    await waitFor(() => expect(api.getSettings).toHaveBeenCalled());
    expect(publicBaseUrlBadge()).toHaveTextContent('Not set');
  });

  it('sends the edited value, trimmed, in the save payload', async () => {
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings({ public_base_url: '' }));

    renderEmailPage();
    await waitFor(() => expect(screen.getByLabelText('Public Base URL')).toHaveValue(''));

    fireEvent.change(screen.getByLabelText('Public Base URL'), {
      target: { value: '  https://ecm.example.com  ' },
    });
    await saveSettingsPage();

    const payload = vi.mocked(api.saveSettings).mock.calls[0][0];
    expect(payload.public_base_url).toBe('https://ecm.example.com');
  });

  it('round-trips an untouched stored value instead of dropping the field', async () => {
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings({
      public_base_url: 'https://ecm.example.com',
    }));

    renderEmailPage();
    await waitFor(() => expect(screen.getByLabelText('Public Base URL')).toHaveValue('https://ecm.example.com'));

    await saveSettingsPage();

    const payload = vi.mocked(api.saveSettings).mock.calls[0][0];
    expect(payload.public_base_url).toBe('https://ecm.example.com');
  });

  it('shows the restart action when the backend reports a pending log policy', async () => {
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings());
    vi.mocked(api.saveSettings).mockResolvedValue({
      status: 'ok',
      configured: true,
      server_changed: false,
      restart_required: true,
    });

    renderEmailPage();
    await waitFor(() => expect(api.getSettings).toHaveBeenCalled());
    await saveSettingsPage();

    expect(notificationMocks.notify).toHaveBeenCalledWith(
      expect.objectContaining({
        title: 'Restart Required',
        message: 'Persistent logging settings changed. Restart services to apply.',
      }),
    );
  });
});
