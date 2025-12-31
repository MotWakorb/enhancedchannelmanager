import { useState, useEffect, useRef } from 'react';
import {
  DndContext,
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
import type { Channel, ChannelGroup, Stream, M3UAccount, M3UGroupSetting, Logo, ChangeInfo, ChangeRecord, SavePoint, EPGData, EPGSource, StreamProfile, ChannelListFilterSettings } from '../types';
import * as api from '../services/api';
import { HistoryToolbar } from './HistoryToolbar';
import './ChannelsPane.css';

// Helper to format duration for display
function formatDuration(ms: number): string {
  const seconds = Math.floor(ms / 1000);
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);

  if (hours > 0) {
    return `${hours}h ${minutes % 60}m`;
  }
  if (minutes > 0) {
    return `${minutes}m ${seconds % 60}s`;
  }
  return `${seconds}s`;
}

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
  onCreateChannel: (name: string, channelNumber?: number, groupId?: number) => Promise<Channel>;
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
  // Edit mode toggle props
  onEnterEditMode?: () => void;
  onExitEditMode?: () => void;
  onCancelEditMode?: () => void;
  isCommitting?: boolean;
  stagedOperationCount?: number;
  editModeDuration?: number | null;
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
  // EPG and Stream Profile props
  epgData?: EPGData[];
  epgSources?: EPGSource[];
  streamProfiles?: StreamProfile[];
  epgDataLoading?: boolean;
  // Channel list filter props
  providerGroupSettings?: Record<number, M3UGroupSetting>;
  channelListFilters?: ChannelListFilterSettings;
  onChannelListFiltersChange?: (updates: Partial<ChannelListFilterSettings>) => void;
  newlyCreatedGroupIds?: Set<number>;
  onTrackNewlyCreatedGroup?: (groupId: number) => void;
}

interface GroupState {
  [groupId: number]: boolean;
}

interface SortableChannelProps {
  channel: Channel;
  isSelected: boolean;
  isExpanded: boolean;
  isDragOver: boolean;
  isEditingNumber: boolean;
  isEditingName: boolean;
  isModified: boolean;
  isEditMode: boolean;
  editingNumber: string;
  editingName: string;
  logoUrl: string | null;
  onEditingNumberChange: (value: string) => void;
  onEditingNameChange: (value: string) => void;
  onStartEditNumber: (e: React.MouseEvent) => void;
  onStartEditName: (e: React.MouseEvent) => void;
  onSaveNumber: () => void;
  onSaveName: () => void;
  onCancelEditNumber: () => void;
  onCancelEditName: () => void;
  onClick: () => void;
  onStreamDragOver: (e: React.DragEvent) => void;
  onStreamDragLeave: () => void;
  onStreamDrop: (e: React.DragEvent) => void;
  onDelete: () => void;
  onEditChannel: () => void;
}

interface SortableStreamItemProps {
  stream: Stream;
  providerName: string | null;
  isEditMode: boolean;
  onRemove: (streamId: number) => void;
}

