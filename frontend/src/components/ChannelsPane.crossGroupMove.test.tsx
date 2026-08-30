/**
 * Cross-group move: the whole staged result must be duplicate-free
 * (bead `enhancedchannelmanager-i85dg`, Codex pre-merge review finding 2).
 *
 * `handleCrossGroupMoveConfirm` stages three phases: the channels being moved
 * take their new numbers, `planChannelNumberShift` pushes whatever was already
 * on those numbers out of the way, and, when "Close gaps in source group" is
 * ticked, the channels left behind in the source group are compacted.
 *
 * The planner makes phases one and two duplicate-free. Phase three used to be
 * computed entirely outside it, against the PRE-move numbers, so it could
 * reassign a number the move had just claimed. Duplicate-freedom is the
 * property this bead exists to establish, and a duplicate produced two phases
 * later is the same defect, so it is pinned here at the level that can see all
 * three phases: render the pane, drive the real dialog, and read every staged
 * update back.
 *
 * Like the other ChannelsPane suites, this renders the pane directly with the
 * minimal prop set. ChannelsPane has no general-purpose test suite.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ChannelsPane } from './ChannelsPane';
import { NotificationProvider } from '../contexts/NotificationContext';
import type { Channel, ChannelGroup, ChannelListFilterSettings } from '../types';

const SOURCE_GROUP_ID = 10;
const TARGET_GROUP_ID = 20;

const groups: ChannelGroup[] = [
  { id: SOURCE_GROUP_ID, name: 'Sports', channel_count: 3 },
  { id: TARGET_GROUP_ID, name: 'News', channel_count: 1 },
];

function makeFilters(): ChannelListFilterSettings {
  return {
    showEmptyGroups: true,
    showNewlyCreatedGroups: true,
    showProviderGroups: true,
    showManualGroups: true,
    showAutoChannelGroups: true,
  };
}

function makeChannel(id: number, name: string, channelNumber: number, groupId: number): Channel {
  return {
    id,
    channel_number: channelNumber,
    name,
    channel_group_id: groupId,
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

interface StagedUpdate {
  channelId: number;
  data: Partial<Channel>;
}

function renderPane(channels: Channel[], selectedIds: number[]) {
  const staged: StagedUpdate[] = [];
  render(
    <NotificationProvider>
      <ChannelsPane
        channelGroups={groups}
        channels={channels}
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
        selectedGroups={[SOURCE_GROUP_ID, TARGET_GROUP_ID]}
        onSelectedGroupsChange={vi.fn()}
        loading={false}
        autoRenameChannelNumber={false}
        isEditMode
        selectedChannelIds={new Set(selectedIds)}
        onClearChannelSelection={vi.fn()}
        onStartBatch={vi.fn()}
        onEndBatch={vi.fn()}
        onStageUpdateChannel={(channelId, data) => {
          staged.push({ channelId, data });
        }}
        channelListFilters={makeFilters()}
        onChannelListFiltersChange={vi.fn()}
      />
    </NotificationProvider>,
  );
  return staged;
}

/**
 * Drive the real dialog: More → Move to group → "News", then pick a custom
 * starting number and tick "Close gaps in source group".
 */
async function moveToNewsWithCustomNumber(customNumber: string, closeSourceGaps: boolean) {
  const user = userEvent.setup();

  await user.click(screen.getByRole('button', { name: 'More selection actions' }));
  await user.click(document.querySelector('[data-submenu-trigger="move"]') as HTMLElement);

  const chooser = document.getElementById('selection-bar-move-chooser') as HTMLElement;
  expect(chooser).not.toBeNull();
  await user.click(within(chooser).getByText('News'));

  const dialog = document.querySelector('.cross-group-move-dialog') as HTMLElement;
  expect(dialog, 'cross-group move dialog did not open').not.toBeNull();

  const customRadio = within(dialog)
    .getByText('Custom starting number')
    .closest('label')!
    .querySelector('input[type="radio"]') as HTMLInputElement;
  await user.click(customRadio);

  const numberInput = dialog.querySelector('.custom-number-input-inline') as HTMLInputElement;
  expect(numberInput, 'custom starting number input did not appear').not.toBeNull();
  await user.type(numberInput, customNumber);

  if (closeSourceGaps) {
    const gapCheckbox = within(dialog)
      .getByText('Close gaps in source group')
      .closest('label')!
      .querySelector('input[type="checkbox"]') as HTMLInputElement;
    expect(gapCheckbox, '"Close gaps in source group" was not offered').not.toBeNull();
    await user.click(gapCheckbox);
  }

  await user.click(within(dialog).getByRole('button', { name: /^Move Channel$/ }));
}

