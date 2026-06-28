/**
 * EPG source-priority ordering of the BulkEPGAssignModal manual-search card
 * (bead enhancedchannelmanager-s5vyz.4 / .5 — frontend half).
 *
 * EPGSearchCard sorts its search results by source priority DESC *before* the
 * `.slice(0, MAX_SEARCH_RESULTS=50)` display cap. These tests prove:
 *   - a high-priority entry that would fall outside the unsorted 50-window
 *     still appears (and appears first) after sorting;
 *   - equal-priority entries preserve their input (relevance) order
 *     (Array.sort is stable);
 *   - missing/undefined source priority is treated as 0 and does not throw.
 */
import { describe, it, expect, vi } from 'vitest';
import { render } from '@testing-library/react';
import { EPGSearchCard } from './BulkEPGAssignModal';
import type { EPGData, EPGSource } from '../types';

function makeSource(id: number, name: string, priority?: number): EPGSource {
  return {
    id,
    name,
    source_type: 'xmltv',
    url: null,
    api_key: null,
    username: null,
    is_active: true,
    file_path: null,
    refresh_interval: 0,
    priority: priority ?? 0,
    status: 'idle' as EPGSource['status'],
    last_message: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: null,
    custom_properties: null,
    epg_data_count: '0',
  };
}

function renderCard(epgData: EPGData[], epgSources: EPGSource[], searchTerm: string) {
  return render(
    <EPGSearchCard
      channelName="ESPN"
      normalizedName="espn"
      detectedCountry={null}
      epgData={epgData}
      epgSources={epgSources}
      selectedEpg={undefined}
      onSelect={vi.fn()}
      onClose={vi.fn()}
      searchTerm={searchTerm}
      onSearchChange={vi.fn()}
    />,
  );
}

/** Rendered result names in DOM order. */
function resultNames(container: HTMLElement): string[] {
  return Array.from(
    container.querySelectorAll('.epg-search-options .epg-name'),
  ).map((el) => el.textContent ?? '');
}

describe('BulkEPGAssignModal EPGSearchCard source-priority ordering', () => {
  it('surfaces a high-priority entry that would be outside the unsorted slice cap, first', () => {
    // 60 matching entries. The high-priority entry is LAST in input order
    // (index 59) so it falls outside the unsorted slice(0,50) window — yet it
    // must surface first (and remain visible) after the priority sort.
    const epgSources = [
      makeSource(1, 'Backup', 1),
      makeSource(2, 'Preferred', 100),
    ];
    const epgData: EPGData[] = [];
    for (let i = 0; i < 59; i++) {
      epgData.push({
        id: i + 1,
        name: `ESPN ${String(i).padStart(3, '0')}`,
        tvg_id: `espn${i}.us`,
        icon_url: null,
        epg_source: 1, // low priority
      });
    }
    epgData.push({
      id: 9999,
      name: 'ESPN ZZZ-PREFERRED',
      tvg_id: 'espnzzz.us',
      icon_url: null,
      epg_source: 2, // high priority, last in input order
    });

    const { container } = renderCard(epgData, epgSources, 'ESPN');
    const names = resultNames(container);
    expect(names.length).toBe(50); // slice cap applied
    expect(names[0]).toBe('ESPN ZZZ-PREFERRED'); // surfaced first
    expect(names).toContain('ESPN ZZZ-PREFERRED'); // not truncated out
  });

  it('preserves input (relevance) order for equal-priority entries (stable sort)', () => {
    const epgSources = [makeSource(1, 'Only', 5)];
    const epgData: EPGData[] = [
      { id: 1, name: 'ESPN Alpha', tvg_id: 'a.us', icon_url: null, epg_source: 1 },
      { id: 2, name: 'ESPN Bravo', tvg_id: 'b.us', icon_url: null, epg_source: 1 },
      { id: 3, name: 'ESPN Charlie', tvg_id: 'c.us', icon_url: null, epg_source: 1 },
    ];
    const { container } = renderCard(epgData, epgSources, 'ESPN');
    expect(resultNames(container)).toEqual(['ESPN Alpha', 'ESPN Bravo', 'ESPN Charlie']);
  });

  it('treats missing/undefined source priority as 0 without throwing', () => {
    const epgSources = [
      makeSource(1, 'HasPriority', 10),
      makeSource(2, 'NoPriority'), // priority defaults to 0 here; also test an absent source below
    ];
    const epgData: EPGData[] = [
      { id: 1, name: 'ESPN NoPrio', tvg_id: 'np.us', icon_url: null, epg_source: 2 },
      { id: 2, name: 'ESPN HasPrio', tvg_id: 'hp.us', icon_url: null, epg_source: 1 },
      // Entry whose source is entirely absent from epgSources — must not throw.
      { id: 3, name: 'ESPN Orphan', tvg_id: 'orph.us', icon_url: null, epg_source: 777 },
    ];
    const { container } = renderCard(epgData, epgSources, 'ESPN');
    const names = resultNames(container);
    expect(names[0]).toBe('ESPN HasPrio');
    expect(names).toContain('ESPN Orphan'); // graceful: no crash, still rendered
  });

  it('re-sorts when the search term changes (sort is keyed on results, not stale)', () => {
    const epgSources = [makeSource(1, 'Backup', 1), makeSource(2, 'Preferred', 100)];
    const epgData: EPGData[] = [
      { id: 1, name: 'ESPN Low', tvg_id: 'low.us', icon_url: null, epg_source: 1 },
      { id: 2, name: 'ESPN High', tvg_id: 'high.us', icon_url: null, epg_source: 2 },
    ];
    const { container, rerender } = renderCard(epgData, epgSources, 'ESPN');
    expect(resultNames(container)[0]).toBe('ESPN High');
    rerender(
      <EPGSearchCard
        channelName="ESPN"
        normalizedName="espn"
        detectedCountry={null}
        epgData={epgData}
        epgSources={epgSources}
        selectedEpg={undefined}
        onSelect={vi.fn()}
        onClose={vi.fn()}
        searchTerm="High"
        onSearchChange={vi.fn()}
      />,
    );
    expect(resultNames(container)).toEqual(['ESPN High']);
  });
});
