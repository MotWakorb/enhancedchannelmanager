/**
 * Every label an operator is told to look for is the label they will see.
 *
 * The Journal filter dropdown offered "Merge Not Applied" and the row badge
 * rendered the same action type as "Merge Unapplied", because the badge went
 * through a generic title-case of the identifier. The Pending Merges notice
 * tells operators to look in the Journal under the FIRST wording — so an
 * operator who followed the instruction found a badge with different words and
 * nothing to tell them the two were the same row type.
 *
 * The property: for every action type with a hand-written label, the badge
 * rendered for a row of that type reads the SAME string the filter offers.
 * `ACTION_TYPE_LABELS` is what makes that structural — one entry feeds both —
 * so what is pinned here is the rendered output of the only current entry plus
 * the untouched generic path. This is an assertion about `merge_unapplied`,
 * not an enumeration of the dropdown; a second hand-written label would need
 * its own case here.
 */
import type * as React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { JournalTab } from './JournalTab';
import { NotificationProvider } from '../../contexts/NotificationContext';
import * as api from '../../services/api';
import type { JournalEntry, JournalResponse, JournalStats } from '../../types';

vi.mock('../../services/api');

const renderWithProviders = (ui: React.JSX.Element) =>
  render(<NotificationProvider>{ui}</NotificationProvider>);

const makeEntry = (overrides: Partial<JournalEntry> = {}): JournalEntry => ({
  id: 1,
  timestamp: '2026-06-24T12:00:00Z',
  category: 'channel',
  action_type: 'create',
  entity_id: 42,
  entity_name: 'Test Channel',
  description: 'Created channel',
  before_value: null,
  after_value: null,
  user_initiated: true,
  mutation_source: 'ui',
  batch_id: null,
  ...overrides,
});

const mockResponse = (entries: JournalEntry[]): JournalResponse => ({
  count: entries.length,
  page: 1,
  page_size: 50,
  total_pages: 1,
  results: entries,
});

const mockStats: JournalStats = {
  total_entries: 1,
  by_category: { channel: 1 },
  by_action_type: { merge_unapplied: 1 },
  date_range: { oldest: '2026-06-01T00:00:00Z', newest: '2026-06-24T12:00:00Z' },
};

describe('JournalTab — filter labels and row badges agree', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getJournalStats).mockResolvedValue(mockStats);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders the merge_unapplied badge with the label the filter offers', async () => {
    vi.mocked(api.getJournalEntries).mockResolvedValue(
      mockResponse([
        makeEntry({
          action_type: 'merge_unapplied',
          description: 'Accepted a merge that could not be applied',
        }),
      ]),
    );

    renderWithProviders(<JournalTab />);

    await waitFor(() => {
      expect(screen.getByText('Merge Not Applied')).toBeInTheDocument();
    });
    // The wording an operator is sent to look for. "Merge Unapplied" was the
    // generic title-case of the identifier and is what they used to see.
    expect(screen.queryByText('Merge Unapplied')).toBeNull();
  });

  it('leaves the generic title-case in place for every other action type', async () => {
    vi.mocked(api.getJournalEntries).mockResolvedValue(
      mockResponse([makeEntry({ action_type: 'stream_add' })]),
    );

    renderWithProviders(<JournalTab />);

    await waitFor(() => {
      expect(screen.getByText('Stream Add')).toBeInTheDocument();
    });
  });
});
