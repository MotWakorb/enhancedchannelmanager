/**
 * Manually inserting a channel at an occupied number goes through the same
 * conflict flow as a bulk create (bead `enhancedchannelmanager-fprsq`).
 *
 * `handleBulkCreate` short-circuited manual entry with `doBulkCreate(false)` and
 * returned BEFORE the conflict check, and `doBulkCreate`'s manual branch then
 * accepted a `pushDown` argument it never read. Two defects, one on top of the
 * other: the "Channel Number Conflict" dialog was never shown in manual entry
 * at all, so an operator inserting a channel onto an occupied number got a
 * duplicate channel number with no warning of any kind; and had the dialog been
 * shown, its "Push channels down" button would have pushed nothing.
 *
 * The fixtures use a decimal insert on purpose. `38.1` is where the two counts
 * that matter diverge: one channel is IN the way, but pushing it down ripples
 * onto `38.2` and moves two. The dialog has to state both, and the size of the
 * insert it states is ONE, not `bulkCreateStats.channelCount`, which is zero in
 * manual entry because there are no selected streams.
 *
 * The two callbacks are the real ones from `App.tsx` rather than fixed-value
 * stubs, so the counts in the assertions are computed by the code under test's
 * own collaborators rather than handed to it.
 */
import { describe, it, expect, vi } from 'vitest';
import { useState } from 'react';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { StreamsPane } from './StreamsPane';
import { NotificationProvider } from '../contexts/NotificationContext';
import { planChannelNumberShift } from '../utils/channelNumberShift';
import type { Channel, ChannelGroup, Stream, StreamGroupInfo } from '../types';

const TARGET_GROUP_ID = 1;

const CHANNEL_GROUPS: ChannelGroup[] = [
  { id: TARGET_GROUP_ID, name: 'Entertainment', channel_count: 3 },
];

