/**
 * Bulk-create conflict dialog: blast radius before committing
 * (bead `enhancedchannelmanager-i85dg`).
 *
 * The dialog used to state only how many channels sat on the numbers the new
 * channels would claim. That number is the tip of the operation: "Push
 * channels down" also renumbers everything between the insertion point and
 * the first wide enough run of free numbers, which on the PO's lineup was
 * hundreds of channels the dialog never mentioned.
 *
 * These render the real dialog and check the figure it shows against the
 * planner's answer for the same lineup, so the two cannot drift apart.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { StreamsPane } from './StreamsPane';
import { NotificationProvider } from '../contexts/NotificationContext';
import { planChannelNumberShift } from '../utils/channelNumberShift';
import type { Stream, StreamGroupInfo, Channel, ChannelGroup } from '../types';

const TARGET_GROUP_ID = 1;
const INSERT_AT = 300;

function makeStream(id: number, name: string): Stream {
  return {
    id,
    name,
    url: `http://example.com/${id}.m3u8`,
    m3u_account: 1,
    logo_url: null,
    tvg_id: null,
    channel_group: null,
    channel_group_name: 'US | Entertainment',
    is_custom: false,
  };
}

function makeChannel(id: number, channelNumber: number, groupId: number | null): Channel {
  return {
    id,
    channel_number: channelNumber,
    name: `Channel ${channelNumber}`,
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

/**
 * The PO's live shape: a contiguous target group, a later group separated by a
 * gap, and one outlier number high above both. The outlier is what used to
 * drag every later group into the cascade.
 */
function lineup(): Channel[] {
  const channels: Channel[] = [];
  let id = 1;
  for (let n = 201; n <= 500; n++) channels.push(makeChannel(id++, n, TARGET_GROUP_ID));
  channels.push(makeChannel(id++, 8000, TARGET_GROUP_ID));
  for (let n = 600; n <= 699; n++) channels.push(makeChannel(id++, n, 2));
  return channels;
}

const STREAMS: Stream[] = [makeStream(1, 'Example Network')];
const STREAM_GROUPS: StreamGroupInfo[] = [{ name: 'US | Entertainment', count: 1 }];
const CHANNEL_GROUPS: ChannelGroup[] = [
  { id: TARGET_GROUP_ID, name: 'Entertainment', channel_count: 301 },
  { id: 2, name: 'Sports', channel_count: 100 },
];

function renderConflictDialog(overrides: Partial<React.ComponentProps<typeof StreamsPane>> = {}) {
  const channels = lineup();
  return {
    channels,
    ...render(
      <NotificationProvider>
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
        channels={channels}
        channelGroups={CHANNEL_GROUPS}
        isEditMode
        externalTriggerStreamIds={[1]}
        externalTriggerTargetGroupId={TARGET_GROUP_ID}
        externalTriggerStartingNumber={INSERT_AT}
        onBulkCreateFromGroup={vi.fn()}
        onCreateChannel={vi.fn()}
        onExternalTriggerHandled={vi.fn()}
        // Same handlers App.tsx supplies, over the same lineup.
        onCheckConflicts={(startingNumber, count) =>
          channels.filter(
            (ch) =>
              ch.channel_number !== null &&
              ch.channel_number >= startingNumber &&
              ch.channel_number <= startingNumber + count - 1,
          ).length
        }
        onCountPushDownShift={(startingNumber, count) =>
          planChannelNumberShift({ channels, startingNumber, count }).shifts.length
        }
        onGetHighestChannelNumber={() => 8000}
        {...overrides}
      />
      </NotificationProvider>,
    ),
  };
}

describe('bulk-create conflict dialog', () => {
  it('states how many existing channels the push-down would renumber', async () => {
    const user = userEvent.setup();
    const { channels } = renderConflictDialog();

    await user.click(await screen.findByRole('button', { name: /Create 1 Channel/ }));

    // Scoped by name: the bulk-create modal underneath is also a dialog.
    const dialog = await screen.findByRole('dialog', { name: 'Channel Number Conflict' });

    // The planner's answer for this lineup: 201 channels, 300 through 500.
    // The outlier at 8000 and the whole of group 2 stay put.
    const expected = planChannelNumberShift({ channels, startingNumber: INSERT_AT, count: 1 });
    expect(expected.shifts).toHaveLength(201);

    const pushDown = within(dialog).getByRole('button', { name: /Push channels down/ });
    expect(pushDown).toHaveTextContent(
      `renumbering ${expected.shifts.length} existing channels upward by 1`,
    );

    // The pre-existing conflict figure counts only the claimed number, so the
    // dialog would have said "1" and nothing about the other 200.
    expect(dialog).toHaveTextContent('1 existing channel would');
  });

  it('falls back to the wording without a figure when no counter is supplied', async () => {
    const user = userEvent.setup();
    renderConflictDialog({ onCountPushDownShift: undefined });

    await user.click(await screen.findByRole('button', { name: /Create 1 Channel/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Channel Number Conflict' });

    const pushDown = within(dialog).getByRole('button', { name: /Push channels down/ });
    expect(pushDown).toHaveTextContent('shift existing channels by 1');
    expect(pushDown).not.toHaveTextContent('renumbering');
  });
});