function SortableStreamItem({ stream, providerName, isEditMode, onRemove }: SortableStreamItemProps) {
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
        {providerName && <span className="inline-stream-provider">{providerName}</span>}
      </div>
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
  isExpanded,
  isDragOver,
  isEditingNumber,
  isEditingName,
  isModified,
  isEditMode,
  editingNumber,
  editingName,
  logoUrl,
  onEditingNumberChange,
  onEditingNameChange,
  onStartEditNumber,
  onStartEditName,
  onSaveNumber,
  onSaveName,
  onCancelEditNumber,
  onCancelEditName,
  onClick,
  onStreamDragOver,
  onStreamDragLeave,
  onStreamDrop,
  onDelete,
  onEditChannel,
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
      className={`channel-item ${isSelected ? 'selected' : ''} ${isDragOver ? 'drag-over' : ''} ${isDragging ? 'dragging' : ''} ${isModified ? 'channel-modified' : ''}`}
      onClick={onClick}
      onDragOver={onStreamDragOver}
      onDragLeave={onStreamDragLeave}
      onDrop={onStreamDrop}
    >
      <span
        className={`channel-drag-handle ${!isEditMode ? 'disabled' : ''}`}
        {...attributes}
        {...listeners}
        title={isEditMode ? 'Drag to reorder' : 'Enter Edit Mode to reorder channels'}
      >
        ⋮⋮
      </span>
      <span className="channel-expand-icon">{isExpanded ? '▼' : '▶'}</span>
      <div
        className={`channel-logo-container ${isEditMode ? 'editable' : ''}`}
        onClick={(e) => {
          e.stopPropagation();
          if (isEditMode) {
            onEditChannel();
          }
        }}
        title={isEditMode ? 'Click to edit channel logo' : 'Enter Edit Mode to change logo'}
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
      <span className="channel-streams-count">
        {channel.streams.length} stream{channel.streams.length !== 1 ? 's' : ''}
      </span>
      {isEditMode && (
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
  onToggle: () => void;
}

function DroppableGroupHeader({
  groupId,
  groupName,
  channelCount,
  isEmpty,
  isExpanded,
  isEditMode,
  isAutoSync,
  onToggle,
}: DroppableGroupHeaderProps) {
  const droppableId = `group-${groupId}`;
  const { isOver, setNodeRef } = useDroppable({
    id: droppableId,
    disabled: !isEditMode,
  });

  return (
    <div
      ref={setNodeRef}
      className={`group-header ${isOver && isEditMode ? 'drop-target' : ''}`}
      onClick={onToggle}
    >
      <span className="group-toggle">{isExpanded ? '▼' : '▶'}</span>
      <span className="group-name">{groupName}</span>
      {isAutoSync && (
        <span className="group-auto-sync-badge" title="Auto-populated by channel sync">
          <span className="material-icons">sync</span>
        </span>
      )}
      <span className="group-count">{channelCount}</span>
      {isEmpty && <span className="group-empty-badge">Empty</span>}
    </div>
  );
}

// Edit Channel Modal Component
interface ChannelMetadataChanges {
  channel_number?: number;
  name?: string;
  logo_id?: number | null;
  tvg_id?: string | null;
  tvc_guide_stationid?: string | null;
  epg_data_id?: number | null;
  stream_profile_id?: number | null;
}

interface EditChannelModalProps {
  channel: Channel;
  logos: Logo[];
  epgData: { id: number; tvg_id: string; name: string; icon_url: string | null; epg_source: number }[];
  epgSources: { id: number; name: string }[];
  streamProfiles: { id: number; name: string; is_active: boolean }[];
  onClose: () => void;
  onSave: (changes: ChannelMetadataChanges) => Promise<void>;
  onLogoCreate: (url: string) => Promise<Logo>;
  onLogoUpload: (file: File) => Promise<Logo>;
  epgDataLoading?: boolean;
}

function EditChannelModal({
  channel,
  logos,
  epgData,
  epgSources,
  streamProfiles,
  onClose,
  onSave,
  onLogoCreate,
  onLogoUpload,
  epgDataLoading,
}: EditChannelModalProps) {
  // Create a map for quick EPG source name lookup
  const epgSourceMap = new Map(epgSources.map((s) => [s.id, s.name]));

  // Channel basic info state
  const [channelNumber, setChannelNumber] = useState<string>(String(channel.channel_number));
  const [channelName, setChannelName] = useState<string>(channel.name);

  // Logo state
  const [selectedLogoId, setSelectedLogoId] = useState<number | null>(channel.logo_id);
  const [logoSearch, setLogoSearch] = useState('');
  const [newLogoUrl, setNewLogoUrl] = useState('');
  const [addingLogo, setAddingLogo] = useState(false);
  const [uploadingLogo, setUploadingLogo] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Metadata state
  const [tvgId, setTvgId] = useState<string>(channel.tvg_id || '');
  const [tvcGuideStationId, setTvcGuideStationId] = useState<string>(channel.tvc_guide_stationid || '');
  const [selectedEpgDataId, setSelectedEpgDataId] = useState<number | null>(channel.epg_data_id);
  const [selectedStreamProfileId, setSelectedStreamProfileId] = useState<number | null>(channel.stream_profile_id);

  // EPG search state
  const [epgSearch, setEpgSearch] = useState('');
  const [epgDropdownOpen, setEpgDropdownOpen] = useState(false);

  // TVG-ID from EPG picker state
  const [tvgIdPickerOpen, setTvgIdPickerOpen] = useState(false);
  const [tvgIdSearch, setTvgIdSearch] = useState('');

  const [saving, setSaving] = useState(false);
  const [showDiscardConfirm, setShowDiscardConfirm] = useState(false);

  // Filter logos by search term
  const filteredLogos = logos.filter((logo) =>
    logo.name.toLowerCase().includes(logoSearch.toLowerCase())
  );

  // Get currently selected logo
  const currentLogo = selectedLogoId ? logos.find((l) => l.id === selectedLogoId) : null;

  // Get currently selected EPG data
  const currentEpgData = selectedEpgDataId ? epgData.find((e) => e.id === selectedEpgDataId) : null;

  // Check if any changes were made
  const parsedChannelNumber = parseFloat(channelNumber);
  const hasChanges =
    (!isNaN(parsedChannelNumber) && parsedChannelNumber !== channel.channel_number) ||
    channelName !== channel.name ||
    selectedLogoId !== channel.logo_id ||
    tvgId !== (channel.tvg_id || '') ||
    tvcGuideStationId !== (channel.tvc_guide_stationid || '') ||
    selectedEpgDataId !== channel.epg_data_id ||
    selectedStreamProfileId !== channel.stream_profile_id;

  // Handle close with unsaved changes check
  const handleClose = () => {
    if (hasChanges) {
      setShowDiscardConfirm(true);
    } else {
      onClose();
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const changes: ChannelMetadataChanges = {};

      if (!isNaN(parsedChannelNumber) && parsedChannelNumber !== channel.channel_number) {
        changes.channel_number = parsedChannelNumber;
      }
      if (channelName !== channel.name) {
        changes.name = channelName;
      }
      if (selectedLogoId !== channel.logo_id) {
        changes.logo_id = selectedLogoId;
      }
      if (tvgId !== (channel.tvg_id || '')) {
        changes.tvg_id = tvgId || null;
      }
      if (tvcGuideStationId !== (channel.tvc_guide_stationid || '')) {
        changes.tvc_guide_stationid = tvcGuideStationId || null;
      }
      if (selectedEpgDataId !== channel.epg_data_id) {
        changes.epg_data_id = selectedEpgDataId;
      }
      if (selectedStreamProfileId !== channel.stream_profile_id) {
        changes.stream_profile_id = selectedStreamProfileId;
      }

      await onSave(changes);
    } finally {
      setSaving(false);
    }
  };

  const handleAddLogoFromUrl = async () => {
    if (!newLogoUrl.trim()) return;

    setAddingLogo(true);
    try {
      const newLogo = await onLogoCreate(newLogoUrl.trim());
      setSelectedLogoId(newLogo.id);
      setNewLogoUrl('');
    } catch (err) {
      console.error('Failed to add logo:', err);
    } finally {
      setAddingLogo(false);
    }
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    // Validate file type
    if (!file.type.startsWith('image/')) {
      console.error('Invalid file type. Please select an image file.');
      return;
    }

    setUploadingLogo(true);
    try {
      const newLogo = await onLogoUpload(file);
      setSelectedLogoId(newLogo.id);
    } catch (err) {
      console.error('Failed to upload logo:', err);
    } finally {
      setUploadingLogo(false);
      // Reset file input
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  };

  const handleEpgSearch = (value: string) => {
    setEpgSearch(value);
  };

  // Handle TVG-ID picker search
  const handleTvgIdSearch = (value: string) => {
    setTvgIdSearch(value);
  };

  // Filter EPG data client-side based on search terms
  const filteredEpgData = epgData.filter((epg) => {
    const searchTerm = (epgDropdownOpen ? epgSearch : tvgIdSearch).toLowerCase();
    if (!searchTerm) return true;
    return (
      epg.name.toLowerCase().includes(searchTerm) ||
      epg.tvg_id.toLowerCase().includes(searchTerm)
    );
  });

  // Filter for TVG-ID picker specifically
  const filteredTvgIdEpgData = epgData.filter((epg) => {
    const searchTerm = tvgIdSearch.toLowerCase();
    if (!searchTerm) return true;
    return (
      epg.name.toLowerCase().includes(searchTerm) ||
      epg.tvg_id.toLowerCase().includes(searchTerm)
    );
  });

  // Select TVG-ID from EPG data picker
  const handleSelectTvgIdFromEpg = (epg: { tvg_id: string }) => {
    setTvgId(epg.tvg_id);
    setTvgIdPickerOpen(false);
    setTvgIdSearch('');
  };

  return (
    <div className="edit-channel-modal-overlay" onClick={handleClose}>
      <div className="edit-channel-modal" onClick={(e) => e.stopPropagation()}>
        <div className="edit-channel-titlebar">
          <span className="edit-channel-titlebar-text">Edit Channel</span>
          <button className="edit-channel-titlebar-close" onClick={handleClose} title="Close">
            <span className="material-icons">close</span>
          </button>
        </div>
        <div className="edit-channel-content">
        {/* Channel Number and Name Section */}
        <div className="edit-channel-header-fields">
          <div className="edit-channel-number-field">
            <label>Channel #</label>
            <input
              type="text"
              className="edit-channel-number-input"
              value={channelNumber}
              onChange={(e) => setChannelNumber(e.target.value)}
              placeholder="123"
            />
          </div>
          <div className="edit-channel-name-field">
            <label>Channel Name</label>
            <input
              type="text"
              className="edit-channel-name-input"
              value={channelName}
              onChange={(e) => setChannelName(e.target.value)}
              placeholder="Enter channel name..."
            />
          </div>
        </div>

        {/* TVG-ID Section */}
        <div className="edit-channel-section">
          <label>TVG-ID</label>
          <div className="edit-channel-input-row">
            <input
              type="text"
              className="edit-channel-text-input"
              placeholder="Enter TVG-ID for EPG matching..."
              value={tvgId}
              onChange={(e) => setTvgId(e.target.value)}
            />
            <button
              className="edit-channel-get-epg-btn"
              onClick={() => {
                if (currentEpgData?.tvg_id) {
                  // If EPG data is selected, directly copy the TVG-ID
                  setTvgId(currentEpgData.tvg_id);
                } else {
                  // Otherwise, open the picker to search
                  setTvgIdPickerOpen(!tvgIdPickerOpen);
                }
              }}
              title={currentEpgData ? "Copy TVG-ID from selected EPG data" : "Search EPG data for TVG-ID"}
            >
              <span className="material-icons">{currentEpgData ? 'content_copy' : 'search'}</span>
              {currentEpgData ? 'Copy from EPG' : 'Get from EPG'}
            </button>
          </div>
          {tvgIdPickerOpen && (
            <div className="tvg-id-picker">
              <input
                type="text"
                className="edit-channel-text-input"
                placeholder="Search EPG data..."
                value={tvgIdSearch}
                onChange={(e) => handleTvgIdSearch(e.target.value)}
                autoFocus
              />
              <div className="tvg-id-picker-dropdown">
                {epgDataLoading ? (
                  <div className="epg-dropdown-loading">Loading...</div>
                ) : filteredTvgIdEpgData.length === 0 ? (
                  <div className="epg-dropdown-empty">
                    {tvgIdSearch ? 'No EPG data found' : 'Type to search EPG data'}
                  </div>
                ) : (
                  filteredTvgIdEpgData.slice(0, 100).map((epg) => (
                    <div
                      key={epg.id}
                      className="epg-dropdown-item"
                      onClick={() => handleSelectTvgIdFromEpg(epg)}
                    >
                      {epg.icon_url && (
                        <img
                          src={epg.icon_url}
                          alt=""
                          className="epg-dropdown-icon"
                          onError={(e) => {
                            (e.target as HTMLImageElement).style.display = 'none';
                          }}
                        />
                      )}
                      <div className="epg-dropdown-info">
                        <span className="epg-dropdown-name">{epg.name}</span>
                        <span className="epg-dropdown-tvgid">{epg.tvg_id}</span>
                        {epgSourceMap.get(epg.epg_source) && (
                          <span className="epg-dropdown-source">{epgSourceMap.get(epg.epg_source)}</span>
                        )}
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          )}
          <span className="edit-channel-hint">Used for matching EPG program data</span>
        </div>

        {/* Gracenote Station ID Section */}
        <div className="edit-channel-section">
          <label>Gracenote Station ID</label>
          <input
            type="text"
            className="edit-channel-text-input"
            placeholder="Enter Gracenote/TVC station ID..."
            value={tvcGuideStationId}
            onChange={(e) => setTvcGuideStationId(e.target.value)}
          />
          <span className="edit-channel-hint">Numeric ID for Gracenote/TVC guide data</span>
        </div>

        {/* EPG Data Section */}
        <div className="edit-channel-section">
          <label>EPG Data</label>
          {currentEpgData && (
            <div className="current-epg-preview">
              {currentEpgData.icon_url && (
                <img
                  src={currentEpgData.icon_url}
                  alt=""
                  onError={(e) => {
                    (e.target as HTMLImageElement).style.display = 'none';
                  }}
                />
              )}
              <div className="current-epg-info">
                <span className="current-epg-name">{currentEpgData.name}</span>
                <span className="current-epg-tvgid">{currentEpgData.tvg_id}</span>
              </div>
              <button
                className="current-epg-remove-btn"
                onClick={() => setSelectedEpgDataId(null)}
              >
                Remove
              </button>
            </div>
          )}
          <div className="epg-search-container">
            <input
              type="text"
              className="edit-channel-text-input"
              placeholder="Search EPG data..."
              value={epgSearch}
              onChange={(e) => handleEpgSearch(e.target.value)}
              onFocus={() => setEpgDropdownOpen(true)}
            />
            {epgDropdownOpen && (
              <div className="epg-dropdown">
                {epgDataLoading ? (
                  <div className="epg-dropdown-loading">Loading...</div>
                ) : filteredEpgData.length === 0 ? (
                  <div className="epg-dropdown-empty">
                    {epgSearch ? 'No EPG data found' : 'Type to search or scroll to browse'}
                  </div>
                ) : (
                  filteredEpgData.slice(0, 100).map((epg) => (
                    <div
                      key={epg.id}
                      className={`epg-dropdown-item ${selectedEpgDataId === epg.id ? 'selected' : ''}`}
                      onClick={() => {
                        setSelectedEpgDataId(epg.id);
                        setEpgDropdownOpen(false);
                        setEpgSearch('');
                      }}
                    >
                      {epg.icon_url && (
                        <img
                          src={epg.icon_url}
                          alt=""
                          className="epg-dropdown-icon"
                          onError={(e) => {
                            (e.target as HTMLImageElement).style.display = 'none';
                          }}
                        />
                      )}
                      <div className="epg-dropdown-info">
                        <span className="epg-dropdown-name">{epg.name}</span>
                        <span className="epg-dropdown-tvgid">{epg.tvg_id}</span>
                        {epgSourceMap.get(epg.epg_source) && (
                          <span className="epg-dropdown-source">{epgSourceMap.get(epg.epg_source)}</span>
                        )}
                      </div>
                    </div>
                  ))
                )}
              </div>
            )}
          </div>
        </div>

        {/* Stream Profile Section */}
        <div className="edit-channel-section">
          <label>Stream Profile</label>
          <select
            className="edit-channel-select"
            value={selectedStreamProfileId ?? ''}
            onChange={(e) => setSelectedStreamProfileId(e.target.value ? Number(e.target.value) : null)}
          >
            <option value="">Default (no profile)</option>
            {streamProfiles
              .filter((p) => p.is_active)
              .map((profile) => (
                <option key={profile.id} value={profile.id}>
                  {profile.name}
                </option>
              ))}
          </select>
          <span className="edit-channel-hint">Determines how streams are processed/transcoded</span>
        </div>

        {/* Logo Section */}
        <div className="edit-channel-section">
          <label>Channel Logo</label>

          {/* Current logo preview */}
          {currentLogo && (
            <div className="current-logo-preview">
              <img
                src={currentLogo.cache_url || currentLogo.url}
                alt={currentLogo.name}
                onError={(e) => {
                  (e.target as HTMLImageElement).src = currentLogo.url;
                }}
              />
              <span>{currentLogo.name}</span>
              <button
                className="current-logo-remove-btn"
                onClick={() => setSelectedLogoId(null)}
              >
                Remove
              </button>
            </div>
          )}

          {/* Logo search */}
          <input
            type="text"
            className="logo-search-input"
            placeholder="Search logos..."
            value={logoSearch}
            onChange={(e) => setLogoSearch(e.target.value)}
          />

          {/* Logo grid */}
          <div className="logo-selection-grid">
            <div
              className={`logo-option ${selectedLogoId === null ? 'selected' : ''}`}
              onClick={() => setSelectedLogoId(null)}
            >
              <div className="logo-option-none">No Logo</div>
            </div>
            {filteredLogos.map((logo) => (
              <div
                key={logo.id}
                className={`logo-option ${selectedLogoId === logo.id ? 'selected' : ''}`}
                onClick={() => setSelectedLogoId(logo.id)}
                title={logo.name}
              >
                <img
                  src={logo.cache_url || logo.url}
                  alt={logo.name}
                  onError={(e) => {
                    (e.target as HTMLImageElement).src = logo.url;
                  }}
                />
              </div>
            ))}
          </div>

          {/* Add logo from URL or file */}
          <div className="logo-add-row">
            <input
              type="text"
              placeholder="Add logo from URL..."
              value={newLogoUrl}
              onChange={(e) => setNewLogoUrl(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  handleAddLogoFromUrl();
                }
              }}
            />
            <button
              onClick={handleAddLogoFromUrl}
              disabled={!newLogoUrl.trim() || addingLogo}
              className="logo-add-btn"
            >
              {addingLogo ? 'Adding...' : 'Add'}
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              onChange={handleFileUpload}
              style={{ display: 'none' }}
              id="logo-file-input"
            />
            <button
              onClick={() => fileInputRef.current?.click()}
              disabled={uploadingLogo}
              className="logo-upload-btn"
            >
              <span className="material-icons">upload_file</span>
              {uploadingLogo ? 'Uploading...' : 'Upload'}
            </button>
          </div>
        </div>

        <div className="edit-channel-actions">
          <button
            className="edit-channel-save-btn"
            onClick={handleSave}
            disabled={saving || !hasChanges}
          >
            {saving ? 'Saving...' : 'Save Changes'}
          </button>
        </div>
        </div>

        {/* Discard Changes Confirmation Dialog */}
        {showDiscardConfirm && (
          <div className="discard-confirm-overlay" onClick={() => setShowDiscardConfirm(false)}>
            <div className="discard-confirm-dialog" onClick={(e) => e.stopPropagation()}>
              <div className="discard-confirm-title">Unsaved Changes</div>
              <div className="discard-confirm-message">
                You have unsaved changes. Are you sure you want to close without saving?
              </div>
              <div className="discard-confirm-actions">
                <button
                  className="discard-confirm-cancel"
                  onClick={() => setShowDiscardConfirm(false)}
                >
                  Keep Editing
                </button>
                <button
                  className="discard-confirm-discard"
                  onClick={onClose}
                >
                  Discard Changes
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
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
  onStageAddStream: _onStageAddStream, // Handled in App.tsx for stream drops
  onStageRemoveStream,
  onStageReorderStreams,
  onStageBulkAssignNumbers: _onStageBulkAssignNumbers, // Handled in App.tsx for channel reorder
  // Edit mode toggle props
  onEnterEditMode,
  onExitEditMode,
  onCancelEditMode,
  isCommitting = false,
  stagedOperationCount = 0,
  editModeDuration = null,
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
  // EPG and Stream Profile props
  epgData = [],
  epgSources = [],
  streamProfiles = [],
  epgDataLoading = false,
  // Channel list filter props
  providerGroupSettings = {},
  channelListFilters,
  onChannelListFiltersChange,
  newlyCreatedGroupIds = new Set(),
  onTrackNewlyCreatedGroup,
}: ChannelsPaneProps) {
  // Suppress unused variable warnings - these are passed through but handled in parent
  void _onStageAddStream;
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
  const [newChannelName, setNewChannelName] = useState('');
  const [newChannelNumber, setNewChannelNumber] = useState('');
  const [newChannelGroup, setNewChannelGroup] = useState<number | ''>('');
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

  // Cancel edit mode confirmation state
  const [showCancelEditModeConfirm, setShowCancelEditModeConfirm] = useState(false);

  // Create channel group modal state
  const [showCreateGroupModal, setShowCreateGroupModal] = useState(false);
  const [newGroupName, setNewGroupName] = useState('');
  const [creatingGroup, setCreatingGroup] = useState(false);

  // Cross-group move modal state
  const [showCrossGroupMoveModal, setShowCrossGroupMoveModal] = useState(false);
  const [crossGroupMoveData, setCrossGroupMoveData] = useState<{
    channel: Channel;
    targetGroupId: number | null;
    targetGroupName: string;
    sourceGroupName: string;
    isTargetAutoSync: boolean;
    suggestedChannelNumber: number | null;
    minChannelInGroup: number | null;
    maxChannelInGroup: number | null;
  } | null>(null);

  // Stream reorder sensors (separate from channel reorder)
  const streamSensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
  );


  // Sync local channels with props
  // In edit mode, we only sync when entering/exiting edit mode, not on every channels change
  // This prevents the parent's state update (from channel creation) from overwriting our local changes
  useEffect(() => {
    if (!isEditMode) {
      // Not in edit mode - always sync with props
      setLocalChannels(channels);
    }
  }, [channels, isEditMode]);

  // Sync when entering edit mode (to pick up latest channels)
  useEffect(() => {
    if (isEditMode) {
      setLocalChannels(channels);
    }
    // Only run when isEditMode changes, not when channels change
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isEditMode]);

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

  // Handle channel click - toggle selection
  const handleChannelClick = (channel: Channel) => {
    if (selectedChannelId === channel.id) {
      onChannelSelect(null); // Collapse if already selected
    } else {
      onChannelSelect(channel);
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

      await onDeleteChannel(channelToDelete.id);
      // Remove from local state
      setLocalChannels((prev) => prev.filter((ch) => ch.id !== channelToDelete.id));
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
      const newChannel = await onCreateChannel(
        newChannelName.trim(),
        channelNum,
        newChannelGroup !== '' ? newChannelGroup : undefined
      );
      // In edit mode, we need to manually add the new channel to localChannels
      // since we disabled the automatic sync from parent state
      if (isEditMode && newChannel) {
        setLocalChannels((prev) => [...prev, newChannel]);
      }
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
      const groupId = newChannelGroup !== '' ? newChannelGroup : null;
      const sourceChannels = isEditMode ? localChannels : channels;
      const channelsToShift = sourceChannels
        .filter((ch) => {
          const sameGroup = groupId === null
            ? ch.channel_group_id === null
            : ch.channel_group_id === groupId;
          return sameGroup && ch.channel_number !== null && ch.channel_number >= conflictingChannelNumber;
        })
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

    // Look for a number at the beginning of the channel name
    // Pattern: "123 | Channel Name" or "123-Channel Name" or "123.Channel Name" or "123 Channel Name"
    // This matches a number at the start followed by a separator (space, |, -, .)
    const prefixMatch = channelName.match(/^(\d+(?:\.\d+)?)\s*([|\-.\s])\s*(.*)$/);

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

  // All groups sorted alphabetically (for filter dropdown - includes empty groups)
  const allGroupsSorted = [...channelGroups].sort((a, b) => a.name.localeCompare(b.name));

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

  const handleDragStart = (_event: DragStartEvent) => {
    // Could add visual feedback here
  };

  const handleDragOver = (_event: DragOverEvent) => {
    // Could add drop zone preview here
  };

  const handleDragEnd = (event: DragEndEvent) => {
    // Block channel reordering when not in edit mode
    if (!isEditMode) return;

    const { active, over } = event;

    if (!over || active.id === over.id) return;

    const activeChannel = localChannels.find((c) => c.id === active.id);
    if (!activeChannel) return;

    // Check if dropped on a group header (cross-group move)
    const overId = String(over.id);
    if (overId.startsWith('group-')) {
      const targetGroupId = overId.replace('group-', '');
      const newGroupId = targetGroupId === 'ungrouped' ? null : parseInt(targetGroupId, 10);

      // Don't do anything if dropping on the same group
      if ((newGroupId === null && activeChannel.channel_group_id === null) ||
          (newGroupId !== null && activeChannel.channel_group_id === newGroupId)) {
        return;
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
          // Suggest the next number after the max
          suggestedChannelNumber = maxChannelInGroup + 1;
        }
      }

      // Show the modal instead of immediately moving
      setCrossGroupMoveData({
        channel: activeChannel,
        targetGroupId: newGroupId,
        targetGroupName,
        sourceGroupName,
        isTargetAutoSync,
        suggestedChannelNumber,
        minChannelInGroup,
        maxChannelInGroup,
      });
      setShowCrossGroupMoveModal(true);

      return;
    }

    // Otherwise, this is a within-group reorder
    const overChannel = localChannels.find((c) => c.id === over.id);
    if (!overChannel) return;

    // Get the group for the channels
    const groupId = activeChannel.channel_group_id ?? 'ungrouped';
    const groupChannels = channelsByGroup[groupId] || [];

    const oldIndex = groupChannels.findIndex((c) => c.id === active.id);
    const newIndex = groupChannels.findIndex((c) => c.id === over.id);

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
      const overNum = overChannel.channel_number!;

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

  // Handle cross-group move confirmation
  const handleCrossGroupMoveConfirm = (keepChannelNumber: boolean, newChannelNumber?: number) => {
    if (!crossGroupMoveData) return;

    const { channel, targetGroupId, targetGroupName, sourceGroupName } = crossGroupMoveData;

    // Determine the final channel number
    let finalChannelNumber = channel.channel_number;
    if (!keepChannelNumber && newChannelNumber !== undefined) {
      finalChannelNumber = newChannelNumber;
    }

    // Check if auto-rename applies when changing channel number
    let finalName = channel.name;
    if (!keepChannelNumber && newChannelNumber !== undefined && newChannelNumber !== channel.channel_number) {
      const newName = computeAutoRename(channel.name, channel.channel_number, newChannelNumber);
      if (newName) {
        finalName = newName;
      }
    }

    // Update local state immediately
    const updatedChannels = localChannels.map((ch) => {
      if (ch.id === channel.id) {
        return {
          ...ch,
          channel_group_id: targetGroupId,
          channel_number: finalChannelNumber,
          name: finalName,
        };
      }
      return ch;
    });
    setLocalChannels(updatedChannels);

    // Stage the changes
    if (onStageUpdateChannel) {
      const updates: Partial<Channel> = { channel_group_id: targetGroupId };
      let description = `Moved "${channel.name}" from "${sourceGroupName}" to "${targetGroupName}"`;

      if (!keepChannelNumber && newChannelNumber !== undefined && newChannelNumber !== channel.channel_number) {
        updates.channel_number = newChannelNumber;
        description += ` (channel ${channel.channel_number ?? '-'} → ${newChannelNumber})`;

        // Include name update if auto-rename applied
        if (finalName !== channel.name) {
          updates.name = finalName;
          description += `, renamed to "${finalName}"`;
        }
      }

      onStageUpdateChannel(channel.id, updates, description);
    }

    // Close modal
    setShowCrossGroupMoveModal(false);
    setCrossGroupMoveData(null);
  };

  const handleCrossGroupMoveCancel = () => {
    setShowCrossGroupMoveModal(false);
    setCrossGroupMoveData(null);
  };

  const renderGroup = (groupId: number | 'ungrouped', groupName: string, groupChannels: Channel[], isEmpty: boolean = false) => {
    // Only show empty groups if explicitly marked (selected in filter or newly created)
    if (groupChannels.length === 0 && !isEmpty) return null;
    // Only show groups that are in selectedGroups (or ungrouped which is always shown if it has channels)
    if (groupId !== 'ungrouped' && !selectedGroups.includes(groupId as number)) return null;

    const numericGroupId = groupId === 'ungrouped' ? -1 : groupId;
    const isExpanded = expandedGroups[numericGroupId] === true;
    const isAutoSync = groupId !== 'ungrouped' && autoSyncRelatedGroups.has(groupId);

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
          onToggle={() => toggleGroup(numericGroupId)}
        />
        {isExpanded && isEmpty && (
          <div className="group-channels empty-group-placeholder">
            <div className="empty-group-message">
              No channels in this group. Drag a channel here or create a new one.
            </div>
          </div>
        )}
        {isExpanded && !isEmpty && (
            <SortableContext
              items={groupChannels.map((c) => c.id)}
              strategy={verticalListSortingStrategy}
            >
              <div className="group-channels">
                {groupChannels.map((channel) => (
                  <div key={channel.id} className="channel-wrapper">
                    <SortableChannel
                      channel={channel}
                      isSelected={selectedChannelId === channel.id}
                      isExpanded={selectedChannelId === channel.id}
                      isDragOver={dragOverChannelId === channel.id}
                      isEditingNumber={editingChannelId === channel.id}
                      isEditingName={editingNameChannelId === channel.id}
                      isModified={modifiedChannelIds?.has(channel.id) ?? false}
                      isEditMode={isEditMode}
                      editingNumber={editingChannelNumber}
                      editingName={editingChannelName}
                      logoUrl={getChannelLogoUrl(channel)}
                      onEditingNumberChange={setEditingChannelNumber}
                      onEditingNameChange={setEditingChannelName}
                      onStartEditNumber={(e) => handleStartEditNumber(e, channel)}
                      onStartEditName={(e) => handleStartEditName(e, channel)}
                      onSaveNumber={() => handleSaveChannelNumber(channel.id)}
                      onSaveName={() => handleSaveChannelName(channel.id)}
                      onCancelEditNumber={handleCancelEditNumber}
                      onCancelEditName={handleCancelEditName}
                      onClick={() => handleChannelClick(channel)}
                      onStreamDragOver={(e) => handleStreamDragOver(e, channel.id)}
                      onStreamDragLeave={handleStreamDragLeave}
                      onStreamDrop={(e) => handleStreamDrop(e, channel.id)}
                      onDelete={() => handleDeleteChannelClick(channel)}
                      onEditChannel={() => handleEditChannel(channel)}
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
                                    />
                                  </div>
                                ))}
                              </div>
                            </SortableContext>
                          </DndContext>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </SortableContext>
        )}
      </div>
    );
  };

  // Handle cancel edit mode click
  const handleCancelEditModeClick = () => {
    if (stagedOperationCount > 0) {
      setShowCancelEditModeConfirm(true);
    } else {
      onCancelEditMode?.();
    }
  };

  const handleConfirmCancelEditMode = () => {
    setShowCancelEditModeConfirm(false);
    onCancelEditMode?.();
  };

  return (
    <div className="channels-pane">
      <div className={`pane-header ${isEditMode ? 'edit-mode' : ''}`}>
        <div className="pane-header-title">
          <h2>{isEditMode ? 'Editing Channels' : 'Channels'}</h2>
          {isEditMode && (
            <span className="edit-mode-info">
              {stagedOperationCount > 0 && (
                <span className="edit-mode-count">
                  {stagedOperationCount} change{stagedOperationCount !== 1 ? 's' : ''}
                </span>
              )}
              {editModeDuration !== null && (
                <span className="edit-mode-duration">
                  ({formatDuration(editModeDuration)})
                </span>
              )}
            </span>
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
                onClick={() => setShowCreateModal(true)}
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
          {/* Edit Mode: Done and Cancel buttons */}
          {isEditMode && onExitEditMode && onCancelEditMode && (
            <div className="edit-mode-buttons">
              <button
                className="edit-mode-done-btn"
                onClick={onExitEditMode}
                disabled={isCommitting}
                title="Apply changes"
              >
                <span className="material-icons" style={{ fontSize: '16px', marginRight: '4px' }}>check</span>
                Done
                {stagedOperationCount > 0 && (
                  <span className="edit-mode-done-count">{stagedOperationCount}</span>
                )}
              </button>
              <button
                className="edit-mode-cancel-btn"
                onClick={handleCancelEditModeClick}
                disabled={isCommitting}
                title="Cancel and discard changes"
              >
                <span className="material-icons" style={{ fontSize: '16px', marginRight: '4px' }}>close</span>
                Cancel
              </button>
            </div>
          )}
          {/* Not in Edit Mode: Enter Edit Mode button */}
          {!isEditMode && onEnterEditMode && (
            <button
              className="enter-edit-mode-btn"
              onClick={onEnterEditMode}
              title="Enter Edit Mode"
            >
              <span className="material-icons" style={{ fontSize: '16px', marginRight: '4px' }}>edit</span>
              Edit Mode
            </button>
          )}
        </div>
      </div>

      {/* Cancel Edit Mode Confirmation Dialog */}
      {showCancelEditModeConfirm && (
        <div className="edit-mode-dialog-overlay" onClick={() => setShowCancelEditModeConfirm(false)}>
          <div className="edit-mode-dialog" onClick={(e) => e.stopPropagation()}>
            <div className="edit-mode-dialog-header">
              <h2>Discard Changes?</h2>
            </div>
            <div className="edit-mode-dialog-content">
              <p className="edit-mode-dialog-summary-intro">
                You have <strong>{stagedOperationCount}</strong> pending change{stagedOperationCount !== 1 ? 's' : ''} that will be lost.
              </p>
              <p className="edit-mode-dialog-question">
                Are you sure you want to cancel Edit Mode and discard all changes?
              </p>
            </div>
            <div className="edit-mode-dialog-actions">
              <button
                className="edit-mode-dialog-btn secondary"
                onClick={() => setShowCancelEditModeConfirm(false)}
              >
                Keep Editing
              </button>
              <button
                className="edit-mode-dialog-btn danger"
                onClick={handleConfirmCancelEditMode}
              >
                Discard Changes
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Create Channel Modal */}
      {showCreateModal && (
        <div className="modal-overlay" onClick={handleCloseCreateModal}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <h3>Create New Channel</h3>
            <div className="modal-form">
              <label>
                Channel Name *
                <input
                  type="text"
                  value={newChannelName}
                  onChange={(e) => setNewChannelName(e.target.value)}
                  placeholder="e.g., ESPN HD"
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
        <div className="modal-overlay" onClick={handleCloseCreateGroupModal}>
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
        <div className="modal-overlay" onClick={() => setShowConflictDialog(false)}>
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
        <div className="modal-overlay" onClick={handleCancelDelete}>
          <div className="modal-content delete-dialog" onClick={(e) => e.stopPropagation()}>
            <h3>Delete Channel</h3>
            <div className="delete-message">
              <p>
                Are you sure you want to delete channel{' '}
                <strong>{channelToDelete.channel_number} - {channelToDelete.name}</strong>?
              </p>
              <p className="delete-warning">
                This action cannot be undone. The channel and all its stream assignments will be permanently removed.
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
            try {
              const name = url.split('/').pop()?.split('?')[0] || 'Logo';
              const newLogo = await api.createLogo({ name, url });
              if (onLogosChange) {
                onLogosChange();
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
        <div className="modal-overlay" onClick={handleCrossGroupMoveCancel}>
          <div className="modal-content cross-group-move-dialog" onClick={(e) => e.stopPropagation()}>
            <h3>Move Channel to Group</h3>

            <div className="cross-group-move-info">
              <p>
                Moving <strong>{crossGroupMoveData.channel.name}</strong> from{' '}
                <span className="group-tag">{crossGroupMoveData.sourceGroupName}</span> to{' '}
                <span className="group-tag">{crossGroupMoveData.targetGroupName}</span>
              </p>
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
                <label>Channel Number</label>
                {crossGroupMoveData.minChannelInGroup !== null && crossGroupMoveData.maxChannelInGroup !== null && (
                  <p className="group-range-info">
                    Target group range: {crossGroupMoveData.minChannelInGroup} – {crossGroupMoveData.maxChannelInGroup}
                  </p>
                )}
              </div>

              <div className="move-option-buttons">
                <button
                  className="move-option-btn"
                  onClick={() => handleCrossGroupMoveConfirm(true)}
                >
                  <span className="material-icons">numbers</span>
                  <div className="move-option-text">
                    <strong>Keep current number</strong>
                    <span>Stay at channel {crossGroupMoveData.channel.channel_number ?? '(none)'}</span>
                  </div>
                </button>

                {crossGroupMoveData.suggestedChannelNumber !== null && (
                  <button
                    className="move-option-btn suggested"
                    onClick={() => handleCrossGroupMoveConfirm(false, crossGroupMoveData.suggestedChannelNumber!)}
                  >
                    <span className="material-icons">add_circle</span>
                    <div className="move-option-text">
                      <strong>Assign next available</strong>
                      <span>Use channel {crossGroupMoveData.suggestedChannelNumber}</span>
                    </div>
                  </button>
                )}
              </div>
            </div>

            <div className="modal-actions">
              <button
                className="modal-btn cancel"
                onClick={handleCrossGroupMoveCancel}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="pane-filters">
        <input
          type="text"
          placeholder="Search channels..."
          value={searchTerm}
          onChange={(e) => onSearchChange(e.target.value)}
          className="search-input"
        />
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
                    return a.name.localeCompare(b.name);
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

      <div className="pane-content">
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
          </DndContext>
        )}
      </div>
    </div>
  );
}
