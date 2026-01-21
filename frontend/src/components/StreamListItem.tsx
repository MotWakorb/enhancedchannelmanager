import { memo } from 'react';
import { useSortable } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import type { Stream, StreamStats } from '../types';
import { openInVLC } from '../utils/vlc';

export interface StreamListItemProps {
  stream: Stream;
  providerName: string | null;
  isEditMode: boolean;
  onRemove: (streamId: number) => void;
  onCopyUrl?: () => void;
  onClearStats?: (streamId: number) => void;
  showStreamUrls?: boolean;
  streamStats?: StreamStats | null;
}

export const StreamListItem = memo(function StreamListItem({
  stream,
  providerName,
  isEditMode,
  onRemove,
  onCopyUrl,
  onClearStats,
  showStreamUrls = true,
  streamStats
}: StreamListItemProps) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: stream.id, disabled: !isEditMode });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  };

  // Format audio channels for display
  const formatAudioChannels = (channels: number | null) => {
    if (!channels) return null;
    if (channels === 2) return 'Stereo';
    if (channels === 6) return '5.1';
    if (channels === 8) return '7.1';
    return `${channels}ch`;
  };

  // Format resolution for compact display
  const formatResolution = (resolution: string | null) => {
    if (!resolution) return null;
    // Convert "1920x1080" to "1080p", "1280x720" to "720p", etc.
    const match = resolution.match(/(\d+)x(\d+)/);
    if (match) {
      const height = parseInt(match[2], 10);
      if (height >= 2160) return '4K';
      if (height >= 1080) return '1080p';
      if (height >= 720) return '720p';
      if (height >= 480) return '480p';
      return `${height}p`;
    }
    return resolution;
  };

  // Format bitrate for display
  const formatBitrate = (bitrate: number | null) => {
    if (!bitrate) return null;
    const mbps = bitrate / 1000000;
    if (mbps >= 1) {
      return `${mbps.toFixed(1)} Mbps`;
    }
    const kbps = bitrate / 1000;
    return `${kbps.toFixed(0)} kbps`;
  };

  return (
    <div ref={setNodeRef} style={style} className="inline-stream-item">
      <span
        className={`stream-drag-handle ${!isEditMode ? 'disabled' : ''}`}
        {...(isEditMode ? { ...attributes, ...listeners } : {})}
        title={isEditMode ? 'Drag to reorder' : 'Enter Edit Mode to reorder streams'}
      >
        ⋮⋮
      </span>
      {stream.logo_url && (
        <img
          src={stream.logo_url}
          alt=""
          className="stream-logo-small"
          onError={(e) => {
            (e.target as HTMLImageElement).style.display = 'none';
          }}
        />
      )}
      <div className="inline-stream-info">
        <span className="inline-stream-name">{stream.name}</span>
        {/* Stream metadata tags */}
        {streamStats && streamStats.probe_status === 'success' && (
          <span className="stream-metadata">
            {streamStats.resolution && (
              <span className="meta-tag resolution" title={streamStats.resolution}>
                {formatResolution(streamStats.resolution)}
              </span>
            )}
            {streamStats.fps && (
              <span className="meta-tag fps" title={`${streamStats.fps} fps`}>
                {parseFloat(streamStats.fps).toFixed(0)}fps
              </span>
            )}
            {streamStats.video_bitrate && (
              <span className="meta-tag bitrate" title={`Video bitrate: ${formatBitrate(streamStats.video_bitrate)}`}>
                {formatBitrate(streamStats.video_bitrate)}
              </span>
            )}
            {streamStats.video_codec && (
              <span className="meta-tag codec" title={`Video: ${streamStats.video_codec}`}>
                {streamStats.video_codec}
              </span>
            )}
            {streamStats.audio_codec && (
              <span className="meta-tag audio-codec" title={`Audio codec: ${streamStats.audio_codec.toUpperCase()}`}>
                {streamStats.audio_codec.toUpperCase()}
              </span>
            )}
            {streamStats.audio_channels && (
              <span className="meta-tag audio" title={`${streamStats.audio_channels} channels`}>
                {formatAudioChannels(streamStats.audio_channels)}
              </span>
            )}
          </span>
        )}
        {/* Probe status indicator for failed/timeout */}
        {streamStats && (streamStats.probe_status === 'failed' || streamStats.probe_status === 'timeout') && (
          <span
            className={`meta-tag probe-${streamStats.probe_status}`}
            title={streamStats.error_message || `Probe ${streamStats.probe_status}`}
          >
            <span className="material-icons">error_outline</span>
          </span>
        )}
        {showStreamUrls && stream.url && (
          <span className="inline-stream-url" title={stream.url}>
            {stream.url}
          </span>
        )}
        {providerName && <span className="inline-stream-provider">{providerName}</span>}
      </div>
      {stream.url && (
        <button
          className="vlc-btn"
          onClick={(e) => {
            e.stopPropagation();
            openInVLC(stream.url!, stream.name);
          }}
          title="Open in VLC"
        >
          <span className="material-icons">play_circle</span>
        </button>
      )}
      {onClearStats && streamStats && (streamStats.probe_status === 'failed' || streamStats.probe_status === 'timeout') && (
        <button
          className="clear-stats-btn"
          onClick={(e) => {
            e.stopPropagation();
            onClearStats(stream.id);
          }}
          title="Clear probe stats"
        >
          <span className="material-icons">clear</span>
        </button>
      )}
      {onCopyUrl && (
        <button
          className="copy-url-btn"
          onClick={(e) => {
            e.stopPropagation();
            onCopyUrl();
          }}
          title="Copy stream URL"
        >
          <span className="material-icons">content_copy</span>
        </button>
      )}
      {isEditMode && (
        <button
          className="remove-stream-btn"
          onClick={(e) => {
            e.stopPropagation();
            onRemove(stream.id);
          }}
          title="Remove stream"
        >
          ✕
        </button>
      )}
    </div>
  );
});
