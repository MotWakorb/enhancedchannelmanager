import { memo } from 'react';
import { useSortable } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import type { Channel } from '../types';
import { openInVLC } from '../utils/vlc';

export interface ChannelListItemProps {
  channel: Channel;
  isSelected: boolean;
  isMultiSelected: boolean;
  isExpanded: boolean;
  isDragOver: boolean;
  isEditingNumber: boolean;
  isEditingName: boolean;
  isModified: boolean;
  isEditMode: boolean;
  editingNumber: string;
  editingName: string;
  logoUrl: string | null;
  multiSelectCount: number;
  onEditingNumberChange: (value: string) => void;
  onEditingNameChange: (value: string) => void;
  onStartEditNumber: (e: React.MouseEvent) => void;
  onStartEditName: (e: React.MouseEvent) => void;
  onSaveNumber: () => void;
  onSaveName: () => void;
  onCancelEditNumber: () => void;
  onCancelEditName: () => void;
  onClick: (e: React.MouseEvent) => void;
  onToggleExpand: () => void;
  onToggleSelect: (e: React.MouseEvent) => void;
  onStreamDragOver: (e: React.DragEvent) => void;
  onStreamDragLeave: () => void;
  onStreamDrop: (e: React.DragEvent) => void;
  onDelete: () => void;
  onEditChannel: () => void;
  onCopyChannelUrl?: () => void;
  onContextMenu?: (e: React.MouseEvent) => void;
  channelUrl?: string;
  showStreamUrls?: boolean;
  onProbeChannel?: () => void;
  isProbing?: boolean;
  hasFailedStreams?: boolean;
}

