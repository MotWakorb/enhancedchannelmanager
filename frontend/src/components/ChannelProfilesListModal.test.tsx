/**
 * Unit tests for the bulk apply-to-selected feature added to
 * ChannelProfilesListModal (enhancedchannelmanager-hq3de.i).
 *
 * Contracts under test:
 *   - A dedicated "select" checkbox per channel row, distinct from the
 *     enable/disable toggle, drives a bulk-apply toolbar.
 *   - "Apply to Selected: Enable" / "Disable" call
 *     PATCH /api/channel-profiles/{id}/channels/bulk-update with the
 *     selected channel_ids and refresh the profile.
 *   - Selecting/applying does not interfere with the existing per-row
 *     toggle + "Save Changes" pending-diff flow.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ChannelProfilesListModal } from './ChannelProfilesListModal';
import type { Channel, ChannelGroup, ChannelProfile } from '../types';

vi.mock('../services/api', () => ({
  getChannelProfiles: vi.fn(),
  getChannelProfile: vi.fn(),
  createChannelProfile: vi.fn(),
  updateChannelProfile: vi.fn(),
  deleteChannelProfile: vi.fn(),
  updateProfileChannel: vi.fn(),
  bulkUpdateProfileChannels: vi.fn(),
}));

const mockSuccess = vi.fn();
const mockError = vi.fn();
const mockNotifications = { success: mockSuccess, error: mockError, warning: vi.fn(), info: vi.fn() };
vi.mock('../contexts/NotificationContext', () => ({
  useNotifications: () => mockNotifications,
}));

import * as api from '../services/api';

const profile: ChannelProfile = { id: 1, name: 'Kids Profile', channels: [10] };

const channels: Channel[] = [
  {
    id: 10, channel_number: 1, name: 'Cartoon Network', channel_group_id: null,
    tvg_id: null, tvc_guide_stationid: null, epg_data_id: null, streams: [],
    stream_profile_id: null, uuid: 'u1', logo_id: null, auto_created: false,
    auto_created_by: null, auto_created_by_name: null,
  },
  {
    id: 11, channel_number: 2, name: 'Nick Jr', channel_group_id: null,
    tvg_id: null, tvc_guide_stationid: null, epg_data_id: null, streams: [],
    stream_profile_id: null, uuid: 'u2', logo_id: null, auto_created: false,
    auto_created_by: null, auto_created_by_name: null,
  },
];

const channelGroups: ChannelGroup[] = [];

describe('ChannelProfilesListModal — bulk apply-to-selected (bead hq3de.i)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getChannelProfiles).mockResolvedValue([profile]);
  });

  async function openChannelsView() {
    render(
      <ChannelProfilesListModal
        isOpen={true}
        onClose={vi.fn()}
        onSaved={vi.fn()}
        channels={channels}
        channelGroups={channelGroups}
      />
    );
    await waitFor(() => screen.getByText('Kids Profile'));
    fireEvent.click(screen.getByText('Kids Profile'));
    await waitFor(() => screen.getByText('Cartoon Network'));
  }

  it('does not show the bulk-apply toolbar until a channel is selected', async () => {
    await openChannelsView();
    expect(screen.queryByText(/Apply to Selected/)).not.toBeInTheDocument();
  });

  it('selecting a channel reveals the bulk-apply toolbar with a count', async () => {
    await openChannelsView();

    fireEvent.click(screen.getByLabelText('Select Cartoon Network for bulk apply'));

    expect(screen.getByText('1 selected')).toBeInTheDocument();
    expect(screen.getByText('Apply to Selected: Enable')).toBeInTheDocument();
    expect(screen.getByText('Apply to Selected: Disable')).toBeInTheDocument();
  });

  it('applies enable to selected channels via the bulk endpoint and refreshes the profile', async () => {
    vi.mocked(api.bulkUpdateProfileChannels).mockResolvedValue({});
    vi.mocked(api.getChannelProfile).mockResolvedValue({ id: 1, name: 'Kids Profile', channels: [10, 11] });

    await openChannelsView();

    fireEvent.click(screen.getByLabelText('Select Cartoon Network for bulk apply'));
    fireEvent.click(screen.getByLabelText('Select Nick Jr for bulk apply'));
    fireEvent.click(screen.getByText('Apply to Selected: Enable'));

    await waitFor(() => {
      expect(api.bulkUpdateProfileChannels).toHaveBeenCalledWith(1, expect.arrayContaining([10, 11]), true);
    });
    await waitFor(() => {
      expect(api.getChannelProfile).toHaveBeenCalledWith(1);
    });
    // Selection clears after a successful apply.
    await waitFor(() => {
      expect(screen.queryByText(/selected/)).not.toBeInTheDocument();
    });
  });

  it('applies disable to selected channels via the bulk endpoint', async () => {
    vi.mocked(api.bulkUpdateProfileChannels).mockResolvedValue({});
    vi.mocked(api.getChannelProfile).mockResolvedValue({ id: 1, name: 'Kids Profile', channels: [] });

    await openChannelsView();

    fireEvent.click(screen.getByLabelText('Select Cartoon Network for bulk apply'));
    fireEvent.click(screen.getByText('Apply to Selected: Disable'));

    await waitFor(() => {
      expect(api.bulkUpdateProfileChannels).toHaveBeenCalledWith(1, [10], false);
    });
  });

  it('Clear Selection empties the selection without calling the API', async () => {
    await openChannelsView();

    fireEvent.click(screen.getByLabelText('Select Cartoon Network for bulk apply'));
    fireEvent.click(screen.getByText('Clear Selection'));

    expect(screen.queryByText(/Apply to Selected/)).not.toBeInTheDocument();
    expect(api.bulkUpdateProfileChannels).not.toHaveBeenCalled();
  });

  it('selecting a channel for bulk apply does not toggle its enable state', async () => {
    await openChannelsView();

    // Cartoon Network starts enabled (in profile.channels). Clicking the
    // bulk-select checkbox must not flip the separate enable toggle.
    const enableToggle = screen.getByLabelText('Select Cartoon Network for bulk apply')
      .closest('.channel-item')!
      .querySelector('.modal-toggle input') as HTMLInputElement;
    expect(enableToggle.checked).toBe(true);

    fireEvent.click(screen.getByLabelText('Select Cartoon Network for bulk apply'));

    expect(enableToggle.checked).toBe(true);
    expect(screen.queryByText(/Save Changes \(/)).not.toBeInTheDocument();
  });

  it('surfaces an error notification when the bulk apply fails', async () => {
    vi.mocked(api.bulkUpdateProfileChannels).mockRejectedValue(new Error('upstream rejected'));

    await openChannelsView();

    fireEvent.click(screen.getByLabelText('Select Cartoon Network for bulk apply'));
    fireEvent.click(screen.getByText('Apply to Selected: Enable'));

    await waitFor(() => {
      expect(mockError).toHaveBeenCalledWith('upstream rejected', 'Profiles');
    });
  });
});
