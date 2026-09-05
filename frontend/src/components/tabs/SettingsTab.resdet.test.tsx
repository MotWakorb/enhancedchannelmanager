import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../services/api';
import { SettingsTab } from './SettingsTab';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/api')>();
  return {
    ...actual,
    getSettings: vi.fn(),
    saveSettings: vi.fn(),
    getChannelProfiles: vi.fn(),
    listAlertMethods: vi.fn(),
    getM3UAccounts: vi.fn(),
    getStreams: vi.fn(),
    getProbeHistory: vi.fn(),
    getProbeProgress: vi.fn(),
  };
});

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

type SettingsResponse = Awaited<ReturnType<typeof api.getSettings>>;

const makeSettings = (useResdet: boolean): SettingsResponse => ({
  url: 'http://dispatcharr:8000',
  username: 'admin',
  use_resdet_for_resolution: useResdet,
  stream_sort_priority: ['resolution'],
  stream_sort_enabled: { resolution: true },
} as SettingsResponse);

describe('resdet resolution setting (6cyl3 / GH #618)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.saveSettings).mockResolvedValue({
      status: 'ok', configured: true, server_changed: false,
    });
    vi.mocked(api.getChannelProfiles).mockResolvedValue([]);
    vi.mocked(api.listAlertMethods).mockResolvedValue([]);
    vi.mocked(api.getM3UAccounts).mockResolvedValue([]);
    vi.mocked(api.getStreams).mockResolvedValue({
      count: 0, next: null, previous: null, results: [],
    });
    vi.mocked(api.getProbeHistory).mockResolvedValue([]);
    vi.mocked(api.getProbeProgress).mockResolvedValue({
      in_progress: false,
      total: 0,
      current: 0,
      status: 'idle',
      current_stream: '',
      success_count: 0,
      failed_count: 0,
      skipped_count: 0,
      black_screen_count: 0,
      low_fps_count: 0,
      percentage: 0,
    });
  });

  it('defaults absent settings to ffprobe resolution detection', async () => {
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings(undefined as unknown as boolean));
    render(<SettingsTab onSaved={vi.fn()} initialSettingsPage="maintenance" />);

    expect(await screen.findByRole('checkbox', { name: /Use resdet for resolution detection/i })).not.toBeChecked();
  });

  it('loads, toggles, and saves the resdet option', async () => {
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings(true));
    render(<SettingsTab onSaved={vi.fn()} initialSettingsPage="maintenance" />);

    const checkbox = await screen.findByRole('checkbox', { name: /Use resdet for resolution detection/i });
    expect(checkbox).toBeChecked();
    fireEvent.click(checkbox);
    fireEvent.click(screen.getByRole('button', { name: /Save Settings/i }));

    await waitFor(() => expect(api.saveSettings).toHaveBeenCalled());
    expect(vi.mocked(api.saveSettings).mock.calls[0][0].use_resdet_for_resolution).toBe(false);
  });
});
