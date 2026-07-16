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

/** Build `count` synthetic duplicate groups, each with 2 channels, all ids unique. */
function makeSyntheticGroups(count: number): FindDuplicatesResponse {
  const groups = Array.from({ length: count }, (_, i) => ({
    normalized_name: `channel ${i}`,
    channels: [
      {
        id: i * 2 + 1,
        name: `Channel ${i} A`,
        normalized_name: `channel ${i}`,
        channel_number: i,
        stream_count: 2,
        channel_group_id: null,
        channel_group_name: '',
      },
      {
        id: i * 2 + 2,
        name: `Channel ${i} B`,
        normalized_name: `channel ${i}`,
        channel_number: null,
        stream_count: 0,
        channel_group_id: null,
        channel_group_name: '',
      },
    ],
  }));
  return {
    groups,
    total_groups: count,
    total_duplicate_channels: count * 2,
  };
}

describe('FindDuplicatesModal — renders actual rows at scale (enhancedchannelmanager-uahp6)', () => {
  const mockClose = vi.fn();
  const mockMerged = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('mounts one .dup-group DOM element per group for a 100-group result set', async () => {
    vi.mocked(api.findDuplicateChannels).mockResolvedValue(makeSyntheticGroups(100));

    const { container } = render(<FindDuplicatesModal onClose={mockClose} onMerged={mockMerged} />);

    await waitFor(() => {
      expect(screen.getByText(/Found 100 groups/)).toBeInTheDocument();
    });

    // This is the assertion the pre-existing test file never made: that the
    // group data arriving in state actually produced DOM rows. A rendering
    // regression that silently drops rows (the merge-blind bug) would leave
    // `groups.length === 100` true while this count stays 0.
    expect(container.querySelectorAll('.dup-group')).toHaveLength(100);

    // With rows genuinely present, the safety net must not trip.
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Merge 100 Groups/ })).not.toBeDisabled();
    });
  });
});

describe('FindDuplicatesModal — scope label and scoped API call (enhancedchannelmanager-uahp6)', () => {
  const mockClose = vi.fn();
  const mockMerged = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.findDuplicateChannels).mockResolvedValue({
      groups: [],
      total_groups: 0,
      total_duplicate_channels: 0,
    });
  });

  it('calls the API with the given channelIds and labels the scope', async () => {
    render(
      <FindDuplicatesModal
        onClose={mockClose}
        onMerged={mockMerged}
        channelIds={[10, 20, 30]}
      />
    );

    await waitFor(() => {
      expect(api.findDuplicateChannels).toHaveBeenCalledWith([10, 20, 30], false);
    });
    expect(screen.getByText('Scanned 3 selected channels')).toBeInTheDocument();
  });

  it('calls the API with no scope and labels a global scan when channelIds is omitted', async () => {
    render(<FindDuplicatesModal onClose={mockClose} onMerged={mockMerged} />);

    await waitFor(() => {
      expect(api.findDuplicateChannels).toHaveBeenCalledWith(undefined, false);
    });
    expect(screen.getByText('Scanned all channels')).toBeInTheDocument();
  });

  it('treats an empty channelIds array as global, not a zero-channel scope', async () => {
    render(<FindDuplicatesModal onClose={mockClose} onMerged={mockMerged} channelIds={[]} />);

    await waitFor(() => {
      expect(api.findDuplicateChannels).toHaveBeenCalledWith(undefined, false);
    });
    expect(screen.getByText('Scanned all channels')).toBeInTheDocument();
  });

  it('uses singular phrasing for exactly one selected channel', async () => {
    render(<FindDuplicatesModal onClose={mockClose} onMerged={mockMerged} channelIds={[10]} />);

    await waitFor(() => {
      expect(screen.getByText('Scanned 1 selected channel')).toBeInTheDocument();
    });
  });
});

