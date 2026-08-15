/**
 * The bulk-create modal's starting channel number is held to the canonical
 * channel-number contract (bead `enhancedchannelmanager-ic884.1`).
 *
 * The starting number is the one channel number an operator types here, and
 * the whole created run is derived from it, so one out-of-contract entry would
 * have produced a run of out-of-contract channels. `1.05` is the fixture value
 * because it sits exactly between two in-contract tenths.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { StreamsPane } from './StreamsPane';
import { CHANNEL_NUMBER_RULE_MESSAGE } from '../utils/channelNumber';
import type { Stream, StreamGroupInfo, Channel, ChannelGroup } from '../types';

const TARGET_GROUP_ID = 1;

const STREAMS: Stream[] = [
  {
    id: 1,
    name: 'Example Network',
    url: 'http://example.com/1.m3u8',
    m3u_account: 1,
    logo_url: null,
    tvg_id: null,
    channel_group: null,
    channel_group_name: 'US | Entertainment',
    is_custom: false,
  },
];
const STREAM_GROUPS: StreamGroupInfo[] = [{ name: 'US | Entertainment', count: 1 }];
const CHANNEL_GROUPS: ChannelGroup[] = [
  { id: TARGET_GROUP_ID, name: 'Entertainment', channel_count: 0 },
];
const CHANNELS: Channel[] = [];

function renderBulkCreate(onBulkCreateFromGroup = vi.fn()) {
  render(
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
      externalTriggerStreamIds={[1]}
      externalTriggerTargetGroupId={TARGET_GROUP_ID}
      externalTriggerStartingNumber={100}
      onBulkCreateFromGroup={onBulkCreateFromGroup}
      onCreateChannel={vi.fn()}
      onExternalTriggerHandled={vi.fn()}
    />,
  );
  return { onBulkCreateFromGroup };
}

function startingNumberInput(): HTMLInputElement {
  return screen.getByPlaceholderText('e.g., 100 or 38.1') as HTMLInputElement;
}

function createButton() {
  return screen.getByRole('button', { name: /Create 1 Channel/ });
}

describe('bulk-create starting channel number contract', () => {
  it('renders the operator-facing rule for an out-of-contract starting number', async () => {
    const user = userEvent.setup();
    renderBulkCreate();

    const input = await screen.findByPlaceholderText('e.g., 100 or 38.1');
    await user.clear(input);
    await user.type(input, '1.05');

    expect(screen.getByRole('alert')).toHaveTextContent(CHANNEL_NUMBER_RULE_MESSAGE);
  });

  it('blocks creation while the starting number is out of contract', async () => {
    const user = userEvent.setup();
    const { onBulkCreateFromGroup } = renderBulkCreate();

    const input = await screen.findByPlaceholderText('e.g., 100 or 38.1');
    await user.clear(input);
    await user.type(input, '1.05');

    expect(createButton()).toBeDisabled();
    expect(onBulkCreateFromGroup).not.toHaveBeenCalled();
  });

  it('accepts an in-contract decimal starting number', async () => {
    const user = userEvent.setup();
    renderBulkCreate();

    const input = await screen.findByPlaceholderText('e.g., 100 or 38.1');
    await user.clear(input);
    await user.type(input, '38.1');

    expect(screen.queryByRole('alert')).toBeNull();
    expect(createButton()).not.toBeDisabled();
    expect(startingNumberInput().value).toBe('38.1');
  });
});
