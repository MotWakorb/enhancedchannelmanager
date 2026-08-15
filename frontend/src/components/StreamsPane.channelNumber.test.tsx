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
import { useState, type ComponentProps } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { StreamsPane } from './StreamsPane';
import { NotificationProvider } from '../contexts/NotificationContext';
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
  {
    id: 2,
    name: 'Example Sports',
    url: 'http://example.com/2.m3u8',
    m3u_account: 1,
    logo_url: null,
    tvg_id: null,
    channel_group: null,
    channel_group_name: 'US | Sports',
    is_custom: false,
  },
];
const STREAM_GROUPS: StreamGroupInfo[] = [
  { name: 'US | Entertainment', count: 1 },
  { name: 'US | Sports', count: 1 },
];
const CHANNEL_GROUPS: ChannelGroup[] = [
  { id: TARGET_GROUP_ID, name: 'Entertainment', channel_count: 0 },
];
const CHANNELS: Channel[] = [];
// Hoisted so the prop keeps a stable identity across renders. The trigger
// effect lists it as a dependency, so a fresh array literal per render would
// re-open the modal on every render and spin.
const TRIGGER_GROUP_NAMES = ['US | Entertainment', 'US | Sports'];
const TRIGGER_STREAM_IDS = [1];

function renderBulkCreate(onBulkCreateFromGroup = vi.fn()) {
  render(
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
      channels={CHANNELS}
      channelGroups={CHANNEL_GROUPS}
      isEditMode
      externalTriggerStreamIds={TRIGGER_STREAM_IDS}
      externalTriggerTargetGroupId={TARGET_GROUP_ID}
      externalTriggerStartingNumber={100}
      onBulkCreateFromGroup={onBulkCreateFromGroup}
      onCreateChannel={vi.fn()}
      onExternalTriggerHandled={vi.fn()}
    />
    </NotificationProvider>,
  );
  return { onBulkCreateFromGroup };
}

/**
 * The multi-group trigger is driven through a harness that CLEARS the prop in
 * `onExternalTriggerHandled`, exactly as `App.tsx` does. The trigger effect
 * lists the prop among its dependencies and re-opens the modal whenever it is
 * still set, so a stub that never clears it re-fires forever.
 */
type BulkCreateFromGroup = NonNullable<
  ComponentProps<typeof StreamsPane>['onBulkCreateFromGroup']
>;

function SeparateGroupHarness({
  onBulkCreateFromGroup,
}: {
  onBulkCreateFromGroup: BulkCreateFromGroup;
}) {
  const [groupNames, setGroupNames] = useState<string[] | null>(TRIGGER_GROUP_NAMES);
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
      externalTriggerGroupNames={groupNames}
      externalTriggerTargetGroupId={TARGET_GROUP_ID}
      onBulkCreateFromGroup={onBulkCreateFromGroup}
      onCreateChannel={vi.fn()}
      onExternalTriggerHandled={() => setGroupNames(null)}
    />
  );
}

function renderSeparateGroupBulkCreate(onBulkCreateFromGroup = vi.fn()) {
  render(
    <NotificationProvider>
      <SeparateGroupHarness onBulkCreateFromGroup={onBulkCreateFromGroup} />
    </NotificationProvider>,
  );
  return { onBulkCreateFromGroup };
}

function startingNumberInput(): HTMLInputElement {
  return screen.getByPlaceholderText('e.g., 100 or 38.1') as HTMLInputElement;
}

function createButton() {
  return screen.getByRole('button', { name: /Create 1 Channel/ });
}

function separateModeCreateButton() {
  return screen.getByRole('button', { name: /Create 2 Channels/ });
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

/**
 * The multi-group ("separate group") mode is a second entry point for the same
 * value, and it used to validate with `parseFloat(...) >= 0` while converting
 * with `parseInt`. `1.05` therefore passed the guard and created channels
 * beginning at `1`, which is exactly the silent normalisation the contract
 * exists to prevent. Only the first group's value was checked at all.
 */
describe('bulk-create separate-group starting channel numbers', () => {
  it('renders the operator-facing rule for an out-of-contract group start', async () => {
    const user = userEvent.setup();
    renderSeparateGroupBulkCreate();

    const inputs = await screen.findAllByPlaceholderText('Auto');
    await user.type(inputs[0], '1.05');

    expect(screen.getByRole('alert')).toHaveTextContent(CHANNEL_NUMBER_RULE_MESSAGE);
  });

  it('blocks creation while a group start is out of contract', async () => {
    const user = userEvent.setup();
    const { onBulkCreateFromGroup } = renderSeparateGroupBulkCreate();

    const inputs = await screen.findAllByPlaceholderText('Auto');
    await user.type(inputs[0], '1.05');

    expect(separateModeCreateButton()).toBeDisabled();
    expect(onBulkCreateFromGroup).not.toHaveBeenCalled();
  });

  it('blocks creation when a LATER group start is out of contract', async () => {
    // The old guard read only the first group, so an out-of-contract value on
    // any group after the first reached `parseInt` untouched.
    const user = userEvent.setup();
    const { onBulkCreateFromGroup } = renderSeparateGroupBulkCreate();

    const inputs = await screen.findAllByPlaceholderText('Auto');
    await user.type(inputs[0], '100');
    await user.type(inputs[1], '1.05');

    expect(screen.getByRole('alert')).toHaveTextContent(CHANNEL_NUMBER_RULE_MESSAGE);
    expect(separateModeCreateButton()).toBeDisabled();
    expect(onBulkCreateFromGroup).not.toHaveBeenCalled();
  });

  it('creates from the in-contract decimal the operator typed, not its integer part', async () => {
    const user = userEvent.setup();
    const { onBulkCreateFromGroup } = renderSeparateGroupBulkCreate();

    const inputs = await screen.findAllByPlaceholderText('Auto');
    await user.type(inputs[0], '38.1');

    expect(screen.queryByRole('alert')).toBeNull();
    await user.click(separateModeCreateButton());

    expect(onBulkCreateFromGroup).toHaveBeenCalled();
    // Second positional argument is the starting number for the group.
    expect(onBulkCreateFromGroup.mock.calls[0][1]).toBe(38.1);
  });

  it('leaves an empty group start meaning "continue from the previous group"', async () => {
    const user = userEvent.setup();
    const { onBulkCreateFromGroup } = renderSeparateGroupBulkCreate();

    const inputs = await screen.findAllByPlaceholderText('Auto');
    await user.type(inputs[0], '100');

    expect(screen.queryByRole('alert')).toBeNull();
    expect(separateModeCreateButton()).not.toBeDisabled();
    await user.click(separateModeCreateButton());

    expect(onBulkCreateFromGroup.mock.calls[0][1]).toBe(100);
    expect(onBulkCreateFromGroup.mock.calls[1][1]).toBe(101);
  });
});
