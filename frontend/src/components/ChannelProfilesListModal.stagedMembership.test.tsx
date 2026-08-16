/**
 * The Profiles modal obeys Edit Mode's staging promise for the data it shares
 * with the selection bar (bead enhancedchannelmanager-kz089, fix round 2).
 *
 * Round 1 made the selection bar's "Profile visibility" stage. Opening
 * **Profiles** from the toolbar menu reached the same data by a different route
 * and wrote it immediately, through BOTH of its channel-assignment buttons:
 * "Save Changes" PATCHed each membership, and "Apply to Selected" used the bulk
 * endpoint. Neither added to the staged count and Discard could not reverse
 * either.
 *
 * Per the PO's 2026-08-15 decision, membership stages and profile create /
 * rename / delete stays immediate — a staged membership operation needs a real
 * profile id to reference — so the modal says so at the point of action instead.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ChannelProfilesListModal } from './ChannelProfilesListModal';
import { NotificationProvider } from '../contexts/NotificationContext';
import { profileMembershipKey } from '../types/editMode';
import type { StagedSideEffects } from '../types';
import * as api from '../services/api';
import type { Channel, ChannelGroup, ChannelProfile } from '../types';

const PROFILE_ID = 3;
const GROUP_ID = 10;

function makeChannel(id: number, name: string): Channel {
  return {
    id,
    channel_number: id,
    name,
    channel_group_id: GROUP_ID,
    tvg_id: null,
    tvc_guide_stationid: null,
    epg_data_id: null,
    streams: [],
    stream_profile_id: null,
    uuid: `uuid-${id}`,
    logo_id: null,
    auto_created: false,
    auto_created_by: null,
    auto_created_by_name: null,
  };
}

const CHANNELS = [makeChannel(1, 'Alpha'), makeChannel(2, 'Bravo')];
const GROUPS: ChannelGroup[] = [{ id: GROUP_ID, name: 'Sports', channel_count: 2 }];

/** Channel 1 enabled in the profile, channel 2 not. */
const PROFILE = { id: PROFILE_ID, name: 'Kids', channels: [1] } as unknown as ChannelProfile;

function emptySideEffects(): StagedSideEffects {
  return {
    profileMembership: new Map(),
    restoredGroupIds: new Set(),
    clearedStreamIds: new Set(),
  };
}

interface Staged {
  profileId: number;
  channelIds: number[];
  enabled: boolean;
}

function renderModal(overrides: Partial<React.ComponentProps<typeof ChannelProfilesListModal>> = {}) {
  const staged: Staged[] = [];
  const view = render(
    <NotificationProvider>
      <ChannelProfilesListModal
        isOpen
        onClose={vi.fn()}
        onSaved={vi.fn()}
        channels={CHANNELS}
        channelGroups={GROUPS}
        isEditMode
        stagedSideEffects={emptySideEffects()}
        onStageSetProfileMembership={(profileId, channelIds, enabled) =>
          staged.push({ profileId, channelIds, enabled })}
        onStartBatch={vi.fn()}
        onEndBatch={vi.fn()}
        {...overrides}
      />
    </NotificationProvider>,
  );
  return { staged, view };
}

/** Walk from the profile list into a profile's channel-assignment view. */
async function openChannels(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: 'Manage channels' }));
  await screen.findByText('Manage Channels');
}

