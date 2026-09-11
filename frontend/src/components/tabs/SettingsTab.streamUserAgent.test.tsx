import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { SettingsTab } from './SettingsTab';
import * as api from '../../services/api';

vi.mock('../../hooks/useAuth', () => ({ useAuth: () => ({ user: { is_admin: true } }) }));
vi.mock('../../contexts/NotificationContext', () => ({ useNotifications: () => ({
  success: vi.fn(), error: vi.fn(), notify: vi.fn(), dismiss: vi.fn(),
}) }));
vi.mock('../../services/api', async (importOriginal) => ({
  ...await importOriginal<typeof api>(),
  getSettings: vi.fn(async () => ({
    url: 'http://dispatcharr.example', username: 'admin',
    stream_user_agent: 'dispatcharr', stream_sort_priority: [], stream_sort_enabled: {},
    show_stream_urls: true, hide_auto_sync_groups: false, hide_ungrouped_streams: false,
    auto_rename_channel_number: false, include_channel_number_in_name: false,
    remove_country_prefix: false, include_country_in_name: false,
    channel_number_separator: ' - ', country_separator: ' | ', timezone_preference: 'UTC',
  })),
  getProbeHistory: vi.fn(async () => []),
  getProbeProgress: vi.fn(async () => ({ in_progress: false })),
  getM3UAccounts: vi.fn(async () => []),
  getChannelProfiles: vi.fn(async () => []),
  listAlertMethods: vi.fn(async () => []),
  saveSettings: vi.fn(async () => ({ status: 'ok' })),
}));

it('loads the shared default and saves an override through the existing Settings action', async () => {
  render(<SettingsTab onSaved={vi.fn()} initialSettingsPage="appearance" />);
  const selector = await screen.findByRole('button', { name: 'Stream User-Agent' });
  expect(selector).toHaveTextContent('Use Dispatcharr User-Agent (Default)');
  fireEvent.click(selector);
  expect(screen.getAllByRole('option').map(option => option.textContent?.replace('check', '').trim())).toEqual([
    'Use Dispatcharr User-Agent (Default)', 'Chrome', 'Firefox', 'Safari', 'VLC', 'TiviMate',
  ]);
  fireEvent.click(screen.getByRole('option', { name: 'TiviMate' }));
  fireEvent.click(screen.getByRole('button', { name: /Save Settings/i }));
  await waitFor(() => expect(api.saveSettings).toHaveBeenCalledWith(expect.objectContaining({ stream_user_agent: 'tivimate' })));
  expect(selector).toHaveTextContent('TiviMate');
});
