import { memo, useRef, useEffect, useState, useCallback } from 'react';
import mpegts from 'mpegts.js';
import type { VideoPlayerProps, VideoPlayerState, VideoPlayerError } from '../types';
import './VideoPlayer.css';

/**
 * VideoPlayer component for MPEG-TS stream playback using mpegts.js
 *
 * This component wraps mpegts.js to provide in-browser playback of MPEG-TS streams
 * via Media Source Extensions (MSE). It transmuxes MPEG-TS to fMP4 on-the-fly.
 */
export const VideoPlayer = memo(function VideoPlayer({
  src,
  autoPlay = true,
  controls = true,
  muted = true,
  className = '',
  width,
  height,
  onStateChange,
  onError,
  onPlay,
  onPause,
  onEnded,
}: VideoPlayerProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const playerRef = useRef<mpegts.Player | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const [playerState, setPlayerState] = useState<VideoPlayerState>('idle');
  const [error, setError] = useState<VideoPlayerError | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [isMuted, setIsMuted] = useState(muted);
  const [volume, setVolume] = useState(1);
  const [isFullscreen, setIsFullscreen] = useState(false);

  // Update state and notify parent
  const updateState = useCallback((newState: VideoPlayerState) => {
    setPlayerState(newState);
    onStateChange?.(newState);
  }, [onStateChange]);

  // Handle errors
  const handleError = useCallback((errorInfo: VideoPlayerError) => {
    setError(errorInfo);
    updateState('error');
    onError?.(errorInfo);
  }, [onError, updateState]);

  // Initialize player
  useEffect(() => {
    if (!videoRef.current || !src) return;

    // Check browser support
    if (!mpegts.isSupported()) {
      // Use a timeout to avoid synchronous setState during effect execution
      const timeoutId = setTimeout(() => {
        handleError({
          code: 'UNSUPPORTED',
          message: 'Your browser does not support MPEG-TS playback',
          details: 'Media Source Extensions (MSE) is required for playback',
        });
      }, 0);
      return () => clearTimeout(timeoutId);
    }

    // Use a timeout to avoid synchronous setState during effect execution
    const loadingTimeoutId = setTimeout(() => {
      updateState('loading');
    }, 0);

    // Create player with buffered configuration for smooth playback
    const player = mpegts.createPlayer(
      {
        type: 'mpegts',
        isLive: true,
        url: src,
      },
      {
        enableWorker: true,
        enableStashBuffer: true,           // Enable buffering for smoother playback
        stashInitialSize: 512 * 1024,      // 512KB initial buffer
        isLive: true,
        liveBufferLatencyChasing: false,   // Don't aggressively chase latency (causes choppiness)
        liveBufferLatencyMaxLatency: 10.0, // Allow up to 10 seconds of latency
        liveBufferLatencyMinRemain: 3.0,   // Keep at least 3 seconds buffered
        autoCleanupSourceBuffer: true,
        autoCleanupMaxBackwardDuration: 30,  // Keep 30s of backward buffer
        autoCleanupMinBackwardDuration: 15,  // Clean when buffer exceeds 15s behind
      }
    );

    playerRef.current = player;

    // Attach to video element
    player.attachMediaElement(videoRef.current);

    // Set up event handlers
    player.on(mpegts.Events.ERROR, (errorType: string, errorDetail: string, errorInfo: { code?: number; msg?: string }) => {
      console.error('[VideoPlayer] Error:', errorType, errorDetail, errorInfo);

      // Check for unsupported codec errors
      const errorMsg = errorInfo?.msg || errorDetail || '';
      const isCodecError = errorMsg.includes('codecs=') ||
                           errorMsg.includes('unsupported') ||
                           errorType === 'MediaMSEError';

      // Extract codec name if present
      const codecMatch = errorMsg.match(/codecs=([^'")]+)/);
      const codecName = codecMatch ? codecMatch[1] : null;

      if (isCodecError && codecName) {
        // Provide user-friendly codec error message
        const codecMap: Record<string, string> = {
          'ac-3': 'AC-3 (Dolby Digital)',
          'ec-3': 'E-AC-3 (Dolby Digital Plus)',
          'dtsc': 'DTS',
          'dtsh': 'DTS-HD',
        };
        const friendlyCodec = codecMap[codecName] || codecName.toUpperCase();

        handleError({
          code: 'UNSUPPORTED_CODEC',
          message: `Unsupported audio codec: ${friendlyCodec}`,
          details: 'This codec is not supported by browser playback. Use VLC or download the M3U file instead.',
        });
      } else {
        handleError({
          code: errorType,
          message: errorDetail,
          details: errorInfo?.msg,
        });
      }
    });

    player.on(mpegts.Events.LOADING_COMPLETE, () => {
      console.log('[VideoPlayer] Loading complete');
    });

    player.on(mpegts.Events.MEDIA_INFO, (mediaInfo: mpegts.MSEPlayerMediaInfo) => {
      console.log('[VideoPlayer] Media info:', mediaInfo);
    });

    // Load the stream
    player.load();

    // Auto-play if requested
    if (autoPlay) {
      const playPromise = player.play();
      if (playPromise && typeof playPromise.catch === 'function') {
        playPromise.catch((e: Error) => {
          // Auto-play may be blocked by browser
          console.warn('[VideoPlayer] Auto-play blocked:', e.message);
          updateState('paused');
        });
      }
    }

    // Cleanup on unmount
    return () => {
      clearTimeout(timeoutId);
      clearTimeout(loadingTimeoutId);
      if (playerRef.current) {
        playerRef.current.pause();
        playerRef.current.unload();
        playerRef.current.detachMediaElement();
        playerRef.current.destroy();
        playerRef.current = null;
      }
    };
  }, [src, autoPlay, handleError, updateState]);

  // Video element event handlers
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;

    const handleVideoPlay = () => {
      setIsPlaying(true);
      updateState('playing');
      onPlay?.();
    };

    const handleVideoPause = () => {
      setIsPlaying(false);
      updateState('paused');
      onPause?.();
    };

    const handleVideoEnded = () => {
      setIsPlaying(false);
      updateState('ended');
      onEnded?.();
    };

    const handleVideoError = () => {
      const videoError = video.error;
      handleError({
        code: `MEDIA_ERROR_${videoError?.code || 'UNKNOWN'}`,
        message: videoError?.message || 'Video playback error',
      });
    };

    const handleWaiting = () => {
      if (playerState !== 'error') {
        updateState('loading');
      }
    };

    const handleCanPlay = () => {
      if (playerState === 'loading') {
        updateState(isPlaying ? 'playing' : 'paused');
      }
    };

    video.addEventListener('play', handleVideoPlay);
    video.addEventListener('pause', handleVideoPause);
    video.addEventListener('ended', handleVideoEnded);
    video.addEventListener('error', handleVideoError);
    video.addEventListener('waiting', handleWaiting);
    video.addEventListener('canplay', handleCanPlay);

    return () => {
      video.removeEventListener('play', handleVideoPlay);
      video.removeEventListener('pause', handleVideoPause);
      video.removeEventListener('ended', handleVideoEnded);
      video.removeEventListener('error', handleVideoError);
      video.removeEventListener('waiting', handleWaiting);
      video.removeEventListener('canplay', handleCanPlay);
    };
  }, [handleError, isPlaying, onEnded, onPause, onPlay, playerState, updateState]);

  // Control handlers
  const togglePlay = useCallback(() => {
    const video = videoRef.current;
    if (!video) return;

    if (isPlaying) {
      video.pause();
    } else {
      video.play().catch((e: Error) => {
        console.warn('[VideoPlayer] Play blocked:', e.message);
      });
    }
  }, [isPlaying]);

  const toggleMute = useCallback(() => {
    const video = videoRef.current;
    if (!video) return;

    video.muted = !video.muted;
    setIsMuted(video.muted);
  }, []);

  const handleVolumeChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const video = videoRef.current;
    if (!video) return;

    const newVolume = parseFloat(e.target.value);
    video.volume = newVolume;
    setVolume(newVolume);

    if (newVolume > 0 && video.muted) {
      video.muted = false;
      setIsMuted(false);
    }
  }, []);

  const toggleFullscreen = useCallback(() => {
    const container = containerRef.current;
    if (!container) return;

    if (!document.fullscreenElement) {
      container.requestFullscreen().then(() => {
        setIsFullscreen(true);
      }).catch((e) => {
        console.warn('[VideoPlayer] Fullscreen request failed:', e);
      });
    } else {
      document.exitFullscreen().then(() => {
        setIsFullscreen(false);
      });
    }
  }, []);

  // Listen for fullscreen changes
  useEffect(() => {
    const handleFullscreenChange = () => {
      setIsFullscreen(!!document.fullscreenElement);
    };

    document.addEventListener('fullscreenchange', handleFullscreenChange);
    return () => {
      document.removeEventListener('fullscreenchange', handleFullscreenChange);
    };
  }, []);

  // Calculate dimensions
  const style: React.CSSProperties = {};
  if (width) style.width = typeof width === 'number' ? `${width}px` : width;
  if (height) style.height = typeof height === 'number' ? `${height}px` : height;

  return (
    <div
      ref={containerRef}
      className={`video-player ${className} ${isFullscreen ? 'video-player--fullscreen' : ''}`}
      style={style}
    >
      <video
        ref={videoRef}
        className="video-player__video"
        muted={isMuted}
        playsInline
      />

      {/* Loading overlay */}
      {playerState === 'loading' && (
        <div className="video-player__overlay video-player__overlay--loading">
          <div className="video-player__spinner" />
          <span>Loading stream...</span>
        </div>
      )}

      {/* Error overlay */}
      {playerState === 'error' && error && (
        <div className="video-player__overlay video-player__overlay--error">
          <span className="material-icons">error_outline</span>
          <span className="video-player__error-message">{error.message}</span>
          {error.details && (
            <span className="video-player__error-details">{error.details}</span>
          )}
        </div>
      )}

      {/* Controls */}
      {controls && playerState !== 'error' && (
        <div className="video-player__controls">
          <button
            type="button"
            className="video-player__control-btn"
            onClick={togglePlay}
            title={isPlaying ? 'Pause' : 'Play'}
          >
            <span className="material-icons">
              {isPlaying ? 'pause' : 'play_arrow'}
            </span>
          </button>

          <button
            type="button"
            className="video-player__control-btn"
            onClick={toggleMute}
            title={isMuted ? 'Unmute' : 'Mute'}
          >
            <span className="material-icons">
              {isMuted ? 'volume_off' : volume > 0.5 ? 'volume_up' : 'volume_down'}
            </span>
          </button>

          <input
            type="range"
            className="video-player__volume-slider"
            min="0"
            max="1"
            step="0.1"
            value={isMuted ? 0 : volume}
            onChange={handleVolumeChange}
            title="Volume"
          />

          <div className="video-player__spacer" />

          <button
            type="button"
            className="video-player__control-btn"
            onClick={toggleFullscreen}
            title={isFullscreen ? 'Exit Fullscreen' : 'Fullscreen'}
          >
            <span className="material-icons">
              {isFullscreen ? 'fullscreen_exit' : 'fullscreen'}
            </span>
          </button>
        </div>
      )}
    </div>
  );
});
