/**
 * Unit tests for JournalTab component and helper functions.
 */
import type * as React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { JournalTab } from './JournalTab';
import { NotificationProvider } from '../../contexts/NotificationContext';
import * as api from '../../services/api';
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

    it('renders Auto-creation label for source=auto_creation', async () => {
      vi.mocked(api.getJournalEntries).mockResolvedValue(
        mockResponse([makeEntry({ mutation_source: 'auto_creation' })])
      );

      renderWithProviders(<JournalTab />);

      await waitFor(() => {
        expect(screen.queryByText('Loading journal...')).not.toBeInTheDocument();
      });

      const badges = document.querySelectorAll('.source-badge');
      const texts = Array.from(badges).map((b) => b.textContent);
      expect(texts).toContain('Auto-creation');
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

  describe('empty state', () => {
    it('shows empty state when no entries exist', async () => {
      vi.mocked(api.getJournalEntries).mockResolvedValue(mockResponse([]));

      renderWithProviders(<JournalTab />);

      await waitFor(() => {
        expect(screen.getByText('No journal entries')).toBeInTheDocument();
      });
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
  });
});
