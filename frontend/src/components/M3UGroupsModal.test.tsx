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
import { render, screen, within, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { M3UGroupsModal } from './M3UGroupsModal';
import type { M3UAccount } from '../types';

vi.mock('../services/api', async () => {
  // Keep the real profileApplyIncomplete decision helper (#9) — only the
  // network functions are stubbed.
  const actual = await vi.importActual<typeof import('../services/api')>('../services/api');
  return {
    ...actual,
    getM3UAccount: vi.fn(),
    getChannelGroups: vi.fn(),
    updateM3UGroupSettings: vi.fn(),
    refreshM3UAccount: vi.fn(),
  };
});

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
    vi.mocked(api.updateM3UGroupSettings).mockResolvedValue({ message: 'ok' });
    vi.mocked(api.refreshM3UAccount).mockResolvedValue({ success: true, message: 'started' });
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

  it('gear tooltip names BOTH missing prerequisites when the group itself is disabled (bead 09x38.8)', async () => {
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
        allAccounts={[disabledGroupAccount]}
      />
    );

    await screen.findByText('Sports HD');
    const row = screen.getByText('Sports HD').closest('.group-row') as HTMLElement;
    const settingsBtn = row.querySelector('.settings-btn') as HTMLButtonElement;

    expect(settingsBtn.disabled).toBe(true);
    expect(settingsBtn.title).toBe('Enable this group and turn on Auto-Sync to configure settings');
  });

  it('gear tooltip names only the Auto-Sync prerequisite when the group is already enabled (bead 09x38.8)', async () => {
    render(
      <M3UGroupsModal
        isOpen={true}
        onClose={vi.fn()}
        onSaved={vi.fn()}
        account={account}
        allAccounts={[account]}
      />
    );

    await screen.findByText('Sports HD');
    const row = screen.getByText('Sports HD').closest('.group-row') as HTMLElement;
    const settingsBtn = row.querySelector('.settings-btn') as HTMLButtonElement;

    // Fixture's group is enabled: true, auto_channel_sync: false — only the
    // Auto-Sync toggle is the blocker, so the tooltip should not also claim
    // the group needs to be enabled.
    expect(settingsBtn.disabled).toBe(true);
    expect(settingsBtn.title).toBe('Turn on Auto-Sync to configure settings');
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

    await user.click(screen.getByRole('button', { name: /Save & Refresh/i }));

    await waitFor(() => expect(api.updateM3UGroupSettings).toHaveBeenCalled());
    const [accountId, payload] = vi.mocked(api.updateM3UGroupSettings).mock.calls[0];
    expect(accountId).toBe(account.id);
    const groupPayload = (payload as { group_settings: Array<{ channel_group: number; auto_channel_sync: boolean }> })
      .group_settings.find((g) => g.channel_group === 100);
    expect(groupPayload?.auto_channel_sync).toBe(true);
  });
});

/**
 * Bead enhancedchannelmanager-igqcy — Dispatcharr v0.25.0+ group-settings is a
 * FULL-ROW upsert (omitted fields silently reset: auto_channel_sync -> false,
 * start/end -> null, custom_properties -> {}). Proven live against v0.27.2:
 * an enabled-only ECM write wiped a natively-configured group. Every save must
 * carry the complete field set — including auto_sync_channel_end (v0.25.0+)
 * and custom_properties verbatim (unknown keys like channel_numbering_mode
 * must survive the round-trip). Save also chains an M3U refresh (Dispatcharr
 * parity: its modal's only save action is Save & Refresh) — but only after a
 * successful save.
 *
 * The channel_groups row shape below mirrors the REAL v0.27.2 serializer
 * payload recorded during the bd-478fe QA pass.
 */
