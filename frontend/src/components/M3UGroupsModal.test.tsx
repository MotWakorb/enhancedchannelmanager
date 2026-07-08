/**
 * Unit tests for M3UGroupsModal — bd-dgs64 (GH #591).
 *
 * Dispatcharr channel groups are global entities: a group with the same name
 * on two M3U providers shares one channel_group ID. `autoSyncedByOtherAccounts`
 * (commit 030c1ef8) locks a group's Auto-Sync toggle/Start#/Settings once
 * another account already auto-syncs that ID, to avoid two providers silently
 * double-creating channels for the same group.
 *
 * `allowMultiProviderAutoSync` is the opt-out (admin-only, backend-sourced
 * setting): when true, the lock is lifted — the controls stay usable — but an
 * informational shared-ownership indicator remains so the overlap is still
 * visible. When false (default), behavior is unchanged from before this bead.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { M3UGroupsModal } from './M3UGroupsModal';
import type { M3UAccount } from '../types';

vi.mock('../services/api', () => ({
  getM3UAccount: vi.fn(),
  getChannelGroups: vi.fn(),
  updateM3UGroupSettings: vi.fn(),
}));

// Stable object reference (NOT recreated per call) — the real
// NotificationContext memoizes its value with useMemo, and M3UGroupsModal's
// data-load useEffect depends on `notifications`. A mock that returns a new
// object on every render would refire that effect after every state update
// (including the auto-sync toggle click), clobbering local state back to the
// last-fetched snapshot before the assertions run.
const mockNotifications = {
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
};

vi.mock('../contexts/NotificationContext', () => ({
  useNotifications: () => mockNotifications,
}));

import * as api from '../services/api';

function makeAccount(overrides: Partial<M3UAccount> = {}): M3UAccount {
  return {
    id: 1,
    name: 'Provider A',
    server_url: 'http://provider-a.example',
    is_active: true,
    channel_groups: [
      {
        id: 10,
        channel_group: 100,
        enabled: true,
        auto_channel_sync: false,
        auto_sync_channel_start: null,
        custom_properties: null,
      },
    ],
    ...overrides,
  } as M3UAccount;
}

function makeOtherAccount(overrides: Partial<M3UAccount> = {}): M3UAccount {
  return {
    id: 2,
    name: 'Provider B',
    server_url: 'http://provider-b.example',
    is_active: true,
    channel_groups: [
      {
        id: 20,
        channel_group: 100, // same global group ID as account 1's group
        enabled: true,
        auto_channel_sync: true, // Provider B already auto-syncs this group
        auto_sync_channel_start: 500,
        custom_properties: null,
      },
    ],
    ...overrides,
  } as M3UAccount;
}

describe('M3UGroupsModal — multi-provider auto-sync guard (bd-dgs64)', () => {
  const account = makeAccount();
  const otherAccount = makeOtherAccount();

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getM3UAccount).mockResolvedValue(account);
    vi.mocked(api.getChannelGroups).mockResolvedValue([
      { id: 100, name: 'Sports HD' } as never,
    ]);
  });

  it('locks the row with an owned-by indicator when the setting is off (default, unchanged behavior)', async () => {
    render(
      <M3UGroupsModal
        isOpen={true}
        onClose={vi.fn()}
        onSaved={vi.fn()}
        account={account}
        allAccounts={[account, otherAccount]}
      />
    );

    await screen.findByText('Sports HD');
    const row = screen.getByText('Sports HD').closest('.group-row') as HTMLElement;

    expect(within(row).getByText('Provider B')).toBeInTheDocument();
    // No live checkbox for auto-sync on this row — it's the locked indicator instead.
    const autoSyncCell = row.querySelector('.group-autosync') as HTMLElement;
    expect(within(autoSyncCell).queryByRole('checkbox')).not.toBeInTheDocument();
    const startInput = within(row).getByPlaceholderText('--') as HTMLInputElement;
    expect(startInput.disabled).toBe(true);
    const settingsBtn = row.querySelector('.settings-btn') as HTMLButtonElement;
    expect(settingsBtn.disabled).toBe(true);
    expect(settingsBtn.title).toMatch(/Auto-synced by: Provider B/i);
  });

  it('unlocks the toggle, Start #, and Settings button when the setting is on, keeping a shared-ownership indicator', async () => {
    render(
      <M3UGroupsModal
        isOpen={true}
        onClose={vi.fn()}
        onSaved={vi.fn()}
        account={account}
        allAccounts={[account, otherAccount]}
        allowMultiProviderAutoSync={true}
      />
    );

    await screen.findByText('Sports HD');
    const row = screen.getByText('Sports HD').closest('.group-row') as HTMLElement;

    // The "owned by" locked text is gone...
    expect(within(row).queryByText('Provider B')).not.toBeInTheDocument();
    // ...but a shared indicator with the same information still exists.
    const sharedIndicator = row.querySelector('.autosync-shared-indicator') as HTMLElement;
    expect(sharedIndicator).toBeInTheDocument();
    expect(sharedIndicator.title).toMatch(/Also auto-synced by: Provider B/i);

    // The auto-sync toggle in this row is now a live, enabled checkbox.
    const autoSyncCell = row.querySelector('.group-autosync') as HTMLElement;
    const autoSyncCheckbox = within(autoSyncCell).getByRole('checkbox') as HTMLInputElement;
    expect(autoSyncCheckbox.disabled).toBe(false);
    expect(autoSyncCheckbox.checked).toBe(false);

    const user = userEvent.setup();
    await user.click(autoSyncCheckbox);
    expect(autoSyncCheckbox.checked).toBe(true);

    // After toggling on, Start # should become enabled (group.enabled is true).
    const startInput = within(row).getByPlaceholderText('--') as HTMLInputElement;
    expect(startInput.disabled).toBe(false);
  });

  it('keeps Start # and Settings locked for a disabled group even when the setting is on (enabled-gate not overridden by the multi-provider opt-out)', async () => {
    const disabledGroupAccount = makeAccount({
      channel_groups: [
        {
          id: 10,
          channel_group: 100,
          channel_group_name: 'Sports HD',
          enabled: false,
          enabled_vod: false,
          enabled_series: false,
          auto_channel_sync: false,
          auto_sync_channel_start: null,
          custom_properties: null,
        },
      ],
    });
    vi.mocked(api.getM3UAccount).mockResolvedValue(disabledGroupAccount);

    render(
      <M3UGroupsModal
        isOpen={true}
        onClose={vi.fn()}
        onSaved={vi.fn()}
        account={disabledGroupAccount}
        allAccounts={[disabledGroupAccount, otherAccount]}
        allowMultiProviderAutoSync={true}
      />
    );

    await screen.findByText('Sports HD');
    const row = screen.getByText('Sports HD').closest('.group-row') as HTMLElement;

    // Auto-sync toggle stays disabled — gated on group.enabled, independent
    // of the multi-provider lock lift.
    const autoSyncCell = row.querySelector('.group-autosync') as HTMLElement;
    const autoSyncCheckbox = within(autoSyncCell).getByRole('checkbox') as HTMLInputElement;
    expect(autoSyncCheckbox.disabled).toBe(true);

    // Start # and Settings must also stay disabled — the multi-provider
    // opt-out only lifts the OTHER-ACCOUNT ownership lock, never the
    // this-group-is-disabled gate.
    const startInput = within(row).getByPlaceholderText('--') as HTMLInputElement;
    expect(startInput.disabled).toBe(true);
    const settingsBtn = row.querySelector('.settings-btn') as HTMLButtonElement;
    expect(settingsBtn.disabled).toBe(true);
  });

  it('sends the updated auto_channel_sync value in the save payload after toggling and clicking Save', async () => {
    render(
      <M3UGroupsModal
        isOpen={true}
        onClose={vi.fn()}
        onSaved={vi.fn()}
        account={account}
        allAccounts={[account, otherAccount]}
        allowMultiProviderAutoSync={true}
      />
    );

    await screen.findByText('Sports HD');
    const row = screen.getByText('Sports HD').closest('.group-row') as HTMLElement;
    const autoSyncCell = row.querySelector('.group-autosync') as HTMLElement;
    const autoSyncCheckbox = within(autoSyncCell).getByRole('checkbox') as HTMLInputElement;

    const user = userEvent.setup();
    await user.click(autoSyncCheckbox);
    expect(autoSyncCheckbox.checked).toBe(true);

    await user.click(screen.getByRole('button', { name: /Save Changes/i }));

    await waitFor(() => expect(api.updateM3UGroupSettings).toHaveBeenCalled());
    const [accountId, payload] = vi.mocked(api.updateM3UGroupSettings).mock.calls[0];
    expect(accountId).toBe(account.id);
    const groupPayload = (payload as { group_settings: Array<{ channel_group: number; auto_channel_sync: boolean }> })
      .group_settings.find((g) => g.channel_group === 100);
    expect(groupPayload?.auto_channel_sync).toBe(true);
  });
});
