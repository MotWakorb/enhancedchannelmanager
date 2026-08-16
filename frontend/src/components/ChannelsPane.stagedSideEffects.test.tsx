/**
 * Edit Mode shows what is staged, and takes it back when the staging is undone
 * (bead enhancedchannelmanager-kz089, fix round 2).
 *
 * Round 1 staged Clear Stream Stats and Restore Hidden Group, and made each
 * disappear from the UI by mutating component-local state — a `Map.delete` and
 * an array filter that `discard` and `localUndo` cannot reach. So Discard
 * dropped the change count while the stats stayed visually gone and the group
 * stayed removed, and Redo could not reapply them.
 *
 * The reviewer noted this is exactly the defect the missing clear-stats DOM test
 * was hiding, so the clear-stats affordance is exercised here at the DOM rather
 * than only through the staging callback.
 *
 * Also covers the probe immediacy statement. Probing writes stream stats the
 * instant it finishes while CLEARING the same data stages; per the PO's
 * 2026-08-15 decision probing stays immediate — a probe result staged and
 * applied forty minutes later would be worse than none — so Edit Mode's rule is
 * met by saying so at the point of action, without a mandatory acknowledgement
 * on a routine diagnostic.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ChannelsPane } from './ChannelsPane';
import { NotificationProvider } from '../contexts/NotificationContext';
import * as api from '../services/api';
import type {
  Channel,
  ChannelGroup,
  ChannelListFilterSettings,
  StagedSideEffects,
  Stream,
  StreamStats,
} from '../types';

const GROUP_ID = 10;
const CHANNEL_ID = 1;
const STREAM_ID = 11;
const HIDDEN_GROUP_ID = 77;

function makeFilters(): ChannelListFilterSettings {
  return {
    showEmptyGroups: true,
    showNewlyCreatedGroups: true,
    showProviderGroups: true,
    showManualGroups: true,
    showAutoChannelGroups: true,
  };
}

const CHANNEL: Channel = {
  id: CHANNEL_ID,
  channel_number: 1,
  name: 'Alpha',
  channel_group_id: GROUP_ID,
  tvg_id: null,
  tvc_guide_stationid: null,
  epg_data_id: null,
  streams: [STREAM_ID],
  stream_profile_id: null,
  uuid: 'uuid-1',
  logo_id: null,
  auto_created: false,
  auto_created_by: null,
  auto_created_by_name: null,
};

const STREAM = { id: STREAM_ID, name: 'Stream A', url: null, m3u_account: null } as unknown as Stream;
const GROUPS: ChannelGroup[] = [{ id: GROUP_ID, name: 'Sports', channel_count: 1 }];

/** A probe that FAILED — the only state that renders the reset-stats button. */
const FAILED_STATS = {
  stream_id: STREAM_ID,
  probe_status: 'failed',
  strike_count: 1,
} as unknown as StreamStats;

function emptySideEffects(): StagedSideEffects {
  return {
    profileMembership: new Map(),
    restoredGroupIds: new Set(),
    clearedStreamIds: new Set(),
  };
}

interface Recorder {
  clearedStats: number[][];
  restoredGroups: number[];
}

function paneProps(rec: Recorder, overrides: Record<string, unknown>) {
  return {
    channelGroups: GROUPS,
    channels: [CHANNEL],
    streams: [STREAM],
    providers: [],
    selectedChannelId: CHANNEL_ID,
    onChannelSelect: vi.fn(),
    onChannelUpdate: vi.fn(),
    onChannelDrop: vi.fn(),
    onBulkStreamDrop: vi.fn(),
    onChannelReorder: vi.fn(),
    onCreateChannel: vi.fn(),
    onDeleteChannel: vi.fn(),
    searchTerm: '',
    onSearchChange: vi.fn(),
    selectedGroups: [GROUP_ID],
    onSelectedGroupsChange: vi.fn(),
    loading: false,
    autoRenameChannelNumber: false,
    isEditMode: true,
    selectedChannelIds: new Set<number>(),
    onClearChannelSelection: vi.fn(),
    channelListFilters: makeFilters(),
    onChannelListFiltersChange: vi.fn(),
    stagedSideEffects: emptySideEffects(),
    onStageClearStreamStats: (streamIds: number[]) => rec.clearedStats.push(streamIds),
    onStageRestoreChannelGroup: (groupId: number) => rec.restoredGroups.push(groupId),
    ...overrides,
  } as unknown as React.ComponentProps<typeof ChannelsPane>;
}

function renderPane(overrides: Record<string, unknown> = {}) {
  const rec: Recorder = { clearedStats: [], restoredGroups: [] };
  const view = render(
    <NotificationProvider>
      <ChannelsPane {...paneProps(rec, overrides)} />
    </NotificationProvider>,
  );
  const rerender = (next: Record<string, unknown> = {}) =>
    view.rerender(
      <NotificationProvider>
        <ChannelsPane {...paneProps(rec, { ...overrides, ...next })} />
      </NotificationProvider>,
    );
  return { rec, rerender };
}

/** Channel rows only exist once their group is expanded. */
async function expandGroup(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: 'Expand all groups' }));
  await screen.findByText('Alpha');
}