describe('M3UGroupsModal — full-row save payload + Save & Refresh (bead igqcy)', () => {
  // Real v0.27.2 row shape: auto_sync_channel_end present, custom_properties
  // carrying keys ECM does not model.
  const nativeConfiguredAccount = makeAccount({
    channel_groups: [
      {
        id: 10,
        channel_group: 100,
        enabled: true,
        auto_channel_sync: true,
        auto_sync_channel_start: 1,
        auto_sync_channel_end: 500,
        custom_properties: {
          group_override: 7,                    // ECM-known key
          channel_numbering_mode: 'assigned',   // unknown to ECM (v0.27.2)
          force_dummy_epg: true,                // unknown to ECM (v0.27.2)
        },
      },
    ] as never,
  });

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getM3UAccount).mockResolvedValue(nativeConfiguredAccount);
    vi.mocked(api.getChannelGroups).mockResolvedValue([
      { id: 100, name: 'Sports HD' } as never,
    ]);
    vi.mocked(api.updateM3UGroupSettings).mockResolvedValue({ message: 'ok' });
    vi.mocked(api.refreshM3UAccount).mockResolvedValue({ success: true, message: 'started' });
  });

  async function toggleEnabledAndSave() {
    render(
      <M3UGroupsModal
        isOpen={true}
        onClose={vi.fn()}
        onSaved={vi.fn()}
        account={nativeConfiguredAccount}
        allAccounts={[nativeConfiguredAccount]}
      />
    );

    await screen.findByText('Sports HD');
    const row = screen.getByText('Sports HD').closest('.group-row') as HTMLElement;
    const enabledCell = row.querySelector('.group-enabled') as HTMLElement;
    const enabledCheckbox = within(enabledCell).getByRole('checkbox') as HTMLInputElement;

    const user = userEvent.setup();
    await user.click(enabledCheckbox); // enabled-only intent (the live-proven clobber)
    await user.click(screen.getByRole('button', { name: /Save & Refresh/i }));
  }

  it('sends the complete field set — an enabled-only toggle preserves auto-sync, start, end, and custom_properties verbatim', async () => {
    await toggleEnabledAndSave();

    await waitFor(() => expect(api.updateM3UGroupSettings).toHaveBeenCalled());
    const [, payload] = vi.mocked(api.updateM3UGroupSettings).mock.calls[0];
    const sent = (payload as { group_settings: Array<Record<string, unknown>> })
      .group_settings.find((g) => g.channel_group === 100);

    expect(sent).toBeDefined();
    expect(sent?.enabled).toBe(false);            // the intended change
    expect(sent?.auto_channel_sync).toBe(false);  // disabling the group also disables auto-sync (existing UI rule)
    expect(sent?.auto_sync_channel_start).toBe(1);
    expect(sent?.auto_sync_channel_end).toBe(500);
    // Unknown custom_properties keys survive the round-trip verbatim.
    expect(sent?.custom_properties).toEqual({
      group_override: 7,
      channel_numbering_mode: 'assigned',
      force_dummy_epg: true,
    });
  });

  it('chains an M3U refresh of the account after a successful save', async () => {
    await toggleEnabledAndSave();

    await waitFor(() => expect(api.refreshM3UAccount).toHaveBeenCalledWith(nativeConfiguredAccount.id));
    // Refresh fires strictly after the save.
    const saveOrder = vi.mocked(api.updateM3UGroupSettings).mock.invocationCallOrder[0];
    const refreshOrder = vi.mocked(api.refreshM3UAccount).mock.invocationCallOrder[0];
    expect(refreshOrder).toBeGreaterThan(saveOrder);
  });

  it('does NOT fire the refresh when the save fails', async () => {
    vi.mocked(api.updateM3UGroupSettings).mockRejectedValue(new Error('save failed'));

    await toggleEnabledAndSave();

    await waitFor(() => expect(api.updateM3UGroupSettings).toHaveBeenCalled());
    expect(api.refreshM3UAccount).not.toHaveBeenCalled();
  });

  it('Round-9 B1: a fail-closed (non-2xx) save reads as NOT saved — modal stays open, no refresh/onSaved', async () => {
    // The backend returns 503 -> updateM3UGroupSettings throws with the detail.
    vi.mocked(api.updateM3UGroupSettings).mockRejectedValue(
      new Error('The channel-profile selection was NOT saved — please retry.')
    );
    const onClose = vi.fn();
    const onSaved = vi.fn();
    render(
      <M3UGroupsModal
        isOpen={true} onClose={onClose} onSaved={onSaved}
        account={nativeConfiguredAccount} allAccounts={[nativeConfiguredAccount]}
      />
    );
    await screen.findByText('Sports HD');
    const row = screen.getByText('Sports HD').closest('.group-row') as HTMLElement;
    const enabledCheckbox = within(row.querySelector('.group-enabled') as HTMLElement)
      .getByRole('checkbox') as HTMLInputElement;
    const user = userEvent.setup();
    await user.click(enabledCheckbox);
    await user.click(screen.getByRole('button', { name: /Save & Refresh/i }));

    await waitFor(() => expect(api.updateM3UGroupSettings).toHaveBeenCalled());
    // NOT saved: no refresh, no onSaved, modal NOT closed, and an error toast
    // carrying the "NOT saved" message.
    expect(api.refreshM3UAccount).not.toHaveBeenCalled();
    expect(onSaved).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    expect(mockNotifications.error).toHaveBeenCalledWith(
      expect.stringContaining('NOT saved'), 'M3U Groups'
    );
  });

  it('#9: warns (not plain success) when the save reports an incomplete profile apply', async () => {
    vi.mocked(api.updateM3UGroupSettings).mockResolvedValue({
      message: 'ok',
      ecm_profile_apply: [
        { status: 'partial_failure', group_id: 100, failed_profile_ids: [2] },
      ],
    });

    await toggleEnabledAndSave();

    await waitFor(() =>
      expect(mockNotifications.warning).toHaveBeenCalledWith(
        expect.stringContaining('applying some channel profiles failed'),
        'M3U Groups'
      )
    );
    // The plain success toast is NOT shown in this case.
    expect(mockNotifications.success).not.toHaveBeenCalledWith(
      expect.stringContaining('Group settings saved — M3U refresh started'),
      'M3U Groups'
    );
  });

  it('#9 / NIT 6: warns with stale-specific guidance (no false auto-retry) when the selection is fully stale', async () => {
    vi.mocked(api.updateM3UGroupSettings).mockResolvedValue({
      message: 'ok',
      ecm_profile_apply: [{ status: 'stale_selection', group_id: 100 }],
    });

    await toggleEnabledAndSave();

    await waitFor(() =>
      expect(mockNotifications.warning).toHaveBeenCalledWith(
        expect.stringContaining('no longer exist'),
        'M3U Groups'
      )
    );
  });

  it('follow-up: degraded apply warns that profiles could not be fully enforced', async () => {
    vi.mocked(api.updateM3UGroupSettings).mockResolvedValue({
      message: 'ok',
      ecm_profile_apply: [{ status: 'degraded', group_id: 100 }],
    });

    await toggleEnabledAndSave();

    await waitFor(() =>
      expect(mockNotifications.warning).toHaveBeenCalledWith(
        expect.stringContaining('could not be fully enforced'),
        'M3U Groups'
      )
    );
  });

  it('follow-up: a cross-account conflict warns with normalize guidance', async () => {
    vi.mocked(api.updateM3UGroupSettings).mockResolvedValue({
      message: 'ok',
      ecm_profile_apply: [{ status: 'reconciled', group_id: 100, conflict: true }],
    });

    await toggleEnabledAndSave();

    await waitFor(() =>
      expect(mockNotifications.warning).toHaveBeenCalledWith(
        expect.stringContaining('conflicting profile selections'),
        'M3U Groups'
      )
    );
  });

  it('#9: shows plain success when the profile apply is clean', async () => {
    vi.mocked(api.updateM3UGroupSettings).mockResolvedValue({
      message: 'ok',
      ecm_profile_apply: [{ status: 'reconciled', group_id: 100 }],
    });

    await toggleEnabledAndSave();

    await waitFor(() =>
      expect(mockNotifications.success).toHaveBeenCalledWith(
        expect.stringContaining('Group settings saved'),
        'M3U Groups'
      )
    );
  });
});

