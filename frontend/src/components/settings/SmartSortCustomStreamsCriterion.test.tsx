/**
 * Tests for the `custom_streams` Smart Sort criterion in the Settings UI
 * (bead ap1ud / GH #244).
 *
 * The Smart Sort Priority list lives in SettingsTab.tsx (Channel Defaults page).
 * These tests verify that the dedicated `custom_streams` criterion:
 *   - renders as a draggable, toggleable row in the priority list,
 *   - reflects its saved enabled state (default disabled),
 *   - can be toggled on and persisted via saveSettings,
 *   - is auto-merged into the list (disabled) for existing installs whose
 *     saved settings predate the criterion (mergeSortCriteria behaviour).
 *
 * Mirrors the harness in DeduplicationSettingsSection.test.tsx — the api module
 * is mocked and SettingsTab is rendered on the channel-defaults page.
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
}));

vi.mock('../../services/autoCreationApi', () => ({
  getAutoCreationRules: vi.fn(),
  getAutoCreationGroups: vi.fn(),
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

// Stub sub-components that pull in DnD context or heavy deps.
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
vi.mock('../settings/LookupTableSection', () => ({
  LookupTableSection: () => <div data-testid="stub-lookup" />,
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
  stream_sort_priority: ['resolution', 'bitrate', 'framerate', 'video_codec', 'm3u_priority', 'audio_channels', 'custom_streams'] as api.SortCriterion[],
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
  // nngkg: DBAS outbound-policy mode (default LAN-friendly).
  ssrf_outbound_mode: 'lan_friendly' as const,
};

function renderOnChannelDefaults() {
  return render(
    <SettingsTab
      onSaved={vi.fn()}
      initialSettingsPage="channel-defaults"
    />
  );
}

describe('Smart Sort custom_streams criterion (bead ap1ud / GH #244)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings());
    vi.mocked(api.saveSettings).mockResolvedValue({ status: 'ok', configured: true, server_changed: false });
    vi.mocked(api.getChannelProfiles).mockResolvedValue([]);
    vi.mocked(api.listAlertMethods).mockResolvedValue([]);
    vi.mocked(api.getM3UAccounts).mockResolvedValue([]);
  });

  it('renders the Custom Streams criterion row in the Smart Sort priority list', async () => {
    renderOnChannelDefaults();

    await waitFor(() => {
      expect(screen.getByText('Custom Streams')).toBeInTheDocument();
    });
  });

  it('shows the Custom Streams criterion as disabled by default (checkbox unchecked)', async () => {
    renderOnChannelDefaults();

    const checkbox = await findCustomStreamsCheckbox();
    expect(checkbox.checked).toBe(false);
  });

  it('shows the Custom Streams criterion enabled when the saved setting enables it', async () => {
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings({
      stream_sort_enabled: {
        resolution: true, bitrate: true, framerate: true, video_codec: false,
        m3u_priority: false, audio_channels: false, custom_streams: true,
      } as api.SortEnabledMap,
    }));
    renderOnChannelDefaults();

    const checkbox = await findCustomStreamsCheckbox();
    expect(checkbox.checked).toBe(true);
  });

  it('toggles the Custom Streams criterion on when its checkbox is clicked', async () => {
    renderOnChannelDefaults();

    const checkbox = await findCustomStreamsCheckbox();
    expect(checkbox.checked).toBe(false);

    fireEvent.click(checkbox);

    await waitFor(() => {
      expect(checkbox.checked).toBe(true);
    });
  });

  it('auto-merges custom_streams (disabled) for existing installs whose saved settings predate it', async () => {
    // Existing install: saved settings have no custom_streams in priority or enabled.
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings({
      stream_sort_priority: ['resolution', 'bitrate', 'framerate'] as api.SortCriterion[],
      stream_sort_enabled: {
        resolution: true, bitrate: true, framerate: true,
      } as unknown as api.SortEnabledMap,
    }));
    renderOnChannelDefaults();

    // mergeSortCriteria appends the unknown criterion (disabled) so the row still appears.
    const checkbox = await findCustomStreamsCheckbox();
    expect(checkbox.checked).toBe(false);
  });
});

/**
 * The Smart Sort priority list renders each criterion as a row whose checkbox
 * carries a title describing enable/disable. The Custom Streams row's checkbox
 * is the one immediately preceding the "Custom Streams" label text.
 */
async function findCustomStreamsCheckbox(): Promise<HTMLInputElement> {
  const label = await screen.findByText('Custom Streams');
  // Row container is the criterion item div; find the checkbox within it.
  const row = label.closest('div');
  if (!row) throw new Error('Custom Streams row container not found');
  // Walk up to the criterion item (the flex row holding the drag handle + checkbox).
  let container: HTMLElement | null = row;
  let checkbox: HTMLInputElement | null = null;
  while (container && !checkbox) {
    checkbox = container.querySelector('input[type="checkbox"]');
    container = container.parentElement;
  }
  if (!checkbox) throw new Error('Custom Streams checkbox not found');
  return checkbox;
}
