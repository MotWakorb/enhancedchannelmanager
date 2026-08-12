/**
 * bead 9ej7f — the Notification Settings page must survive a REDACTED read of
 * the shared Discord webhook and Telegram bot token.
 *
 * GET /api/settings now withholds `discord_webhook_url`, `telegram_bot_token`
 * and `telegram_chat_id` from a caller that is not allowed to write them (an
 * ordinary non-admin, or the static MCP service key), returning "" alongside
 * the `discord_configured` / `telegram_configured` booleans that say the
 * integration IS set up. The backend keeps the stored value on such a caller's
 * save, so the credential cannot be wiped by the round trip.
 *
 * The frontend half of that contract is the badge: `handleSave` used to
 * recompute "Configured" straight from the form field, so a non-admin saving
 * an unrelated preference watched a live integration report itself
 * Unconfigured. These tests pin both directions.
 *
 * Follows the isolated-render pattern of ./SettingsTab.digest.test.tsx.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';

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
    notify: vi.fn().mockReturnValue('toast-id'),
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

const WEBHOOK = 'https://discord.com/api/webhooks/1/abcdefghijklmnop';
const BOT_TOKEN = '123456789:AAEabcdefghijklmnopqrstuvwxyz012345';
const CHAT_ID = '-1001234567890';

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
  smtp_host: 'smtp.test',
  smtp_port: 587,
  smtp_user: '',
  smtp_from_email: 'ecm@test.local',
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

function renderOnNotifications() {
  return render(<SettingsTab onSaved={vi.fn()} initialSettingsPage="email" />);
}

function badgeFor(heading: string): HTMLElement {
  const header = screen.getByRole('heading', { name: heading }).parentElement!;
  return within(header).getByText(/Configured|Unconfigured/);
}

async function saveSettingsPage() {
  fireEvent.click(screen.getByRole('button', { name: /Save Settings/i }));
  await waitFor(() => expect(api.saveSettings).toHaveBeenCalled());
}

describe('SettingsTab notification credentials — redacted read (bead 9ej7f)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.saveSettings).mockResolvedValue({ status: 'ok', configured: true, server_changed: false });
    vi.mocked(api.getChannelProfiles).mockResolvedValue([]);
    vi.mocked(api.listAlertMethods).mockResolvedValue([]);
    vi.mocked(api.getM3UAccounts).mockResolvedValue([]);
    vi.mocked(api.getStreams).mockResolvedValue({ count: 0, next: null, previous: null, results: [] });
  });

  it('renders empty credential fields when the server redacted them', async () => {
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings({
      discord_configured: true,
      discord_webhook_url: '',
      telegram_configured: true,
      telegram_bot_token: '',
      telegram_chat_id: '',
    }));

    renderOnNotifications();

    await waitFor(() => expect(api.getSettings).toHaveBeenCalled());
    await waitFor(() => {
      expect(screen.getByLabelText('Webhook URL')).toHaveValue('');
    });
    expect(screen.getByLabelText('Bot Token')).toHaveValue('');
    expect(screen.getByLabelText('Chat ID')).toHaveValue('');
  });

  it('keeps the Configured badges after a save when the values were redacted', async () => {
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings({
      discord_configured: true,
      discord_webhook_url: '',
      telegram_configured: true,
      telegram_bot_token: '',
      telegram_chat_id: '',
    }));

    renderOnNotifications();
    await waitFor(() => expect(badgeFor('Discord Webhook')).toHaveTextContent('Configured'));

    await saveSettingsPage();

    await waitFor(() => expect(badgeFor('Discord Webhook')).toHaveTextContent('Configured'));
    expect(badgeFor('Telegram Bot')).toHaveTextContent('Configured');
  });

  it('still clears the badge when a caller that CAN see the value blanks it', async () => {
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings({
      discord_configured: true,
      discord_webhook_url: WEBHOOK,
      telegram_configured: true,
      telegram_bot_token: BOT_TOKEN,
      telegram_chat_id: CHAT_ID,
    }));

    renderOnNotifications();
    await waitFor(() => expect(screen.getByLabelText('Webhook URL')).toHaveValue(WEBHOOK));

    fireEvent.change(screen.getByLabelText('Webhook URL'), { target: { value: '' } });
    await saveSettingsPage();

    await waitFor(() => expect(badgeFor('Discord Webhook')).toHaveTextContent('Unconfigured'));
  });

  it('sends the untouched (empty) values back so the backend can preserve them', async () => {
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings({
      discord_configured: true,
      discord_webhook_url: '',
      telegram_configured: true,
      telegram_bot_token: '',
      telegram_chat_id: '',
    }));

    renderOnNotifications();
    await waitFor(() => expect(api.getSettings).toHaveBeenCalled());

    await saveSettingsPage();

    const payload = vi.mocked(api.saveSettings).mock.calls[0][0];
    expect(payload.discord_webhook_url).toBe('');
    expect(payload.telegram_bot_token).toBe('');
    expect(payload.telegram_chat_id).toBe('');
  });
});