function makeChannel(id: number, name: string, channelNumber: number): Channel {
  return {
    id,
    channel_number: channelNumber,
    name,
    channel_group_id: TARGET_GROUP_ID,
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

/** 38.1 and 38.2 are adjacent on the tenths grid; 100 is the highest number. */
const CHANNELS: Channel[] = [
  makeChannel(1, 'Existing One', 38.1),
  makeChannel(2, 'Existing Two', 38.2),
  makeChannel(3, 'Existing Far', 100),
];

const STREAMS: Stream[] = [];
const STREAM_GROUPS: StreamGroupInfo[] = [];

// The App.tsx implementations, transcribed so the counts under assertion are
// produced rather than asserted into existence.
const checkConflicts = (startingNumber: number, count: number): number => {
  const endNumber = startingNumber + count - 1;
  return CHANNELS.filter(
    (ch) =>
      ch.channel_number !== null &&
      ch.channel_number >= startingNumber &&
      ch.channel_number <= endNumber,
  ).length;
};

const countPushDownShift = (startingNumber: number, count: number): number =>
  planChannelNumberShift({
    channels: CHANNELS,
    startingNumber,
    count,
    step: startingNumber % 1 !== 0 ? 0.1 : 1,
  }).shifts.length;

const getHighestChannelNumber = (): number =>
  CHANNELS.reduce((highest, ch) => Math.max(highest, ch.channel_number ?? 0), 0);

/**
 * Drives manual entry the way `App.tsx` does: the trigger prop is set once and
 * CLEARED in `onExternalTriggerHandled`. The trigger effect lists the prop
 * among its dependencies, so a stub that never clears it re-opens the modal
 * forever.
 */
function ManualEntryHarness({
  onCreateChannel,
}: {
  onCreateChannel: (
    name: string,
    channelNumber?: number,
    groupId?: number,
    newGroupName?: string,
    pushDownOnConflict?: boolean,
  ) => Promise<void>;
}) {
  const [manualEntry, setManualEntry] = useState(true);
  return (
    <StreamsPane
      streams={STREAMS}
      providers={[]}
      streamGroups={STREAM_GROUPS}
      searchTerm=""
      onSearchChange={vi.fn()}
      providerFilter={null}
      onProviderFilterChange={vi.fn()}
      groupFilter={null}
      onGroupFilterChange={vi.fn()}
      loading={false}
      channels={CHANNELS}
      channelGroups={CHANNEL_GROUPS}
      isEditMode
      externalTriggerManualEntry={manualEntry}
      externalTriggerTargetGroupId={TARGET_GROUP_ID}
      onBulkCreateFromGroup={vi.fn()}
      onCreateChannel={onCreateChannel}
      onCheckConflicts={checkConflicts}
      onCountPushDownShift={countPushDownShift}
      onGetHighestChannelNumber={getHighestChannelNumber}
      onExternalTriggerHandled={() => setManualEntry(false)}
    />
  );
}

function renderManualEntry() {
  const onCreateChannel = vi.fn().mockResolvedValue(undefined);
  render(
    <NotificationProvider>
      <ManualEntryHarness onCreateChannel={onCreateChannel} />
    </NotificationProvider>,
  );
  return { onCreateChannel };
}

async function fillManualEntry(name: string, channelNumber: string) {
  const user = userEvent.setup();
  await user.type(await screen.findByPlaceholderText('Enter channel name'), name);
  if (channelNumber) {
    await user.type(screen.getByPlaceholderText('e.g., 100 or 38.1'), channelNumber);
  }
  await user.click(screen.getByRole('button', { name: /Create Channel/ }));
  return user;
}

function conflictDialog(): HTMLElement | null {
  const heading = screen.queryByRole('heading', { name: 'Channel Number Conflict' });
  return heading ? (heading.closest('.conflict-dialog') as HTMLElement) : null;
}

describe('manual channel insert onto an occupied number', () => {
  it('warns instead of silently creating a duplicate channel number', async () => {
    const { onCreateChannel } = renderManualEntry();
    await fillManualEntry('New Channel', '38.1');

    const dialog = conflictDialog();
    expect(dialog, 'the Channel Number Conflict dialog was not shown').not.toBeNull();
    // Nothing is created while the operator is still being asked.
    expect(onCreateChannel).not.toHaveBeenCalled();
    const message = dialog!.querySelector('.conflict-message') as HTMLElement;
    expect(message.textContent).toContain('1 existing channel would conflict');
    expect(message.textContent).toContain('starting at 38.1');
  });

  it('sizes the insert at one channel, not at the zero streams it has', async () => {
    renderManualEntry();
    await fillManualEntry('New Channel', '38.1');

    const dialog = conflictDialog()!;
    // `bulkCreateStats.channelCount` is 0 in manual entry, so reading it here
    // would offer to shift existing channels "upward by 0". Two channels move
    // because the insert at 38.1 ripples onto 38.2.
    expect(within(dialog).getByText(/renumbering/)).toHaveTextContent(
      'Insert at 38.1, renumbering 2 existing channels upward by 1',
    );
  });

  it('honours "Push channels down", which used to be a button that did nothing', async () => {
    const { onCreateChannel } = renderManualEntry();
    const user = await fillManualEntry('New Channel', '38.1');

    await user.click(within(conflictDialog()!).getByRole('button', { name: /Push channels down/ }));

    expect(onCreateChannel).toHaveBeenCalledTimes(1);
    const [name, channelNumber, , , pushDown] = onCreateChannel.mock.calls[0];
    expect(name).toBe('New Channel');
    expect(channelNumber).toBe(38.1);
    expect(pushDown).toBe(true);
  });

  it('honours "Insert at end" by creating at the end-of-sequence number', async () => {
    const { onCreateChannel } = renderManualEntry();
    const user = await fillManualEntry('New Channel', '38.1');

    await user.click(within(conflictDialog()!).getByRole('button', { name: /Insert at end/ }));

    expect(onCreateChannel).toHaveBeenCalledTimes(1);
    const [, channelNumber, , , pushDown] = onCreateChannel.mock.calls[0];
    // The manual branch used to ignore the override and re-read the typed
    // field, so "Insert at end" created the channel back at 38.1.
    expect(channelNumber).toBe(getHighestChannelNumber() + 1);
    expect(pushDown).toBe(false);
  });

  it('honours "Create anyway" by creating the duplicate the operator chose', async () => {
    const { onCreateChannel } = renderManualEntry();
    const user = await fillManualEntry('New Channel', '38.1');

    await user.click(within(conflictDialog()!).getByRole('button', { name: /Create anyway/ }));

    expect(onCreateChannel).toHaveBeenCalledTimes(1);
    const [, channelNumber, , , pushDown] = onCreateChannel.mock.calls[0];
    expect(channelNumber).toBe(38.1);
    expect(pushDown).toBe(false);
  });
});

describe('manual channel insert with nothing in the way', () => {
  it('creates straight away at a free number', async () => {
    const { onCreateChannel } = renderManualEntry();
    await fillManualEntry('New Channel', '55');

    expect(conflictDialog()).toBeNull();
    expect(onCreateChannel).toHaveBeenCalledTimes(1);
    const [, channelNumber, , , pushDown] = onCreateChannel.mock.calls[0];
    expect(channelNumber).toBe(55);
    expect(pushDown).toBe(false);
  });

  it('creates an UNNUMBERED channel without asking about conflicts', async () => {
    // No channel number is a normal, legal state in Dispatcharr, and nothing
    // can collide with it.
    const { onCreateChannel } = renderManualEntry();
    await fillManualEntry('New Channel', '');

    expect(conflictDialog()).toBeNull();
    expect(onCreateChannel).toHaveBeenCalledTimes(1);
    expect(onCreateChannel.mock.calls[0][1]).toBeUndefined();
  });
});