/**
 * The number every channel ends on once the staged updates are applied in
 * order, which is what the operator will see and what the commit will send.
 */
function finalNumbers(channels: Channel[], staged: StagedUpdate[]): Map<number, number | null> {
  const finals = new Map(channels.map((ch) => [ch.id, ch.channel_number]));
  for (const { channelId, data } of staged) {
    if ('channel_number' in data) finals.set(channelId, data.channel_number ?? null);
  }
  return finals;
}

function expectNoDuplicateNumbers(finals: Map<number, number | null>) {
  const assigned = [...finals.values()].filter((n): n is number => n !== null);
  const collisions = assigned.filter((n, i) => assigned.indexOf(n) !== i);
  expect(collisions, `duplicate channel numbers in the staged result: ${JSON.stringify([...finals])}`)
    .toEqual([]);
}

describe('ChannelsPane cross-group move with source gap closing', () => {
  it('does not renumber a remaining source channel onto the moved channel\'s new number', async () => {
    // Codex's reproduction, shaped so the dialog actually offers the gap-close
    // option: it needs more than one channel left behind in the source group,
    // with a gap between them. Alpha moves out to number 11; Bravo and Charlie
    // stay and compact from the source group's minimum, 10. Compacting them
    // blindly puts Charlie on 11, which Alpha has just taken.
    const channels = [
      makeChannel(1, 'Alpha', 15, SOURCE_GROUP_ID),
      makeChannel(2, 'Bravo', 10, SOURCE_GROUP_ID),
      makeChannel(3, 'Charlie', 30, SOURCE_GROUP_ID),
      makeChannel(4, 'Delta', 100, TARGET_GROUP_ID),
    ];
    const staged = renderPane(channels, [1]);

    await moveToNewsWithCustomNumber('11', true);

    const finals = finalNumbers(channels, staged);
    expect(finals.get(1)).toBe(11);
    expectNoDuplicateNumbers(finals);
  });

  it('does not renumber a remaining source channel onto a number the push-down just claimed', async () => {
    // The same collision one phase over. Alpha moves onto 20, which Bravo
    // already held, so the push-down shifts Bravo to 21. The gap close then
    // wants to compact Bravo and Charlie from 20 upward, landing Charlie
    // on 21 as well. A channel the push-down has already moved must keep that
    // number and be allocated around, not given a second one.
    const channels = [
      makeChannel(1, 'Alpha', 5, SOURCE_GROUP_ID),
      makeChannel(2, 'Bravo', 20, SOURCE_GROUP_ID),
      makeChannel(3, 'Charlie', 30, SOURCE_GROUP_ID),
      makeChannel(4, 'Delta', 100, TARGET_GROUP_ID),
    ];
    const staged = renderPane(channels, [1]);

    await moveToNewsWithCustomNumber('20', true);

    const finals = finalNumbers(channels, staged);
    expect(finals.get(1)).toBe(20);
    expectNoDuplicateNumbers(finals);
  });

  it('still closes the gaps it can when nothing else is in the way', async () => {
    // The feature has to keep working: with the move landing clear of the
    // source group, Bravo and Charlie compact onto 10 and 11 as before.
    const channels = [
      makeChannel(1, 'Alpha', 15, SOURCE_GROUP_ID),
      makeChannel(2, 'Bravo', 10, SOURCE_GROUP_ID),
      makeChannel(3, 'Charlie', 30, SOURCE_GROUP_ID),
      makeChannel(4, 'Delta', 100, TARGET_GROUP_ID),
    ];
    const staged = renderPane(channels, [1]);

    await moveToNewsWithCustomNumber('101', true);

    const finals = finalNumbers(channels, staged);
    expect(finals.get(1)).toBe(101);
    expect(finals.get(2)).toBe(10);
    expect(finals.get(3)).toBe(11);
    expectNoDuplicateNumbers(finals);
  });
});
