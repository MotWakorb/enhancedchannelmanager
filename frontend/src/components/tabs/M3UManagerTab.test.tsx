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
      // Recovery used to be asserted through "1 provider account" in the route
      // header. That count is gone (bead enhancedchannelmanager-tygwm) — it
      // restated the list right below it — so recovery is now proven by the
      // failure status and its Retry disappearing and the account rendering.
      expect(screen.queryByRole('status', { name: 'Provider accounts unavailable' })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Retry loading provider accounts' })).not.toBeInTheDocument();
      expect(screen.queryByText(/\d+ provider accounts?/)).not.toBeInTheDocument();
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

  describe('account deletion', () => {
    it('reloads account state and warns when deletion succeeded but linked-settings cleanup failed', async () => {
      const account = makeAccount({ id: 2, name: 'Partially Cleaned Provider' });
      vi.mocked(api.getM3UAccounts)
        .mockResolvedValueOnce([account])
        .mockResolvedValueOnce([]);
      vi.mocked(api.getServerGroups).mockResolvedValue([]);
      vi.mocked(api.deleteM3UAccount).mockResolvedValue({
        status: 'deleted_with_cleanup_warning',
        account_deleted: true,
        linked_settings_cleanup: 'failed',
        message: 'The account was deleted, but linked-settings cleanup failed. This DELETE must not be retried.',
        deleted_groups: [],
        skipped_groups: [],
        failed_groups: [],
      });
      vi.stubGlobal('confirm', vi.fn(() => true));
      const onAccountsChange = vi.fn();

      renderWithProviders(<M3UManagerTab onAccountsChange={onAccountsChange} />);
      await screen.findByText('Partially Cleaned Provider');
      fireEvent.click(screen.getByRole('button', { name: 'Delete account' }));

      await waitFor(() => {
        expect(api.getM3UAccounts).toHaveBeenCalledTimes(2);
        expect(screen.queryByText('Partially Cleaned Provider')).not.toBeInTheDocument();
      });
      expect(onAccountsChange).toHaveBeenCalledTimes(1);
      expect(await screen.findByText(/linked-settings cleanup failed/i)).toBeVisible();
      expect(screen.getByText(/must not be retried/i)).toBeVisible();
    });
  });

  // Bead enhancedchannelmanager-7dxx0. The pane opened on an unlabelled
  // table — route title, description, then straight into the accounts list.
  // The heading is rendered by PageHeader, so asserting it sits inside
  // `.header-title` is what pins it to the shared section role (15px/600/1.3,
  // asserted from disk in PageHeader.test.tsx) rather than a hand-rolled h2
  // carrying its own typography.
  describe('accounts section heading (bead enhancedchannelmanager-7dxx0)', () => {
    it('labels the accounts table with a section heading rendered above it', async () => {
      vi.mocked(api.getM3UAccounts).mockResolvedValue([makeAccount()]);
      vi.mocked(api.getServerGroups).mockResolvedValue([]);

      renderWithProviders(<M3UManagerTab />);
      await waitFor(() => expect(screen.getByText('Standard Playlist')).toBeInTheDocument());

      const list = screen.getByText('Standard Playlist').closest('.m3u-accounts-list');
      expect(list).not.toBeNull();

      const heading = screen.getByRole('heading', { level: 2, name: 'M3U Accounts' });
      expect(heading.closest('.header-title')).not.toBeNull();
      expect(
        heading.compareDocumentPosition(list as HTMLElement) & Node.DOCUMENT_POSITION_FOLLOWING
      ).toBeTruthy();
    });

    // The heading is not conditional on there being rows. Beyond keeping the
    // pane's structure stable between states, it repairs an outline that
    // skipped h1 -> h3: the empty state's "No M3U Accounts" is an h3 and the
    // route title is the page's only h1.
    it('keeps the heading over the empty state so the outline never skips a level', async () => {
      vi.mocked(api.getM3UAccounts).mockResolvedValue([]);
      vi.mocked(api.getServerGroups).mockResolvedValue([]);

      renderWithProviders(<M3UManagerTab />);

      const heading = await screen.findByRole('heading', { level: 2, name: 'M3U Accounts' });
      const emptyState = screen.getByRole('heading', { level: 3, name: 'No M3U Accounts' });
      expect(
        heading.compareDocumentPosition(emptyState) & Node.DOCUMENT_POSITION_FOLLOWING
      ).toBeTruthy();
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

  // Refresh VOD moved from a visible row button into the row's overflow kebab
  // in bead enhancedchannelmanager-xh33o: the eight-button row did not fit its
  // 180px actions column, and `.action-btn` is now `flex-shrink: 0`, so a
  // button that does not fit is clipped rather than squeezed. Opening the
  // kebab is therefore part of reaching the action, which is what these
  // assertions now walk.
  describe('Refresh VOD (bead hq3de.d)', () => {
    const openRowMenu = (accountName: string) =>
      fireEvent.click(screen.getByRole('button', { name: `More actions for ${accountName}` }));

    it('offers Refresh VOD only in an XtreamCodes account row menu', async () => {
      vi.mocked(api.getM3UAccounts).mockResolvedValue([
        makeAccount({ id: 1, name: 'Standard Playlist', account_type: 'STD' }),
        makeAccount({ id: 2, name: 'Xtream Account', account_type: 'XC' }),
      ]);
      vi.mocked(api.getServerGroups).mockResolvedValue([]);

      renderWithProviders(<M3UManagerTab />);
      await waitFor(() => expect(screen.getByText('Xtream Account')).toBeInTheDocument());

      openRowMenu('Standard Playlist');
      expect(screen.queryByRole('menuitem', { name: /refresh vod/i })).not.toBeInTheDocument();
      fireEvent.keyDown(screen.getByRole('menu'), { key: 'Escape' });

      openRowMenu('Xtream Account');
      expect(screen.getAllByRole('menuitem', { name: /refresh vod/i })).toHaveLength(1);
    });

    it('keeps Refresh VOD out of the row until the kebab is opened', async () => {
      vi.mocked(api.getM3UAccounts).mockResolvedValue([
        makeAccount({ id: 2, name: 'Xtream Account', account_type: 'XC' }),
      ]);
      vi.mocked(api.getServerGroups).mockResolvedValue([]);

      renderWithProviders(<M3UManagerTab />);
      await waitFor(() => expect(screen.getByText('Xtream Account')).toBeInTheDocument());

      expect(screen.queryByRole('menuitem', { name: /refresh vod/i })).not.toBeInTheDocument();
      // The four actions that stay visible are what the column holds.
      expect(screen.getByLabelText('Disable account')).toBeInTheDocument();
      expect(screen.getByLabelText('Refresh account')).toBeInTheDocument();
      expect(screen.getByLabelText('Edit account')).toBeInTheDocument();
      expect(screen.getByLabelText('Delete account')).toBeInTheDocument();
    });

    it('calls refreshM3UVod for the clicked XC account', async () => {
      vi.mocked(api.getM3UAccounts).mockResolvedValue([
        makeAccount({ id: 2, name: 'Xtream Account', account_type: 'XC' }),
      ]);
      vi.mocked(api.getServerGroups).mockResolvedValue([]);
      vi.mocked(api.refreshM3UVod).mockResolvedValue({});

      renderWithProviders(<M3UManagerTab />);
      await waitFor(() => expect(screen.getByText('Xtream Account')).toBeInTheDocument());

      openRowMenu('Xtream Account');
      fireEvent.click(screen.getByRole('menuitem', { name: /refresh vod/i }));

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

      openRowMenu('Xtream Account');
      expect(screen.getByRole('menuitem', { name: /refresh vod/i })).toBeDisabled();
    });
  });

  // Bead enhancedchannelmanager-xh33o. The per-row setup actions moved into
  // the row kebab so the visible buttons fit the 180px actions column at the
  // 32px box `.action-btn` declares.
  describe('Row overflow kebab (bead enhancedchannelmanager-xh33o)', () => {
    it('holds the three per-account setup actions and closes on selection', async () => {
      vi.mocked(api.getM3UAccounts).mockResolvedValue([makeAccount()]);
      vi.mocked(api.getServerGroups).mockResolvedValue([]);

      renderWithProviders(<M3UManagerTab />);
      await waitFor(() => expect(screen.getByText('Standard Playlist')).toBeInTheDocument());

      expect(screen.queryByRole('menuitem', { name: /manage groups/i })).not.toBeInTheDocument();

      fireEvent.click(screen.getByRole('button', { name: 'More actions for Standard Playlist' }));
      expect(screen.getByRole('menuitem', { name: /manage groups/i })).toBeInTheDocument();
      expect(screen.getByRole('menuitem', { name: /manage account profiles/i })).toBeInTheDocument();
      expect(screen.getByRole('menuitem', { name: /manage filters/i })).toBeInTheDocument();

      fireEvent.click(screen.getByRole('menuitem', { name: /manage groups/i }));
      expect(screen.queryByRole('menuitem', { name: /manage groups/i })).not.toBeInTheDocument();
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

    // Bead enhancedchannelmanager-sccol: the badge moved off the account-name
    // line onto the meta line beside the connection-type chip. Pinned by
    // structure, not by pixel — the point of the move is that the two chips
    // answer the same question, so they must share a container.
    it('sits on the meta line beside the connection-type chip, not on the name line', async () => {
      vi.mocked(api.getM3UAccounts).mockResolvedValue([
        makeAccount({ id: 1, name: 'Catchup Provider' }),
      ]);
      vi.mocked(api.getServerGroups).mockResolvedValue([]);
      vi.mocked(api.getProviderCatchupStatus).mockResolvedValue({
        '1': { has_catchup: true, catchup_days: 5 },
      });

      renderWithProviders(<M3UManagerTab />);
      const badge = await screen.findByLabelText('Provider supports catch-up — up to 5 days');

      expect(badge.closest('.account-details')).not.toBeNull();
      expect(badge.closest('.account-name')).toBeNull();
      expect(badge.closest('.account-details'))
        .toBe(screen.getByText('Standard M3U').closest('.account-details'));
    });
  });

  // Bead enhancedchannelmanager-sccol. The inference itself is unit-tested in
  // utils/hdhomerun.test.ts; these pin that the row actually consults it and
  // that a generic standard playlist is left alone.
  describe('Connection-type chip', () => {
    async function renderWithAccount(overrides: Partial<M3UAccount>) {
      vi.mocked(api.getM3UAccounts).mockResolvedValue([makeAccount(overrides)]);
      vi.mocked(api.getServerGroups).mockResolvedValue([]);
      renderWithProviders(<M3UManagerTab />);
      await waitFor(() => expect(screen.getByText(overrides.name as string)).toBeInTheDocument());
    }

    it('reads HDHomeRun for a tuner lineup URL', async () => {
      await renderWithAccount({
        name: 'HD Homerun',
        account_type: 'STD',
        server_url: 'http://192.168.1.105/lineup.m3u',
      });

      const chip = screen.getByText('HDHomeRun');
      expect(chip).toHaveClass('account-type', 'hdhr');
      expect(screen.queryByText('Standard M3U')).not.toBeInTheDocument();
    });

    it('leaves a generic standard playlist as Standard M3U', async () => {
      await renderWithAccount({
        name: 'Generic Playlist',
        account_type: 'STD',
        server_url: 'http://provider.example/hdhomerun/playlist.m3u',
      });

      expect(screen.getByText('Standard M3U')).toHaveClass('account-type', 'std');
      expect(screen.queryByText('HDHomeRun')).not.toBeInTheDocument();
    });

    it('leaves an XtreamCodes account alone whatever its URL says', async () => {
      await renderWithAccount({
        name: 'XC Provider',
        account_type: 'XC',
        server_url: 'http://192.168.1.105:5004/lineup.m3u',
      });

      expect(screen.getByText('XtreamCodes')).toHaveClass('account-type', 'xc');
      expect(screen.queryByText('HDHomeRun')).not.toBeInTheDocument();
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
