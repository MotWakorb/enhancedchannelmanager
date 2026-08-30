/**
 * Tests for the M3U Digest load-failure lifecycle (bead
 * enhancedchannelmanager-fi3dq).
 *
 * When GET /api/settings/m3u-digest (or the accounts fetch in the same
 * batch) fails, the page previously re-issued the request batch in a
 * tight loop -- the activation effect re-fired every time digestLoading
 * flipped back to false with digestSettings still null -- and pushed one
 * error toast per failed attempt. These tests pin the fixed contract:
 *
 *   - one failed page activation issues exactly ONE request batch;
 *   - the page settles into a stable inline error state (role=alert)
 *     with a Retry button instead of an eternal spinner;
 *   - Retry issues exactly one new request batch and recovers to the
 *     settings form on success (or back to the error state on failure);
 *   - the load failure produces no error toast at all -- the inline
 *     alert is the error surface, so there is nothing to storm.
 *
 * Layer: component wiring (SettingsTab rendered directly on the
 * m3u-digest page with the api module mocked). Rendered-browser
 * verification of the same path is part of the bead's ship evidence.
 *
 * Follows the isolated-render pattern of ./SettingsTab.digest.test.tsx.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, render, screen, fireEvent, waitFor } from '@testing-library/react';

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

// Stable spies so the tests can assert on toast traffic.
const notifySpies = {
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
  notify: vi.fn().mockReturnValue('toast-id'),
  dismiss: vi.fn(),
};

vi.mock('../../contexts/NotificationContext', () => ({
  useNotifications: () => notifySpies,
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

function makeSettings(): Awaited<ReturnType<typeof api.getSettings>> {
  return { ...settingsBase } as Awaited<ReturnType<typeof api.getSettings>>;
}

const mockAccounts: M3UAccount[] = [
  { id: 1, name: 'Standard Provider' } as unknown as M3UAccount,
];

function makeDigestSettings(): M3UDigestSettings {
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

/** Let any wrongly-scheduled follow-up request batches fire before counting. */
async function settle(ms = 75) {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, ms));
  });
}

describe('SettingsTab M3U Digest — load-failure lifecycle (fi3dq)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings());
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
  });

  it('issues exactly one request batch per failed activation and settles into an inline error state', async () => {
    vi.mocked(api.getM3UDigestSettings).mockRejectedValue(new Error('digest backend unavailable'));

    renderOnM3UDigest();

    // The page must terminate in a visible, stable error state...
    const alert = await screen.findByTestId('digest-load-error');
    expect(alert).toHaveTextContent(/digest backend unavailable/);
    expect(alert).toHaveAttribute('role', 'alert');

    // ...not an eternal spinner.
    expect(screen.queryByText(/Loading digest settings/)).not.toBeInTheDocument();

    // One failed activation = one request batch. Give a broken retry loop
    // time to reveal itself before counting.
    await settle();
    expect(api.getM3UDigestSettings).toHaveBeenCalledTimes(1);
  });

  it('never fires an error toast for the digest load failure (inline alert is the surface)', async () => {
    vi.mocked(api.getM3UDigestSettings).mockRejectedValue(new Error('digest backend unavailable'));

    renderOnM3UDigest();

    await screen.findByTestId('digest-load-error');
    await settle();

    expect(notifySpies.error).not.toHaveBeenCalled();
    expect(notifySpies.notify).not.toHaveBeenCalled();
  });

  it('Retry issues exactly one new request batch and recovers to the settings form', async () => {
    vi.mocked(api.getM3UDigestSettings)
      .mockRejectedValueOnce(new Error('digest backend unavailable'))
      .mockResolvedValue(makeDigestSettings());

    renderOnM3UDigest();

    const retry = await screen.findByRole('button', { name: /retry loading digest settings/i });
    fireEvent.click(retry);

    // Recovered: the form renders and the error state is gone.
    await screen.findByLabelText('Enable M3U digest emails');
    expect(screen.queryByTestId('digest-load-error')).not.toBeInTheDocument();

    await settle();
    expect(api.getM3UDigestSettings).toHaveBeenCalledTimes(2);
  });

  it('a Retry that fails again returns to the error state without extra request batches', async () => {
    vi.mocked(api.getM3UDigestSettings).mockRejectedValue(new Error('digest backend unavailable'));

    renderOnM3UDigest();

    const retry = await screen.findByRole('button', { name: /retry loading digest settings/i });
    fireEvent.click(retry);

    await waitFor(() => {
      expect(screen.getByTestId('digest-load-error')).toBeInTheDocument();
    });

    await settle();
    expect(api.getM3UDigestSettings).toHaveBeenCalledTimes(2);
    expect(notifySpies.error).not.toHaveBeenCalled();
  });
});
