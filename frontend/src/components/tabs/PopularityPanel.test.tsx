/**
 * Unit tests for PopularityPanel component (v0.11.0)
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { PopularityPanel } from './PopularityPanel';
import * as api from '../../services/api';
import type { ChannelPopularityScore, PopularityRankingsResponse } from '../../types';

// Mock the API module
vi.mock('../../services/api');

// Mock the NotificationContext
const mockNotifications = {
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
};

vi.mock('../../contexts/NotificationContext', () => ({
  useNotifications: () => mockNotifications,
}));

describe('PopularityPanel', () => {
  // Mock data
  const mockRankings: ChannelPopularityScore[] = [
    {
      channel_id: 'ch-1',
      channel_name: 'ESPN',
      score: 85.5,
      rank: 1,
      trend: 'up',
      trend_percent: 12.5,
      watch_count_7d: 500,
      watch_time_7d: 36000,
      unique_viewers_7d: 150,
      bandwidth_7d: 1073741824,
      previous_rank: 3,
    },
    {
      channel_id: 'ch-2',
      channel_name: 'CNN',
      score: 72.3,
      rank: 2,
      trend: 'stable',
      trend_percent: 2.1,
      watch_count_7d: 350,
      watch_time_7d: 28800,
      unique_viewers_7d: 100,
      bandwidth_7d: 536870912,
      previous_rank: 2,
    },
    {
      channel_id: 'ch-3',
      channel_name: 'HBO',
      score: 65.0,
      rank: 3,
      trend: 'down',
      trend_percent: -15.2,
      watch_count_7d: 200,
      watch_time_7d: 21600,
      unique_viewers_7d: 75,
      bandwidth_7d: 268435456,
      previous_rank: 1,
    },
  ];

  const mockRankingsResponse: PopularityRankingsResponse = {
    rankings: mockRankings,
    total: 3,
    page: 1,
    page_size: 50,
  };

  const mockTrendingUp: ChannelPopularityScore[] = [
    { ...mockRankings[0] },
  ];

  const mockTrendingDown: ChannelPopularityScore[] = [
    { ...mockRankings[2] },
  ];

  beforeEach(() => {
    vi.clearAllMocks();

    // Setup default mocks
    vi.mocked(api.getPopularityRankings).mockResolvedValue(mockRankingsResponse);
    vi.mocked(api.getTrendingChannels).mockImplementation((direction: string) => {
      if (direction === 'up') return Promise.resolve(mockTrendingUp);
      return Promise.resolve(mockTrendingDown);
    });
    vi.mocked(api.calculatePopularity).mockResolvedValue({
      channels_scored: 10,
      channels_created: 5,
      channels_updated: 5,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe('initial rendering', () => {
    it('shows loading state initially', async () => {
      render(<PopularityPanel />);

      expect(screen.getByText('Loading popularity data...')).toBeInTheDocument();
    });

    it('fetches data on mount', async () => {
      render(<PopularityPanel />);

      await waitFor(() => {
        expect(api.getPopularityRankings).toHaveBeenCalledWith(50, 0);
        expect(api.getTrendingChannels).toHaveBeenCalledWith('up', 10);
        expect(api.getTrendingChannels).toHaveBeenCalledWith('down', 10);
      });
    });

    it('renders panel header after loading', async () => {
      render(<PopularityPanel />);

      await waitFor(() => {
        expect(screen.getByText('Popularity Rankings')).toBeInTheDocument();
      });
    });

    it('displays total channel count', async () => {
      render(<PopularityPanel />);

      await waitFor(() => {
        expect(screen.getByText('3 channels')).toBeInTheDocument();
      });
    });
  });

  describe('rankings view', () => {
    it('displays channel rankings', async () => {
      render(<PopularityPanel />);

      await waitFor(() => {
        expect(screen.getByText('ESPN')).toBeInTheDocument();
        expect(screen.getByText('CNN')).toBeInTheDocument();
        expect(screen.getByText('HBO')).toBeInTheDocument();
      });
    });

    it('displays rank numbers', async () => {
      render(<PopularityPanel />);

      await waitFor(() => {
        expect(screen.getByText('#1')).toBeInTheDocument();
        expect(screen.getByText('#2')).toBeInTheDocument();
        expect(screen.getByText('#3')).toBeInTheDocument();
      });
    });

    it('displays trend indicators', async () => {
      render(<PopularityPanel />);

      await waitFor(() => {
        // Trend percentages
        expect(screen.getByText(/12\.5%/)).toBeInTheDocument();
        expect(screen.getByText(/2\.1%/)).toBeInTheDocument();
        expect(screen.getByText(/15\.2%/)).toBeInTheDocument();
      });
    });

    it('displays score values', async () => {
      render(<PopularityPanel />);

      await waitFor(() => {
        expect(screen.getByText('85.5')).toBeInTheDocument();
        expect(screen.getByText('72.3')).toBeInTheDocument();
        expect(screen.getByText('65.0')).toBeInTheDocument();
      });
    });
  });

  describe('view toggle', () => {
    it('renders view toggle buttons', async () => {
      render(<PopularityPanel />);

      await waitFor(() => {
        expect(screen.getByRole('button', { name: 'Rankings' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Trending' })).toBeInTheDocument();
      });
    });

    it('switches to trending view when clicked', async () => {
      render(<PopularityPanel />);

      await waitFor(() => {
        expect(screen.getByText('ESPN')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByRole('button', { name: 'Trending' }));

      await waitFor(() => {
        expect(screen.getByText('Trending Up')).toBeInTheDocument();
        expect(screen.getByText('Trending Down')).toBeInTheDocument();
      });
    });
  });

  describe('trending view', () => {
    it('displays trending up channels', async () => {
      render(<PopularityPanel />);

      await waitFor(() => {
        expect(screen.getByText('ESPN')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByRole('button', { name: 'Trending' }));

      await waitFor(() => {
        // ESPN should appear in trending up section
        const trendingUpSection = screen.getByText('Trending Up').closest('.trending-column');
        expect(trendingUpSection).toContainElement(screen.getAllByText('ESPN')[0]);
      });
    });

    it('displays trending down channels', async () => {
      render(<PopularityPanel />);

      await waitFor(() => {
        expect(screen.getByText('HBO')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByRole('button', { name: 'Trending' }));

      await waitFor(() => {
        // HBO should appear in trending down section
        expect(screen.getByText('Trending Down')).toBeInTheDocument();
        const allHBO = screen.getAllByText('HBO');
        expect(allHBO.length).toBeGreaterThan(0);
      });
    });

    it('shows empty states when no trending channels', async () => {
      vi.mocked(api.getTrendingChannels).mockResolvedValue([]);

      render(<PopularityPanel />);

      await waitFor(() => {
        expect(screen.getByText('Popularity Rankings')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByRole('button', { name: 'Trending' }));

      await waitFor(() => {
        expect(screen.getByText('No channels trending up')).toBeInTheDocument();
        expect(screen.getByText('No channels trending down')).toBeInTheDocument();
      });
    });
  });

  describe('ranking item expansion', () => {
    it('expands ranking item when clicked', async () => {
      render(<PopularityPanel />);

      await waitFor(() => {
        expect(screen.getByText('ESPN')).toBeInTheDocument();
      });

      // Click on ESPN ranking item
      const espnItem = screen.getByText('ESPN').closest('.ranking-item');
      fireEvent.click(espnItem!);

      await waitFor(() => {
        expect(screen.getByText('Watch Count')).toBeInTheDocument();
        expect(screen.getByText('Watch Time')).toBeInTheDocument();
        expect(screen.getByText('Unique Viewers')).toBeInTheDocument();
        expect(screen.getByText('Bandwidth')).toBeInTheDocument();
      });
    });

    it('displays expanded details correctly', async () => {
      render(<PopularityPanel />);

      await waitFor(() => {
        expect(screen.getByText('ESPN')).toBeInTheDocument();
      });

      const espnItem = screen.getByText('ESPN').closest('.ranking-item');
      fireEvent.click(espnItem!);

      await waitFor(() => {
        expect(screen.getByText('500')).toBeInTheDocument(); // watch count
        expect(screen.getByText('10h 0m')).toBeInTheDocument(); // watch time (36000s)
        expect(screen.getByText('150')).toBeInTheDocument(); // unique viewers
        expect(screen.getByText('1.0 GB')).toBeInTheDocument(); // bandwidth
      });
    });

    it('shows previous rank information', async () => {
      render(<PopularityPanel />);

      await waitFor(() => {
        expect(screen.getByText('ESPN')).toBeInTheDocument();
      });

      const espnItem = screen.getByText('ESPN').closest('.ranking-item');
      fireEvent.click(espnItem!);

      await waitFor(() => {
        expect(screen.getByText('Previous rank: #3')).toBeInTheDocument();
        expect(screen.getByText(/↑ 2/)).toBeInTheDocument(); // Improved by 2
      });
    });

    it('collapses when clicked again', async () => {
      render(<PopularityPanel />);

      await waitFor(() => {
        expect(screen.getByText('ESPN')).toBeInTheDocument();
      });

      const espnItem = screen.getByText('ESPN').closest('.ranking-item');
      fireEvent.click(espnItem!);

      await waitFor(() => {
        expect(screen.getByText('Watch Count')).toBeInTheDocument();
      });

      fireEvent.click(espnItem!);

      await waitFor(() => {
        expect(screen.queryByText('Watch Count')).not.toBeInTheDocument();
      });
    });
  });

  describe('recalculate functionality', () => {
    it('renders recalculate button', async () => {
      render(<PopularityPanel />);

      await waitFor(() => {
        expect(screen.getByRole('button', { name: 'Recalculate' })).toBeInTheDocument();
      });
    });

    it('calls calculatePopularity when clicked', async () => {
      render(<PopularityPanel />);

      await waitFor(() => {
        expect(screen.getByText('Recalculate')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByRole('button', { name: 'Recalculate' }));

      await waitFor(() => {
        expect(api.calculatePopularity).toHaveBeenCalledWith(7);
      });
    });

    it('shows calculating state while processing', async () => {
      // Make calculatePopularity take time
      vi.mocked(api.calculatePopularity).mockImplementation(() =>
        new Promise(resolve => setTimeout(() => resolve({
          channels_scored: 10,
          channels_created: 5,
          channels_updated: 5,
        }), 100))
      );

      render(<PopularityPanel />);

      await waitFor(() => {
        expect(screen.getByText('Recalculate')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByRole('button', { name: 'Recalculate' }));

      expect(screen.getByText('Calculating...')).toBeInTheDocument();
    });

    it('shows success notification after calculation', async () => {
      render(<PopularityPanel />);

      await waitFor(() => {
        expect(screen.getByText('Recalculate')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByRole('button', { name: 'Recalculate' }));

      await waitFor(() => {
        expect(mockNotifications.success).toHaveBeenCalledWith(
          'Calculated 10 channels (5 new, 5 updated)',
          'Popularity Calculated'
        );
      });
    });

    it('shows error notification on calculation failure', async () => {
      vi.mocked(api.calculatePopularity).mockRejectedValue(new Error('Calculation failed'));

      render(<PopularityPanel />);

      await waitFor(() => {
        expect(screen.getByText('Recalculate')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByRole('button', { name: 'Recalculate' }));

      await waitFor(() => {
        expect(mockNotifications.error).toHaveBeenCalledWith(
          'Calculation failed',
          'Calculation Failed'
        );
      });
    });
  });

  describe('error handling', () => {
    it('shows toast notification when API fails', async () => {
      vi.mocked(api.getPopularityRankings).mockRejectedValue(new Error('Network error'));

      render(<PopularityPanel />);

      await waitFor(() => {
        expect(mockNotifications.error).toHaveBeenCalledWith('Network error', 'Popularity');
      });
    });
  });

  describe('empty states', () => {
    it('shows empty state when no rankings exist', async () => {
      vi.mocked(api.getPopularityRankings).mockResolvedValue({
        rankings: [],
        total: 0,
        page: 1,
        page_size: 50,
      });

      render(<PopularityPanel />);

      await waitFor(() => {
        expect(screen.getByText(/No popularity data available/)).toBeInTheDocument();
        expect(screen.getByText(/Click "Recalculate"/)).toBeInTheDocument();
      });
    });
  });

  describe('refresh trigger', () => {
    it('refetches data when refreshTrigger changes', async () => {
      const { rerender } = render(<PopularityPanel refreshTrigger={1} />);

      await waitFor(() => {
        expect(api.getPopularityRankings).toHaveBeenCalledTimes(1);
      });

      vi.mocked(api.getPopularityRankings).mockClear();

      rerender(<PopularityPanel refreshTrigger={2} />);

      await waitFor(() => {
        expect(api.getPopularityRankings).toHaveBeenCalled();
      });
    });
  });
});
