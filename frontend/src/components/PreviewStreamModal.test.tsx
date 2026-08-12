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

import * as api from '../services/api';

describe('PreviewStreamModal', () => {
  const mockStream: Stream = {
    id: 1,
    name: 'Test Stream',
    url: 'http://example.com/stream.ts',
    tvg_id: 'test.stream',
    channel_group_name: 'Sports',
    m3u_account: 1,
    logo_url: null,
    channel_group: 1,
    is_custom: false,
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
    auto_created: false,
    auto_created_by: null,
    auto_created_by_name: null,
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

    it('returns null when no stream or channel provided', async () => {
      const { container } = render(
        <PreviewStreamModal {...defaultProps} />
      );
      expect(container.firstChild).toBeNull();

      // Let the mount-time getSettings() fetch settle so the resulting state
      // update (setPreviewMode) happens inside act() — otherwise React logs
      // an act() warning for the un-awaited promise resolution.
      await waitFor(() => {
        expect(api.getSettings).toHaveBeenCalled();
      });
    });
  });

  describe('rendering - stream preview', () => {
    it('renders modal when isOpen is true with stream', async () => {
      render(<PreviewStreamModal {...defaultProps} stream={mockStream} />);
      expect(screen.getByText('Stream Preview')).toBeInTheDocument();

      // Let the mount-time getSettings() fetch settle so the resulting state
      // update (setPreviewMode) happens inside act().
      await waitFor(() => expect(screen.getByText('Transcode')).toBeInTheDocument());
    });

    it('renders stream name', async () => {
      render(<PreviewStreamModal {...defaultProps} stream={mockStream} />);
      expect(screen.getByText('Test Stream')).toBeInTheDocument();

      await waitFor(() => expect(screen.getByText('Transcode')).toBeInTheDocument());
    });

    it('renders channel name when provided', async () => {
      render(
        <PreviewStreamModal
          {...defaultProps}
          stream={mockStream}
          channelName="My Channel"
        />
      );
      expect(screen.getByText('My Channel')).toBeInTheDocument();

      await waitFor(() => expect(screen.getByText('Transcode')).toBeInTheDocument());
    });

    it('renders TVG-ID metadata', async () => {
      render(<PreviewStreamModal {...defaultProps} stream={mockStream} />);
      expect(screen.getByText('TVG-ID: test.stream')).toBeInTheDocument();

      await waitFor(() => expect(screen.getByText('Transcode')).toBeInTheDocument());
    });

    it('renders channel group metadata', async () => {
      render(<PreviewStreamModal {...defaultProps} stream={mockStream} />);
      expect(screen.getByText('Sports')).toBeInTheDocument();

      await waitFor(() => expect(screen.getByText('Transcode')).toBeInTheDocument());
    });

    it('renders provider name when provided', async () => {
      render(
        <PreviewStreamModal
          {...defaultProps}
          stream={mockStream}
          providerName="My IPTV Provider"
        />
      );
      expect(screen.getByText('My IPTV Provider')).toBeInTheDocument();

      await waitFor(() => expect(screen.getByText('Transcode')).toBeInTheDocument());
    });

    it('renders video player', async () => {
      render(<PreviewStreamModal {...defaultProps} stream={mockStream} />);
      expect(screen.getByTestId('mock-video-player')).toBeInTheDocument();

      await waitFor(() => expect(screen.getByText('Transcode')).toBeInTheDocument());
    });

    it('renders fallback options for streams', async () => {
      render(<PreviewStreamModal {...defaultProps} stream={mockStream} />);
      expect(screen.getByText('Open in VLC')).toBeInTheDocument();
      expect(screen.getByText('Download M3U')).toBeInTheDocument();
      expect(screen.getByText('Copy URL')).toBeInTheDocument();

      await waitFor(() => expect(screen.getByText('Transcode')).toBeInTheDocument());
    });
  });

  describe('rendering - channel preview', () => {
    it('renders channel preview title', async () => {
      render(<PreviewStreamModal {...defaultProps} channel={mockChannel} />);
      expect(screen.getByText('Channel Preview')).toBeInTheDocument();

      // Let the mount-time getSettings() fetch settle so the resulting state
      // update (setPreviewMode) happens inside act().
      await waitFor(() => expect(screen.getByText('Transcode')).toBeInTheDocument());
    });

    it('renders channel name', async () => {
      render(<PreviewStreamModal {...defaultProps} channel={mockChannel} />);
      expect(screen.getByText('Test Channel')).toBeInTheDocument();

      await waitFor(() => expect(screen.getByText('Transcode')).toBeInTheDocument());
    });

    it('renders channel number metadata', async () => {
      render(<PreviewStreamModal {...defaultProps} channel={mockChannel} />);
      expect(screen.getByText('Channel 101')).toBeInTheDocument();

      await waitFor(() => expect(screen.getByText('Transcode')).toBeInTheDocument());
    });

    it('renders stream count metadata', async () => {
      render(<PreviewStreamModal {...defaultProps} channel={mockChannel} />);
      expect(screen.getByText('2 streams')).toBeInTheDocument();

      await waitFor(() => expect(screen.getByText('Transcode')).toBeInTheDocument());
    });

    it('renders TVG-ID for channel', async () => {
      render(<PreviewStreamModal {...defaultProps} channel={mockChannel} />);
      expect(screen.getByText('TVG-ID: test.channel')).toBeInTheDocument();

      await waitFor(() => expect(screen.getByText('Transcode')).toBeInTheDocument());
    });

    it('renders channel preview note', async () => {
      render(<PreviewStreamModal {...defaultProps} channel={mockChannel} />);
      expect(screen.getByText(/channel output as it would appear to clients/)).toBeInTheDocument();

      await waitFor(() => expect(screen.getByText('Transcode')).toBeInTheDocument());
    });

    it('does not render fallback options for channels', async () => {
      render(<PreviewStreamModal {...defaultProps} channel={mockChannel} />);
      expect(screen.queryByText('Open in VLC')).not.toBeInTheDocument();

      await waitFor(() => expect(screen.getByText('Transcode')).toBeInTheDocument());
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
    it('renders close button', async () => {
      render(<PreviewStreamModal {...defaultProps} stream={mockStream} />);
      expect(screen.getByText('close')).toBeInTheDocument();

      // Let the mount-time getSettings() fetch settle.
      await waitFor(() => expect(screen.getByText('Transcode')).toBeInTheDocument());
    });

    it('calls onClose when close button clicked', async () => {
      render(<PreviewStreamModal {...defaultProps} stream={mockStream} />);
      fireEvent.click(screen.getByText('close'));
      expect(defaultProps.onClose).toHaveBeenCalled();

      // Let the mount-time getSettings() fetch settle.
      await waitFor(() => expect(api.getSettings).toHaveBeenCalled());
    });

    it('does not close when overlay clicked', async () => {
      render(<PreviewStreamModal {...defaultProps} stream={mockStream} />);
      fireEvent.click(document.querySelector('.modal-overlay')!);
      expect(defaultProps.onClose).not.toHaveBeenCalled();

      // Let the mount-time getSettings() fetch settle.
      await waitFor(() => expect(screen.getByText('Transcode')).toBeInTheDocument());
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

    it('renders footer close button', async () => {
      render(<PreviewStreamModal {...defaultProps} stream={mockStream} />);
      expect(screen.getByText('Close')).toBeInTheDocument();

      // Let the mount-time getSettings() fetch settle.
      await waitFor(() => expect(screen.getByText('Transcode')).toBeInTheDocument());
    });

    it('calls onClose when footer close button clicked', async () => {
      render(<PreviewStreamModal {...defaultProps} stream={mockStream} />);
      fireEvent.click(screen.getByText('Close'));
      expect(defaultProps.onClose).toHaveBeenCalled();

      // Let the mount-time getSettings() fetch settle.
      await waitFor(() => expect(api.getSettings).toHaveBeenCalled());
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
      expect(screen.getByText(/raw stream previews connect from the ECM container/i)).toBeInTheDocument();
      expect(screen.getByText(/same VPN or network route/i)).toBeInTheDocument();
    });

    it('does not show raw-provider routing guidance for channel proxy errors', async () => {
      render(<PreviewStreamModal {...defaultProps} channel={mockChannel} />);
      const callbacks = (window as unknown as { __videoPlayerCallbacks: { onStateChange: (s: string) => void; onError: (e: { message: string }) => void } }).__videoPlayerCallbacks;
      await act(async () => {
        callbacks?.onStateChange('error');
        callbacks?.onError({ message: 'Playback failed' });
      });
      expect(screen.queryByText(/raw stream previews connect from the ECM container/i)).not.toBeInTheDocument();
    });

    it('shows alternative options section for streams', async () => {
      render(<PreviewStreamModal {...defaultProps} stream={mockStream} />);
      // The "Alternative Options" header should be visible for streams
      expect(screen.getByText('Alternative Options')).toBeInTheDocument();

      // Let the mount-time getSettings() fetch settle.
      await waitFor(() => expect(screen.getByText('Transcode')).toBeInTheDocument());
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
    it('generates stream preview URL for streams', async () => {
      render(<PreviewStreamModal {...defaultProps} stream={mockStream} />);

      // Check the src that was passed to VideoPlayer via the stored window value
      const passedSrc = (window as unknown as { __videoPlayerSrc: string }).__videoPlayerSrc;
      expect(passedSrc).toContain('/api/stream-preview/1');

      // Let the mount-time getSettings() fetch settle.
      await waitFor(() => expect(screen.getByText('Transcode')).toBeInTheDocument());
    });

    it('generates channel preview URL for channels', async () => {
      render(<PreviewStreamModal {...defaultProps} channel={mockChannel} />);

      // Check the src that was passed to VideoPlayer via the stored window value
      const passedSrc = (window as unknown as { __videoPlayerSrc: string }).__videoPlayerSrc;
      expect(passedSrc).toContain('/api/channel-preview/1');

      // Let the mount-time getSettings() fetch settle.
      await waitFor(() => expect(screen.getByText('Transcode')).toBeInTheDocument());
    });
  });
});
