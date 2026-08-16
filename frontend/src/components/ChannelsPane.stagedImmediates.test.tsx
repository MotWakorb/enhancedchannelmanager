/**
 * Edit Mode's staged-vs-immediate semantics, at the affordances
 * (bead enhancedchannelmanager-kz089, folding in enhancedchannelmanager-i4yk1).
 *
 * Edit Mode presents itself as a staging area. Eleven of its actions behaved
 * that way and ten wrote through to the server the moment they were clicked,
 * with nothing on screen distinguishing them — and three delete dialogs
 * actively promised "Changes can be undone while in edit mode" while their
 * toolbar neighbours committed immediately.
 *
 * The PO's decision was a hybrid, not "label everything" and not "stage
 * everything":
 *
 *  - the cheap, local writers stage, so they behave like their neighbours;
 *  - Merge and Import CSV, accepted as genuine staging exceptions, say plainly
 *    at the point of action that they apply immediately;
 *  - the three delete dialogs stop making a promise about the MODE and make one
 *    about their own button instead.
 *
 * These tests cover the affordance side. The queue-level contract (counted,
 * undoable, discardable, sent only at Apply All) is in
 * `hooks/useEditMode.stagedImmediates.test.ts`.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ChannelsPane, EDIT_MODE_DELETE_STAGED_NOTE } from './ChannelsPane';
import { NotificationProvider } from '../contexts/NotificationContext';
import * as api from '../services/api';
import type {
  Channel,
  ChannelGroup,
  ChannelListFilterSettings,
  ChannelProfile,
} from '../types';

const GROUP_ID = 10;
const PROFILE_ID = 3;

function makeFilters(): ChannelListFilterSettings {
  return {
    showEmptyGroups: true,
    showNewlyCreatedGroups: true,
    showProviderGroups: true,
    showManualGroups: true,
    showAutoChannelGroups: true,
  };
}

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

const GROUPS: ChannelGroup[] = [{ id: GROUP_ID, name: 'Sports', channel_count: 2 }];
const CHANNELS = [makeChannel(1, 'Alpha'), makeChannel(2, 'Bravo')];
const PROFILES = [
  { id: PROFILE_ID, name: 'Kids', channels: [] },
] as unknown as ChannelProfile[];

interface Recorder {
  profileMembership: { profileId: number; channelIds: number[]; enabled: boolean }[];
  restoredGroups: number[];
  clearedStats: number[][];
  updates: { channelId: number; data: Partial<Channel> }[];
}

function renderPane(overrides: Partial<React.ComponentProps<typeof ChannelsPane>> = {}) {
  const rec: Recorder = {
    profileMembership: [], restoredGroups: [], clearedStats: [], updates: [],
  };
  render(
    <NotificationProvider>
      <ChannelsPane
        channelGroups={GROUPS}
        channels={CHANNELS}
        streams={[]}
        providers={[]}
        selectedChannelId={null}
        onChannelSelect={vi.fn()}
        onChannelUpdate={vi.fn()}
        onChannelDrop={vi.fn()}
        onBulkStreamDrop={vi.fn()}
        onChannelReorder={vi.fn()}
        onCreateChannel={vi.fn()}
        onDeleteChannel={vi.fn()}
        searchTerm=""
        onSearchChange={vi.fn()}
        selectedGroups={[GROUP_ID]}
        onSelectedGroupsChange={vi.fn()}
        loading={false}
        autoRenameChannelNumber={false}
        isEditMode
        selectedChannelIds={new Set<number>([1, 2])}
        onClearChannelSelection={vi.fn()}
        channelListFilters={makeFilters()}
        onChannelListFiltersChange={vi.fn()}
        channelProfiles={PROFILES}
        onStartBatch={vi.fn()}
        onEndBatch={vi.fn()}
        onStageUpdateChannel={(channelId, data) => rec.updates.push({ channelId, data })}
        onStageSetProfileMembership={(profileId, channelIds, enabled) =>
          rec.profileMembership.push({ profileId, channelIds, enabled })}
        onStageRestoreChannelGroup={(groupId) => rec.restoredGroups.push(groupId)}
        onStageClearStreamStats={(streamIds) => rec.clearedStats.push(streamIds)}
        {...overrides}
      />
    </NotificationProvider>,
  );
  return rec;
}

beforeEach(() => {
  vi.spyOn(api, 'updateProfileChannel').mockResolvedValue({ success: true });
  vi.spyOn(api, 'restoreChannelGroup').mockResolvedValue(undefined);
  vi.spyOn(api, 'updateChannel').mockResolvedValue(CHANNELS[0]);
  vi.spyOn(api, 'getHiddenChannelGroups').mockResolvedValue([
    { id: 77, name: 'Archived Locals', hidden_at: '2026-08-01T00:00:00Z' },
  ]);
});

afterEach(() => {
  vi.restoreAllMocks();
});

/** Open the floating selection bar's "More selection actions" menu. */
async function openSelectionMenu(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: 'More selection actions' }));
}

