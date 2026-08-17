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
 * so what is pinned here is the rendered output of each entry plus the
 * untouched generic path. Each hand-written label carries its own case: these
 * are assertions about specific action types, not an enumeration of the
 * dropdown.
 *
 * `bulk_merge_incomplete` joined later and shows why the last case matters.
 * The backend emits it for a bulk-merge group whose source channels are not
 * all gone, and `docs/api.md` tells an operator to filter the Journal on the
 * action type to find the merges that need attention — but it was in neither
 * the `JournalActionType` union nor `ACTION_TYPE_LABELS`, so it fell through
 * the generic transform and was absent from the Action dropdown entirely. The
 * advice named a filter that did not exist.
 */
import type * as React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
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

  it('renders the bulk_merge_incomplete badge with the label the filter offers', async () => {
    vi.mocked(api.getJournalEntries).mockResolvedValue(
      mockResponse([
        makeEntry({
          action_type: 'bulk_merge_incomplete',
          description: "Merged 3 channels into 'BBC One'; 2 source channels remain",
        }),
      ]),
    );

    renderWithProviders(<JournalTab />);

    await waitFor(() => {
      expect(screen.getByText('Merge Incomplete')).toBeInTheDocument();
    });
    // What the generic title-case of the identifier produced, which is what an
    // operator used to see and what no filter or doc ever said.
    expect(screen.queryByText('Bulk Merge Incomplete')).toBeNull();
  });

  it.each([
    ['merge_unapplied' as const],
    ['bulk_merge_incomplete' as const],
  ])(
    'offers %s in the Action dropdown under the same words as its badge',
    async (actionType) => {
      vi.mocked(api.getJournalEntries).mockResolvedValue(
        mockResponse([makeEntry({ action_type: actionType })]),
      );

      renderWithProviders(<JournalTab />);

      // The badge is the operator's starting point: read what it actually says
      // rather than restating a literal here, so a label changed in one place
      // and not the other fails this test instead of passing both halves.
      const badgeText = await waitFor(() => {
        const text = document
          .querySelector('.entry-row .action-badge')
          ?.textContent?.trim();
        expect(text).toBeTruthy();
        return text as string;
      });

      fireEvent.click(screen.getByText('All Actions'));

      expect(
        await screen.findByRole('option', { name: badgeText as string }),
      ).toBeInTheDocument();
    },
  );

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
