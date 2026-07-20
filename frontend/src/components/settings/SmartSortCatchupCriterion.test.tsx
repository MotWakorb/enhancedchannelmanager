import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SettingsTab } from '../tabs/SettingsTab';
import * as api from '../../services/api';

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/api')>();
  return {
    ...actual,
    getSettings: vi.fn(),
    saveSettings: vi.fn(),
    getChannelProfiles: vi.fn(),
    listAlertMethods: vi.fn(),
    getM3UAccounts: vi.fn(),
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

const makeSettings = (overrides: Partial<SettingsResponse> = {}): SettingsResponse => ({
  dispatcharr_url: '',
  dispatcharr_username: '',
  dispatcharr_password_configured: false,
  stream_sort_priority: [
    'resolution', 'bitrate', 'framerate', 'video_codec',
    'm3u_priority', 'audio_channels', 'custom_streams',
  ],
  stream_sort_enabled: {
    resolution: true,
    bitrate: true,
    framerate: true,
    video_codec: false,
    m3u_priority: false,
    audio_channels: false,
    custom_streams: false,
  },
  ...overrides,
} as SettingsResponse);

function renderOnChannelDefaults() {
  return render(<SettingsTab onSaved={vi.fn()} initialSettingsPage="channel-defaults" />);
}

async function findCatchupCheckbox(): Promise<HTMLInputElement> {
  const label = await screen.findByText('Catch-up');
  let container: HTMLElement | null = label.closest('div');
  while (container) {
    const checkbox = container.querySelector<HTMLInputElement>('input[type="checkbox"]');
    if (checkbox) return checkbox;
    container = container.parentElement;
  }
  throw new Error('Catch-up checkbox not found');
}

describe('Smart Sort catch-up criterion (enhancedchannelmanager-jnbka / GH #652)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings());
    vi.mocked(api.saveSettings).mockResolvedValue({
      status: 'ok', configured: true, server_changed: false,
    });
    vi.mocked(api.getChannelProfiles).mockResolvedValue([]);
    vi.mocked(api.listAlertMethods).mockResolvedValue([]);
    vi.mocked(api.getM3UAccounts).mockResolvedValue([]);
  });

  it('renders as the eighth criterion with the catch-up clock and requested subheading', async () => {
    renderOnChannelDefaults();

    await screen.findByText('Catch-up');
    expect(screen.getByText('Catch-up enabled')).toBeInTheDocument();
    const rows = screen.getAllByText(/Resolution|Bitrate|Framerate|Video Codec|M3U Priority|Audio Channels|Custom Streams|Catch-up/);
    expect(rows[rows.length - 1]).toHaveTextContent('Catch-up');
    expect(screen.getByText('history')).toBeInTheDocument();
  });

  it('auto-merges catchup disabled for saved settings that predate it', async () => {
    renderOnChannelDefaults();

    expect((await findCatchupCheckbox()).checked).toBe(false);
  });

  it('toggles the catch-up criterion on', async () => {
    renderOnChannelDefaults();
    const checkbox = await findCatchupCheckbox();

    fireEvent.click(checkbox);

    await waitFor(() => expect(checkbox.checked).toBe(true));
  });
});
