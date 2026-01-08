import { useState, useEffect, useRef, useMemo } from 'react';
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
import type { Channel, ChannelGroup, ChannelProfile, Stream, M3UAccount, M3UGroupSetting, Logo, ChangeInfo, ChangeRecord, SavePoint, EPGData, EPGSource, StreamProfile, ChannelListFilterSettings } from '../types';
import { ChannelProfilesListModal } from './ChannelProfilesListModal';
import type { ChannelDefaults } from './StreamsPane';
import * as api from '../services/api';
import type { NumberSeparator } from '../services/api';
import { HistoryToolbar } from './HistoryToolbar';
import { BulkEPGAssignModal, type EPGAssignment } from './BulkEPGAssignModal';
import { EditChannelModal, type ChannelMetadataChanges } from './EditChannelModal';
import { naturalCompare } from '../utils/naturalSort';
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
  onStreamGroupDrop?: (groupNames: string[], streamIds: number[]) => void;
  // Bulk streams drop callback (for opening bulk create modal when dropping multiple streams)
  // Includes target group ID and starting channel number for pre-filling the modal
  onBulkStreamsDrop?: (streamIds: number[], groupId: number | null, startingNumber: number) => void;
  // Appearance settings
  showStreamUrls?: boolean;
  // EPG matching settings
  epgAutoMatchThreshold?: number;
  // External trigger to open edit modal for a specific channel
  externalChannelToEdit?: Channel | null;
  onExternalChannelEditHandled?: () => void;
}

interface GroupState {
  [groupId: number]: boolean;
}

interface SortableChannelProps {
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
}

interface SortableStreamItemProps {
  stream: Stream;
  providerName: string | null;
  isEditMode: boolean;
  onRemove: (streamId: number) => void;
  onCopyUrl?: () => void;
  showStreamUrls?: boolean;
}

function SortableStreamItem({ stream, providerName, isEditMode, onRemove, onCopyUrl, showStreamUrls = true }: SortableStreamItemProps) {
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
        {showStreamUrls && stream.url && (
          <span className="inline-stream-url" title={stream.url}>
            {stream.url}
          </span>
        )}
        {providerName && <span className="inline-stream-provider">{providerName}</span>}
      </div>
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
}

function SortableChannel({
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
}: SortableChannelProps) {
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
        {...attributes}
        {...listeners}
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
        {isExpanded ? '▼' : '▶'}
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
      <span className={`channel-streams-count ${channel.streams.length === 0 ? 'no-streams' : ''}`}>
        {channel.streams.length === 0 && <span className="material-icons warning-icon">warning</span>}
        {channel.streams.length} stream{channel.streams.length !== 1 ? 's' : ''}
      </span>
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
}

// Droppable Group Header component for cross-group channel dragging
interface DroppableGroupHeaderProps {
  groupId: number | 'ungrouped';
  groupName: string;
  channelCount: number;
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
}

function DroppableGroupHeader({
  groupId,
  groupName,
  channelCount,
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
}: DroppableGroupHeaderProps) {
  const droppableId = `group-${groupId}`;
  const { isOver, setNodeRef } = useDroppable({
    id: droppableId,
    disabled: !isEditMode,
  });

  const [streamDragOver, setStreamDragOver] = useState(false);

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
      <span className="group-toggle">{isExpanded ? '▼' : '▶'}</span>
      <span className="group-name">{groupName}</span>
      {isAutoSync && (
        <span className="group-auto-sync-badge" title="Auto-populated by channel sync">
          Auto-Sync
        </span>
      )}
      <span className="group-count">{channelCount}</span>
      {isEmpty && <span className="group-empty-badge">Empty</span>}
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
}

// Droppable zone at the end of a group (for dropping below the last channel)
interface DroppableGroupEndProps {
  groupId: number | 'ungrouped';
  isEditMode: boolean;
  showDropIndicator: boolean;
}

