/**
 * Unit tests for VideoPlayer component.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { VideoPlayer } from './VideoPlayer';

// Mock mpegts.js
const mockPlayer = {
  attachMediaElement: vi.fn(),
  load: vi.fn(),
  play: vi.fn().mockResolvedValue(undefined),
  pause: vi.fn(),
  unload: vi.fn(),
  detachMediaElement: vi.fn(),
  destroy: vi.fn(),
  on: vi.fn(),
};

vi.mock('mpegts.js', () => ({
  default: {
    isSupported: vi.fn(() => true),
    createPlayer: vi.fn(() => mockPlayer),
    Events: {
      ERROR: 'error',
      LOADING_COMPLETE: 'loading_complete',
      MEDIA_INFO: 'media_info',
    },
  },
}));

describe('VideoPlayer', () => {
  const defaultProps = {
    src: 'http://example.com/stream.ts',
    onStateChange: vi.fn(),
    onError: vi.fn(),
    onPlay: vi.fn(),
    onPause: vi.fn(),
    onEnded: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  describe('rendering', () => {
    it('renders the video container', () => {
      render(<VideoPlayer {...defaultProps} />);
      expect(document.querySelector('.video-player')).toBeInTheDocument();
    });

    it('renders the video element', () => {
      render(<VideoPlayer {...defaultProps} />);
      expect(document.querySelector('video')).toBeInTheDocument();
    });

    it('applies custom className', () => {
      render(<VideoPlayer {...defaultProps} className="custom-class" />);
      expect(document.querySelector('.video-player')).toHaveClass('custom-class');
    });

    it('applies custom dimensions', () => {
      render(<VideoPlayer {...defaultProps} width={640} height={360} />);
      const container = document.querySelector('.video-player') as HTMLElement;
      expect(container.style.width).toBe('640px');
      expect(container.style.height).toBe('360px');
    });

    it('applies string dimensions', () => {
      render(<VideoPlayer {...defaultProps} width="100%" height="auto" />);
      const container = document.querySelector('.video-player') as HTMLElement;
      expect(container.style.width).toBe('100%');
      expect(container.style.height).toBe('auto');
    });
  });

  describe('controls', () => {
    it('renders controls by default', () => {
      render(<VideoPlayer {...defaultProps} />);
      expect(document.querySelector('.video-player__controls')).toBeInTheDocument();
    });

    it('hides controls when controls prop is false', () => {
      render(<VideoPlayer {...defaultProps} controls={false} />);
      expect(document.querySelector('.video-player__controls')).not.toBeInTheDocument();
    });

    it('renders play/pause button', () => {
      render(<VideoPlayer {...defaultProps} />);
      expect(screen.getByTitle('Play')).toBeInTheDocument();
    });

    it('renders mute/unmute button', () => {
      render(<VideoPlayer {...defaultProps} />);
      expect(screen.getByTitle('Unmute')).toBeInTheDocument();
    });

    it('renders volume slider', () => {
      render(<VideoPlayer {...defaultProps} />);
      expect(screen.getByTitle('Volume')).toBeInTheDocument();
    });

    it('renders fullscreen button', () => {
      render(<VideoPlayer {...defaultProps} />);
      expect(screen.getByTitle('Fullscreen')).toBeInTheDocument();
    });
  });

  describe('loading state', () => {
    it('shows loading overlay initially', () => {
      render(<VideoPlayer {...defaultProps} />);
      expect(screen.getByText('Loading stream...')).toBeInTheDocument();
    });

    it('calls onStateChange with loading', () => {
      render(<VideoPlayer {...defaultProps} />);
      expect(defaultProps.onStateChange).toHaveBeenCalledWith('loading');
    });
  });

  describe('player initialization', () => {
    it('checks browser support', async () => {
      const mpegts = await import('mpegts.js');
      render(<VideoPlayer {...defaultProps} />);
      expect(mpegts.default.isSupported).toHaveBeenCalled();
    });

    it('creates player with correct config', async () => {
      const mpegts = await import('mpegts.js');
      render(<VideoPlayer {...defaultProps} />);
      expect(mpegts.default.createPlayer).toHaveBeenCalledWith(
        expect.objectContaining({
          type: 'mpegts',
          isLive: true,
          url: defaultProps.src,
        }),
        expect.any(Object)
      );
    });

    it('attaches player to video element', () => {
      render(<VideoPlayer {...defaultProps} />);
      expect(mockPlayer.attachMediaElement).toHaveBeenCalled();
    });

    it('loads the stream', () => {
      render(<VideoPlayer {...defaultProps} />);
      expect(mockPlayer.load).toHaveBeenCalled();
    });

    it('auto-plays by default', () => {
      render(<VideoPlayer {...defaultProps} />);
      expect(mockPlayer.play).toHaveBeenCalled();
    });

    it('does not auto-play when autoPlay is false', () => {
      render(<VideoPlayer {...defaultProps} autoPlay={false} />);
      expect(mockPlayer.play).not.toHaveBeenCalled();
    });
  });

  describe('muted state', () => {
    it('starts muted by default', () => {
      render(<VideoPlayer {...defaultProps} />);
      const video = document.querySelector('video') as HTMLVideoElement;
      expect(video.muted).toBe(true);
    });

    it('starts unmuted when muted prop is false', () => {
      render(<VideoPlayer {...defaultProps} muted={false} />);
      const video = document.querySelector('video') as HTMLVideoElement;
      expect(video.muted).toBe(false);
    });

    it('toggles mute on button click', () => {
      render(<VideoPlayer {...defaultProps} />);
      const muteButton = screen.getByTitle('Unmute');

      // Video starts muted
      const video = document.querySelector('video') as HTMLVideoElement;
      expect(video.muted).toBe(true);

      // Click to unmute
      fireEvent.click(muteButton);
      expect(video.muted).toBe(false);

      // Click to mute again
      fireEvent.click(screen.getByTitle('Mute'));
      expect(video.muted).toBe(true);
    });
  });

  describe('volume control', () => {
    it('renders volume slider', () => {
      render(<VideoPlayer {...defaultProps} />);
      const slider = screen.getByTitle('Volume') as HTMLInputElement;
      expect(slider).toBeInTheDocument();
      expect(slider.type).toBe('range');
    });

    it('updates volume on slider change', () => {
      render(<VideoPlayer {...defaultProps} muted={false} />);
      const video = document.querySelector('video') as HTMLVideoElement;
      const slider = screen.getByTitle('Volume') as HTMLInputElement;

      fireEvent.change(slider, { target: { value: '0.5' } });
      expect(video.volume).toBe(0.5);
    });
  });

  describe('unsupported browser', () => {
    it('shows error when MSE not supported', async () => {
      const mpegts = await import('mpegts.js');
      vi.mocked(mpegts.default.isSupported).mockReturnValueOnce(false);

      render(<VideoPlayer {...defaultProps} />);

      expect(defaultProps.onError).toHaveBeenCalledWith(
        expect.objectContaining({
          code: 'UNSUPPORTED',
          message: 'Your browser does not support MPEG-TS playback',
        })
      );
    });
  });

  describe('cleanup', () => {
    it('cleans up player on unmount', () => {
      const { unmount } = render(<VideoPlayer {...defaultProps} />);

      unmount();

      expect(mockPlayer.pause).toHaveBeenCalled();
      expect(mockPlayer.unload).toHaveBeenCalled();
      expect(mockPlayer.detachMediaElement).toHaveBeenCalled();
      expect(mockPlayer.destroy).toHaveBeenCalled();
    });
  });

  describe('no src', () => {
    it('does not create player when src is empty', async () => {
      const mpegts = await import('mpegts.js');
      vi.mocked(mpegts.default.createPlayer).mockClear();

      render(<VideoPlayer {...defaultProps} src="" />);

      expect(mpegts.default.createPlayer).not.toHaveBeenCalled();
    });
  });
});
