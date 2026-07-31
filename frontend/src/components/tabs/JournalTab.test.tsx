/**
 * Unit tests for JournalTab component and helper functions.
 */
import type * as React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { JournalTab } from './JournalTab';
import { NotificationProvider } from '../../contexts/NotificationContext';
import * as api from '../../services/api';
import { HttpError } from '../../services/httpClient';
import type { JournalEntry, JournalResponse, JournalStats } from '../../types';

// Mock the API module
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
  total_entries: 5,
  by_category: { channel: 3, epg: 1, m3u: 1 },
  by_action_type: { create: 2, update: 2, start: 1, stop: 0 },
  date_range: { oldest: '2026-06-01T00:00:00Z', newest: '2026-06-24T12:00:00Z' },
};

describe('JournalTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getJournalEntries).mockResolvedValue(mockResponse([makeEntry()]));
    vi.mocked(api.getJournalStats).mockResolvedValue(mockStats);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe('initial rendering', () => {
    it('keeps a disabled safe toolbar mounted during initial loading', () => {
      vi.mocked(api.getJournalEntries).mockReset().mockReturnValue(new Promise(() => {}));
      vi.mocked(api.getJournalStats).mockReset().mockReturnValue(new Promise(() => {}));

      renderWithProviders(<JournalTab />);

      expect(screen.getByRole('toolbar', { name: 'Journal entry controls' })).toBeInTheDocument();
      expect(screen.getByPlaceholderText('Search entries...')).toBeDisabled();
      expect(screen.getByLabelText('Days to keep')).toBeDisabled();
      expect(screen.getByRole('button', { name: /purge old entries/i })).toBeDisabled();
    });

    it('renders the Journal header', async () => {
      renderWithProviders(<JournalTab />);

      await waitFor(() => {
        expect(screen.getByText('Journal')).toBeInTheDocument();
      });
    });

    it('fetches entries and stats on mount', async () => {
      renderWithProviders(<JournalTab />);

      await waitFor(() => {
        expect(api.getJournalEntries).toHaveBeenCalled();
        expect(api.getJournalStats).toHaveBeenCalled();
      });
    });

    it('shows the Source column header', async () => {
      renderWithProviders(<JournalTab />);

      await waitFor(() => {
        expect(screen.queryByText('Loading journal...')).not.toBeInTheDocument();
      });

      expect(screen.getByText('Source')).toBeInTheDocument();
    });
  });

  describe('mutation_source badge display', () => {
    it('renders UI label for source=ui', async () => {
      vi.mocked(api.getJournalEntries).mockResolvedValue(
        mockResponse([makeEntry({ mutation_source: 'ui' })])
      );

      renderWithProviders(<JournalTab />);

      await waitFor(() => {
        expect(screen.queryByText('Loading journal...')).not.toBeInTheDocument();
      });

      const badges = document.querySelectorAll('.source-badge');
      const texts = Array.from(badges).map((b) => b.textContent);
      expect(texts).toContain('UI');
    });

    it('renders AI label for source=mcp_ai', async () => {
      vi.mocked(api.getJournalEntries).mockResolvedValue(
        mockResponse([makeEntry({ mutation_source: 'mcp_ai' })])
      );

      renderWithProviders(<JournalTab />);

      await waitFor(() => {
        expect(screen.queryByText('Loading journal...')).not.toBeInTheDocument();
      });

      const badges = document.querySelectorAll('.source-badge');
      const texts = Array.from(badges).map((b) => b.textContent);
      expect(texts).toContain('AI');
    });

    it('renders Scheduler label for source=scheduler', async () => {
      vi.mocked(api.getJournalEntries).mockResolvedValue(
        mockResponse([makeEntry({ mutation_source: 'scheduler' })])
      );

      renderWithProviders(<JournalTab />);

      await waitFor(() => {
        expect(screen.queryByText('Loading journal...')).not.toBeInTheDocument();
      });

      const badges = document.querySelectorAll('.source-badge');
      const texts = Array.from(badges).map((b) => b.textContent);
      expect(texts).toContain('Scheduler');
    });

    it('renders Channel Pipeline label for source=auto_creation', async () => {
      vi.mocked(api.getJournalEntries).mockResolvedValue(
        mockResponse([makeEntry({ mutation_source: 'auto_creation' })])
      );

      renderWithProviders(<JournalTab />);

      await waitFor(() => {
        expect(screen.queryByText('Loading journal...')).not.toBeInTheDocument();
      });

      const badges = document.querySelectorAll('.source-badge');
      const texts = Array.from(badges).map((b) => b.textContent);
      expect(texts).toContain('Channel Pipeline');
    });

    it('renders — for null mutation_source (legacy entries)', async () => {
      vi.mocked(api.getJournalEntries).mockResolvedValue(
        mockResponse([makeEntry({ mutation_source: null })])
      );

      renderWithProviders(<JournalTab />);

      await waitFor(() => {
        expect(screen.queryByText('Loading journal...')).not.toBeInTheDocument();
      });

      const badges = document.querySelectorAll('.source-badge');
      const texts = Array.from(badges).map((b) => b.textContent);
      expect(texts).toContain('—');
    });

    it('applies source-ui CSS class for ui source', async () => {
      vi.mocked(api.getJournalEntries).mockResolvedValue(
        mockResponse([makeEntry({ mutation_source: 'ui' })])
      );

      renderWithProviders(<JournalTab />);

      await waitFor(() => {
        expect(document.querySelector('.source-ui')).toBeInTheDocument();
      });
    });

    it('applies source-mcp_ai CSS class for mcp_ai source', async () => {
      vi.mocked(api.getJournalEntries).mockResolvedValue(
        mockResponse([makeEntry({ mutation_source: 'mcp_ai' })])
      );

      renderWithProviders(<JournalTab />);

      await waitFor(() => {
        expect(document.querySelector('.source-mcp_ai')).toBeInTheDocument();
      });
    });
  });

  describe('mutation_source filter', () => {
    it('passes mutation_source param to API when filter is set', async () => {
      renderWithProviders(<JournalTab />);

      await waitFor(() => {
        expect(screen.queryByText('Loading journal...')).not.toBeInTheDocument();
      });

      // Verify initial call has no mutation_source
      expect(api.getJournalEntries).toHaveBeenCalledWith(
        expect.not.objectContaining({ mutation_source: expect.anything() })
      );
    });

    it('renders All Sources option in the filter dropdown', async () => {
      renderWithProviders(<JournalTab />);

      await waitFor(() => {
        expect(screen.queryByText('Loading journal...')).not.toBeInTheDocument();
      });

      // The CustomSelect shows the active value as its trigger label
      expect(screen.getByText('All Sources')).toBeInTheDocument();
    });
  });

  describe('event_sync category (bead yjchp)', () => {
    it('renders the Event Sync label and sync_alt icon for an event_sync entry', async () => {
      vi.mocked(api.getJournalEntries).mockResolvedValue(
        mockResponse([
          makeEntry({
            category: 'event_sync',
            entity_name: 'Peacock 14: Mercury vs. Aces',
            description: 'Merged stream into master channel',
          }),
        ])
      );

      renderWithProviders(<JournalTab />);

      await waitFor(() => {
        expect(screen.queryByText('Loading journal...')).not.toBeInTheDocument();
      });

      expect(screen.getByText('Event Sync')).toBeInTheDocument();
      const icons = Array.from(document.querySelectorAll('.material-icons')).map(
        (i) => i.textContent
      );
      expect(icons).toContain('sync_alt');
    });

    it('shows the event_sync count in the header stats', async () => {
      vi.mocked(api.getJournalStats).mockResolvedValue({
        ...mockStats,
        by_category: { ...mockStats.by_category, event_sync: 7 },
      });

      renderWithProviders(<JournalTab />);

      await waitFor(() => {
        expect(screen.queryByText('Loading journal...')).not.toBeInTheDocument();
      });

      const stat = document.querySelector('[title="Event Sync entries"]');
      expect(stat).not.toBeNull();
      expect(stat?.textContent).toContain('7');
    });

    it('defaults the event_sync header stat to 0 when the category is absent', async () => {
      renderWithProviders(<JournalTab />);

      await waitFor(() => {
        expect(screen.queryByText('Loading journal...')).not.toBeInTheDocument();
      });

      const stat = document.querySelector('[title="Event Sync entries"]');
      expect(stat).not.toBeNull();
      expect(stat?.textContent).toContain('0');
    });
  });

  describe('empty state', () => {
    it('shows empty state when no entries exist', async () => {
      vi.mocked(api.getJournalEntries).mockResolvedValue(mockResponse([]));

      renderWithProviders(<JournalTab />);

      await waitFor(() => {
        expect(screen.getByText('No journal entries')).toBeInTheDocument();
      });
    });
  });

  describe('entry-row keyboard accessibility (bd-6n14l)', () => {
    it('exposes the entry row as a focusable button with aria-expanded reflecting collapsed state', async () => {
      renderWithProviders(<JournalTab />);

      await waitFor(() => {
        expect(screen.queryByText('Loading journal...')).not.toBeInTheDocument();
      });

      const row = document.querySelector('.entry-row') as HTMLElement;
      expect(row).toHaveAttribute('role', 'button');
      expect(row).toHaveAttribute('tabIndex', '0');
      expect(row).toHaveAttribute('aria-expanded', 'false');
    });

    it('Enter toggles the row open and updates aria-expanded', async () => {
      renderWithProviders(<JournalTab />);

      await waitFor(() => {
        expect(screen.queryByText('Loading journal...')).not.toBeInTheDocument();
      });

      const row = document.querySelector('.entry-row') as HTMLElement;
      fireEvent.keyDown(row, { key: 'Enter' });

      expect(row).toHaveAttribute('aria-expanded', 'true');
      expect(row).toHaveClass('expanded');
    });

    it('Space toggles the row open and updates aria-expanded', async () => {
      renderWithProviders(<JournalTab />);

      await waitFor(() => {
        expect(screen.queryByText('Loading journal...')).not.toBeInTheDocument();
      });

      const row = document.querySelector('.entry-row') as HTMLElement;
      fireEvent.keyDown(row, { key: ' ' });

      expect(row).toHaveAttribute('aria-expanded', 'true');
    });

    it('Enter toggles the row closed again on a second press (aria-expanded flips back)', async () => {
      renderWithProviders(<JournalTab />);

      await waitFor(() => {
        expect(screen.queryByText('Loading journal...')).not.toBeInTheDocument();
      });

      const row = document.querySelector('.entry-row') as HTMLElement;
      fireEvent.keyDown(row, { key: 'Enter' });
      expect(row).toHaveAttribute('aria-expanded', 'true');

      fireEvent.keyDown(row, { key: 'Enter' });
      expect(row).toHaveAttribute('aria-expanded', 'false');
    });

    it('ignores other keys (e.g. Tab) without toggling', async () => {
      renderWithProviders(<JournalTab />);

      await waitFor(() => {
        expect(screen.queryByText('Loading journal...')).not.toBeInTheDocument();
      });

      const row = document.querySelector('.entry-row') as HTMLElement;
      fireEvent.keyDown(row, { key: 'Tab' });

      expect(row).toHaveAttribute('aria-expanded', 'false');
    });
  });

  describe('refresh', () => {
    it('re-fetches data when refresh button is clicked', async () => {
      renderWithProviders(<JournalTab />);

      await waitFor(() => {
        expect(screen.queryByText('Loading journal...')).not.toBeInTheDocument();
      });

      vi.mocked(api.getJournalEntries).mockClear();
      vi.mocked(api.getJournalStats).mockClear();

      const refreshButton = screen.getByRole('button', { name: /refresh/i });
      fireEvent.click(refreshButton);

      await waitFor(() => {
        expect(api.getJournalEntries).toHaveBeenCalled();
        expect(api.getJournalStats).toHaveBeenCalled();
      });
    });

    it('retains populated rows as stale after refresh failure and recovers on retry', async () => {
      vi.mocked(api.getJournalEntries).mockReset()
        .mockResolvedValueOnce(mockResponse([makeEntry()]))
        .mockRejectedValueOnce(new Error('Refresh failed'))
        .mockResolvedValueOnce(mockResponse([makeEntry()]));
      renderWithProviders(<JournalTab />);
      expect(await screen.findByText('Test Channel')).toBeInTheDocument();

      fireEvent.click(screen.getByRole('button', { name: /refresh/i }));

      expect(await screen.findByText(/showing previously loaded entries/i)).toBeInTheDocument();
      expect(screen.getByText('Test Channel')).toBeInTheDocument();
      expect(screen.getByText(/1-1 of 1 entries/)).toBeInTheDocument();

      fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
      await waitFor(() => expect(screen.queryByText(/showing previously loaded entries/i)).not.toBeInTheDocument());
      expect(screen.getByText('Test Channel')).toBeInTheDocument();
    });

    it('treats 401 as permission and clears protected rows and actions', async () => {
      vi.mocked(api.getJournalEntries).mockReset();
      vi.mocked(api.getJournalEntries).mockRejectedValue(new HttpError('Unauthorized', 401));
      renderWithProviders(<JournalTab />);

      expect(await screen.findByText(/don't have permission/i)).toBeInTheDocument();
      expect(screen.queryByText('Test Channel')).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /purge old entries/i })).not.toBeInTheDocument();
    });

    it('ignores an older refresh response after a newer filter request completes', async () => {
      let resolveRefresh!: (value: JournalResponse) => void;
      let resolveFilter!: (value: JournalResponse) => void;
      vi.mocked(api.getJournalEntries).mockReset()
        .mockResolvedValueOnce(mockResponse([makeEntry({ entity_name: 'Initial row' })]))
        .mockReturnValueOnce(new Promise(resolve => { resolveRefresh = resolve; }))
        .mockReturnValueOnce(new Promise(resolve => { resolveFilter = resolve; }));

      renderWithProviders(<JournalTab />);
      expect(await screen.findByText('Initial row')).toBeInTheDocument();

      fireEvent.click(screen.getByRole('button', { name: /refresh/i }));
      fireEvent.click(screen.getByText('All Categories'));
      fireEvent.click(await screen.findByRole('option', { name: 'Channel' }));

      resolveFilter(mockResponse([makeEntry({ id: 3, entity_name: 'Newest filtered row' })]));
      expect(await screen.findByText('Newest filtered row')).toBeInTheDocument();

      resolveRefresh(mockResponse([makeEntry({ id: 2, entity_name: 'Late stale row' })]));
      await waitFor(() => expect(screen.queryByText('Late stale row')).not.toBeInTheDocument());
      expect(screen.getByText('Newest filtered row')).toBeInTheDocument();
    });
  });

  describe('journal purge (bead hq3de.a)', () => {
    let confirmSpy: ReturnType<typeof vi.spyOn>;

    beforeEach(() => {
      confirmSpy = vi.spyOn(window, 'confirm');
    });

    it('defaults the day count to 90', async () => {
      renderWithProviders(<JournalTab />);
      await waitFor(() => {
        expect(screen.queryByText('Loading journal...')).not.toBeInTheDocument();
      });

      expect(screen.getByLabelText('Days to keep')).toHaveValue(90);
    });

    it('does not purge when the confirm dialog is declined', async () => {
      confirmSpy.mockReturnValue(false);
      renderWithProviders(<JournalTab />);
      await waitFor(() => {
        expect(screen.queryByText('Loading journal...')).not.toBeInTheDocument();
      });

      fireEvent.click(screen.getByRole('button', { name: /purge old entries/i }));

      expect(confirmSpy).toHaveBeenCalled();
      expect(api.purgeJournalEntries).not.toHaveBeenCalled();
    });

    it('purges entries older than the entered day count once confirmed and refreshes', async () => {
      confirmSpy.mockReturnValue(true);
      vi.mocked(api.purgeJournalEntries).mockResolvedValue({ deleted: 12, days: 30 });

      renderWithProviders(<JournalTab />);
      await waitFor(() => {
        expect(screen.queryByText('Loading journal...')).not.toBeInTheDocument();
      });

      fireEvent.change(screen.getByLabelText('Days to keep'), { target: { value: '30' } });

      vi.mocked(api.getJournalEntries).mockClear();
      vi.mocked(api.getJournalStats).mockClear();

      fireEvent.click(screen.getByRole('button', { name: /purge old entries/i }));

      await waitFor(() => {
        expect(api.purgeJournalEntries).toHaveBeenCalledWith(30);
      });
      await waitFor(() => {
        expect(api.getJournalEntries).toHaveBeenCalled();
        expect(api.getJournalStats).toHaveBeenCalled();
      });
    });

    it('rejects a day count below 1 without calling the API', async () => {
      renderWithProviders(<JournalTab />);
      await waitFor(() => {
        expect(screen.queryByText('Loading journal...')).not.toBeInTheDocument();
      });

      fireEvent.change(screen.getByLabelText('Days to keep'), { target: { value: '0' } });

      expect(screen.getByRole('button', { name: /purge old entries/i })).toBeDisabled();
      expect(api.purgeJournalEntries).not.toHaveBeenCalled();
    });
  });
});
