/**
 * The Merge Channels modal enforces the canonical channel-number contract
 * (bead `enhancedchannelmanager-ic884.1`).
 *
 * `1.05` is the fixture value because it sits exactly between two in-contract
 * tenths: a rounding implementation would accept it and quietly pick one.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, render, screen, fireEvent } from '@testing-library/react';
import { MergeChannelsModal } from './MergeChannelsModal';
import { CHANNEL_NUMBER_RULE_MESSAGE } from '../utils/channelNumber';
import type { Channel } from '../types';

vi.mock('../services/api', () => ({
  mergeChannels: vi.fn(),
}));

import * as api from '../services/api';

const makeChannel = (id: number, name: string, channel_number: number | null = id): Channel => ({
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
});

const BASE_PROPS = {
  channels: [makeChannel(100, 'Live A'), makeChannel(200, 'Live B')],
  logos: [],
  epgData: [],
  epgSources: [],
  channelGroups: [],
  streamProfiles: [],
  streams: [],
  onClose: vi.fn(),
  onMerged: vi.fn(),
};

function numberInput() {
  return document.querySelector('.merge-input-short') as HTMLInputElement;
}

function mergeButton() {
  return screen.getByRole('button', { name: /Merge 2 Channels/ });
}

describe('MergeChannelsModal channel-number contract', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the operator-facing rule for an out-of-contract target number', () => {
    render(<MergeChannelsModal {...BASE_PROPS} />);
    fireEvent.change(numberInput(), { target: { value: '1.05' } });
    expect(screen.getByRole('alert')).toHaveTextContent(CHANNEL_NUMBER_RULE_MESSAGE);
  });

  it('blocks the merge while the target number is out of contract', () => {
    render(<MergeChannelsModal {...BASE_PROPS} />);
    fireEvent.change(numberInput(), { target: { value: '1.05' } });
    expect(mergeButton()).toBeDisabled();
    fireEvent.click(mergeButton());
    expect(api.mergeChannels).not.toHaveBeenCalled();
  });

  it('sends an in-contract target number through unchanged', async () => {
    render(<MergeChannelsModal {...BASE_PROPS} />);
    fireEvent.change(numberInput(), { target: { value: '1.1' } });
    expect(screen.queryByRole('alert')).toBeNull();
    await act(async () => {
      fireEvent.click(mergeButton());
    });
    expect(api.mergeChannels).toHaveBeenCalledWith(
      expect.objectContaining({ target_channel_number: 1.1 }),
    );
  });

  it('defaults to a blank box, not "Infinity", when no source has a number', async () => {
    render(
      <MergeChannelsModal
        {...BASE_PROPS}
        channels={[makeChannel(100, 'Live A', null), makeChannel(200, 'Live B', null)]}
      />,
    );
    expect(numberInput().value).toBe('');
    expect(screen.queryByRole('alert')).toBeNull();
    await act(async () => {
      fireEvent.click(mergeButton());
    });
    expect(api.mergeChannels).toHaveBeenCalledWith(
      expect.objectContaining({ target_channel_number: null }),
    );
  });
});
