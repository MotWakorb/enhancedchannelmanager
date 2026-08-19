/**
 * bead enhancedchannelmanager-9kwzp.10 item 4 — the Settings tab must not call
 * the alert-method routes it is no longer allowed to call.
 *
 * GET/POST/PATCH /api/alert-methods became human-admin, because the response
 * carries every method's `config` in clear (Discord webhook URL, Telegram bot
 * token, SMTP password). The first cut of that change guarded
 * AlertMethodsSection and MISSED this tab, which calls the same routes from
 * two other places:
 *
 *   - `loadSettings` reads GET /api/alert-methods to resolve the SMTP method's
 *     id and its `to_emails`, on EVERY Settings load;
 *   - `handleSaveSmtpRecipients` writes it back through PATCH (or POST for the
 *     first save).
 *
 * So every non-admin opening Settings fired a guaranteed 403, and the "Email
 * alert recipients" input and its Save button stayed enabled, presenting a
 * control that could only fail. These cases pin both halves.
 *
 * THE THIRD CASE IS THE ONE THAT IS EASY TO GET WRONG. `useAuth` leaves `user`
 * null on an auth-disabled or setup-incomplete instance, where the backend
 * gate no-ops and the call would SUCCEED. A `user?.is_admin ?? false` guard
 * reads that as "not admin" and locks a single-operator install out of its own
 * settings, so the predicate is `!user || user.is_admin` — the same one
 * BackupRestoreSection is given.
 *
 * Follows the isolated-render pattern of ./SettingsTab.notificationRedaction.test.tsx.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('../../services/api', () => ({
  getSettings: vi.fn(),
  saveSettings: vi.fn(),
  getChannelProfiles: vi.fn(),
  generateMCPApiKey: vi.fn(),
  revokeMCPApiKey: vi.fn(),
  getMCPStatus: vi.fn(),
  listAlertMethods: vi.fn(),
  createAlertMethod: vi.fn(),
  updateAlertMethod: vi.fn(),
  resetStats: vi.fn(),
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

// Mutable so each case can pick its principal. `null` is the AUTH-DISABLED
// posture, not "unknown": useAuth leaves `user` null when the backend
// reports require_auth=false or setup_complete=false, and every gate this
// bead added no-ops in exactly that state.
let mockUser: { is_admin: boolean; username: string } | null = { is_admin: true, username: 'admin' };

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
// Surfaces the prop so the wiring is asserted, not assumed.
vi.mock('../settings/AlertMethodsSection', () => ({
  AlertMethodsSection: ({ isAdmin }: { isAdmin: boolean }) => (
    <div data-testid="stub-alert-methods" data-is-admin={String(isAdmin)} />
  ),
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

// Telegram issues `<bot id>:<exactly 35 characters>`. The 35 characters here
// are just the alphabet followed by digits, and the value is assembled from
// parts so no token-shaped literal exists on any single line. Same convention
// as `docs/pytest_conventions.md`: keep the real SHAPE, look like
// nothing.

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
const RECIPIENTS_LABEL = /Email alert recipients/i;
const SAVE_RECIPIENTS = /Save Recipients/i;

function recipientsInput(): HTMLInputElement {
  return screen.getByLabelText(RECIPIENTS_LABEL) as HTMLInputElement;
}

function saveRecipientsButton(): HTMLButtonElement {
  return screen.getByRole('button', { name: SAVE_RECIPIENTS }) as HTMLButtonElement;
}

const SMTP_METHOD = {
  id: 7,
  name: 'Email',
  method_type: 'smtp',
  enabled: true,
  config: { to_emails: 'ops@example.com' },
  notify_info: false,
  notify_success: true,
  notify_warning: true,
  notify_error: true,
  alert_sources: null,
  last_sent_at: null,
  created_at: null,
};

describe('SettingsTab alert-method callers (bead 9kwzp.10 item 4)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUser = { is_admin: true, username: 'admin' };
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings());
    vi.mocked(api.saveSettings).mockResolvedValue({ status: 'ok', configured: true, server_changed: false });
    vi.mocked(api.getChannelProfiles).mockResolvedValue([]);
    vi.mocked(api.listAlertMethods).mockResolvedValue([SMTP_METHOD] as unknown as Awaited<ReturnType<typeof api.listAlertMethods>>);
    vi.mocked(api.getM3UAccounts).mockResolvedValue([]);
    vi.mocked(api.getStreams).mockResolvedValue({ count: 0, next: null, previous: null, results: [] });
  });

  it('does not read the gated route for a non-admin', async () => {
    mockUser = { is_admin: false, username: 'viewer' };
    renderOnNotifications();

    await screen.findByLabelText(RECIPIENTS_LABEL);
    // Give any in-flight load a chance to fire before asserting a negative.
    await waitFor(() => expect(api.getSettings).toHaveBeenCalled());
    expect(api.listAlertMethods).not.toHaveBeenCalled();
  });

  it('presents the recipients control as read-only for a non-admin', async () => {
    mockUser = { is_admin: false, username: 'viewer' };
    renderOnNotifications();

    const input = await screen.findByLabelText(RECIPIENTS_LABEL) as HTMLInputElement;
    await waitFor(() => expect(input.disabled).toBe(true));
    expect(saveRecipientsButton().disabled).toBe(true);
    expect(
      screen.getByText(/Only an administrator can view or change alert recipients\./),
    ).toBeInTheDocument();
  });

  it('never writes the gated route for a non-admin, even if the save is invoked', async () => {
    mockUser = { is_admin: false, username: 'viewer' };
    renderOnNotifications();

    await screen.findByLabelText(RECIPIENTS_LABEL);
    // The button is disabled, so this click is a no-op in a real browser. The
    // point is the handler's own guard: a stale render or a programmatic call
    // must not reach a request that is guaranteed to 403.
    fireEvent.click(saveRecipientsButton());

    await waitFor(() => expect(api.getSettings).toHaveBeenCalled());
    expect(api.updateAlertMethod).not.toHaveBeenCalled();
    expect(api.createAlertMethod).not.toHaveBeenCalled();
  });

  it('tells AlertMethodsSection the caller is not an admin', async () => {
    mockUser = { is_admin: false, username: 'viewer' };
    renderOnNotifications();

    const stub = await screen.findByTestId('stub-alert-methods');
    expect(stub.getAttribute('data-is-admin')).toBe('false');
  });

  it('still reads and enables the control for an admin', async () => {
    renderOnNotifications();

    await waitFor(() => expect(api.listAlertMethods).toHaveBeenCalled());
    const input = recipientsInput();
    await waitFor(() => expect(input.value).toBe('ops@example.com'));
    expect(input.disabled).toBe(false);
    expect(saveRecipientsButton().disabled).toBe(false);
    expect(screen.getByTestId('stub-alert-methods').getAttribute('data-is-admin')).toBe('true');
  });

  it('treats a null user as permitted, because that is the auth-disabled instance', async () => {
    // Regression guard for the `user?.is_admin ?? false` form: on an install
    // running with authentication off there is no user object at all, every
    // gate this bead added no-ops server-side, and the operator must keep
    // full access to their own settings.
    mockUser = null;
    renderOnNotifications();

    await waitFor(() => expect(api.listAlertMethods).toHaveBeenCalled());
    expect(recipientsInput().disabled).toBe(false);
    expect(saveRecipientsButton().disabled).toBe(false);
    expect(screen.getByTestId('stub-alert-methods').getAttribute('data-is-admin')).toBe('true');
  });
});
