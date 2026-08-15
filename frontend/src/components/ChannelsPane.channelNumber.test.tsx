/**
 * The channel list's inline number editor enforces the canonical
 * channel-number contract (bead `enhancedchannelmanager-ic884.1`).
 *
 * This is the highest-traffic channel-number entry point: double-click a
 * number in the list, type, blur. `1.05` is the fixture value because it sits
 * exactly between two in-contract tenths, so a rounding implementation would
 * accept it and quietly pick one.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ChannelsPane } from './ChannelsPane';
import { NotificationProvider } from '../contexts/NotificationContext';
import { CHANNEL_NUMBER_RULE_MESSAGE } from '../utils/channelNumber';
import type { Channel, ChannelGroup, ChannelListFilterSettings } from '../types';

vi.mock('../services/api', async () => {
  const actual = await vi.importActual<typeof import('../services/api')>('../services/api');
  return { ...actual };
});

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
    channel_group_id: null,
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

const groups: ChannelGroup[] = [{ id: 10, name: 'News', channel_count: 0 }];

function renderPane() {
  const onStageUpdateChannel = vi.fn();
  const onChannelUpdate = vi.fn();
  render(
    <NotificationProvider>
      <ChannelsPane
        channelGroups={groups}
        channels={[makeChannel(1, 'Alpha'), makeChannel(2, 'Beta')]}
        streams={[]}
        providers={[]}
        selectedChannelId={null}
        onChannelSelect={vi.fn()}
        onChannelUpdate={onChannelUpdate}
        onChannelDrop={vi.fn()}
        onBulkStreamDrop={vi.fn()}
        onChannelReorder={vi.fn()}
        onCreateChannel={vi.fn()}
        onDeleteChannel={vi.fn()}
        searchTerm=""
        onSearchChange={vi.fn()}
        selectedGroups={[]}
        onSelectedGroupsChange={vi.fn()}
        loading={false}
        autoRenameChannelNumber={false}
        isEditMode
        selectedChannelIds={new Set<number>()}
        onClearChannelSelection={vi.fn()}
        channelListFilters={makeFilters()}
        onChannelListFiltersChange={vi.fn()}
        onStageUpdateChannel={onStageUpdateChannel}
      />
    </NotificationProvider>,
  );
  return { onStageUpdateChannel, onChannelUpdate };
}

/** Open the inline editor on the first channel row and return its input.
 *
 * Groups render collapsed, so the group header is clicked first. */
function openNumberEditor(): HTMLInputElement {
  fireEvent.click(document.querySelector('.group-header') as HTMLElement);
  const numberCell = document.querySelector('.channel-number') as HTMLElement;
  expect(numberCell).not.toBeNull();
  fireEvent.doubleClick(numberCell);
  return document.querySelector('.channel-number-input') as HTMLInputElement;
}

describe('ChannelsPane inline channel-number editor contract', () => {
  it('refuses an out-of-contract entry and shows the operator-facing rule', () => {
    const { onStageUpdateChannel } = renderPane();
    const input = openNumberEditor();
    fireEvent.change(input, { target: { value: '1.05' } });
    fireEvent.blur(input);

    expect(screen.getByText(CHANNEL_NUMBER_RULE_MESSAGE)).toBeInTheDocument();
    expect(onStageUpdateChannel).not.toHaveBeenCalled();
  });

  it('keeps the editor open on the offending value instead of rounding it', () => {
    renderPane();
    const input = openNumberEditor();
    fireEvent.change(input, { target: { value: '1.05' } });
    fireEvent.blur(input);

    const stillOpen = document.querySelector('.channel-number-input') as HTMLInputElement;
    expect(stillOpen).not.toBeNull();
    expect(stillOpen.value).toBe('1.05');
  });

  it('stages an in-contract entry', () => {
    const { onStageUpdateChannel } = renderPane();
    const input = openNumberEditor();
    fireEvent.change(input, { target: { value: '1.1' } });
    fireEvent.blur(input);

    expect(screen.queryByText(CHANNEL_NUMBER_RULE_MESSAGE)).toBeNull();
    expect(onStageUpdateChannel).toHaveBeenCalledWith(
      1,
      expect.objectContaining({ channel_number: 1.1 }),
      expect.any(String),
    );
  });

  it('stages a cleared number as unassigned', () => {
    const { onStageUpdateChannel } = renderPane();
    const input = openNumberEditor();
    fireEvent.change(input, { target: { value: '' } });
    fireEvent.blur(input);

    expect(onStageUpdateChannel).toHaveBeenCalledWith(
      1,
      expect.objectContaining({ channel_number: null }),
      expect.any(String),
    );
  });
});
