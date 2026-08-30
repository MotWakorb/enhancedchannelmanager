/**
 * The Edit Channel modal enforces the canonical channel-number contract
 * (bead `enhancedchannelmanager-ic884.1`).
 *
 * `1.05` is the fixture value because it sits exactly between two in-contract
 * tenths: a rounding implementation would accept it and quietly pick one, and
 * the PO chose rejection over that.
 */
import { describe, it, expect, vi } from 'vitest';
import { act, render, screen, fireEvent } from '@testing-library/react';
import { EditChannelModal } from './EditChannelModal';
import { CHANNEL_NUMBER_RULE_MESSAGE } from '../utils/channelNumber';
import type { Channel } from '../types';

const CHANNEL: Channel = {
  id: 1,
  name: 'ESPN',
  channel_number: 1,
  channel_group_id: null,
  tvg_id: null,
  tvc_guide_stationid: null,
  epg_data_id: null,
  streams: [],
  stream_profile_id: null,
  uuid: 'uuid-1',
  logo_id: null,
  auto_created: false,
  auto_created_by: null,
  auto_created_by_name: null,
};

function renderModal(channel: Channel = CHANNEL) {
  const onSave = vi.fn().mockResolvedValue(undefined);
  render(
    <EditChannelModal
      channel={channel}
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

describe('EditChannelModal channel-number contract', () => {
  it('renders the operator-facing rule for an out-of-contract entry', () => {
    const { input } = renderModal();
    fireEvent.change(input, { target: { value: '1.05' } });
    expect(screen.getByRole('alert')).toHaveTextContent(CHANNEL_NUMBER_RULE_MESSAGE);
  });

  it('blocks Save while the entry is out of contract', () => {
    const { input, save, onSave } = renderModal();
    fireEvent.change(input, { target: { value: '1.05' } });
    expect(save).toBeDisabled();
    fireEvent.click(save);
    expect(onSave).not.toHaveBeenCalled();
  });

  it.each(['-1', '2.001', 'abc'])('rejects the out-of-contract entry %s', (value) => {
    const { input, onSave } = renderModal();
    fireEvent.change(input, { target: { value } });
    expect(screen.getByRole('alert')).toHaveTextContent(CHANNEL_NUMBER_RULE_MESSAGE);
    expect(onSave).not.toHaveBeenCalled();
  });

  it.each(['2', '2.1', '0'])('accepts the in-contract entry %s and saves it', async (value) => {
    const { input, save, onSave } = renderModal();
    fireEvent.change(input, { target: { value } });
    expect(screen.queryByRole('alert')).toBeNull();
    expect(save).not.toBeDisabled();
    await act(async () => {
      fireEvent.click(save);
    });
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({ channel_number: Number(value) }),
    );
  });

  it('shows an empty box, not the text "null", for an unassigned channel', () => {
    const { input } = renderModal({ ...CHANNEL, channel_number: null });
    expect(input.value).toBe('');
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