describe('FindDuplicatesModal — merge-blind DOM-presence guard (enhancedchannelmanager-uahp6)', () => {
  const mockClose = vi.fn();
  const mockMerged = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('disables Merge and shows an inline error when groups exist but zero rows rendered', async () => {
    vi.mocked(api.findDuplicateChannels).mockResolvedValue(makeSyntheticGroups(5));

    // Surgically fake a render-side failure: `.dup-group` lookups on the
    // group-list container return nothing, as they would if a virtualization
    // or keying bug dropped every row, while every other querySelectorAll
    // call (including Testing Library's internals) behaves normally.
    const originalQSA = Element.prototype.querySelectorAll;
    const qsaSpy = vi
      .spyOn(Element.prototype, 'querySelectorAll')
      .mockImplementation(function (this: Element, selector: string) {
        if (selector === '.dup-group') {
          return [] as unknown as NodeListOf<Element>;
        }
        return originalQSA.call(this, selector);
      });

    try {
      render(<FindDuplicatesModal onClose={mockClose} onMerged={mockMerged} />);

      await waitFor(() => {
        expect(screen.getByText(/failed to render/)).toBeInTheDocument();
      });

      expect(screen.getByRole('button', { name: /Merge 5 Groups/ })).toBeDisabled();
    } finally {
      qsaSpy.mockRestore();
    }
  });

  it('does not trip the guard when rows render normally', async () => {
    vi.mocked(api.findDuplicateChannels).mockResolvedValue(makeSyntheticGroups(5));

    render(<FindDuplicatesModal onClose={mockClose} onMerged={mockMerged} />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Merge 5 Groups/ })).toBeInTheDocument();
    });

    expect(screen.queryByText(/failed to render/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Merge 5 Groups/ })).not.toBeDisabled();
  });
});

describe('FindDuplicatesModal — ignore-spacing fold toggle (GH #645)', () => {
  const mockClose = vi.fn();
  const mockMerged = vi.fn();

  const EMPTY_RESPONSE: FindDuplicatesResponse = {
    groups: [],
    total_groups: 0,
    total_duplicate_channels: 0,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.findDuplicateChannels).mockResolvedValue(DUPLICATE_RESPONSE);
  });

  it('renders the toggle unchecked and scans without folding by default', async () => {
    render(<FindDuplicatesModal onClose={mockClose} onMerged={mockMerged} />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Merge 1 Group/ })).toBeInTheDocument();
    });

    const toggle = screen.getByRole('checkbox', { name: /ignore spacing differences/i });
    expect(toggle).not.toBeChecked();
    expect(api.findDuplicateChannels).toHaveBeenCalledTimes(1);
    expect(api.findDuplicateChannels).toHaveBeenCalledWith(undefined, false);
  });

  it('re-scans with folding enabled when the toggle is switched on', async () => {
    render(<FindDuplicatesModal onClose={mockClose} onMerged={mockMerged} />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Merge 1 Group/ })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('checkbox', { name: /ignore spacing differences/i }));

    await waitFor(() => {
      expect(api.findDuplicateChannels).toHaveBeenCalledTimes(2);
    });
    expect(api.findDuplicateChannels).toHaveBeenLastCalledWith(undefined, true);
  });

  it('passes the channel scope through on a folded re-scan', async () => {
    render(<FindDuplicatesModal onClose={mockClose} onMerged={mockMerged} channelIds={[1, 2, 3]} />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Merge 1 Group/ })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('checkbox', { name: /ignore spacing differences/i }));

    await waitFor(() => {
      expect(api.findDuplicateChannels).toHaveBeenLastCalledWith([1, 2, 3], true);
    });
  });

  it('keeps the toggle available when the exact scan finds nothing', async () => {
    vi.mocked(api.findDuplicateChannels).mockResolvedValue(EMPTY_RESPONSE);

    render(<FindDuplicatesModal onClose={mockClose} onMerged={mockMerged} />);

    await waitFor(() => {
      expect(screen.getByText(/No duplicate channels found/)).toBeInTheDocument();
    });

    // Operator can still opt into the folded scan from the empty state.
    const toggle = screen.getByRole('checkbox', { name: /ignore spacing differences/i });
    vi.mocked(api.findDuplicateChannels).mockResolvedValue(DUPLICATE_RESPONSE);
    fireEvent.click(toggle);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Merge 1 Group/ })).toBeInTheDocument();
    });
  });
});
