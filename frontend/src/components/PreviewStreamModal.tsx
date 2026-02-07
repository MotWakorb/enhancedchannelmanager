import { memo, useState, useCallback, useMemo, useEffect } from 'react';
import { VideoPlayer } from './VideoPlayer';
import * as api from '../services/api';
import type { PreviewStreamModalProps, VideoPlayerState, VideoPlayerError } from '../types';
import type { StreamPreviewMode } from '../services/api';
import './ModalBase.css';
import './PreviewStreamModal.css';
import { ModalOverlay } from './ModalOverlay';

// Labels for preview modes
const PREVIEW_MODE_LABELS: Record<StreamPreviewMode, { label: string; icon: string; description: string }> = {
  passthrough: { label: 'Passthrough', icon: 'fast_forward', description: 'Direct stream (no processing)' },
  transcode: { label: 'Transcode', icon: 'transform', description: 'Audio transcoded to AAC' },
  video_only: { label: 'Video Only', icon: 'videocam', description: 'Audio stripped' },
};

/**
 * PreviewStreamModal - Modal for previewing MPEG-TS streams or channels in-browser
 *
 * Features:
 * - Video player with mpegts.js for MPEG-TS playback
 * - Stream/Channel metadata display
 * - Fallback options (Open in VLC, Copy URL)
 * - Supports both individual streams and channel output preview
 */