beforeEach(() => {
  vi.spyOn(api, 'getStreamStatsByIds').mockResolvedValue(
    { [STREAM_ID]: FAILED_STATS } as unknown as Awaited<ReturnType<typeof api.getStreamStatsByIds>>,
  );
  vi.spyOn(api, 'getStaleStreamIds').mockResolvedValue(
    { stale_stream_ids: [], last_seen: null, count: 0 } as unknown as Awaited<ReturnType<typeof api.getStaleStreamIds>>,
  );
  vi.spyOn(api, 'clearStreamStats').mockResolvedValue({ cleared: 1, stream_ids: [STREAM_ID] });
  vi.spyOn(api, 'restoreChannelGroup').mockResolvedValue(undefined);
  vi.spyOn(api, 'getHiddenChannelGroups').mockResolvedValue([
    { id: HIDDEN_GROUP_ID, name: 'Archived Locals', hidden_at: '2026-08-01T00:00:00Z' },
  ]);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('clearing probe stats from the stream row', () => {
  it('stages the clear instead of writing it, from the affordance itself', async () => {
    const user = userEvent.setup();
    const { rec } = renderPane();
    await expandGroup(user);

    const reset = await screen.findByRole('button', { name: 'Reset probe status' });
    await user.click(reset);

    expect(rec.clearedStats).toEqual([[STREAM_ID]]);
    expect(api.clearStreamStats).not.toHaveBeenCalled();
  });

  it('hides the stats while the clear is staged', async () => {
    const user = userEvent.setup();
    const staged = emptySideEffects();
    staged.clearedStreamIds.add(STREAM_ID);
    renderPane({ stagedSideEffects: staged });
    await expandGroup(user);

    // The reset button only renders for a stream that HAS a failed probe, so
    // its absence is the row reading as never probed.
    await screen.findByText('Stream A');
    expect(screen.queryByRole('button', { name: 'Reset probe status' })).not.toBeInTheDocument();
  });

  it('brings the stats back when the staged clear goes away', async () => {
    const user = userEvent.setup();
    const staged = emptySideEffects();
    staged.clearedStreamIds.add(STREAM_ID);
    const { rerender } = renderPane({ stagedSideEffects: staged });
    await expandGroup(user);
    await screen.findByText('Stream A');
    expect(screen.queryByRole('button', { name: 'Reset probe status' })).not.toBeInTheDocument();

    // Discard (or Undo) empties the operation queue, so the derived view empties
    // too. This is what component-local state could not do.
    rerender({ stagedSideEffects: emptySideEffects() });

    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Reset probe status' })).toBeInTheDocument());
  });

  it('still writes immediately when Edit Mode is off', async () => {
    const user = userEvent.setup();
    const { rec } = renderPane({ isEditMode: false });
    await expandGroup(user);

    await user.click(await screen.findByRole('button', { name: 'Reset probe status' }));

    await waitFor(() => expect(api.clearStreamStats).toHaveBeenCalledWith([STREAM_ID]));
    expect(rec.clearedStats).toEqual([]);
  });
});

describe('restoring a hidden group', () => {
  async function openHiddenGroups(user: ReturnType<typeof userEvent.setup>) {
    await user.click(screen.getAllByRole('button', { name: 'More actions' })[0]);
    await user.click(screen.getByRole('menuitem', { name: /Hidden Groups/ }));
  }

  it('drops the row while the restore is staged', async () => {
    const user = userEvent.setup();
    const { rec } = renderPane();

    await openHiddenGroups(user);
    await user.click(await screen.findByRole('button', { name: /Restore/i }));

    expect(rec.restoredGroups).toEqual([HIDDEN_GROUP_ID]);
    expect(api.restoreChannelGroup).not.toHaveBeenCalled();
  });

  it('brings the row back when the staged restore goes away', async () => {
    const user = userEvent.setup();
    const staged = emptySideEffects();
    staged.restoredGroupIds.add(HIDDEN_GROUP_ID);
    const { rerender } = renderPane({ stagedSideEffects: staged });

    await openHiddenGroups(user);
    expect(await screen.findByText('No hidden groups')).toBeInTheDocument();

    rerender({ stagedSideEffects: emptySideEffects() });

    await waitFor(() => expect(screen.getByText('Archived Locals')).toBeInTheDocument());
  });
});

describe('the probe immediacy statement', () => {
  it('appears in the per-channel actions menu in Edit Mode', async () => {
    const user = userEvent.setup();
    renderPane();
    await expandGroup(user);

    await user.click(screen.getAllByRole('button', { name: 'Channel actions' })[0]);

    const note = await screen.findByTestId('probe-immediate-note');
    expect(note.textContent).toContain('applies immediately');
    expect(note.textContent).toContain('Discard will not undo it');
  });

  it('is absent outside Edit Mode, where nothing promises staging', async () => {
    const user = userEvent.setup();
    renderPane({ isEditMode: false });
    await expandGroup(user);

    await user.click(screen.getAllByRole('button', { name: 'Channel actions' })[0]);

    await screen.findByRole('menuitem', { name: /Probe Channel/ });
    expect(screen.queryByTestId('probe-immediate-note')).not.toBeInTheDocument();
  });

  it('appears in the group actions menu, which is Edit Mode only', async () => {
    // Third probe entry point. The review named the per-channel and bulk
    // probes; this one came out of re-enumerating Edit Mode's actions in fix
    // round 2, and it is the one whose whole menu exists only in Edit Mode.
    const user = userEvent.setup();
    renderPane();

    await user.click(screen.getByRole('button', { name: 'Group actions' }));

    const note = await screen.findByTestId('probe-immediate-note-group');
    expect(note.textContent).toContain('applies immediately');
    expect(note.textContent).toContain('Discard will not undo it');
  });

  it('appears beside the bulk Probe button on the selection bar', async () => {
    renderPane({ selectedChannelIds: new Set([CHANNEL_ID]) });

    const note = await screen.findByTestId('probe-immediate-note-bulk');
    expect(note.textContent).toContain('applies immediately');
    // It must not claim the whole bar is immediate — everything else stages.
    expect(note.textContent).toContain('Everything else on this bar stages');
  });
});
