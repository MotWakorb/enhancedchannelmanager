/**
 * The Dispatcharr Connection summary card must show the connection that was
 * just saved (bead enhancedchannelmanager-5z7c9, instance 1 — drill run
 * 2026-08-06-run9 finding P-4).
 *
 * WHAT WENT WRONG. `SettingsModal` is rendered in TWO places: by
 * `SettingsTab` behind its own Edit button, and by `App`, which opens it
 * unprompted on a first run (`if (!settings.configured) setSettingsOpen(true)`).
 * On a fresh install the modal an operator fills in on Settings → General is
 * App's, not the tab's — so the tab's `onSaved` handler never ran, the tab's
 * copy of the settings stayed at its mount-time values, and the card behind
 * the modal still read "Server URL: Not configured / Auth Method: Username &
 * Password" after a save that `GET /api/settings` already reflected. An
 * operator reasonably concludes the save failed and does it again.
 *
 * The test therefore renders the tab and an App-owned modal as siblings —
 * the exact arrangement of the first run — and asserts on the rendered card.
 * A test that opened the tab's own modal would pass against the bug.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('../../services/api', () => ({
  getSettings: vi.fn(),
  saveSettings: vi.fn(),
  testConnection: vi.fn(),
  getStreams: vi.fn(),
  getProbeHistory: vi.fn(),
  getProbeProgress: vi.fn(),
  getM3UAccounts: vi.fn(),
  getChannelProfiles: vi.fn(),
  listAlertMethods: vi.fn(),
  getExportSections: vi.fn(),
  listSavedBackups: vi.fn(),
  getM3UDigestSettings: vi.fn(),
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

// Sections pulled in at SettingsTab module scope that the General page does
// not render — stubbed so this file only exercises the connection card.
vi.mock('../settings/NormalizationEngineSection', () => ({
  NormalizationEngineSection: () => null,
}));
vi.mock('../settings/TagEngineSection', () => ({ TagEngineSection: () => null }));
vi.mock('../settings/UserManagementSection', () => ({ UserManagementSection: () => null }));
vi.mock('../settings/LinkedAccountsSection', () => ({ LinkedAccountsSection: () => null }));
vi.mock('../settings/TLSSettingsSection', () => ({ TLSSettingsSection: () => null }));
vi.mock('../settings/MCPSettingsSection', () => ({ MCPSettingsSection: () => null }));
vi.mock('../settings/AuthSettingsSection', () => ({ AuthSettingsSection: () => null }));
vi.mock('../settings/BackupRestoreSection', () => ({ BackupRestoreSection: () => null }));
vi.mock('../ScheduledTasksSection', () => ({ ScheduledTasksSection: () => null }));
vi.mock('../DeleteOrphanedGroupsModal', () => ({ DeleteOrphanedGroupsModal: () => null }));

import * as api from '../../services/api';
import { SettingsTab } from './SettingsTab';
import { SettingsModal } from '../SettingsModal';

const DISPATCHARR_URL = 'http://bkr-dispatcharr:9191';

const BASE_SETTINGS = {
  configured: false,
  url: '',
  username: '',
  auth_method: 'password',
  dispatcharr_api_key_configured: false,
  theme: 'dark',
  auto_rename_channel_number: false,
  include_channel_number_in_name: false,
  channel_number_separator: '-',
  remove_country_prefix: false,
  include_country_in_name: false,
  country_separator: '|',
  timezone_preference: 'both',
  show_stream_urls: true,
  hide_auto_sync_groups: false,
  hide_ungrouped_streams: false,
  default_channel_profile_ids: [],
  stream_sort_priority: ['resolution'],
  stream_sort_enabled: { resolution: true },
  frontend_log_level: 'INFO',
};

const SAVED_SETTINGS = {
  ...BASE_SETTINGS,
  configured: true,
  url: DISPATCHARR_URL,
  auth_method: 'api_key',
  dispatcharr_api_key_configured: true,
};

/** The rendered value of one row of the connection summary card. */
function connectionValue(label: string): string {
  const row = screen.getByText(label).closest('.connection-info-row') as HTMLElement;
  return row.querySelector('.connection-value')?.textContent ?? '';
}