function DroppableGroupEnd({
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
}

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
  // Appearance settings
  showStreamUrls = true,
  // EPG matching settings
  epgAutoMatchThreshold = 80,
  // External trigger to open edit modal
  externalChannelToEdit,
  onExternalChannelEditHandled,
}: ChannelsPaneProps) {
  // Suppress unused variable warnings - these are passed through but handled in parent
  void _onStageBulkAssignNumbers;
  void onLogosChange;
  const [expandedGroups, setExpandedGroups] = useState<GroupState>({});
  const [dragOverChannelId, setDragOverChannelId] = useState<number | null>(null);
  const [localChannels, setLocalChannels] = useState<Channel[]>(channels);
  const [groupDropdownOpen, setGroupDropdownOpen] = useState(false);
  const [groupFilterSearch, setGroupFilterSearch] = useState('');
  const [filterSettingsOpen, setFilterSettingsOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const groupFilterSearchRef = useRef<HTMLInputElement>(null);
  const filterSettingsRef = useRef<HTMLDivElement>(null);

  // Create channel modal state
  const [showCreateModal, setShowCreateModal] = useState(false);

  // Channel profiles modal state
  const [showProfilesModal, setShowProfilesModal] = useState(false);
  const [newChannelName, setNewChannelName] = useState('');
  const [newChannelNumber, setNewChannelNumber] = useState('');
  const [newChannelGroup, setNewChannelGroup] = useState<number | ''>('');
  const [newChannelLogoId, setNewChannelLogoId] = useState<number | null>(null); // Logo from dropped stream
  const [newChannelLogoUrl, setNewChannelLogoUrl] = useState<string | null>(null); // Logo URL from dropped stream (used if no logo_id match)
  const [newChannelTvgId, setNewChannelTvgId] = useState<string | null>(null); // tvg_id from dropped stream
  const [newChannelStreamIds, setNewChannelStreamIds] = useState<number[]>([]); // Streams to assign after channel creation
  const [newChannelSelectedProfiles, setNewChannelSelectedProfiles] = useState<Set<number>>(new Set());
  const [newChannelProfilesExpanded, setNewChannelProfilesExpanded] = useState(false);
  // Naming options state for single channel create
  const [newChannelNamingExpanded, setNewChannelNamingExpanded] = useState(false);
  const [newChannelAddNumber, setNewChannelAddNumber] = useState(false);
  const [newChannelNumberSeparator, setNewChannelNumberSeparator] = useState<NumberSeparator>('-');
  const [newChannelStripCountry, setNewChannelStripCountry] = useState(false);
  const [groupSearchText, setGroupSearchText] = useState('');
  const [showGroupDropdown, setShowGroupDropdown] = useState(false);
  const [creating, setCreating] = useState(false);
  const [showConflictDialog, setShowConflictDialog] = useState(false);
  const [conflictingChannelNumber, setConflictingChannelNumber] = useState<number | null>(null);
  const groupInputRef = useRef<HTMLInputElement>(null);
  const groupDropdownListRef = useRef<HTMLDivElement>(null);

  // Edit channel number state
  const [editingChannelId, setEditingChannelId] = useState<number | null>(null);
  const [editingChannelNumber, setEditingChannelNumber] = useState('');

  // Edit channel name state
  const [editingNameChannelId, setEditingNameChannelId] = useState<number | null>(null);
  const [editingChannelName, setEditingChannelName] = useState('');

  // Inline stream display state
  const [channelStreams, setChannelStreams] = useState<Stream[]>([]);
  const [streamsLoading, setStreamsLoading] = useState(false);

  // Delete channel state
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [channelToDelete, setChannelToDelete] = useState<Channel | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [renumberAfterDelete, setRenumberAfterDelete] = useState(true);
  const [subsequentChannels, setSubsequentChannels] = useState<Channel[]>([]);

  // Edit channel modal state
  const [showEditChannelModal, setShowEditChannelModal] = useState(false);
  const [channelToEdit, setChannelToEdit] = useState<Channel | null>(null);

  // Stream group drop state (for bulk channel creation)
  const [streamGroupDragOver, setStreamGroupDragOver] = useState(false);

  // Create channel group modal state
  const [showCreateGroupModal, setShowCreateGroupModal] = useState(false);
  const [newGroupName, setNewGroupName] = useState('');
  const [creatingGroup, setCreatingGroup] = useState(false);

  // Delete group state
  const [showDeleteGroupConfirm, setShowDeleteGroupConfirm] = useState(false);
  const [groupToDelete, setGroupToDelete] = useState<ChannelGroup | null>(null);
  const [deletingGroup, setDeletingGroup] = useState(false);
  const [deleteGroupChannels, setDeleteGroupChannels] = useState(false);

  // Bulk delete channels state
  const [showBulkDeleteConfirm, setShowBulkDeleteConfirm] = useState(false);
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const [deleteEmptyGroups, setDeleteEmptyGroups] = useState(true); // Default to true since user is deleting all channels in group

  // Bulk EPG assignment modal state
  const [showBulkEPGModal, setShowBulkEPGModal] = useState(false);

  // Cross-group move modal state
  const [showCrossGroupMoveModal, setShowCrossGroupMoveModal] = useState(false);
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
  const [showSortRenumberModal, setShowSortRenumberModal] = useState(false);
  const [sortRenumberData, setSortRenumberData] = useState<{
    groupId: number | 'ungrouped';
    groupName: string;
    channels: Channel[];
    currentMinNumber: number | null;
  } | null>(null);
  const [sortRenumberStartingNumber, setSortRenumberStartingNumber] = useState<string>('');
  const [sortStripNumbers, setSortStripNumbers] = useState<boolean>(true);

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

  // Close dropdowns when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setGroupDropdownOpen(false);
        setGroupFilterSearch('');
      }
      if (filterSettingsRef.current && !filterSettingsRef.current.contains(event.target as Node)) {
        setFilterSettingsOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Handle external trigger to open edit modal from Guide tab
  useEffect(() => {
    if (externalChannelToEdit) {
      setChannelToEdit(externalChannelToEdit);
      setShowEditChannelModal(true);
      onExternalChannelEditHandled?.();
    }
  }, [externalChannelToEdit, onExternalChannelEditHandled]);

  // Close group autocomplete dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (
        groupInputRef.current &&
        !groupInputRef.current.contains(event.target as Node) &&
        groupDropdownListRef.current &&
        !groupDropdownListRef.current.contains(event.target as Node)
      ) {
        setShowGroupDropdown(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Filter channel groups based on search text (for create modal dropdown)
  const searchFilteredChannelGroups = channelGroups.filter((group) =>
    group.name.toLowerCase().includes(groupSearchText.toLowerCase())
  );

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

    setShowDeleteConfirm(true);
  };

  // Handle opening edit channel modal
  const handleEditChannel = (channel: Channel) => {
    setChannelToEdit(channel);
    setShowEditChannelModal(true);
  };

  // Helper to get logo URL for a channel
  const getChannelLogoUrl = (channel: Channel): string | null => {
    // For staged channels (during edit mode), use the temporary logo URL
    if (channel._stagedLogoUrl) {
      return channel._stagedLogoUrl;
    }
    if (!channel.logo_id) return null;
    const logo = logos.find((l) => l.id === channel.logo_id);
    return logo?.cache_url || logo?.url || null;
  };

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
          const newName = computeAutoRename(ch.name, ch.channel_number, newNumber);
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
              const newName = computeAutoRename(ch.name, ch.channel_number, newNumber);
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
      setShowDeleteConfirm(false);
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
    setShowDeleteConfirm(false);
    setChannelToDelete(null);
    setSubsequentChannels([]);
    setRenumberAfterDelete(true);
  };

  // Handle initiating group deletion
  const handleDeleteGroupClick = (group: ChannelGroup) => {
    setGroupToDelete(group);
    setShowDeleteGroupConfirm(true);
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

      setShowDeleteGroupConfirm(false);
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
    setShowDeleteGroupConfirm(false);
    setGroupToDelete(null);
    setDeleteGroupChannels(false);
  };

  // Handle bulk delete channels
  const handleBulkDeleteClick = () => {
    if (selectedChannelIds.size === 0) return;
    setShowBulkDeleteConfirm(true);
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

      setShowBulkDeleteConfirm(false);
    } catch (err) {
      console.error('Failed to bulk delete channels:', err);
    } finally {
      setBulkDeleting(false);
    }
  };

  const handleCancelBulkDelete = () => {
    setShowBulkDeleteConfirm(false);
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
    setShowBulkEPGModal(false);
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
    setShowCreateGroupModal(false);
    setNewGroupName('');
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
      handleCloseCreateGroupModal();
    } catch (err) {
      console.error('Failed to create channel group:', err);
    } finally {
      setCreatingGroup(false);
    }
  };

  // Close the create modal and reset form state
  const handleCloseCreateModal = () => {
    setShowCreateModal(false);
    setShowConflictDialog(false);
    setConflictingChannelNumber(null);
    setNewChannelName('');
    setNewChannelNumber('');
    setNewChannelGroup('');
    setNewChannelLogoId(null);
    setNewChannelLogoUrl(null);
    setNewChannelTvgId(null);
    setNewChannelStreamIds([]);
    setGroupSearchText('');
    setShowGroupDropdown(false);
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

  // Check if a channel number already exists
  // Use localChannels in edit mode since it may have been modified
  const channelNumberExists = (num: number): boolean => {
    const sourceChannels = isEditMode ? localChannels : channels;
    return sourceChannels.some((ch) => ch.channel_number === num);
  };

  // Handle stream dropped on group header - creates new channel with stream name
  // Supports multiple streams being dropped at once (e.g., same stream from different providers)
  const handleStreamDropOnGroup = (groupId: number | 'ungrouped', streamIds: number[]) => {
    if (streamIds.length === 0) return;

    // Get all dropped streams
    const droppedStreams = streamIds
      .map(id => allStreams.find((s: Stream) => s.id === id))
      .filter((s): s is Stream => s !== undefined);
    if (droppedStreams.length === 0) return;

    // Check if there are multiple unique stream names (would create multiple channels)
    const uniqueNormalizedNames = new Set(
      droppedStreams.map(s => api.normalizeStreamName(s.name, 'both'))
    );

    // Calculate the starting channel number for this group
    const numericGroupId = groupId === 'ungrouped' ? '' : groupId;
    const nextNumber = getNextChannelNumberForGroup(numericGroupId);
    const targetGroupId = groupId === 'ungrouped' ? null : groupId;

    // If multiple unique names, use bulk create modal instead
    if (uniqueNormalizedNames.size > 1 && onBulkStreamsDrop) {
      onBulkStreamsDrop(streamIds, targetGroupId, nextNumber);
      return;
    }

    // Use the first stream's info for the channel details
    const firstStream = droppedStreams[0];

    // Use stream name as the channel name
    setNewChannelName(firstStream.name);

    // Find matching logo by URL if stream has a logo_url
    // Always capture the logo_url for fallback during commit
    setNewChannelLogoUrl(firstStream.logo_url ?? null);
    if (firstStream.logo_url) {
      const matchingLogo = logos.find(
        (logo) => logo.url === firstStream.logo_url || logo.cache_url === firstStream.logo_url
      );
      setNewChannelLogoId(matchingLogo?.id ?? null);
    } else {
      setNewChannelLogoId(null);
    }

    // Capture the stream's tvg_id for the new channel
    setNewChannelTvgId(firstStream.tvg_id ?? null);

    // Store all stream IDs to assign after channel creation
    setNewChannelStreamIds(streamIds);

    // Set the group (handle 'ungrouped' case)
    if (groupId === 'ungrouped') {
      setNewChannelGroup('');
      setGroupSearchText('');
    } else {
      const group = channelGroups.find((g) => g.id === groupId);
      setNewChannelGroup(groupId);
      setGroupSearchText(group?.name || '');
    }
    setNewChannelNumber(nextNumber.toString());

    // Set default channel profile from settings
    setNewChannelSelectedProfiles(
      channelDefaults?.defaultChannelProfileId ? new Set([channelDefaults.defaultChannelProfileId]) : new Set()
    );
    setNewChannelProfilesExpanded(false);

    // Set naming options from settings defaults
    setNewChannelAddNumber(channelDefaults?.includeChannelNumberInName ?? false);
    setNewChannelNumberSeparator((channelDefaults?.channelNumberSeparator as NumberSeparator) || '-');
    setNewChannelStripCountry(channelDefaults?.removeCountryPrefix ?? false);
    setNewChannelNamingExpanded(false);

    // Open the create modal
    setShowCreateModal(true);
  };

  // Handle stream dropped between channels - creates new channel at specific position
  const handleStreamDropAtPosition = (
    groupId: number | 'ungrouped',
    streamIds: number[],
    insertAtChannelNumber: number
  ) => {
    if (streamIds.length === 0) return;

    // Get all dropped streams
    const droppedStreams = streamIds
      .map(id => allStreams.find((s: Stream) => s.id === id))
      .filter((s): s is Stream => s !== undefined);
    if (droppedStreams.length === 0) return;

    // Check if there are multiple unique stream names (would create multiple channels)
    const uniqueNormalizedNames = new Set(
      droppedStreams.map(s => api.normalizeStreamName(s.name, 'both'))
    );

    const targetGroupId = groupId === 'ungrouped' ? null : groupId;

    // If multiple unique names, use bulk create modal instead
    if (uniqueNormalizedNames.size > 1 && onBulkStreamsDrop) {
      onBulkStreamsDrop(streamIds, targetGroupId, insertAtChannelNumber);
      return;
    }

    // Use the first stream's info for the channel details
    const firstStream = droppedStreams[0];

    // Use stream name as the channel name
    setNewChannelName(firstStream.name);

    // Find matching logo by URL if stream has a logo_url
    setNewChannelLogoUrl(firstStream.logo_url ?? null);
    if (firstStream.logo_url) {
      const matchingLogo = logos.find(
        (logo) => logo.url === firstStream.logo_url || logo.cache_url === firstStream.logo_url
      );
      setNewChannelLogoId(matchingLogo?.id ?? null);
    } else {
      setNewChannelLogoId(null);
    }

    // Capture the stream's tvg_id for the new channel
    setNewChannelTvgId(firstStream.tvg_id ?? null);

    // Store all stream IDs to assign after channel creation
    setNewChannelStreamIds(streamIds);

    // Set the group
    if (groupId === 'ungrouped') {
      setNewChannelGroup('');
      setGroupSearchText('');
    } else {
      const group = channelGroups.find((g) => g.id === groupId);
      setNewChannelGroup(groupId);
      setGroupSearchText(group?.name || '');
    }

    // Use the specific insert position
    setNewChannelNumber(insertAtChannelNumber.toString());

    // Set default channel profile from settings
    setNewChannelSelectedProfiles(
      channelDefaults?.defaultChannelProfileId ? new Set([channelDefaults.defaultChannelProfileId]) : new Set()
    );
    setNewChannelProfilesExpanded(false);

    // Set naming options from settings defaults
    setNewChannelAddNumber(channelDefaults?.includeChannelNumberInName ?? false);
    setNewChannelNumberSeparator((channelDefaults?.channelNumberSeparator as NumberSeparator) || '-');
    setNewChannelStripCountry(channelDefaults?.removeCountryPrefix ?? false);
    setNewChannelNamingExpanded(false);

    // Open the create modal
    setShowCreateModal(true);
  };

  // Handle creating a new channel - checks for conflicts first
  const handleCreateChannel = async () => {
    if (!newChannelName.trim() || !newChannelNumber.trim()) return;

    const channelNum = parseFloat(newChannelNumber);
    if (isNaN(channelNum)) return;

    // Check if this channel number already exists
    if (channelNumberExists(channelNum)) {
      setConflictingChannelNumber(channelNum);
      setShowConflictDialog(true);
      return;
    }

    // No conflict, create the channel directly
    await createChannelWithNumber(channelNum);
  };

  // Create channel with the specified number (after conflict resolution or no conflict)
  const createChannelWithNumber = async (channelNum: number) => {
    setCreating(true);
    try {
      // Build the final channel name with naming options applied
      let finalName = newChannelName.trim();

      // Strip country prefix if requested (e.g., "US : BET Gospel" -> "BET Gospel")
      if (newChannelStripCountry) {
        finalName = api.stripCountryPrefix(finalName);
      }

      // Add channel number prefix if requested
      if (newChannelAddNumber) {
        finalName = `${channelNum} ${newChannelNumberSeparator} ${finalName}`;
      }

      // Pass profile IDs to onCreateChannel - it handles both edit mode (pending assignments) and normal mode (immediate API calls)
      const profileIdsArray = newChannelSelectedProfiles.size > 0 ? Array.from(newChannelSelectedProfiles) : undefined;

      const newChannel = await onCreateChannel(
        finalName,
        channelNum,
        newChannelGroup !== '' ? newChannelGroup : undefined,
        newChannelLogoId ?? undefined,
        newChannelTvgId ?? undefined,
        newChannelLogoUrl ?? undefined,
        profileIdsArray
      );

      // Assign the streams to the channel if any were dropped
      if (newChannelStreamIds.length > 0 && newChannel?.id) {
        if (isEditMode && onStageAddStream) {
          // In edit mode, stage all stream assignments
          for (const streamId of newChannelStreamIds) {
            const stream = allStreams.find((s: Stream) => s.id === streamId);
            onStageAddStream(newChannel.id, streamId, `Added stream "${stream?.name || streamId}" to channel`);
          }
        } else {
          // In normal mode, call the API directly for each stream
          for (const streamId of newChannelStreamIds) {
            try {
              await onChannelDrop(newChannel.id, streamId);
            } catch (err) {
              console.error(`Failed to assign stream ${streamId} to channel ${newChannel.id}:`, err);
            }
          }
        }
      }

      // Profile assignment is now handled by onCreateChannel (in normal mode it makes API calls immediately,
      // in edit mode it stores them in pendingProfileAssignmentsRef to apply after commit)

      // In edit mode, the channel is added to workingCopy by stageCreateChannel,
      // which flows through the channels prop and syncs to localChannels via useEffect.
      // We don't manually add here to avoid duplicates.
      handleCloseCreateModal();
    } catch {
      // Error handled in parent
    } finally {
      setCreating(false);
    }
  };

  // Handle conflict resolution: push channels down to make room
  const handleConflictPushDown = async () => {
    if (conflictingChannelNumber === null) return;

    setCreating(true);
    setShowConflictDialog(false);

    try {
      // Get channels that need to be shifted (>= the conflicting number)
      // Use localChannels in edit mode since it may have been modified
      // IMPORTANT: Push down ALL channels with >= the number, across ALL groups
      // Channel numbers must be unique globally, not just within a group
      const sourceChannels = isEditMode ? localChannels : channels;
      const channelsToShift = sourceChannels
        .filter((ch) => ch.channel_number !== null && ch.channel_number >= conflictingChannelNumber)
        .sort((a, b) => (b.channel_number ?? 0) - (a.channel_number ?? 0)); // Sort descending to avoid conflicts

      // If in edit mode, stage the shifts; otherwise, this would need API calls
      if (isEditMode && onStageUpdateChannel) {
        // Shift each channel down by 1 (starting from highest to avoid conflicts)
        for (const ch of channelsToShift) {
          const newNum = ch.channel_number! + 1;
          const newName = computeAutoRename(ch.name, ch.channel_number, newNum);
          const description = newName
            ? `Changed "${ch.name}" to "${newName}"`
            : `Changed channel number from ${ch.channel_number} to ${newNum}`;
          onStageUpdateChannel(ch.id, {
            channel_number: newNum,
            ...(newName ? { name: newName } : {})
          }, description);
        }

        // Update local state
        setLocalChannels((prev) =>
          prev.map((ch) => {
            const shift = channelsToShift.find((s) => s.id === ch.id);
            if (shift) {
              const newNum = ch.channel_number! + 1;
              const newName = computeAutoRename(ch.name, ch.channel_number, newNum);
              return { ...ch, channel_number: newNum, ...(newName ? { name: newName } : {}) };
            }
            return ch;
          })
        );
      }

      // Now create the new channel at the original number
      await createChannelWithNumber(conflictingChannelNumber);
    } catch {
      // Error handled
    } finally {
      setCreating(false);
    }
  };

  // Handle conflict resolution: add to end of group
  const handleConflictAddToEnd = async () => {
    setShowConflictDialog(false);
    const endNumber = getNextChannelNumberForGroup(newChannelGroup);
    await createChannelWithNumber(endNumber);
  };

  // Handle selecting a group from the autocomplete dropdown
  const handleSelectGroup = (group: ChannelGroup | null) => {
    if (group) {
      setNewChannelGroup(group.id);
      setGroupSearchText(group.name);
    } else {
      setNewChannelGroup('');
      setGroupSearchText('');
    }
    setShowGroupDropdown(false);
  };

  // Helper function to compute auto-rename for a channel number change
  // Returns the new name if auto-rename should apply, undefined otherwise
  const computeAutoRename = (
    channelName: string,
    _oldNumber: number | null,
    newNumber: number | null
  ): string | undefined => {
    if (!autoRenameChannelNumber || newNumber === null) {
      return undefined;
    }

    const newNumberStr = String(newNumber);

    // Check for number in the middle: "US | 5034 - DABL" or "US | 5034: DABL"
    // Pattern: PREFIX | NUMBER - SUFFIX (where PREFIX doesn't start with a digit)
    const midMatch = channelName.match(/^([A-Za-z].+?\s*\|\s*)(\d+(?:\.\d+)?)\s*([-:]\s*.+)$/);
    if (midMatch) {
      const [, prefix, oldNum, suffix] = midMatch;
      // If the number is already the new number, no change needed
      if (oldNum === newNumberStr) {
        return undefined;
      }
      // Replace the number in the middle
      const newName = `${prefix}${newNumberStr} ${suffix}`;
      return newName !== channelName ? newName : undefined;
    }

    // Look for a number at the beginning of the channel name
    // Pattern: "123 | Channel Name" or "123 - Channel Name" or "123: Channel Name" or "123 Channel Name"
    // This matches a number at the start followed by a separator (space, |, -, :, .)
    const prefixMatch = channelName.match(/^(\d+(?:\.\d+)?)\s*([|\-:.\s])\s*(.*)$/);

    if (prefixMatch) {
      const [, oldPrefix, separator, rest] = prefixMatch;
      // If the prefix is already the new number, no change needed
      if (oldPrefix === newNumberStr) {
        return undefined;
      }
      // Replace the prefix with the new number
      const newName = `${newNumberStr}${separator === ' ' ? ' ' : ` ${separator} `}${rest}`;
      return newName !== channelName ? newName : undefined;
    }

    // Also check for number at the end: "Channel Name | 123"
    const suffixMatch = channelName.match(/^(.*)\s*([|\-.])\s*(\d+(?:\.\d+)?)$/);
    if (suffixMatch) {
      const [, prefix, separator, oldSuffix] = suffixMatch;
      // If the suffix is already the new number, no change needed
      if (oldSuffix === newNumberStr) {
        return undefined;
      }
      // Replace the suffix with the new number
      const newName = `${prefix} ${separator} ${newNumberStr}`;
      return newName !== channelName ? newName : undefined;
    }

    return undefined;
  };

  // Helper function to strip leading/trailing/middle channel numbers from a name for sorting purposes
  // Matches same patterns as computeAutoRename: "123 | Name", "123-Name", "US | 5034 - Name", "Name | 123"
  const getNameForSorting = (channelName: string): string => {
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
  };

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
    if (channel) {
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
          onStreamGroupDrop(groupNames, streamIds);
        } catch {
          console.error('Failed to parse stream group drop data');
        }
      } else {
        // Fallback to single group (backward compatibility)
        const groupName = e.dataTransfer.getData('streamGroupName');
        if (groupName && streamIdsJson) {
          try {
            const streamIds = JSON.parse(streamIdsJson) as number[];
            onStreamGroupDrop([groupName], streamIds);
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
    return false; // Hide auto-created channels from non-auto-sync groups
  });
  const channelsByGroup = visibleChannels.reduce<Record<number | 'ungrouped', Channel[]>>(
    (acc, channel) => {
      const key = channel.channel_group_id ?? 'ungrouped';
      if (!acc[key]) acc[key] = [];
      acc[key].push(channel);
      return acc;
    },
    { ungrouped: [] }
  );

  // Sort channels within each group by channel_number
  Object.values(channelsByGroup).forEach((group) => {
    group.sort((a, b) => (a.channel_number ?? 9999) - (b.channel_number ?? 9999));
  });

  // Sort channel groups by their lowest channel number (only groups with channels)
  const sortedChannelGroups = [...channelGroups]
    .filter((g) => channelsByGroup[g.id]?.length > 0)
    .sort((a, b) => {
      const aMin = channelsByGroup[a.id]?.[0]?.channel_number ?? 9999;
      const bMin = channelsByGroup[b.id]?.[0]?.channel_number ?? 9999;
      return aMin - bMin;
    });

  // All groups sorted alphabetically with natural sort (for filter dropdown - includes empty groups)
  const allGroupsSorted = [...channelGroups].sort((a, b) => naturalCompare(a.name, b.name));

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
  const filteredChannelGroups = sortedChannelGroups.filter((g) => shouldShowGroup(g.id));

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

    if (isCrossGroupMove) {
      // Collect channels to move: if the dragged channel is part of multi-selection, move all selected
      // Otherwise, just move the single dragged channel
      let channelsToMove: Channel[] = [];
      if (selectedChannelIds.has(activeChannel.id) && selectedChannelIds.size > 1) {
        // Multi-selection: collect all selected channels from the same source group
        channelsToMove = localChannels.filter(
          (ch) => selectedChannelIds.has(ch.id) && ch.channel_group_id === activeChannel.channel_group_id
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
      const sourceGroupName = activeChannel.channel_group_id === null
        ? 'Uncategorized'
        : channelGroups.find((g) => g.id === activeChannel.channel_group_id)?.name ?? 'Unknown Group';

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
      const sourceGroupId = activeChannel.channel_group_id;
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
      let sourceGroupHasGaps = false;
      let sourceGroupMinChannel: number | null = null;
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
      setShowCrossGroupMoveModal(true);

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

    // Reorder locally for immediate feedback
    const reorderedGroup = [...groupChannels];
    const [removed] = reorderedGroup.splice(oldIndex, 1);
    reorderedGroup.splice(newIndex, 0, removed);

    if (isContiguous) {
      // Channels are contiguous - renumber them sequentially
      // Use the starting number from the original group (before reorder)
      const startingNumber = groupChannels[0]?.channel_number ?? 1;

      // Calculate new numbers and auto-rename for each channel
      const channelUpdates: Array<{ id: number; newNumber: number; newName?: string; oldName: string }> = [];
      reorderedGroup.forEach((ch, index) => {
        const newNumber = startingNumber + index;
        const newName = computeAutoRename(ch.name, ch.channel_number, newNumber);
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
      // The dragged channel takes the target position, and channels in between shift
      // All channels must have a number, so we can safely use them
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

        const newName = computeAutoRename(ch.name, chNum, newNumber);
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
          const newName = computeAutoRename(channel.name, channel.channel_number, newNumber);
          if (newName) {
            finalName = newName;
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
          const newName = computeAutoRename(channel.name, channel.channel_number, newNumber);
          if (newName) {
            finalName = newName;
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
      if (!keepChannelNumber && finalChannelNumber !== null && finalChannelNumber !== channel.channel_number) {
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
    setShowCrossGroupMoveModal(false);
    setCrossGroupMoveData(null);
  };

  const handleCrossGroupMoveCancel = () => {
    setShowCrossGroupMoveModal(false);
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
    setShowSortRenumberModal(true);
  };

  const handleSortRenumberCancel = () => {
    setShowSortRenumberModal(false);
    setSortRenumberData(null);
    setSortRenumberStartingNumber('');
    setSortStripNumbers(true);
  };

  const handleSortRenumberConfirm = () => {
    if (!sortRenumberData || !onStageUpdateChannel) return;

    const startingNumber = parseInt(sortRenumberStartingNumber, 10);
    if (isNaN(startingNumber) || startingNumber < 1) return;

    // Sort channels alphabetically by name (case-insensitive, natural sort for numbers)
    // If sortStripNumbers is enabled, strip channel numbers from names before comparing
    const sortedChannels = [...sortRenumberData.channels].sort((a, b) => {
      const nameA = sortStripNumbers ? getNameForSorting(a.name) : a.name;
      const nameB = sortStripNumbers ? getNameForSorting(b.name) : b.name;
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
        // Apply auto-rename if enabled
        let updates: Partial<Channel> = { channel_number: newNumber };
        if (autoRenameChannelNumber && channel.channel_number !== null) {
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
    setShowSortRenumberModal(false);
    setSortRenumberData(null);
    setSortRenumberStartingNumber('');
    setSortStripNumbers(true);
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

    // Handler to select/deselect all channels in this group
    const handleSelectAllInGroup = () => {
      if (!onSelectGroupChannels) return;
      const allSelected = selectedCountInGroup === groupChannels.length;
      // If all are selected, deselect all; otherwise select all
      onSelectGroupChannels(allGroupChannelIds, !allSelected);
    };

    return (
      <div key={groupId} className={`channel-group ${isEmpty ? 'empty-group' : ''}`}>
        <DroppableGroupHeader
          groupId={groupId}
          groupName={groupName}
          channelCount={groupChannels.length}
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
                    <SortableChannel
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
                      onCopyChannelUrl={dispatcharrUrl && channel.uuid ? () => navigator.clipboard.writeText(`${dispatcharrUrl}/proxy/ts/stream/${channel.uuid}`) : undefined}
                      channelUrl={dispatcharrUrl && channel.uuid ? `${dispatcharrUrl}/proxy/ts/stream/${channel.uuid}` : undefined}
                      showStreamUrls={showStreamUrls}
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
                                    <SortableStreamItem
                                      stream={stream}
                                      providerName={providers.find((p) => p.id === stream.m3u_account)?.name ?? null}
                                      isEditMode={isEditMode}
                                      onRemove={handleRemoveStream}
                                      onCopyUrl={stream.url ? () => navigator.clipboard.writeText(stream.url!) : undefined}
                                      showStreamUrls={showStreamUrls}
                                    />
                                  </div>
                                ))}
                              </div>
                            </SortableContext>
                          </DndContext>
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
          {isEditMode && selectedChannelIds.size > 0 && (
            <div className="selection-info">
              <span className="selection-count">{selectedChannelIds.size} selected</span>
              <button
                className="bulk-epg-btn"
                onClick={() => setShowBulkEPGModal(true)}
                title="Assign EPG to selected channels"
              >
                <span className="material-icons">live_tv</span>
                EPG
              </button>
              <button
                className="bulk-delete-btn"
                onClick={handleBulkDeleteClick}
                title="Delete selected channels"
              >
                <span className="material-icons">delete</span>
                Delete
              </button>
              <button
                className="clear-selection-btn"
                onClick={onClearChannelSelection}
                title="Clear selection"
              >
                Clear
              </button>
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
                onClick={() => {
                  // Reset form state
                  setNewChannelName('');
                  setNewChannelNumber('');
                  setNewChannelGroup('');
                  setGroupSearchText('');
                  setNewChannelLogoId(null);
                  setNewChannelLogoUrl(null);
                  setNewChannelTvgId(null);
                  setNewChannelStreamIds([]);
                  // Set default channel profile from settings
                  setNewChannelSelectedProfiles(
                    channelDefaults?.defaultChannelProfileId ? new Set([channelDefaults.defaultChannelProfileId]) : new Set()
                  );
                  setNewChannelProfilesExpanded(false);
                  // Set naming options from settings defaults
                  setNewChannelAddNumber(channelDefaults?.includeChannelNumberInName ?? false);
                  setNewChannelNumberSeparator((channelDefaults?.channelNumberSeparator as NumberSeparator) || '-');
                  setNewChannelStripCountry(channelDefaults?.removeCountryPrefix ?? false);
                  setNewChannelNamingExpanded(false);
                  setShowCreateModal(true);
                }}
                title="Create new channel"
              >
                <span className="material-icons create-channel-icon">add</span>
                <span>Channel</span>
              </button>
              <button
                className="create-group-btn"
                onClick={() => setShowCreateGroupModal(true)}
                title="Create new channel group"
              >
                <span className="material-icons create-channel-icon">create_new_folder</span>
                <span>Group</span>
              </button>
            </>
          )}
          <button
            className="profiles-btn"
            onClick={() => setShowProfilesModal(true)}
            title="Manage channel profiles"
          >
            <span className="material-icons">group</span>
            <span>Profiles</span>
          </button>
        </div>
      </div>

      {/* Create Channel Modal */}
      {showCreateModal && (
        <div className="modal-overlay">
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <h3>Create New Channel</h3>
            <div className="modal-form">
              <label>
                Channel Name *
                <input
                  type="text"
                  value={newChannelName}
                  onChange={(e) => setNewChannelName(e.target.value)}
                  placeholder="e.g., Sports Channel HD"
                  autoFocus
                />
              </label>
              <label>
                Channel Number *
                <input
                  type="number"
                  value={newChannelNumber}
                  onChange={(e) => setNewChannelNumber(e.target.value)}
                  placeholder="e.g., 100"
                  min="1"
                  step="any"
                />
              </label>
              <label>
                Channel Group
                <div className="group-autocomplete">
                  <input
                    ref={groupInputRef}
                    type="text"
                    value={groupSearchText}
                    onChange={(e) => {
                      setGroupSearchText(e.target.value);
                      setShowGroupDropdown(true);
                      // Clear selection if user is typing something different
                      if (newChannelGroup !== '') {
                        const selectedGroup = channelGroups.find((g) => g.id === newChannelGroup);
                        if (selectedGroup && e.target.value !== selectedGroup.name) {
                          setNewChannelGroup('');
                        }
                      }
                    }}
                    onFocus={() => setShowGroupDropdown(true)}
                    placeholder="Type to search or leave empty..."
                  />
                  {groupSearchText && (
                    <button
                      type="button"
                      className="group-autocomplete-clear"
                      onClick={() => handleSelectGroup(null)}
                    >
                      ✕
                    </button>
                  )}
                  {showGroupDropdown && (
                    <div ref={groupDropdownListRef} className="group-autocomplete-dropdown">
                      <div
                        className={`group-autocomplete-option ${newChannelGroup === '' ? 'selected' : ''}`}
                        onClick={() => handleSelectGroup(null)}
                      >
                        No Group
                      </div>
                      {searchFilteredChannelGroups.map((group) => (
                        <div
                          key={group.id}
                          className={`group-autocomplete-option ${newChannelGroup === group.id ? 'selected' : ''}`}
                          onClick={() => handleSelectGroup(group)}
                        >
                          {group.name}
                        </div>
                      ))}
                      {searchFilteredChannelGroups.length === 0 && groupSearchText && (
                        <div className="group-autocomplete-empty">No matching groups</div>
                      )}
                    </div>
                  )}
                </div>
              </label>

              {/* Naming Options Section */}
              <div className="collapsible-section">
                <button
                  type="button"
                  className={`collapsible-header ${newChannelNamingExpanded ? 'expanded' : ''}`}
                  onClick={() => setNewChannelNamingExpanded(!newChannelNamingExpanded)}
                >
                  <span className="material-icons">
                    {newChannelNamingExpanded ? 'expand_more' : 'chevron_right'}
                  </span>
                  <span>Naming Options</span>
                  <span className="collapsible-summary">
                    {(() => {
                      const options: string[] = [];
                      if (newChannelStripCountry) options.push('Strip country');
                      if (newChannelAddNumber) options.push(`Add # (${newChannelNumberSeparator})`);
                      return options.length > 0 ? options.join(', ') : 'Default';
                    })()}
                  </span>
                </button>
                {newChannelNamingExpanded && (
                  <div className="collapsible-content naming-options">
                    {/* Strip country prefix - only show if detected */}
                    {api.getCountryPrefix(newChannelName) && (
                      <label className="naming-option">
                        <input
                          type="checkbox"
                          checked={newChannelStripCountry}
                          onChange={(e) => setNewChannelStripCountry(e.target.checked)}
                        />
                        <span>Strip country prefix ({api.getCountryPrefix(newChannelName)})</span>
                      </label>
                    )}

                    {/* Add channel number to name */}
                    <label className="naming-option">
                      <input
                        type="checkbox"
                        checked={newChannelAddNumber}
                        onChange={(e) => setNewChannelAddNumber(e.target.checked)}
                      />
                      <span>Add channel number to name</span>
                    </label>
                    {newChannelAddNumber && (
                      <div className="separator-options">
                        <span className="separator-label">Separator:</span>
                        <button
                          type="button"
                          className={`separator-btn ${newChannelNumberSeparator === '-' ? 'active' : ''}`}
                          onClick={() => setNewChannelNumberSeparator('-')}
                        >
                          -
                        </button>
                        <button
                          type="button"
                          className={`separator-btn ${newChannelNumberSeparator === ':' ? 'active' : ''}`}
                          onClick={() => setNewChannelNumberSeparator(':')}
                        >
                          :
                        </button>
                        <button
                          type="button"
                          className={`separator-btn ${newChannelNumberSeparator === '|' ? 'active' : ''}`}
                          onClick={() => setNewChannelNumberSeparator('|')}
                        >
                          |
                        </button>
                      </div>
                    )}

                    {/* Preview - show when any naming option is active */}
                    {(newChannelStripCountry || newChannelAddNumber) && newChannelName && newChannelNumber && (
                      <div className="naming-preview">
                        <span className="preview-label">Preview:</span>
                        <span className="preview-name">
                          {(() => {
                            let preview = newChannelName;
                            if (newChannelStripCountry) {
                              preview = api.stripCountryPrefix(preview);
                            }
                            if (newChannelAddNumber) {
                              preview = `${newChannelNumber} ${newChannelNumberSeparator} ${preview}`;
                            }
                            return preview;
                          })()}
                        </span>
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* Channel Profiles Section */}
              {channelProfiles.length > 0 && (
                <div className="collapsible-section">
                  <button
                    type="button"
                    className={`collapsible-header ${newChannelProfilesExpanded ? 'expanded' : ''}`}
                    onClick={() => setNewChannelProfilesExpanded(!newChannelProfilesExpanded)}
                  >
                    <span className="material-icons">
                      {newChannelProfilesExpanded ? 'expand_more' : 'chevron_right'}
                    </span>
                    <span>Channel Profiles</span>
                    <span className="collapsible-summary">
                      {newChannelSelectedProfiles.size === 0
                        ? 'None selected'
                        : `${newChannelSelectedProfiles.size} selected`}
                    </span>
                  </button>
                  {newChannelProfilesExpanded && (
                    <div className="collapsible-content profile-list">
                      {channelProfiles.map((profile) => (
                        <label key={profile.id} className="profile-checkbox">
                          <input
                            type="checkbox"
                            checked={newChannelSelectedProfiles.has(profile.id)}
                            onChange={() => {
                              const newSet = new Set(newChannelSelectedProfiles);
                              if (newSet.has(profile.id)) {
                                newSet.delete(profile.id);
                              } else {
                                newSet.add(profile.id);
                              }
                              setNewChannelSelectedProfiles(newSet);
                            }}
                          />
                          <span>{profile.name}</span>
                        </label>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
            <div className="modal-actions">
              <button
                className="modal-btn cancel"
                onClick={handleCloseCreateModal}
                disabled={creating}
              >
                Cancel
              </button>
              <button
                className="modal-btn primary"
                onClick={handleCreateChannel}
                disabled={creating || !newChannelName.trim() || !newChannelNumber.trim()}
              >
                {creating ? 'Creating...' : 'Create Channel'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Create Channel Group Modal */}
      {showCreateGroupModal && (
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

      {/* Channel Number Conflict Dialog */}
      {showConflictDialog && conflictingChannelNumber !== null && (
        <div className="modal-overlay">
          <div className="modal-content conflict-dialog" onClick={(e) => e.stopPropagation()}>
            <h3>Channel Number Conflict</h3>
            <div className="conflict-message">
              <p>
                Channel number <strong>{conflictingChannelNumber}</strong> is already in use.
              </p>
              <p>How would you like to proceed?</p>
            </div>
            <div className="conflict-options">
              <button
                className="conflict-option-btn push-down"
                onClick={handleConflictPushDown}
                disabled={creating}
              >
                <span className="material-icons">vertical_align_bottom</span>
                <div className="conflict-option-text">
                  <strong>Push channels down</strong>
                  <span>Insert at {conflictingChannelNumber} and shift existing channels</span>
                </div>
              </button>
              <button
                className="conflict-option-btn add-to-end"
                onClick={handleConflictAddToEnd}
                disabled={creating}
              >
                <span className="material-icons">last_page</span>
                <div className="conflict-option-text">
                  <strong>Add to end</strong>
                  <span>Use next available number ({getNextChannelNumberForGroup(newChannelGroup)})</span>
                </div>
              </button>
            </div>
            <div className="modal-actions">
              <button
                className="modal-btn cancel"
                onClick={() => setShowConflictDialog(false)}
                disabled={creating}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete Channel Confirmation Dialog */}
      {showDeleteConfirm && channelToDelete && (
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
                      const newName = computeAutoRename(ch.name, ch.channel_number, newNumber);
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
      {showDeleteGroupConfirm && groupToDelete && (
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
      {showBulkDeleteConfirm && selectedChannelIds.size > 0 && (() => {
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
        isOpen={showBulkEPGModal && selectedChannelIds.size > 0}
        selectedChannels={channels.filter(c => selectedChannelIds.has(c.id))}
        streams={allStreams}
        epgData={epgData || []}
        epgSources={epgSources || []}
        onClose={() => setShowBulkEPGModal(false)}
        onAssign={handleBulkEPGAssign}
        epgAutoMatchThreshold={epgAutoMatchThreshold}
      />

      {/* Edit Channel Modal */}
      {showEditChannelModal && channelToEdit && (
        <EditChannelModal
          channel={channelToEdit}
          logos={logos}
          epgData={epgData}
          epgSources={epgSources}
          streamProfiles={streamProfiles}
          epgDataLoading={epgDataLoading}
          onClose={() => {
            setShowEditChannelModal(false);
            setChannelToEdit(null);
          }}
          onSave={async (changes: ChannelMetadataChanges) => {
            if (Object.keys(changes).length === 0) {
              setShowEditChannelModal(false);
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
              const logoName = changes.logo_id ? logos.find((l) => l.id === changes.logo_id)?.name : null;
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
            setShowEditChannelModal(false);
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
      {showCrossGroupMoveModal && crossGroupMoveData && (
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

      {/* Sort & Renumber Modal */}
      {showSortRenumberModal && sortRenumberData && (
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
            </div>

            {/* Preview of sorted order */}
            <div className="sort-renumber-preview">
              <label>Preview (sorted A–Z)</label>
              <ul className="sort-renumber-preview-list">
                {[...sortRenumberData.channels]
                  .sort((a, b) => {
                    const nameA = sortStripNumbers ? getNameForSorting(a.name) : a.name;
                    const nameB = sortStripNumbers ? getNameForSorting(b.name) : b.name;
                    return naturalCompare(nameA.toLowerCase(), nameB.toLowerCase());
                  })
                  .slice(0, 5)
                  .map((ch, index) => {
                    const startNum = parseInt(sortRenumberStartingNumber, 10) || 1;
                    const newNumber = startNum + index;
                    return (
                      <li key={ch.id}>
                        <span className="preview-old-number">{ch.channel_number ?? '-'}</span>
                        <span className="preview-arrow">→</span>
                        <span className="preview-new-number">{newNumber}</span>
                        <span className="preview-name">{ch.name}</span>
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

      {/* Channel Profiles Modal */}
      <ChannelProfilesListModal
        isOpen={showProfilesModal}
        onClose={() => setShowProfilesModal(false)}
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
          <input
            type="text"
            placeholder="Search channels..."
            value={searchTerm}
            onChange={(e) => onSearchChange(e.target.value)}
            className="search-input"
          />
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
                  >
                    ✕
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
            {/* Render filtered groups with channels */}
            {filteredChannelGroups.map((group) =>
              renderGroup(group.id, group.name, channelsByGroup[group.id] || [])
            )}
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
            {/* Always render Uncategorized if it has channels */}
            {channelsByGroup.ungrouped?.length > 0 &&
              renderGroup('ungrouped', 'Uncategorized', channelsByGroup.ungrouped)}

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
      </div>
    </div>
  );
}
