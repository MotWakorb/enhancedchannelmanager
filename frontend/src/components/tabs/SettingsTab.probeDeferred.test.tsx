import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../services/api';
import { SettingsTab } from './SettingsTab';

vi.mock('../../services/api', async (importOriginal) => ({
  ...await importOriginal<typeof import('../../services/api')>(),
  getSettings: vi.fn(), getChannelProfiles: vi.fn(), listAlertMethods: vi.fn(),
  getM3UAccounts: vi.fn(), getStreams: vi.fn(), getProbeHistory: vi.fn(), getProbeProgress: vi.fn(),
  getStaleStreams: vi.fn(), probeBulkStreams: vi.fn(),
}));
const notifications = vi.hoisted(() => ({
  success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn(),
  notify: vi.fn(), dismiss: vi.fn(),
}));
vi.mock('../../contexts/NotificationContext', () => ({ useNotifications: () => notifications }));
vi.mock('../../hooks/useAuth', () => ({ useAuth: () => ({ user: { is_admin: true, username: 'admin' } }) }));

const reason = 'Probe deferred: provider capacity is limited or unverified; shared viewer coordination is unavailable';
const progress = {
  in_progress: false, total: 1, current: 1, status: 'deferred', current_stream: '',
  success_count: 0, failed_count: 0, skipped_count: 1, black_screen_count: 0,
  low_fps_count: 0, percentage: 100, deferred_reason: reason,
};
const history: api.ProbeHistoryEntry = {
  timestamp: '2026-11-08T00:00:00Z', end_timestamp: '2026-11-08T00:00:01Z', duration_seconds: 1,
  total: 1, status: 'deferred', deferred_reason: reason,
  success_count: 0, failed_count: 0, skipped_count: 1, black_screen_count: 0, low_fps_count: 0,
  success_streams: [], failed_streams: [], black_screen_streams: [], low_fps_streams: [],
  skipped_streams: [{ id: 7, name: 'Limited provider', reason }],
};

describe('fail-closed probe feedback', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getSettings).mockResolvedValue({ url: 'http://dispatcharr:8000', username: 'admin' } as Awaited<ReturnType<typeof api.getSettings>>);
    vi.mocked(api.getChannelProfiles).mockResolvedValue([]);
    vi.mocked(api.listAlertMethods).mockResolvedValue([]);
    vi.mocked(api.getM3UAccounts).mockResolvedValue([]);
    vi.mocked(api.getStreams).mockResolvedValue({ count: 0, next: null, previous: null, results: [] });
    vi.mocked(api.getProbeHistory).mockResolvedValue([history]);
    vi.mocked(api.getProbeProgress).mockResolvedValue(progress);
  });
  afterEach(() => { vi.useRealTimers(); });

  it('shows the deferred history reason and preserves skipped details', async () => {
    render(<SettingsTab onSaved={vi.fn()} initialSettingsPage="maintenance" />);
    expect(await screen.findByText(reason)).toBeVisible();
    fireEvent.click(screen.getByTitle('View skipped streams'));
    expect(await screen.findByText('Limited provider')).toBeVisible();
    expect(screen.getAllByText(reason)).toHaveLength(2);
    expect(notifications.success).not.toHaveBeenCalled();
  });

  it.each([reason, undefined])('warns instead of reporting success when polling finishes deferred (%s)', async (deferredReason) => {
    vi.mocked(api.getProbeHistory).mockResolvedValue([]);
    vi.mocked(api.getProbeProgress)
      .mockResolvedValueOnce({ ...progress, in_progress: true, status: 'probing' })
      .mockResolvedValue({ ...progress, deferred_reason: deferredReason });
    render(<SettingsTab onSaved={vi.fn()} initialSettingsPage="maintenance" />);
    await waitFor(() => expect(api.getProbeProgress).toHaveBeenCalled());
    vi.useFakeTimers();
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(notifications.warning).toHaveBeenCalledWith(reason, 'Stream Probe Deferred');
    expect(notifications.success).not.toHaveBeenCalled();
    expect(screen.getByRole('status', { name: 'Stream probe deferred' })).toHaveTextContent(reason);
    expect(screen.queryByText(/Probing streams\.\.\./)).not.toBeInTheDocument();
  });

  it('does not report a deferred stale-stream bulk probe as finished probing', async () => {
    vi.mocked(api.getProbeHistory).mockResolvedValue([]);
    vi.mocked(api.getStaleStreams).mockResolvedValue({
      streams: [{ stream_id: 7, stream_name: 'Limited provider', last_probed: null,
        provider_last_seen: null, reasons: ['not_probed_recently'], channels: [] }],
      threshold_days: 7,
    });
    vi.mocked(api.probeBulkStreams).mockResolvedValue({ status: 'started', message: 'Started' } as unknown as Awaited<ReturnType<typeof api.probeBulkStreams>>);
    render(<SettingsTab onSaved={vi.fn()} initialSettingsPage="maintenance" />);
    fireEvent.click(await screen.findByRole('button', { name: /Scan for Stale Streams/ }));
    fireEvent.click(await screen.findByRole('checkbox', { name: /Limited provider/ }));
    vi.useFakeTimers();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Probe 1 Stream/ })); });
    expect(api.probeBulkStreams).toHaveBeenCalledWith([7]);
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(notifications.warning).toHaveBeenCalledWith(reason, 'Stream Probe Deferred');
    expect(notifications.success).not.toHaveBeenCalled();
  });
});