beforeEach(() => {
  vi.spyOn(api, 'getChannelProfiles').mockResolvedValue([PROFILE]);
  vi.spyOn(api, 'getChannelProfile').mockResolvedValue(PROFILE);
  vi.spyOn(api, 'updateProfileChannel').mockResolvedValue({ success: true });
  vi.spyOn(api, 'bulkUpdateProfileChannels').mockResolvedValue({
    success: true,
  } as unknown as Awaited<ReturnType<typeof api.bulkUpdateProfileChannels>>);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('Save Changes in Edit Mode', () => {
  it('stages the membership diff instead of PATCHing it', async () => {
    const user = userEvent.setup();
    const { staged } = renderModal();
    await openChannels(user);

    // Toggle channel 2 on (it starts disabled) and channel 1 off.
    await user.click(screen.getByText('Bravo'));
    await user.click(screen.getByText('Alpha'));
    await user.click(screen.getByRole('button', { name: /Stage Changes/ }));

    expect(api.updateProfileChannel).not.toHaveBeenCalled();
    expect(staged).toEqual(
      expect.arrayContaining([
        { profileId: PROFILE_ID, channelIds: [2], enabled: true },
        { profileId: PROFILE_ID, channelIds: [1], enabled: false },
      ]),
    );
  });

  it('still writes immediately when Edit Mode is off', async () => {
    const user = userEvent.setup();
    const { staged } = renderModal({ isEditMode: false });
    await openChannels(user);

    await user.click(screen.getByText('Bravo'));
    await user.click(screen.getByRole('button', { name: /Save Changes/ }));

    await waitFor(() => expect(api.updateProfileChannel).toHaveBeenCalled());
    expect(staged).toEqual([]);
  });
});

describe('Apply to Selected in Edit Mode', () => {
  it('stages instead of calling the bulk endpoint', async () => {
    const user = userEvent.setup();
    const { staged } = renderModal();
    await openChannels(user);

    await user.click(screen.getByLabelText('Select Alpha for bulk apply'));
    await user.click(screen.getByRole('button', { name: /Stage to Selected: Disable/ }));

    expect(api.bulkUpdateProfileChannels).not.toHaveBeenCalled();
    expect(staged).toEqual([
      { profileId: PROFILE_ID, channelIds: [1], enabled: false },
    ]);
  });

  it('still writes immediately when Edit Mode is off', async () => {
    const user = userEvent.setup();
    const { staged } = renderModal({ isEditMode: false });
    await openChannels(user);

    await user.click(screen.getByLabelText('Select Alpha for bulk apply'));
    await user.click(screen.getByRole('button', { name: /Apply to Selected: Disable/ }));

    await waitFor(() => expect(api.bulkUpdateProfileChannels).toHaveBeenCalled());
    expect(staged).toEqual([]);
  });
});

describe('the working-copy representation of a staged membership', () => {
  it('shows the channel as the staged operation left it, not as the server has it', async () => {
    const user = userEvent.setup();
    const sideEffects = emptySideEffects();
    // Channel 1 is enabled upstream and staged for disable; channel 2 the
    // reverse. Counted-but-invisible is the defect this asserts against.
    sideEffects.profileMembership.set(profileMembershipKey(PROFILE_ID, 1), false);
    sideEffects.profileMembership.set(profileMembershipKey(PROFILE_ID, 2), true);
    renderModal({ stagedSideEffects: sideEffects });
    await openChannels(user);

    const alphaRow = screen.getByText('Alpha').closest('.channel-item')!;
    const bravoRow = screen.getByText('Bravo').closest('.channel-item')!;
    expect(alphaRow.className).not.toContain('enabled');
    expect(bravoRow.className).toContain('enabled');
    // The header count reads the same working copy.
    expect(screen.getByText(`1 / ${CHANNELS.length} enabled`)).toBeInTheDocument();
  });

  it('reverts with the staged operations, because it is derived from them', async () => {
    const user = userEvent.setup();
    const sideEffects = emptySideEffects();
    sideEffects.profileMembership.set(profileMembershipKey(PROFILE_ID, 1), false);
    const { view } = renderModal({ stagedSideEffects: sideEffects });
    await openChannels(user);
    expect(screen.getByText('Alpha').closest('.channel-item')!.className)
      .not.toContain('enabled');

    // Discard empties the operation queue, so the derived view empties too.
    view.rerender(
      <NotificationProvider>
        <ChannelProfilesListModal
          isOpen
          onClose={vi.fn()}
          onSaved={vi.fn()}
          channels={CHANNELS}
          channelGroups={GROUPS}
          isEditMode
          stagedSideEffects={emptySideEffects()}
          onStageSetProfileMembership={vi.fn()}
        />
      </NotificationProvider>,
    );

    await waitFor(() =>
      expect(screen.getByText('Alpha').closest('.channel-item')!.className)
        .toContain('enabled'));
  });
});

describe('profile create / rename / delete', () => {
  it('says at the point of action that it applies immediately', async () => {
    renderModal();

    const note = await screen.findByTestId('profile-admin-immediate-note');
    expect(within(note).getByText(/applies immediately/)).toBeInTheDocument();
    expect(note.textContent).toContain('Discard will not undo it');
    // And it distinguishes itself from the membership editing that DOES stage,
    // so the sentence cannot be read as covering the whole modal.
    expect(note.textContent).toContain('does stage');
  });

  it('stays immediate — creating a profile is a real write', async () => {
    const created = { id: 9, name: 'New', channels: [] } as unknown as ChannelProfile;
    const createSpy = vi.spyOn(api, 'createChannelProfile').mockResolvedValue(created);
    renderModal();

    fireEvent.change(await screen.findByPlaceholderText('New profile name...'), {
      target: { value: 'New' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(createSpy).toHaveBeenCalledWith({ name: 'New' }));
  });

  it('shows no such note outside Edit Mode, where nothing promises staging', async () => {
    renderModal({ isEditMode: false });

    await screen.findByPlaceholderText('New profile name...');
    expect(screen.queryByTestId('profile-admin-immediate-note')).not.toBeInTheDocument();
  });
});
