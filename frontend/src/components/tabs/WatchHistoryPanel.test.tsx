/**
 * Unit tests for WatchHistoryPanel component (v0.11.0)
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { WatchHistoryPanel } from './WatchHistoryPanel';
import * as api from '../../services/api';
import type { WatchHistoryResponse, WatchHistoryEntry } from '../../types';

// Mock the API module
vi.mock('../../services/api');

describe('WatchHistoryPanel', () => {
  // Mock data
  const mockHistoryEntries: WatchHistoryEntry[] = [
    {
      id: 1,
      channel_id: 'ch-1',
      channel_name: 'ESPN',
      ip_address: '192.168.1.100',
      connected_at: new Date(Date.now() - 3600000).toISOString(), // 1 hour ago
      disconnected_at: new Date(Date.now() - 1800000).toISOString(), // 30 min ago
      watch_seconds: 1800,
      date: '2026-02-05',
    },
    {
      id: 2,
      channel_id: 'ch-2',
      channel_name: 'CNN',
      ip_address: '192.168.1.101',
      connected_at: new Date(Date.now() - 7200000).toISOString(), // 2 hours ago
      disconnected_at: new Date(Date.now() - 5400000).toISOString(), // 1.5 hours ago
      watch_seconds: 1800,
      date: '2026-02-05',
    },
    {
      id: 3,
      channel_id: 'ch-3',
      channel_name: 'HBO',
      ip_address: '192.168.1.102',
      connected_at: new Date(Date.now() - 600000).toISOString(), // 10 min ago
      disconnected_at: null, // Still watching
      watch_seconds: 600,
      date: '2026-02-05',
    },
  ];

  const mockHistoryResponse: WatchHistoryResponse = {
    history: mockHistoryEntries,
    total: 3,
    page: 1,
    page_size: 25,
    total_pages: 1,
    summary: {
      unique_channels: 3,
      unique_ips: 3,
      total_watch_seconds: 4200,
    },
  };

  beforeEach(() => {
    vi.clearAllMocks();

    // Setup default mock
    vi.mocked(api.getWatchHistory).mockResolvedValue(mockHistoryResponse);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe('initial rendering', () => {
    it('shows loading state initially', async () => {
      render(<WatchHistoryPanel />);

      expect(screen.getByText('Loading watch history...')).toBeInTheDocument();
    });

    it('fetches data on mount with default params', async () => {
      render(<WatchHistoryPanel />);

      await waitFor(() => {
        expect(api.getWatchHistory).toHaveBeenCalledWith({
          page: 1,
          pageSize: 25,
          channelId: undefined,
          ipAddress: undefined,
          days: 7,
        });
      });
    });

    it('renders panel header after loading', async () => {
      render(<WatchHistoryPanel />);

      await waitFor(() => {
        expect(screen.getByText('Watch History')).toBeInTheDocument();
      });
    });

    it('displays total sessions count', async () => {
      render(<WatchHistoryPanel />);

      await waitFor(() => {
        expect(screen.getByText('3 sessions')).toBeInTheDocument();
      });
    });
  });

  describe('summary stats', () => {
    it('displays summary statistics', async () => {
      render(<WatchHistoryPanel />);

      await waitFor(() => {
        // Check that summary section renders with stat values
        // Note: formatDuration(4200) = "1h 10m" (hours > 0, so no seconds shown)
        expect(screen.getByText('1h 10m')).toBeInTheDocument(); // total time (4200s)
        // Verify stat labels are present, which confirms the section renders
        expect(screen.getByText('Channels')).toBeInTheDocument();
      });
    });

    it('displays stat labels', async () => {
      render(<WatchHistoryPanel />);

      await waitFor(() => {
        expect(screen.getByText('Channels')).toBeInTheDocument();
        expect(screen.getByText('Viewers')).toBeInTheDocument();
        expect(screen.getByText('Total Time')).toBeInTheDocument();
        expect(screen.getByText('Sessions')).toBeInTheDocument();
      });
    });
  });

  describe('history table', () => {
    it('displays history entries', async () => {
      render(<WatchHistoryPanel />);

      await waitFor(() => {
        expect(screen.getByText('ESPN')).toBeInTheDocument();
        expect(screen.getByText('CNN')).toBeInTheDocument();
        expect(screen.getByText('HBO')).toBeInTheDocument();
      });
    });

    it('displays IP addresses', async () => {
      render(<WatchHistoryPanel />);

      await waitFor(() => {
        expect(screen.getByText('192.168.1.100')).toBeInTheDocument();
        expect(screen.getByText('192.168.1.101')).toBeInTheDocument();
        expect(screen.getByText('192.168.1.102')).toBeInTheDocument();
      });
    });

    it('displays watch durations', async () => {
      render(<WatchHistoryPanel />);

      await waitFor(() => {
        // 1800 seconds = 30m 0s
        expect(screen.getAllByText('30m 0s').length).toBeGreaterThan(0);
        // 600 seconds = 10m 0s
        expect(screen.getByText('10m 0s')).toBeInTheDocument();
      });
    });

    it('displays status badges', async () => {
      render(<WatchHistoryPanel />);

      await waitFor(() => {
        expect(screen.getAllByText('Completed').length).toBe(2);
        expect(screen.getByText('Watching')).toBeInTheDocument();
      });
    });

    it('displays table headers', async () => {
      render(<WatchHistoryPanel />);

      await waitFor(() => {
        expect(screen.getByText('Time')).toBeInTheDocument();
        expect(screen.getByText('Channel')).toBeInTheDocument();
        expect(screen.getByText('Viewer IP')).toBeInTheDocument();
        expect(screen.getByText('Duration')).toBeInTheDocument();
        expect(screen.getByText('Status')).toBeInTheDocument();
      });
    });
  });

  describe('row expansion', () => {
    it('expands row when clicked', async () => {
      render(<WatchHistoryPanel />);

      await waitFor(() => {
        expect(screen.getByText('ESPN')).toBeInTheDocument();
      });

      // Find and click a row
      const espnRow = screen.getByText('ESPN').closest('.history-row');
      fireEvent.click(espnRow!);

      await waitFor(() => {
        expect(screen.getByText('Connected')).toBeInTheDocument();
        expect(screen.getByText('Disconnected')).toBeInTheDocument();
        expect(screen.getByText('Channel ID')).toBeInTheDocument();
        expect(screen.getByText('Date')).toBeInTheDocument();
      });
    });

    it('shows filter buttons in expanded row', async () => {
      render(<WatchHistoryPanel />);

      await waitFor(() => {
        expect(screen.getByText('ESPN')).toBeInTheDocument();
      });

      const espnRow = screen.getByText('ESPN').closest('.history-row');
      fireEvent.click(espnRow!);

      await waitFor(() => {
        expect(screen.getByRole('button', { name: 'Filter by Channel' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Filter by IP' })).toBeInTheDocument();
      });
    });

    it('collapses row when clicked again', async () => {
      render(<WatchHistoryPanel />);

      await waitFor(() => {
        expect(screen.getByText('ESPN')).toBeInTheDocument();
      });

      const espnRow = screen.getByText('ESPN').closest('.history-row');
      fireEvent.click(espnRow!);

      await waitFor(() => {
        expect(screen.getByText('Connected')).toBeInTheDocument();
      });

      fireEvent.click(espnRow!);

      await waitFor(() => {
        expect(screen.queryByText('Connected')).not.toBeInTheDocument();
      });
    });
  });

  describe('filters', () => {
    it('renders filter controls', async () => {
      render(<WatchHistoryPanel />);

      await waitFor(() => {
        expect(screen.getByText('Time Period:')).toBeInTheDocument();
        expect(screen.getByText('Channel:')).toBeInTheDocument();
        expect(screen.getByText('IP:')).toBeInTheDocument();
      });
    });

    it('renders time period select with options', async () => {
      render(<WatchHistoryPanel />);

      await waitFor(() => {
        expect(screen.getByRole('combobox')).toBeInTheDocument();
        expect(screen.getByRole('option', { name: 'Last 24 hours' })).toBeInTheDocument();
        expect(screen.getByRole('option', { name: 'Last 7 days' })).toBeInTheDocument();
        expect(screen.getByRole('option', { name: 'Last 30 days' })).toBeInTheDocument();
        expect(screen.getByRole('option', { name: 'Last 90 days' })).toBeInTheDocument();
        expect(screen.getByRole('option', { name: 'All time' })).toBeInTheDocument();
      });
    });

    it('changes time period filter', async () => {
      render(<WatchHistoryPanel />);

      await waitFor(() => {
        expect(screen.getByRole('combobox')).toBeInTheDocument();
      });

      vi.mocked(api.getWatchHistory).mockClear();

      const select = screen.getByRole('combobox');
      fireEvent.change(select, { target: { value: '30' } });

      await waitFor(() => {
        expect(api.getWatchHistory).toHaveBeenCalledWith(
          expect.objectContaining({ days: 30 })
        );
      });
    });

    it('filters by channel when input changes', async () => {
      render(<WatchHistoryPanel />);

      await waitFor(() => {
        expect(screen.getByPlaceholderText('Filter by channel ID')).toBeInTheDocument();
      });

      vi.mocked(api.getWatchHistory).mockClear();

      const channelInput = screen.getByPlaceholderText('Filter by channel ID');
      fireEvent.change(channelInput, { target: { value: 'ch-1' } });

      await waitFor(() => {
        expect(api.getWatchHistory).toHaveBeenCalledWith(
          expect.objectContaining({ channelId: 'ch-1' })
        );
      });
    });

    it('filters by IP when input changes', async () => {
      render(<WatchHistoryPanel />);

      await waitFor(() => {
        expect(screen.getByPlaceholderText('Filter by IP')).toBeInTheDocument();
      });

      vi.mocked(api.getWatchHistory).mockClear();

      const ipInput = screen.getByPlaceholderText('Filter by IP');
      fireEvent.change(ipInput, { target: { value: '192.168.1.100' } });

      await waitFor(() => {
        expect(api.getWatchHistory).toHaveBeenCalledWith(
          expect.objectContaining({ ipAddress: '192.168.1.100' })
        );
      });
    });

    it('shows clear filters button when filters are active', async () => {
      render(<WatchHistoryPanel />);

      await waitFor(() => {
        expect(screen.getByPlaceholderText('Filter by channel ID')).toBeInTheDocument();
      });

      const channelInput = screen.getByPlaceholderText('Filter by channel ID');
      fireEvent.change(channelInput, { target: { value: 'ch-1' } });

      await waitFor(() => {
        expect(screen.getByRole('button', { name: 'Clear Filters' })).toBeInTheDocument();
      });
    });

    it('clears all filters when clear button clicked', async () => {
      render(<WatchHistoryPanel />);

      await waitFor(() => {
        expect(screen.getByPlaceholderText('Filter by channel ID')).toBeInTheDocument();
      });

      const channelInput = screen.getByPlaceholderText('Filter by channel ID');
      fireEvent.change(channelInput, { target: { value: 'ch-1' } });

      await waitFor(() => {
        expect(screen.getByRole('button', { name: 'Clear Filters' })).toBeInTheDocument();
      });

      vi.mocked(api.getWatchHistory).mockClear();

      fireEvent.click(screen.getByRole('button', { name: 'Clear Filters' }));

      await waitFor(() => {
        expect(api.getWatchHistory).toHaveBeenCalledWith(
          expect.objectContaining({
            channelId: undefined,
            ipAddress: undefined,
            days: 7,
          })
        );
      });
    });

    it('applies filter from expanded row button', async () => {
      render(<WatchHistoryPanel />);

      await waitFor(() => {
        expect(screen.getByText('ESPN')).toBeInTheDocument();
      });

      const espnRow = screen.getByText('ESPN').closest('.history-row');
      fireEvent.click(espnRow!);

      await waitFor(() => {
        expect(screen.getByRole('button', { name: 'Filter by Channel' })).toBeInTheDocument();
      });

      vi.mocked(api.getWatchHistory).mockClear();

      fireEvent.click(screen.getByRole('button', { name: 'Filter by Channel' }));

      await waitFor(() => {
        expect(api.getWatchHistory).toHaveBeenCalledWith(
          expect.objectContaining({ channelId: 'ch-1' })
        );
      });
    });
  });

  describe('pagination', () => {
    it('displays pagination when multiple pages exist', async () => {
      vi.mocked(api.getWatchHistory).mockResolvedValue({
        ...mockHistoryResponse,
        total: 100,
        total_pages: 4,
      });

      render(<WatchHistoryPanel />);

      await waitFor(() => {
        expect(screen.getByText('Page 1 of 4')).toBeInTheDocument();
      });
    });

    it('hides pagination when single page', async () => {
      render(<WatchHistoryPanel />);

      await waitFor(() => {
        expect(screen.getByText('Watch History')).toBeInTheDocument();
      });

      expect(screen.queryByText(/Page \d+ of/)).not.toBeInTheDocument();
    });

    it('navigates to next page', async () => {
      vi.mocked(api.getWatchHistory).mockResolvedValue({
        ...mockHistoryResponse,
        total: 100,
        total_pages: 4,
      });

      render(<WatchHistoryPanel />);

      await waitFor(() => {
        expect(screen.getByText('Page 1 of 4')).toBeInTheDocument();
      });

      vi.mocked(api.getWatchHistory).mockClear();

      // Get pagination buttons - first is prev, second is next
      const pageButtons = document.querySelectorAll('.page-btn');
      const nextButton = pageButtons[1]; // Second button is "next"
      fireEvent.click(nextButton!);

      await waitFor(() => {
        expect(api.getWatchHistory).toHaveBeenCalledWith(
          expect.objectContaining({ page: 2 })
        );
      });
    });

    it('disables previous button on first page', async () => {
      vi.mocked(api.getWatchHistory).mockResolvedValue({
        ...mockHistoryResponse,
        total: 100,
        total_pages: 4,
      });

      render(<WatchHistoryPanel />);

      await waitFor(() => {
        expect(screen.getByText('Page 1 of 4')).toBeInTheDocument();
      });

      const prevButton = document.querySelector('.page-btn:first-child');
      expect(prevButton).toBeDisabled();
    });
  });

  describe('refresh functionality', () => {
    it('renders refresh button', async () => {
      render(<WatchHistoryPanel />);

      await waitFor(() => {
        expect(screen.getByText('Watch History')).toBeInTheDocument();
      });

      // Find refresh button by its class or icon
      const refreshBtn = document.querySelector('.refresh-btn');
      expect(refreshBtn).toBeInTheDocument();
    });

    it('refetches data when refresh clicked', async () => {
      render(<WatchHistoryPanel />);

      await waitFor(() => {
        expect(screen.getByText('Watch History')).toBeInTheDocument();
      });

      vi.mocked(api.getWatchHistory).mockClear();

      const refreshBtn = document.querySelector('.refresh-btn');
      fireEvent.click(refreshBtn!);

      await waitFor(() => {
        expect(api.getWatchHistory).toHaveBeenCalled();
      });
    });
  });

  describe('error handling', () => {
    it('displays error message when API fails', async () => {
      vi.mocked(api.getWatchHistory).mockRejectedValue(new Error('Network error'));

      render(<WatchHistoryPanel />);

      await waitFor(() => {
        expect(screen.getByText('Network error')).toBeInTheDocument();
      });
    });
  });

  describe('empty states', () => {
    it('shows empty state when no history exists', async () => {
      vi.mocked(api.getWatchHistory).mockResolvedValue({
        history: [],
        total: 0,
        page: 1,
        page_size: 25,
        total_pages: 0,
        summary: {
          unique_channels: 0,
          unique_ips: 0,
          total_watch_seconds: 0,
        },
      });

      render(<WatchHistoryPanel />);

      await waitFor(() => {
        expect(screen.getByText('No watch history found for the selected filters.')).toBeInTheDocument();
      });
    });
  });

  describe('refresh trigger', () => {
    it('refetches data when refreshTrigger changes', async () => {
      const { rerender } = render(<WatchHistoryPanel refreshTrigger={1} />);

      await waitFor(() => {
        expect(api.getWatchHistory).toHaveBeenCalledTimes(1);
      });

      vi.mocked(api.getWatchHistory).mockClear();

      rerender(<WatchHistoryPanel refreshTrigger={2} />);

      await waitFor(() => {
        expect(api.getWatchHistory).toHaveBeenCalled();
      });
    });
  });
});
