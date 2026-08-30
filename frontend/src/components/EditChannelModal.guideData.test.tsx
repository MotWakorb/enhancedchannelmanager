/**
 * EditChannelModal — "no guide data loaded" is not "no match"
 * (bead enhancedchannelmanager-3vtim).
 *
 * Backup/restore drill run 2026-08-08-run17. An operator added an EPG source,
 * waited for `status=success` (14,663 rows on the Dispatcharr side, `KERA`
 * among them), opened Edit Channel without reloading, typed `KERA` — and the
 * EPG Data picker said "No EPG data found". Not one `/api/epg/*` request left
 * the browser for the entire failing attempt: the modal filters the in-memory
 * `epgData` prop client-side, and that prop was still the empty array `App`
 * loaded at startup, before the source existed.
 *
 * "No EPG data found" is a claim about the SEARCH. An empty cache is a claim
 * about the APP, and saying the first when the second is true sent the operator
 * looking for missing guide data in Dispatcharr, where it was all present. The
 * refresh itself is fixed in EPGManagerTab (it publishes `epg-data` when a
 * download completes); this is the honest empty state for the window before it
 * lands, and for the genuinely-no-sources case.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { EditChannelModal } from './EditChannelModal';
import type { Channel } from '../types';

const CHANNEL: Channel = {
  id: 1,
  name: 'TX | Dallas | PBS KERA',
  channel_number: 103,
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

type EpgEntry = {
  id: number;
  tvg_id: string;
  name: string;
  icon_url: string | null;
  epg_source: number;
};

function baseProps(epgData: EpgEntry[], epgDataLoading = false) {
  return {
    channel: CHANNEL,
    logos: [],
    epgData,
    epgSources: [{ id: 1, name: 'EPG Guru US', priority: 1 }],
    streamProfiles: [],
    onClose: vi.fn(),
    onSave: vi.fn().mockResolvedValue(undefined),
    onLogoCreate: vi.fn(),
    onLogoUpload: vi.fn(),
    epgDataLoading,
  };
}

const KERA: EpgEntry = {
  id: 3425,
  name: 'KERA',
  tvg_id: 'KERA(PBS)(KERA).us',
  icon_url: null,
  epg_source: 1,
};

/** Focus the main EPG dropdown input and type a search term. */
function searchEpgDropdown(term: string) {
  const inputs = screen.getAllByPlaceholderText('Search EPG data...');
  const input = inputs[inputs.length - 1];
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: term } });
}

describe('EditChannelModal — empty guide cache vs. empty search result', () => {
  it('says the guide has not loaded rather than "No EPG data found" when the cache is empty', () => {
    render(<EditChannelModal {...baseProps([])} />);

    searchEpgDropdown('KERA');

    const empty = screen.getByTestId('epg-picker-no-guide-data');
    expect(empty.textContent).toContain('Guide data has not loaded yet');
    expect(screen.queryByText('No EPG data found')).toBeNull();
  });

  it('still says "No EPG data found" when the guide IS loaded and nothing matches', () => {
    render(<EditChannelModal {...baseProps([KERA])} />);

    searchEpgDropdown('nothing-matches-this');

    expect(screen.queryByTestId('epg-picker-no-guide-data')).toBeNull();
    expect(screen.getByText('No EPG data found')).toBeTruthy();
  });

  it('leaves the in-flight load showing its own spinner, not the empty state', () => {
    render(<EditChannelModal {...baseProps([], true)} />);

    searchEpgDropdown('KERA');

    expect(screen.queryByTestId('epg-picker-no-guide-data')).toBeNull();
    expect(screen.getByText('Loading...')).toBeTruthy();
  });

  it('applies the same distinction to the TVG-ID picker', () => {
    render(<EditChannelModal {...baseProps([])} />);

    // The TVG-ID picker opens from the "Get from EPG" button beside the field.
    fireEvent.click(screen.getByTitle('Search EPG data for TVG-ID'));

    const empty = screen.getByTestId('tvg-id-picker-no-guide-data');
    expect(empty.textContent).toContain('Guide data has not loaded yet');
  });
});
