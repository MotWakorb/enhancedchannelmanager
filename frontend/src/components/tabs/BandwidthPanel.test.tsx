/**
 * Unit tests for BandwidthPanel component (v0.11.0)
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { BandwidthPanel } from './BandwidthPanel';
import * as api from '../../services/api';
import type { BandwidthSummary } from '../../types';

// Mock the API module
vi.mock('../../services/api');

describe('BandwidthPanel', () => {
  // Mock data
  const mockBandwidthStats: BandwidthSummary = {
    today: 1073741824, // 1 GB
    this_week: 5368709120, // 5 GB
    this_month: 21474836480, // 20 GB
    this_year: 107374182400, // 100 GB
    all_time: 214748364800, // 200 GB
    today_in: 536870912, // 512 MB inbound
    today_out: 1073741824, // 1 GB outbound
    week_in: 2684354560, // 2.5 GB inbound
    week_out: 5368709120, // 5 GB outbound
    all_time_in: 107374182400, // 100 GB inbound
    all_time_out: 214748364800, // 200 GB outbound
    today_peak_bitrate_in: 50000000, // 50 Mbps
    today_peak_bitrate_out: 100000000, // 100 Mbps
    daily_history: [
      { date: '2026-01-30', bytes_transferred: 1000000000, bytes_in: 400000000, bytes_out: 600000000 },
      { date: '2026-01-31', bytes_transferred: 1200000000, bytes_in: 500000000, bytes_out: 700000000 },
      { date: '2026-02-01', bytes_transferred: 800000000, bytes_in: 300000000, bytes_out: 500000000 },
      { date: '2026-02-02', bytes_transferred: 1500000000, bytes_in: 600000000, bytes_out: 900000000 },
      { date: '2026-02-03', bytes_transferred: 1100000000, bytes_in: 450000000, bytes_out: 650000000 },
      { date: '2026-02-04', bytes_transferred: 900000000, bytes_in: 350000000, bytes_out: 550000000 },
      { date: '2026-02-05', bytes_transferred: 1073741824, bytes_in: 536870912, bytes_out: 536870912 },
    ],
  };

  beforeEach(() => {
    vi.clearAllMocks();

    // Setup default mock
    vi.mocked(api.getBandwidthStats).mockResolvedValue(mockBandwidthStats);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe('initial rendering', () => {
    it('shows loading state initially', async () => {
      render(<BandwidthPanel />);

      expect(screen.getByText('Loading bandwidth data...')).toBeInTheDocument();
    });

    it('fetches data on mount', async () => {
      render(<BandwidthPanel />);

      await waitFor(() => {
        expect(api.getBandwidthStats).toHaveBeenCalled();
      });
    });

    it('renders panel header after loading', async () => {
      render(<BandwidthPanel />);

      await waitFor(() => {
        expect(screen.getByText('Bandwidth In/Out')).toBeInTheDocument();
      });
    });
  });

  describe('inbound/outbound summary', () => {
    it('displays inbound summary card', async () => {
      render(<BandwidthPanel />);

      await waitFor(() => {
        expect(screen.getByText('Inbound (from providers)')).toBeInTheDocument();
      });
    });

    it('displays outbound summary card', async () => {
      render(<BandwidthPanel />);

      await waitFor(() => {
        expect(screen.getByText('Outbound (to viewers)')).toBeInTheDocument();
      });
    });

    it('displays inbound statistics', async () => {
      render(<BandwidthPanel />);

      // Wait for loading to complete
      await waitFor(() => {
        expect(screen.getByText('Bandwidth In/Out')).toBeInTheDocument();
      });

      // Check inbound stats are displayed
      // Note: Some values may appear multiple times (e.g., in summary and chart)
      // Today inbound: 512 MB, Week inbound: 2.5 GB, All time inbound: 100 GB
      expect(screen.getAllByText('512.0 MB').length).toBeGreaterThan(0);
      expect(screen.getAllByText('2.5 GB').length).toBeGreaterThan(0);
      expect(screen.getAllByText('100.0 GB').length).toBeGreaterThan(0);
    });

    it('displays outbound statistics', async () => {
      render(<BandwidthPanel />);

      // Wait for loading to complete
      await waitFor(() => {
        expect(screen.getByText('Bandwidth In/Out')).toBeInTheDocument();
      });

      // Check outbound stats are displayed
      // Note: Some values may appear multiple times (e.g., in summary and chart)
      // Today outbound: 1 GB, Week outbound: 5 GB, All time outbound: 200 GB
      expect(screen.getAllByText('1.0 GB').length).toBeGreaterThan(0);
      expect(screen.getAllByText('5.0 GB').length).toBeGreaterThan(0);
      expect(screen.getAllByText('200.0 GB').length).toBeGreaterThan(0);
    });

    it('displays stat labels in both cards', async () => {
      render(<BandwidthPanel />);

      // Wait for loading to complete
      await waitFor(() => {
        expect(screen.getByText('Bandwidth In/Out')).toBeInTheDocument();
      });

      // Labels appear in both inbound and outbound cards
      expect(screen.getAllByText('Today').length).toBe(2);
      expect(screen.getAllByText('This Week').length).toBe(2);
      expect(screen.getAllByText('All Time').length).toBe(2);
    });
  });

  describe('peak bitrates section', () => {
    it('displays peak bitrates header', async () => {
      render(<BandwidthPanel />);

      await waitFor(() => {
        expect(screen.getByText('Peak Bitrates (Today)')).toBeInTheDocument();
      });
    });

    it('displays peak inbound bitrate', async () => {
      render(<BandwidthPanel />);

      await waitFor(() => {
        expect(screen.getByText('Peak Inbound')).toBeInTheDocument();
        expect(screen.getByText('50.0 Mbps')).toBeInTheDocument();
      });
    });

    it('displays peak outbound bitrate', async () => {
      render(<BandwidthPanel />);

      await waitFor(() => {
        expect(screen.getByText('Peak Outbound')).toBeInTheDocument();
        expect(screen.getByText('100.0 Mbps')).toBeInTheDocument();
      });
    });
  });

  describe('ratio section', () => {
    it('displays ratio header', async () => {
      render(<BandwidthPanel />);

      await waitFor(() => {
        expect(screen.getByText('Bandwidth Ratio (This Week)')).toBeInTheDocument();
      });
    });

    it('displays ratio labels with values', async () => {
      render(<BandwidthPanel />);

      await waitFor(() => {
        // Inbound is 2.5 GB, Outbound is 5 GB
        // So inbound is 33% and outbound is 67%
        expect(screen.getByText(/Inbound: 2\.5 GB/)).toBeInTheDocument();
        expect(screen.getByText(/Outbound: 5\.0 GB/)).toBeInTheDocument();
      });
    });
  });

  describe('daily chart', () => {
    it('displays chart section', async () => {
      render(<BandwidthPanel />);

      await waitFor(() => {
        expect(screen.getByText('Last 7 Days')).toBeInTheDocument();
      });
    });

    it('displays chart legend', async () => {
      render(<BandwidthPanel />);

      await waitFor(() => {
        expect(screen.getByText('Inbound')).toBeInTheDocument();
        expect(screen.getByText('Outbound')).toBeInTheDocument();
      });
    });

    it('renders day labels', async () => {
      render(<BandwidthPanel />);

      await waitFor(() => {
        // Day labels will vary based on date, but should have some weekday abbreviations
        const dayLabels = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
        const foundLabels = dayLabels.filter(day => screen.queryByText(day) !== null);
        expect(foundLabels.length).toBeGreaterThan(0);
      });
    });
  });

  describe('refresh functionality', () => {
    it('renders refresh button', async () => {
      render(<BandwidthPanel />);

      await waitFor(() => {
        expect(screen.getByText('Bandwidth In/Out')).toBeInTheDocument();
      });

      const refreshBtn = document.querySelector('.refresh-btn');
      expect(refreshBtn).toBeInTheDocument();
    });

    it('refetches data when refresh clicked', async () => {
      render(<BandwidthPanel />);

      await waitFor(() => {
        expect(screen.getByText('Bandwidth In/Out')).toBeInTheDocument();
      });

      vi.mocked(api.getBandwidthStats).mockClear();

      const refreshBtn = document.querySelector('.refresh-btn');
      fireEvent.click(refreshBtn!);

      await waitFor(() => {
        expect(api.getBandwidthStats).toHaveBeenCalled();
      });
    });
  });

  describe('error handling', () => {
    it('displays error message when API fails', async () => {
      vi.mocked(api.getBandwidthStats).mockRejectedValue(new Error('Network error'));

      render(<BandwidthPanel />);

      await waitFor(() => {
        expect(screen.getByText('Network error')).toBeInTheDocument();
      });
    });

    it('displays generic error for non-Error exceptions', async () => {
      vi.mocked(api.getBandwidthStats).mockRejectedValue('Something went wrong');

      render(<BandwidthPanel />);

      await waitFor(() => {
        expect(screen.getByText('Failed to load bandwidth data')).toBeInTheDocument();
      });
    });
  });

  describe('empty states', () => {
    it('shows empty state when no data available', async () => {
      vi.mocked(api.getBandwidthStats).mockResolvedValue(null as unknown as BandwidthSummary);

      render(<BandwidthPanel />);

      await waitFor(() => {
        expect(screen.getByText('No bandwidth data available yet.')).toBeInTheDocument();
      });
    });

    it('handles empty daily history gracefully', async () => {
      vi.mocked(api.getBandwidthStats).mockResolvedValue({
        ...mockBandwidthStats,
        daily_history: [],
      });

      render(<BandwidthPanel />);

      await waitFor(() => {
        expect(screen.getByText('Bandwidth In/Out')).toBeInTheDocument();
        // Chart section should not appear
        expect(screen.queryByText('Last 7 Days')).not.toBeInTheDocument();
      });
    });
  });

  describe('refresh trigger', () => {
    it('refetches data when refreshTrigger changes', async () => {
      const { rerender } = render(<BandwidthPanel refreshTrigger={1} />);

      await waitFor(() => {
        expect(api.getBandwidthStats).toHaveBeenCalledTimes(1);
      });

      vi.mocked(api.getBandwidthStats).mockClear();

      rerender(<BandwidthPanel refreshTrigger={2} />);

      await waitFor(() => {
        expect(api.getBandwidthStats).toHaveBeenCalled();
      });
    });
  });

  describe('helper functions', () => {
    // Helper functions are tested through component rendering
    // The component tests verify that:
    // - formatBytes() converts bytes to human readable (1GB = "1.0 GB")
    // - formatBitrate() converts bps to human readable (50000000 = "50.0 Mbps")
    // - formatDateLabel() converts date to weekday short form

    it('formatBytes handles zero correctly', async () => {
      vi.mocked(api.getBandwidthStats).mockResolvedValue({
        ...mockBandwidthStats,
        today_in: 0,
      });

      render(<BandwidthPanel />);

      await waitFor(() => {
        expect(screen.getByText('0 B')).toBeInTheDocument();
      });
    });

    it('formatBitrate handles zero correctly', async () => {
      vi.mocked(api.getBandwidthStats).mockResolvedValue({
        ...mockBandwidthStats,
        today_peak_bitrate_in: 0,
      });

      render(<BandwidthPanel />);

      await waitFor(() => {
        expect(screen.getByText('0 bps')).toBeInTheDocument();
      });
    });
  });
});
