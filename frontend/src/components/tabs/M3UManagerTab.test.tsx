/**
 * Unit tests for M3UManagerTab additions:
 *   - "Server Groups" toolbar button opens ServerGroupsModal (bead hq3de.c).
 *   - Per-row "Refresh VOD" button, shown only for XtreamCodes (XC) accounts,
 *     calls POST /api/m3u/accounts/{id}/refresh-vod (bead hq3de.d).
 *   - "Stream Profiles" toolbar button opens StreamProfilesListModal (bead hq3de.j).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { M3UManagerTab } from './M3UManagerTab';
import { NotificationProvider } from '../../contexts/NotificationContext';
import type { M3UAccount } from '../../types';
import { HttpError } from '../../services/httpClient';

vi.mock('../../services/api');

// Stub ServerGroupsModal — it has its own test suite; assert only that
// M3UManagerTab opens it with a close/change handler wired.
vi.mock('../ServerGroupsModal', () => ({
  ServerGroupsModal: ({ onClose }: { onClose: () => void }) => (
    <div data-testid="server-groups-modal">
      <button onClick={onClose}>Close Server Groups</button>
    </div>
  ),
}));

// Stub StreamProfilesListModal — it has its own test suite; assert only
// that M3UManagerTab opens it with the streamProfiles prop + handlers wired.
vi.mock('../StreamProfilesListModal', () => ({
  StreamProfilesListModal: ({ streamProfiles, onClose, onChanged }: { streamProfiles: unknown[]; onClose: () => void; onChanged: () => void }) => (
    <div data-testid="stream-profiles-modal">
      {streamProfiles.length} profile(s)
      <button onClick={onClose}>Close Stream Profiles</button>
      <button onClick={onChanged}>Trigger Changed</button>
    </div>
  ),
}));

import * as api from '../../services/api';

const renderWithProviders = (ui: React.JSX.Element) =>
  render(<NotificationProvider>{ui}</NotificationProvider>);

function makeAccount(overrides: Partial<M3UAccount> = {}): M3UAccount {
  return {
    id: 1,
    name: 'Standard Playlist',
    server_url: 'http://example.com/playlist.m3u',
    file_path: null,
    server_group: null,
    max_streams: 5,
    is_active: true,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    user_agent: null,
    profiles: [],
    locked: false,
    channel_groups: [],
    refresh_interval: 24,
    custom_properties: null,
    account_type: 'STD',
    username: null,
    password: null,
    stale_stream_days: 0,
    priority: 0,
    status: 'success',
    last_message: null,
    enable_vod: false,
    auto_enable_new_groups_live: false,
    auto_enable_new_groups_vod: false,
    auto_enable_new_groups_series: false,
    ...overrides,
  };
}

describe('M3UManagerTab', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(api.getSettings).mockResolvedValue({} as never);
    // Default: no catch-up anywhere (bead 4dpiz). Individual tests override.
    vi.mocked(api.getProviderCatchupStatus).mockResolvedValue({});
  });

  describe('source lifecycle', () => {
    it('recovers from a transient failure through the scoped Retry action', async () => {
      vi.mocked(api.getM3UAccounts)
        .mockRejectedValueOnce(new Error('Network down'))
        .mockResolvedValueOnce([makeAccount({ name: 'Recovered Provider' })]);
      vi.mocked(api.getServerGroups).mockResolvedValue([]);

      renderWithProviders(<M3UManagerTab />);

      expect(await screen.findByRole('status', { name: 'Provider accounts unavailable' })).toBeVisible();
      fireEvent.click(screen.getByRole('button', { name: 'Retry loading provider accounts' }));

      expect(await screen.findByText('Recovered Provider')).toBeVisible();
      expect(screen.getByText('1 provider account')).toBeVisible();
      expect(api.getM3UAccounts).toHaveBeenCalledTimes(2);
    });

    it('does not offer Retry or protected actions for a permission failure', async () => {
      vi.mocked(api.getM3UAccounts).mockRejectedValue(new HttpError('Forbidden', 403));
      vi.mocked(api.getServerGroups).mockResolvedValue([]);

      renderWithProviders(<M3UManagerTab />);

      expect(await screen.findByRole('status', { name: 'Provider accounts access denied' })).toBeVisible();
      expect(screen.queryByRole('button', { name: /Retry loading/i })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /Add M3U Account/i })).not.toBeInTheDocument();
    });
  });

  describe('Server Groups management (bead hq3de.c)', () => {
    it('opens and closes the Server Groups modal from the toolbar', async () => {
      vi.mocked(api.getM3UAccounts).mockResolvedValue([makeAccount()]);
      vi.mocked(api.getServerGroups).mockResolvedValue([]);

      renderWithProviders(<M3UManagerTab />);
      await waitFor(() => expect(screen.getByText('Standard Playlist')).toBeInTheDocument());

      expect(screen.queryByTestId('server-groups-modal')).not.toBeInTheDocument();

      // Server Groups now lives inside the header kebab (bead 09x38.2).
      fireEvent.click(screen.getByRole('button', { name: /m3u setup actions/i }));
      fireEvent.click(screen.getByRole('menuitem', { name: /server groups/i }));
      expect(screen.getByTestId('server-groups-modal')).toBeInTheDocument();

      fireEvent.click(screen.getByText('Close Server Groups'));
      expect(screen.queryByTestId('server-groups-modal')).not.toBeInTheDocument();
    });
  });

  describe('Header overflow kebab (bead 09x38.2)', () => {
    it('keeps Refresh All and Add M3U Account as primary toolbar buttons', async () => {
      vi.mocked(api.getM3UAccounts).mockResolvedValue([makeAccount()]);
      vi.mocked(api.getServerGroups).mockResolvedValue([]);

      renderWithProviders(<M3UManagerTab />);
      await waitFor(() => expect(screen.getByText('Standard Playlist')).toBeInTheDocument());

      // Primary actions are directly visible (not hidden behind the kebab).
      expect(screen.getByRole('button', { name: /refresh all/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /add m3u account/i })).toBeInTheDocument();

      // Setup/admin actions are NOT rendered until the kebab is opened.
      expect(screen.queryByRole('menuitem', { name: /server groups/i })).not.toBeInTheDocument();
      expect(screen.queryByRole('menuitem', { name: /manage links/i })).not.toBeInTheDocument();
    });

    it('opens the kebab to reveal all four setup/admin actions, then closes on selection', async () => {
      vi.mocked(api.getM3UAccounts).mockResolvedValue([makeAccount()]);
      vi.mocked(api.getServerGroups).mockResolvedValue([]);

      renderWithProviders(<M3UManagerTab />);
      await waitFor(() => expect(screen.getByText('Standard Playlist')).toBeInTheDocument());

      fireEvent.click(screen.getByRole('button', { name: /m3u setup actions/i }));

      expect(screen.getByRole('menuitem', { name: /server groups/i })).toBeInTheDocument();
      expect(screen.getByRole('menuitem', { name: /stream profiles/i })).toBeInTheDocument();
      expect(screen.getByRole('menuitem', { name: /manage links/i })).toBeInTheDocument();
      expect(screen.getByRole('menuitem', { name: /sync groups/i })).toBeInTheDocument();

      // Selecting an item closes the menu.
      fireEvent.click(screen.getByRole('menuitem', { name: /manage links/i }));
      expect(screen.queryByRole('menuitem', { name: /server groups/i })).not.toBeInTheDocument();
    });
  });

  describe('Refresh VOD (bead hq3de.d)', () => {
    it('shows the Refresh VOD action only for XtreamCodes accounts', async () => {
      vi.mocked(api.getM3UAccounts).mockResolvedValue([
        makeAccount({ id: 1, name: 'Standard Playlist', account_type: 'STD' }),
        makeAccount({ id: 2, name: 'Xtream Account', account_type: 'XC' }),
      ]);
      vi.mocked(api.getServerGroups).mockResolvedValue([]);

      renderWithProviders(<M3UManagerTab />);
      await waitFor(() => expect(screen.getByText('Xtream Account')).toBeInTheDocument());

      expect(screen.getAllByLabelText('Refresh VOD content')).toHaveLength(1);
    });

    it('calls refreshM3UVod for the clicked XC account', async () => {
      vi.mocked(api.getM3UAccounts).mockResolvedValue([
        makeAccount({ id: 2, name: 'Xtream Account', account_type: 'XC' }),
      ]);
      vi.mocked(api.getServerGroups).mockResolvedValue([]);
      vi.mocked(api.refreshM3UVod).mockResolvedValue({});

      renderWithProviders(<M3UManagerTab />);
      await waitFor(() => expect(screen.getByText('Xtream Account')).toBeInTheDocument());

      fireEvent.click(screen.getByLabelText('Refresh VOD content'));

      await waitFor(() => {
        expect(api.refreshM3UVod).toHaveBeenCalledWith(2);
      });
    });

    it('disables Refresh VOD for an inactive account', async () => {
      vi.mocked(api.getM3UAccounts).mockResolvedValue([
        makeAccount({ id: 2, name: 'Xtream Account', account_type: 'XC', is_active: false }),
      ]);
      vi.mocked(api.getServerGroups).mockResolvedValue([]);

      renderWithProviders(<M3UManagerTab />);
      await waitFor(() => expect(screen.getByText('Xtream Account')).toBeInTheDocument());

      expect(screen.getByLabelText('Refresh VOD content')).toBeDisabled();
    });
  });

  describe('Provider catch-up badge (bead 4dpiz)', () => {
    it('renders the catch-up badge on a provider row when has_catchup is true', async () => {
      vi.mocked(api.getM3UAccounts).mockResolvedValue([
        makeAccount({ id: 1, name: 'Catchup Provider' }),
      ]);
      vi.mocked(api.getServerGroups).mockResolvedValue([]);
      vi.mocked(api.getProviderCatchupStatus).mockResolvedValue({
        '1': { has_catchup: true, catchup_days: 5 },
      });

      renderWithProviders(<M3UManagerTab />);
      await waitFor(() => expect(screen.getByText('Catchup Provider')).toBeInTheDocument());

      await waitFor(() =>
        expect(
          screen.getByLabelText('Provider supports catch-up — up to 5 days')
        ).toBeInTheDocument()
      );
    });

    it('renders NO catch-up badge when has_catchup is false', async () => {
      vi.mocked(api.getM3UAccounts).mockResolvedValue([
        makeAccount({ id: 1, name: 'No Catchup Provider' }),
      ]);
      vi.mocked(api.getServerGroups).mockResolvedValue([]);
      vi.mocked(api.getProviderCatchupStatus).mockResolvedValue({
        '1': { has_catchup: false, catchup_days: null },
      });

      renderWithProviders(<M3UManagerTab />);
      await waitFor(() => expect(screen.getByText('No Catchup Provider')).toBeInTheDocument());

      expect(screen.queryByLabelText(/provider supports catch-up/i)).not.toBeInTheDocument();
    });
  });

  describe('Stream Profiles management (bead hq3de.j)', () => {
    it('opens and closes the Stream Profiles modal, passing the current streamProfiles prop', async () => {
      vi.mocked(api.getM3UAccounts).mockResolvedValue([makeAccount()]);
      vi.mocked(api.getServerGroups).mockResolvedValue([]);

      renderWithProviders(
        <M3UManagerTab streamProfiles={[{ id: 1, name: 'Default', command: 'ffmpeg', parameters: '', is_active: true, locked: false }]} />
      );
      await waitFor(() => expect(screen.getByText('Standard Playlist')).toBeInTheDocument());

      expect(screen.queryByTestId('stream-profiles-modal')).not.toBeInTheDocument();

      fireEvent.click(screen.getByRole('button', { name: /m3u setup actions/i }));
      fireEvent.click(screen.getByRole('menuitem', { name: /stream profiles/i }));
      expect(screen.getByTestId('stream-profiles-modal')).toBeInTheDocument();
      expect(screen.getByText('1 profile(s)')).toBeInTheDocument();

      fireEvent.click(screen.getByText('Close Stream Profiles'));
      expect(screen.queryByTestId('stream-profiles-modal')).not.toBeInTheDocument();
    });

    it('calls onStreamProfilesChange when the modal reports a change', async () => {
      vi.mocked(api.getM3UAccounts).mockResolvedValue([makeAccount()]);
      vi.mocked(api.getServerGroups).mockResolvedValue([]);
      const onStreamProfilesChange = vi.fn();

      renderWithProviders(
        <M3UManagerTab streamProfiles={[]} onStreamProfilesChange={onStreamProfilesChange} />
      );
      await waitFor(() => expect(screen.getByText('Standard Playlist')).toBeInTheDocument());

      fireEvent.click(screen.getByRole('button', { name: /m3u setup actions/i }));
      fireEvent.click(screen.getByRole('menuitem', { name: /stream profiles/i }));
      fireEvent.click(screen.getByText('Trigger Changed'));

      expect(onStreamProfilesChange).toHaveBeenCalledTimes(1);
    });
  });
});