export const PreviewStreamModal = memo(function PreviewStreamModal({
  isOpen,
  onClose,
  stream,
  channel,
  channelName,
  providerName,
}: PreviewStreamModalProps) {
  const [playerState, setPlayerState] = useState<VideoPlayerState>('idle');
  const [playerError, setPlayerError] = useState<VideoPlayerError | null>(null);
  const [urlCopied, setUrlCopied] = useState(false);
  const [previewMode, setPreviewMode] = useState<StreamPreviewMode>('passthrough');

  // Fetch current preview mode setting when modal opens
  useEffect(() => {
    if (isOpen) {
      api.getSettings()
        .then((settings) => {
          if (settings.stream_preview_mode) {
            setPreviewMode(settings.stream_preview_mode);
          }
        })
        .catch(() => {
          // Ignore errors, use default
        });
    }
  }, [isOpen]);

  // Determine if we're previewing a channel or a stream
  const isChannelPreview = !!channel;
  const previewTarget = channel || stream;

  // Get display info based on what we're previewing
  const displayInfo = useMemo(() => {
    if (channel) {
      return {
        name: channel.name,
        title: 'Channel Preview',
        icon: 'live_tv',
        // For channels, we don't have a direct URL - it goes through Dispatcharr
        externalUrl: null,
      };
    }
    if (stream) {
      return {
        name: stream.name,
        title: 'Stream Preview',
        icon: 'play_circle',
        externalUrl: stream.url,
      };
    }
    return null;
  }, [channel, stream]);

  // Handle player state changes
  const handleStateChange = useCallback((state: VideoPlayerState) => {
    setPlayerState(state);
  }, []);

  // Handle player errors
  const handleError = useCallback((error: VideoPlayerError) => {
    setPlayerError(error);
  }, []);

  // Copy URL to clipboard (only for streams with direct URLs)
  const handleCopyUrl = useCallback(async () => {
    if (!displayInfo?.externalUrl) return;

    try {
      await navigator.clipboard.writeText(displayInfo.externalUrl);
      setUrlCopied(true);
      setTimeout(() => setUrlCopied(false), 2000);
    } catch {
      // Fallback for older browsers
      const textArea = document.createElement('textarea');
      textArea.value = displayInfo.externalUrl;
      document.body.appendChild(textArea);
      textArea.select();
      document.execCommand('copy');
      document.body.removeChild(textArea);
      setUrlCopied(true);
      setTimeout(() => setUrlCopied(false), 2000);
    }
  }, [displayInfo?.externalUrl]);

  // Open in VLC (only for streams with direct URLs)
  const handleOpenInVLC = useCallback(() => {
    if (!displayInfo?.externalUrl) return;

    // Try VLC protocol handler
    const vlcUrl = `vlc://${encodeURIComponent(displayInfo.externalUrl)}`;
    window.location.href = vlcUrl;
  }, [displayInfo?.externalUrl]);

  // Download M3U file for external player
  const handleDownloadM3U = useCallback(() => {
    if (!displayInfo?.externalUrl) return;

    const m3uContent = `#EXTM3U\n#EXTINF:-1,${displayInfo.name || 'Stream'}\n${displayInfo.externalUrl}`;
    const blob = new Blob([m3uContent], { type: 'audio/x-mpegurl' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${(displayInfo.name || 'stream').replace(/[^a-zA-Z0-9]/g, '_')}.m3u`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  }, [displayInfo?.name, displayInfo?.externalUrl]);

  if (!isOpen || !previewTarget || !displayInfo) return null;

  // Build the proxy URL for playback
  // Use different endpoints for channels vs streams
  const previewUrl = isChannelPreview
    ? `${window.location.origin}/api/channel-preview/${channel!.id}`
    : `${window.location.origin}/api/stream-preview/${stream!.id}`;

  return (
    <ModalOverlay onClose={onClose}>
      <div
        className="modal-container modal-lg preview-stream-modal"
      >
        {/* Header */}
        <div className="modal-header">
          <div className="preview-stream-header-info">
            <h2>
              <span className="material-icons">{displayInfo.icon}</span>
              {displayInfo.title}
            </h2>
            {channelName && !isChannelPreview && (
              <span className="preview-stream-channel">{channelName}</span>
            )}
          </div>
          <button className="modal-close-btn" onClick={onClose}>
            <span className="material-icons">close</span>
          </button>
        </div>

        {/* Body */}
        <div className="modal-body modal-body-flush">
          {/* Video Player */}
          <div className="preview-stream-player">
            <VideoPlayer
              src={previewUrl}
              autoPlay={true}
              controls={true}
              muted={true}
              onStateChange={handleStateChange}
              onError={handleError}
            />
          </div>

          {/* Info */}
          <div className="preview-stream-info">
            <div className="preview-stream-info-header">
              <h3>{displayInfo.name}</h3>
              <div className="preview-stream-status">
                {playerState === 'loading' && (
                  <span className="status-loading">
                    <span className="material-icons modal-spinning-ccw">sync</span>
                    Connecting...
                  </span>
                )}
                {playerState === 'playing' && (
                  <span className="status-playing">
                    <span className="material-icons">circle</span>
                    Live
                  </span>
                )}
                {playerState === 'error' && (
                  <span className="status-error">
                    <span className="material-icons">error</span>
                    Error
                  </span>
                )}
              </div>
            </div>

            {/* Metadata - different for channels vs streams */}
            <div className="preview-stream-metadata">
              {/* Preview mode indicator - always shown */}
              <div className="metadata-item metadata-mode" title={PREVIEW_MODE_LABELS[previewMode].description}>
                <span className="material-icons">{PREVIEW_MODE_LABELS[previewMode].icon}</span>
                <span>{PREVIEW_MODE_LABELS[previewMode].label}</span>
              </div>
              {isChannelPreview ? (
                <>
                  {channel!.channel_number && (
                    <div className="metadata-item">
                      <span className="material-icons">tag</span>
                      <span>Channel {channel!.channel_number}</span>
                    </div>
                  )}
                  {channel!.tvg_id && (
                    <div className="metadata-item">
                      <span className="material-icons">tv</span>
                      <span>TVG-ID: {channel!.tvg_id}</span>
                    </div>
                  )}
                  <div className="metadata-item">
                    <span className="material-icons">stream</span>
                    <span>{channel!.streams.length} stream{channel!.streams.length !== 1 ? 's' : ''}</span>
                  </div>
                </>
              ) : (
                <>
                  {stream!.channel_group_name && (
                    <div className="metadata-item">
                      <span className="material-icons">folder</span>
                      <span>{stream!.channel_group_name}</span>
                    </div>
                  )}
                  {stream!.tvg_id && (
                    <div className="metadata-item">
                      <span className="material-icons">tv</span>
                      <span>TVG-ID: {stream!.tvg_id}</span>
                    </div>
                  )}
                  {providerName && (
                    <div className="metadata-item">
                      <span className="material-icons">dns</span>
                      <span>{providerName}</span>
                    </div>
                  )}
                </>
              )}
            </div>

            {/* Error details */}
            {playerState === 'error' && playerError && (
              <div className="preview-stream-error">
                <span className="material-icons">warning</span>
                <div>
                  <strong>Playback Error</strong>
                  <p>{playerError.message}</p>
                  {playerError.details && <p className="error-details">{playerError.details}</p>}
                </div>
              </div>
            )}

            {/* Fallback Options - only show for streams with direct URLs */}
            {displayInfo.externalUrl && (
              <div className={`preview-stream-fallback ${playerError?.code === 'UNSUPPORTED_CODEC' ? 'fallback-highlighted' : ''}`}>
                <h4>{playerError?.code === 'UNSUPPORTED_CODEC' ? 'Use External Player' : 'Alternative Options'}</h4>
                <div className="fallback-buttons">
                  <button
                    type="button"
                    className={`modal-btn ${playerError?.code === 'UNSUPPORTED_CODEC' ? 'modal-btn-primary' : 'modal-btn-secondary'}`}
                    onClick={handleOpenInVLC}
                    title="Open stream in VLC media player"
                  >
                    <span className="material-icons">play_arrow</span>
                    Open in VLC
                  </button>
                  <button
                    type="button"
                    className={`modal-btn ${playerError?.code === 'UNSUPPORTED_CODEC' ? 'modal-btn-primary' : 'modal-btn-secondary'}`}
                    onClick={handleDownloadM3U}
                    title="Download M3U playlist file"
                  >
                    <span className="material-icons">download</span>
                    Download M3U
                  </button>
                  <button
                    type="button"
                    className="modal-btn modal-btn-secondary"
                    onClick={handleCopyUrl}
                    title="Copy stream URL to clipboard"
                  >
                    <span className="material-icons">
                      {urlCopied ? 'check' : 'content_copy'}
                    </span>
                    {urlCopied ? 'Copied!' : 'Copy URL'}
                  </button>
                </div>
              </div>
            )}

            {/* Info for channel preview - no direct URL available */}
            {isChannelPreview && (
              <div className="preview-stream-fallback">
                <h4>Channel Output</h4>
                <p className="channel-preview-note">
                  This preview shows the channel output as it would appear to clients.
                  The stream is served through Dispatcharr's proxy.
                </p>
              </div>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="modal-footer">
          <button
            type="button"
            className="modal-btn modal-btn-secondary"
            onClick={onClose}
          >
            Close
          </button>
        </div>
      </div>
    </ModalOverlay>
  );
});
