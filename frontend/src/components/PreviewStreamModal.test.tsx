/**
 * Unit tests for PreviewStreamModal component.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { PreviewStreamModal } from './PreviewStreamModal';
import type { Stream, Channel } from '../types';

// Mock the VideoPlayer component - the factory must not reference external variables
vi.mock('./VideoPlayer', () => ({
  VideoPlayer: ({ onStateChange, onError, src }: { onStateChange: (s: string) => void; onError: (e: { message: string }) => void; src: string }) => {
    // Store callbacks and src for later use in tests
    (window as unknown as { __videoPlayerCallbacks: { onStateChange: typeof onStateChange; onError: typeof onError } }).__videoPlayerCallbacks = { onStateChange, onError };
    (window as unknown as { __videoPlayerSrc: string }).__videoPlayerSrc = src;
    return <div data-testid="mock-video-player">Mock Video Player</div>;
  },
}));

// Mock the API module
vi.mock('../services/api', async () => {
  return {
    getSettings: vi.fn(() => Promise.resolve({ stream_preview_mode: 'transcode' })),
  };
});

describe('PreviewStreamModal', () => {
  const mockStream: Stream = {
    id: 1,
    name: 'Test Stream',
    url: 'http://example.com/stream.ts',
    tvg_id: 'test.stream',
    channel_group_name: 'Sports',
    m3u_account: 1,
    logo_url: null,
    channel_group_id: 1,
    stream_hash: 'abc123',
    is_custom: false,
    epg_id: null,
    probe_status: null,
    probe_error: null,
    probe_bitrate: null,
    probe_resolution: null,
    probe_codec: null,
    last_probed_at: null,
  };

  const mockChannel: Channel = {
    id: 1,
    name: 'Test Channel',
    channel_number: 101,
    uuid: 'test-uuid',
    streams: [1, 2],
    tvg_id: 'test.channel',
    logo_id: null,
    channel_group_id: 1,
    epg_data_id: null,
    stream_profile_id: null,
    tvc_guide_stationid: null,
    user_level: 0,
    auto_created: false,
  };

  const defaultProps = {
    isOpen: true,
    onClose: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('rendering - closed', () => {
    it('returns null when isOpen is false', () => {
      const { container } = render(
        <PreviewStreamModal {...defaultProps} isOpen={false} stream={mockStream} />
      );
      expect(container.firstChild).toBeNull();
    });

    it('returns null when no stream or channel provided', () => {
      const { container } = render(
        <PreviewStreamModal {...defaultProps} />
      );
      expect(container.firstChild).toBeNull();
    });
  });

  describe('rendering - stream preview', () => {
    it('renders modal when isOpen is true with stream', () => {
      render(<PreviewStreamModal {...defaultProps} stream={mockStream} />);
      expect(screen.getByText('Stream Preview')).toBeInTheDocument();
    });

    it('renders stream name', () => {
      render(<PreviewStreamModal {...defaultProps} stream={mockStream} />);
      expect(screen.getByText('Test Stream')).toBeInTheDocument();
    });

    it('renders channel name when provided', () => {
      render(
        <PreviewStreamModal
          {...defaultProps}
          stream={mockStream}
          channelName="My Channel"
        />
      );
      expect(screen.getByText('My Channel')).toBeInTheDocument();
    });

    it('renders TVG-ID metadata', () => {
      render(<PreviewStreamModal {...defaultProps} stream={mockStream} />);
      expect(screen.getByText('TVG-ID: test.stream')).toBeInTheDocument();
    });

    it('renders channel group metadata', () => {
      render(<PreviewStreamModal {...defaultProps} stream={mockStream} />);
      expect(screen.getByText('Sports')).toBeInTheDocument();
    });

    it('renders provider name when provided', () => {
      render(
        <PreviewStreamModal
          {...defaultProps}
          stream={mockStream}
          providerName="My IPTV Provider"
        />
      );
      expect(screen.getByText('My IPTV Provider')).toBeInTheDocument();
    });

    it('renders video player', () => {
      render(<PreviewStreamModal {...defaultProps} stream={mockStream} />);
      expect(screen.getByTestId('mock-video-player')).toBeInTheDocument();
    });

    it('renders fallback options for streams', () => {
      render(<PreviewStreamModal {...defaultProps} stream={mockStream} />);
      expect(screen.getByText('Open in VLC')).toBeInTheDocument();
      expect(screen.getByText('Download M3U')).toBeInTheDocument();
      expect(screen.getByText('Copy URL')).toBeInTheDocument();
    });
  });

  describe('rendering - channel preview', () => {
    it('renders channel preview title', () => {
      render(<PreviewStreamModal {...defaultProps} channel={mockChannel} />);
      expect(screen.getByText('Channel Preview')).toBeInTheDocument();
    });

    it('renders channel name', () => {
      render(<PreviewStreamModal {...defaultProps} channel={mockChannel} />);
      expect(screen.getByText('Test Channel')).toBeInTheDocument();
    });

    it('renders channel number metadata', () => {
      render(<PreviewStreamModal {...defaultProps} channel={mockChannel} />);
      expect(screen.getByText('Channel 101')).toBeInTheDocument();
    });

    it('renders stream count metadata', () => {
      render(<PreviewStreamModal {...defaultProps} channel={mockChannel} />);
      expect(screen.getByText('2 streams')).toBeInTheDocument();
    });

    it('renders TVG-ID for channel', () => {
      render(<PreviewStreamModal {...defaultProps} channel={mockChannel} />);
      expect(screen.getByText('TVG-ID: test.channel')).toBeInTheDocument();
    });

    it('renders channel preview note', () => {
      render(<PreviewStreamModal {...defaultProps} channel={mockChannel} />);
      expect(screen.getByText(/channel output as it would appear to clients/)).toBeInTheDocument();
    });

    it('does not render fallback options for channels', () => {
      render(<PreviewStreamModal {...defaultProps} channel={mockChannel} />);
      expect(screen.queryByText('Open in VLC')).not.toBeInTheDocument();
    });
  });

  describe('preview mode indicator', () => {
    it('fetches and displays preview mode', async () => {
      render(<PreviewStreamModal {...defaultProps} stream={mockStream} />);

      await waitFor(() => {
        expect(screen.getByText('Transcode')).toBeInTheDocument();
      });
    });

    it('shows mode tooltip on hover', async () => {
      render(<PreviewStreamModal {...defaultProps} stream={mockStream} />);

      await waitFor(() => {
        expect(screen.getByText('Transcode')).toBeInTheDocument();
      });

      // Find the parent metadata-mode element and check its title
      const modeText = screen.getByText('Transcode');
      const modeElement = modeText.closest('.metadata-item');
      expect(modeElement).toHaveAttribute('title', 'Audio transcoded to AAC');
    });
  });

  describe('close functionality', () => {
    it('renders close button', () => {
      render(<PreviewStreamModal {...defaultProps} stream={mockStream} />);
      expect(screen.getByText('close')).toBeInTheDocument();
    });

    it('calls onClose when close button clicked', () => {
      render(<PreviewStreamModal {...defaultProps} stream={mockStream} />);
      fireEvent.click(screen.getByText('close'));
      expect(defaultProps.onClose).toHaveBeenCalled();
    });

    it('does not close when overlay clicked', () => {
      render(<PreviewStreamModal {...defaultProps} stream={mockStream} />);
      fireEvent.click(document.querySelector('.modal-overlay')!);
      expect(defaultProps.onClose).not.toHaveBeenCalled();
    });

    it('does not close when modal content clicked', async () => {
      render(<PreviewStreamModal {...defaultProps} stream={mockStream} />);

      // Wait for async state updates
      await waitFor(() => {
        expect(screen.getByText('Stream Preview')).toBeInTheDocument();
      });

      // Click on the modal content (not overlay)
      const modalContent = document.querySelector('.preview-stream-modal')!;
      fireEvent.click(modalContent);
      expect(defaultProps.onClose).not.toHaveBeenCalled();
    });

    it('renders footer close button', () => {
      render(<PreviewStreamModal {...defaultProps} stream={mockStream} />);
      expect(screen.getByText('Close')).toBeInTheDocument();
    });

    it('calls onClose when footer close button clicked', () => {
      render(<PreviewStreamModal {...defaultProps} stream={mockStream} />);
      fireEvent.click(screen.getByText('Close'));
      expect(defaultProps.onClose).toHaveBeenCalled();
    });
  });

  describe('player state', () => {
    it('shows loading status initially', async () => {
      render(<PreviewStreamModal {...defaultProps} stream={mockStream} />);

      // Simulate loading state
      const callbacks = (window as unknown as { __videoPlayerCallbacks: { onStateChange: (s: string) => void } }).__videoPlayerCallbacks;
      await act(async () => {
        callbacks?.onStateChange('loading');
      });

      expect(screen.getByText('Connecting...')).toBeInTheDocument();
    });

    it('shows playing status when playing', async () => {
      render(<PreviewStreamModal {...defaultProps} stream={mockStream} />);

      const callbacks = (window as unknown as { __videoPlayerCallbacks: { onStateChange: (s: string) => void } }).__videoPlayerCallbacks;
      await act(async () => {
        callbacks?.onStateChange('playing');
      });

      expect(screen.getByText('Live')).toBeInTheDocument();
    });

    it('shows error status on error', async () => {
      render(<PreviewStreamModal {...defaultProps} stream={mockStream} />);

      const callbacks = (window as unknown as { __videoPlayerCallbacks: { onStateChange: (s: string) => void; onError: (e: { message: string }) => void } }).__videoPlayerCallbacks;
      await act(async () => {
        callbacks?.onStateChange('error');
        callbacks?.onError({ message: 'Test error' });
      });

      expect(screen.getByText('Error')).toBeInTheDocument();
    });
  });

  describe('error handling', () => {
    it('displays error message when error occurs', async () => {
      render(<PreviewStreamModal {...defaultProps} stream={mockStream} />);

      const callbacks = (window as unknown as { __videoPlayerCallbacks: { onStateChange: (s: string) => void; onError: (e: { message: string; details?: string }) => void } }).__videoPlayerCallbacks;
      await act(async () => {
        callbacks?.onStateChange('error');
        callbacks?.onError({ message: 'Playback failed', details: 'Connection timeout' });
      });

      // Check for error message
      expect(screen.getByText('Playback Error')).toBeInTheDocument();
    });

    it('shows alternative options section for streams', () => {
      render(<PreviewStreamModal {...defaultProps} stream={mockStream} />);
      // The "Alternative Options" header should be visible for streams
      expect(screen.getByText('Alternative Options')).toBeInTheDocument();
    });
  });

  describe('copy URL functionality', () => {
    it('copies URL to clipboard on button click', async () => {
      // Mock clipboard API
      const writeText = vi.fn().mockResolvedValue(undefined);
      Object.assign(navigator, {
        clipboard: { writeText },
      });

      render(<PreviewStreamModal {...defaultProps} stream={mockStream} />);
      fireEvent.click(screen.getByText('Copy URL'));

      await waitFor(() => {
        expect(writeText).toHaveBeenCalledWith(mockStream.url);
      });
    });

    it('shows copied confirmation', async () => {
      const writeText = vi.fn().mockResolvedValue(undefined);
      Object.assign(navigator, {
        clipboard: { writeText },
      });

      render(<PreviewStreamModal {...defaultProps} stream={mockStream} />);
      fireEvent.click(screen.getByText('Copy URL'));

      await waitFor(() => {
        expect(screen.getByText('Copied!')).toBeInTheDocument();
      });
    });
  });

  describe('preview URL generation', () => {
    it('generates stream preview URL for streams', () => {
      render(<PreviewStreamModal {...defaultProps} stream={mockStream} />);

      // Check the src that was passed to VideoPlayer via the stored window value
      const passedSrc = (window as unknown as { __videoPlayerSrc: string }).__videoPlayerSrc;
      expect(passedSrc).toContain('/api/stream-preview/1');
    });

    it('generates channel preview URL for channels', () => {
      render(<PreviewStreamModal {...defaultProps} channel={mockChannel} />);

      // Check the src that was passed to VideoPlayer via the stored window value
      const passedSrc = (window as unknown as { __videoPlayerSrc: string }).__videoPlayerSrc;
      expect(passedSrc).toContain('/api/channel-preview/1');
    });
  });
});
