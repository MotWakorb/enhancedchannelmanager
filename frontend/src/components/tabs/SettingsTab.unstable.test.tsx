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

const notifications = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
  notify: vi.fn().mockReturnValue('toast-id'),
  dismiss: vi.fn(),
}));

vi.mock('../../contexts/NotificationContext', () => ({
  useNotifications: () => notifications,
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

describe('Unstable playback settings', () => {
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

  it('loads and saves the playback reconnect threshold', async () => {
    vi.mocked(api.getSettings).mockResolvedValue({ ...makeSettings(false), playback_reconnect_threshold: 9 });
    render(<SettingsTab onSaved={vi.fn()} initialSettingsPage="maintenance" />);
    const threshold = await screen.findByRole('spinbutton', { name: /Playback reconnect threshold/i });
    expect(threshold).toHaveValue(9);
    fireEvent.change(threshold, { target: { value: '12' } });
    fireEvent.click(screen.getByRole('button', { name: /Save Settings/i }));
    await waitFor(() => expect(api.saveSettings).toHaveBeenCalled());
    expect(vi.mocked(api.saveSettings).mock.calls[0][0].playback_reconnect_threshold).toBe(12);
  });

  it('loads and saves the playback start-failure threshold', async () => {
    vi.mocked(api.getSettings).mockResolvedValue({ ...makeSettings(false), playback_start_failure_threshold: 3 });
    render(<SettingsTab onSaved={vi.fn()} initialSettingsPage="maintenance" />);
    const threshold = await screen.findByRole('spinbutton', { name: /Playback start-failure threshold/i });
    expect(threshold).toHaveValue(3);
    fireEvent.change(threshold, { target: { value: '5' } });
    fireEvent.click(screen.getByRole('button', { name: /Save Settings/i }));
    await waitFor(() => expect(api.saveSettings).toHaveBeenCalled());
    expect(vi.mocked(api.saveSettings).mock.calls[0][0].playback_start_failure_threshold).toBe(5);
  });

  it('defaults unstable deprioritization off and appends the category to an old order', async () => {
    vi.mocked(api.getSettings).mockResolvedValue({ ...makeSettings(false), deprioritize_failed_streams: true,
      failed_stream_sort_order: ['low_fps', 'failed', 'black_screen', 'low_bitrate'] });
    render(<SettingsTab onSaved={vi.fn()} initialSettingsPage="channel-defaults" />);
    const toggle = await screen.findByRole('checkbox', { name: /Deprioritize Unstable Streams/i });
    expect(toggle).not.toBeChecked();
    fireEvent.click(toggle);
    fireEvent.click(screen.getByRole('button', { name: /Save Settings/i }));
    await waitFor(() => expect(api.saveSettings).toHaveBeenCalled());
    const payload = vi.mocked(api.saveSettings).mock.calls[0][0];
    expect(payload.deprioritize_unstable).toBe(true);
    expect(payload.failed_stream_sort_order).toEqual(['low_fps', 'failed', 'black_screen', 'low_bitrate', 'unstable']);
  });

  it('rejects a non-positive threshold before saving', async () => {
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings(false));
    render(<SettingsTab onSaved={vi.fn()} initialSettingsPage="maintenance" />);
    const threshold = await screen.findByRole('spinbutton', { name: /Playback reconnect threshold/i });
    fireEvent.change(threshold, { target: { value: '0' } });
    fireEvent.click(screen.getByRole('button', { name: /Save Settings/i }));
    await waitFor(() => expect(notifications.error).toHaveBeenCalledWith('Playback reconnect threshold must be positive and finite'));
    expect(api.saveSettings).not.toHaveBeenCalled();
  });

  it('rejects a start-failure threshold below 1 before saving', async () => {
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings(false));
    render(<SettingsTab onSaved={vi.fn()} initialSettingsPage="maintenance" />);
    const threshold = await screen.findByRole('spinbutton', { name: /Playback start-failure threshold/i });
    fireEvent.change(threshold, { target: { value: '0' } });
    fireEvent.click(screen.getByRole('button', { name: /Save Settings/i }));
    await waitFor(() => expect(notifications.error).toHaveBeenCalledWith('Playback start-failure threshold must be a whole number of at least 1'));
    expect(api.saveSettings).not.toHaveBeenCalled();
  });
});