export const ChannelListItem = memo(function ChannelListItem({
  channel,
  isSelected,
  isMultiSelected,
  isExpanded,
  isDragOver,
  isEditingNumber,
  isEditingName,
  isModified,
  isEditMode,
  editingNumber,
  editingName,
  logoUrl,
  multiSelectCount,
  onEditingNumberChange,
  onEditingNameChange,
  onStartEditNumber,
  onStartEditName,
  onSaveNumber,
  onSaveName,
  onCancelEditNumber,
  onCancelEditName,
  onClick,
  onToggleExpand,
  onToggleSelect,
  onStreamDragOver,
  onStreamDragLeave,
  onStreamDrop,
  onDelete,
  onEditChannel,
  onCopyChannelUrl,
  onContextMenu,
  channelUrl,
  showStreamUrls = true,
  onProbeChannel,
  isProbing = false,
  hasFailedStreams = false,
}: ChannelListItemProps) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: channel.id, disabled: !isEditMode });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  };

  const handleNumberKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      onSaveNumber();
    } else if (e.key === 'Escape') {
      onCancelEditNumber();
    }
  };

  const handleNameKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      onSaveName();
    } else if (e.key === 'Escape') {
      onCancelEditName();
    }
  };

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`channel-item ${isSelected && isEditMode ? 'selected' : ''} ${isMultiSelected ? 'multi-selected' : ''} ${isDragOver ? 'drag-over' : ''} ${isDragging ? 'dragging' : ''} ${isModified ? 'channel-modified' : ''} ${channel.streams.length === 0 ? 'no-streams' : ''}`}
      onClick={onClick}
      onContextMenu={onContextMenu}
      onDragOver={onStreamDragOver}
      onDragLeave={onStreamDragLeave}
      onDrop={onStreamDrop}
    >
      {isEditMode && (
        <span
          className={`channel-select-indicator ${isMultiSelected ? 'selected' : ''}`}
          onClick={(e) => {
            e.stopPropagation();
            e.preventDefault();
            onToggleSelect(e);
          }}
          onPointerDown={(e) => e.stopPropagation()}
          onMouseDown={(e) => e.stopPropagation()}
          onTouchStart={(e) => e.stopPropagation()}
          title="Click to select/deselect"
        >
          {isMultiSelected ? (
            <span className="material-icons">check_box</span>
          ) : (
            <span className="material-icons">check_box_outline_blank</span>
          )}
        </span>
      )}
      <span
        className={`channel-drag-handle ${!isEditMode ? 'disabled' : ''}`}
        {...(isEditMode ? { ...attributes, ...listeners } : {})}
        title={isEditMode ? (multiSelectCount > 1 && isMultiSelected ? `Drag ${multiSelectCount} channels` : 'Drag to reorder') : 'Enter Edit Mode to reorder channels'}
      >
        ⋮⋮
      </span>
      <span
        className="channel-expand-icon"
        onClick={(e) => {
          e.stopPropagation();
          onToggleExpand();
        }}
        title="Click to expand/collapse"
      >
        {isExpanded ? '▼︎' : '▶︎'}
      </span>
      <div
        className="channel-logo-container"
      >
        {logoUrl ? (
          <img
            src={logoUrl}
            alt=""
            className="channel-logo"
            onError={(e) => {
              (e.target as HTMLImageElement).style.display = 'none';
            }}
          />
        ) : (
          <div className="channel-logo-placeholder">
            <span className="material-icons">image</span>
          </div>
        )}
      </div>
      {isEditingNumber ? (
        <input
          type="text"
          className="channel-number-input"
          value={editingNumber}
          onChange={(e) => onEditingNumberChange(e.target.value)}
          onKeyDown={handleNumberKeyDown}
          onBlur={onSaveNumber}
          onClick={(e) => e.stopPropagation()}
          autoFocus
        />
      ) : (
        <span
          className={`channel-number ${isEditMode ? 'editable' : ''}`}
          onDoubleClick={onStartEditNumber}
          title={isEditMode ? 'Double-click to edit' : 'Enter Edit Mode to change channel number'}
        >
          {channel.channel_number ?? '-'}
        </span>
      )}
      {isEditingName ? (
        <input
          type="text"
          className="channel-name-input"
          value={editingName}
          onChange={(e) => onEditingNameChange(e.target.value)}
          onKeyDown={handleNameKeyDown}
          onBlur={onSaveName}
          onClick={(e) => e.stopPropagation()}
          autoFocus
        />
      ) : (
        <span
          className={`channel-name ${isEditMode ? 'editable' : ''}`}
          onDoubleClick={onStartEditName}
          title={isEditMode ? 'Double-click to edit name' : 'Enter Edit Mode to change channel name'}
        >
          {channel.name}
        </span>
      )}
      {showStreamUrls && channelUrl && (
        <span className="channel-url" title={channelUrl}>
          {channelUrl}
        </span>
      )}
      <span className={`channel-streams-count ${channel.streams.length === 0 ? 'no-streams' : ''} ${hasFailedStreams ? 'has-failed' : ''}`}>
        {channel.streams.length === 0 && <span className="material-icons warning-icon">warning</span>}
        {hasFailedStreams && channel.streams.length > 0 && (
          <span className="material-icons failed-stream-icon" title="One or more streams failed probe">error</span>
        )}
        {channel.streams.length} stream{channel.streams.length !== 1 ? 's' : ''}
      </span>
      {/* Probe channel button - probes all streams in this channel */}
      {onProbeChannel && channel.streams && channel.streams.length > 0 && (
        <button
          className={`probe-channel-btn ${isProbing ? 'probing' : ''}`}
          onClick={(e) => {
            e.stopPropagation();
            onProbeChannel();
          }}
          disabled={isProbing}
          title={isProbing ? 'Probing streams...' : 'Probe all streams in this channel'}
        >
          <span className={`material-icons ${isProbing ? 'spinning' : ''}`}>
            {isProbing ? 'sync' : 'speed'}
          </span>
        </button>
      )}
      {channelUrl && (
        <button
          className="vlc-btn channel-vlc-btn"
          onClick={(e) => {
            e.stopPropagation();
            openInVLC(channelUrl, channel.name);
          }}
          title="Open channel in VLC"
        >
          <span className="material-icons">play_circle</span>
        </button>
      )}
      {onCopyChannelUrl && (
        <button
          className="copy-url-btn channel-copy-url-btn"
          onClick={(e) => {
            e.stopPropagation();
            onCopyChannelUrl();
          }}
          title="Copy channel stream URL"
        >
          <span className="material-icons">content_copy</span>
        </button>
      )}
      {isEditMode && (
        <>
          <button
            className="channel-row-edit-btn"
            onClick={(e) => {
              e.stopPropagation();
              onEditChannel();
            }}
            title="Edit channel"
          >
            <span className="material-icons">edit</span>
          </button>
          <button
            className="channel-row-delete-btn"
            onClick={(e) => {
              e.stopPropagation();
              onDelete();
            }}
            title="Delete channel"
          >
            <span className="material-icons">delete</span>
          </button>
        </>
      )}
    </div>
  );
});
