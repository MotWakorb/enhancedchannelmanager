/**
 * The Edit Channel modal performs the same duplicate check as the inline
 * editor before it stages or saves a changed channel number (bead
 * `enhancedchannelmanager-vdxbx`, acceptance criterion 2).
 *
 * Same PO decision, same shape: WARN, do not block. What must not happen is an
 * ACCIDENTAL duplicate — so Save stops, says which channel is already there,
 * and only goes through once the operator says to. The confirmation travels
 * back to the caller so the staged operation can carry it, or the final-state
 * preflight will refuse the very duplicate the operator just approved.
 */
import { describe, it, expect, vi } from 'vitest';
import { act, render, screen, fireEvent } from '@testing-library/react';
import { EditChannelModal } from './EditChannelModal';
import type { Channel } from '../types';

function makeChannel(id: number, name: string, channel_number: number | null): Channel {
  return {
    id,
    name,
    channel_number,
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

const CHANNEL = makeChannel(1, 'ESPN', 1);
const LINEUP = [CHANNEL, makeChannel(2, 'TNT', 2), makeChannel(3, 'AMC', 3)];

function renderModal(otherChannels: Channel[] = LINEUP, channel: Channel = CHANNEL) {
  const onSave = vi.fn().mockResolvedValue(undefined);
  render(
    <EditChannelModal
      channel={channel}
      channelsForNumberCheck={otherChannels}
      logos={[]}
      epgData={[]}
      epgSources={[]}
      streamProfiles={[]}
      onClose={vi.fn()}
      onSave={onSave}
      onLogoCreate={vi.fn()}
      onLogoUpload={vi.fn()}
    />,
  );
  const input = document.querySelector('.edit-channel-number-input') as HTMLInputElement;
  const save = screen.getByRole('button', { name: /save changes/i });
  return { onSave, input, save };
}

describe('EditChannelModal duplicate channel number', () => {
  it('warns instead of saving when another channel already holds the number', async () => {
    const { input, save, onSave } = renderModal();
    fireEvent.change(input, { target: { value: '2' } });
    await act(async () => {
      fireEvent.click(save);
    });

    expect(screen.getByTestId('edit-channel-duplicate-confirm')).toBeInTheDocument();
    expect(onSave).not.toHaveBeenCalled();
  });

  it('names the conflicting channel', async () => {
    const { input, save } = renderModal();
    fireEvent.change(input, { target: { value: '2' } });
    await act(async () => {
      fireEvent.click(save);
    });

    expect(screen.getByTestId('edit-channel-duplicate-confirm').textContent).toContain('TNT');
  });

  it('compares canonically, so 2.0 conflicts with 2', async () => {
    const { input, save } = renderModal();
    fireEvent.change(input, { target: { value: '2.0' } });
    await act(async () => {
      fireEvent.click(save);
    });

    expect(screen.getByTestId('edit-channel-duplicate-confirm')).toBeInTheDocument();
  });

  it('saves with the acknowledgement once the operator confirms', async () => {
    const { input, save, onSave } = renderModal();
    fireEvent.change(input, { target: { value: '2' } });
    await act(async () => {
      fireEvent.click(save);
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /use it anyway/i }));
    });

    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({ channel_number: 2 }),
      { acknowledgedDuplicate: { number: 2, occupantChannelIds: [2] } },
    );
  });

  it('goes back to the form without saving when the operator backs out', async () => {
    const { input, save, onSave } = renderModal();
    fireEvent.change(input, { target: { value: '2' } });
    await act(async () => {
      fireEvent.click(save);
    });
    fireEvent.click(screen.getByRole('button', { name: /go back/i }));

    expect(onSave).not.toHaveBeenCalled();
    expect(screen.queryByTestId('edit-channel-duplicate-confirm')).toBeNull();
    expect(
      (document.querySelector('.edit-channel-number-input') as HTMLInputElement).value,
    ).toBe('2');
  });

  it('asks again after the operator retypes a different occupied number', async () => {
    const { input, save, onSave } = renderModal();
    fireEvent.change(input, { target: { value: '2' } });
    await act(async () => {
      fireEvent.click(save);
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /use it anyway/i }));
    });
    onSave.mockClear();

    // A decision about 2 says nothing about 3.
    fireEvent.change(input, { target: { value: '3' } });
    await act(async () => {
      fireEvent.click(save);
    });
    expect(screen.getByTestId('edit-channel-duplicate-confirm')).toBeInTheDocument();
    expect(onSave).not.toHaveBeenCalled();
  });

  it('does not warn when the channel keeps its own number', async () => {
    const { input, save, onSave } = renderModal();
    // Change something else so there is a change to save at all.
    fireEvent.change(input, { target: { value: '1.0' } });
    fireEvent.change(document.querySelector('.edit-channel-name-input') as HTMLInputElement, {
      target: { value: 'ESPN HD' },
    });
    await act(async () => {
      fireEvent.click(save);
    });

    expect(screen.queryByTestId('edit-channel-duplicate-confirm')).toBeNull();
    expect(onSave).toHaveBeenCalled();
  });

  it('does not warn for an unused number', async () => {
    const { input, save, onSave } = renderModal();
    fireEvent.change(input, { target: { value: '99' } });
    await act(async () => {
      fireEvent.click(save);
    });

    expect(screen.queryByTestId('edit-channel-duplicate-confirm')).toBeNull();
    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({ channel_number: 99 }));
  });

  it('never warns about an out-of-contract entry — it refuses it', async () => {
    const { input, save, onSave } = renderModal();
    fireEvent.change(input, { target: { value: '1.05' } });
    await act(async () => {
      fireEvent.click(save);
    });

    expect(screen.queryByTestId('edit-channel-duplicate-confirm')).toBeNull();
    expect(onSave).not.toHaveBeenCalled();
  });

  it('checks against effective local state, not only the server list', async () => {
    // A channel staged for creation in this session has a negative id and no
    // server row; the working copy is what carries it.
    const withStaged = [...LINEUP, makeChannel(-1, 'Staged New', 50)];
    const { input, save } = renderModal(withStaged);
    fireEvent.change(input, { target: { value: '50' } });
    await act(async () => {
      fireEvent.click(save);
    });

    expect(screen.getByTestId('edit-channel-duplicate-confirm').textContent).toContain('Staged New');
  });

  it('warns about nothing when no lineup was supplied', async () => {
    const { input, save, onSave } = renderModal([]);
    fireEvent.change(input, { target: { value: '2' } });
    await act(async () => {
      fireEvent.click(save);
    });

    expect(screen.queryByTestId('edit-channel-duplicate-confirm')).toBeNull();
    expect(onSave).toHaveBeenCalled();
  });
});
