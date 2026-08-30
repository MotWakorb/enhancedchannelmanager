/**
 * The inline channel-number editor warns before it stages a number another
 * channel already holds (bead `enhancedchannelmanager-vdxbx`), and asks before
 * it clears a number out of a name that carries one (bead
 * `enhancedchannelmanager-ic884.5`).
 *
 * The PO's decision this encodes: WARN, do not block. `ic884.1` deliberately
 * declined to enforce uniqueness because Dispatcharr permits duplicates, so a
 * hard refusal here would contradict a shipped decision. The operator must be
 * able to proceed, deliberately — and the deliberate choice has to be recorded
 * on the operation, or the final-state preflight will raise it again at Apply.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ChannelsPane } from './ChannelsPane';
import { NotificationProvider } from '../contexts/NotificationContext';
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

function makeChannel(id: number, name: string, channel_number: number | null): Channel {
  return {
    id,
    channel_number,
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

function renderPane(channels: Channel[], autoRename = false) {
  const onStageUpdateChannel = vi.fn();
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
        selectedGroups={[]}
        onSelectedGroupsChange={vi.fn()}
        loading={false}
        autoRenameChannelNumber={autoRename}
        isEditMode
        selectedChannelIds={new Set<number>()}
        onClearChannelSelection={vi.fn()}
        channelListFilters={makeFilters()}
        onChannelListFiltersChange={vi.fn()}
        onStageUpdateChannel={onStageUpdateChannel}
      />
    </NotificationProvider>,
  );
  return { onStageUpdateChannel };
}

/** Open the inline editor on the row at `index` and return its input. */
function openNumberEditor(index = 0): HTMLInputElement {
  fireEvent.click(document.querySelector('.group-header') as HTMLElement);
  const cells = document.querySelectorAll('.channel-number');
  fireEvent.doubleClick(cells[index] as HTMLElement);
  return document.querySelector('.channel-number-input') as HTMLInputElement;
}

function typeAndBlur(input: HTMLInputElement, value: string) {
  fireEvent.change(input, { target: { value } });
  fireEvent.blur(input);
}

const LINEUP = [
  makeChannel(1, 'Alpha', 1),
  makeChannel(2, 'Beta', 2),
  makeChannel(3, 'Gamma', 3),
];

