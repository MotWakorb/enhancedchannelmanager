/**
 * Tests for NotificationCenter probe pause/resume controls (bead vdrku).
 *
 * Contract: Pause/Resume buttons render for an active/paused probe
 * notification and call the real StreamProber-backed endpoints
 * (POST /api/stream-stats/probe/pause, /probe/resume — added alongside
 * this test; see backend/routers/stream_stats.py and the
 * TestPauseProbe/TestResumeProbe classes in
 * backend/tests/routers/test_stream_stats.py). Prior to this bead the
 * buttons called endpoints that did not exist on the backend router even
 * though `StreamProber.pause_probe`/`resume_probe` were already fully
 * implemented and honored by the probe loops (backend/stream_prober.py) —
 * git history shows no removal (`git log -S "probe/pause"` — only hit is
 * the frontend's original commit), so this was a missing 2-endpoint HTTP
 * wire-up, not a refactor regression, and trivially recoverable per the
 * bead's restore-vs-remove criterion. Cancel must keep working regardless
 * of probe status.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import type { Notification } from '../services/api';

vi.mock('../services/api', () => ({
  getNotifications: vi.fn(),
  cancelProbe: vi.fn(),
  pauseProbe: vi.fn(),
  resumeProbe: vi.fn(),
  markNotificationRead: vi.fn(),
  markAllNotificationsRead: vi.fn(),
  deleteNotification: vi.fn(),
  clearNotifications: vi.fn(),
  restartServices: vi.fn(),
}));

vi.mock('../contexts/NotificationContext', () => ({
  useNotifications: () => ({
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    warning: vi.fn(),
    notify: vi.fn(),
  }),
}));

import * as api from '../services/api';
import { NotificationCenter } from './NotificationCenter';

type Mock = ReturnType<typeof vi.fn>;

function probeNotification(status: string, overrides: Partial<Notification> = {}): Notification {
  return {
    id: 1,
    type: 'info',
    title: 'Probing streams',
    message: 'Probe in progress',
    read: false,
    source: 'stream_probe',
    source_id: null,
    action_label: null,
    action_url: null,
    metadata: {
      progress: {
        current: 5,
        total: 10,
        success: 3,
        failed: 1,
        skipped: 0,
        black_screen: 0,
        low_fps: 0,
        status,
        current_stream: 'ESPN',
      },
    },
    created_at: new Date().toISOString(),
    read_at: null,
    expires_at: null,
    ...overrides,
  };
}

async function openPanel() {
  render(<NotificationCenter />);
  // Wait for the initial mount-time loadNotifications() to settle before
  // interacting, so the click's own state update isn't racing it (avoids a
  // spurious "not wrapped in act" warning from the unrelated initial load).
  await waitFor(() => {
    expect(api.getNotifications).toHaveBeenCalled();
  });
  const bell = await screen.findByRole('button', { name: /Notifications/i });
  fireEvent.click(bell);
  await waitFor(() => {
    expect(screen.getByText('Notifications')).toBeInTheDocument();
  });
}

describe('NotificationCenter probe controls', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders a Pause button for an actively probing notification and calls the real pause endpoint', async () => {
    (api.getNotifications as Mock).mockResolvedValue({
      notifications: [probeNotification('probing')],
      unread_count: 1,
    });
    (api.pauseProbe as Mock).mockResolvedValue({ status: 'paused', message: 'Probe paused' });

    await openPanel();

    const pauseBtn = await screen.findByRole('button', { name: /Pause probe/i });
    fireEvent.click(pauseBtn);

    await waitFor(() => {
      expect(api.pauseProbe).toHaveBeenCalledTimes(1);
    });
    // No Resume button while status is 'probing'.
    expect(screen.queryByRole('button', { name: /Resume probe/i })).not.toBeInTheDocument();
  });

  it('renders a Resume button for a paused notification and calls the real resume endpoint', async () => {
    (api.getNotifications as Mock).mockResolvedValue({
      notifications: [probeNotification('paused')],
      unread_count: 1,
    });
    (api.resumeProbe as Mock).mockResolvedValue({ status: 'resumed', message: 'Probe resumed' });

    await openPanel();

    const resumeBtn = await screen.findByRole('button', { name: /Resume probe/i });
    fireEvent.click(resumeBtn);

    await waitFor(() => {
      expect(api.resumeProbe).toHaveBeenCalledTimes(1);
    });
    // No Pause button while already paused.
    expect(screen.queryByRole('button', { name: /Pause probe/i })).not.toBeInTheDocument();
  });

  it('Cancel still works for an active probe notification', async () => {
    (api.getNotifications as Mock).mockResolvedValue({
      notifications: [probeNotification('probing')],
      unread_count: 1,
    });
    (api.cancelProbe as Mock).mockResolvedValue({ status: 'cancelling', message: 'Probe cancellation requested' });

    await openPanel();

    const cancelBtn = await screen.findByRole('button', { name: /Cancel probe/i });
    fireEvent.click(cancelBtn);

    await waitFor(() => {
      expect(api.cancelProbe).toHaveBeenCalledTimes(1);
    });
  });

  it('renders no pause/resume/cancel controls for a completed probe notification', async () => {
    (api.getNotifications as Mock).mockResolvedValue({
      notifications: [probeNotification('completed')],
      unread_count: 1,
    });

    await openPanel();

    await screen.findByText('Probing streams');
    expect(screen.queryByRole('button', { name: /Pause probe/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Resume probe/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Cancel probe/i })).not.toBeInTheDocument();
  });
});