describe('profile visibility from the selection bar', () => {
  it('stages instead of PATCHing every membership immediately', async () => {
    const user = userEvent.setup();
    const rec = renderPane();

    await openSelectionMenu(user);
    await user.click(screen.getByRole('menuitem', { name: 'Profile visibility' }));
    await user.click(screen.getByRole('menuitem', { name: 'Disable selected channels in profile Kids' }));

    expect(rec.profileMembership).toEqual([
      { profileId: PROFILE_ID, channelIds: [1, 2], enabled: false },
    ]);
    expect(api.updateProfileChannel).not.toHaveBeenCalled();
  });

  it('still writes immediately when Edit Mode is off', async () => {
    const user = userEvent.setup();
    // Outside Edit Mode there is no staging promise to keep, and the selection
    // bar itself is Edit-Mode-only — so the fallback is exercised by dropping
    // the staging hook rather than by leaving the mode.
    const rec = renderPane({ onStageSetProfileMembership: undefined });

    await openSelectionMenu(user);
    await user.click(screen.getByRole('menuitem', { name: 'Profile visibility' }));
    await user.click(screen.getByRole('menuitem', { name: 'Enable selected channels in profile Kids' }));

    await waitFor(() => expect(api.updateProfileChannel).toHaveBeenCalled());
    expect(rec.profileMembership).toEqual([]);
  });
});

describe('Set Logo from M3U / EPG', () => {
  it('stages the logo assignment rather than PATCHing each channel', async () => {
    const user = userEvent.setup();
    vi.spyOn(api, 'getChannelStreams').mockResolvedValue([
      { id: 99, name: 'S', logo_url: 'http://logo/a.png' },
    ] as unknown as Awaited<ReturnType<typeof api.getChannelStreams>>);
    vi.spyOn(api, 'getOrCreateLogo').mockResolvedValue({
      id: 42, name: 'a', url: 'http://logo/a.png',
    } as unknown as Awaited<ReturnType<typeof api.getOrCreateLogo>>);
    const rec = renderPane();

    await openSelectionMenu(user);
    await user.click(screen.getByRole('menuitem', { name: /Set Logo from M3U/ }));

    await waitFor(() => expect(rec.updates.length).toBe(2));
    expect(rec.updates).toEqual([
      { channelId: 1, data: { logo_id: 42 } },
      { channelId: 2, data: { logo_id: 42 } },
    ]);
    // The channel write is what had to stop. Resolving the URL to a Logo row
    // still happens, because the picker needs a real id and the logo catalog
    // is additive.
    expect(api.updateChannel).not.toHaveBeenCalled();
  });
});

describe('restoring a hidden group', () => {
  it('stages the restore instead of POSTing it', async () => {
    const user = userEvent.setup();
    const rec = renderPane();

    await user.click(screen.getAllByRole('button', { name: 'More actions' })[0]);
    await user.click(screen.getByRole('menuitem', { name: /Hidden Groups/ }));
    const restore = await screen.findByRole('button', { name: /Restore/i });
    await user.click(restore);

    expect(rec.restoredGroups).toEqual([77]);
    expect(api.restoreChannelGroup).not.toHaveBeenCalled();
  });
});

describe('the three delete dialogs', () => {
  it('no longer promise that changes can be undone while in edit mode', () => {
    renderPane();
    // The sentence is gone from the module's copy entirely — it was a claim
    // about the MODE, made in the one place ECM could least keep it.
    expect(EDIT_MODE_DELETE_STAGED_NOTE).not.toContain('Changes can be undone while in edit mode');
  });

  it('say what this delete does and how to reverse it', () => {
    expect(EDIT_MODE_DELETE_STAGED_NOTE).toContain('staged');
    expect(EDIT_MODE_DELETE_STAGED_NOTE).toContain('Apply All');
    expect(EDIT_MODE_DELETE_STAGED_NOTE.toLowerCase()).toContain('undo');
    expect(EDIT_MODE_DELETE_STAGED_NOTE.toLowerCase()).toContain('discard');
  });

  it('shows that note on the bulk delete confirmation', async () => {
    const user = userEvent.setup();
    renderPane();

    await user.click(screen.getByTitle('Delete selected channels'));

    expect(screen.getByText(EDIT_MODE_DELETE_STAGED_NOTE)).toBeInTheDocument();
  });
});