describe('Dispatcharr Connection card freshness (bead 5z7c9 instance 1)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Element.prototype.scrollTo = vi.fn();
    Element.prototype.scrollIntoView = vi.fn();

    // A settings store that answers with what was last written, so the card
    // can only go stale for the reason under test.
    // Only the connection fields matter here; the rest of SettingsResponse is
    // not read by the card under test, hence the narrow literal.
    let stored = { ...BASE_SETTINGS } as unknown as api.SettingsResponse;
    vi.mocked(api.getSettings).mockImplementation(() => Promise.resolve(stored));
    vi.mocked(api.saveSettings).mockImplementation(() => {
      stored = { ...SAVED_SETTINGS } as unknown as api.SettingsResponse;
      return Promise.resolve({ status: 'ok', configured: true, server_changed: true });
    });
    vi.mocked(api.testConnection).mockResolvedValue({ success: true, message: 'Connected' });
    vi.mocked(api.getStreams).mockResolvedValue({ count: 0, next: null, previous: null, results: [] });
    vi.mocked(api.getM3UAccounts).mockResolvedValue([]);
    vi.mocked(api.getProbeHistory).mockResolvedValue([]);
    vi.mocked(api.getProbeProgress).mockResolvedValue({
      in_progress: false, total: 0, current: 0, status: 'idle', current_stream: '',
      success_count: 0, failed_count: 0, skipped_count: 0, black_screen_count: 0,
      low_fps_count: 0, percentage: 0,
    });
    vi.mocked(api.getChannelProfiles).mockResolvedValue([]);
    vi.mocked(api.listAlertMethods).mockResolvedValue([]);
  });

  /** Fills the API-key form in the open modal and saves it. */
  async function saveApiKeyConnection() {
    fireEvent.change(screen.getByLabelText('Dispatcharr URL'), {
      target: { value: DISPATCHARR_URL },
    });
    fireEvent.click(screen.getByRole('tab', { name: 'API Key' }));
    fireEvent.change(screen.getByLabelText('API Key'), { target: { value: 'run9-api-key' } });

    fireEvent.click(screen.getByRole('button', { name: 'Test Connection' }));
    await screen.findByRole('button', { name: 'Connected' });

    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(api.saveSettings).toHaveBeenCalledTimes(1));
  }

  it('updates after a save made through the modal App opens on a first run', async () => {
    render(
      <>
        <SettingsTab onSaved={vi.fn()} initialSettingsPage="general" />
        {/* App's first-run modal — SettingsTab knows nothing about it. */}
        <SettingsModal isOpen onClose={vi.fn()} onSaved={vi.fn()} />
      </>,
    );

    await waitFor(() => expect(connectionValue('Server URL:')).toBe('Not configured'));
    expect(connectionValue('Auth Method:')).toBe('Username & Password');

    await saveApiKeyConnection();

    await waitFor(() => expect(connectionValue('Server URL:')).toBe(DISPATCHARR_URL));
    expect(connectionValue('Auth Method:')).toBe('API Key');
    expect(connectionValue('API Key:')).toBe('••••••••');
  });

  it('refetches the settings exactly once per save', async () => {
    render(
      <>
        <SettingsTab onSaved={vi.fn()} initialSettingsPage="general" />
        <SettingsModal isOpen onClose={vi.fn()} onSaved={vi.fn()} />
      </>,
    );

    await waitFor(() => expect(connectionValue('Server URL:')).toBe('Not configured'));
    const beforeSave = vi.mocked(api.getSettings).mock.calls.length;

    await saveApiKeyConnection();
    await waitFor(() => expect(connectionValue('Server URL:')).toBe(DISPATCHARR_URL));

    expect(vi.mocked(api.getSettings).mock.calls.length - beforeSave).toBe(1);
  });
});