/**
 * Bead enhancedchannelmanager-09x38.7 — dirty-guard on close.
 *
 * Confirmed live: toggling a group's Enabled/Auto-Sync tracks dirty state
 * (enables Save & Refresh) but closing via the X button or Escape discarded
 * all pending changes instantly with zero confirmation. There is no Cancel
 * button in this modal, only X and Save & Refresh. Outside-click close does
 * NOT exist for this modal — ModalOverlay disables backdrop-click-to-close
 * by default and M3UGroupsModal never opts in (no `onClick` passed to
 * ModalOverlay) — so there is no third path to cover here.
 *
 * Fix: both the X button and Escape now route through a shared
 * handleRequestClose that, when dirty, calls window.confirm('Discard
 * unsaved changes?') before closing. Declining the confirm leaves the modal
 * open with in-progress toggle state intact. A clean close (no changes)
 * remains instant with no confirm call. Save & Refresh is untouched — it
 * never goes through handleRequestClose.
 */
describe('M3UGroupsModal — dirty-guard on close (bead 09x38.7)', () => {
  const account = makeAccount();

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getM3UAccount).mockResolvedValue(account);
    vi.mocked(api.getChannelGroups).mockResolvedValue([
      { id: 100, name: 'Sports HD' } as never,
    ]);
    vi.mocked(api.updateM3UGroupSettings).mockResolvedValue({ message: 'ok' });
    vi.mocked(api.refreshM3UAccount).mockResolvedValue({ success: true, message: 'started' });
    // Default confirm to true; individual tests override to exercise decline.
    vi.spyOn(window, 'confirm').mockReturnValue(true);
  });

  async function renderDirtied(onClose = vi.fn()) {
    render(
      <M3UGroupsModal
        isOpen={true}
        onClose={onClose}
        onSaved={vi.fn()}
        account={account}
        allAccounts={[account]}
      />
    );

    await screen.findByText('Sports HD');
    const row = screen.getByText('Sports HD').closest('.group-row') as HTMLElement;
    const enabledCell = row.querySelector('.group-enabled') as HTMLElement;
    const enabledCheckbox = within(enabledCell).getByRole('checkbox') as HTMLInputElement;

    const user = userEvent.setup();
    await user.click(enabledCheckbox); // dirty: was true, now false
    expect(enabledCheckbox.checked).toBe(false);

    return { onClose, enabledCheckbox };
  }

  it('clean close (no changes) via X closes instantly with no confirm prompt', async () => {
    const onClose = vi.fn();
    render(
      <M3UGroupsModal
        isOpen={true}
        onClose={onClose}
        onSaved={vi.fn()}
        account={account}
        allAccounts={[account]}
      />
    );
    await screen.findByText('Sports HD');

    fireEvent.click(screen.getByLabelText('Close'));

    expect(window.confirm).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('clean close (no changes) via Escape closes instantly with no confirm prompt', async () => {
    const onClose = vi.fn();
    render(
      <M3UGroupsModal
        isOpen={true}
        onClose={onClose}
        onSaved={vi.fn()}
        account={account}
        allAccounts={[account]}
      />
    );
    await screen.findByText('Sports HD');

    fireEvent.keyDown(document, { key: 'Escape' });

    expect(window.confirm).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('dirty + X-close prompts "Discard unsaved changes?" and discards on confirm', async () => {
    const { onClose } = await renderDirtied();

    fireEvent.click(screen.getByLabelText('Close'));

    expect(window.confirm).toHaveBeenCalledWith('Discard unsaved changes?');
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('dirty + X-close: declining the confirm keeps the modal open with state intact', async () => {
    vi.mocked(window.confirm).mockReturnValue(false);
    const { onClose, enabledCheckbox } = await renderDirtied();

    fireEvent.click(screen.getByLabelText('Close'));

    expect(window.confirm).toHaveBeenCalledWith('Discard unsaved changes?');
    expect(onClose).not.toHaveBeenCalled();
    // Still rendered, still showing the dirtied (unchecked) state.
    expect(screen.getByText('Sports HD')).toBeInTheDocument();
    expect(enabledCheckbox.checked).toBe(false);
  });

  it('dirty + Escape prompts "Discard unsaved changes?" and discards on confirm', async () => {
    const { onClose } = await renderDirtied();

    fireEvent.keyDown(document, { key: 'Escape' });

    expect(window.confirm).toHaveBeenCalledWith('Discard unsaved changes?');
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('dirty + Escape: declining the confirm keeps the modal open with state intact', async () => {
    vi.mocked(window.confirm).mockReturnValue(false);
    const { onClose, enabledCheckbox } = await renderDirtied();

    fireEvent.keyDown(document, { key: 'Escape' });

    expect(window.confirm).toHaveBeenCalledWith('Discard unsaved changes?');
    expect(onClose).not.toHaveBeenCalled();
    expect(enabledCheckbox.checked).toBe(false);
  });

  it('Save & Refresh is unaffected by the dirty-guard — no confirm prompt, saves and closes', async () => {
    const { onClose } = await renderDirtied();

    fireEvent.click(screen.getByRole('button', { name: /Save & Refresh/i }));

    await waitFor(() => expect(api.updateM3UGroupSettings).toHaveBeenCalled());
    expect(window.confirm).not.toHaveBeenCalled();
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
  });
});
