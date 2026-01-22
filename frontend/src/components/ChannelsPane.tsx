import React, { useState, useEffect, useRef, useMemo, memo, useCallback } from 'react';
import {
  DndContext,
  DragOverlay,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  DragEndEvent,
  DragOverEvent,
  DragStartEvent,
  useDroppable,
} from '@dnd-kit/core';
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import type { Channel, ChannelGroup, ChannelProfile, Stream, StreamStats, M3UAccount, M3UGroupSetting, Logo, ChangeInfo, ChangeRecord, SavePoint, EPGData, EPGSource, StreamProfile, ChannelListFilterSettings } from '../types';
import { logger } from '../utils/logger';
import { computeAutoRename } from '../utils/channelRename';
import { ChannelProfilesListModal } from './ChannelProfilesListModal';
import type { ChannelDefaults } from './StreamsPane';
import * as api from '../services/api';
import type { SortCriterion, GracenoteConflictMode } from '../services/api';
import { HistoryToolbar } from './HistoryToolbar';
import { BulkEPGAssignModal, type EPGAssignment } from './BulkEPGAssignModal';
import { BulkLCNFetchModal, type LCNAssignment } from './BulkLCNFetchModal';
import { GracenoteConflictModal, type GracenoteConflict } from './GracenoteConflictModal';
import { EditChannelModal, type ChannelMetadataChanges } from './EditChannelModal';
import { NormalizeNamesModal } from './NormalizeNamesModal';
import { naturalCompare } from '../utils/naturalSort';
import { useCopyFeedback } from '../hooks/useCopyFeedback';
import { useDropdown } from '../hooks/useDropdown';
import { useContextMenu } from '../hooks/useContextMenu';
import { useModal } from '../hooks/useModal';
import { ChannelListItem } from './ChannelListItem';
import { StreamListItem } from './StreamListItem';
import './ChannelsPane.css';

interface ChannelsPaneProps {
  channelGroups: ChannelGroup[];
  channels: Channel[];
  streams: Stream[];
  providers: M3UAccount[];
  selectedChannelId: number | null;
  onChannelSelect: (channel: Channel | null) => void;
  onChannelUpdate: (channel: Channel, changeInfo?: ChangeInfo) => void;
  onChannelDrop: (channelId: number, streamId: number) => void;
  onBulkStreamDrop: (channelId: number, streamIds: number[]) => void;
  onChannelReorder: (channelIds: number[], startingNumber: number) => void;
  onCreateChannel: (name: string, channelNumber?: number, groupId?: number, logoId?: number, tvgId?: string, logoUrl?: string, profileIds?: number[]) => Promise<Channel>;
  onDeleteChannel: (channelId: number) => Promise<void>;
  searchTerm: string;
  onSearchChange: (term: string) => void;
  selectedGroups: number[];
  onSelectedGroupsChange: (groupIds: number[]) => void;
  loading: boolean;
  autoRenameChannelNumber: boolean;
  // Edit mode props
  isEditMode?: boolean;
  modifiedChannelIds?: Set<number>;
  onStageUpdateChannel?: (channelId: number, data: Partial<Channel>, description: string) => void;
  onStageAddStream?: (channelId: number, streamId: number, description: string) => void;
  onStageRemoveStream?: (channelId: number, streamId: number, description: string) => void;
  onStageReorderStreams?: (channelId: number, streamIds: number[], description: string) => void;
  onStageBulkAssignNumbers?: (channelIds: number[], startingNumber: number, description: string) => void;
  onStageDeleteChannel?: (channelId: number, description: string) => void;
  onStageDeleteChannelGroup?: (groupId: number, description: string) => void;
  onStartBatch?: (description: string) => void;
  onEndBatch?: () => void;
  isCommitting?: boolean;
  // History toolbar props (only shown in edit mode)
  canUndo?: boolean;
  canRedo?: boolean;
  undoCount?: number;
  redoCount?: number;
  lastChange?: ChangeRecord | null;
  savePoints?: SavePoint[];
  hasUnsavedChanges?: boolean;
  isOperationPending?: boolean;
  onUndo?: () => void;
  onRedo?: () => void;
  onCreateSavePoint?: (name?: string) => void;
  onRevertToSavePoint?: (id: string) => void;
  onDeleteSavePoint?: (id: string) => void;
  // Logo props
  logos?: Logo[];
  onLogosChange?: () => void;
  // Channel group callback
  onChannelGroupsChange?: () => void;
  onDeleteChannelGroup?: (groupId: number) => Promise<void>;
  // EPG and Stream Profile props
  epgData?: EPGData[];
  epgSources?: EPGSource[];
  streamProfiles?: StreamProfile[];
  epgDataLoading?: boolean;
  // Channel Profiles props
  channelProfiles?: ChannelProfile[];
  onChannelProfilesChange?: () => Promise<void>;
  // Channel defaults from settings (naming options, default profile, etc.)
  channelDefaults?: ChannelDefaults;
  // Channel list filter props
  providerGroupSettings?: Record<number, M3UGroupSetting>;
  channelListFilters?: ChannelListFilterSettings;
  onChannelListFiltersChange?: (updates: Partial<ChannelListFilterSettings>) => void;
  newlyCreatedGroupIds?: Set<number>;
  onTrackNewlyCreatedGroup?: (groupId: number) => void;
  // Multi-select props
  selectedChannelIds?: Set<number>;
  lastSelectedChannelId?: number | null;
  onToggleChannelSelection?: (channelId: number, addToSelection: boolean) => void;
  onClearChannelSelection?: () => void;
  onSelectChannelRange?: (fromId: number, toId: number, groupChannelIds: number[]) => void;
  onSelectGroupChannels?: (channelIds: number[], select: boolean) => void;
  // Dispatcharr URL for constructing channel stream URLs
  dispatcharrUrl?: string;
  // Stream group drop callback (for bulk channel creation) - supports multiple groups
  // Now includes optional target group ID and suggested starting number for positional drops
  onStreamGroupDrop?: (groupNames: string[], streamIds: number[], targetGroupId?: number, suggestedStartingNumber?: number) => void;
  // Bulk streams drop callback (for opening bulk create modal when dropping multiple streams)
  // Includes target group ID and starting channel number for pre-filling the modal
  onBulkStreamsDrop?: (streamIds: number[], groupId: number | null, startingNumber: number) => void;
  // Callback to open create channel modal (routes to bulk create modal in manual entry mode)
  onOpenCreateChannelModal?: () => void;
  // Appearance settings
  showStreamUrls?: boolean;
  // EPG matching settings
  epgAutoMatchThreshold?: number;
  // Gracenote conflict handling
  gracenoteConflictMode?: GracenoteConflictMode;
  // External trigger to open edit modal for a specific channel
  externalChannelToEdit?: Channel | null;
  onExternalChannelEditHandled?: () => void;
}

interface GroupState {
  [groupId: number]: boolean;
}

// ChannelListItem component extracted to ChannelListItem.tsx
// StreamListItem component extracted to StreamListItem.tsx

// Reusable Sort Dropdown Button component
interface SortDropdownButtonProps {
  onSortByMode: (mode: 'smart' | 'resolution' | 'bitrate' | 'framerate') => void;
  disabled?: boolean;
  isLoading?: boolean;
  className?: string;
  showLabel?: boolean;
  labelText?: string;
  enabledCriteria?: Record<'resolution' | 'bitrate' | 'framerate', boolean>;
}

const SortDropdownButton = memo(function SortDropdownButton({
  onSortByMode,
  disabled = false,
  isLoading = false,
  className = '',
  showLabel = false,
  labelText = 'Sort',
  enabledCriteria = { resolution: true, bitrate: true, framerate: true },
}: SortDropdownButtonProps) {
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Check if any criteria are enabled (for Smart Sort to be useful)
  const anyEnabled = enabledCriteria.resolution || enabledCriteria.bitrate || enabledCriteria.framerate;

  // Close on outside click
  useEffect(() => {
    if (!isOpen) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [isOpen]);

  const handleModeClick = (mode: 'smart' | 'resolution' | 'bitrate' | 'framerate') => {
    setIsOpen(false);
    onSortByMode(mode);
  };

  return (
    <div className={`sort-dropdown-container ${className}`} ref={dropdownRef}>
      <button
        className={`sort-dropdown-btn ${isLoading ? 'loading' : ''}`}
        onClick={() => setIsOpen(!isOpen)}
        disabled={disabled || isLoading || !anyEnabled}
        title={isLoading ? 'Sorting streams...' : !anyEnabled ? 'No sort criteria enabled' : 'Sort streams'}
      >
        <span className={`material-icons ${isLoading ? 'spinning' : ''}`}>
          {isLoading ? 'sync' : 'sort'}
        </span>
        {showLabel && <span>{labelText}</span>}
        <span className="material-icons sort-dropdown-arrow">arrow_drop_down</span>
      </button>
      {isOpen && (
        <div className="sort-dropdown-menu">
          {anyEnabled && (
            <>
              <button className="sort-dropdown-item" onClick={() => handleModeClick('smart')}>
                <span className="material-icons">auto_awesome</span>
                <span>Smart Sort</span>
              </button>
              <div className="sort-dropdown-divider" />
            </>
          )}
          {enabledCriteria.resolution && (
            <button className="sort-dropdown-item" onClick={() => handleModeClick('resolution')}>
              <span className="material-icons">aspect_ratio</span>
              <span>By Resolution</span>
            </button>
          )}
          {enabledCriteria.bitrate && (
            <button className="sort-dropdown-item" onClick={() => handleModeClick('bitrate')}>
              <span className="material-icons">speed</span>
              <span>By Bitrate</span>
            </button>
          )}
          {enabledCriteria.framerate && (
            <button className="sort-dropdown-item" onClick={() => handleModeClick('framerate')}>
              <span className="material-icons">slow_motion_video</span>
              <span>By Framerate</span>
            </button>
          )}
        </div>
      )}
    </div>
  );
});

// Sortable Group Header wrapper for drag-and-drop group reordering
interface SortableGroupHeaderProps extends Omit<DroppableGroupHeaderProps, 'groupId'> {
  groupId: number | 'ungrouped';
}

const SortableGroupHeader = memo(function SortableGroupHeader(props: SortableGroupHeaderProps) {
  const { groupId, isEditMode } = props;

  // Don't make ungrouped sortable
  const isSortable = groupId !== 'ungrouped' && isEditMode;

  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({
    id: `group-${groupId}`,
    disabled: !isSortable,
  });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  };

  return (
    <div ref={setNodeRef} style={style}>
      <DroppableGroupHeader
        {...props}
        dragHandleProps={isSortable ? { ...attributes, ...listeners } : undefined}
      />
    </div>
  );
});

// Droppable Group Header component for cross-group channel dragging
interface DroppableGroupHeaderProps {
  groupId: number | 'ungrouped';
  groupName: string;
  channelCount: number;
  channelRange: { min: number | null; max: number | null } | null;
  isEmpty: boolean;
  isExpanded: boolean;
  isEditMode: boolean;
  isAutoSync: boolean;
  isManualGroup: boolean;
  selectedCount: number;
  onToggle: () => void;
  onSortAndRenumber?: () => void;
  onDeleteGroup?: () => void;
  onSelectAll?: () => void;
  onStreamDropOnGroup?: (groupId: number | 'ungrouped', streamIds: number[]) => void;
  onContextMenu?: (e: React.MouseEvent) => void;
  dragHandleProps?: any;
  onProbeGroup?: () => void;
  isProbing?: boolean;
  onSortStreamsByQuality?: () => void;
  onSortStreamsByMode?: (mode: 'smart' | 'resolution' | 'bitrate' | 'framerate') => void;
  isSortingByQuality?: boolean;
  enabledCriteria?: Record<'resolution' | 'bitrate' | 'framerate', boolean>;
  failedChannelCount?: number;
}

const DroppableGroupHeader = memo(function DroppableGroupHeader({
  groupId,
  groupName,
  channelCount,
  channelRange,
  isEmpty,
  isExpanded,
  isEditMode,
  isAutoSync,
  isManualGroup,
  selectedCount,
  onToggle,
  onSortAndRenumber,
  onDeleteGroup,
  onSelectAll,
  onStreamDropOnGroup,
  onContextMenu,
  dragHandleProps,
  onProbeGroup,
  isProbing = false,
  onSortStreamsByQuality,
  onSortStreamsByMode,
  isSortingByQuality = false,
  enabledCriteria = { resolution: true, bitrate: true, framerate: true },
  failedChannelCount = 0,
}: DroppableGroupHeaderProps) {
  const droppableId = `group-${groupId}`;
  const { isOver, setNodeRef } = useDroppable({
    id: droppableId,
    disabled: !isEditMode,
  });

  const [streamDragOver, setStreamDragOver] = useState(false);
  const [sortDropdownOpen, setSortDropdownOpen] = useState(false);
  const sortDropdownRef = useRef<HTMLDivElement>(null);

  // Close dropdown on outside click
  useEffect(() => {
    if (!sortDropdownOpen) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (sortDropdownRef.current && !sortDropdownRef.current.contains(e.target as Node)) {
        setSortDropdownOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [sortDropdownOpen]);

  const handleSortClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    onSortAndRenumber?.();
  };

  const handleDeleteClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    onDeleteGroup?.();
  };

  const handleCheckboxClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    onSelectAll?.();
  };

  const handleSortModeClick = (mode: 'smart' | 'resolution' | 'bitrate' | 'framerate') => {
    setSortDropdownOpen(false);
    if (onSortStreamsByMode) {
      onSortStreamsByMode(mode);
    } else if (onSortStreamsByQuality) {
      onSortStreamsByQuality();
    }
  };


  const handleStreamDragOver = (e: React.DragEvent) => {
    const types = e.dataTransfer.types.map(t => t.toLowerCase());
    if (types.includes('streamid')) {
      e.preventDefault();
      e.stopPropagation();
      setStreamDragOver(true);
    }
  };

  const handleStreamDragLeave = (e: React.DragEvent) => {
    e.stopPropagation();
    setStreamDragOver(false);
  };

  const handleStreamDrop = (e: React.DragEvent) => {
    e.stopPropagation();
    setStreamDragOver(false);

    e.preventDefault();

    // Check for multiple streams first (streamIds), fall back to single (streamId)
    const streamIdsJson = e.dataTransfer.getData('streamIds');
    const streamId = e.dataTransfer.getData('streamId');

    if (onStreamDropOnGroup) {
      if (streamIdsJson) {
        try {
          const streamIds = JSON.parse(streamIdsJson) as number[];
          if (streamIds.length > 0) {
            onStreamDropOnGroup(groupId, streamIds);
            return;
          }
        } catch {
          // Fall through to single stream handling
        }
      }

      // Fallback to single stream
      if (streamId) {
        onStreamDropOnGroup(groupId, [parseInt(streamId, 10)]);
      }
    }
  };

  // Determine checkbox state: all selected, some selected, or none selected
  const allSelected = channelCount > 0 && selectedCount === channelCount;
  const someSelected = selectedCount > 0 && selectedCount < channelCount;

  return (
    <div
      ref={setNodeRef}
      className={`group-header ${isOver && isEditMode ? 'drop-target' : ''} ${streamDragOver ? 'stream-drag-over' : ''}`}
      onClick={onToggle}
      onContextMenu={onContextMenu}
      onDragOver={handleStreamDragOver}
      onDragLeave={handleStreamDragLeave}
      onDrop={handleStreamDrop}
    >
      {isEditMode && !isEmpty && (
        <span
          className={`group-checkbox ${allSelected ? 'checked' : ''} ${someSelected ? 'indeterminate' : ''}`}
          onClick={handleCheckboxClick}
          title={allSelected ? 'Deselect all channels in group' : 'Select all channels in group'}
        >
          <span className="material-icons">
            {allSelected ? 'check_box' : someSelected ? 'indeterminate_check_box' : 'check_box_outline_blank'}
          </span>
        </span>
      )}
      {isEditMode && groupId !== 'ungrouped' && (
        <span
          className="group-drag-handle"
          {...dragHandleProps}
          title="Drag to reorder group"
        >
          ⋮⋮
        </span>
      )}
      <span className="group-toggle">{isExpanded ? '▼︎' : '▶︎'}</span>
      <span className="group-name">
        {groupName}
        {groupId === 'ungrouped' && (
          <span className="group-subtext"> – Channels without a specific group</span>
        )}
      </span>
      {isAutoSync && (
        <span className="group-auto-sync-badge" title="Auto-populated by channel sync">
          Auto-Sync
        </span>
      )}
      <span className="group-count">{channelCount}</span>
      {failedChannelCount > 0 && (
        <span className="group-failed-indicator" title={`${failedChannelCount} channel${failedChannelCount !== 1 ? 's' : ''} with failed streams`}>
          <span className="material-icons">error</span>
          <span className="failed-count">{failedChannelCount}</span>
        </span>
      )}
      {channelRange && channelRange.min !== null && channelRange.max !== null && (
        <span className="group-range" title="Channel number range">
          {channelRange.min === channelRange.max
            ? `#${channelRange.min}`
            : `#${channelRange.min}–${channelRange.max}`}
        </span>
      )}
      {isEmpty && <span className="group-empty-badge">Empty</span>}
      {onProbeGroup && !isEmpty && (
        <button
          className={`probe-group-btn ${isProbing ? 'probing' : ''}`}
          onClick={(e) => {
            e.stopPropagation();
            onProbeGroup();
          }}
          disabled={isProbing}
          title={isProbing ? 'Probing streams...' : 'Probe all streams in this group'}
        >
          <span className={`material-icons ${isProbing ? 'spinning' : ''}`}>
            {isProbing ? 'sync' : 'speed'}
          </span>
        </button>
      )}
      {isEditMode && !isEmpty && (onSortStreamsByQuality || onSortStreamsByMode) && (enabledCriteria.resolution || enabledCriteria.bitrate || enabledCriteria.framerate) && (
        <div className="sort-dropdown-container" ref={sortDropdownRef}>
          <button
            className={`group-sort-quality-btn ${isSortingByQuality ? 'sorting' : ''}`}
            onClick={(e) => {
              e.stopPropagation();
              setSortDropdownOpen(!sortDropdownOpen);
            }}
            disabled={isSortingByQuality}
            title={isSortingByQuality ? 'Sorting streams...' : 'Sort streams in this group'}
          >
            <span className={`material-icons ${isSortingByQuality ? 'spinning' : ''}`}>
              {isSortingByQuality ? 'sync' : 'sort'}
            </span>
            <span className="material-icons sort-dropdown-arrow">arrow_drop_down</span>
          </button>
          {sortDropdownOpen && (
            <div className="sort-dropdown-menu" onClick={(e) => e.stopPropagation()}>
              <button className="sort-dropdown-item" onClick={() => handleSortModeClick('smart')}>
                <span className="material-icons">auto_awesome</span>
                <span>Smart Sort</span>
              </button>
              <div className="sort-dropdown-divider" />
              {enabledCriteria.resolution && (
                <button className="sort-dropdown-item" onClick={() => handleSortModeClick('resolution')}>
                  <span className="material-icons">aspect_ratio</span>
                  <span>By Resolution</span>
                </button>
              )}
              {enabledCriteria.bitrate && (
                <button className="sort-dropdown-item" onClick={() => handleSortModeClick('bitrate')}>
                  <span className="material-icons">speed</span>
                  <span>By Bitrate</span>
                </button>
              )}
              {enabledCriteria.framerate && (
                <button className="sort-dropdown-item" onClick={() => handleSortModeClick('framerate')}>
                  <span className="material-icons">slow_motion_video</span>
                  <span>By Framerate</span>
                </button>
              )}
            </div>
          )}
        </div>
      )}
      {isEditMode && !isEmpty && onSortAndRenumber && (
        <button
          className="group-sort-btn"
          onClick={handleSortClick}
          title="Sort alphabetically and renumber channels"
        >
          <span className="material-icons">sort_by_alpha</span>
        </button>
      )}
      {isEditMode && isManualGroup && onDeleteGroup && (
        <button
          className="group-delete-btn"
          onClick={handleDeleteClick}
          title="Delete this group"
        >
          <span className="material-icons">delete</span>
        </button>
      )}
    </div>
  );
});

// Droppable zone at the end of a group (for dropping below the last channel)
interface DroppableGroupEndProps {
  groupId: number | 'ungrouped';
  isEditMode: boolean;
  showDropIndicator: boolean;
}

const DroppableGroupEnd = memo(function DroppableGroupEnd({
  groupId,
  isEditMode,
  showDropIndicator,
}: DroppableGroupEndProps) {
  const droppableId = `group-end-${groupId}`;
  const { isOver, setNodeRef } = useDroppable({
    id: droppableId,
    disabled: !isEditMode,
  });

  return (
    <div
      ref={setNodeRef}
      className={`group-end-dropzone ${isOver && isEditMode ? 'drop-target-active' : ''}`}
    >
      {(showDropIndicator || (isOver && isEditMode)) && (
        <div className="channel-drop-indicator">
          <div className="drop-indicator-line" />
        </div>
      )}
    </div>
  );
});

