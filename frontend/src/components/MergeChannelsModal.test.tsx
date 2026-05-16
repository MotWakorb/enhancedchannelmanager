/**
 * Unit tests for MergeChannelsModal — error-surface behaviour.
 *
 * bd-7j6v1 (follow-up to bd-ct9wl): the backend POST /api/channels/merge now
 * returns 422 with a human-readable detail string when the submitted source
 * IDs are stale. Verify the modal renders that detail in the error banner
 * rather than a generic fallback, and that non-422 errors still produce a
 * reasonable message.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MergeChannelsModal } from './MergeChannelsModal';
import { HttpError } from '../services/httpClient';
import type { Channel } from '../types';

/** Click the merge submit button (not the header text which also says "Merge N Channels"). */
function clickMergeButton() {
  const button = screen.getByRole('button', { name: /Merge 2 Channels/ });
  fireEvent.click(button);
}

// Mock the API module
vi.mock('../services/api', () => ({
  mergeChannels: vi.fn(),
}));

import * as api from '../services/api';

// Minimal channel fixtures for two channels being merged
const makeChannel = (id: number, name: string): Channel => ({
  id,
  name,
  channel_number: id,
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

const CHANNELS = [makeChannel(100, 'Live A'), makeChannel(200, 'Live B')];

const BASE_PROPS = {
  channels: CHANNELS,
  logos: [],
  epgData: [],
  epgSources: [],
  channelGroups: [],
  streamProfiles: [],
  streams: [],
  onClose: vi.fn(),
  onMerged: vi.fn(),
};

describe('MergeChannelsModal — 422 detail surface (bd-7j6v1)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the backend 422 detail string in the error banner, not a generic fallback', async () => {
    const detail = 'Source channels [999] no longer exist — refresh the channels list and try again';
    vi.mocked(api.mergeChannels).mockRejectedValue(new HttpError(detail, 422));

    render(<MergeChannelsModal {...BASE_PROPS} />);

    clickMergeButton();

    await waitFor(() => {
      expect(screen.getByText(detail)).toBeInTheDocument();
    });
    // The generic fallback copy must NOT appear.
    expect(screen.queryByText('Merge failed')).not.toBeInTheDocument();
  });

  it('shows a reasonable message for non-422 errors (generic HttpError)', async () => {
    vi.mocked(api.mergeChannels).mockRejectedValue(
      new HttpError('Internal Server Error', 500),
    );

    render(<MergeChannelsModal {...BASE_PROPS} />);

    clickMergeButton();

    await waitFor(() => {
      // The error banner must be visible — any non-empty message is acceptable.
      const banner = screen.getByText('Internal Server Error');
      expect(banner).toBeInTheDocument();
    });
  });

  it('falls back to generic copy when the thrown value is not an Error', async () => {
    // Non-Error thrown values (rare but possible from unforeseen paths) fall
    // back to the generic "Merge failed" copy.
    vi.mocked(api.mergeChannels).mockRejectedValue('raw string error');

    render(<MergeChannelsModal {...BASE_PROPS} />);

    clickMergeButton();

    await waitFor(() => {
      expect(screen.getByText('Merge failed')).toBeInTheDocument();
    });
  });
});
