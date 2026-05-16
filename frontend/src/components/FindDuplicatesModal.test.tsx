/**
 * Unit tests for FindDuplicatesModal — bulk merge error-surface behaviour.
 *
 * bd-7j6v1 (follow-up to bd-ct9wl): the backend POST /api/channels/bulk-merge
 * now returns 422 with a human-readable detail string when submitted source IDs
 * are stale (bd-ozhkf). Verify the modal renders that detail in the error
 * banner rather than a generic fallback, and that non-422 errors produce a
 * reasonable message.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { FindDuplicatesModal } from './FindDuplicatesModal';
import { HttpError } from '../services/httpClient';

// Mock both API calls this modal makes
vi.mock('../services/api', () => ({
  findDuplicateChannels: vi.fn(),
  bulkMergeChannels: vi.fn(),
}));

import * as api from '../services/api';
import type { FindDuplicatesResponse } from '../services/api';

// Minimal duplicate-group response to put the modal in a state where
// the Merge button is enabled.
const DUPLICATE_RESPONSE: FindDuplicatesResponse = {
  groups: [
    {
      normalized_name: 'live a',
      channels: [
        {
          id: 100,
          name: 'Live A',
          normalized_name: 'live a',
          channel_number: 1,
          stream_count: 2,
          channel_group_id: null,
          channel_group_name: '',
        },
        {
          id: 200,
          name: 'Live A (dup)',
          normalized_name: 'live a',
          channel_number: null,
          stream_count: 0,
          channel_group_id: null,
          channel_group_name: '',
        },
      ],
    },
  ],
  total_groups: 1,
  total_duplicate_channels: 1,
};

describe('FindDuplicatesModal — bulk merge 422 detail surface (bd-7j6v1)', () => {
  const mockClose = vi.fn();
  const mockMerged = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.findDuplicateChannels).mockResolvedValue(DUPLICATE_RESPONSE);
  });

  it('renders the backend 422 detail string in the error banner, not a generic fallback', async () => {
    const detail =
      'Source channels [200] no longer exist — refresh the channels list and try again';
    vi.mocked(api.bulkMergeChannels).mockRejectedValue(new HttpError(detail, 422));

    render(<FindDuplicatesModal onClose={mockClose} onMerged={mockMerged} />);

    // Wait for the duplicate list to load and button to appear
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Merge 1 Group/ })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /Merge 1 Group/ }));

    await waitFor(() => {
      expect(screen.getByText(detail)).toBeInTheDocument();
    });
    // The generic fallback copy must NOT appear.
    expect(screen.queryByText('Merge failed')).not.toBeInTheDocument();
  });

  it('shows a reasonable message for non-422 errors (generic HttpError)', async () => {
    vi.mocked(api.bulkMergeChannels).mockRejectedValue(
      new HttpError('Internal Server Error', 500),
    );

    render(<FindDuplicatesModal onClose={mockClose} onMerged={mockMerged} />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Merge 1 Group/ })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /Merge 1 Group/ }));

    await waitFor(() => {
      // Any non-empty, non-generic error message is acceptable for 500.
      expect(screen.getByText('Internal Server Error')).toBeInTheDocument();
    });
  });

  it('falls back to generic copy when the thrown value is not an Error', async () => {
    vi.mocked(api.bulkMergeChannels).mockRejectedValue('raw string error');

    render(<FindDuplicatesModal onClose={mockClose} onMerged={mockMerged} />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Merge 1 Group/ })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /Merge 1 Group/ }));

    await waitFor(() => {
      expect(screen.getByText('Merge failed')).toBeInTheDocument();
    });
  });
});
