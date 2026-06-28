/**
 * EPG source-priority ordering of EditChannelModal search results
 * (bead enhancedchannelmanager-s5vyz.4 / .5 — frontend half).
 *
 * The modal sorts EPG search results by source priority DESC *before* the
 * `.slice(0, 100)` display cap. These tests prove:
 *   - a high-priority entry that would fall outside the unsorted 100-window
 *     still appears (and appears first) after sorting;
 *   - equal-priority entries preserve their input (relevance) order
 *     (Array.sort is stable);
 *   - missing/undefined priority is treated as 0 and does not throw.
 *
 * Both rendered dropdowns are exercised: the main EPG dropdown
 * (`filteredEpgData`) and the TVG-ID picker (`filteredTvgIdEpgData`).
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { EditChannelModal } from './EditChannelModal';
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

type EpgEntry = {
  id: number;
  tvg_id: string;
  name: string;
  icon_url: string | null;
  epg_source: number;
};

type Source = { id: number; name: string; priority?: number };

function baseProps(epgData: EpgEntry[], epgSources: Source[]) {
  return {
    channel: CHANNEL,
    logos: [],
    epgData,
    epgSources,
    streamProfiles: [],
    onClose: vi.fn(),
    onSave: vi.fn().mockResolvedValue(undefined),
    onLogoCreate: vi.fn(),
    onLogoUpload: vi.fn(),
  };
}

/** Open the main EPG dropdown, type a search, and return rendered result names in order. */
function searchEpgDropdownNames(term: string): string[] {
  // The main EPG dropdown input opens on focus (placeholder "Search EPG data...").
  // It is the input whose dropdown renders `.epg-dropdown` (not the tvg-id picker).
  const inputs = screen.getAllByPlaceholderText('Search EPG data...');
  // The epgDropdown input is the one inside `.epg-search-row`; pick the last
  // "Search EPG data..." input which is the always-rendered epg-search input.
  const input = inputs[inputs.length - 1];
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: term } });
  const items = document.querySelectorAll('.epg-dropdown .epg-dropdown-item .epg-dropdown-name');
  return Array.from(items).map((el) => el.textContent ?? '');
}

describe('EditChannelModal EPG search source-priority ordering', () => {
  it('surfaces a high-priority entry that would be outside the unsorted slice cap, first', () => {
    // 120 matching entries. Entry "ZZZ-PREFERRED" is LAST in input order
    // (index 119) so it falls outside the unsorted slice(0,100) window — but it
    // belongs to the highest-priority source, so after sorting it must be first.
    const epgSources: Source[] = [
      { id: 1, name: 'Backup', priority: 1 },
      { id: 2, name: 'Preferred', priority: 100 },
    ];
    const epgData: EpgEntry[] = [];
    for (let i = 0; i < 119; i++) {
      epgData.push({
        id: i + 1,
        name: `ESPN ${String(i).padStart(3, '0')}`,
        tvg_id: `espn${i}.us`,
        icon_url: null,
        epg_source: 1, // low-priority source
      });
    }
    epgData.push({
      id: 9999,
      name: 'ESPN ZZZ-PREFERRED',
      tvg_id: 'espnzzz.us',
      icon_url: null,
      epg_source: 2, // high-priority source, but last in input order
    });

    render(<EditChannelModal {...baseProps(epgData, epgSources)} />);
    const names = searchEpgDropdownNames('ESPN');

    expect(names.length).toBe(100); // slice cap applied
    expect(names[0]).toBe('ESPN ZZZ-PREFERRED'); // high-priority surfaced first
    expect(names).toContain('ESPN ZZZ-PREFERRED'); // and not truncated out
  });

  it('preserves input (relevance) order for equal-priority entries (stable sort)', () => {
    // All entries share one source => equal priority => stable order preserved.
    const epgSources: Source[] = [{ id: 1, name: 'Only', priority: 5 }];
    const epgData: EpgEntry[] = [
      { id: 1, name: 'ESPN Alpha', tvg_id: 'a.us', icon_url: null, epg_source: 1 },
      { id: 2, name: 'ESPN Bravo', tvg_id: 'b.us', icon_url: null, epg_source: 1 },
      { id: 3, name: 'ESPN Charlie', tvg_id: 'c.us', icon_url: null, epg_source: 1 },
    ];
    render(<EditChannelModal {...baseProps(epgData, epgSources)} />);
    const names = searchEpgDropdownNames('ESPN');
    expect(names).toEqual(['ESPN Alpha', 'ESPN Bravo', 'ESPN Charlie']);
  });

  it('treats missing/undefined source priority as 0 without throwing', () => {
    // Source 2 has no priority field; source 1 has priority 10. Entry from
    // source 1 must outrank the priority-less one, and rendering must not throw.
    const epgSources: Source[] = [
      { id: 1, name: 'HasPriority', priority: 10 },
      { id: 2, name: 'NoPriority' }, // priority undefined
    ];
    const epgData: EpgEntry[] = [
      { id: 1, name: 'ESPN NoPrio', tvg_id: 'np.us', icon_url: null, epg_source: 2 },
      { id: 2, name: 'ESPN HasPrio', tvg_id: 'hp.us', icon_url: null, epg_source: 1 },
    ];
    render(<EditChannelModal {...baseProps(epgData, epgSources)} />);
    const names = searchEpgDropdownNames('ESPN');
    expect(names[0]).toBe('ESPN HasPrio');
  });

  it('TVG-ID picker dropdown is also sorted by source priority before slicing', () => {
    const epgSources: Source[] = [
      { id: 1, name: 'Backup', priority: 1 },
      { id: 2, name: 'Preferred', priority: 100 },
    ];
    const epgData: EpgEntry[] = [];
    for (let i = 0; i < 119; i++) {
      epgData.push({
        id: i + 1,
        name: `ESPN ${String(i).padStart(3, '0')}`,
        tvg_id: `espn${i}.us`,
        icon_url: null,
        epg_source: 1,
      });
    }
    epgData.push({
      id: 9999,
      name: 'ESPN ZZZ-PREFERRED',
      tvg_id: 'espnzzz.us',
      icon_url: null,
      epg_source: 2,
    });

    render(<EditChannelModal {...baseProps(epgData, epgSources)} />);
    // Open the TVG-ID picker via its button. Two "Get from EPG" buttons exist
    // (this one + the LCN/Gracenote fetch); select by its unique title.
    fireEvent.click(screen.getByTitle('Search EPG data for TVG-ID'));
    const picker = document.querySelector('.tvg-id-picker') as HTMLElement;
    const searchInput = within(picker).getByPlaceholderText('Search EPG data...');
    fireEvent.change(searchInput, { target: { value: 'ESPN' } });

    const names = Array.from(
      picker.querySelectorAll('.epg-dropdown-item .epg-dropdown-name'),
    ).map((el) => el.textContent ?? '');
    expect(names.length).toBe(100);
    expect(names[0]).toBe('ESPN ZZZ-PREFERRED');
  });
});
