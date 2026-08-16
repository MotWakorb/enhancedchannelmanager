/**
 * A renumbering RUN is refused when its numbers cannot all exist (bead
 * `enhancedchannelmanager-ic884.5`).
 *
 * The start of every one of these dialogs is already held to the whole-number
 * rule field by field (`enhancedchannelmanager-j3pyx`). That is a different
 * property, and it is not enough: `2**53 - 1` is a perfectly valid whole
 * channel number, and a run of three from there is not, because consecutive
 * integers stop being distinct floats at `2**53`. The tail of the run then
 * lands several channels silently on one number — which is precisely the
 * "returns an occupied number at large magnitudes" failure `ic884.1`'s review
 * rounds blocked twice.
 *
 * So the check is over the RUN, not over the value, and it fires before any
 * operation is staged. What each test pins is that nothing was staged, not
 * merely that a message appeared.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ChannelsPane } from './ChannelsPane';
import { NotificationProvider } from '../contexts/NotificationContext';
import type { Channel, ChannelGroup, ChannelListFilterSettings } from '../types';

const GROUP_ID = 10;
const groups: ChannelGroup[] = [{ id: GROUP_ID, name: 'Sports', channel_count: 3 }];

/** The largest whole number the renumber fields accept — and a bad place to start a run. */
const MAX_SAFE_START = Number.MAX_SAFE_INTEGER;

function makeFilters(): ChannelListFilterSettings {
  return {
    showEmptyGroups: true,
    showNewlyCreatedGroups: true,
    showProviderGroups: true,
    showManualGroups: true,
    showAutoChannelGroups: true,
  };
}

function makeChannel(id: number, name: string, channelNumber: number): Channel {
  return {
    id,
    channel_number: channelNumber,
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

const CHANNELS = [
  makeChannel(1, 'Alpha', 1),
  makeChannel(2, 'Bravo', 2),
  makeChannel(3, 'Charlie', 3),
];

function renderPane(selectedIds: number[] = []) {
  const staged: { channelId: number; data: Partial<Channel> }[] = [];
  render(
    <NotificationProvider>
      <ChannelsPane
        channelGroups={groups}
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

async function openSortAndRenumber(user: ReturnType<typeof userEvent.setup>) {
  const header = Array.from(document.querySelectorAll('.group-header')).find((el) =>
    el.querySelector('.group-name')?.textContent?.includes('Sports'),
  );
  await user.click(header!.querySelector<HTMLButtonElement>('.group-menu-btn')!);
  await user.click(screen.getByRole('button', { name: /Sort & Renumber/ }));
  return document.querySelector('.sort-renumber-dialog') as HTMLElement;
}

async function openMassRenumber(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: 'Renumber' }));
  return document.querySelector('.mass-renumber-dialog') as HTMLElement;
}

async function retype(
  user: ReturnType<typeof userEvent.setup>,
  input: HTMLInputElement,
  value: string,
) {
  await user.clear(input);
  await user.type(input, value);
}

describe('Sort & Renumber run bounds', () => {
  it('refuses a run whose numbers stop being distinct, staging nothing', async () => {
    const user = userEvent.setup();
    const staged = renderPane();
    const dialog = await openSortAndRenumber(user);

    const input = within(dialog).getByLabelText('Starting Channel Number') as HTMLInputElement;
    await retype(user, input, String(MAX_SAFE_START));
    await user.click(within(dialog).getByRole('button', { name: /^Sort & Renumber$/ }));

    expect(staged).toEqual([]);
  });

  it('still renumbers an ordinary run', async () => {
    const user = userEvent.setup();
    const staged = renderPane();
    const dialog = await openSortAndRenumber(user);

    const input = within(dialog).getByLabelText('Starting Channel Number') as HTMLInputElement;
    await retype(user, input, '20');
    await user.click(within(dialog).getByRole('button', { name: /^Sort & Renumber$/ }));

    expect(staged.map((s) => s.data.channel_number)).toEqual([20, 21, 22]);
  });
});

describe('Renumber (selection) run bounds', () => {
  it('refuses a run whose numbers stop being distinct, staging nothing', async () => {
    const user = userEvent.setup();
    const staged = renderPane([1, 2, 3]);
    const dialog = await openMassRenumber(user);

    const input = within(dialog).getByLabelText('Starting Channel Number') as HTMLInputElement;
    await retype(user, input, String(MAX_SAFE_START));
    await user.click(within(dialog).getByRole('button', { name: /^Renumber$/ }));

    expect(staged).toEqual([]);
  });

  it('still renumbers an ordinary run', async () => {
    const user = userEvent.setup();
    const staged = renderPane([1, 2, 3]);
    const dialog = await openMassRenumber(user);

    const input = within(dialog).getByLabelText('Starting Channel Number') as HTMLInputElement;
    await retype(user, input, '40');
    await user.click(within(dialog).getByRole('button', { name: /^Renumber$/ }));

    expect(staged.map((s) => s.data.channel_number).sort((a, b) => (a as number) - (b as number)))
      .toEqual([40, 41, 42]);
  });
});
