/**
 * Tests for the Jellyfin Integration Settings subsection (bd-r5f0c.5 / W5).
 *
 * Mirrors EmbyIntegrationSection.test.tsx.
 *
 * Contracts under test:
 *   - Section renders with the three fields (enabled, base_url, api_key).
 *   - Fields populate from loaded settings (key itself is masked).
 *   - "Test Connection" button calls api.testJellyfinConnection with
 *     form-state values and renders ok/error inline.
 *   - Save persists jellyfin_enabled and jellyfin_base_url always, and
 *     jellyfin_api_key only when the operator entered a fresh value
 *     (preserve-on-omit).
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
  testEmbyConnection: vi.fn(),
  testPlexConnection: vi.fn(),
  testJellyfinConnection: vi.fn(),
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
  stream_sort_priority: ['resolution', 'bitrate', 'framerate'] as api.SortCriterion[],
  stream_sort_enabled: { resolution: true, bitrate: true, framerate: true, video_codec: false, m3u_priority: false, audio_channels: false } as api.SortEnabledMap,
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
  // Jellyfin fields under test (bd-r5f0c.5 / W5)
  jellyfin_enabled: false,
  jellyfin_base_url: '',
  jellyfin_api_key_configured: false,
};

function renderOnIntegrations() {
  return render(
    <SettingsTab
      onSaved={vi.fn()}
      initialSettingsPage="integrations"
    />
  );
}

describe('JellyfinIntegrationSection (bd-r5f0c.5 / W5)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings());
    vi.mocked(api.saveSettings).mockResolvedValue({ status: 'ok', configured: true, server_changed: false });
    vi.mocked(api.getChannelProfiles).mockResolvedValue([]);
    vi.mocked(api.listAlertMethods).mockResolvedValue([]);
    vi.mocked(api.getM3UAccounts).mockResolvedValue([]);
  });

  // --- Rendering ---

  it('renders the Jellyfin Integration section on the Integrations page', async () => {
    renderOnIntegrations();
    await waitFor(() => {
      expect(screen.getByTestId('jellyfin-integration-section')).toBeInTheDocument();
    });
    expect(screen.getByTestId('jellyfin-enabled-checkbox')).toBeInTheDocument();
    expect(screen.getByTestId('jellyfin-base-url-input')).toBeInTheDocument();
    expect(screen.getByTestId('jellyfin-api-key-input')).toBeInTheDocument();
    expect(screen.getByTestId('jellyfin-test-connection-btn')).toBeInTheDocument();
  });

  it('populates form fields from loaded settings', async () => {
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings({
      jellyfin_enabled: true,
      jellyfin_base_url: 'http://jellyfin.local:8096',
      jellyfin_api_key_configured: true,
    }));

    renderOnIntegrations();

    await waitFor(() => {
      const enabled = screen.getByTestId('jellyfin-enabled-checkbox') as HTMLInputElement;
      const baseUrl = screen.getByTestId('jellyfin-base-url-input') as HTMLInputElement;
      const apiKey = screen.getByTestId('jellyfin-api-key-input') as HTMLInputElement;
      expect(enabled.checked).toBe(true);
      expect(baseUrl.value).toBe('http://jellyfin.local:8096');
      expect(apiKey.value).toBe('');
      expect(apiKey.placeholder).toBe('••••••••');
    });
  });

  it('uses a password-type input for the API key field', async () => {
    renderOnIntegrations();
    await waitFor(() => {
      const apiKey = screen.getByTestId('jellyfin-api-key-input') as HTMLInputElement;
      expect(apiKey.type).toBe('password');
    });
  });

  // --- Test Connection ---

  it('calls api.testJellyfinConnection with form-state values when Test is clicked', async () => {
    vi.mocked(api.testJellyfinConnection).mockResolvedValue({ ok: true });
    renderOnIntegrations();

    await waitFor(() => {
      expect(screen.getByTestId('jellyfin-base-url-input')).toBeInTheDocument();
    });

    fireEvent.change(screen.getByTestId('jellyfin-base-url-input'), {
      target: { value: 'http://jellyfin.local:8096' },
    });
    fireEvent.change(screen.getByTestId('jellyfin-api-key-input'), {
      target: { value: 'fresh-jf-key' },
    });

    fireEvent.click(screen.getByTestId('jellyfin-test-connection-btn'));

    await waitFor(() => {
      expect(api.testJellyfinConnection).toHaveBeenCalledWith(
        'http://jellyfin.local:8096',
        'fresh-jf-key',
      );
    });
  });

  it('shows a success message inline when the test succeeds', async () => {
    vi.mocked(api.testJellyfinConnection).mockResolvedValue({ ok: true });
    renderOnIntegrations();

    await waitFor(() => {
      expect(screen.getByTestId('jellyfin-base-url-input')).toBeInTheDocument();
    });

    fireEvent.change(screen.getByTestId('jellyfin-base-url-input'), {
      target: { value: 'http://jellyfin.local:8096' },
    });
    fireEvent.change(screen.getByTestId('jellyfin-api-key-input'), {
      target: { value: 'jf-key' },
    });
    fireEvent.click(screen.getByTestId('jellyfin-test-connection-btn'));

    await waitFor(() => {
      expect(screen.getByTestId('jellyfin-test-result-success')).toBeInTheDocument();
    });
  });

  it('shows the backend error message inline when the test fails', async () => {
    vi.mocked(api.testJellyfinConnection).mockResolvedValue({
      ok: false,
      error: 'Jellyfin /Sessions returned 401 unauthorized — check API key',
    });
    renderOnIntegrations();

    await waitFor(() => {
      expect(screen.getByTestId('jellyfin-base-url-input')).toBeInTheDocument();
    });

    fireEvent.change(screen.getByTestId('jellyfin-base-url-input'), {
      target: { value: 'http://jellyfin.local:8096' },
    });
    fireEvent.change(screen.getByTestId('jellyfin-api-key-input'), {
      target: { value: 'bad-key' },
    });
    fireEvent.click(screen.getByTestId('jellyfin-test-connection-btn'));

    await waitFor(() => {
      const errEl = screen.getByTestId('jellyfin-test-result-error');
      expect(errEl).toBeInTheDocument();
      expect(errEl.textContent).toContain('401');
    });
  });

  it('rejects the test click with an inline error when base URL is empty', async () => {
    renderOnIntegrations();

    await waitFor(() => {
      expect(screen.getByTestId('jellyfin-test-connection-btn')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId('jellyfin-test-connection-btn'));

    await waitFor(() => {
      const err = screen.getByTestId('jellyfin-test-result-error');
      expect(err.textContent?.toLowerCase()).toContain('base url');
    });
    expect(api.testJellyfinConnection).not.toHaveBeenCalled();
  });

  // --- Save ---

  it('saves jellyfin_enabled and jellyfin_base_url on save', async () => {
    renderOnIntegrations();

    await waitFor(() => {
      expect(screen.getByTestId('jellyfin-enabled-checkbox')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId('jellyfin-enabled-checkbox'));
    fireEvent.change(screen.getByTestId('jellyfin-base-url-input'), {
      target: { value: 'http://jellyfin.local:8096' },
    });
    fireEvent.change(screen.getByTestId('jellyfin-api-key-input'), {
      target: { value: 'fresh-jf-key' },
    });

    const saveBtn = screen.getByRole('button', { name: /save settings/i });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(api.saveSettings).toHaveBeenCalledWith(
        expect.objectContaining({
          jellyfin_enabled: true,
          jellyfin_base_url: 'http://jellyfin.local:8096',
          jellyfin_api_key: 'fresh-jf-key',
        }),
      );
    });
  });

  it('omits jellyfin_api_key from the save payload when the field is blank (preserve-on-omit)', async () => {
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings({
      jellyfin_enabled: true,
      jellyfin_base_url: 'http://old-jellyfin:8096',
      jellyfin_api_key_configured: true,
    }));

    renderOnIntegrations();

    await waitFor(() => {
      expect(screen.getByTestId('jellyfin-base-url-input')).toBeInTheDocument();
    });

    fireEvent.change(screen.getByTestId('jellyfin-base-url-input'), {
      target: { value: 'http://new-jellyfin:8096' },
    });

    fireEvent.click(screen.getByRole('button', { name: /save settings/i }));

    await waitFor(() => {
      expect(api.saveSettings).toHaveBeenCalled();
    });

    const callArgs = vi.mocked(api.saveSettings).mock.calls[0][0];
    expect(callArgs.jellyfin_base_url).toBe('http://new-jellyfin:8096');
    expect(callArgs).not.toHaveProperty('jellyfin_api_key');
  });
});
