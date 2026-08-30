/**
 * Tests for the M3U Digest "Account Filter" picker (GH #496 / bead
 * enhancedchannelmanager-wwovg).
 *
 * Operators running a high-churn "FAST" M3U provider (10k+ stream URL
 * changes/hour) want digest NOTIFICATIONS scoped to a subset of M3U
 * accounts — DB change logging stays complete regardless. This exercises
 * the account multi-select rendering (via the shared
 * GroupMultiSelectDropdown, populated from listM3UAccounts) and the save
 * payload carrying `account_ids`.
 *
 * Follows the isolated-render pattern established by
 * ../settings/DeduplicationSettingsSection.test.tsx: mock the api module
 * and heavyweight nested sections, render SettingsTab directly on the
 * `m3u-digest` page.
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
  getM3UAccounts: vi.fn(),
  getExportSections: vi.fn(),
  listSavedBackups: vi.fn(),
  getStreams: vi.fn(),
  getProbeHistory: vi.fn(),
  getProbeProgress: vi.fn(),
  getM3UDigestSettings: vi.fn(),
  updateM3UDigestSettings: vi.fn(),
  sendTestM3UDigest: vi.fn(),
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

// Stub sub-components that pull in DnD context or heavy deps -- not exercised
// by the m3u-digest page but imported at SettingsTab module scope.
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
import type { M3UAccount, M3UDigestSettings } from '../../types';
import { SettingsTab } from './SettingsTab';

// Minimal settings fixture -- only the fields loadSettings() actually reads
// off the top-level api.getSettings() response.
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

const mockAccounts: M3UAccount[] = [
  { id: 1, name: 'Standard Provider' } as unknown as M3UAccount,
  { id: 2, name: 'FAST Churn Provider' } as unknown as M3UAccount,
  { id: 3, name: 'Backup Provider' } as unknown as M3UAccount,
];

function makeDigestSettings(overrides: Partial<M3UDigestSettings> = {}): M3UDigestSettings {
  return {
    id: 1,
    enabled: true,
    frequency: 'daily',
    email_recipients: ['ops@example.com'],
    include_group_changes: true,
    include_stream_changes: true,
    show_detailed_list: true,
    min_changes_threshold: 1,
    send_to_discord: false,
    exclude_group_patterns: [],
    exclude_stream_patterns: [],
    account_ids: [],
    last_digest_at: null,
    created_at: '2026-07-18T00:00:00Z',
    updated_at: '2026-07-18T00:00:00Z',
    ...overrides,
  };
}

function renderOnM3UDigest() {
  return render(
    <SettingsTab
      onSaved={vi.fn()}
      initialSettingsPage="m3u-digest"
    />
  );
}

describe('SettingsTab M3U Digest — account filter (GH #496)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings());
    vi.mocked(api.saveSettings).mockResolvedValue({ status: 'ok', configured: true, server_changed: false });
    vi.mocked(api.getChannelProfiles).mockResolvedValue([]);
    vi.mocked(api.listAlertMethods).mockResolvedValue([]);
    vi.mocked(api.getM3UAccounts).mockResolvedValue(mockAccounts);
    vi.mocked(api.getStreams).mockResolvedValue({ count: 0, next: null, previous: null, results: [] });
    vi.mocked(api.getProbeHistory).mockResolvedValue([]);
    vi.mocked(api.getProbeProgress).mockResolvedValue({
      in_progress: false, total: 0, current: 0, status: 'idle', current_stream: '',
      success_count: 0, failed_count: 0, skipped_count: 0, black_screen_count: 0,
      low_fps_count: 0, percentage: 0,
    });
    vi.mocked(api.getM3UDigestSettings).mockResolvedValue(makeDigestSettings());
    vi.mocked(api.updateM3UDigestSettings).mockImplementation(async (data) => makeDigestSettings(data));
  });

  it('renders the account multi-select populated with the real M3U accounts', async () => {
    renderOnM3UDigest();

    const picker = await screen.findByRole('button', { name: 'M3U Accounts' });
    expect(picker).toHaveTextContent('All accounts');

    fireEvent.click(picker);

    // Options render inside a portal, so query the document body directly.
    await waitFor(() => {
      expect(screen.getByText('Standard Provider')).toBeInTheDocument();
    });
    expect(screen.getByText('FAST Churn Provider')).toBeInTheDocument();
    expect(screen.getByText('Backup Provider')).toBeInTheDocument();
  });

  it('shows "N selected" once a subset of accounts is chosen', async () => {
    renderOnM3UDigest();

    const picker = await screen.findByRole('button', { name: 'M3U Accounts' });
    fireEvent.click(picker);

    const option = await screen.findByText('Standard Provider');
    fireEvent.click(option);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'M3U Accounts' })).toHaveTextContent('1 account selected');
    });
  });

  it('reflects a previously-saved account selection on load', async () => {
    vi.mocked(api.getM3UDigestSettings).mockResolvedValue(makeDigestSettings({ account_ids: [1, 3] }));
    renderOnM3UDigest();

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'M3U Accounts' })).toHaveTextContent('2 accounts selected');
    });
  });

  it('sends the selected account_ids in the save payload', async () => {
    renderOnM3UDigest();

    const picker = await screen.findByRole('button', { name: 'M3U Accounts' });
    fireEvent.click(picker);

    const option = await screen.findByText('FAST Churn Provider');
    fireEvent.click(option);

    const saveBtn = screen.getByRole('button', { name: /save digest settings/i });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(api.updateM3UDigestSettings).toHaveBeenCalledWith(
        expect.objectContaining({ account_ids: [2] })
      );
    });
  });

  it('saves account_ids: [] (all accounts) when nothing is selected', async () => {
    renderOnM3UDigest();

    const saveBtn = await screen.findByRole('button', { name: /save digest settings/i });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(api.updateM3UDigestSettings).toHaveBeenCalledWith(
        expect.objectContaining({ account_ids: [] })
      );
    });
  });

  it('includes account_ids in the "send test digest" payload', async () => {
    vi.mocked(api.sendTestM3UDigest).mockResolvedValue({ success: true, message: 'sent' });
    renderOnM3UDigest();

    const picker = await screen.findByRole('button', { name: 'M3U Accounts' });
    fireEvent.click(picker);
    const option = await screen.findByText('Backup Provider');
    fireEvent.click(option);

    const testBtn = screen.getByRole('button', { name: /send test digest/i });
    fireEvent.click(testBtn);

    await waitFor(() => {
      expect(api.updateM3UDigestSettings).toHaveBeenCalledWith(
        expect.objectContaining({ account_ids: [3] })
      );
    });
    expect(api.sendTestM3UDigest).toHaveBeenCalled();
  });
});
