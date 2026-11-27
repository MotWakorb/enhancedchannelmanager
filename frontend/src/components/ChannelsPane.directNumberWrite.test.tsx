/**
 * ChannelsPane writes that go straight to `PATCH /api/channels/{id}` (nothing
 * staged) send the number the pane displayed as the baseline whenever they set
 * `channel_number`, so the server can refuse a write over a change made since.
 *
 * Two such writes exist: the inline number editor when no staging callback is
 * wired, and the Edit Channel modal outside Edit Mode.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ChannelsPane } from './ChannelsPane';
import { NotificationProvider } from '../contexts/NotificationContext';
import * as api from '../services/api';
import { HttpError } from '../services/httpClient';
import type { Channel, ChannelGroup, ChannelListFilterSettings } from '../types';

function makeFilters(): ChannelListFilterSettings {
  return {
    showEmptyGroups: true,
    showNewlyCreatedGroups: true,
    showProviderGroups: true,
    showManualGroups: true,
    showAutoChannelGroups: true,
  };
}

function makeChannel(id: number, name: string, channelNumber: number | null): Channel {
  return {
    id,
    channel_number: channelNumber,
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
const CONFLICT = 'Channel was not renumbered: channel 1 is on channel number 9, and this '
  + 'request expected 4. Somebody else changed it.';

function renderPane(options: {
  isEditMode: boolean;
  channelToEdit?: Channel | null;
  first?: Channel;
  others?: Channel[];
}) {
  const onChannelUpdate = vi.fn();
  const onChannelsChange = vi.fn();
  const first = options.first ?? makeChannel(1, 'Alpha', 4);
  const pane = (channels: Channel[]) => (
    <NotificationProvider>
      <ChannelsPane
        channelGroups={groups}
        channels={channels}
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
        isEditMode={options.isEditMode}
        selectedChannelIds={new Set<number>()}
        onClearChannelSelection={vi.fn()}
        channelListFilters={makeFilters()}
        onChannelListFiltersChange={vi.fn()}
        externalChannelToEdit={options.channelToEdit ?? null}
        onExternalChannelEditHandled={vi.fn()}
        onChannelsChange={onChannelsChange}
      />
    </NotificationProvider>
  );
  const { rerender } = render(pane([first, ...(options.others ?? [])]));
  return {
    onChannelUpdate,
    onChannelsChange,
    rerenderWith: (channels: Channel[]) => rerender(pane(channels)),
  };
}

function openNumberEditor(): HTMLInputElement {
  fireEvent.click(document.querySelector('.group-header') as HTMLElement);
  const numberCell = document.querySelector('.channel-number') as HTMLElement;
  fireEvent.doubleClick(numberCell);
  return document.querySelector('.channel-number-input') as HTMLInputElement;
}

beforeEach(() => {
  vi.spyOn(api, 'updateChannel').mockImplementation(async (id, data) => ({
    ...makeChannel(id, 'Alpha', 4),
    ...(data as Partial<Channel>),
  }));
});

afterEach(() => vi.restoreAllMocks());

describe('inline number editor writing directly', () => {
  it('sends the number it displayed as the baseline', async () => {
    renderPane({ isEditMode: true });
    const input = openNumberEditor();
    fireEvent.change(input, { target: { value: '7' } });
    await act(async () => { fireEvent.blur(input); });

    expect(api.updateChannel).toHaveBeenCalledWith(1, { channel_number: 7 }, { expectedNumber: 4 });
  });

  it('sends null for a channel it displayed with no number', async () => {
    renderPane({ isEditMode: true, first: makeChannel(1, 'Alpha', null) });
    const input = openNumberEditor();
    fireEvent.change(input, { target: { value: '7' } });
    await act(async () => { fireEvent.blur(input); });

    expect(api.updateChannel).toHaveBeenCalledWith(1, { channel_number: 7 }, { expectedNumber: null });
  });

  it('shows the server\'s reason when the channel moved, records nothing, and reloads', async () => {
    vi.mocked(api.updateChannel).mockRejectedValueOnce(new HttpError(CONFLICT, 409, { message: CONFLICT }));
    const { onChannelUpdate, onChannelsChange } = renderPane({ isEditMode: true });
    const input = openNumberEditor();
    fireEvent.change(input, { target: { value: '7' } });
    await act(async () => { fireEvent.blur(input); });

    expect(await screen.findByText(CONFLICT)).toBeInTheDocument();
    expect(onChannelUpdate).not.toHaveBeenCalled();
    expect(onChannelsChange).toHaveBeenCalledTimes(1);
  });

  it('keeps the number shown when the duplicate warning opened, not one loaded later', async () => {
    const beta = { ...makeChannel(2, 'Beta', 7), channel_group_id: null };
    const { rerenderWith } = renderPane({ isEditMode: true, others: [beta] });
    fireEvent.click(document.querySelector('.group-header') as HTMLElement);
    const alphaCell = Array.from(document.querySelectorAll('.channel-number'))
      .find((cell) => cell.textContent?.trim() === '4') as HTMLElement;
    fireEvent.doubleClick(alphaCell);
    const input = document.querySelector('.channel-number-input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '7' } });
    await act(async () => { fireEvent.blur(input); });
    expect(screen.getByRole('button', { name: 'Use It Anyway' })).toBeInTheDocument();

    // A reload lands while the warning is open and shows Alpha somewhere else.
    rerenderWith([makeChannel(1, 'Alpha', 9), beta]);
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Use It Anyway' }));
    });

    expect(api.updateChannel).toHaveBeenCalledWith(
      1, { channel_number: 7 }, { expectedNumber: 4 },
    );
  });
});

describe('Edit Channel modal outside Edit Mode', () => {
  function numberInput(): HTMLInputElement {
    return document.querySelector('.edit-channel-number-input') as HTMLInputElement;
  }

  it('sends the number the modal opened on as the baseline', async () => {
    renderPane({ isEditMode: false, channelToEdit: makeChannel(1, 'Alpha', 4) });
    await waitFor(() => expect(numberInput()).not.toBeNull());
    fireEvent.change(numberInput(), { target: { value: '7' } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /save changes/i }));
    });

    expect(api.updateChannel).toHaveBeenCalledWith(1, { channel_number: 7 }, { expectedNumber: 4 });
  });

  it('sends no baseline for an edit that leaves the number alone', async () => {
    renderPane({ isEditMode: false, channelToEdit: makeChannel(1, 'Alpha', 4) });
    await waitFor(() => expect(numberInput()).not.toBeNull());
    const name = document.querySelector('.edit-channel-name-input') as HTMLInputElement;
    fireEvent.change(name, { target: { value: 'Alpha HD' } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /save changes/i }));
    });

    expect(api.updateChannel).toHaveBeenCalledWith(1, { name: 'Alpha HD' }, undefined);
  });

  it('shows the server\'s reason when the channel moved, and reloads', async () => {
    vi.mocked(api.updateChannel).mockRejectedValueOnce(new HttpError(CONFLICT, 409, { message: CONFLICT }));
    const { onChannelUpdate, onChannelsChange } = renderPane({ isEditMode: false, channelToEdit: makeChannel(1, 'Alpha', 4) });
    await waitFor(() => expect(numberInput()).not.toBeNull());
    fireEvent.change(numberInput(), { target: { value: '7' } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /save changes/i }));
    });

    expect(await screen.findByText(CONFLICT)).toBeInTheDocument();
    expect(onChannelUpdate).not.toHaveBeenCalled();
    expect(onChannelsChange).toHaveBeenCalledTimes(1);
  });
});
