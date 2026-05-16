/**
 * Tests for the Stream Deduplication settings controls (BD-K / bd-ugzn4).
 *
 * The controls live directly in SettingsTab.tsx (Channel Defaults → Stream
 * Deduplication section). This test file exercises the dedup-specific
 * rendering, load, save, and clamping behaviour without full SettingsTab
 * integration — it mocks the api module and renders SettingsTab in isolation.
 *
 * ADR-008 §D2: dedup_threshold stored as float 0.60-1.00; UI displays and
 * edits as integer percent 60-100. Hard floor 60 (server enforces; UI
 * clamps as convenience).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

// SettingsTab has many nested imports — mock the heavyweight ones so the
// unit test stays fast and does not hit missing module boundaries.
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

// Stub sub-components that pull in DnD context or heavy deps
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

// Minimal settings fixture — only the fields SettingsTab actually reads.
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
  // Dedup fields under test (BD-K / bd-ugzn4)
  dedup_threshold: 0.80,
  dedup_m3u_toast_suppressed: false,
};

function renderOnChannelDefaults() {
  return render(
    <SettingsTab
      onSaved={vi.fn()}
      initialSettingsPage="channel-defaults"
    />
  );
}

describe('DeduplicationSettingsSection (BD-K / bd-ugzn4)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings());
    vi.mocked(api.saveSettings).mockResolvedValue({ status: 'ok', configured: true, server_changed: false });
    vi.mocked(api.getChannelProfiles).mockResolvedValue([]);
    vi.mocked(api.listAlertMethods).mockResolvedValue([]);
    vi.mocked(api.getM3UAccounts).mockResolvedValue([]);
  });

  // --- Rendering ---

  it('renders the dedup threshold input with the operator\'s current value', async () => {
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings({ dedup_threshold: 0.75 }));
    renderOnChannelDefaults();

    await waitFor(() => {
      const input = screen.getByTestId('dedup-threshold-input') as HTMLInputElement;
      expect(input.value).toBe('75');
    });
  });

  it('renders the dedup threshold input with default 80 when field is absent from settings', async () => {
    const withoutDedup = { ...settingsBase } as Partial<typeof settingsBase>;
    delete (withoutDedup as Record<string, unknown>)['dedup_threshold'];
    vi.mocked(api.getSettings).mockResolvedValue(withoutDedup as Awaited<ReturnType<typeof api.getSettings>>);

    renderOnChannelDefaults();

    await waitFor(() => {
      const input = screen.getByTestId('dedup-threshold-input') as HTMLInputElement;
      expect(input.value).toBe('80');
    });
  });

  it('renders the toast-suppressor checkbox reflecting the operator\'s current value (false)', async () => {
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings({ dedup_m3u_toast_suppressed: false }));
    renderOnChannelDefaults();

    await waitFor(() => {
      const checkbox = screen.getByTestId('dedup-toast-suppressed-checkbox') as HTMLInputElement;
      expect(checkbox.checked).toBe(false);
    });
  });

  it('renders the toast-suppressor checkbox as checked when operator has suppressed the toast', async () => {
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings({ dedup_m3u_toast_suppressed: true }));
    renderOnChannelDefaults();

    await waitFor(() => {
      const checkbox = screen.getByTestId('dedup-toast-suppressed-checkbox') as HTMLInputElement;
      expect(checkbox.checked).toBe(true);
    });
  });

  it('toast suppressor starts unchecked by default (field absent)', async () => {
    const withoutToast = { ...settingsBase } as Partial<typeof settingsBase>;
    delete (withoutToast as Record<string, unknown>)['dedup_m3u_toast_suppressed'];
    vi.mocked(api.getSettings).mockResolvedValue(withoutToast as Awaited<ReturnType<typeof api.getSettings>>);

    renderOnChannelDefaults();

    await waitFor(() => {
      const checkbox = screen.getByTestId('dedup-toast-suppressed-checkbox') as HTMLInputElement;
      expect(checkbox.checked).toBe(false);
    });
  });

  // --- Save: threshold ---

  it('POSTs the new dedup_threshold as a float when the operator edits and saves', async () => {
    renderOnChannelDefaults();

    // Wait for the settings to load
    await waitFor(() => {
      expect(screen.getByTestId('dedup-threshold-input')).toBeInTheDocument();
    });

    // Change the threshold from 80 to 90
    const input = screen.getByTestId('dedup-threshold-input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '90' } });

    // Click the Save button
    const saveBtn = screen.getByRole('button', { name: /save settings/i });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(api.saveSettings).toHaveBeenCalledWith(
        expect.objectContaining({ dedup_threshold: 0.90 })
      );
    });
  });

  // --- Save: toast suppressor ---

  it('POSTs the toggled dedup_m3u_toast_suppressed value when operator checks and saves', async () => {
    renderOnChannelDefaults();

    await waitFor(() => {
      expect(screen.getByTestId('dedup-toast-suppressed-checkbox')).toBeInTheDocument();
    });

    const checkbox = screen.getByTestId('dedup-toast-suppressed-checkbox') as HTMLInputElement;
    fireEvent.click(checkbox); // toggle from false → true

    const saveBtn = screen.getByRole('button', { name: /save settings/i });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(api.saveSettings).toHaveBeenCalledWith(
        expect.objectContaining({ dedup_m3u_toast_suppressed: true })
      );
    });
  });

  // --- Clamping ---

  it('clamps threshold to 60 when operator enters a value below the floor', async () => {
    renderOnChannelDefaults();

    await waitFor(() => {
      expect(screen.getByTestId('dedup-threshold-input')).toBeInTheDocument();
    });

    const input = screen.getByTestId('dedup-threshold-input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '30' } });

    // Value should be clamped to 60 immediately on change
    expect(input.value).toBe('60');
  });

  it('clamps threshold to 100 when operator enters a value above the ceiling', async () => {
    renderOnChannelDefaults();

    await waitFor(() => {
      expect(screen.getByTestId('dedup-threshold-input')).toBeInTheDocument();
    });

    const input = screen.getByTestId('dedup-threshold-input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '150' } });

    expect(input.value).toBe('100');
  });

  it('saves threshold clamped to 0.60 when operator somehow bypasses UI validation', async () => {
    renderOnChannelDefaults();

    await waitFor(() => {
      expect(screen.getByTestId('dedup-threshold-input')).toBeInTheDocument();
    });

    // Simulate a value below floor reaching the onChange handler (e.g. direct prop manipulation)
    const input = screen.getByTestId('dedup-threshold-input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '10' } });
    // After clamp, value is 60 → save sends 0.60
    const saveBtn = screen.getByRole('button', { name: /save settings/i });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(api.saveSettings).toHaveBeenCalledWith(
        expect.objectContaining({ dedup_threshold: 0.60 })
      );
    });
  });

  // --- Hint text ---

  it('shows the ADR-008 hard floor hint text below the threshold input', async () => {
    renderOnChannelDefaults();

    await waitFor(() => {
      expect(screen.getByText(/ADR-008 hard floor/i)).toBeInTheDocument();
    });
  });

  it('shows the hint that pending merges are still queued when toast is suppressed', async () => {
    renderOnChannelDefaults();

    await waitFor(() => {
      expect(screen.getByText(/Pending merges are still queued/i)).toBeInTheDocument();
    });
  });
});