describe('inline channel-number editor — duplicate warning', () => {
  it('warns instead of staging when another channel already holds the number', () => {
    const { onStageUpdateChannel } = renderPane(LINEUP);
    typeAndBlur(openNumberEditor(0), '2');

    expect(screen.getByTestId('channel-number-confirm')).toBeInTheDocument();
    expect(onStageUpdateChannel).not.toHaveBeenCalled();
  });

  it('names the conflicting channel well enough to act on', () => {
    renderPane(LINEUP);
    typeAndBlur(openNumberEditor(0), '2');

    const dialog = screen.getByTestId('channel-number-confirm');
    expect(dialog.textContent).toContain('Beta');
    expect(dialog.textContent).toContain('2');
  });

  it('compares canonically, so 2.0 conflicts with 2', () => {
    renderPane(LINEUP);
    typeAndBlur(openNumberEditor(0), '2.0');
    expect(screen.getByTestId('channel-number-confirm')).toBeInTheDocument();
  });

  it('stages with the acknowledgement once the operator confirms', () => {
    const { onStageUpdateChannel } = renderPane(LINEUP);
    typeAndBlur(openNumberEditor(0), '2');
    fireEvent.click(screen.getByRole('button', { name: /use it anyway/i }));

    expect(onStageUpdateChannel).toHaveBeenCalledWith(
      1,
      expect.objectContaining({ channel_number: 2 }),
      expect.any(String),
      { acknowledgedDuplicate: { number: 2, occupantChannelIds: [2] } },
    );
  });

  it('reopens the editor on the refused value when the operator backs out', () => {
    const { onStageUpdateChannel } = renderPane(LINEUP);
    typeAndBlur(openNumberEditor(0), '2');
    fireEvent.click(screen.getByRole('button', { name: /go back/i }));

    expect(onStageUpdateChannel).not.toHaveBeenCalled();
    const reopened = document.querySelector('.channel-number-input') as HTMLInputElement;
    expect(reopened).not.toBeNull();
    expect(reopened.value).toBe('2');
  });

  it('does not warn when the channel keeps its own number', () => {
    const { onStageUpdateChannel } = renderPane(LINEUP);
    typeAndBlur(openNumberEditor(0), '1');

    expect(screen.queryByTestId('channel-number-confirm')).toBeNull();
    expect(onStageUpdateChannel).toHaveBeenCalledWith(
      1,
      expect.objectContaining({ channel_number: 1 }),
      expect.any(String),
    );
  });

  it('does not warn for an unused number', () => {
    const { onStageUpdateChannel } = renderPane(LINEUP);
    typeAndBlur(openNumberEditor(0), '99');

    expect(screen.queryByTestId('channel-number-confirm')).toBeNull();
    expect(onStageUpdateChannel).toHaveBeenCalledTimes(1);
  });

  it('never warns about a malformed entry — it refuses it', () => {
    const { onStageUpdateChannel } = renderPane(LINEUP);
    typeAndBlur(openNumberEditor(0), 'abc');

    expect(screen.queryByTestId('channel-number-confirm')).toBeNull();
    expect(onStageUpdateChannel).not.toHaveBeenCalled();
  });

  it('sees channels only present in local staged state', () => {
    // `channels` here is Edit Mode's working copy, so a channel created in
    // this session is in it and a channel deleted in this session is not.
    // The staged create at 50 has no server row at all.
    const withStaged = [...LINEUP, makeChannel(-1, 'Staged New', 50)];
    renderPane(withStaged);
    typeAndBlur(openNumberEditor(0), '50');
    expect(screen.getByTestId('channel-number-confirm')).toBeInTheDocument();
  });

  it('treats an unassigned number as no conflict, however many channels have none', () => {
    const unnumbered = [
      makeChannel(1, 'Alpha', 1),
      makeChannel(2, 'Beta', null),
      makeChannel(3, 'Gamma', null),
    ];
    const { onStageUpdateChannel } = renderPane(unnumbered);
    typeAndBlur(openNumberEditor(0), '');

    expect(screen.queryByTestId('channel-number-confirm')).toBeNull();
    expect(onStageUpdateChannel).toHaveBeenCalledWith(
      1,
      expect.objectContaining({ channel_number: null }),
      expect.any(String),
    );
  });
});

describe('inline channel-number editor — clearing a number', () => {
  it('asks before stranding a number inside the channel name', () => {
    const named = [makeChannel(1, '1 | Alpha', 1), makeChannel(2, 'Beta', 2)];
    const { onStageUpdateChannel } = renderPane(named, true);
    typeAndBlur(openNumberEditor(0), '');

    const dialog = screen.getByTestId('channel-number-confirm');
    expect(dialog.textContent).toContain('1 | Alpha');
    expect(onStageUpdateChannel).not.toHaveBeenCalled();
  });

  it('clears without asking when the name carries no number', () => {
    const { onStageUpdateChannel } = renderPane(LINEUP);
    typeAndBlur(openNumberEditor(0), '');

    expect(screen.queryByTestId('channel-number-confirm')).toBeNull();
    expect(onStageUpdateChannel).toHaveBeenCalledWith(
      1,
      expect.objectContaining({ channel_number: null }),
      expect.any(String),
    );
  });

  it('clears on confirmation, with no duplicate acknowledgement attached', () => {
    const named = [makeChannel(1, '1 | Alpha', 1), makeChannel(2, 'Beta', 2)];
    const { onStageUpdateChannel } = renderPane(named, true);
    typeAndBlur(openNumberEditor(0), '');
    fireEvent.click(screen.getByRole('button', { name: /clear it anyway/i }));

    expect(onStageUpdateChannel).toHaveBeenCalledWith(
      1,
      expect.objectContaining({ channel_number: null }),
      expect.any(String),
    );
  });
});
