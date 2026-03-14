import { memo, useState, useRef, useEffect } from 'react';
import { createPortal } from 'react-dom';
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
  hasBlackScreenStreams?: boolean;
  onPreviewChannel?: () => void;
}

interface ChannelMenuProps {
  channel: Channel;
  isEditMode: boolean;
  isProbing: boolean;
  channelUrl?: string;
  onProbeChannel?: () => void;
  onPreviewChannel?: () => void;
  onCopyChannelUrl?: () => void;
  onEditChannel: () => void;
  onDelete: () => void;
}

const ChannelMenu = memo(function ChannelMenu({
  channel,
  isEditMode,
  isProbing,
  channelUrl,
  onProbeChannel,
  onPreviewChannel,
  onCopyChannelUrl,
  onEditChannel,
  onDelete,
}: ChannelMenuProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuPosition, setMenuPosition] = useState<{ top: number; left: number } | null>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    const handleClickOutside = (e: MouseEvent) => {
      const target = e.target as Node;
      if (
        btnRef.current && !btnRef.current.contains(target) &&
        dropdownRef.current && !dropdownRef.current.contains(target)
      ) {
        setMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [menuOpen]);

  // Flip menu upward if it would overflow the viewport bottom
  useEffect(() => {
    if (!menuOpen || !dropdownRef.current || !menuPosition) return;
    const el = dropdownRef.current;
    const rect = el.getBoundingClientRect();
    const viewportHeight = window.innerHeight;
    if (rect.bottom > viewportHeight) {
      // Position above the button instead of below
      el.style.top = `${Math.max(0, menuPosition.top - rect.height - (btnRef.current?.getBoundingClientRect().height ?? 0) - 4)}px`;
    }
  }, [menuOpen, menuPosition]);

  const hasStreams = channel.streams && channel.streams.length > 0;
  const hasAnyItem = hasStreams || channelUrl || isEditMode;
  if (!hasAnyItem) return null;

  return (
    <>
      <button
        className="channel-menu-btn"
        ref={btnRef}
        onClick={(e) => {
          e.stopPropagation();
          if (menuOpen) {
            setMenuOpen(false);
          } else {
            const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
            setMenuPosition({ top: rect.bottom + 2, left: rect.right });
            setMenuOpen(true);
          }
        }}
        title="Channel actions"
      >
        <span className="material-icons">more_vert</span>
      </button>
      {menuOpen && menuPosition && createPortal(
        <div
          className="channel-menu-dropdown"
          ref={dropdownRef}
          style={{ top: menuPosition.top, left: menuPosition.left }}
          onClick={(e) => e.stopPropagation()}
        >
          {onProbeChannel && hasStreams && (
            <button
              className={`channel-menu-item ${isProbing ? 'loading' : ''}`}
              onClick={() => { setMenuOpen(false); onProbeChannel(); }}
              disabled={isProbing}
            >
              <span className={`material-icons ${isProbing ? 'spinning' : ''}`}>
                {isProbing ? 'sync' : 'speed'}
              </span>
              <span>{isProbing ? 'Probing...' : 'Probe Channel'}</span>
            </button>
          )}
          {onPreviewChannel && hasStreams && (
            <button
              className="channel-menu-item"
              onClick={() => { setMenuOpen(false); onPreviewChannel(); }}
            >
              <span className="material-icons">visibility</span>
              <span>Preview</span>
            </button>
          )}
          {channelUrl && (
            <button
              className="channel-menu-item"
              onClick={() => { setMenuOpen(false); openInVLC(channelUrl, channel.name); }}
            >
              <span className="material-icons">play_circle</span>
              <span>Open in VLC</span>
            </button>
          )}
          {onCopyChannelUrl && (
            <button
              className="channel-menu-item"
              onClick={() => { setMenuOpen(false); onCopyChannelUrl(); }}
            >
              <span className="material-icons">content_copy</span>
              <span>Copy URL</span>
            </button>
          )}
          {isEditMode && (
            <>
              <div className="channel-menu-divider" />
              <button
                className="channel-menu-item"
                onClick={() => { setMenuOpen(false); onEditChannel(); }}
              >
                <span className="material-icons">edit</span>
                <span>Edit Channel</span>
              </button>
              <button
                className="channel-menu-item danger"
                onClick={() => { setMenuOpen(false); onDelete(); }}
              >
                <span className="material-icons">delete</span>
                <span>Delete Channel</span>
              </button>
            </>
          )}
        </div>,
        document.body
      )}
    </>
  );
});

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
  hasBlackScreenStreams = false,
  onPreviewChannel,
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
      <span className={`channel-streams-count ${channel.streams.length === 0 ? 'no-streams' : ''} ${hasFailedStreams ? 'has-failed' : !hasFailedStreams && hasBlackScreenStreams ? 'has-black-screen' : ''}`}>
        {channel.streams.length === 0 && <span className="material-icons warning-icon">warning</span>}
        {hasFailedStreams && channel.streams.length > 0 && (
          <span className="material-icons failed-stream-icon" title="One or more streams failed probe">error</span>
        )}
        {!hasFailedStreams && hasBlackScreenStreams && channel.streams.length > 0 && (
          <span className="material-icons black-screen-icon" title="One or more streams detected as black screen">videocam_off</span>
        )}
        {channel.streams.length} stream{channel.streams.length !== 1 ? 's' : ''}
      </span>
      <ChannelMenu
        channel={channel}
        isEditMode={isEditMode}
        isProbing={isProbing}
        channelUrl={channelUrl}
        onProbeChannel={onProbeChannel}
        onPreviewChannel={onPreviewChannel}
        onCopyChannelUrl={onCopyChannelUrl}
        onEditChannel={onEditChannel}
        onDelete={onDelete}
      />
    </div>
  );
});
