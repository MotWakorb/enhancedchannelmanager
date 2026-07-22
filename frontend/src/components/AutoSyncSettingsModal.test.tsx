/**
 * Unit tests for AutoSyncSettingsModal — bead enhancedchannelmanager-igqcy.
 *
 * Dispatcharr's group-settings upsert replaces custom_properties WHOLESALE,
 * and its auto-sync consumes keys this form does not model
 * (channel_numbering_mode, channel_numbering_fallback,
 * name_match_exclude_regex, force_dummy_epg — all post-v0.24). The previous
 * save handler rebuilt the object from {} using only ECM-known keys, silently
 * erasing everything else on the next save. These tests pin the fixed
 * contract: save starts from the group's CURRENT custom_properties and
 * overlays only the managed keys — unknown keys survive verbatim, and
 * clearing a managed field deletes exactly that key.
 *
 * The custom_properties shape mirrors the REAL v0.27.2 payload recorded
 * during the bd-478fe QA pass.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AutoSyncSettingsModal } from './AutoSyncSettingsModal';
import type { AutoSyncCustomProperties } from '../types';

vi.mock('../services/api', () => ({
  getLogos: vi.fn().mockResolvedValue({ results: [] }),
  createLogo: vi.fn(),
  createChannelGroup: vi.fn(),
}));

const mockNotifications = {
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
};

vi.mock('../contexts/NotificationContext', () => ({
  useNotifications: () => mockNotifications,
}));

// Real v0.27.2 custom_properties: one ECM-managed key plus keys ECM does
// not model, written by Dispatcharr's own UI.
const v0272CustomProperties: AutoSyncCustomProperties = {
  name_match_regex: '^ESPN.*',              // ECM-managed key
  channel_numbering_mode: 'assigned',       // unknown to ECM
  channel_numbering_fallback: 'next',       // unknown to ECM
  force_dummy_epg: true,                    // unknown to ECM
};

function renderModal(onSave: (props: AutoSyncCustomProperties) => void,
                     customProperties: AutoSyncCustomProperties | null = v0272CustomProperties) {
  return render(
    <AutoSyncSettingsModal
      isOpen={true}
      onClose={vi.fn()}
      onSave={onSave}
      groupName="Sports HD"
      customProperties={customProperties}
      epgSources={[]}
      channelGroups={[]}
      channelProfiles={[]}
      streamProfiles={[]}
    />
  );
}

describe('AutoSyncSettingsModal — custom_properties merge (bead igqcy)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('preserves unknown custom_properties keys verbatim through a save', async () => {
    const onSave = vi.fn();
    renderModal(onSave);

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /Save Settings/i }));

    expect(onSave).toHaveBeenCalledTimes(1);
    const saved = onSave.mock.calls[0][0] as AutoSyncCustomProperties;
    expect(saved.channel_numbering_mode).toBe('assigned');
    expect(saved.channel_numbering_fallback).toBe('next');
    expect(saved.force_dummy_epg).toBe(true);
    expect(saved.name_match_regex).toBe('^ESPN.*');
  });

  it('editing a managed field keeps unknown keys untouched', async () => {
    const onSave = vi.fn();
    renderModal(onSave);

    const user = userEvent.setup();
    const filterInput = screen.getByPlaceholderText('e.g., ^(ESPN|FOX).*') as HTMLInputElement;
    expect(filterInput.value).toBe('^ESPN.*');
    await user.clear(filterInput);
    await user.type(filterInput, '^FOX.*');
    await user.click(screen.getByRole('button', { name: /Save Settings/i }));

    const saved = onSave.mock.calls[0][0] as AutoSyncCustomProperties;
    expect(saved.name_match_regex).toBe('^FOX.*');
    expect(saved.channel_numbering_mode).toBe('assigned');
    expect(saved.force_dummy_epg).toBe(true);
  });

  it('clearing a managed field deletes exactly that key — unknown keys survive', async () => {
    const onSave = vi.fn();
    renderModal(onSave);

    const user = userEvent.setup();
    const filterInput = screen.getByPlaceholderText('e.g., ^(ESPN|FOX).*') as HTMLInputElement;
    await user.clear(filterInput);
    await user.click(screen.getByRole('button', { name: /Save Settings/i }));

    const saved = onSave.mock.calls[0][0] as AutoSyncCustomProperties;
    expect('name_match_regex' in saved).toBe(false);
    expect(saved.channel_numbering_mode).toBe('assigned');
    expect(saved.channel_numbering_fallback).toBe('next');
    expect(saved.force_dummy_epg).toBe(true);
  });

  it('Clear All removes only the managed keys — unknown keys survive', async () => {
    const onSave = vi.fn();
    renderModal(onSave);

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /Clear All/i }));
    await user.click(screen.getByRole('button', { name: /Save Settings/i }));

    const saved = onSave.mock.calls[0][0] as AutoSyncCustomProperties;
    expect('name_match_regex' in saved).toBe(false);
    expect('custom_epg_id' in saved).toBe(false);
    expect(saved.channel_numbering_mode).toBe('assigned');
    expect(saved.channel_numbering_fallback).toBe('next');
    expect(saved.force_dummy_epg).toBe(true);
  });
});

describe('AutoSyncSettingsModal — collision-prone section disambiguation (bead 09x38.8)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('keeps the three original section titles (no field removal/rename)', () => {
    renderModal(vi.fn());

    expect(screen.getByText('Channel Profile Assignment')).toBeInTheDocument();
    expect(screen.getByText('Stream Profile Assignment')).toBeInTheDocument();
    expect(screen.getByText('Channel Name Filter (Regex)')).toBeInTheDocument();
  });

  it('Channel Profile Assignment help text names the Dispatcharr entity and cross-references Manage Account Profiles', () => {
    renderModal(vi.fn());

    expect(screen.getByText(/Dispatcharr Channel Profiles \(client-facing visibility\)/i))
      .toBeInTheDocument();
    expect(screen.getByText(/Manage Account Profiles/i)).toBeInTheDocument();
  });

  it('Stream Profile Assignment help text explains the top-level Stream Profiles screen is read-only', () => {
    renderModal(vi.fn());

    expect(screen.getByText(/stream \(transcode\) profile/i)).toBeInTheDocument();
    expect(screen.getByText(/Stream Profiles.*read-only catalog/i)).toBeInTheDocument();
  });

  it('Channel Name Filter help text distinguishes group-scoped sync-time filtering from account-wide Manage Filters', () => {
    renderModal(vi.fn());

    expect(screen.getByText(/applied at sync time/i)).toBeInTheDocument();
    expect(screen.getByText(/Manage Filters/i)).toBeInTheDocument();
  });
});

describe('AutoSyncSettingsModal — profile-management relabel + guardrail copy (GH #720 Part B #9)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('zero profile selection reads as "Not managed by Auto-Sync", not delete-ish "None selected"', () => {
    // customProperties without channel_profile_ids -> empty selection.
    renderModal(vi.fn(), { name_match_regex: '^ESPN.*' });

    expect(screen.getByText('Not managed by Auto-Sync')).toBeInTheDocument();
    expect(screen.queryByText('None selected')).not.toBeInTheDocument();
  });

  it('the profile dropdown offers "Stop managing profiles" instead of "Clear All"', async () => {
    renderModal(vi.fn(), { name_match_regex: '^ESPN.*' });
    const user = userEvent.setup();

    // Open the Channel Profile Assignment dropdown.
    await user.click(screen.getByText('Not managed by Auto-Sync'));

    expect(screen.getByRole('button', { name: /Stop managing profiles/i })).toBeInTheDocument();
  });

  it('helper copy explains empty = ECM stops managing (memberships unchanged) and pipeline-owned channels are excluded', () => {
    renderModal(vi.fn(), { name_match_regex: '^ESPN.*' });

    expect(screen.getByText(/stops managing this group.*profiles and leaves existing memberships/i))
      .toBeInTheDocument();
    expect(screen.getByText(/set by a Channel Pipeline rule are excluded from/i))
      .toBeInTheDocument();
  });
});

describe('AutoSyncSettingsModal — profile picker accessibility + stale-selection state (GH #720 Part B)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  function renderWithProfiles(customProperties: AutoSyncCustomProperties | null) {
    return render(
      <AutoSyncSettingsModal
        isOpen={true}
        onClose={vi.fn()}
        onSave={vi.fn()}
        groupName="Sports HD"
        customProperties={customProperties}
        epgSources={[]}
        channelGroups={[]}
        channelProfiles={[
          { id: 1, name: 'Everyone' } as never,
          { id: 2, name: 'Kids' } as never,
          { id: 3, name: 'Sports' } as never,
        ]}
        streamProfiles={[]}
      />
    );
  }

  it('the profile trigger exposes an accessible name + haspopup, and opens an aria listbox', async () => {
    renderWithProfiles({ channel_profile_ids: ['1'] });
    const user = userEvent.setup();

    const trigger = screen.getByRole('button', { name: /Channel Profile Assignment/i });
    expect(trigger).toHaveAttribute('aria-haspopup', 'listbox');
    expect(trigger).toHaveAttribute('aria-expanded', 'false');

    await user.click(trigger);

    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    const listbox = screen.getByRole('listbox');
    expect(listbox).toHaveAttribute('aria-multiselectable', 'true');
    expect(screen.getAllByRole('option')).toHaveLength(3);
    // The selected profile is announced via aria-selected.
    expect(screen.getByRole('option', { name: /Everyone/i })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('option', { name: /Kids/i })).toHaveAttribute('aria-selected', 'false');
  });

  it('keyboard: Arrow to an option + Enter toggles it; Escape closes the listbox', async () => {
    const onSave = vi.fn();
    render(
      <AutoSyncSettingsModal
        isOpen={true} onClose={vi.fn()} onSave={onSave} groupName="Sports HD"
        customProperties={{}} epgSources={[]} channelGroups={[]}
        channelProfiles={[{ id: 1, name: 'Everyone' } as never, { id: 2, name: 'Kids' } as never]}
        streamProfiles={[]}
      />
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /Channel Profile Assignment/i }));

    const listbox = screen.getByRole('listbox');
    listbox.focus();
    await user.keyboard('{ArrowDown}');  // active -> Kids (index 1)
    await user.keyboard('{Enter}');       // toggle Kids on
    await user.keyboard('{Escape}');      // close

    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Save Settings/i }));
    const saved = onSave.mock.calls[0][0] as AutoSyncCustomProperties;
    expect(saved.channel_profile_ids).toEqual(['2']);
  });

  it('a selection referencing deleted profiles shows a clear stale-count state, not a blank picker', () => {
    // Selected ids 1 and 99; only profile 1 still exists -> 1 missing.
    renderWithProfiles({ channel_profile_ids: ['1', '99'] });

    expect(screen.getByRole('alert'))
      .toHaveTextContent(/1 previously-selected profile\(s\) no longer exist/i);
  });
});