export function ChannelsPane({
  channelGroups,
  channels,
  streams: allStreams,
  providers,
  selectedChannelId,
  onChannelSelect,
  onChannelUpdate,
  onChannelDrop,
  onBulkStreamDrop,
  onChannelReorder,
  onCreateChannel,
  onDeleteChannel,
  searchTerm,
  onSearchChange,
  selectedGroups,
  onSelectedGroupsChange,
  loading,
  autoRenameChannelNumber,
  isEditMode = false,
  modifiedChannelIds,
  onStageUpdateChannel,
  onStageAddStream, // Used for stream assignment after channel creation
  onStageRemoveStream,
  onStageReorderStreams,
  onStageBulkAssignNumbers: _onStageBulkAssignNumbers, // Handled in App.tsx for channel reorder
  onStageDeleteChannel,
  onStageDeleteChannelGroup,
  onStartBatch,
  onEndBatch,
  isCommitting = false,
  // History toolbar props
  canUndo = false,
  canRedo = false,
  undoCount = 0,
  redoCount = 0,
  lastChange = null,
  savePoints = [],
  hasUnsavedChanges = false,
  isOperationPending = false,
  onUndo,
  onRedo,
  onCreateSavePoint,
  onRevertToSavePoint,
  onDeleteSavePoint,
  // Logo props
  logos = [],
  onLogosChange,
  // Channel group callback
  onChannelGroupsChange,
  onDeleteChannelGroup,
  // EPG and Stream Profile props
  epgData = [],
  epgSources = [],
  streamProfiles = [],
  epgDataLoading = false,
  // Channel Profiles props
  channelProfiles = [],
  onChannelProfilesChange,
  // Channel defaults from settings
  channelDefaults,
  // Channel list filter props
  providerGroupSettings = {},
  channelListFilters,
  onChannelListFiltersChange,
  newlyCreatedGroupIds = new Set(),
  onTrackNewlyCreatedGroup,
  // Multi-select props
  selectedChannelIds = new Set(),
  lastSelectedChannelId = null,
  onToggleChannelSelection,
  onClearChannelSelection,
  onSelectChannelRange,
  onSelectGroupChannels,
  // Dispatcharr URL
  dispatcharrUrl = '',
  // Stream group drop
  onStreamGroupDrop,
  // Bulk streams drop
  onBulkStreamsDrop,
  // Create channel modal
  onOpenCreateChannelModal,
  // Appearance settings
  showStreamUrls = true,
  // EPG matching settings
  epgAutoMatchThreshold = 80,
  // Gracenote conflict handling
  gracenoteConflictMode = 'ask',
  // External trigger to open edit modal
  externalChannelToEdit,
  onExternalChannelEditHandled,
}: ChannelsPaneProps) {
  // Suppress unused variable warnings - these are passed through but handled in parent
  void _onStageBulkAssignNumbers;
  void onLogosChange;
  // These props are no longer used directly since channel creation is routed to bulk create modal
  void onCreateChannel;
  void onStageAddStream;
  void channelProfiles;
  const [expandedGroups, setExpandedGroups] = useState<GroupState>({});
  const [groupOrder, setGroupOrder] = useState<number[]>([]); // Custom order for groups
  const [dragOverChannelId, setDragOverChannelId] = useState<number | null>(null);
  const [localChannels, setLocalChannels] = useState<Channel[]>(channels);
  const [groupFilterSearch, setGroupFilterSearch] = useState('');
  const groupFilterSearchRef = useRef<HTMLInputElement>(null);

  // Dropdown management with useDropdown hook
  const {
    isOpen: groupDropdownOpen,
    setIsOpen: setGroupDropdownOpen,
    dropdownRef,
  } = useDropdown();

  const {
    isOpen: filterSettingsOpen,
    setIsOpen: setFilterSettingsOpen,
    dropdownRef: filterSettingsRef,
  } = useDropdown();

  // Modal management with useModal hook
  const profilesModal = useModal();

  // Edit channel number state
  const [editingChannelId, setEditingChannelId] = useState<number | null>(null);
  const [editingChannelNumber, setEditingChannelNumber] = useState('');

  // Edit channel name state
  const [editingNameChannelId, setEditingNameChannelId] = useState<number | null>(null);
  const [editingChannelName, setEditingChannelName] = useState('');

  // Inline stream display state
  const [channelStreams, setChannelStreams] = useState<Stream[]>([]);
  const [streamsLoading, setStreamsLoading] = useState(false);

  // Stream stats state for displaying probe metadata
  const [streamStatsMap, setStreamStatsMap] = useState<Map<number, StreamStats>>(new Map());
  const [probingChannels, setProbingChannels] = useState<Set<number>>(new Set());
  const [probingGroups, setProbingGroups] = useState<Set<number | 'ungrouped'>>(new Set());

  // Delete channel state
  const deleteConfirmModal = useModal();
  const [channelToDelete, setChannelToDelete] = useState<Channel | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [renumberAfterDelete, setRenumberAfterDelete] = useState(true);
  const [subsequentChannels, setSubsequentChannels] = useState<Channel[]>([]);

  // Edit channel modal state
  const editChannelModal = useModal();
  const [channelToEdit, setChannelToEdit] = useState<Channel | null>(null);

  // Copy to clipboard feedback state
  const { copySuccess, copyError, handleCopy } = useCopyFeedback();

  // Stream group drop state (for bulk channel creation)
  const [streamGroupDragOver, setStreamGroupDragOver] = useState(false);
  // Track which group drop zone is being hovered (for positional drops)
  const [streamGroupDropTarget, setStreamGroupDropTarget] = useState<{ afterGroupId: number | 'ungrouped' | null } | null>(null);

  // Create channel group modal state
  const createGroupModal = useModal();
  const [newGroupName, setNewGroupName] = useState('');
  const [creatingGroup, setCreatingGroup] = useState(false);
  const [createGroupShouldMoveChannels, setCreateGroupShouldMoveChannels] = useState(false);

  // Delete group state
  const deleteGroupConfirmModal = useModal();
  const [groupToDelete, setGroupToDelete] = useState<ChannelGroup | null>(null);
  const [deletingGroup, setDeletingGroup] = useState(false);
  const [deleteGroupChannels, setDeleteGroupChannels] = useState(false);

  // Bulk delete channels state
  const bulkDeleteConfirmModal = useModal();
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const [deleteEmptyGroups, setDeleteEmptyGroups] = useState(true); // Default to true since user is deleting all channels in group

  // Bulk EPG assignment modal state
  const bulkEPGModal = useModal();

  // Bulk LCN fetch modal state
  const bulkLCNModal = useModal();

  // Gracenote conflict modal state
  const gracenoteConflictModal = useModal();
  const [gracenoteConflicts, setGracenoteConflicts] = useState<GracenoteConflict[]>([]);
  const [pendingLCNAssignments, setPendingLCNAssignments] = useState<LCNAssignment[]>([]);

  // Normalize names modal state
  const normalizeModal = useModal();

  // Context menu management
  const {
    contextMenu,
    showContextMenu,
    hideContextMenu,
  } = useContextMenu<{ channelIds: number[] }>();

  // Cross-group move modal state
  const crossGroupMoveModal = useModal();
  const [crossGroupMoveData, setCrossGroupMoveData] = useState<{
    channels: Channel[];  // Changed from single channel to array
    targetGroupId: number | null;
    targetGroupName: string;
    sourceGroupId: number | null;  // Added to track source group for renumbering
    sourceGroupName: string;
    isTargetAutoSync: boolean;
    suggestedChannelNumber: number | null;
    minChannelInGroup: number | null;
    maxChannelInGroup: number | null;
    insertAtPosition: boolean;  // true if dropped on a specific channel (not group header)
    sourceGroupHasGaps: boolean;  // true if removing channels would create gaps
    sourceGroupMinChannel: number | null;  // Min channel in source group (for renumber preview)
  } | null>(null);
  const [customStartingNumber, setCustomStartingNumber] = useState<string>('');
  const [renumberSourceGroup, setRenumberSourceGroup] = useState<boolean>(false);
  // Selected numbering option: 'keep' | 'suggested' | 'custom'
  const [selectedNumberingOption, setSelectedNumberingOption] = useState<'keep' | 'suggested' | 'custom'>('suggested');

  // Sort and Renumber modal state
  const sortRenumberModal = useModal();
  const [sortRenumberData, setSortRenumberData] = useState<{
    groupId: number | 'ungrouped';
    groupName: string;
    channels: Channel[];
    currentMinNumber: number | null;
  } | null>(null);
  const [sortRenumberStartingNumber, setSortRenumberStartingNumber] = useState<string>('');
  const [sortStripNumbers, setSortStripNumbers] = useState<boolean>(true);
  const [sortIgnoreCountry, setSortIgnoreCountry] = useState<boolean>(false);
  const [sortRenumberUpdateNames, setSortRenumberUpdateNames] = useState<boolean>(true);

  // Mass Renumber modal state
  const massRenumberModal = useModal();
  const [massRenumberStartingNumber, setMassRenumberStartingNumber] = useState<string>('');
  const [massRenumberChannels, setMassRenumberChannels] = useState<Channel[]>([]);
  const [massRenumberUpdateNames, setMassRenumberUpdateNames] = useState<boolean>(true);

  // Hidden groups state
  const hiddenGroupsModal = useModal();
  const [hiddenGroups, setHiddenGroups] = useState<{ id: number; name: string; hidden_at: string }[]>([]);

  // Group reorder modal state
  const groupReorderModal = useModal();
  const [groupReorderData, setGroupReorderData] = useState<{
    groupId: number;
    groupName: string;
    channels: Channel[];
    newPosition: number;  // Index in the group order
    suggestedStartingNumber: number | null;
    precedingGroupName: string | null;
    precedingGroupMaxChannel: number | null;
  } | null>(null);
  const [groupReorderNumberingOption, setGroupReorderNumberingOption] = useState<'keep' | 'suggested' | 'custom'>('suggested');
  const [groupReorderCustomNumber, setGroupReorderCustomNumber] = useState<string>('');

  // Drag overlay state
  const [activeDragId, setActiveDragId] = useState<number | null>(null);
  // Drop indicator state - tracks where to show the drop indicator line
  const [dropIndicator, setDropIndicator] = useState<{
    channelId: number;
    position: 'before' | 'after';
    groupId: number | 'ungrouped';
    atGroupEnd?: boolean;  // When true, indicates dropping at end of group
  } | null>(null);

  // Stream insert indicator - tracks where a stream is being dragged to create a new channel
  const [streamInsertIndicator, setStreamInsertIndicator] = useState<{
    channelId: number;  // The channel before/after which to insert
    position: 'before' | 'after';
    groupId: number | 'ungrouped';
    channelNumber: number;  // The channel number to insert at
  } | null>(null);

  // Stream reorder sensors (separate from channel reorder)
  const streamSensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
  );


  // Sync local channels with props
  // In edit mode, we sync when:
  // 1. Entering edit mode (to pick up latest channels)
  // 2. When channels prop changes AND we're not actively dragging (for undo/redo)
  // We DON'T sync during drag operations to preserve local reordering state
  useEffect(() => {
    if (!isEditMode) {
      // Not in edit mode - always sync with props
      setLocalChannels(channels);
    } else if (activeDragId === null) {
      // In edit mode and not dragging - sync for undo/redo
      setLocalChannels(channels);
    }
    // When actively dragging, don't sync to preserve drag state
  }, [channels, isEditMode, activeDragId]);

  // Clear group filter search when dropdown closes
  useEffect(() => {
    if (!groupDropdownOpen) {
      setGroupFilterSearch('');
    }
  }, [groupDropdownOpen]);

  // Handle external trigger to open edit modal from Guide tab
  useEffect(() => {
    if (externalChannelToEdit) {
      setChannelToEdit(externalChannelToEdit);
      editChannelModal.open();
      onExternalChannelEditHandled?.();
    }
  }, [externalChannelToEdit, onExternalChannelEditHandled]);

  // Create a Map for O(1) logo lookups instead of O(n) array.find()
  const logoMap = useMemo(() => {
    const map = new Map<number, Logo>();
    for (const logo of logos) {
      map.set(logo.id, logo);
    }
    return map;
  }, [logos]);

  // Load streams when a channel is selected
  useEffect(() => {
    const loadStreams = async () => {
      if (!selectedChannelId) {
        setChannelStreams([]);
        return;
      }

      const selectedChannel = channels.find((c) => c.id === selectedChannelId);
      if (!selectedChannel || selectedChannel.streams.length === 0) {
        setChannelStreams([]);
        return;
      }

      // In edit mode, use the local streams list to avoid API calls for staged changes
      if (isEditMode) {
        const orderedStreams = selectedChannel.streams
          .map((id) => allStreams.find((s) => s.id === id))
          .filter((s): s is Stream => s !== undefined);
        setChannelStreams(orderedStreams);
        return;
      }

      // Normal mode - fetch from API
      setStreamsLoading(true);
      try {
        const streamDetails = await api.getChannelStreams(selectedChannelId);
        // Sort streams to match the order in channel.streams
        const orderedStreams = selectedChannel.streams
          .map((id) => streamDetails.find((s: Stream) => s.id === id))
          .filter((s): s is Stream => s !== undefined);
        setChannelStreams(orderedStreams);
      } catch (err) {
        console.error('Failed to load streams:', err);
      } finally {
        setStreamsLoading(false);
      }
    };
    loadStreams();
  }, [selectedChannelId, channels, isEditMode, allStreams]);

  // Fetch stream stats when channelStreams changes
  useEffect(() => {
    const fetchStreamStats = async () => {
      if (channelStreams.length === 0) return;

      const streamIds = channelStreams.map((s) => s.id);
      try {
        const stats = await api.getStreamStatsByIds(streamIds);
        setStreamStatsMap((prev) => {
          const next = new Map(prev);
          for (const [idStr, stat] of Object.entries(stats)) {
            next.set(parseInt(idStr, 10), stat);
          }
          return next;
        });
      } catch (err) {
        // Stats not available is OK - they may not have been probed yet
        console.debug('Failed to fetch stream stats:', err);
      }
    };
    fetchStreamStats();
  }, [channelStreams]);

  // Pre-load stream stats for all streams assigned to channels (for failed streams indicators)
  useEffect(() => {
    const preloadAllStreamStats = async () => {
      // Collect all unique stream IDs from all channels
      const allStreamIds = new Set<number>();
      for (const channel of channels) {
        for (const streamId of channel.streams) {
          allStreamIds.add(streamId);
        }
      }

      if (allStreamIds.size === 0) return;

      try {
        const stats = await api.getStreamStatsByIds(Array.from(allStreamIds));
        setStreamStatsMap((prev) => {
          const next = new Map(prev);
          for (const [idStr, stat] of Object.entries(stats)) {
            next.set(parseInt(idStr, 10), stat);
          }
          return next;
        });
      } catch (err) {
        // Stats not available is OK - they may not have been probed yet
        console.debug('Failed to pre-load stream stats:', err);
      }
    };
    preloadAllStreamStats();
  }, [channels]);

  // Handle probe channel request - probes all streams in a channel
  const handleProbeChannel = useCallback(async (channel: Channel) => {
    // channel.streams is an array of stream IDs (numbers)
    const streamIds = channel.streams;
    console.log(`[ChannelsPane] handleProbeChannel called for channel ${channel.id} (${channel.name}) with ${streamIds.length} streams`);

    if (streamIds.length === 0) {
      console.log(`[ChannelsPane] No streams to probe for channel ${channel.id}`);
      return;
    }

    setProbingChannels((prev) => new Set(prev).add(channel.id));
    try {
      console.log(`[ChannelsPane] Calling probeBulkStreams for channel ${channel.id}`);
      const result = await api.probeBulkStreams(streamIds);
      console.log(`[ChannelsPane] probeBulkStreams succeeded for channel ${channel.id}, probed ${result.probed} streams`);

      // Update stats map with results
      if (result.results) {
        setStreamStatsMap((prev) => {
          const next = new Map(prev);
          for (const stats of result.results) {
            next.set(stats.stream_id, stats);
          }
          return next;
        });
      }
    } catch (err) {
      console.error(`[ChannelsPane] Failed to probe channel ${channel.id} streams:`, err);
    } finally {
      setProbingChannels((prev) => {
        const next = new Set(prev);
        next.delete(channel.id);
        return next;
      });
    }
  }, []);

  // Handle probe group request - probes all streams in all channels of a group
  // Uses the same backend probe logic as "Probe All Streams Now" but filtered to a single group
  const handleProbeGroup = useCallback(async (groupId: number | 'ungrouped', groupName: string) => {
    console.log(`[ChannelsPane] handleProbeGroup called for group ${groupId} (${groupName})`);

    if (groupId === 'ungrouped') {
      console.log(`[ChannelsPane] Cannot probe ungrouped channels via group probe`);
      return;
    }

    setProbingGroups((prev) => new Set(prev).add(groupId));
    try {
      // Use the same backend probe logic as Settings -> Probe All Streams Now
      // This ensures consistent filtering, logging, and channel discovery
      // Pass skipM3uRefresh=true since this is an on-demand probe from UI
      console.log(`[ChannelsPane] Calling probeAllStreams for group '${groupName}' (skipping M3U refresh)`);
      const result = await api.probeAllStreams([groupName], true);
      console.log(`[ChannelsPane] probeAllStreams started for group '${groupName}':`, result);

      // Poll for probe completion to keep the spinner active
      const pollInterval = setInterval(async () => {
        try {
          const progress = await api.getProbeProgress();
          console.log(`[ChannelsPane] Probe progress for '${groupName}':`, progress);

          if (!progress.in_progress) {
            // Probe completed - clear the spinner
            clearInterval(pollInterval);
            setProbingGroups((prev) => {
              const next = new Set(prev);
              next.delete(groupId);
              return next;
            });
            console.log(`[ChannelsPane] Probe completed for group '${groupName}'`);
          }
        } catch (err) {
          // If we can't get progress, stop polling and clear spinner
          console.error(`[ChannelsPane] Failed to get probe progress:`, err);
          clearInterval(pollInterval);
          setProbingGroups((prev) => {
            const next = new Set(prev);
            next.delete(groupId);
            return next;
          });
        }
      }, 2000); // Poll every 2 seconds

    } catch (err) {
      console.error(`[ChannelsPane] Failed to start probe for group '${groupName}':`, err);
      // Clear spinner on error
      setProbingGroups((prev) => {
        const next = new Set(prev);
        next.delete(groupId);
        return next;
      });
    }
  }, []);

  // Handle channel row click - in edit mode handles multi-select, outside edit mode expands
  const handleChannelClick = (channel: Channel, e: React.MouseEvent, groupChannelIds: number[]) => {
    // In edit mode, clicking the row handles selection (Ctrl/Shift modifiers)
    if (isEditMode) {
      const isCtrlOrCmd = e.ctrlKey || e.metaKey;
      const isShift = e.shiftKey;

      if (isShift && lastSelectedChannelId !== null && onSelectChannelRange) {
        // Shift+click: select range from last selected to current
        onSelectChannelRange(lastSelectedChannelId, channel.id, groupChannelIds);
        return;
      }

      if (isCtrlOrCmd && onToggleChannelSelection) {
        // Ctrl/Cmd+click: toggle selection
        onToggleChannelSelection(channel.id, true);
        return;
      }

      // Regular click in edit mode: just select this one channel (clear others)
      if (onToggleChannelSelection) {
        onToggleChannelSelection(channel.id, false);
      }
      return;
    }

    // Outside edit mode: toggle expand/collapse
    if (selectedChannelId === channel.id) {
      onChannelSelect(null); // Collapse if already selected
    } else {
      onChannelSelect(channel);
    }
  };

  // Handle expand icon click - toggle expand/collapse
  const handleToggleExpand = (channel: Channel) => {
    if (selectedChannelId === channel.id) {
      onChannelSelect(null); // Collapse
    } else {
      onChannelSelect(channel); // Expand
    }
  };

  // Handle checkbox click - toggle selection
  const handleToggleSelect = (channel: Channel, e: React.MouseEvent, groupChannelIds: number[]) => {
    e.stopPropagation();

    const isCtrlOrCmd = e.ctrlKey || e.metaKey;
    const isShift = e.shiftKey;

    if (isShift && lastSelectedChannelId !== null && onSelectChannelRange) {
      // Shift+click: select range
      onSelectChannelRange(lastSelectedChannelId, channel.id, groupChannelIds);
      return;
    }

    if (onToggleChannelSelection) {
      // Toggle this channel's selection (add to existing if Ctrl held, otherwise just this one)
      onToggleChannelSelection(channel.id, isCtrlOrCmd || selectedChannelIds.size > 0);
    }
  };

  // Handle context menu (right-click)
  const handleContextMenu = (channel: Channel, e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();

    // Only show context menu in edit mode when channels are selected
    if (!isEditMode || selectedChannelIds.size === 0) return;

    // If right-clicked channel isn't selected, select only it
    if (!selectedChannelIds.has(channel.id)) {
      if (onToggleChannelSelection) {
        onToggleChannelSelection(channel.id, false);
      }
      showContextMenu(e.clientX, e.clientY, { channelIds: [channel.id] });
    } else {
      // Use all selected channels
      showContextMenu(e.clientX, e.clientY, { channelIds: Array.from(selectedChannelIds) });
    }
  };

  const handleMoveToGroup = (targetGroupId: number | null) => {
    if (!contextMenu) return;

    const channelsToMove = localChannels
      .filter(ch => contextMenu.metadata.channelIds.includes(ch.id))
      .sort((a, b) => naturalCompare(a.name, b.name));
    if (channelsToMove.length === 0) return;

    const targetGroupName = targetGroupId === null
      ? 'Uncategorized'
      : channelGroups.find((g) => g.id === targetGroupId)?.name ?? 'Unknown Group';

    const sourceGroupId = channelsToMove[0].channel_group_id;
    const sourceGroupName = sourceGroupId === null
      ? 'Uncategorized'
      : channelGroups.find((g) => g.id === sourceGroupId)?.name ?? 'Unknown Group';

    // Check if target group is an auto-sync group
    const isTargetAutoSync = targetGroupId !== null && autoSyncRelatedGroups.has(targetGroupId);

    // Calculate channel number range in target group
    const targetGroupChannels = targetGroupId === null
      ? channelsByGroup.ungrouped || []
      : channelsByGroup[targetGroupId] || [];

    let minChannelInGroup: number | null = null;
    let maxChannelInGroup: number | null = null;
    let suggestedChannelNumber: number | null = null;

    if (targetGroupChannels.length > 0) {
      const channelNumbers = targetGroupChannels
        .map(ch => ch.channel_number)
        .filter((n): n is number => n !== null)
        .sort((a, b) => a - b);

      if (channelNumbers.length > 0) {
        minChannelInGroup = channelNumbers[0];
        maxChannelInGroup = channelNumbers[channelNumbers.length - 1];
        suggestedChannelNumber = maxChannelInGroup + 1;
      }
    }

    // Calculate source group info for renumbering option
    const sourceGroupChannels = sourceGroupId === null
      ? channelsByGroup.ungrouped || []
      : channelsByGroup[sourceGroupId] || [];

    const movedChannelIds = new Set(channelsToMove.map(ch => ch.id));
    const remainingSourceChannelNumbers = sourceGroupChannels
      .filter(ch => !movedChannelIds.has(ch.id))
      .map(ch => ch.channel_number)
      .filter((n): n is number => n !== null)
      .sort((a, b) => a - b);

    let sourceGroupHasGaps = false;
    let sourceGroupMinChannel: number | null = null;
    if (remainingSourceChannelNumbers.length > 1) {
      sourceGroupMinChannel = remainingSourceChannelNumbers[0];
      for (let i = 1; i < remainingSourceChannelNumbers.length; i++) {
        if (remainingSourceChannelNumbers[i] - remainingSourceChannelNumbers[i - 1] > 1) {
          sourceGroupHasGaps = true;
          break;
        }
      }
    }

    setCrossGroupMoveData({
      channels: channelsToMove,
      targetGroupId,
      targetGroupName,
      sourceGroupId,
      sourceGroupName,
      isTargetAutoSync,
      suggestedChannelNumber,
      minChannelInGroup,
      maxChannelInGroup,
      insertAtPosition: false,
      sourceGroupHasGaps,
      sourceGroupMinChannel,
    });
    crossGroupMoveModal.open();
    hideContextMenu();
  };

  const handleCreateGroupAndMove = () => {
    if (!contextMenu) return;
    hideContextMenu();
    setCreateGroupShouldMoveChannels(true);  // Flag to move channels after creating group
    createGroupModal.open();
    setNewGroupName('');
  };

  // Handle context menu on group header (when entire group is selected)
  const handleGroupContextMenu = (groupChannelIds: number[], e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();

    // Only show context menu in edit mode when channels in this group are selected
    if (!isEditMode || selectedChannelIds.size === 0) return;

    // Get the intersection of selected channels and channels in this group
    const selectedInGroup = groupChannelIds.filter(id => selectedChannelIds.has(id));

    if (selectedInGroup.length === 0) return;

    showContextMenu(e.clientX, e.clientY, { channelIds: selectedInGroup });
  };

  // Handle copying channel URL to clipboard
  const handleCopyChannelUrl = async (url: string, channelName: string) => {
    await handleCopy(url, `channel URL for "${channelName}"`);
  };

  // Handle copying stream URL to clipboard
  const handleCopyStreamUrl = async (url: string, streamName: string) => {
    await handleCopy(url, `stream URL for "${streamName}"`);
  };

  // Handle removing a stream from the selected channel
  const handleRemoveStream = async (streamId: number) => {
    if (!selectedChannelId) return;
    // Require edit mode for stream removal
    if (!isEditMode || !onStageRemoveStream) return;

    const channel = channels.find((c) => c.id === selectedChannelId);
    const stream = channelStreams.find((s) => s.id === streamId);
    const description = `Removed "${stream?.name || 'stream'}" from "${channel?.name || 'channel'}"`;

    // Stage the operation locally
    onStageRemoveStream(selectedChannelId, streamId, description);
    setChannelStreams((prev) => prev.filter((s) => s.id !== streamId));
  };

  // Handle clearing probe stats for a stream
  const handleClearStreamStats = async (streamId: number) => {
    console.log('handleClearStreamStats called with streamId:', streamId);
    try {
      const result = await api.clearStreamStats([streamId]);
      console.log('clearStreamStats result:', result);
      // Remove from local stats map so UI updates immediately
      setStreamStatsMap((prev) => {
        const next = new Map(prev);
        next.delete(streamId);
        console.log('Removed streamId from stats map:', streamId);
        return next;
      });
    } catch (err) {
      console.error('Failed to clear stream stats:', err);
    }
  };

  // Handle initiating channel deletion
  const handleDeleteChannelClick = (channel: Channel) => {
    setChannelToDelete(channel);

    // Find subsequent contiguous channels in the same group
    const groupId = channel.channel_group_id ?? 'ungrouped';
    const groupChannels = channelsByGroup[groupId] || [];
    const channelNumber = channel.channel_number;

    if (channelNumber !== null) {
      // Find channels that come after the deleted one and are contiguous
      const subsequent: Channel[] = [];
      let expectedNumber = channelNumber + 1;

      // Sort channels by number to ensure we check in order
      const sortedGroupChannels = [...groupChannels].sort(
        (a, b) => (a.channel_number ?? 9999) - (b.channel_number ?? 9999)
      );

      // Find the index of the channel being deleted
      const deleteIndex = sortedGroupChannels.findIndex((ch) => ch.id === channel.id);

      // Check channels after the deleted one for contiguity
      for (let i = deleteIndex + 1; i < sortedGroupChannels.length; i++) {
        const ch = sortedGroupChannels[i];
        if (ch.channel_number === expectedNumber) {
          subsequent.push(ch);
          expectedNumber++;
        } else {
          // Gap found, stop checking
          break;
        }
      }

      setSubsequentChannels(subsequent);
      setRenumberAfterDelete(subsequent.length > 0); // Default to renumber if there are subsequent channels
    } else {
      setSubsequentChannels([]);
      setRenumberAfterDelete(false);
    }

    deleteConfirmModal.open();
  };

  // Handle opening edit channel modal
  const handleEditChannel = (channel: Channel) => {
    setChannelToEdit(channel);
    editChannelModal.open();
  };

  // Helper to get logo URL for a channel - uses logoMap for O(1) lookup
  const getChannelLogoUrl = useCallback((channel: Channel): string | null => {
    // For staged channels (during edit mode), use the temporary logo URL
    if (channel._stagedLogoUrl) {
      return channel._stagedLogoUrl;
    }
    if (!channel.logo_id) return null;
    const logo = logoMap.get(channel.logo_id);
    return logo?.cache_url || logo?.url || null;
  }, [logoMap]);

  // Handle confirming channel deletion
  const handleConfirmDelete = async () => {
    if (!channelToDelete) return;

    setDeleting(true);
    try {
      // If renumbering is enabled and there are subsequent channels, renumber them first
      if (renumberAfterDelete && subsequentChannels.length > 0 && isEditMode && onStageUpdateChannel) {
        // Renumber each subsequent channel (move up by 1)
        for (const ch of subsequentChannels) {
          const newNumber = ch.channel_number! - 1;
          const newName = autoRenameChannelNumber ? computeAutoRename(ch.name, ch.channel_number, newNumber) : undefined;
          const description = newName
            ? `Changed "${ch.name}" to "${newName}"`
            : `Changed channel number from ${ch.channel_number} to ${newNumber}`;
          onStageUpdateChannel(ch.id, {
            channel_number: newNumber,
            ...(newName ? { name: newName } : {}),
          }, description);
        }

        // Update local state for the renumbered channels
        setLocalChannels((prev) =>
          prev.map((ch) => {
            const subsequent = subsequentChannels.find((s) => s.id === ch.id);
            if (subsequent) {
              const newNumber = ch.channel_number! - 1;
              const newName = autoRenameChannelNumber ? computeAutoRename(ch.name, ch.channel_number, newNumber) : undefined;
              return {
                ...ch,
                channel_number: newNumber,
                ...(newName ? { name: newName } : {}),
              };
            }
            return ch;
          })
        );
      }

      // In edit mode, stage the delete operation for undo support
      if (isEditMode && onStageDeleteChannel) {
        const description = `Delete channel "${channelToDelete.name}"`;
        onStageDeleteChannel(channelToDelete.id, description);
        // Local state is updated via displayChannels from working copy
      } else {
        // Not in edit mode, delete immediately via API
        await onDeleteChannel(channelToDelete.id);
        // Remove from local state
        setLocalChannels((prev) => prev.filter((ch) => ch.id !== channelToDelete.id));
      }

      // Clear selection if deleted channel was selected
      if (selectedChannelId === channelToDelete.id) {
        onChannelSelect(null);
      }
      deleteConfirmModal.close();
      setChannelToDelete(null);
      setSubsequentChannels([]);
    } catch (err) {
      console.error('Failed to delete channel:', err);
    } finally {
      setDeleting(false);
    }
  };

  // Handle canceling channel deletion
  const handleCancelDelete = () => {
    deleteConfirmModal.close();
    setChannelToDelete(null);
    setSubsequentChannels([]);
    setRenumberAfterDelete(true);
  };

  // Handle initiating group deletion
  const handleDeleteGroupClick = (group: ChannelGroup) => {
    setGroupToDelete(group);
    deleteGroupConfirmModal.open();
  };

  // Handle confirming group deletion
  const handleConfirmDeleteGroup = async () => {
    if (!groupToDelete) return;

    setDeletingGroup(true);
    try {
      // If "also delete channels" is checked, delete the channels first
      if (deleteGroupChannels && groupToDelete.channel_count > 0) {
        // Find all channels in this group
        const channelsInGroup = channels.filter((ch) => ch.channel_group_id === groupToDelete.id);

        if (isEditMode && onStageDeleteChannel && onStartBatch && onEndBatch) {
          // In edit mode, stage all channel deletes as a batch
          onStartBatch(`Delete group "${groupToDelete.name}" and ${channelsInGroup.length} channels`);
          for (const channel of channelsInGroup) {
            onStageDeleteChannel(channel.id, `Delete channel "${channel.name}"`);
          }
          // Stage the group delete
          if (onStageDeleteChannelGroup) {
            onStageDeleteChannelGroup(groupToDelete.id, `Delete group "${groupToDelete.name}"`);
          }
          onEndBatch();
        } else {
          // Not in edit mode, delete channels immediately via API
          for (const channel of channelsInGroup) {
            await onDeleteChannel(channel.id);
          }
          // Then delete the group
          if (onDeleteChannelGroup) {
            await onDeleteChannelGroup(groupToDelete.id);
          }
        }
      } else {
        // Just delete the group (channels will be moved to ungrouped)
        if (isEditMode && onStageDeleteChannelGroup) {
          const description = `Delete group "${groupToDelete.name}"`;
          onStageDeleteChannelGroup(groupToDelete.id, description);
        } else if (onDeleteChannelGroup) {
          await onDeleteChannelGroup(groupToDelete.id);
        }
      }

      deleteGroupConfirmModal.close();
      setGroupToDelete(null);
      setDeleteGroupChannels(false);
    } catch (err) {
      console.error('Failed to delete group:', err);
    } finally {
      setDeletingGroup(false);
    }
  };

  // Handle canceling group deletion
  const handleCancelDeleteGroup = () => {
    deleteGroupConfirmModal.close();
    setGroupToDelete(null);
    setDeleteGroupChannels(false);
  };

  // Handle bulk delete channels
  const handleBulkDeleteClick = () => {
    if (selectedChannelIds.size === 0) return;
    bulkDeleteConfirmModal.open();
  };

  const handleConfirmBulkDelete = async () => {
    if (selectedChannelIds.size === 0) return;

    setBulkDeleting(true);
    try {
      const channelIdsToDelete = Array.from(selectedChannelIds);

      // In edit mode, stage the delete operations for undo support
      if (isEditMode && onStageDeleteChannel && onStartBatch && onEndBatch) {
        // Find groups that would be emptied by this delete (if checkbox is checked)
        // Only check when no search filter is active (otherwise user can't select all channels in a group)
        const groupsToDelete: ChannelGroup[] = [];
        if (deleteEmptyGroups && onStageDeleteChannelGroup && !searchTerm) {
          for (const group of channelGroups) {
            const channelsInGroup = channels.filter(ch => ch.channel_group_id === group.id);
            if (channelsInGroup.length > 0) {
              const allSelected = channelsInGroup.every(ch => selectedChannelIds.has(ch.id));
              if (allSelected) {
                groupsToDelete.push(group);
              }
            }
          }
        }

        // Use batch to group all deletes as a single undo operation
        const batchDescription = groupsToDelete.length > 0
          ? `Delete ${channelIdsToDelete.length} channels and ${groupsToDelete.length} group${groupsToDelete.length !== 1 ? 's' : ''}`
          : `Delete ${channelIdsToDelete.length} channels`;
        onStartBatch(batchDescription);

        // Stage channel deletions
        for (const channelId of channelIdsToDelete) {
          const channel = channels.find((ch) => ch.id === channelId);
          const description = `Delete channel "${channel?.name || channelId}"`;
          onStageDeleteChannel(channelId, description);
        }

        // Stage group deletions if checkbox is checked
        if (deleteEmptyGroups && onStageDeleteChannelGroup) {
          for (const group of groupsToDelete) {
            onStageDeleteChannelGroup(group.id, `Delete group "${group.name}"`);
          }
        }

        onEndBatch();
        // Local state is updated via displayChannels from working copy
      } else {
        // Not in edit mode, delete immediately via API
        for (const channelId of channelIdsToDelete) {
          await onDeleteChannel(channelId);
        }
        // Update local state
        setLocalChannels((prev) => prev.filter((ch) => !selectedChannelIds.has(ch.id)));
      }

      // Clear selection
      if (onClearChannelSelection) {
        onClearChannelSelection();
      }

      // Clear selected channel if it was deleted
      if (selectedChannelId && selectedChannelIds.has(selectedChannelId)) {
        onChannelSelect(null);
      }

      bulkDeleteConfirmModal.close();
    } catch (err) {
      console.error('Failed to bulk delete channels:', err);
    } finally {
      setBulkDeleting(false);
    }
  };

  const handleCancelBulkDelete = () => {
    bulkDeleteConfirmModal.close();
  };

  // Handle bulk EPG assignment
  const handleBulkEPGAssign = (assignments: EPGAssignment[]) => {
    if (!isEditMode || !onStageUpdateChannel || !onStartBatch || !onEndBatch) {
      return;
    }

    // Use batch to group all assignments as a single undo operation
    onStartBatch(`Assign EPG to ${assignments.length} channels`);
    for (const assignment of assignments) {
      const description = `Assign EPG "${assignment.tvg_id}" to "${assignment.channelName}"`;
      onStageUpdateChannel(assignment.channelId, {
        tvg_id: assignment.tvg_id,
        epg_data_id: assignment.epg_data_id,
      }, description);
    }
    onEndBatch();

    // Close modal and clear selection
    bulkEPGModal.close();
    if (onClearChannelSelection) {
      onClearChannelSelection();
    }
  };

  // Handle bulk LCN assignment
  const handleBulkLCNAssign = (assignments: LCNAssignment[]) => {
    if (!isEditMode || !onStageUpdateChannel || !onStartBatch || !onEndBatch) {
      return;
    }

    // Detect conflicts: channels that already have a different gracenote ID
    const conflicts: GracenoteConflict[] = [];
    const nonConflicts: LCNAssignment[] = [];

    for (const assignment of assignments) {
      const channel = channels.find(c => c.id === assignment.channelId);
      if (channel?.tvc_guide_stationid && channel.tvc_guide_stationid !== assignment.tvc_guide_stationid) {
        // Conflict: channel has a different gracenote ID
        conflicts.push({
          channelId: assignment.channelId,
          channelName: assignment.channelName,
          oldGracenoteId: channel.tvc_guide_stationid,
          newGracenoteId: assignment.tvc_guide_stationid,
        });
      } else {
        // No conflict: either no existing ID or same ID
        nonConflicts.push(assignment);
      }
    }

    // Handle based on conflict mode
    if (conflicts.length > 0) {
      if (gracenoteConflictMode === 'ask') {
        // Show conflict modal for user to decide
        setGracenoteConflicts(conflicts);
        setPendingLCNAssignments(assignments);
        gracenoteConflictModal.open();
        return; // Don't process yet, wait for user input
      } else if (gracenoteConflictMode === 'skip') {
        // Skip conflicted assignments, only process non-conflicts
        processLCNAssignments(nonConflicts);
        return;
      }
      // If 'overwrite', fall through to process all assignments
    }

    // No conflicts or mode is 'overwrite': process all assignments
    processLCNAssignments(assignments);
  };

  // Process LCN assignments (extracted for reuse)
  const processLCNAssignments = (assignments: LCNAssignment[]) => {
    if (!onStageUpdateChannel || !onStartBatch || !onEndBatch) {
      return;
    }

    if (assignments.length === 0) {
      // Close modal and clear selection
      bulkLCNModal.close();
      if (onClearChannelSelection) {
        onClearChannelSelection();
      }
      return;
    }

    // Use batch to group all assignments as a single undo operation
    onStartBatch(`Assign Gracenote ID to ${assignments.length} channels`);
    for (const assignment of assignments) {
      const description = `Assign Gracenote ID "${assignment.tvc_guide_stationid}" to "${assignment.channelName}"`;
      onStageUpdateChannel(assignment.channelId, {
        tvc_guide_stationid: assignment.tvc_guide_stationid,
      }, description);
    }
    onEndBatch();

    // Close modal and clear selection
    bulkLCNModal.close();
    if (onClearChannelSelection) {
      onClearChannelSelection();
    }
  };

  // Handle conflict resolution from modal
  const handleGracenoteConflictResolve = (channelsToUpdate: number[]) => {
    // User selected which channels to overwrite
    const selectedAssignments = pendingLCNAssignments.filter(assignment =>
      channelsToUpdate.includes(assignment.channelId)
    );

    // Also include non-conflicted assignments
    const nonConflicts = pendingLCNAssignments.filter(assignment => {
      const isConflict = gracenoteConflicts.some(c => c.channelId === assignment.channelId);
      return !isConflict;
    });

    const allAssignments = [...nonConflicts, ...selectedAssignments];

    // Close conflict modal and process assignments
    gracenoteConflictModal.close();
    setGracenoteConflicts([]);
    setPendingLCNAssignments([]);

    processLCNAssignments(allAssignments);
  };

  // Handle conflict modal cancel
  const handleGracenoteConflictCancel = () => {
    gracenoteConflictModal.close();
    setGracenoteConflicts([]);
    setPendingLCNAssignments([]);
  };

  // Handle normalize names
  const handleNormalizeNames = (channelUpdates: Array<{ id: number; newName: string }>) => {
    if (!isEditMode || !onStageUpdateChannel) return;

    if (channelUpdates.length > 1 && onStartBatch && onEndBatch) {
      onStartBatch(`Normalize ${channelUpdates.length} channel names`);
    }

    for (const update of channelUpdates) {
      onStageUpdateChannel(update.id, { name: update.newName }, `Normalize name to "${update.newName}"`);
    }

    if (channelUpdates.length > 1 && onStartBatch && onEndBatch) {
      onEndBatch();
    }

    normalizeModal.close();
    if (onClearChannelSelection) {
      onClearChannelSelection();
    }
  };

  // Handle reordering streams within the channel
  const handleStreamDragEnd = async (event: DragEndEvent) => {
    if (!selectedChannelId) return;
    // Require edit mode for stream reordering
    if (!isEditMode || !onStageReorderStreams) return;

    const { active, over } = event;
    const channel = channels.find((c) => c.id === selectedChannelId);

    if (over && active.id !== over.id) {
      const oldIndex = channelStreams.findIndex((s) => s.id === active.id);
      const newIndex = channelStreams.findIndex((s) => s.id === over.id);

      const newStreams = arrayMove(channelStreams, oldIndex, newIndex);
      const newStreamIds = newStreams.map((s) => s.id);
      const description = `Reordered streams in "${channel?.name || 'channel'}"`;

      setChannelStreams(newStreams);
      // Stage the operation locally
      onStageReorderStreams(selectedChannelId, newStreamIds, description);
    }
  };

  // Sort mode types
  type SortMode = 'smart' | 'resolution' | 'bitrate' | 'framerate';

  // Sort mode labels for journal/description
  const SORT_MODE_LABELS: Record<SortMode, string> = {
    smart: 'Smart Sort',
    resolution: 'resolution',
    bitrate: 'bitrate',
    framerate: 'framerate',
  };

  // Get sort value for a stream based on criterion
  const getSortValue = useCallback((stats: StreamStats | undefined, criterion: SortCriterion): number => {
    if (!stats || stats.probe_status !== 'success') return -1;
    switch (criterion) {
      case 'resolution': {
        if (!stats.resolution) return -1;
        const match = stats.resolution.match(/(\d+)x(\d+)/);
        return match ? parseInt(match[2], 10) : -1; // Return height
      }
      case 'bitrate':
        return stats.video_bitrate ?? stats.bitrate ?? -1;
      case 'framerate': {
        if (!stats.fps) return -1;
        const fps = parseFloat(stats.fps);
        return isNaN(fps) ? -1 : fps;
      }
      default:
        return -1;
    }
  }, []);

  // Create comparator for multi-criteria sorting
  const createMultiCriteriaSortComparator = useCallback((
    statsMap: Map<number, StreamStats> | Record<number, StreamStats>,
    priority: SortCriterion[]
  ) => {
    const getStats = (id: number): StreamStats | undefined => {
      if (statsMap instanceof Map) {
        return statsMap.get(id);
      }
      return statsMap[id];
    };

    return (aId: number, bId: number): number => {
      const aStats = getStats(aId);
      const bStats = getStats(bId);

      // Check probe status FIRST if the setting is enabled - failed/timeout/pending streams sort to bottom
      if (channelDefaults?.deprioritizeFailedStreams) {
        const aProbeSuccess = aStats?.probe_status === 'success';
        const bProbeSuccess = bStats?.probe_status === 'success';

        // If one stream failed and the other succeeded, prioritize the successful one
        if (aProbeSuccess && !bProbeSuccess) return -1; // a comes first (successful)
        if (!aProbeSuccess && bProbeSuccess) return 1;  // b comes first (successful)

        // If both failed/timeout/pending, maintain current order (stable sort)
        if (!aProbeSuccess && !bProbeSuccess) return 0;
      }

      // Both streams succeeded (or setting disabled) - now sort by quality criteria
      for (const criterion of priority) {
        const aVal = getSortValue(aStats, criterion);
        const bVal = getSortValue(bStats, criterion);

        // Both have no data for this criterion - continue to next
        if (aVal === -1 && bVal === -1) continue;

        // One has no data - sort it to the end
        if (aVal === -1) return 1;
        if (bVal === -1) return -1;

        // Both have data - compare (higher is better)
        if (bVal !== aVal) return bVal - aVal;
      }

      // All criteria equal
      return 0;
    };
  }, [getSortValue, channelDefaults?.deprioritizeFailedStreams]);

  // Get effective sort priority based on mode, filtered by enabled criteria
  const getEffectivePriority = useCallback((mode: SortMode): SortCriterion[] => {
    const enabledMap = channelDefaults?.streamSortEnabled ?? { resolution: true, bitrate: true, framerate: true };

    if (mode === 'smart') {
      // Filter the priority list to only include enabled criteria
      const priority = channelDefaults?.streamSortPriority ?? ['resolution', 'bitrate', 'framerate'];
      return priority.filter(criterion => enabledMap[criterion]);
    }
    // Single criterion mode - just use that criterion (already enabled check done in UI)
    return [mode as SortCriterion];
  }, [channelDefaults?.streamSortPriority, channelDefaults?.streamSortEnabled]);

  // Sort streams by specified mode (single channel)
  const handleSortStreamsByMode = useCallback((mode: SortMode) => {
    if (!selectedChannelId || !isEditMode || !onStageReorderStreams) return;

    const channel = channels.find((c) => c.id === selectedChannelId);
    const priority = getEffectivePriority(mode);
    const comparator = createMultiCriteriaSortComparator(streamStatsMap, priority);

    // Sort streams
    const sortedStreams = [...channelStreams].sort((a, b) => comparator(a.id, b.id));

    // Check if already sorted
    const alreadySorted = sortedStreams.every((s, i) => s.id === channelStreams[i].id);
    if (alreadySorted) return;

    const newStreamIds = sortedStreams.map((s) => s.id);
    const description = `Sorted streams by ${SORT_MODE_LABELS[mode]} in "${channel?.name || 'channel'}"`;

    setChannelStreams(sortedStreams);
    onStageReorderStreams(selectedChannelId, newStreamIds, description);
  }, [selectedChannelId, isEditMode, onStageReorderStreams, channelStreams, streamStatsMap, channels, getEffectivePriority, createMultiCriteriaSortComparator]);

  // State for bulk sort operation
  const [bulkSortingByQuality, setBulkSortingByQuality] = useState(false);

  // Bulk sort streams by mode for multiple channels
  const handleBulkSortStreamsByMode = useCallback(async (channelIds: number[], mode: SortMode) => {
    if (!isEditMode || !onStageReorderStreams || channelIds.length === 0) return;

    setBulkSortingByQuality(true);
    try {
      // Get all channels to process
      const channelsToProcess = channels.filter(ch => channelIds.includes(ch.id) && ch.streams.length > 1);
      if (channelsToProcess.length === 0) {
        setBulkSortingByQuality(false);
        return;
      }

      // Collect all stream IDs
      const allStreamIds = channelsToProcess.flatMap(ch => ch.streams);

      // Fetch stats for all streams
      const stats = await api.getStreamStatsByIds(allStreamIds);

      // Get sort priority and create comparator
      const priority = getEffectivePriority(mode);
      const comparator = createMultiCriteriaSortComparator(stats, priority);

      // Start batch operation
      if (onStartBatch) {
        const scope = channelIds.length === channels.length ? 'all channels' :
          channelIds.length === 1 ? `"${channelsToProcess[0]?.name}"` :
          `${channelsToProcess.length} channels`;
        onStartBatch(`Sort streams by ${SORT_MODE_LABELS[mode]} in ${scope}`);
      }

      let changesCount = 0;
      for (const channel of channelsToProcess) {
        // Sort stream IDs
        const sortedStreamIds = [...channel.streams].sort(comparator);

        // Check if order changed
        const changed = !sortedStreamIds.every((id, i) => id === channel.streams[i]);
        if (changed) {
          changesCount++;
          onStageReorderStreams(channel.id, sortedStreamIds, `Sorted streams by ${SORT_MODE_LABELS[mode]} in "${channel.name}"`);
        }
      }

      if (onEndBatch) {
        onEndBatch();
      }

      // Update local channelStreams if current channel was affected
      if (selectedChannelId && channelIds.includes(selectedChannelId)) {
        const currentChannel = channels.find(ch => ch.id === selectedChannelId);
        if (currentChannel) {
          const sortedIds = [...currentChannel.streams].sort(comparator);
          const newStreams = sortedIds.map(id => channelStreams.find(s => s.id === id)).filter((s): s is Stream => !!s);
          if (newStreams.length === channelStreams.length) {
            setChannelStreams(newStreams);
          }
        }
      }

      logger.info(`Bulk sort by ${SORT_MODE_LABELS[mode]}: ${changesCount} of ${channelsToProcess.length} channels reordered`);
    } catch (err) {
      logger.error(`Failed to bulk sort streams by ${SORT_MODE_LABELS[mode]}:`, err);
    } finally {
      setBulkSortingByQuality(false);
    }
  }, [isEditMode, onStageReorderStreams, channels, onStartBatch, onEndBatch, selectedChannelId, channelStreams, getEffectivePriority, createMultiCriteriaSortComparator]);

  // Sort all channels' streams by mode
  const handleSortAllStreamsByMode = useCallback((mode: SortMode) => {
    const allChannelIds = channels.map(ch => ch.id);
    handleBulkSortStreamsByMode(allChannelIds, mode);
  }, [channels, handleBulkSortStreamsByMode]);

  // Sort selected channels' streams by mode
  const handleSortSelectedStreamsByMode = useCallback((mode: SortMode) => {
    handleBulkSortStreamsByMode(Array.from(selectedChannelIds), mode);
  }, [selectedChannelIds, handleBulkSortStreamsByMode]);

  // Sort a group's channels' streams by mode
  const handleSortGroupStreamsByMode = useCallback((groupId: number | 'ungrouped', mode: SortMode) => {
    const groupChannelIds = channels
      .filter(ch => (groupId === 'ungrouped' ? ch.channel_group_id === null : ch.channel_group_id === groupId))
      .map(ch => ch.id);
    handleBulkSortStreamsByMode(groupChannelIds, mode);
  }, [channels, handleBulkSortStreamsByMode]);

  // Legacy handler
  const handleSortGroupStreamsByQuality = useCallback((groupId: number | 'ungrouped') => {
    handleSortGroupStreamsByMode(groupId, 'smart');
  }, [handleSortGroupStreamsByMode]);

  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: {
        distance: 8,
      },
    }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    })
  );

  // Close the create group modal and reset form state
  const handleCloseCreateGroupModal = () => {
    createGroupModal.close();
    setNewGroupName('');
    setCreateGroupShouldMoveChannels(false);  // Reset the flag
  };

  // Handle creating a new channel group
  const handleCreateGroup = async () => {
    if (!newGroupName.trim()) return;

    setCreatingGroup(true);
    try {
      const newGroup = await api.createChannelGroup(newGroupName.trim());
      if (onChannelGroupsChange) {
        onChannelGroupsChange();
      }
      // Track the newly created group
      if (onTrackNewlyCreatedGroup) {
        onTrackNewlyCreatedGroup(newGroup.id);
      }
      // Auto-select the new group so it appears in the channel list
      if (!selectedGroups.includes(newGroup.id)) {
        onSelectedGroupsChange([...selectedGroups, newGroup.id]);
      }

      // If we have selected channels AND this was triggered from context menu, move them to the new group
      if (createGroupShouldMoveChannels && selectedChannelIds.size > 0) {
        const channelsToMove = localChannels
          .filter(ch => selectedChannelIds.has(ch.id))
          .sort((a, b) => naturalCompare(a.name, b.name));
        if (channelsToMove.length > 0) {
          const sourceGroupId = channelsToMove[0].channel_group_id;
          const sourceGroupName = sourceGroupId === null
            ? 'Uncategorized'
            : channelGroups.find((g) => g.id === sourceGroupId)?.name ?? 'Unknown Group';

          // Calculate target group info (new group is empty, so no existing channels)
          const minChannelInGroup: number | null = null;
          const maxChannelInGroup: number | null = null;
          const suggestedChannelNumber: number | null = null;

          setCrossGroupMoveData({
            channels: channelsToMove,
            targetGroupId: newGroup.id,
            targetGroupName: newGroup.name,
            sourceGroupId,
            sourceGroupName,
            isTargetAutoSync: false,
            suggestedChannelNumber,
            minChannelInGroup,
            maxChannelInGroup,
            insertAtPosition: false,
            sourceGroupHasGaps: false,
            sourceGroupMinChannel: null,
          });
          crossGroupMoveModal.open();
        }
      }

      handleCloseCreateGroupModal();
    } catch (err) {
      console.error('Failed to create channel group:', err);
    } finally {
      setCreatingGroup(false);
    }
  };

  // Load hidden groups
  const loadHiddenGroups = async () => {
    try {
      const groups = await api.getHiddenChannelGroups();
      setHiddenGroups(groups);
    } catch (error) {
      console.error('Failed to load hidden groups:', error);
    }
  };

  // Restore a hidden group
  const handleRestoreGroup = async (groupId: number) => {
    try {
      await api.restoreChannelGroup(groupId);
      // Reload hidden groups list
      await loadHiddenGroups();
      // Reload channel groups to show the restored group
      if (onChannelGroupsChange) {
        onChannelGroupsChange();
      }
    } catch (error) {
      console.error('Failed to restore group:', error);
    }
  };

  // Open hidden groups modal and load the list
  const handleShowHiddenGroups = () => {
    hiddenGroupsModal.open();
    loadHiddenGroups();
  };

  // Get the next available channel number at the end of a group
  // Use localChannels in edit mode since it may have been modified
  const getNextChannelNumberForGroup = (groupId: number | ''): number => {
    const sourceChannels = isEditMode ? localChannels : channels;
    const groupChannels = groupId !== ''
      ? sourceChannels.filter((ch) => ch.channel_group_id === groupId)
      : sourceChannels.filter((ch) => ch.channel_group_id === null);

    if (groupChannels.length === 0) {
      // No channels in group, start at 1 (or find a reasonable default)
      return 1;
    }

    // Find the max channel number in the group and add 1
    const maxNumber = Math.max(...groupChannels.map((ch) => ch.channel_number ?? 0));
    return maxNumber + 1;
  };

  // Get the suggested starting number based on the group that would precede the drop location
  // This is used for smart channel number inference when dropping stream groups between/after channel groups
  const getSuggestedStartingNumberAfterGroup = (precedingGroupId: number | 'ungrouped' | null): number => {
    const sourceChannels = isEditMode ? localChannels : channels;

    if (precedingGroupId === null) {
      // Dropped at the very beginning - suggest starting at 1
      return 1;
    }

    // Get all channels from the preceding group
    const precedingGroupChannels = precedingGroupId === 'ungrouped'
      ? sourceChannels.filter((ch) => ch.channel_group_id === null)
      : sourceChannels.filter((ch) => ch.channel_group_id === precedingGroupId);

    if (precedingGroupChannels.length === 0) {
      // Empty preceding group - find the highest number before this point
      // Get all groups in order and find the preceding group's position
      const groupIndex = filteredChannelGroups.findIndex(g =>
        precedingGroupId === 'ungrouped' ? false : g.id === precedingGroupId
      );

      if (groupIndex <= 0) {
        return 1;
      }

      // Look at all channels in groups before this one
      const precedingGroupIds = filteredChannelGroups.slice(0, groupIndex).map(g => g.id);
      const channelsBeforeThisGroup = sourceChannels.filter(ch =>
        ch.channel_group_id !== null && precedingGroupIds.includes(ch.channel_group_id)
      );

      if (channelsBeforeThisGroup.length === 0) {
        return 1;
      }

      const maxBeforeNumber = Math.max(...channelsBeforeThisGroup.map((ch) => ch.channel_number ?? 0));
      return maxBeforeNumber + 1;
    }

    // Find the max channel number in the preceding group and suggest next number
    const maxNumber = Math.max(...precedingGroupChannels.map((ch) => ch.channel_number ?? 0));
    return maxNumber + 1;
  };

  // Handle stream dropped on group header - creates new channel(s) with stream name(s)
  // Always routes to bulk create modal for consistent UX (works for 1 or many streams)
  const handleStreamDropOnGroup = (groupId: number | 'ungrouped', streamIds: number[]) => {
    if (streamIds.length === 0 || !onBulkStreamsDrop) return;

    // Calculate the starting channel number for this group
    const numericGroupId = groupId === 'ungrouped' ? '' : groupId;
    const nextNumber = getNextChannelNumberForGroup(numericGroupId);
    const targetGroupId = groupId === 'ungrouped' ? null : groupId;

    // Route to bulk create modal (handles single or multiple streams)
    onBulkStreamsDrop(streamIds, targetGroupId, nextNumber);
  };

  // Handle stream dropped between channels - creates new channel(s) at specific position
  // Always routes to bulk create modal for consistent UX (works for 1 or many streams)
  const handleStreamDropAtPosition = (
    groupId: number | 'ungrouped',
    streamIds: number[],
    insertAtChannelNumber: number
  ) => {
    if (streamIds.length === 0 || !onBulkStreamsDrop) return;

    const targetGroupId = groupId === 'ungrouped' ? null : groupId;

    // Route to bulk create modal (handles single or multiple streams)
    onBulkStreamsDrop(streamIds, targetGroupId, insertAtChannelNumber);
  };

  // Helper function to strip leading/trailing/middle channel numbers from a name for sorting purposes
  // Matches same patterns as computeAutoRename: "123 | Name", "123-Name", "US | 5034 - Name", "Name | 123"
  // Memoized with useCallback - no dependencies as it's a pure function
  const getNameForSorting = useCallback((channelName: string): string => {
    // Try stripping mid-position number first: "US | 5034 - Name" -> "US - Name"
    const midMatch = channelName.match(/^([A-Za-z].+?\s*\|\s*)\d+(?:\.\d+)?\s*([-:]\s*.+)$/);
    if (midMatch) {
      return (midMatch[1] + midMatch[2]).trim();
    }

    // Try stripping prefix: "123 | Name" or "123-Name" or "123.Name" or "123 Name"
    const prefixMatch = channelName.match(/^(\d+(?:\.\d+)?)\s*[|\-.\s]\s*(.+)$/);
    if (prefixMatch) {
      return prefixMatch[2].trim();
    }

    // Try stripping suffix: "Name | 123"
    const suffixMatch = channelName.match(/^(.+)\s*[|\-.]\s*(\d+(?:\.\d+)?)$/);
    if (suffixMatch) {
      return suffixMatch[1].trim();
    }

    // No number prefix/suffix found, return as-is
    return channelName;
  }, []);

  // Helper function to strip country prefix from channel name for sorting
  // Common patterns: "US | Name", "UK: Name", "CA - Name", "AU Name"
  // Country codes are typically 2-3 uppercase letters at the start
  const stripCountryPrefix = useCallback((channelName: string): string => {
    // Match country code (2-3 uppercase letters) followed by separator and the rest
    // Supports: "US | Name", "UK: Name", "CA - Name", "USA | Name", etc.
    const match = channelName.match(/^[A-Z]{2,3}\s*[|:\-]\s*(.+)$/);
    if (match) {
      return match[1].trim();
    }
    // Also try without separator: "US Name" (2-3 uppercase followed by space and uppercase)
    const noSepMatch = channelName.match(/^[A-Z]{2,3}\s+([A-Z].+)$/);
    if (noSepMatch) {
      return noSepMatch[1].trim();
    }
    return channelName;
  }, []);

  // Handle editing channel number
  const handleStartEditNumber = (e: React.MouseEvent, channel: Channel) => {
    e.stopPropagation();
    // Block channel number editing when not in edit mode
    if (!isEditMode) return;
    setEditingChannelId(channel.id);
    setEditingChannelNumber(channel.channel_number?.toString() ?? '');
  };

  const handleSaveChannelNumber = async (channelId: number) => {
    const newNumber = editingChannelNumber.trim() ? parseFloat(editingChannelNumber) : null;
    const channel = channels.find((c) => c.id === channelId);

    const updateData: { channel_number: number | null; name?: string } = { channel_number: newNumber };
    let nameChanged = false;

    // If auto-rename is enabled, check if we should update the channel name
    if (channel && autoRenameChannelNumber) {
      const newName = computeAutoRename(channel.name, channel.channel_number, newNumber);
      if (newName) {
        updateData.name = newName;
        nameChanged = true;
      }
    }

    // Determine description (preview what will change)
    const description = nameChanged
      ? `Changed "${channel?.name}" to "${updateData.name}"`
      : `Changed channel number from ${channel?.channel_number ?? '-'} to ${newNumber ?? '-'}`;

    if (isEditMode && onStageUpdateChannel) {
      // In edit mode, stage the operation locally
      onStageUpdateChannel(channelId, updateData, description);
    } else {
      // Normal mode - call API directly
      try {
        const updatedChannel = await api.updateChannel(channelId, updateData);
        const changeType = nameChanged ? 'channel_name_update' : 'channel_number_update';
        onChannelUpdate(updatedChannel, { type: changeType, description });
      } catch (err) {
        console.error('Failed to update channel number:', err);
      }
    }
    setEditingChannelId(null);
  };

  const handleCancelEditNumber = () => {
    setEditingChannelId(null);
    setEditingChannelNumber('');
  };

  // Handle editing channel name
  const handleStartEditName = (e: React.MouseEvent, channel: Channel) => {
    e.stopPropagation();
    // Block channel name editing when not in edit mode
    if (!isEditMode) return;
    setEditingNameChannelId(channel.id);
    setEditingChannelName(channel.name);
  };

  const handleSaveChannelName = async (channelId: number) => {
    const newName = editingChannelName.trim();
    const channel = channels.find((c) => c.id === channelId);

    if (!newName || newName === channel?.name) {
      // No change or empty name, just cancel
      setEditingNameChannelId(null);
      return;
    }

    const description = `Renamed channel "${channel?.name}" to "${newName}"`;

    if (isEditMode && onStageUpdateChannel) {
      // In edit mode, stage the operation locally
      onStageUpdateChannel(channelId, { name: newName }, description);
    } else {
      // Normal mode - call API directly
      try {
        const updatedChannel = await api.updateChannel(channelId, { name: newName });
        onChannelUpdate(updatedChannel, { type: 'channel_name_update', description });
      } catch (err) {
        console.error('Failed to update channel name:', err);
      }
    }
    setEditingNameChannelId(null);
  };

  const handleCancelEditName = () => {
    setEditingNameChannelId(null);
    setEditingChannelName('');
  };

  const toggleGroup = (groupId: number) => {
    setExpandedGroups((prev) => ({ ...prev, [groupId]: !prev[groupId] }));
  };

  const handleStreamDragOver = (e: React.DragEvent, channelId: number) => {
    // Block stream drag-over when not in edit mode
    if (!isEditMode) return;

    // Only handle stream drags (from external drag source)
    // Check for both lowercase 'streamid' and actual data types
    const types = e.dataTransfer.types.map(t => t.toLowerCase());
    if (types.includes('streamid') || types.includes('streamids')) {
      e.preventDefault();
      setDragOverChannelId(channelId);
    }
  };

  const handleStreamDragLeave = () => {
    setDragOverChannelId(null);
  };

  const handleStreamDrop = (e: React.DragEvent, channelId: number) => {
    setDragOverChannelId(null);

    // Block stream drops when not in edit mode
    if (!isEditMode) return;

    e.preventDefault();

    // Check for bulk drag first
    const bulkDrag = e.dataTransfer.getData('bulkDrag');
    if (bulkDrag === 'true') {
      const streamIdsJson = e.dataTransfer.getData('streamIds');
      if (streamIdsJson) {
        try {
          const streamIds = JSON.parse(streamIdsJson) as number[];
          onBulkStreamDrop(channelId, streamIds);
          return;
        } catch {
          // Fall through to single stream handling
        }
      }
    }

    // Single stream drop
    const streamId = e.dataTransfer.getData('streamId');
    if (streamId) {
      onChannelDrop(channelId, parseInt(streamId, 10));
    }
  };

  // Handle stream group drag over the pane (for bulk channel creation)
  const handlePaneDragOver = (e: React.DragEvent) => {
    // Check if this is a stream group drag
    if (e.dataTransfer.types.includes('streamgroupdrag')) {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'copy';
      setStreamGroupDragOver(true);
    }
  };

  const handlePaneDragLeave = (e: React.DragEvent) => {
    // Only trigger if leaving the pane (not entering a child element)
    if (!e.currentTarget.contains(e.relatedTarget as Node)) {
      setStreamGroupDragOver(false);
    }
  };

  const handlePaneDrop = (e: React.DragEvent) => {
    setStreamGroupDragOver(false);

    // Determine drop target and suggested number
    let targetGroupId: number | undefined;
    let suggestedStartingNumber: number | undefined;

    if (streamGroupDropTarget) {
      // Calculate based on the drop zone that was hovered
      const precedingGroupId = streamGroupDropTarget.afterGroupId;
      suggestedStartingNumber = getSuggestedStartingNumberAfterGroup(precedingGroupId);
      // For now, we don't set a target group ID (user will choose in modal)
      // But we could default to creating in a new group or the next group
    }

    // Clear drop target state
    setStreamGroupDropTarget(null);

    // Check for stream group drop (supports multiple groups)
    const isStreamGroupDrag = e.dataTransfer.getData('streamGroupDrag');
    if (isStreamGroupDrag === 'true' && isEditMode && onStreamGroupDrop) {
      e.preventDefault();
      const streamIdsJson = e.dataTransfer.getData('streamGroupStreamIds');
      // Check for multiple groups first (new format)
      const groupNamesJson = e.dataTransfer.getData('streamGroupNames');
      if (groupNamesJson && streamIdsJson) {
        try {
          const groupNames = JSON.parse(groupNamesJson) as string[];
          const streamIds = JSON.parse(streamIdsJson) as number[];
          onStreamGroupDrop(groupNames, streamIds, targetGroupId, suggestedStartingNumber);
        } catch {
          console.error('Failed to parse stream group drop data');
        }
      } else {
        // Fallback to single group (backward compatibility)
        const groupName = e.dataTransfer.getData('streamGroupName');
        if (groupName && streamIdsJson) {
          try {
            const streamIds = JSON.parse(streamIdsJson) as number[];
            onStreamGroupDrop([groupName], streamIds, targetGroupId, suggestedStartingNumber);
          } catch {
            console.error('Failed to parse stream IDs from stream group drop');
          }
        }
      }
    }
  };

  // Filter channels: show manual channels always, show auto-created only if their group is related to auto_channel_sync
  // Note: providerGroupSettings keys are strings from JSON even though typed as number
  const providerSettingsMap = providerGroupSettings as unknown as Record<string, M3UGroupSetting> | undefined;

  // Build a set of group IDs that are related to auto_channel_sync:
  // 1. Groups that have auto_channel_sync: true directly
  // 2. Groups that are group_override targets of auto_channel_sync groups
  const autoSyncRelatedGroups = new Set<number>();
  if (providerSettingsMap) {
    for (const setting of Object.values(providerSettingsMap)) {
      if (setting.auto_channel_sync) {
        // Add the source group itself
        autoSyncRelatedGroups.add(setting.channel_group);
        // Also add the group_override target if set
        if (setting.custom_properties?.group_override) {
          autoSyncRelatedGroups.add(setting.custom_properties.group_override);
        }
      }
    }
  }

  // Memoize expensive channel filtering and grouping operations
  const channelsByGroup = useMemo(() => {
    // Debug: Log channel group_id distribution before filtering
    const groupIdCounts: Record<string, number> = {};
    localChannels.forEach((ch) => {
      const key = ch.channel_group_id !== null ? String(ch.channel_group_id) : 'ungrouped';
      groupIdCounts[key] = (groupIdCounts[key] || 0) + 1;
    });
    logger.info('[CHANNELS-DEBUG] Frontend received channels per group_id (before filtering):', groupIdCounts);
    logger.info(`[CHANNELS-DEBUG] Total channels in localChannels: ${localChannels.length}`);

    // Count auto_created channels for debugging
    const autoCreatedChannels = localChannels.filter(ch => ch.auto_created);
    const manualChannels = localChannels.filter(ch => !ch.auto_created);
    logger.info(`[CHANNELS-DEBUG] Channel breakdown: ${manualChannels.length} manual, ${autoCreatedChannels.length} auto_created`);

    // Log auto_created channels by group for debugging
    if (autoCreatedChannels.length > 0) {
      const autoCreatedByGroup: Record<string, { count: number; samples: string[] }> = {};
      autoCreatedChannels.forEach((ch) => {
        const key = ch.channel_group_id !== null ? String(ch.channel_group_id) : 'ungrouped';
        if (!autoCreatedByGroup[key]) autoCreatedByGroup[key] = { count: 0, samples: [] };
        autoCreatedByGroup[key].count++;
        if (autoCreatedByGroup[key].samples.length < 3) {
          autoCreatedByGroup[key].samples.push(`#${ch.channel_number} ${ch.name}`);
        }
      });
      logger.info('[CHANNELS-DEBUG] Auto-created channels by group:', autoCreatedByGroup);
      logger.info('[CHANNELS-DEBUG] autoSyncRelatedGroups:', Array.from(autoSyncRelatedGroups));
    }

    // Filter channels based on search term and auto-created filter
    let hiddenDueToAutoCreated = 0;
    const visibleChannels = localChannels.filter((ch) => {
      // First, apply search filter if there's a search term
      if (searchTerm) {
        const searchLower = searchTerm.toLowerCase();
        const nameMatch = ch.name?.toLowerCase().includes(searchLower);
        const numberMatch = ch.channel_number?.toString().includes(searchTerm);
        if (!nameMatch && !numberMatch) return false;
      }

      if (!ch.auto_created) return true; // Always show manual channels
      // For auto-created channels, check if their group is related to auto_channel_sync
      const groupId = ch.channel_group_id;
      if (groupId && autoSyncRelatedGroups.has(groupId)) {
        // Show auto-created channel if showAutoChannelGroups filter is on
        return channelListFilters?.showAutoChannelGroups !== false;
      }
      hiddenDueToAutoCreated++;
      return false; // Hide auto-created channels from non-auto-sync groups
    });

    if (hiddenDueToAutoCreated > 0) {
      logger.warn(`[CHANNELS-DEBUG] Hidden ${hiddenDueToAutoCreated} channels due to auto_created filter (not in autoSyncRelatedGroups)`);
    }

    // Debug: Log after filtering
    const afterFilterCounts: Record<string, number> = {};
    visibleChannels.forEach((ch) => {
      const key = ch.channel_group_id !== null ? String(ch.channel_group_id) : 'ungrouped';
      afterFilterCounts[key] = (afterFilterCounts[key] || 0) + 1;
    });
    logger.info('[CHANNELS-DEBUG] Visible channels per group_id (after filtering):', afterFilterCounts);
    logger.info(`[CHANNELS-DEBUG] Visible channels: ${visibleChannels.length}, filtered out: ${localChannels.length - visibleChannels.length}`);

    // Group channels by channel_group_id
    const grouped = visibleChannels.reduce<Record<number | 'ungrouped', Channel[]>>(
      (acc, channel) => {
        const key = channel.channel_group_id ?? 'ungrouped';
        if (!acc[key]) acc[key] = [];
        acc[key].push(channel);
        return acc;
      },
      { ungrouped: [] }
    );

    // Sort channels within each group by channel_number
    Object.values(grouped).forEach((group) => {
      group.sort((a, b) => (a.channel_number ?? 9999) - (b.channel_number ?? 9999));
    });

    return grouped;
  }, [localChannels, searchTerm, channelListFilters, autoSyncRelatedGroups]);

  // Sort channel groups by their lowest channel number (only groups with channels)
  const sortedChannelGroups = useMemo(() => {
    return [...channelGroups]
      .filter((g) => channelsByGroup[g.id]?.length > 0)
      .sort((a, b) => {
        // If custom order exists, use it
        if (groupOrder.length > 0) {
          const aIndex = groupOrder.indexOf(a.id);
          const bIndex = groupOrder.indexOf(b.id);
          // If both in order array, use order
          if (aIndex !== -1 && bIndex !== -1) {
            return aIndex - bIndex;
          }
          // If only one is in order, prioritize the one in order
          if (aIndex !== -1) return -1;
          if (bIndex !== -1) return 1;
        }
        // Default: sort by lowest channel number
        const aMin = channelsByGroup[a.id]?.[0]?.channel_number ?? 9999;
        const bMin = channelsByGroup[b.id]?.[0]?.channel_number ?? 9999;
        return aMin - bMin;
      });
  }, [channelGroups, channelsByGroup, groupOrder]);

  // All groups sorted alphabetically with natural sort (for filter dropdown - includes empty groups)
  const allGroupsSorted = useMemo(() => {
    return [...channelGroups].sort((a, b) => naturalCompare(a.name, b.name));
  }, [channelGroups]);

  // Helper function to determine if a group should be visible based on filter settings
  const shouldShowGroup = (groupId: number): boolean => {
    if (!channelListFilters) return true;

    const groupHasChannels = (channelsByGroup[groupId]?.length ?? 0) > 0;
    const isNewlyCreated = newlyCreatedGroupIds.has(groupId);
    // Check if this group is related to auto_channel_sync (source or target)
    const isAutoSyncRelated = autoSyncRelatedGroups.has(groupId);
    // Note: providerGroupSettings keys are strings from JSON, so we use String(groupId)
    const groupIdStr = String(groupId);
    const isProviderGroup = groupIdStr in (providerSettingsMap ?? {});
    const isManualGroup = !isProviderGroup && !isAutoSyncRelated;

    // Empty group checks
    if (!groupHasChannels) {
      // If showEmptyGroups is off, only show if newly created and showNewlyCreatedGroups is on
      if (!channelListFilters.showEmptyGroups) {
        if (isNewlyCreated && channelListFilters.showNewlyCreatedGroups) {
          // Allow newly created empty groups
        } else {
          return false;
        }
      }
    }

    // Auto channel group filter - applies to groups related to auto_channel_sync
    if (isAutoSyncRelated && !channelListFilters.showAutoChannelGroups) {
      return false;
    }

    // Provider group filter (groups linked to an M3U provider, but not auto-sync related)
    if (isProviderGroup && !isAutoSyncRelated && !channelListFilters.showProviderGroups) {
      return false;
    }

    // Manual group filter (groups not linked to any M3U provider)
    if (isManualGroup && !channelListFilters.showManualGroups) {
      return false;
    }

    return true;
  };

  // Filter sorted channel groups based on filter settings
  const filteredChannelGroups = useMemo(() => {
    return sortedChannelGroups.filter((g) => shouldShowGroup(g.id));
  }, [sortedChannelGroups, channelListFilters, channelsByGroup, newlyCreatedGroupIds, autoSyncRelatedGroups, providerSettingsMap]);

  const handleDragStart = (event: DragStartEvent) => {
    const activeId = event.active.id;
    if (typeof activeId === 'number') {
      setActiveDragId(activeId);
    }
  };

  const handleDragOver = (event: DragOverEvent) => {
    if (!isEditMode) {
      setDropIndicator(null);
      return;
    }

    const { active, over } = event;

    if (!over) {
      setDropIndicator(null);
      return;
    }

    const activeChannel = localChannels.find((c) => c.id === active.id);
    if (!activeChannel) {
      setDropIndicator(null);
      return;
    }

    const overId = String(over.id);

    // If hovering over a group-end drop zone, show indicator at end of that group
    if (overId.startsWith('group-end-')) {
      const targetGroupIdStr = overId.replace('group-end-', '');
      const targetGroupId: number | 'ungrouped' = targetGroupIdStr === 'ungrouped'
        ? 'ungrouped'
        : parseInt(targetGroupIdStr, 10);
      const targetGroupChannels = channelsByGroup[targetGroupId] || [];

      if (targetGroupChannels.length > 0) {
        const lastChannel = targetGroupChannels[targetGroupChannels.length - 1];
        setDropIndicator({
          channelId: lastChannel.id,
          position: 'after',
          groupId: targetGroupId,
          atGroupEnd: true,
        });
      } else {
        setDropIndicator(null);
      }
      return;
    }

    // If hovering over a group header, don't show channel drop indicator
    if (overId.startsWith('group-')) {
      setDropIndicator(null);
      return;
    }

    // Find the channel being hovered over
    const overChannel = localChannels.find((c) => c.id === over.id);
    if (!overChannel) {
      setDropIndicator(null);
      return;
    }

    // Don't show indicator if hovering over the same channel being dragged
    if (activeChannel.id === overChannel.id) {
      setDropIndicator(null);
      return;
    }

    // Determine the group
    const groupId = overChannel.channel_group_id ?? 'ungrouped';

    // Determine if we're in the same group or a different group
    const isSameGroup = activeChannel.channel_group_id === overChannel.channel_group_id;

    // Get group channels to determine position
    const groupChannels = channelsByGroup[groupId] || [];
    const overIndex = groupChannels.findIndex((c) => c.id === over.id);

    if (isSameGroup) {
      // Within same group - determine if dropping before or after based on indices
      const activeIndex = groupChannels.findIndex((c) => c.id === active.id);
      const position = overIndex > activeIndex ? 'after' : 'before';

      setDropIndicator({
        channelId: overChannel.id,
        position,
        groupId,
      });
    } else {
      // Cross-group move - show indicator before the target channel
      setDropIndicator({
        channelId: overChannel.id,
        position: 'before',
        groupId,
      });
    }
  };

  const handleDragEnd = (event: DragEndEvent) => {
    // Clear drag overlay state
    setActiveDragId(null);
    setDropIndicator(null);

    // Block channel reordering when not in edit mode
    if (!isEditMode) return;

    const { active, over } = event;

    if (!over || active.id === over.id) return;

    // Check if this is a group drag (not a channel drag)
    const activeIdStr = String(active.id);
    const overIdStr = String(over.id);

    if (activeIdStr.startsWith('group-') && overIdStr.startsWith('group-')) {
      // Extract group IDs
      const activeGroupId = activeIdStr.replace('group-', '');
      const overGroupId = overIdStr.replace('group-', '');

      // Don't allow reordering ungrouped
      if (activeGroupId === 'ungrouped' || overGroupId === 'ungrouped') return;

      const activeGroupNumId = parseInt(activeGroupId, 10);
      const overGroupNumId = parseInt(overGroupId, 10);

      // If groupOrder is empty, initialize it with current sorted group order
      let currentOrder = groupOrder;
      if (currentOrder.length === 0) {
        currentOrder = sortedChannelGroups.map(g => g.id);
      }

      // Find indices in current order
      const oldIndex = currentOrder.indexOf(activeGroupNumId);
      const newIndex = currentOrder.indexOf(overGroupNumId);

      if (oldIndex !== -1 && newIndex !== -1 && oldIndex !== newIndex) {
        // Calculate the new order to find the preceding group
        const newOrder = arrayMove(currentOrder, oldIndex, newIndex);
        const newPositionInOrder = newOrder.indexOf(activeGroupNumId);

        // Get the group being moved and its channels
        const movedGroup = channelGroups.find(g => g.id === activeGroupNumId);
        const movedGroupChannels = channelsByGroup[activeGroupNumId] || [];

        // Find the preceding group (if any) to calculate suggested starting number
        let precedingGroupName: string | null = null;
        let precedingGroupMaxChannel: number | null = null;
        let suggestedStartingNumber: number | null = null;

        if (newPositionInOrder > 0) {
          const precedingGroupId = newOrder[newPositionInOrder - 1];
          const precedingGroup = channelGroups.find(g => g.id === precedingGroupId);
          const precedingGroupChannels = channelsByGroup[precedingGroupId] || [];

          if (precedingGroup) {
            precedingGroupName = precedingGroup.name;

            // Find the max channel number in the preceding group
            const precedingChannelNumbers = precedingGroupChannels
              .map(ch => ch.channel_number)
              .filter((n): n is number => n !== null);

            if (precedingChannelNumbers.length > 0) {
              precedingGroupMaxChannel = Math.max(...precedingChannelNumbers);
              suggestedStartingNumber = precedingGroupMaxChannel + 1;
            }
          }
        } else {
          // First position - suggest starting at 1
          suggestedStartingNumber = 1;
        }

        // Show the group reorder modal
        setGroupReorderData({
          groupId: activeGroupNumId,
          groupName: movedGroup?.name ?? 'Unknown Group',
          channels: movedGroupChannels,
          newPosition: newPositionInOrder,
          suggestedStartingNumber,
          precedingGroupName,
          precedingGroupMaxChannel,
        });
        setGroupReorderNumberingOption('suggested');
        setGroupReorderCustomNumber(suggestedStartingNumber?.toString() ?? '');
        groupReorderModal.open();

        // Store the pending new order to apply when confirmed
        // We'll use the modal data to track this
      }
      return;
    }

    const activeChannel = localChannels.find((c) => c.id === active.id);
    if (!activeChannel) return;

    // Check if dropped on a group header (cross-group move) or on a channel in a different group
    const overId = String(over.id);
    const overChannel = localChannels.find((c) => c.id === over.id);

    // Determine if this is a cross-group move
    let isCrossGroupMove = false;
    let newGroupId: number | null = null;
    let insertAtChannelNumber: number | null = null;
    let droppedAtGroupEnd = false;

    if (overId.startsWith('group-end-')) {
      // Dropped at the end of a group
      const targetGroupIdStr = overId.replace('group-end-', '');
      newGroupId = targetGroupIdStr === 'ungrouped' ? null : parseInt(targetGroupIdStr, 10);
      droppedAtGroupEnd = true;

      // Get the target group's channels to find the last channel number
      const targetGroupChannels = newGroupId === null
        ? channelsByGroup.ungrouped || []
        : channelsByGroup[newGroupId] || [];

      if (targetGroupChannels.length > 0) {
        const lastChannel = targetGroupChannels[targetGroupChannels.length - 1];
        // For end of group, we'll insert after the last channel
        insertAtChannelNumber = lastChannel.channel_number !== null
          ? lastChannel.channel_number + 1
          : null;
      }

      // Check if it's actually a different group
      if ((newGroupId === null && activeChannel.channel_group_id !== null) ||
          (newGroupId !== null && activeChannel.channel_group_id !== newGroupId)) {
        isCrossGroupMove = true;
      }
    } else if (overId.startsWith('group-')) {
      // Dropped on a group header
      const targetGroupId = overId.replace('group-', '');
      newGroupId = targetGroupId === 'ungrouped' ? null : parseInt(targetGroupId, 10);

      // Check if it's actually a different group
      if ((newGroupId === null && activeChannel.channel_group_id !== null) ||
          (newGroupId !== null && activeChannel.channel_group_id !== newGroupId)) {
        isCrossGroupMove = true;
      }
    } else if (overChannel && overChannel.channel_group_id !== activeChannel.channel_group_id) {
      // Dropped on a channel in a different group
      isCrossGroupMove = true;
      newGroupId = overChannel.channel_group_id;
      // Use the channel number of the drop target as the suggested insertion point
      insertAtChannelNumber = overChannel.channel_number;
    }

    // Additional check: if this is a multi-selection and some selected channels are in different groups
    // than the target, treat this as a cross-group move even if the dragged channel is in the target group
    if (!isCrossGroupMove && selectedChannelIds.has(activeChannel.id) && selectedChannelIds.size > 1) {
      // Determine the effective target group (either from overChannel or group-end target)
      const effectiveTargetGroupId = overChannel?.channel_group_id ?? activeChannel.channel_group_id;
      // Check if any selected channel is in a different group than the target
      const hasChannelsFromOtherGroups = localChannels.some(
        (ch) => selectedChannelIds.has(ch.id) && ch.channel_group_id !== effectiveTargetGroupId
      );
      if (hasChannelsFromOtherGroups) {
        isCrossGroupMove = true;
        newGroupId = effectiveTargetGroupId;
        if (overChannel) {
          insertAtChannelNumber = overChannel.channel_number;
        }
      }
    }

    if (isCrossGroupMove) {
      // Collect channels to move: if the dragged channel is part of multi-selection, move all selected
      // Otherwise, just move the single dragged channel
      let channelsToMove: Channel[] = [];
      if (selectedChannelIds.has(activeChannel.id) && selectedChannelIds.size > 1) {
        // Multi-selection: collect ALL selected channels (from any group, not just the dragged channel's group)
        // Exclude channels that are already in the target group
        channelsToMove = localChannels.filter(
          (ch) => selectedChannelIds.has(ch.id) && ch.channel_group_id !== newGroupId
        );
        // Sort by channel number for consistent ordering
        channelsToMove.sort((a, b) => (a.channel_number ?? 0) - (b.channel_number ?? 0));
      } else {
        channelsToMove = [activeChannel];
      }

      // Get the target group's name for the description
      const targetGroupName = newGroupId === null
        ? 'Uncategorized'
        : channelGroups.find((g) => g.id === newGroupId)?.name ?? 'Unknown Group';

      // Determine source group(s) - could be multiple groups in multi-select
      const sourceGroupIds = new Set(channelsToMove.map(ch => ch.channel_group_id));
      const isMultiSourceGroup = sourceGroupIds.size > 1;
      let sourceGroupName: string;
      if (isMultiSourceGroup) {
        sourceGroupName = 'multiple groups';
      } else {
        const singleSourceGroupId = channelsToMove[0]?.channel_group_id;
        sourceGroupName = singleSourceGroupId === null
          ? 'Uncategorized'
          : channelGroups.find((g) => g.id === singleSourceGroupId)?.name ?? 'Unknown Group';
      }

      // Check if target group is an auto-sync group
      const isTargetAutoSync = newGroupId !== null && autoSyncRelatedGroups.has(newGroupId);

      // Calculate channel number range in target group
      const targetGroupChannels = newGroupId === null
        ? channelsByGroup.ungrouped || []
        : channelsByGroup[newGroupId] || [];

      let minChannelInGroup: number | null = null;
      let maxChannelInGroup: number | null = null;
      let suggestedChannelNumber: number | null = null;

      if (targetGroupChannels.length > 0) {
        const channelNumbers = targetGroupChannels
          .map(ch => ch.channel_number)
          .filter((n): n is number => n !== null)
          .sort((a, b) => a - b);

        if (channelNumbers.length > 0) {
          minChannelInGroup = channelNumbers[0];
          maxChannelInGroup = channelNumbers[channelNumbers.length - 1];

          // If we dropped on a specific channel, suggest that channel's number
          // Otherwise suggest the next number after the max
          if (insertAtChannelNumber !== null) {
            suggestedChannelNumber = insertAtChannelNumber;
          } else {
            suggestedChannelNumber = maxChannelInGroup + 1;
          }
        }
      }

      // Calculate source group info for renumbering option
      // For multi-source-group moves, source renumbering is disabled (too complex)
      let sourceGroupHasGaps = false;
      let sourceGroupMinChannel: number | null = null;
      const sourceGroupId = isMultiSourceGroup ? null : (channelsToMove[0]?.channel_group_id ?? null);

      if (!isMultiSourceGroup) {
        const sourceGroupChannels = sourceGroupId === null
          ? channelsByGroup.ungrouped || []
          : channelsByGroup[sourceGroupId] || [];

        // Get channel numbers from source group (excluding channels being moved)
        const movedChannelIds = new Set(channelsToMove.map(ch => ch.id));
        const remainingSourceChannelNumbers = sourceGroupChannels
          .filter(ch => !movedChannelIds.has(ch.id))
          .map(ch => ch.channel_number)
          .filter((n): n is number => n !== null)
          .sort((a, b) => a - b);

        // Check if there will be gaps after the move
        if (remainingSourceChannelNumbers.length > 1) {
          sourceGroupMinChannel = remainingSourceChannelNumbers[0];
          // Check for gaps in the remaining channels
          for (let i = 1; i < remainingSourceChannelNumbers.length; i++) {
            if (remainingSourceChannelNumbers[i] - remainingSourceChannelNumbers[i - 1] > 1) {
              sourceGroupHasGaps = true;
              break;
            }
          }
        }
      }

      // Show the modal instead of immediately moving
      setCrossGroupMoveData({
        channels: channelsToMove,
        targetGroupId: newGroupId,
        targetGroupName,
        sourceGroupId,
        sourceGroupName,
        isTargetAutoSync,
        suggestedChannelNumber,
        minChannelInGroup,
        maxChannelInGroup,
        insertAtPosition: insertAtChannelNumber !== null,
        sourceGroupHasGaps,
        sourceGroupMinChannel,
      });
      setRenumberSourceGroup(false);  // Reset the checkbox when showing modal
      setSelectedNumberingOption('suggested');  // Default to suggested option
      setCustomStartingNumber('');  // Clear custom number input
      crossGroupMoveModal.open();

      return;
    }

    // Otherwise, this is a within-group reorder
    // Get the group for the channels
    const groupId = activeChannel.channel_group_id ?? 'ungrouped';
    const groupChannels = channelsByGroup[groupId] || [];

    const oldIndex = groupChannels.findIndex((c) => c.id === active.id);

    // Determine the new index: either from the overChannel or end of group
    let newIndex: number;
    if (droppedAtGroupEnd) {
      // Dropped at end of same group - move to the last position
      newIndex = groupChannels.length - 1;
    } else if (overChannel) {
      newIndex = groupChannels.findIndex((c) => c.id === over.id);
    } else {
      return;
    }

    if (oldIndex === -1 || newIndex === -1) return;

    // Check if channels in this group are contiguous (each differs by 1 from the next)
    // Channels are sorted by channel_number, so we check if each consecutive pair differs by 1
    const isContiguous = groupChannels.every((ch, i) => {
      if (i === 0) return true;
      const prev = groupChannels[i - 1];
      const prevNum = prev.channel_number ?? 0;
      const currNum = ch.channel_number ?? 0;
      return currNum - prevNum === 1;
    });

    // Check if this is a multi-selection move within the same group
    const isMultiSelectMove = selectedChannelIds.has(activeChannel.id) && selectedChannelIds.size > 1;

    // Get the selected channels that are in this group, sorted by their current position
    const selectedInGroup = isMultiSelectMove
      ? groupChannels.filter((ch) => selectedChannelIds.has(ch.id))
      : [activeChannel];

    // Reorder locally for immediate feedback
    const reorderedGroup = [...groupChannels];

    if (isMultiSelectMove && selectedInGroup.length > 1) {
      // Multi-select: remove all selected channels first, then insert them at target position
      // Get indices of all selected channels (in reverse order to splice correctly)
      const selectedIndices = selectedInGroup
        .map((ch) => reorderedGroup.findIndex((c) => c.id === ch.id))
        .filter((idx) => idx !== -1)
        .sort((a, b) => b - a); // Sort descending to remove from end first

      // Remove all selected channels
      const removedChannels: Channel[] = [];
      for (const idx of selectedIndices) {
        const [removed] = reorderedGroup.splice(idx, 1);
        removedChannels.unshift(removed); // unshift to maintain original order
      }

      // Calculate the insertion index after removals
      // Count how many selected channels were before the target position
      const selectedBeforeTarget = selectedInGroup.filter((ch) => {
        const chIdx = groupChannels.findIndex((c) => c.id === ch.id);
        return chIdx < newIndex;
      }).length;

      // Adjust target index: subtract the number of selected channels that were before it
      const adjustedNewIndex = Math.min(newIndex - selectedBeforeTarget, reorderedGroup.length);

      // Insert all removed channels at the target position
      reorderedGroup.splice(adjustedNewIndex, 0, ...removedChannels);
    } else {
      // Single channel move
      const [removed] = reorderedGroup.splice(oldIndex, 1);
      reorderedGroup.splice(newIndex, 0, removed);
    }

    if (isContiguous) {
      // Channels are contiguous - renumber them sequentially
      // Use the starting number from the original group (before reorder)
      const startingNumber = groupChannels[0]?.channel_number ?? 1;

      // Calculate new numbers and auto-rename for each channel
      const channelUpdates: Array<{ id: number; newNumber: number; newName?: string; oldName: string }> = [];
      reorderedGroup.forEach((ch, index) => {
        const newNumber = startingNumber + index;
        const newName = autoRenameChannelNumber ? computeAutoRename(ch.name, ch.channel_number, newNumber) : undefined;
        channelUpdates.push({ id: ch.id, newNumber, newName, oldName: ch.name });
      });

      // Update local state immediately (with auto-renamed names)
      const updatedChannels = localChannels.map((ch) => {
        const update = channelUpdates.find((u) => u.id === ch.id);
        if (update) {
          return {
            ...ch,
            channel_number: update.newNumber,
            ...(update.newName ? { name: update.newName } : {}),
          };
        }
        return ch;
      });
      setLocalChannels(updatedChannels);

      // Stage individual updates for channels that need renaming
      if (isEditMode && onStageUpdateChannel) {
        // Start a batch if there are multiple updates
        if (channelUpdates.length > 1 && onStartBatch) {
          onStartBatch(`Reorder channels within group`);
        }

        for (const update of channelUpdates) {
          if (update.newName) {
            // Channel needs both number and name update
            const description = `Changed "${update.oldName}" to "${update.newName}"`;
            onStageUpdateChannel(update.id, { channel_number: update.newNumber, name: update.newName }, description);
          } else {
            // Just number update
            const ch = reorderedGroup.find((c) => c.id === update.id);
            const description = `Changed channel number from ${ch?.channel_number ?? '-'} to ${update.newNumber}`;
            onStageUpdateChannel(update.id, { channel_number: update.newNumber }, description);
          }
        }

        // End the batch
        if (channelUpdates.length > 1 && onEndBatch) {
          onEndBatch();
        }
      } else {
        // Call API to persist the reorder (without auto-rename - server would need to handle it)
        onChannelReorder(
          reorderedGroup.map((c) => c.id),
          startingNumber
        );
      }
    } else {
      // Channels are NOT contiguous - insert and shift channel numbers
      // The dragged channel(s) take the target position, and channels in between shift
      // All channels must have a number, so we can safely use them

      // For multi-select, use the reorderedGroup which already has the correct order
      // and assign new channel numbers based on the new positions while preserving gaps
      if (isMultiSelectMove && selectedInGroup.length > 1) {
        // Multi-select non-contiguous: assign numbers based on new position in reorderedGroup
        // We'll use the existing channel numbers and shift as needed

        // Get channel numbers from reorderedGroup to determine new assignments
        // Find where the selected channels ended up and what numbers they should get
        const selectedIds = new Set(selectedInGroup.map(ch => ch.id));

        // Calculate updates based on new positions
        const channelUpdates: Array<{ id: number; oldNumber: number; newNumber: number; oldName: string; newName?: string }> = [];

        // Get the existing channel numbers and their positions
        const existingNumbers = groupChannels
          .map(ch => ch.channel_number!)
          .sort((a, b) => a - b);

        // Assign numbers based on the new order in reorderedGroup
        reorderedGroup.forEach((ch, index) => {
          const oldNumber = ch.channel_number!;
          const newNumber = existingNumbers[index];

          if (oldNumber !== newNumber) {
            const newName = autoRenameChannelNumber ? computeAutoRename(ch.name, oldNumber, newNumber) : undefined;
            channelUpdates.push({
              id: ch.id,
              oldNumber,
              newNumber,
              oldName: ch.name,
              newName,
            });
          }
        });

        // Update local state with new numbers and names
        const updatedChannels = localChannels.map((ch) => {
          const update = channelUpdates.find((u) => u.id === ch.id);
          if (update) {
            return {
              ...ch,
              channel_number: update.newNumber,
              ...(update.newName ? { name: update.newName } : {}),
            };
          }
          return ch;
        });
        setLocalChannels(updatedChannels);

        // Stage updates for all affected channels
        if (isEditMode && onStageUpdateChannel) {
          if (channelUpdates.length > 1 && onStartBatch) {
            onStartBatch(`Move ${selectedInGroup.length} channels`);
          }

          for (const update of channelUpdates) {
            const updateData: { channel_number: number; name?: string } = { channel_number: update.newNumber };
            let description: string;

            if (update.newName) {
              updateData.name = update.newName;
              description = `Changed "${update.oldName}" to "${update.newName}"`;
            } else {
              description = `Changed channel number from ${update.oldNumber} to ${update.newNumber}`;
            }

            onStageUpdateChannel(update.id, updateData, description);
          }

          if (channelUpdates.length > 1 && onEndBatch) {
            onEndBatch();
          }
        }
        return;
      }

      // Single channel move in non-contiguous group
      const activeNum = activeChannel.channel_number!;

      // For group end drop, use the last channel's number + 1
      // Otherwise use the overChannel's number
      let overNum: number;
      if (droppedAtGroupEnd) {
        const lastChannel = groupChannels[groupChannels.length - 1];
        overNum = (lastChannel?.channel_number ?? 0) + 1;
      } else if (overChannel) {
        overNum = overChannel.channel_number!;
      } else {
        return;
      }

      // Determine direction and range
      const movingDown = overNum > activeNum;
      const minNum = Math.min(activeNum, overNum);
      const maxNum = Math.max(activeNum, overNum);

      // Get all channels in this group that are within the affected range
      // All channels must have a number
      const affectedChannels = groupChannels.filter((ch) => {
        const num = ch.channel_number!;
        return num >= minNum && num <= maxNum;
      });

      // Calculate new numbers for each affected channel
      const channelUpdates: Array<{ id: number; oldNumber: number; newNumber: number; oldName: string; newName?: string }> = [];

      for (const ch of affectedChannels) {
        const chNum = ch.channel_number!;
        let newNumber: number;

        if (ch.id === activeChannel.id) {
          // The dragged channel moves to the target position
          newNumber = overNum;
        } else if (movingDown) {
          // Moving down: channels in range shift up by 1 (toward smaller numbers)
          newNumber = chNum - 1;
        } else {
          // Moving up: channels in range shift down by 1 (toward larger numbers)
          newNumber = chNum + 1;
        }

        const newName = autoRenameChannelNumber ? computeAutoRename(ch.name, chNum, newNumber) : undefined;
        channelUpdates.push({
          id: ch.id,
          oldNumber: chNum,
          newNumber,
          oldName: ch.name,
          newName,
        });
      }

      // Update local state with new numbers and names
      const updatedChannels = localChannels.map((ch) => {
        const update = channelUpdates.find((u) => u.id === ch.id);
        if (update) {
          return {
            ...ch,
            channel_number: update.newNumber,
            ...(update.newName ? { name: update.newName } : {}),
          };
        }
        return ch;
      });
      setLocalChannels(updatedChannels);

      // Stage updates for all affected channels
      if (isEditMode && onStageUpdateChannel) {
        // Start a batch if there are multiple updates
        if (channelUpdates.length > 1 && onStartBatch) {
          onStartBatch(`Move channel and shift others`);
        }

        for (const update of channelUpdates) {
          const updateData: { channel_number: number; name?: string } = { channel_number: update.newNumber };
          let description: string;

          if (update.newName) {
            updateData.name = update.newName;
            description = `Changed "${update.oldName}" to "${update.newName}"`;
          } else {
            description = `Changed channel number from ${update.oldNumber} to ${update.newNumber}`;
          }

          onStageUpdateChannel(update.id, updateData, description);
        }

        // End the batch
        if (channelUpdates.length > 1 && onEndBatch) {
          onEndBatch();
        }
      } else {
        // Normal mode - update via API (this path shouldn't happen since we block non-edit mode earlier)
        for (const update of channelUpdates) {
          const ch = localChannels.find((c) => c.id === update.id);
          if (ch) {
            const updateData: Partial<Channel> = { channel_number: update.newNumber };
            if (update.newName) updateData.name = update.newName;
            const description = update.newName
              ? `Changed "${update.oldName}" to "${update.newName}"`
              : `Changed channel number from ${update.oldNumber} to ${update.newNumber}`;
            onChannelUpdate({ ...ch, ...updateData }, { type: 'channel_number_update', description });
          }
        }
      }
    }
  };

  // Handle group reorder confirmation
  const handleGroupReorderConfirm = () => {
    if (!groupReorderData) return;

    const { groupId, channels, newPosition } = groupReorderData;

    // First, apply the group reorder
    let currentOrder = groupOrder;
    if (currentOrder.length === 0) {
      currentOrder = sortedChannelGroups.map(g => g.id);
    }

    const oldIndex = currentOrder.indexOf(groupId);
    if (oldIndex !== -1) {
      // We need to calculate the new order based on newPosition
      // Remove from old position
      const withoutGroup = currentOrder.filter(id => id !== groupId);
      // Insert at new position
      const newOrder = [
        ...withoutGroup.slice(0, newPosition),
        groupId,
        ...withoutGroup.slice(newPosition),
      ];
      setGroupOrder(newOrder);
    }

    // Then, renumber channels if requested
    if (groupReorderNumberingOption !== 'keep' && channels.length > 0) {
      let startingNumber: number;

      if (groupReorderNumberingOption === 'custom') {
        startingNumber = parseInt(groupReorderCustomNumber, 10);
        if (isNaN(startingNumber)) {
          startingNumber = groupReorderData.suggestedStartingNumber ?? 1;
        }
      } else {
        startingNumber = groupReorderData.suggestedStartingNumber ?? 1;
      }

      // Sort channels by current channel number to maintain relative order
      const sortedChannels = [...channels].sort((a, b) =>
        (a.channel_number ?? 0) - (b.channel_number ?? 0)
      );

      // Use batch operation for renumbering
      startBatch(`Renumber "${groupReorderData.groupName}" starting at ${startingNumber}`);

      sortedChannels.forEach((channel, index) => {
        const newNumber = startingNumber + index;
        if (channel.channel_number !== newNumber) {
          stageUpdateChannel(
            channel.id,
            { channel_number: newNumber },
            `Renumber "${channel.name}" to ${newNumber}`
          );
        }
      });

      endBatch();
    }

    // Close modal and reset state
    groupReorderModal.close();
    setGroupReorderData(null);
    setGroupReorderNumberingOption('suggested');
    setGroupReorderCustomNumber('');
  };

  // Handle group reorder cancel
  const handleGroupReorderCancel = () => {
    groupReorderModal.close();
    setGroupReorderData(null);
    setGroupReorderNumberingOption('suggested');
    setGroupReorderCustomNumber('');
  };

  // Handle cross-group move confirmation (supports multiple channels)
  const handleCrossGroupMoveConfirm = (keepChannelNumber: boolean, startingChannelNumber?: number, shouldRenumberSource?: boolean) => {
    if (!crossGroupMoveData) return;

    const { channels: channelsToMove, targetGroupId, targetGroupName, sourceGroupId, sourceGroupName, sourceGroupMinChannel } = crossGroupMoveData;

    // Build updates for moved channels
    const channelUpdates: Array<{
      channel: Channel;
      finalChannelNumber: number | null;
      finalName: string;
    }> = [];

    // Build updates for existing channels that need to be shifted (target group)
    const shiftUpdates: Array<{
      channel: Channel;
      finalChannelNumber: number;
      finalName: string;
    }> = [];

    // Build updates for source group renumbering
    const sourceRenumberUpdates: Array<{
      channel: Channel;
      finalChannelNumber: number;
      finalName: string;
    }> = [];

    // Check if we need to shift existing channels to avoid duplicates
    // This applies when assigning new numbers (not keeping current) and any moved channel
    // would conflict with an existing channel in the target group
    if (!keepChannelNumber && startingChannelNumber !== undefined) {
      // Get existing channels in the target group
      const targetGroupChannels = localChannels.filter((ch) => {
        if (targetGroupId === null) {
          return ch.channel_group_id === null;
        }
        return ch.channel_group_id === targetGroupId;
      });

      // Calculate the range of channel numbers that will be used by the moved channels
      const movedRangeStart = startingChannelNumber;
      const movedRangeEnd = startingChannelNumber + channelsToMove.length - 1;

      // Find channels that would conflict (their number falls within the moved range)
      const conflictingChannels = targetGroupChannels.filter((ch) => {
        return ch.channel_number !== null &&
               ch.channel_number >= movedRangeStart &&
               ch.channel_number <= movedRangeEnd;
      });

      // If there are conflicts, we need to shift existing channels
      if (conflictingChannels.length > 0) {
        // Find all channels at or after the insertion point that need to be shifted
        const channelsToShift = targetGroupChannels.filter((ch) => {
          return ch.channel_number !== null && ch.channel_number >= startingChannelNumber;
        });

        // Sort by channel number to process in order
        channelsToShift.sort((a, b) => (a.channel_number ?? 0) - (b.channel_number ?? 0));

        // Shift each channel up by the number of channels being inserted
        const shiftAmount = channelsToMove.length;
        for (const channel of channelsToShift) {
          const newNumber = (channel.channel_number ?? 0) + shiftAmount;
          let finalName = channel.name;

          // Apply auto-rename if enabled
          if (autoRenameChannelNumber) {
            const newName = computeAutoRename(channel.name, channel.channel_number, newNumber);
            if (newName) {
              finalName = newName;
            }
          }

          shiftUpdates.push({ channel, finalChannelNumber: newNumber, finalName });
        }
      }
    }

    // Handle source group renumbering (close gaps)
    if (shouldRenumberSource && sourceGroupMinChannel !== null) {
      // Get remaining channels in source group (excluding those being moved)
      const movedChannelIds = new Set(channelsToMove.map(ch => ch.id));
      const remainingSourceChannels = localChannels
        .filter((ch) => {
          if (sourceGroupId === null) {
            return ch.channel_group_id === null && !movedChannelIds.has(ch.id);
          }
          return ch.channel_group_id === sourceGroupId && !movedChannelIds.has(ch.id);
        })
        .filter(ch => ch.channel_number !== null)
        .sort((a, b) => (a.channel_number ?? 0) - (b.channel_number ?? 0));

      // Renumber sequentially starting from the original minimum
      remainingSourceChannels.forEach((channel, index) => {
        const newNumber = sourceGroupMinChannel + index;
        if (newNumber !== channel.channel_number) {
          let finalName = channel.name;
          if (autoRenameChannelNumber) {
            const newName = computeAutoRename(channel.name, channel.channel_number, newNumber);
            if (newName) {
              finalName = newName;
            }
          }
          sourceRenumberUpdates.push({ channel, finalChannelNumber: newNumber, finalName });
        }
      });
    }

    channelsToMove.forEach((channel, index) => {
      // Determine the final channel number
      let finalChannelNumber = channel.channel_number;
      if (!keepChannelNumber && startingChannelNumber !== undefined) {
        // Assign sequential numbers starting from the suggested number
        finalChannelNumber = startingChannelNumber + index;
      }

      // Check if auto-rename applies when changing channel number
      let finalName = channel.name;
      if (autoRenameChannelNumber && !keepChannelNumber && finalChannelNumber !== null && finalChannelNumber !== channel.channel_number) {
        const newName = computeAutoRename(channel.name, channel.channel_number, finalChannelNumber);
        if (newName) {
          finalName = newName;
        }
      }

      channelUpdates.push({ channel, finalChannelNumber, finalName });
    });

    // Update local state immediately (moved, shifted, and source-renumbered channels)
    const updatedChannels = localChannels.map((ch) => {
      // Check if this is a moved channel
      const moveUpdate = channelUpdates.find((u) => u.channel.id === ch.id);
      if (moveUpdate) {
        return {
          ...ch,
          channel_group_id: targetGroupId,
          channel_number: moveUpdate.finalChannelNumber,
          name: moveUpdate.finalName,
        };
      }

      // Check if this is a shifted channel (target group)
      const shiftUpdate = shiftUpdates.find((u) => u.channel.id === ch.id);
      if (shiftUpdate) {
        return {
          ...ch,
          channel_number: shiftUpdate.finalChannelNumber,
          name: shiftUpdate.finalName,
        };
      }

      // Check if this is a source group renumbered channel
      const sourceUpdate = sourceRenumberUpdates.find((u) => u.channel.id === ch.id);
      if (sourceUpdate) {
        return {
          ...ch,
          channel_number: sourceUpdate.finalChannelNumber,
          name: sourceUpdate.finalName,
        };
      }

      return ch;
    });
    setLocalChannels(updatedChannels);

    // Stage the changes in separate batches for each logical phase:
    // 1. Move channels to target group (with new numbers if applicable)
    // 2. Shift existing channels in target group (if inserting at position)
    // 3. Renumber remaining channels in source group (if closing gaps)
    if (onStageUpdateChannel) {
      const channelNames = channelsToMove.length <= 3
        ? channelsToMove.map(ch => ch.name).join(', ')
        : `${channelsToMove.length} channels`;

      // Batch 1: Move the channels to target group
      if (channelUpdates.length > 1 && onStartBatch) {
        onStartBatch(`Move ${channelNames} to "${targetGroupName}"`);
      }

      for (const update of channelUpdates) {
        const { channel, finalChannelNumber, finalName } = update;
        const updates: Partial<Channel> = { channel_group_id: targetGroupId };
        let description = `Moved "${channel.name}" from "${sourceGroupName}" to "${targetGroupName}"`;

        if (!keepChannelNumber && finalChannelNumber !== null && finalChannelNumber !== channel.channel_number) {
          updates.channel_number = finalChannelNumber;
          description += ` (channel ${channel.channel_number ?? '-'} → ${finalChannelNumber})`;

          // Include name update if auto-rename applied
          if (finalName !== channel.name) {
            updates.name = finalName;
            description += `, renamed to "${finalName}"`;
          }
        }

        onStageUpdateChannel(channel.id, updates, description);
      }

      if (channelUpdates.length > 1 && onEndBatch) {
        onEndBatch();
      }

      // Batch 2: Shift existing channels in target group
      if (shiftUpdates.length > 0) {
        if (shiftUpdates.length > 1 && onStartBatch) {
          onStartBatch(`Shift ${shiftUpdates.length} channels in "${targetGroupName}"`);
        }

        for (const update of shiftUpdates) {
          const { channel, finalChannelNumber, finalName } = update;
          const updates: Partial<Channel> = { channel_number: finalChannelNumber };
          let description = `Shifted "${channel.name}" from channel ${channel.channel_number} to ${finalChannelNumber}`;

          if (finalName !== channel.name) {
            updates.name = finalName;
            description += `, renamed to "${finalName}"`;
          }

          onStageUpdateChannel(channel.id, updates, description);
        }

        if (shiftUpdates.length > 1 && onEndBatch) {
          onEndBatch();
        }
      }

      // Batch 3: Renumber remaining channels in source group
      if (sourceRenumberUpdates.length > 0) {
        if (sourceRenumberUpdates.length > 1 && onStartBatch) {
          onStartBatch(`Renumber ${sourceRenumberUpdates.length} channels in "${sourceGroupName}"`);
        }

        for (const update of sourceRenumberUpdates) {
          const { channel, finalChannelNumber, finalName } = update;
          const updates: Partial<Channel> = { channel_number: finalChannelNumber };
          let description = `Renumbered "${channel.name}" in "${sourceGroupName}" from ${channel.channel_number} to ${finalChannelNumber}`;

          if (finalName !== channel.name) {
            updates.name = finalName;
            description += `, renamed to "${finalName}"`;
          }

          onStageUpdateChannel(channel.id, updates, description);
        }

        if (sourceRenumberUpdates.length > 1 && onEndBatch) {
          onEndBatch();
        }
      }
    }

    // Clear multi-selection after move
    if (onClearChannelSelection) {
      onClearChannelSelection();
    }

    // Close modal
    crossGroupMoveModal.close();
    setCrossGroupMoveData(null);
  };

  const handleCrossGroupMoveCancel = () => {
    crossGroupMoveModal.close();
    setCrossGroupMoveData(null);
    setCustomStartingNumber('');
  };

  // Handle the Move button click based on selected option
  const handleMoveButtonClick = () => {
    if (!crossGroupMoveData) return;

    switch (selectedNumberingOption) {
      case 'keep':
        handleCrossGroupMoveConfirm(true, undefined, renumberSourceGroup);
        break;
      case 'suggested':
        if (crossGroupMoveData.suggestedChannelNumber !== null) {
          handleCrossGroupMoveConfirm(false, crossGroupMoveData.suggestedChannelNumber, renumberSourceGroup);
        }
        break;
      case 'custom':
        const customNum = parseInt(customStartingNumber, 10);
        if (!isNaN(customNum) && customNum >= 1) {
          handleCrossGroupMoveConfirm(false, customNum, renumberSourceGroup);
        }
        break;
    }
  };

  // Compute conflicts for cross-group move based on selected numbering option
  const getMoveConflicts = useMemo(() => {
    if (!crossGroupMoveData) return { hasConflicts: false, conflicts: [], startNumber: 0 };

    const { channels: channelsToMove, targetGroupId } = crossGroupMoveData;

    // Get the starting number based on selected option
    let startNumber: number | null = null;
    if (selectedNumberingOption === 'keep') {
      // When keeping numbers, check each channel individually for conflicts
      const keptNumbers = channelsToMove.map(ch => ch.channel_number).filter((n): n is number => n !== null);
      if (keptNumbers.length === 0) return { hasConflicts: false, conflicts: [], startNumber: 0 };

      // Get existing channels in target group (excluding the ones being moved)
      const movedIds = new Set(channelsToMove.map(ch => ch.id));
      const targetGroupChannels = localChannels.filter(ch => {
        if (movedIds.has(ch.id)) return false;
        if (targetGroupId === null) return ch.channel_group_id === null;
        return ch.channel_group_id === targetGroupId;
      });

      // Find conflicts for "keep current" option
      const conflicts = targetGroupChannels.filter(ch =>
        ch.channel_number !== null && keptNumbers.includes(ch.channel_number)
      ).sort((a, b) => (a.channel_number ?? 0) - (b.channel_number ?? 0));

      return { hasConflicts: conflicts.length > 0, conflicts, startNumber: null };
    } else if (selectedNumberingOption === 'suggested') {
      startNumber = crossGroupMoveData.suggestedChannelNumber;
    } else if (selectedNumberingOption === 'custom') {
      const customNum = parseInt(customStartingNumber, 10);
      if (!isNaN(customNum) && customNum >= 1) {
        startNumber = customNum;
      }
    }

    if (startNumber === null) return { hasConflicts: false, conflicts: [], startNumber: 0 };

    // Get existing channels in target group (excluding the ones being moved)
    const movedIds = new Set(channelsToMove.map(ch => ch.id));
    const targetGroupChannels = localChannels.filter(ch => {
      if (movedIds.has(ch.id)) return false;
      if (targetGroupId === null) return ch.channel_group_id === null;
      return ch.channel_group_id === targetGroupId;
    });

    // Calculate the range of numbers that will be used
    const endNumber = startNumber + channelsToMove.length - 1;

    // Find channels with numbers in this range
    const conflicts = targetGroupChannels.filter(ch =>
      ch.channel_number !== null &&
      ch.channel_number >= startNumber! &&
      ch.channel_number <= endNumber
    ).sort((a, b) => (a.channel_number ?? 0) - (b.channel_number ?? 0));

    return { hasConflicts: conflicts.length > 0, conflicts, startNumber };
  }, [crossGroupMoveData, selectedNumberingOption, customStartingNumber, localChannels]);

  // Check if Move button should be enabled
  const isMoveButtonEnabled = () => {
    if (selectedNumberingOption === 'custom') {
      const customNum = parseInt(customStartingNumber, 10);
      return !isNaN(customNum) && customNum >= 1;
    }
    return true;
  };

  // Sort & Renumber handlers
  const handleOpenSortRenumber = (groupId: number | 'ungrouped', groupName: string, groupChannels: Channel[]) => {
    const channelNumbers = groupChannels
      .map((ch) => ch.channel_number)
      .filter((n): n is number => n !== null);
    const minNumber = channelNumbers.length > 0 ? Math.min(...channelNumbers) : null;

    setSortRenumberData({
      groupId,
      groupName,
      channels: groupChannels,
      currentMinNumber: minNumber,
    });
    setSortRenumberStartingNumber(minNumber !== null ? String(minNumber) : '1');
    sortRenumberModal.open();
  };

  const handleSortRenumberCancel = () => {
    sortRenumberModal.close();
    setSortRenumberData(null);
    setSortRenumberStartingNumber('');
    setSortStripNumbers(true);
    setSortIgnoreCountry(false);
  };

  const handleSortRenumberConfirm = () => {
    if (!sortRenumberData || !onStageUpdateChannel) return;

    const startingNumber = parseInt(sortRenumberStartingNumber, 10);
    if (isNaN(startingNumber) || startingNumber < 1) return;

    // Sort channels alphabetically by name (case-insensitive, natural sort for numbers)
    // Apply optional transformations for sorting
    const sortedChannels = [...sortRenumberData.channels].sort((a, b) => {
      let nameA = a.name;
      let nameB = b.name;
      // Strip channel numbers if enabled
      if (sortStripNumbers) {
        nameA = getNameForSorting(nameA);
        nameB = getNameForSorting(nameB);
      }
      // Strip country prefix if enabled
      if (sortIgnoreCountry) {
        nameA = stripCountryPrefix(nameA);
        nameB = stripCountryPrefix(nameB);
      }
      return naturalCompare(nameA.toLowerCase(), nameB.toLowerCase());
    });

    // Start a batch for the entire operation
    if (sortedChannels.length > 1 && onStartBatch) {
      onStartBatch(`Sort and renumber ${sortedChannels.length} channels in "${sortRenumberData.groupName}"`);
    }

    // Renumber each channel
    sortedChannels.forEach((channel, index) => {
      const newNumber = startingNumber + index;
      if (channel.channel_number !== newNumber) {
        // Apply auto-rename if enabled in dialog
        let updates: Partial<Channel> = { channel_number: newNumber };
        if (sortRenumberUpdateNames && channel.channel_number !== null) {
          const newName = computeAutoRename(channel.name, channel.channel_number, newNumber);
          if (newName && newName !== channel.name) {
            updates.name = newName;
          }
        }
        const description = updates.name
          ? `Renumber and rename "${channel.name}" to ch.${newNumber} "${updates.name}"`
          : `Renumber "${channel.name}" to ch.${newNumber}`;
        onStageUpdateChannel(channel.id, updates, description);
      }
    });

    if (sortedChannels.length > 1 && onEndBatch) {
      onEndBatch();
    }

    // Close modal
    sortRenumberModal.close();
    setSortRenumberData(null);
    setSortRenumberStartingNumber('');
    setSortStripNumbers(true);
    setSortIgnoreCountry(false);
    setSortRenumberUpdateNames(true);
  };

  // Mass Renumber handlers
  const handleMassRenumberClick = () => {
    // Get selected channels, sorted by current channel number
    const channelsToRenumber = localChannels
      .filter(ch => selectedChannelIds.has(ch.id))
      .sort((a, b) => (a.channel_number ?? 9999) - (b.channel_number ?? 9999));

    if (channelsToRenumber.length === 0) return;

    // Default starting number: minimum of selected channel numbers, or 1
    const minNumber = channelsToRenumber
      .map(ch => ch.channel_number)
      .filter((n): n is number => n !== null)
      .sort((a, b) => a - b)[0] ?? 1;

    setMassRenumberChannels(channelsToRenumber);
    setMassRenumberStartingNumber(String(minNumber));
    massRenumberModal.open();
  };

  // Calculate conflicts for mass renumber
  const getMassRenumberConflicts = useMemo(() => {
    if (!massRenumberModal.isOpen || massRenumberChannels.length === 0) {
      return { hasConflicts: false, conflicts: [] as Channel[], shiftRequired: 0 };
    }

    const startNum = parseInt(massRenumberStartingNumber, 10);
    if (isNaN(startNum) || startNum < 1) {
      return { hasConflicts: false, conflicts: [] as Channel[], shiftRequired: 0 };
    }

    const endNum = startNum + massRenumberChannels.length - 1;
    const renumberingIds = new Set(massRenumberChannels.map(ch => ch.id));

    // Find all channels NOT being renumbered that have numbers in the target range
    const conflicts = localChannels.filter(ch =>
      !renumberingIds.has(ch.id) &&
      ch.channel_number !== null &&
      ch.channel_number >= startNum &&
      ch.channel_number <= endNum
    ).sort((a, b) => (a.channel_number ?? 0) - (b.channel_number ?? 0));

    // Calculate how much to shift: move conflicting channels past the end of renumbered range
    const shiftRequired = conflicts.length > 0 ? endNum - (conflicts[0].channel_number ?? 0) + 1 : 0;

    return { hasConflicts: conflicts.length > 0, conflicts, shiftRequired };
  }, [massRenumberModal.isOpen, massRenumberChannels, massRenumberStartingNumber, localChannels]);

  const handleMassRenumberConfirm = (shiftConflicts: boolean) => {
    if (!onStageUpdateChannel || massRenumberChannels.length === 0) return;

    const startNum = parseInt(massRenumberStartingNumber, 10);
    if (isNaN(startNum) || startNum < 1) return;

    const { conflicts } = getMassRenumberConflicts;

    // Start batch
    if ((massRenumberChannels.length + (shiftConflicts ? conflicts.length : 0)) > 1 && onStartBatch) {
      onStartBatch(`Renumber ${massRenumberChannels.length} channel${massRenumberChannels.length !== 1 ? 's' : ''} starting at ${startNum}`);
    }

    // If shifting conflicts, do that first (shift UP)
    if (shiftConflicts && conflicts.length > 0) {
      const endNum = startNum + massRenumberChannels.length - 1;
      // Shift conflicting channels to start after the renumbered range
      // Process from highest to lowest number to avoid intermediate collisions
      const sortedConflicts = [...conflicts].sort((a, b) => (b.channel_number ?? 0) - (a.channel_number ?? 0));

      sortedConflicts.forEach((channel, index) => {
        // New number = endNum + 1 + (position from the end of conflicts)
        const newNumber = endNum + 1 + (sortedConflicts.length - 1 - index);
        let updates: Partial<Channel> = { channel_number: newNumber };

        // Apply auto-rename if enabled in the dialog
        if (massRenumberUpdateNames && channel.channel_number !== null) {
          const newName = computeAutoRename(channel.name, channel.channel_number, newNumber);
          if (newName && newName !== channel.name) {
            updates.name = newName;
          }
        }

        const description = updates.name
          ? `Shift "${channel.name}" → "${updates.name}" to ch.${newNumber}`
          : `Shift ch.${channel.channel_number} → ${newNumber}`;
        onStageUpdateChannel(channel.id, updates, description);
      });
    }

    // Now renumber the selected channels
    massRenumberChannels.forEach((channel, index) => {
      const newNumber = startNum + index;
      if (channel.channel_number !== newNumber) {
        let updates: Partial<Channel> = { channel_number: newNumber };

        // Apply auto-rename if enabled in the dialog
        if (massRenumberUpdateNames && channel.channel_number !== null) {
          const newName = computeAutoRename(channel.name, channel.channel_number, newNumber);
          if (newName && newName !== channel.name) {
            updates.name = newName;
          }
        }

        const description = updates.name
          ? `Renumber "${channel.name}" → "${updates.name}" to ch.${newNumber}`
          : `Renumber ch.${channel.channel_number ?? '?'} → ${newNumber}`;
        onStageUpdateChannel(channel.id, updates, description);
      }
    });

    // End batch
    if ((massRenumberChannels.length + (shiftConflicts ? conflicts.length : 0)) > 1 && onEndBatch) {
      onEndBatch();
    }

    // Close modal and clear selection
    massRenumberModal.close();
    setMassRenumberChannels([]);
    setMassRenumberStartingNumber('');
    if (onClearChannelSelection) {
      onClearChannelSelection();
    }
  };

  const handleMassRenumberCancel = () => {
    massRenumberModal.close();
    setMassRenumberChannels([]);
    setMassRenumberStartingNumber('');
    setMassRenumberUpdateNames(true); // Reset to default
  };

  const renderGroup = (groupId: number | 'ungrouped', groupName: string, groupChannels: Channel[], isEmpty: boolean = false) => {
    // Only show empty groups if explicitly marked (selected in filter or newly created)
    if (groupChannels.length === 0 && !isEmpty) return null;
    // Only show groups that are in selectedGroups (or ungrouped which is always shown if it has channels)
    if (groupId !== 'ungrouped' && !selectedGroups.includes(groupId as number)) return null;

    const numericGroupId = groupId === 'ungrouped' ? -1 : groupId;
    const isExpanded = expandedGroups[numericGroupId] === true;
    const isAutoSync = groupId !== 'ungrouped' && autoSyncRelatedGroups.has(groupId);

    // Determine if this is a manual group (not linked to any M3U provider and not auto-sync related)
    const groupIdStr = String(groupId);
    const isProviderGroup = groupId !== 'ungrouped' && groupIdStr in (providerSettingsMap ?? {});
    const isManualGroup = groupId !== 'ungrouped' && !isProviderGroup && !isAutoSync;

    // Find the group object for deletion
    const group = groupId !== 'ungrouped' ? channelGroups.find(g => g.id === groupId) : null;

    // Calculate how many channels in this group are selected
    const selectedCountInGroup = groupChannels.filter(ch => selectedChannelIds.has(ch.id)).length;
    const allGroupChannelIds = groupChannels.map(ch => ch.id);

    // Calculate channel number range for this group
    const channelNumbers = groupChannels
      .map(ch => ch.channel_number)
      .filter((num): num is number => num !== null && num !== undefined);
    const channelRange = channelNumbers.length > 0
      ? { min: Math.min(...channelNumbers), max: Math.max(...channelNumbers) }
      : null;

    // Handler to select/deselect all channels in this group
    const handleSelectAllInGroup = () => {
      if (!onSelectGroupChannels) return;
      const allSelected = selectedCountInGroup === groupChannels.length;
      // If all are selected, deselect all; otherwise select all
      onSelectGroupChannels(allGroupChannelIds, !allSelected);
    };

    // Count channels in this group that have failed streams
    const groupFailedChannelCount = groupChannels.filter(channel =>
      channel.streams.some(streamId => {
        const stats = streamStatsMap.get(streamId);
        return stats && (stats.probe_status === 'failed' || stats.probe_status === 'timeout');
      })
    ).length;

    return (
      <div key={groupId} className={`channel-group ${isEmpty ? 'empty-group' : ''}`}>
        <SortableGroupHeader
          groupId={groupId}
          groupName={groupName}
          channelCount={groupChannels.length}
          channelRange={channelRange}
          isEmpty={isEmpty}
          isExpanded={isExpanded}
          isEditMode={isEditMode}
          isAutoSync={isAutoSync}
          isManualGroup={isManualGroup}
          selectedCount={selectedCountInGroup}
          onToggle={() => toggleGroup(numericGroupId)}
          onSortAndRenumber={() => handleOpenSortRenumber(groupId, groupName, groupChannels)}
          onDeleteGroup={group ? () => handleDeleteGroupClick(group) : undefined}
          onSelectAll={handleSelectAllInGroup}
          onStreamDropOnGroup={handleStreamDropOnGroup}
          onContextMenu={(e) => handleGroupContextMenu(groupChannels.map(ch => ch.id), e)}
          onProbeGroup={() => handleProbeGroup(groupId, groupName)}
          isProbing={probingGroups.has(groupId)}
          onSortStreamsByQuality={() => handleSortGroupStreamsByQuality(groupId)}
          onSortStreamsByMode={(mode) => handleSortGroupStreamsByMode(groupId, mode)}
          isSortingByQuality={bulkSortingByQuality}
          enabledCriteria={channelDefaults?.streamSortEnabled}
          failedChannelCount={groupFailedChannelCount}
        />
        {isExpanded && isEmpty && (
          <div className="group-channels empty-group-placeholder">
            <div className="empty-group-message">
              No channels in this group. Drag a channel here or create a new one.
            </div>
          </div>
        )}
        {isExpanded && !isEmpty && (
          <>
            <SortableContext
              items={groupChannels.map((c) => c.id)}
              strategy={verticalListSortingStrategy}
            >
              <div className="group-channels">
                {groupChannels.map((channel) => {
                  // Check if drop indicator should show before this channel
                  const showIndicatorBefore = dropIndicator &&
                    dropIndicator.channelId === channel.id &&
                    dropIndicator.position === 'before' &&
                    dropIndicator.groupId === groupId;
                  // Check if drop indicator should show after this channel
                  const showIndicatorAfter = dropIndicator &&
                    dropIndicator.channelId === channel.id &&
                    dropIndicator.position === 'after' &&
                    dropIndicator.groupId === groupId;
                  // Check if stream insert indicator should show before this channel
                  const showStreamInsertBefore = streamInsertIndicator &&
                    streamInsertIndicator.channelId === channel.id &&
                    streamInsertIndicator.position === 'before' &&
                    streamInsertIndicator.groupId === groupId;

                  return (
                  <div key={channel.id} className="channel-wrapper">
                    {/* Stream insert drop zone - visible when dragging streams in edit mode */}
                    {isEditMode && (
                      <div
                        className={`stream-insert-zone ${showStreamInsertBefore ? 'active' : ''}`}
                        onDragOver={(e) => {
                          const types = e.dataTransfer.types.map(t => t.toLowerCase());
                          if (types.includes('streamid') || types.includes('streamids')) {
                            e.preventDefault();
                            e.stopPropagation();
                            // Set indicator for this position
                            if (channel.channel_number !== null) {
                              setStreamInsertIndicator({
                                channelId: channel.id,
                                position: 'before',
                                groupId,
                                channelNumber: channel.channel_number,
                              });
                            }
                          }
                        }}
                        onDragLeave={(e) => {
                          e.stopPropagation();
                          // Only clear if not entering another insert zone
                          const relatedTarget = e.relatedTarget as HTMLElement;
                          if (!relatedTarget?.classList?.contains('stream-insert-zone')) {
                            setStreamInsertIndicator(null);
                          }
                        }}
                        onDrop={(e) => {
                          e.preventDefault();
                          e.stopPropagation();
                          setStreamInsertIndicator(null);

                          // Get stream IDs
                          const streamIdsJson = e.dataTransfer.getData('streamIds');
                          const streamId = e.dataTransfer.getData('streamId');
                          let streamIds: number[] = [];

                          if (streamIdsJson) {
                            try {
                              streamIds = JSON.parse(streamIdsJson) as number[];
                            } catch {
                              // Fall through to single stream
                            }
                          }
                          if (streamIds.length === 0 && streamId) {
                            streamIds = [parseInt(streamId, 10)];
                          }

                          if (streamIds.length > 0 && channel.channel_number !== null) {
                            handleStreamDropAtPosition(groupId, streamIds, channel.channel_number);
                          }
                        }}
                      >
                        {showStreamInsertBefore && (
                          <div className="stream-insert-indicator">
                            <div className="stream-insert-line" />
                            <span className="stream-insert-label">Insert at {channel.channel_number}</span>
                          </div>
                        )}
                      </div>
                    )}
                    {showIndicatorBefore && (
                      <div className="channel-drop-indicator">
                        <div className="drop-indicator-line" />
                      </div>
                    )}
                    <ChannelListItem
                      channel={channel}
                      isSelected={selectedChannelId === channel.id}
                      isMultiSelected={selectedChannelIds.has(channel.id)}
                      isExpanded={selectedChannelId === channel.id}
                      isDragOver={dragOverChannelId === channel.id}
                      isEditingNumber={editingChannelId === channel.id}
                      isEditingName={editingNameChannelId === channel.id}
                      isModified={modifiedChannelIds?.has(channel.id) ?? false}
                      isEditMode={isEditMode}
                      editingNumber={editingChannelNumber}
                      editingName={editingChannelName}
                      logoUrl={getChannelLogoUrl(channel)}
                      multiSelectCount={selectedChannelIds.size}
                      onEditingNumberChange={setEditingChannelNumber}
                      onEditingNameChange={setEditingChannelName}
                      onStartEditNumber={(e) => handleStartEditNumber(e, channel)}
                      onStartEditName={(e) => handleStartEditName(e, channel)}
                      onSaveNumber={() => handleSaveChannelNumber(channel.id)}
                      onSaveName={() => handleSaveChannelName(channel.id)}
                      onCancelEditNumber={handleCancelEditNumber}
                      onCancelEditName={handleCancelEditName}
                      onClick={(e) => handleChannelClick(channel, e, groupChannels.map((c) => c.id))}
                      onToggleExpand={() => handleToggleExpand(channel)}
                      onToggleSelect={(e) => handleToggleSelect(channel, e, groupChannels.map((c) => c.id))}
                      onStreamDragOver={(e) => handleStreamDragOver(e, channel.id)}
                      onStreamDragLeave={handleStreamDragLeave}
                      onStreamDrop={(e) => handleStreamDrop(e, channel.id)}
                      onDelete={() => handleDeleteChannelClick(channel)}
                      onEditChannel={() => handleEditChannel(channel)}
                      onCopyChannelUrl={dispatcharrUrl && channel.uuid ? () => handleCopyChannelUrl(`${dispatcharrUrl}/proxy/ts/stream/${channel.uuid}`, channel.name) : undefined}
                      onContextMenu={(e) => handleContextMenu(channel, e)}
                      channelUrl={dispatcharrUrl && channel.uuid ? `${dispatcharrUrl}/proxy/ts/stream/${channel.uuid}` : undefined}
                      showStreamUrls={showStreamUrls}
                      onProbeChannel={() => handleProbeChannel(channel)}
                      isProbing={probingChannels.has(channel.id)}
                      hasFailedStreams={channel.streams.some(streamId => {
                        const stats = streamStatsMap.get(streamId);
                        return stats && (stats.probe_status === 'failed' || stats.probe_status === 'timeout');
                      })}
                    />
                    {selectedChannelId === channel.id && (
                      <div className="inline-streams">
                        {streamsLoading ? (
                          <div className="inline-streams-loading">Loading streams...</div>
                        ) : channelStreams.length === 0 ? (
                          <div className="inline-streams-empty">
                            No streams assigned. Drag streams here to add.
                          </div>
                        ) : (
                          <>
                            {/* Stream toolbar - only in edit mode with multiple streams */}
                            {isEditMode && onStageReorderStreams && channelStreams.length > 1 && (
                              <div className="inline-streams-toolbar">
                                <SortDropdownButton
                                  onSortByMode={handleSortStreamsByMode}
                                  className="sort-quality-btn-wrapper"
                                  enabledCriteria={channelDefaults?.streamSortEnabled}
                                />
                              </div>
                            )}
                            <DndContext
                              sensors={streamSensors}
                              collisionDetection={closestCenter}
                              onDragEnd={handleStreamDragEnd}
                            >
                              <SortableContext
                                items={channelStreams.map((s) => s.id)}
                                strategy={verticalListSortingStrategy}
                              >
                                <div className="inline-streams-list">
                                  {channelStreams.map((stream, index) => (
                                    <div key={stream.id} className="inline-stream-row">
                                      <span className="stream-priority">{index + 1}</span>
                                      <StreamListItem
                                        stream={stream}
                                        providerName={providers.find((p) => p.id === stream.m3u_account)?.name ?? null}
                                        isEditMode={isEditMode}
                                        onRemove={handleRemoveStream}
                                        onCopyUrl={stream.url ? () => handleCopyStreamUrl(stream.url!, stream.name) : undefined}
                                        onClearStats={handleClearStreamStats}
                                        showStreamUrls={showStreamUrls}
                                        streamStats={streamStatsMap.get(stream.id) ?? null}
                                      />
                                    </div>
                                  ))}
                                </div>
                              </SortableContext>
                            </DndContext>
                          </>
                        )}
                      </div>
                    )}
                    {showIndicatorAfter && !dropIndicator?.atGroupEnd && (
                      <div className="channel-drop-indicator">
                        <div className="drop-indicator-line" />
                      </div>
                    )}
                  </div>
                  );
                })}
              </div>
            </SortableContext>
            {/* Drop zone at the end of the group - outside SortableContext for better detection */}
            <DroppableGroupEnd
              groupId={groupId}
              isEditMode={isEditMode}
              showDropIndicator={
                dropIndicator?.atGroupEnd === true &&
                dropIndicator?.groupId === groupId
              }
            />
          </>
        )}
      </div>
    );
  };

  return (
    <div className="channels-pane">
      {/* Copy feedback notifications */}
      {copySuccess && (
        <div className="copy-feedback copy-success">
          <span className="material-icons">check_circle</span>
          {copySuccess}
        </div>
      )}
      {copyError && (
        <div className="copy-feedback copy-error">
          <span className="material-icons">error</span>
          {copyError}
        </div>
      )}

      <div className={`pane-header ${isEditMode ? 'edit-mode' : ''}`}>
        <div className="pane-header-title">
          <h2>Channels</h2>
          {(() => {
            const channelsMissingStreams = channels.filter(ch => ch.streams.length === 0);
            const missingStreamsCount = channelsMissingStreams.length;
            if (missingStreamsCount === 0) return null;

            // Get unique group IDs that have channels missing streams
            const groupsWithMissingStreams = new Set(
              channelsMissingStreams.map(ch => ch.channel_group_id).filter((id): id is number => id !== null)
            );
            // Include ungrouped (null group) as group ID 0 for expansion
            const hasUngrouped = channelsMissingStreams.some(ch => ch.channel_group_id === null);

            const handleExpandMissingGroups = () => {
              setExpandedGroups(prev => {
                const newState = { ...prev };
                groupsWithMissingStreams.forEach(groupId => {
                  newState[groupId] = true;
                });
                if (hasUngrouped) {
                  newState[0] = true; // 0 represents ungrouped
                }
                return newState;
              });
            };

            return (
              <button
                className="missing-streams-alert"
                title={`${missingStreamsCount} channel${missingStreamsCount !== 1 ? 's' : ''} without streams - click to expand affected groups`}
                onClick={handleExpandMissingGroups}
              >
                <span className="material-icons">warning</span>
                {missingStreamsCount}
              </button>
            );
          })()}
          {!isEditMode && (() => {
            // Count channels with failed streams
            const channelsWithFailedStreams = channels.filter(ch =>
              ch.streams.some(streamId => {
                const stats = streamStatsMap.get(streamId);
                return stats && (stats.probe_status === 'failed' || stats.probe_status === 'timeout');
              })
            );
            const failedStreamsCount = channelsWithFailedStreams.length;
            if (failedStreamsCount === 0) return null;

            // Get unique group IDs that have channels with failed streams
            const groupsWithFailedStreams = new Set(
              channelsWithFailedStreams.map(ch => ch.channel_group_id).filter((id): id is number => id !== null)
            );
            // Include ungrouped (null group) as group ID 0 for expansion
            const hasUngrouped = channelsWithFailedStreams.some(ch => ch.channel_group_id === null);

            const handleExpandFailedGroups = () => {
              setExpandedGroups(prev => {
                const newState = { ...prev };
                groupsWithFailedStreams.forEach(groupId => {
                  newState[groupId] = true;
                });
                if (hasUngrouped) {
                  newState[0] = true; // 0 represents ungrouped
                }
                return newState;
              });
            };

            return (
              <button
                className="failed-streams-alert"
                title={`${failedStreamsCount} channel${failedStreamsCount !== 1 ? 's' : ''} with failed streams - click to expand affected groups`}
                onClick={handleExpandFailedGroups}
              >
                <span className="material-icons">error</span>
                {failedStreamsCount}
              </button>
            );
          })()}
          {isEditMode && selectedChannelIds.size > 0 && (
            <div className="selection-info">
              <span className="selection-count">{selectedChannelIds.size} selected</span>
              <div className="selection-actions">
                <button
                  className="bulk-action-btn"
                  onClick={() => bulkEPGModal.open()}
                  title="Assign EPG to selected channels"
                >
                  <span className="material-icons">live_tv</span>
                </button>
                <button
                  className="bulk-action-btn"
                  onClick={() => bulkLCNModal.open()}
                  title="Fetch Gracenote IDs for selected channels"
                >
                  <span className="material-icons">confirmation_number</span>
                </button>
                <button
                  className="bulk-action-btn"
                  onClick={() => normalizeModal.open()}
                  title="Normalize channel names"
                >
                  <span className="material-icons">text_format</span>
                </button>
                <button
                  className="bulk-action-btn"
                  onClick={handleMassRenumberClick}
                  title="Renumber channels"
                >
                  <span className="material-icons">tag</span>
                </button>
                <SortDropdownButton
                  onSortByMode={handleSortSelectedStreamsByMode}
                  isLoading={bulkSortingByQuality}
                  className="bulk-action-btn-wrapper"
                  enabledCriteria={channelDefaults?.streamSortEnabled}
                />
                <button
                  className="bulk-action-btn bulk-action-btn--danger"
                  onClick={handleBulkDeleteClick}
                  title="Delete selected channels"
                >
                  <span className="material-icons">delete</span>
                </button>
                <button
                  className="bulk-action-btn bulk-action-btn--clear"
                  onClick={onClearChannelSelection}
                  title="Clear selection"
                >
                  <span className="material-icons">close</span>
                </button>
              </div>
            </div>
          )}
        </div>
        <div className="pane-header-actions">
          {isEditMode && onUndo && onRedo && onCreateSavePoint && onRevertToSavePoint && onDeleteSavePoint && (
            <HistoryToolbar
              canUndo={canUndo}
              canRedo={canRedo}
              undoCount={undoCount}
              redoCount={redoCount}
              lastChange={lastChange}
              savePoints={savePoints}
              hasUnsavedChanges={hasUnsavedChanges}
              isOperationPending={isOperationPending || isCommitting}
              onUndo={onUndo}
              onRedo={onRedo}
              onCreateSavePoint={onCreateSavePoint}
              onRevertToSavePoint={onRevertToSavePoint}
              onDeleteSavePoint={onDeleteSavePoint}
              isEditMode={isEditMode}
            />
          )}
          {isEditMode && (
            <>
              <button
                className="create-channel-btn"
                onClick={() => onOpenCreateChannelModal?.()}
                title="Create new channel"
              >
                <span className="material-icons create-channel-icon">add</span>
              </button>
              <button
                className="create-group-btn"
                onClick={() => createGroupModal.open()}
                title="Create new channel group"
              >
                <span className="material-icons create-channel-icon">create_new_folder</span>
              </button>
              <button
                className="hidden-groups-btn"
                onClick={handleShowHiddenGroups}
                title="View and restore hidden channel groups"
              >
                <span className="material-icons">visibility_off</span>
              </button>
              <SortDropdownButton
                onSortByMode={handleSortAllStreamsByMode}
                isLoading={bulkSortingByQuality}
                showLabel={false}
                className="sort-all-quality-btn-wrapper"
                enabledCriteria={channelDefaults?.streamSortEnabled}
              />
            </>
          )}
          <button
            className="profiles-btn"
            onClick={() => profilesModal.open()}
            title="Manage channel profiles"
          >
            <span className="material-icons">group</span>
          </button>
        </div>
      </div>

      {/* Create Channel Group Modal */}
      {createGroupModal.isOpen && (
        <div className="modal-overlay">
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <h3>Create New Channel Group</h3>
            <div className="modal-form">
              <label>
                Group Name *
                <input
                  type="text"
                  value={newGroupName}
                  onChange={(e) => setNewGroupName(e.target.value)}
                  placeholder="e.g., Sports, Movies, News"
                  autoFocus
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && newGroupName.trim()) {
                      handleCreateGroup();
                    }
                  }}
                />
              </label>
            </div>
            <div className="modal-actions">
              <button
                className="modal-btn cancel"
                onClick={handleCloseCreateGroupModal}
                disabled={creatingGroup}
              >
                Cancel
              </button>
              <button
                className="modal-btn primary"
                onClick={handleCreateGroup}
                disabled={creatingGroup || !newGroupName.trim()}
              >
                {creatingGroup ? 'Creating...' : 'Create Group'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Hidden Groups Modal */}
      {hiddenGroupsModal.isOpen && (
        <div className="modal-overlay">
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <h3>Hidden Channel Groups</h3>
            <div className="modal-form">
              {hiddenGroups.length === 0 ? (
                <p style={{ padding: '20px', textAlign: 'center', color: '#888' }}>
                  No hidden groups
                </p>
              ) : (
                <div style={{ maxHeight: '400px', overflowY: 'auto' }}>
                  {hiddenGroups.map((group) => (
                    <div
                      key={group.id}
                      style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                        padding: '12px',
                        borderBottom: '1px solid var(--border-color)',
                      }}
                    >
                      <div>
                        <div style={{ fontWeight: 'bold' }}>{group.name}</div>
                        <div style={{ fontSize: '0.9em', color: '#888' }}>
                          Hidden {new Date(group.hidden_at).toLocaleDateString()}
                        </div>
                      </div>
                      <button
                        className="modal-btn primary"
                        onClick={() => handleRestoreGroup(group.id)}
                        style={{ marginLeft: '12px' }}
                      >
                        Restore
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
            <div className="modal-actions">
              <button
                className="modal-btn cancel"
                onClick={() => hiddenGroupsModal.close()}
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete Channel Confirmation Dialog */}
      {deleteConfirmModal.isOpen && channelToDelete && (
        <div className="modal-overlay">
          <div className="modal-content delete-dialog" onClick={(e) => e.stopPropagation()}>
            <h3>Delete Channel</h3>
            <div className="delete-message">
              <p>
                Are you sure you want to delete channel{' '}
                <strong>{channelToDelete.channel_number} - {channelToDelete.name}</strong>?
              </p>
              <p className={isEditMode ? "delete-info" : "delete-warning"}>
                {isEditMode
                  ? 'Changes can be undone while in edit mode.'
                  : 'This action cannot be undone. The channel and all its stream assignments will be permanently removed.'}
              </p>
            </div>
            {subsequentChannels.length > 0 && (
              <div className="delete-renumber-option">
                <label className="renumber-checkbox">
                  <input
                    type="checkbox"
                    checked={renumberAfterDelete}
                    onChange={(e) => setRenumberAfterDelete(e.target.checked)}
                  />
                  <span>
                    Renumber {subsequentChannels.length} subsequent channel{subsequentChannels.length !== 1 ? 's' : ''} (move up)
                  </span>
                </label>
                {renumberAfterDelete && (
                  <div className="renumber-preview">
                    {subsequentChannels.slice(0, 3).map((ch) => {
                      const newNumber = ch.channel_number! - 1;
                      const newName = autoRenameChannelNumber ? computeAutoRename(ch.name, ch.channel_number, newNumber) : undefined;
                      return (
                        <div key={ch.id} className="renumber-preview-item">
                          <span className="renumber-old">{ch.channel_number}</span>
                          <span className="renumber-arrow">→</span>
                          <span className="renumber-new">{newNumber}</span>
                          {newName && (
                            <>
                              <span className="renumber-name-old">{ch.name}</span>
                              <span className="renumber-arrow">→</span>
                              <span className="renumber-name-new">{newName}</span>
                            </>
                          )}
                        </div>
                      );
                    })}
                    {subsequentChannels.length > 3 && (
                      <div className="renumber-preview-more">
                        ...and {subsequentChannels.length - 3} more
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
            <div className="modal-actions">
              <button
                className="modal-btn cancel"
                onClick={handleCancelDelete}
                disabled={deleting}
              >
                Cancel
              </button>
              <button
                className="modal-btn danger"
                onClick={handleConfirmDelete}
                disabled={deleting}
              >
                {deleting ? 'Deleting...' : 'Delete'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete Group Confirmation Dialog */}
      {deleteGroupConfirmModal.isOpen && groupToDelete && (
          <div className="modal-overlay">
            <div className="modal-content delete-dialog" onClick={(e) => e.stopPropagation()}>
              <h3>Delete Group</h3>
              <div className="delete-message">
                <p>
                  Are you sure you want to delete the group{' '}
                  <strong>{groupToDelete.name}</strong>?
                </p>
                {groupToDelete.channel_count > 0 && (
                  <>
                    <p className="delete-warning">
                      This group contains {groupToDelete.channel_count} channel{groupToDelete.channel_count !== 1 ? 's' : ''}.
                      {!deleteGroupChannels && ' The channels will be moved to "Ungrouped".'}
                    </p>
                    <div className="delete-group-option">
                      <label className="delete-channels-checkbox">
                        <input
                          type="checkbox"
                          checked={deleteGroupChannels}
                          onChange={(e) => setDeleteGroupChannels(e.target.checked)}
                          disabled={deletingGroup}
                        />
                        <span>Also delete the {groupToDelete.channel_count} channel{groupToDelete.channel_count !== 1 ? 's' : ''}</span>
                      </label>
                    </div>
                  </>
                )}
                <p className="delete-info">
                  {isEditMode
                    ? 'Changes can be undone while in edit mode.'
                    : 'This action cannot be undone.'}
                </p>
              </div>
              <div className="modal-actions">
                <button
                  className="modal-btn cancel"
                  onClick={handleCancelDeleteGroup}
                  disabled={deletingGroup}
                >
                  Cancel
                </button>
                <button
                  className="modal-btn danger"
                  onClick={handleConfirmDeleteGroup}
                  disabled={deletingGroup}
                >
                  {deletingGroup ? 'Deleting...' : 'Delete'}
                </button>
              </div>
            </div>
          </div>
      )}

      {/* Bulk Delete Channels Confirmation Dialog */}
      {bulkDeleteConfirmModal.isOpen && selectedChannelIds.size > 0 && (() => {
        // Compute which groups would be emptied by this bulk delete
        // Use the channels prop (which is displayChannels from edit mode hook, containing all channels)
        // NOT localChannels which may have stale data
        const groupsToEmpty: ChannelGroup[] = [];
        // Only offer to delete empty groups if:
        // 1. In edit mode with the staging function available
        // 2. No search filter is active (when search is active, user can only select visible channels,
        //    so they can't actually select ALL channels in a group)
        if (isEditMode && onStageDeleteChannelGroup && !searchTerm) {
          // For each group, check if ALL its channels are selected for deletion
          for (const group of channelGroups) {
            const channelsInGroup = channels.filter(ch => ch.channel_group_id === group.id);
            if (channelsInGroup.length > 0) {
              const allSelected = channelsInGroup.every(ch => selectedChannelIds.has(ch.id));
              if (allSelected) {
                groupsToEmpty.push(group);
              }
            }
          }
        }

        return (
          <div className="modal-overlay">
            <div className="modal-content delete-dialog" onClick={(e) => e.stopPropagation()}>
              <h3>Delete {selectedChannelIds.size} Channel{selectedChannelIds.size !== 1 ? 's' : ''}</h3>
              <div className="delete-message">
                <p>
                  Are you sure you want to delete{' '}
                  <strong>{selectedChannelIds.size} selected channel{selectedChannelIds.size !== 1 ? 's' : ''}</strong>?
                </p>
                <p className={isEditMode ? "delete-info" : "delete-warning"}>
                  {isEditMode
                    ? 'Changes can be undone while in edit mode.'
                    : 'This action cannot be undone. All selected channels and their stream assignments will be permanently removed.'}
                </p>
                {/* Show checkbox to also delete groups that would be emptied */}
                {groupsToEmpty.length > 0 && (
                  <label className="delete-checkbox-label">
                    <input
                      type="checkbox"
                      checked={deleteEmptyGroups}
                      onChange={(e) => setDeleteEmptyGroups(e.target.checked)}
                      disabled={bulkDeleting}
                    />
                    <span>
                      Also delete {groupsToEmpty.length} empty group{groupsToEmpty.length !== 1 ? 's' : ''}:{' '}
                      <strong>{groupsToEmpty.map(g => g.name).join(', ')}</strong>
                    </span>
                  </label>
                )}
              </div>
              <div className="modal-actions">
                <button
                  className="modal-btn cancel"
                  onClick={handleCancelBulkDelete}
                  disabled={bulkDeleting}
                >
                  Cancel
                </button>
                <button
                  className="modal-btn danger"
                  onClick={handleConfirmBulkDelete}
                  disabled={bulkDeleting}
                >
                  {bulkDeleting ? 'Deleting...' : `Delete ${selectedChannelIds.size} Channel${selectedChannelIds.size !== 1 ? 's' : ''}`}
                </button>
              </div>
            </div>
          </div>
        );
      })()}

      {/* Bulk EPG Assignment Modal */}
      <BulkEPGAssignModal
        isOpen={bulkEPGModal.isOpen && selectedChannelIds.size > 0}
        selectedChannels={channels.filter(c => selectedChannelIds.has(c.id))}
        streams={allStreams}
        epgData={epgData || []}
        epgSources={epgSources || []}
        onClose={() => bulkEPGModal.close()}
        onAssign={handleBulkEPGAssign}
        epgAutoMatchThreshold={epgAutoMatchThreshold}
      />

      {/* Bulk LCN Fetch Modal */}
      <BulkLCNFetchModal
        isOpen={bulkLCNModal.isOpen && selectedChannelIds.size > 0}
        selectedChannels={channels.filter(c => selectedChannelIds.has(c.id))}
        epgData={epgData || []}
        onClose={() => bulkLCNModal.close()}
        onAssign={handleBulkLCNAssign}
      />

      {/* Gracenote Conflict Resolution Modal */}
      <GracenoteConflictModal
        isOpen={gracenoteConflictModal.isOpen}
        conflicts={gracenoteConflicts}
        onResolve={handleGracenoteConflictResolve}
        onCancel={handleGracenoteConflictCancel}
      />

      {/* Normalize Names Modal */}
      {normalizeModal.isOpen && selectedChannelIds.size > 0 && (
        <NormalizeNamesModal
          channels={channels.filter(c => selectedChannelIds.has(c.id))}
          onConfirm={handleNormalizeNames}
          onCancel={() => normalizeModal.close()}
        />
      )}

      {/* Edit Channel Modal */}
      {editChannelModal.isOpen && channelToEdit && (
        <EditChannelModal
          channel={channelToEdit}
          logos={logos}
          epgData={epgData}
          epgSources={epgSources}
          streamProfiles={streamProfiles}
          epgDataLoading={epgDataLoading}
          onClose={() => {
            editChannelModal.close();
            setChannelToEdit(null);
          }}
          onSave={async (changes: ChannelMetadataChanges) => {
            if (Object.keys(changes).length === 0) {
              editChannelModal.close();
              setChannelToEdit(null);
              return;
            }

            // Build description of changes
            const changeDescriptions: string[] = [];
            if (changes.channel_number !== undefined) {
              changeDescriptions.push(`number to ${changes.channel_number}`);
            }
            if (changes.name !== undefined) {
              changeDescriptions.push(`name to "${changes.name}"`);
            }
            if (changes.logo_id !== undefined) {
              const logoName = changes.logo_id ? logoMap.get(changes.logo_id)?.name : null;
              changeDescriptions.push(logoName ? `logo to "${logoName}"` : 'removed logo');
            }
            if (changes.tvg_id !== undefined) {
              changeDescriptions.push(changes.tvg_id ? `TVG-ID to "${changes.tvg_id}"` : 'cleared TVG-ID');
            }
            if (changes.tvc_guide_stationid !== undefined) {
              changeDescriptions.push(changes.tvc_guide_stationid ? `Station ID to "${changes.tvc_guide_stationid}"` : 'cleared Station ID');
            }
            if (changes.epg_data_id !== undefined) {
              const epgName = changes.epg_data_id ? epgData.find((e) => e.id === changes.epg_data_id)?.name : null;
              changeDescriptions.push(epgName ? `EPG to "${epgName}"` : 'removed EPG');
            }
            if (changes.stream_profile_id !== undefined) {
              const profileName = changes.stream_profile_id ? streamProfiles.find((p) => p.id === changes.stream_profile_id)?.name : null;
              changeDescriptions.push(profileName ? `profile to "${profileName}"` : 'cleared profile');
            }

            const description = `Updated ${channelToEdit.name}: ${changeDescriptions.join(', ')}`;

            if (isEditMode && onStageUpdateChannel) {
              onStageUpdateChannel(channelToEdit.id, changes, description);
            } else {
              try {
                const updated = await api.updateChannel(channelToEdit.id, changes);
                onChannelUpdate(updated, {
                  type: 'channel_metadata_update',
                  description,
                });
              } catch (err) {
                console.error('Failed to update channel:', err);
              }
            }
            editChannelModal.close();
            setChannelToEdit(null);
          }}
          onLogoCreate={async (url: string) => {
            // First check if logo already exists in our loaded logos array (instant!)
            const existingLogo = logos.find(l => l.url === url);
            if (existingLogo) {
              return existingLogo;
            }
            // Otherwise create new logo via API
            try {
              const name = url.split('/').pop()?.split('?')[0] || 'Logo';
              const newLogo = await api.createLogo({ name, url });
              if (onLogosChange) {
                await onLogosChange(); // Wait for logos to refresh so the new logo is available
              }
              return newLogo;
            } catch (err) {
              console.error('Failed to create logo:', err);
              throw err;
            }
          }}
          onLogoUpload={async (file: File) => {
            try {
              const newLogo = await api.uploadLogo(file);
              if (onLogosChange) {
                onLogosChange();
              }
              return newLogo;
            } catch (err) {
              console.error('Failed to upload logo:', err);
              throw err;
            }
          }}
        />
      )}

      {/* Cross-Group Move Modal */}
      {crossGroupMoveModal.isOpen && crossGroupMoveData && (
        <div className="modal-overlay">
          <div className="modal-content cross-group-move-dialog" onClick={(e) => e.stopPropagation()}>
            <h3>Move {crossGroupMoveData.channels.length > 1 ? `${crossGroupMoveData.channels.length} Channels` : 'Channel'} to Group</h3>

            <div className="cross-group-move-info">
              {crossGroupMoveData.channels.length === 1 ? (
                <p>
                  Moving <strong>{crossGroupMoveData.channels[0].name}</strong> from{' '}
                  <span className="group-tag">{crossGroupMoveData.sourceGroupName}</span> to{' '}
                  <span className="group-tag">{crossGroupMoveData.targetGroupName}</span>
                </p>
              ) : (
                <>
                  <p>
                    Moving <strong>{crossGroupMoveData.channels.length} channels</strong> from{' '}
                    <span className="group-tag">{crossGroupMoveData.sourceGroupName}</span> to{' '}
                    <span className="group-tag">{crossGroupMoveData.targetGroupName}</span>
                  </p>
                  <ul className="cross-group-move-channel-list">
                    {crossGroupMoveData.channels.slice(0, 5).map((ch) => (
                      <li key={ch.id}>
                        <span className="channel-number-badge">{ch.channel_number ?? '-'}</span>
                        {ch.name}
                      </li>
                    ))}
                    {crossGroupMoveData.channels.length > 5 && (
                      <li className="more-channels">...and {crossGroupMoveData.channels.length - 5} more</li>
                    )}
                  </ul>
                </>
              )}
            </div>

            {crossGroupMoveData.isTargetAutoSync && (
              <div className="cross-group-move-warning">
                <span className="material-icons warning-icon">warning</span>
                <div className="warning-text">
                  <strong>Auto-populated group</strong>
                  <p>
                    The target group "{crossGroupMoveData.targetGroupName}" is managed by auto channel sync.
                    Manually added channels may be affected when the provider syncs.
                  </p>
                </div>
              </div>
            )}

            <div className="cross-group-move-options">
              <div className="channel-number-section">
                <label>Channel Numbers</label>
                {crossGroupMoveData.minChannelInGroup !== null && crossGroupMoveData.maxChannelInGroup !== null && (
                  <p className="group-range-info">
                    Target group range: {crossGroupMoveData.minChannelInGroup} – {crossGroupMoveData.maxChannelInGroup}
                  </p>
                )}
              </div>

              <div className="move-option-radio-group">
                {/* Keep current numbers option */}
                <label className={`move-option-radio ${selectedNumberingOption === 'keep' ? 'selected' : ''}`}>
                  <input
                    type="radio"
                    name="numberingOption"
                    checked={selectedNumberingOption === 'keep'}
                    onChange={() => setSelectedNumberingOption('keep')}
                  />
                  <span className="material-icons">numbers</span>
                  <div className="move-option-text">
                    <strong>Keep current numbers</strong>
                    {crossGroupMoveData.channels.length === 1 ? (
                      <span>Stay at channel {crossGroupMoveData.channels[0].channel_number ?? '(none)'}</span>
                    ) : (
                      <span>Keep existing channel numbers</span>
                    )}
                  </div>
                </label>

                {/* Suggested number option */}
                {crossGroupMoveData.suggestedChannelNumber !== null && (
                  <label className={`move-option-radio ${selectedNumberingOption === 'suggested' ? 'selected' : ''}`}>
                    <input
                      type="radio"
                      name="numberingOption"
                      checked={selectedNumberingOption === 'suggested'}
                      onChange={() => setSelectedNumberingOption('suggested')}
                    />
                    <span className="material-icons">{crossGroupMoveData.insertAtPosition ? 'playlist_add' : 'add_circle'}</span>
                    <div className="move-option-text">
                      <strong>{crossGroupMoveData.insertAtPosition ? 'Insert at position' : 'Assign sequential numbers'}</strong>
                      {crossGroupMoveData.channels.length === 1 ? (
                        <span>{crossGroupMoveData.insertAtPosition ? 'Insert at' : 'Use'} channel {crossGroupMoveData.suggestedChannelNumber}</span>
                      ) : (
                        <span>Starting at {crossGroupMoveData.suggestedChannelNumber} ({crossGroupMoveData.suggestedChannelNumber}–{crossGroupMoveData.suggestedChannelNumber + crossGroupMoveData.channels.length - 1})</span>
                      )}
                    </div>
                  </label>
                )}

                {/* Custom number option */}
                <label className={`move-option-radio ${selectedNumberingOption === 'custom' ? 'selected' : ''}`}>
                  <input
                    type="radio"
                    name="numberingOption"
                    checked={selectedNumberingOption === 'custom'}
                    onChange={() => setSelectedNumberingOption('custom')}
                  />
                  <span className="material-icons">edit</span>
                  <div className="move-option-text">
                    <strong>Custom starting number</strong>
                    {selectedNumberingOption === 'custom' ? (
                      <div className="custom-number-inline">
                        <input
                          type="number"
                          className="custom-number-input-inline"
                          placeholder="Enter channel number"
                          value={customStartingNumber}
                          onChange={(e) => setCustomStartingNumber(e.target.value)}
                          onClick={(e) => e.stopPropagation()}
                          min="1"
                          autoFocus
                        />
                        {customStartingNumber && !isNaN(parseInt(customStartingNumber, 10)) && parseInt(customStartingNumber, 10) >= 1 && crossGroupMoveData.channels.length > 1 && (
                          <span className="custom-number-range-inline">
                            → {parseInt(customStartingNumber, 10)}–{parseInt(customStartingNumber, 10) + crossGroupMoveData.channels.length - 1}
                          </span>
                        )}
                      </div>
                    ) : (
                      <span>Enter a specific channel number</span>
                    )}
                  </div>
                </label>
              </div>
            </div>

            {/* Channel Number Conflict Warning */}
            {getMoveConflicts.hasConflicts && (
              <div className="cross-group-move-conflict-warning">
                <span className="material-icons conflict-icon">swap_vert</span>
                <div className="conflict-warning-text">
                  <strong>Channel numbers will shift</strong>
                  {selectedNumberingOption === 'keep' ? (
                    <p>
                      {getMoveConflicts.conflicts.length === 1 ? (
                        <>Channel <strong>{getMoveConflicts.conflicts[0].channel_number}</strong> ({getMoveConflicts.conflicts[0].name}) already exists in this group and will have duplicate number.</>
                      ) : (
                        <>Channels {getMoveConflicts.conflicts.slice(0, 3).map(ch => ch.channel_number).join(', ')}{getMoveConflicts.conflicts.length > 3 ? ` and ${getMoveConflicts.conflicts.length - 3} more` : ''} already exist in this group and will have duplicate numbers.</>
                      )}
                    </p>
                  ) : (
                    <p>
                      {getMoveConflicts.conflicts.length === 1 ? (
                        <>Channel <strong>{getMoveConflicts.conflicts[0].channel_number}</strong> ({getMoveConflicts.conflicts[0].name}) will be shifted to {(getMoveConflicts.conflicts[0].channel_number ?? 0) + crossGroupMoveData.channels.length}.</>
                      ) : (
                        <>Existing channels ({getMoveConflicts.conflicts.slice(0, 3).map(ch => ch.channel_number).join(', ')}{getMoveConflicts.conflicts.length > 3 ? `, +${getMoveConflicts.conflicts.length - 3} more` : ''}) will be shifted up by {crossGroupMoveData.channels.length} to make room.</>
                      )}
                    </p>
                  )}
                </div>
              </div>
            )}

            {/* Source Group Renumbering Option */}
            {crossGroupMoveData.sourceGroupHasGaps && (
              <div className="cross-group-move-source-renumber">
                <label className="source-renumber-option">
                  <input
                    type="checkbox"
                    checked={renumberSourceGroup}
                    onChange={(e) => setRenumberSourceGroup(e.target.checked)}
                  />
                  <div className="source-renumber-text">
                    <strong>Close gaps in source group</strong>
                    <span>
                      Renumber remaining channels in "{crossGroupMoveData.sourceGroupName}" to remove gaps
                    </span>
                  </div>
                </label>
              </div>
            )}

            <div className="modal-actions">
              <button
                className="modal-btn cancel"
                onClick={handleCrossGroupMoveCancel}
              >
                Cancel
              </button>
              <button
                className="modal-btn primary"
                onClick={handleMoveButtonClick}
                disabled={!isMoveButtonEnabled()}
              >
                Move {crossGroupMoveData.channels.length > 1 ? `${crossGroupMoveData.channels.length} Channels` : 'Channel'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Group Reorder Modal */}
      {groupReorderModal.isOpen && groupReorderData && (
        <div className="modal-overlay">
          <div className="modal-content cross-group-move-dialog" onClick={(e) => e.stopPropagation()}>
            <h3>Reorder Group</h3>

            <div className="cross-group-move-info">
              <p>
                Moving <strong>{groupReorderData.groupName}</strong> with{' '}
                <strong>{groupReorderData.channels.length} channel{groupReorderData.channels.length !== 1 ? 's' : ''}</strong>
                {groupReorderData.precedingGroupName && (
                  <> to after <span className="group-tag">{groupReorderData.precedingGroupName}</span></>
                )}
                {!groupReorderData.precedingGroupName && groupReorderData.newPosition === 0 && (
                  <> to the <strong>first position</strong></>
                )}
              </p>
              {groupReorderData.precedingGroupMaxChannel !== null && (
                <p className="group-range-info">
                  Preceding group ends at channel {groupReorderData.precedingGroupMaxChannel}
                </p>
              )}
            </div>

            <div className="cross-group-move-options">
              <div className="channel-number-section">
                <label>Channel Numbers</label>
              </div>

              <div className="move-option-radio-group">
                {/* Keep current numbers option */}
                <label className={`move-option-radio ${groupReorderNumberingOption === 'keep' ? 'selected' : ''}`}>
                  <input
                    type="radio"
                    name="groupReorderNumberingOption"
                    checked={groupReorderNumberingOption === 'keep'}
                    onChange={() => setGroupReorderNumberingOption('keep')}
                  />
                  <span className="material-icons">numbers</span>
                  <div className="move-option-text">
                    <strong>Keep current numbers</strong>
                    <span>Don't change channel numbers</span>
                  </div>
                </label>

                {/* Suggested number option */}
                {groupReorderData.suggestedStartingNumber !== null && (
                  <label className={`move-option-radio ${groupReorderNumberingOption === 'suggested' ? 'selected' : ''}`}>
                    <input
                      type="radio"
                      name="groupReorderNumberingOption"
                      checked={groupReorderNumberingOption === 'suggested'}
                      onChange={() => setGroupReorderNumberingOption('suggested')}
                    />
                    <span className="material-icons">auto_fix_high</span>
                    <div className="move-option-text">
                      <strong>Renumber sequentially</strong>
                      <span>
                        Starting at {groupReorderData.suggestedStartingNumber}
                        {groupReorderData.channels.length > 1 && (
                          <> ({groupReorderData.suggestedStartingNumber}–{groupReorderData.suggestedStartingNumber + groupReorderData.channels.length - 1})</>
                        )}
                      </span>
                    </div>
                  </label>
                )}

                {/* Custom number option */}
                <label className={`move-option-radio ${groupReorderNumberingOption === 'custom' ? 'selected' : ''}`}>
                  <input
                    type="radio"
                    name="groupReorderNumberingOption"
                    checked={groupReorderNumberingOption === 'custom'}
                    onChange={() => setGroupReorderNumberingOption('custom')}
                  />
                  <span className="material-icons">edit</span>
                  <div className="move-option-text">
                    <strong>Custom starting number</strong>
                    {groupReorderNumberingOption === 'custom' ? (
                      <div className="custom-number-inline">
                        <input
                          type="number"
                          className="custom-number-input-inline"
                          placeholder="Enter starting number"
                          value={groupReorderCustomNumber}
                          onChange={(e) => setGroupReorderCustomNumber(e.target.value)}
                          onClick={(e) => e.stopPropagation()}
                          min="1"
                          autoFocus
                        />
                        {groupReorderCustomNumber && !isNaN(parseInt(groupReorderCustomNumber, 10)) && parseInt(groupReorderCustomNumber, 10) >= 1 && groupReorderData.channels.length > 1 && (
                          <span className="custom-number-range-inline">
                            → {parseInt(groupReorderCustomNumber, 10)}–{parseInt(groupReorderCustomNumber, 10) + groupReorderData.channels.length - 1}
                          </span>
                        )}
                      </div>
                    ) : (
                      <span>Enter a specific starting number</span>
                    )}
                  </div>
                </label>
              </div>
            </div>

            <div className="modal-actions">
              <button
                className="modal-btn cancel"
                onClick={handleGroupReorderCancel}
              >
                Cancel
              </button>
              <button
                className="modal-btn primary"
                onClick={handleGroupReorderConfirm}
                disabled={
                  groupReorderNumberingOption === 'custom' &&
                  (!groupReorderCustomNumber || isNaN(parseInt(groupReorderCustomNumber, 10)) || parseInt(groupReorderCustomNumber, 10) < 1)
                }
              >
                Confirm
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Sort & Renumber Modal */}
      {sortRenumberModal.isOpen && sortRenumberData && (
        <div className="modal-overlay">
          <div className="modal-content sort-renumber-dialog" onClick={(e) => e.stopPropagation()}>
            <h3>Sort & Renumber Channels</h3>

            <div className="sort-renumber-info">
              <p>
                Sort <strong>{sortRenumberData.channels.length} channels</strong> in{' '}
                <span className="group-tag">{sortRenumberData.groupName}</span> alphabetically and assign sequential numbers.
              </p>
            </div>

            <div className="sort-renumber-options">
              <div className="sort-renumber-field">
                <label htmlFor="starting-number">Starting Channel Number</label>
                <input
                  id="starting-number"
                  type="number"
                  min="1"
                  value={sortRenumberStartingNumber}
                  onChange={(e) => setSortRenumberStartingNumber(e.target.value)}
                  className="sort-renumber-input"
                  autoFocus
                />
                {sortRenumberStartingNumber && !isNaN(parseInt(sortRenumberStartingNumber, 10)) && parseInt(sortRenumberStartingNumber, 10) >= 1 && (
                  <span className="sort-renumber-range">
                    Channels will be numbered {parseInt(sortRenumberStartingNumber, 10)} – {parseInt(sortRenumberStartingNumber, 10) + sortRenumberData.channels.length - 1}
                  </span>
                )}
              </div>
              <label className="sort-renumber-checkbox">
                <input
                  type="checkbox"
                  checked={sortStripNumbers}
                  onChange={(e) => setSortStripNumbers(e.target.checked)}
                />
                <span>Ignore channel numbers in names when sorting</span>
              </label>
              <label className="sort-renumber-checkbox">
                <input
                  type="checkbox"
                  checked={sortIgnoreCountry}
                  onChange={(e) => setSortIgnoreCountry(e.target.checked)}
                />
                <span>Ignore country prefix when sorting (e.g., "US | ", "UK: ")</span>
              </label>
              <label className="sort-renumber-checkbox">
                <input
                  type="checkbox"
                  checked={sortRenumberUpdateNames}
                  onChange={(e) => setSortRenumberUpdateNames(e.target.checked)}
                />
                <span>Update channel numbers in names (e.g., "209 | A&E" → "200 | A&E")</span>
              </label>
            </div>

            {/* Preview of sorted order */}
            <div className="sort-renumber-preview">
              <label>Preview (sorted A–Z)</label>
              <ul className="sort-renumber-preview-list">
                {[...sortRenumberData.channels]
                  .sort((a, b) => {
                    let nameA = a.name;
                    let nameB = b.name;
                    if (sortStripNumbers) {
                      nameA = getNameForSorting(nameA);
                      nameB = getNameForSorting(nameB);
                    }
                    if (sortIgnoreCountry) {
                      nameA = stripCountryPrefix(nameA);
                      nameB = stripCountryPrefix(nameB);
                    }
                    return naturalCompare(nameA.toLowerCase(), nameB.toLowerCase());
                  })
                  .slice(0, 5)
                  .map((ch, index) => {
                    const startNum = parseInt(sortRenumberStartingNumber, 10) || 1;
                    const newNumber = startNum + index;
                    const newName = sortRenumberUpdateNames && ch.channel_number !== null
                      ? computeAutoRename(ch.name, ch.channel_number, newNumber)
                      : undefined;
                    return (
                      <li key={ch.id}>
                        <span className="preview-old-number">{ch.channel_number ?? '-'}</span>
                        <span className="preview-arrow">→</span>
                        <span className="preview-new-number">{newNumber}</span>
                        {newName ? (
                          <>
                            <span className="preview-name preview-name-old">{ch.name}</span>
                            <span className="preview-arrow">→</span>
                            <span className="preview-name preview-name-new">{newName}</span>
                          </>
                        ) : (
                          <span className="preview-name">{ch.name}</span>
                        )}
                      </li>
                    );
                  })}
                {sortRenumberData.channels.length > 5 && (
                  <li className="more-channels">...and {sortRenumberData.channels.length - 5} more</li>
                )}
              </ul>
            </div>

            <div className="modal-actions">
              <button
                className="modal-btn cancel"
                onClick={handleSortRenumberCancel}
              >
                Cancel
              </button>
              <button
                className="modal-btn primary"
                onClick={handleSortRenumberConfirm}
                disabled={!sortRenumberStartingNumber || isNaN(parseInt(sortRenumberStartingNumber, 10)) || parseInt(sortRenumberStartingNumber, 10) < 1}
              >
                Sort & Renumber
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Mass Renumber Modal */}
      {massRenumberModal.isOpen && massRenumberChannels.length > 0 && (
        <div className="modal-overlay">
          <div className="modal-content mass-renumber-dialog" onClick={(e) => e.stopPropagation()}>
            <h3>Renumber Channels</h3>

            <div className="mass-renumber-info">
              <p>
                Assign new sequential numbers to <strong>{massRenumberChannels.length} selected channel{massRenumberChannels.length !== 1 ? 's' : ''}</strong>.
              </p>
            </div>

            <div className="mass-renumber-options">
              <div className="mass-renumber-field">
                <label htmlFor="mass-renumber-start">Starting Channel Number</label>
                <input
                  id="mass-renumber-start"
                  type="number"
                  min="1"
                  value={massRenumberStartingNumber}
                  onChange={(e) => setMassRenumberStartingNumber(e.target.value)}
                  className="mass-renumber-input"
                  autoFocus
                />
                {massRenumberStartingNumber && !isNaN(parseInt(massRenumberStartingNumber, 10)) && parseInt(massRenumberStartingNumber, 10) >= 1 && (
                  <span className="mass-renumber-range">
                    Channels will be numbered {parseInt(massRenumberStartingNumber, 10)} – {parseInt(massRenumberStartingNumber, 10) + massRenumberChannels.length - 1}
                  </span>
                )}
              </div>
              <label className="mass-renumber-checkbox">
                <input
                  type="checkbox"
                  checked={massRenumberUpdateNames}
                  onChange={(e) => setMassRenumberUpdateNames(e.target.checked)}
                />
                Update channel numbers in names (e.g., "209 | A&E" → "200 | A&E")
              </label>
            </div>

            {/* Conflict Warning */}
            {getMassRenumberConflicts.hasConflicts && (
              <div className="mass-renumber-conflict-warning">
                <span className="material-icons conflict-icon">warning</span>
                <div className="conflict-warning-content">
                  <strong>{getMassRenumberConflicts.conflicts.length} channel{getMassRenumberConflicts.conflicts.length !== 1 ? 's' : ''} will be displaced</strong>
                  <p>
                    The following channels are in the target range ({parseInt(massRenumberStartingNumber, 10)} – {parseInt(massRenumberStartingNumber, 10) + massRenumberChannels.length - 1}):
                  </p>
                  <ul className="conflict-channel-list">
                    {getMassRenumberConflicts.conflicts.slice(0, 5).map(ch => (
                      <li key={ch.id}>
                        <span className="conflict-channel-number">{ch.channel_number}</span>
                        <span className="conflict-channel-name">{ch.name}</span>
                      </li>
                    ))}
                    {getMassRenumberConflicts.conflicts.length > 5 && (
                      <li className="more-conflicts">...and {getMassRenumberConflicts.conflicts.length - 5} more</li>
                    )}
                  </ul>
                </div>
              </div>
            )}

            {/* Preview */}
            <div className="mass-renumber-preview">
              <label>Preview</label>
              <ul className="mass-renumber-preview-list">
                {massRenumberChannels.slice(0, 5).map((ch, index) => {
                  const startNum = parseInt(massRenumberStartingNumber, 10) || 1;
                  const newNumber = startNum + index;
                  const hasChange = ch.channel_number !== newNumber;
                  const newName = massRenumberUpdateNames && ch.channel_number !== null
                    ? computeAutoRename(ch.name, ch.channel_number, newNumber)
                    : undefined;
                  return (
                    <li key={ch.id} className={hasChange ? 'has-change' : ''}>
                      <span className="preview-old-number">{ch.channel_number ?? '-'}</span>
                      <span className="preview-arrow">→</span>
                      <span className="preview-new-number">{newNumber}</span>
                      {newName ? (
                        <>
                          <span className="preview-name preview-name-old">{ch.name}</span>
                          <span className="preview-arrow">→</span>
                          <span className="preview-name preview-name-new">{newName}</span>
                        </>
                      ) : (
                        <span className="preview-name">{ch.name}</span>
                      )}
                    </li>
                  );
                })}
                {massRenumberChannels.length > 5 && (
                  <li className="more-channels">...and {massRenumberChannels.length - 5} more</li>
                )}
              </ul>
            </div>

            <div className="modal-actions">
              <button
                className="modal-btn cancel"
                onClick={handleMassRenumberCancel}
              >
                Cancel
              </button>
              {getMassRenumberConflicts.hasConflicts ? (
                <button
                  className="modal-btn primary"
                  onClick={() => handleMassRenumberConfirm(true)}
                  disabled={!massRenumberStartingNumber || isNaN(parseInt(massRenumberStartingNumber, 10)) || parseInt(massRenumberStartingNumber, 10) < 1}
                  title={`Shift ${getMassRenumberConflicts.conflicts.length} conflicting channel(s) to numbers ${parseInt(massRenumberStartingNumber, 10) + massRenumberChannels.length} and up`}
                >
                  <span className="material-icons">swap_vert</span>
                  Shift & Renumber
                </button>
              ) : (
                <button
                  className="modal-btn primary"
                  onClick={() => handleMassRenumberConfirm(false)}
                  disabled={!massRenumberStartingNumber || isNaN(parseInt(massRenumberStartingNumber, 10)) || parseInt(massRenumberStartingNumber, 10) < 1}
                >
                  Renumber
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Channel Profiles Modal */}
      <ChannelProfilesListModal
        isOpen={profilesModal.isOpen}
        onClose={() => profilesModal.close()}
        onSaved={() => {
          if (onChannelProfilesChange) {
            onChannelProfilesChange();
          }
        }}
        channels={channels}
        channelGroups={channelGroups}
      />

      <div className="pane-filters">
        <div className="search-row">
          <div className="search-input-wrapper">
            <input
              type="text"
              placeholder="Search channels..."
              value={searchTerm}
              onChange={(e) => onSearchChange(e.target.value)}
              className="search-input"
            />
            {searchTerm && (
              <button
                type="button"
                className="search-clear-btn"
                onClick={() => onSearchChange('')}
                title="Clear search"
              >
                <span className="material-icons">close</span>
              </button>
            )}
          </div>
          {/* Expand/Collapse All Buttons */}
          <div className="expand-collapse-buttons">
            <button
              className="expand-collapse-btn"
              onClick={() => {
                // Get all visible group IDs
                const visibleGroupIds: number[] = [];
                filteredChannelGroups.forEach((g) => visibleGroupIds.push(g.id));
                selectedGroups.forEach((groupId) => {
                  const isEmpty = !channelsByGroup[groupId] || channelsByGroup[groupId].length === 0;
                  if (isEmpty && shouldShowGroup(groupId) && !visibleGroupIds.includes(groupId)) {
                    visibleGroupIds.push(groupId);
                  }
                });
                newlyCreatedGroupIds.forEach((groupId) => {
                  const isEmpty = !channelsByGroup[groupId] || channelsByGroup[groupId].length === 0;
                  const notAlreadyRendered = !filteredChannelGroups.some((g) => g.id === groupId) && !selectedGroups.includes(groupId);
                  if (isEmpty && notAlreadyRendered && shouldShowGroup(groupId) && !visibleGroupIds.includes(groupId)) {
                    visibleGroupIds.push(groupId);
                  }
                });
                // Include ungrouped (as 0) if it has channels
                if (channelsByGroup.ungrouped?.length > 0) {
                  visibleGroupIds.push(0); // 0 represents 'ungrouped'
                }
                // Expand all
                setExpandedGroups((prev) => {
                  const newState = { ...prev };
                  visibleGroupIds.forEach((id) => {
                    newState[id] = true;
                  });
                  return newState;
                });
              }}
              title="Expand all groups"
            >
              <span className="material-icons">unfold_more</span>
            </button>
            <button
              className="expand-collapse-btn"
              onClick={() => {
                // Collapse all
                setExpandedGroups({});
              }}
              title="Collapse all groups"
            >
              <span className="material-icons">unfold_less</span>
            </button>
          </div>
        </div>
        <div className="pane-filters-row">
        <div className="group-filter-dropdown" ref={dropdownRef}>
          <button
            className="group-filter-button"
            onClick={() => setGroupDropdownOpen(!groupDropdownOpen)}
          >
            <span>
              {selectedGroups.length === 0
                ? 'No groups selected'
                : `${selectedGroups.length} group${selectedGroups.length > 1 ? 's' : ''} selected`}
            </span>
            <span className="dropdown-arrow">{groupDropdownOpen ? '▲' : '▼'}</span>
          </button>
          {groupDropdownOpen && (
            <div className="group-filter-menu">
              <div className="group-filter-search">
                <input
                  ref={groupFilterSearchRef}
                  type="text"
                  placeholder="Search groups..."
                  value={groupFilterSearch}
                  onChange={(e) => setGroupFilterSearch(e.target.value)}
                  className="group-filter-search-input"
                  autoFocus
                />
                {groupFilterSearch && (
                  <button
                    className="group-filter-search-clear"
                    onClick={() => setGroupFilterSearch('')}
                    title="Clear search"
                  >
                    <span className="material-icons">close</span>
                  </button>
                )}
              </div>
              <div className="group-filter-actions">
                <button
                  className="group-filter-action"
                  onClick={() => {
                    // Select all visible groups
                    const visibleGroups = allGroupsSorted.filter((g) =>
                      g.name.toLowerCase().includes(groupFilterSearch.toLowerCase())
                    );
                    onSelectedGroupsChange(visibleGroups.map((g) => g.id));
                  }}
                >
                  Select All
                </button>
                <button
                  className="group-filter-action"
                  onClick={() => onSelectedGroupsChange([])}
                >
                  Clear All
                </button>
              </div>
              <div className="group-filter-options">
                {allGroupsSorted
                  .filter((g) => g.name.toLowerCase().includes(groupFilterSearch.toLowerCase()))
                  .sort((a, b) => {
                    const aSelected = selectedGroups.includes(a.id);
                    const bSelected = selectedGroups.includes(b.id);
                    if (aSelected && !bSelected) return -1;
                    if (!aSelected && bSelected) return 1;
                    return naturalCompare(a.name, b.name);
                  })
                  .map((group) => (
                    <label key={group.id} className="group-filter-option">
                      <input
                        type="checkbox"
                        checked={selectedGroups.includes(group.id)}
                        onChange={(e) => {
                          if (e.target.checked) {
                            onSelectedGroupsChange([...selectedGroups, group.id]);
                          } else {
                            onSelectedGroupsChange(selectedGroups.filter((id) => id !== group.id));
                          }
                        }}
                      />
                      <span className="group-option-name">{group.name}</span>
                      <span className="group-option-count">({channelsByGroup[group.id]?.length || 0})</span>
                    </label>
                  ))}
                {allGroupsSorted.filter((g) => g.name.toLowerCase().includes(groupFilterSearch.toLowerCase())).length === 0 && (
                  <div className="group-filter-empty">No groups match "{groupFilterSearch}"</div>
                )}
              </div>
            </div>
          )}
        </div>

        {/* Channel List Filter Settings */}
        <div className="filter-settings-dropdown" ref={filterSettingsRef}>
          <button
            className="filter-settings-button"
            onClick={() => setFilterSettingsOpen(!filterSettingsOpen)}
            title="Channel List Filters"
          >
            <span className="material-icons" style={{ fontSize: '18px' }}>tune</span>
          </button>
          {filterSettingsOpen && channelListFilters && (
            <div className="filter-settings-menu">
              <div className="filter-settings-header">Channel List Filters</div>
              <div className="filter-settings-options">
                <label className="filter-settings-option">
                  <input
                    type="checkbox"
                    checked={channelListFilters.showEmptyGroups}
                    onChange={(e) => onChannelListFiltersChange?.({ showEmptyGroups: e.target.checked })}
                  />
                  <span>Show Empty Groups</span>
                </label>
                <label className="filter-settings-option">
                  <input
                    type="checkbox"
                    checked={channelListFilters.showNewlyCreatedGroups}
                    onChange={(e) => onChannelListFiltersChange?.({ showNewlyCreatedGroups: e.target.checked })}
                  />
                  <span>Show Newly Created Groups</span>
                </label>
                <label className="filter-settings-option">
                  <input
                    type="checkbox"
                    checked={channelListFilters.showProviderGroups}
                    onChange={(e) => onChannelListFiltersChange?.({ showProviderGroups: e.target.checked })}
                  />
                  <span>Show Provider Groups</span>
                </label>
                <label className="filter-settings-option">
                  <input
                    type="checkbox"
                    checked={channelListFilters.showManualGroups}
                    onChange={(e) => onChannelListFiltersChange?.({ showManualGroups: e.target.checked })}
                  />
                  <span>Show Manual Groups</span>
                </label>
                <label className="filter-settings-option">
                  <input
                    type="checkbox"
                    checked={channelListFilters.showAutoChannelGroups}
                    onChange={(e) => onChannelListFiltersChange?.({ showAutoChannelGroups: e.target.checked })}
                  />
                  <span>Show Auto Channel Groups</span>
                </label>
              </div>
            </div>
          )}
        </div>
        </div>
      </div>

      <div
        className={`pane-content ${streamGroupDragOver ? 'stream-group-drop-target' : ''}`}
        onDragOver={handlePaneDragOver}
        onDragLeave={handlePaneDragLeave}
        onDrop={handlePaneDrop}
      >
        {loading ? (
          <div className="loading">Loading channels...</div>
        ) : (
          <DndContext
            sensors={sensors}
            collisionDetection={closestCenter}
            onDragStart={handleDragStart}
            onDragOver={handleDragOver}
            onDragEnd={handleDragEnd}
          >
            {/* Drop zone before first group */}
            {streamGroupDragOver && isEditMode && (
              <div
                className={`stream-group-drop-zone ${streamGroupDropTarget?.afterGroupId === null ? 'active' : ''}`}
                onDragOver={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  setStreamGroupDropTarget({ afterGroupId: null });
                }}
                onDragLeave={(e) => {
                  if (!e.currentTarget.contains(e.relatedTarget as Node)) {
                    setStreamGroupDropTarget(null);
                  }
                }}
              >
                <div className="drop-zone-indicator">
                  <span className="material-icons">add</span>
                  <span>Drop here to insert at beginning</span>
                </div>
              </div>
            )}
            {/* Always render Uncategorized at the top (even when empty) */}
            {renderGroup(
              'ungrouped',
              'Uncategorized',
              channelsByGroup.ungrouped || [],
              (channelsByGroup.ungrouped?.length ?? 0) === 0
            )}
            {/* Drop zone after Uncategorized */}
            {streamGroupDragOver && isEditMode && (
              <div
                className={`stream-group-drop-zone ${streamGroupDropTarget?.afterGroupId === 'ungrouped' ? 'active' : ''}`}
                onDragOver={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  setStreamGroupDropTarget({ afterGroupId: 'ungrouped' });
                }}
                onDragLeave={(e) => {
                  if (!e.currentTarget.contains(e.relatedTarget as Node)) {
                    setStreamGroupDropTarget(null);
                  }
                }}
              >
                <div className="drop-zone-indicator">
                  <span className="material-icons">add</span>
                  <span>Drop here to insert after Uncategorized</span>
                </div>
              </div>
            )}
            {/* Wrap groups in SortableContext for drag-and-drop reordering */}
            <SortableContext
              items={filteredChannelGroups.map((g) => `group-${g.id}`)}
              strategy={verticalListSortingStrategy}
            >
              {/* Render filtered groups with channels, with drop zones between them */}
              {filteredChannelGroups.map((group) => (
                <React.Fragment key={group.id}>
                  {renderGroup(group.id, group.name, channelsByGroup[group.id] || [])}
                  {/* Drop zone after each group */}
                  {streamGroupDragOver && isEditMode && (
                    <div
                      className={`stream-group-drop-zone ${streamGroupDropTarget?.afterGroupId === group.id ? 'active' : ''}`}
                      onDragOver={(e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        setStreamGroupDropTarget({ afterGroupId: group.id });
                      }}
                      onDragLeave={(e) => {
                        if (!e.currentTarget.contains(e.relatedTarget as Node)) {
                          setStreamGroupDropTarget(null);
                        }
                      }}
                    >
                      <div className="drop-zone-indicator">
                        <span className="material-icons">add</span>
                        <span>Drop here to insert after {group.name}</span>
                      </div>
                    </div>
                  )}
                </React.Fragment>
              ))}
            </SortableContext>
            {/* Render selected empty groups that pass the filter */}
            {selectedGroups
              .filter((groupId) => {
                const isEmpty = !channelsByGroup[groupId] || channelsByGroup[groupId].length === 0;
                return isEmpty && shouldShowGroup(groupId);
              })
              .map((groupId) => {
                const group = channelGroups.find((g) => g.id === groupId);
                return group ? renderGroup(group.id, group.name, [], true) : null;
              })
            }
            {/* Render newly created empty groups that pass the filter */}
            {Array.from(newlyCreatedGroupIds)
              .filter((groupId) => {
                const isEmpty = !channelsByGroup[groupId] || channelsByGroup[groupId].length === 0;
                const notAlreadyRendered = !filteredChannelGroups.some((g) => g.id === groupId) && !selectedGroups.includes(groupId);
                return isEmpty && notAlreadyRendered && shouldShowGroup(groupId);
              })
              .map((groupId) => {
                const group = channelGroups.find((g) => g.id === groupId);
                return group ? renderGroup(group.id, group.name, [], true) : null;
              })
            }

            {/* Drag overlay - shows what's being dragged */}
            <DragOverlay dropAnimation={null}>
              {activeDragId !== null && (() => {
                const draggedChannel = localChannels.find((c) => c.id === activeDragId);
                if (!draggedChannel) return null;

                // Check if dragging multiple selected channels
                const isDraggedPartOfSelection = selectedChannelIds.has(activeDragId);
                const dragCount = isDraggedPartOfSelection ? selectedChannelIds.size : 1;

                return (
                  <div className="drag-overlay-item">
                    <span className="material-icons drag-overlay-icon">drag_indicator</span>
                    <span className="drag-overlay-number">{draggedChannel.channel_number ?? '-'}</span>
                    <span className="drag-overlay-name">{draggedChannel.name}</span>
                    {dragCount > 1 && (
                      <span className="drag-overlay-count">+{dragCount - 1} more</span>
                    )}
                  </div>
                );
              })()}
            </DragOverlay>
          </DndContext>
        )}

        {/* Context menu */}
        {contextMenu && (
          <div
            className="context-menu"
            style={{
              position: 'fixed',
              top: contextMenu.y,
              left: contextMenu.x,
              zIndex: 10000,
            }}
            onMouseDown={(e) => e.stopPropagation()}
          >
            <div className="context-menu-item" onClick={() => {
              // Show submenu of groups to move to
              const submenu = document.createElement('div');
              submenu.className = 'context-menu-submenu';
              submenu.style.position = 'fixed';
              submenu.style.top = `${contextMenu.y}px`;
              submenu.style.left = `${contextMenu.x + 200}px`;

              // Uncategorized option
              const uncategorizedOption = document.createElement('div');
              uncategorizedOption.className = 'context-menu-item';
              uncategorizedOption.textContent = 'Uncategorized';
              uncategorizedOption.onclick = () => {
                handleMoveToGroup(null);
                document.body.removeChild(submenu);
              };
              submenu.appendChild(uncategorizedOption);

              // Group options (only show visible/selected groups)
              channelGroups.filter(group => selectedGroups.includes(group.id)).forEach(group => {
                const option = document.createElement('div');
                option.className = 'context-menu-item';
                option.textContent = group.name;
                option.onclick = () => {
                  handleMoveToGroup(group.id);
                  document.body.removeChild(submenu);
                };
                submenu.appendChild(option);
              });

              document.body.appendChild(submenu);

              // Add scroll indicator if content is scrollable
              const checkScrollable = () => {
                if (submenu.scrollHeight > submenu.clientHeight) {
                  submenu.classList.add('scrollable');
                  // Update scroll indicator based on position
                  const updateScrollIndicator = () => {
                    const atTop = submenu.scrollTop <= 0;
                    const atBottom = submenu.scrollTop + submenu.clientHeight >= submenu.scrollHeight - 1;
                    submenu.classList.toggle('scroll-top', !atTop);
                    submenu.classList.toggle('scroll-bottom', !atBottom);
                  };
                  updateScrollIndicator();
                  submenu.addEventListener('scroll', updateScrollIndicator);
                }
              };
              // Check after render
              requestAnimationFrame(checkScrollable);

              // Close submenu when clicking outside
              const closeSubmenu = (e: MouseEvent) => {
                if (!submenu.contains(e.target as Node)) {
                  document.body.removeChild(submenu);
                  document.removeEventListener('mousedown', closeSubmenu);
                }
              };
              setTimeout(() => document.addEventListener('mousedown', closeSubmenu), 0);
            }}>
              Move channels to... <span className="context-menu-arrow">▶</span>
            </div>
            <div className="context-menu-item" onClick={handleCreateGroupAndMove}>
              Create new group and move
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
