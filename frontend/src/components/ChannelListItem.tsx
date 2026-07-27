import { memo, useState, useRef, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { useSortable } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import type { Channel } from '../types';
import { openInVLC } from '../utils/vlc';
import { CatchupBadge } from './CatchupBadge';

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
  channelUrl?: string;
  showStreamUrls?: boolean;
  onProbeChannel?: () => void;
  isProbing?: boolean;
  hasFailedStreams?: boolean;
  hasBlackScreenStreams?: boolean;
  hasLowFpsStreams?: boolean;
  /**
   * bead enhancedchannelmanager-po78p / GH #696 — true when one or more of
   * the channel's assigned streams are flagged `is_stale` by Dispatcharr
   * (its own M3U refresh no longer re-matched the stream in the source
   * playlist). Precedence: rendered after has-failed, before
   * black-screen/low-fps — a stale-but-otherwise-healthy stream is a softer
   * signal than a probe failure but still worth surfacing over cosmetic
   * quality indicators.
   */
  hasStaleStreams?: boolean;
  /**
   * Count of the channel's assigned streams flagged stale, for the specific
   * tooltip text ("2 streams no longer listed by provider (stale)"). Purely
   * cosmetic — `hasStaleStreams` alone gates the icon/row styling.
   */
  staleStreamCount?: number;
  onPreviewChannel?: () => void;
  /**
   * bd-eio04.13 — proposed normalized name if the current channel name
   * would change under the active normalization rules. Undefined if
   * the row is already normalized or the preview has not loaded yet.
   */
  proposedNormalizedName?: string;
  /** Click handler for the would-normalize indicator. */
  onShowNormalizePreview?: () => void;
  /**
   * Applied TVG ID shown beneath the channel name — the channel's own
   * tvg_id, falling back to the linked EPG record's tvg_id (resolved by
   * the parent). Null/undefined hides it.
   */
  tvgId?: string | null;
  /** Name of the linked EPG record, shown alongside the TVG ID. */
  tvgName?: string | null;
  /** Name of the EPG source the linked EPG record belongs to. */
  epgSourceName?: string | null;
  /**
   * Distinct resolution capability tiers among the channel's probed streams,
   * ordered highest-first (e.g. ['4K', 'FHD', 'HD']). Rendered as pills on
   * line 2. Empty/undefined renders no pills.
   */
  capabilities?: string[];
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
    el.querySelector<HTMLButtonElement>('[role="menuitem"]:not(:disabled)')?.focus();
  }, [menuOpen, menuPosition]);

  const closeMenu = (returnFocus = false) => {
    setMenuOpen(false);
    if (returnFocus) btnRef.current?.focus();
  };

  const runAction = (action: () => void, returnFocus: boolean) => {
    setMenuOpen(false);
    action();
    if (returnFocus) btnRef.current?.focus();
  };

  const handleMenuKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const items = [...(dropdownRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not(:disabled)') ?? [])];
    const current = items.indexOf(document.activeElement as HTMLButtonElement);
    if (event.key === 'Escape') {
      event.preventDefault();
      closeMenu(true);
    } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      const delta = event.key === 'ArrowDown' ? 1 : -1;
      items[(current + delta + items.length) % items.length]?.focus();
    } else if (event.key === 'Home') {
      event.preventDefault();
      items[0]?.focus();
    } else if (event.key === 'End') {
      event.preventDefault();
      items[items.length - 1]?.focus();
    }
  };

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
            closeMenu();
          } else {
            const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
            setMenuPosition({ top: rect.bottom + 2, left: rect.right });
            setMenuOpen(true);
          }
        }}
        title="Channel actions"
        aria-label="Channel actions"
        aria-haspopup="menu"
        aria-expanded={menuOpen}
      >
        <span className="material-icons" aria-hidden="true">more_vert</span>
      </button>
      {menuOpen && menuPosition && createPortal(
        <div
          className="channel-menu-dropdown"
          role="menu"
          aria-label="Channel actions"
          ref={dropdownRef}
          style={{ top: menuPosition.top, left: menuPosition.left }}
          onClick={(e) => e.stopPropagation()}
          onKeyDown={handleMenuKeyDown}
        >
          {onProbeChannel && hasStreams && (
            <button
              className={`channel-menu-item ${isProbing ? 'loading' : ''}`}
              role="menuitem"
              title={isProbing ? 'Probing channel streams' : 'Probe channel streams'}
              onClick={() => runAction(onProbeChannel, true)}
              disabled={isProbing}
            >
              <span className={`material-icons ${isProbing ? 'spinning' : ''}`} aria-hidden="true">
                {isProbing ? 'sync' : 'speed'}
              </span>
              <span>{isProbing ? 'Probing...' : 'Probe Channel'}</span>
            </button>
          )}
          {onPreviewChannel && hasStreams && (
            <button
              className="channel-menu-item"
              role="menuitem"
              title="Preview channel"
              onClick={() => runAction(onPreviewChannel, false)}
            >
              <span className="material-icons" aria-hidden="true">visibility</span>
              <span>Preview</span>
            </button>
          )}
          {channelUrl && (
            <button
              className="channel-menu-item"
              role="menuitem"
              title="Open channel in VLC"
              onClick={() => runAction(() => openInVLC(channelUrl, channel.name), true)}
            >
              <span className="material-icons" aria-hidden="true">play_circle</span>
              <span>Open in VLC</span>
            </button>
          )}
          {onCopyChannelUrl && (
            <button
              className="channel-menu-item"
              role="menuitem"
              title="Copy channel URL"
              onClick={() => runAction(onCopyChannelUrl, true)}
            >
              <span className="material-icons" aria-hidden="true">content_copy</span>
              <span>Copy URL</span>
            </button>
          )}
          {isEditMode && (
            <>
              <div className="channel-menu-divider" />
              <button
                className="channel-menu-item"
                role="menuitem"
                title="Edit channel"
                onClick={() => runAction(onEditChannel, false)}
              >
                <span className="material-icons" aria-hidden="true">edit</span>
                <span>Edit Channel</span>
              </button>
              <button
                className="channel-menu-item danger"
                role="menuitem"
                title="Delete channel"
                onClick={() => runAction(onDelete, false)}
              >
                <span className="material-icons" aria-hidden="true">delete</span>
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
  channelUrl,
  showStreamUrls = true,
  onProbeChannel,
  isProbing = false,
  hasFailedStreams = false,
  hasBlackScreenStreams = false,
  hasLowFpsStreams = false,
  hasStaleStreams = false,
  staleStreamCount = 0,
  onPreviewChannel,
  proposedNormalizedName,
  onShowNormalizePreview,
  tvgId,
  tvgName,
  epgSourceName,
  capabilities = [],
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

  const showTvgInfo = Boolean(tvgName && !isEditingName);
  const hasCapabilities = capabilities.length > 0;
  const showLine2 = showTvgInfo || hasCapabilities;

  const streamCount = channel.streams.length;
  const streamCountText = `${streamCount} stream${streamCount !== 1 ? 's' : ''}`;
  const health = streamCount === 0
    ? { key: 'no-streams', icon: 'warning', label: '0 streams; no streams assigned', detail: '0 streams; no streams assigned' }
    : hasFailedStreams
      ? { key: 'failed', icon: 'error', label: `${streamCountText}; failed probe`, detail: 'One or more streams failed probe' }
      : hasStaleStreams
        ? {
            key: 'stale',
            icon: 'history',
            label: `${streamCountText}; stale`,
            detail: `${staleStreamCount > 0 ? staleStreamCount : 'One or more'} stream${staleStreamCount === 1 ? '' : 's'} no longer listed by provider (stale)`,
          }
        : hasBlackScreenStreams
          ? { key: 'black-screen', icon: 'videocam_off', label: `${streamCountText}; black screen`, detail: 'One or more streams detected as black screen' }
          : hasLowFpsStreams
            ? { key: 'low-fps', icon: 'slow_motion_video', label: `${streamCountText}; low FPS`, detail: 'One or more streams have low FPS' }
            : { key: 'healthy', icon: 'lan', label: `${streamCountText}; healthy`, detail: `${streamCountText}; healthy` };
  const healthIconClass = health.key === 'no-streams'
    ? 'warning-icon'
    : health.key === 'healthy'
      ? 'streams-count-icon'
      : health.key === 'failed'
        ? 'failed-stream-icon'
        : health.key === 'stale'
          ? 'stale-stream-icon'
          : `${health.key}-icon`;

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`channel-item ${isSelected && isEditMode ? 'selected' : ''} ${isMultiSelected ? 'multi-selected' : ''} ${isDragOver ? 'drag-over' : ''} ${isDragging ? 'dragging' : ''} ${isModified ? 'channel-modified' : ''} ${channel.streams.length === 0 ? 'no-streams' : ''} ${hasStaleStreams ? 'has-stale-streams' : ''}`}
      onClick={onClick}
      onDragOver={onStreamDragOver}
      onDragLeave={onStreamDragLeave}
      onDrop={onStreamDrop}
    >
      {isEditMode && (
        /* Semantic, keyboard-operable selector (bead enhancedchannelmanager-
           s8xpd, mirroring StreamsPane's stream-item selector from bead
           zwhw4): a real <button> is natively focusable and Space/Enter fire
           click; role="checkbox" + aria-checked announce the actual
           selection state. */
        <button
          type="button"
          role="checkbox"
          aria-checked={isMultiSelected}
          aria-label={`Select channel ${channel.name}`}
          className={`channel-select-indicator ${isMultiSelected ? 'selected' : ''}`}
          onClick={(e) => {
            e.stopPropagation();
            e.preventDefault();
            onToggleSelect(e);
          }}
          onPointerDown={(e) => e.stopPropagation()}
          onMouseDown={(e) => e.stopPropagation()}
          onTouchStart={(e) => e.stopPropagation()}
          draggable={false}
        >
          <span className="material-icons" aria-hidden="true">
            {isMultiSelected ? 'check_box' : 'check_box_outline_blank'}
          </span>
        </button>
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
      <div className="channel-number-col">
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
      </div>
      <div className="channel-content">
        <div className="channel-line1">
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
          {/* Catch-up (timeshift) support — bead enhancedchannelmanager-sy1sz.
              Renders only when Dispatcharr flags the channel is_catchup. */}
          <CatchupBadge isCatchup={channel.is_catchup} catchupDays={channel.catchup_days} />
          {proposedNormalizedName && !isEditingName && (
            <button
              type="button"
              className="channel-normalize-indicator"
              aria-label={`Channel name would normalize to "${proposedNormalizedName}". Click to preview.`}
              title={`This name would be normalized to "${proposedNormalizedName}". Click to preview.`}
              onClick={(e) => {
                e.stopPropagation();
                onShowNormalizePreview?.();
              }}
              data-testid={`channel-normalize-indicator-${channel.id}`}
              data-channel-id={channel.id}
            >
              <span className="material-icons" aria-hidden="true">auto_fix_high</span>
            </button>
          )}
          {showStreamUrls && channelUrl && (
            <span
              className="channel-url"
              title="Click to copy channel URL"
              onClick={(e) => {
                e.stopPropagation();
                onCopyChannelUrl?.();
              }}
            >
              {channelUrl}
            </span>
          )}
        </div>
        {showLine2 && (
          <div className="channel-line2">
            {showTvgInfo && (
              <span
                className="channel-tvg-info"
                title={[epgSourceName && `EPG: ${epgSourceName}`, tvgId && `TVG ID: ${tvgId}`, tvgName && `TVG Name: ${tvgName}`].filter(Boolean).join(' · ')}
                data-testid={`channel-tvg-info-${channel.id}`}
              >
                {epgSourceName ? `${epgSourceName} – ${tvgName}` : tvgName}
              </span>
            )}
            {hasCapabilities && (
              <span className="channel-capabilities" data-testid={`channel-capabilities-${channel.id}`}>
                {capabilities.map((cap) => (
                  <span key={cap} className={`capability-pill cap-${cap.toLowerCase()}`}>
                    {cap}
                  </span>
                ))}
              </span>
            )}
          </div>
        )}
      </div>
      <span
        className={`channel-streams-count ${health.key === 'no-streams' ? 'no-streams' : ''} ${health.key !== 'healthy' && health.key !== 'no-streams' ? `has-${health.key}` : ''}`}
        title={health.detail}
        aria-label={health.label}
      >
        <span
          className={`material-icons ${healthIconClass}`}
          title={health.detail}
          aria-hidden="true"
        >{health.icon}</span>
        {channel.streams.length}
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
