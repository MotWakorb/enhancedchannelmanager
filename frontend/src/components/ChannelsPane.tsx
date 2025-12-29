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
} from '@dnd-kit/core';
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import type { Channel, ChannelGroup, Stream, M3UAccount } from '../types';
import * as api from '../services/api';
import './ChannelsPane.css';

interface ChannelsPaneProps {
  channelGroups: ChannelGroup[];
  channels: Channel[];
  providers: M3UAccount[];
  selectedChannelId: number | null;
  onChannelSelect: (channel: Channel | null) => void;
  onChannelUpdate: (channel: Channel) => void;
  onChannelDrop: (channelId: number, streamId: number) => void;
  onBulkStreamDrop: (channelId: number, streamIds: number[]) => void;
  onChannelReorder: (channelIds: number[], startingNumber: number) => void;
  onCreateChannel: (name: string, channelNumber?: number, groupId?: number) => Promise<Channel>;
  searchTerm: string;
  onSearchChange: (term: string) => void;
  selectedGroups: number[];
  onSelectedGroupsChange: (groupIds: number[]) => void;
  loading: boolean;
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
  editingNumber: string;
  onEditingNumberChange: (value: string) => void;
  onStartEditNumber: (e: React.MouseEvent) => void;
  onSaveNumber: () => void;
  onCancelEditNumber: () => void;
  onClick: () => void;
  onStreamDragOver: (e: React.DragEvent) => void;
  onStreamDragLeave: () => void;
  onStreamDrop: (e: React.DragEvent) => void;
}

interface SortableStreamItemProps {
  stream: Stream;
  providerName: string | null;
  onRemove: (streamId: number) => void;
}

function SortableStreamItem({ stream, providerName, onRemove }: SortableStreamItemProps) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: stream.id });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  };

  return (
    <div ref={setNodeRef} style={style} className="inline-stream-item">
      <span className="stream-drag-handle" {...attributes} {...listeners}>
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
    </div>
  );
}

function SortableChannel({
  channel,
  isSelected,
  isExpanded,
  isDragOver,
  isEditingNumber,
  editingNumber,
  onEditingNumberChange,
  onStartEditNumber,
  onSaveNumber,
  onCancelEditNumber,
  onClick,
  onStreamDragOver,
  onStreamDragLeave,
  onStreamDrop,
}: SortableChannelProps) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: channel.id });

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

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`channel-item ${isSelected ? 'selected' : ''} ${isDragOver ? 'drag-over' : ''} ${isDragging ? 'dragging' : ''}`}
      onClick={onClick}
      onDragOver={onStreamDragOver}
      onDragLeave={onStreamDragLeave}
      onDrop={onStreamDrop}
    >
      <span className="channel-drag-handle" {...attributes} {...listeners}>
        ⋮⋮
      </span>
      <span className="channel-expand-icon">{isExpanded ? '▼' : '▶'}</span>
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
          className="channel-number editable"
          onClick={onStartEditNumber}
          title="Click to edit"
        >
          {channel.channel_number ?? '-'}
        </span>
      )}
      <span className="channel-name">{channel.name}</span>
      <span className="channel-streams-count">
        {channel.streams.length} stream{channel.streams.length !== 1 ? 's' : ''}
      </span>
    </div>
  );
}

export function ChannelsPane({
  channelGroups,
  channels,
  providers,
  selectedChannelId,
  onChannelSelect,
  onChannelUpdate,
  onChannelDrop,
  onBulkStreamDrop,
  onChannelReorder,
  onCreateChannel,
  searchTerm,
  onSearchChange,
  selectedGroups,
  onSelectedGroupsChange,
  loading,
}: ChannelsPaneProps) {
  const [expandedGroups, setExpandedGroups] = useState<GroupState>({});
  const [dragOverChannelId, setDragOverChannelId] = useState<number | null>(null);
  const [localChannels, setLocalChannels] = useState<Channel[]>(channels);
  const [groupDropdownOpen, setGroupDropdownOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Create channel modal state
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [newChannelName, setNewChannelName] = useState('');
  const [newChannelNumber, setNewChannelNumber] = useState('');
  const [newChannelGroup, setNewChannelGroup] = useState<number | ''>('');
  const [groupSearchText, setGroupSearchText] = useState('');
  const [showGroupDropdown, setShowGroupDropdown] = useState(false);
  const [creating, setCreating] = useState(false);
  const groupInputRef = useRef<HTMLInputElement>(null);
  const groupDropdownListRef = useRef<HTMLDivElement>(null);

  // Edit channel number state
  const [editingChannelId, setEditingChannelId] = useState<number | null>(null);
  const [editingChannelNumber, setEditingChannelNumber] = useState('');

  // Inline stream display state
  const [channelStreams, setChannelStreams] = useState<Stream[]>([]);
  const [streamsLoading, setStreamsLoading] = useState(false);

  // Stream reorder sensors (separate from channel reorder)
  const streamSensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
  );


  // Sync local channels with props
  useEffect(() => {
    setLocalChannels(channels);
  }, [channels]);

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setGroupDropdownOpen(false);
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

  // Filter channel groups based on search text
  const filteredChannelGroups = channelGroups.filter((group) =>
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
  }, [selectedChannelId, channels]);

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
    try {
      const updatedChannel = await api.removeStreamFromChannel(selectedChannelId, streamId);
      onChannelUpdate(updatedChannel);
      setChannelStreams((prev) => prev.filter((s) => s.id !== streamId));
    } catch (err) {
      console.error('Failed to remove stream:', err);
    }
  };

  // Handle reordering streams within the channel
  const handleStreamDragEnd = async (event: DragEndEvent) => {
    if (!selectedChannelId) return;
    const { active, over } = event;

    if (over && active.id !== over.id) {
      const oldIndex = channelStreams.findIndex((s) => s.id === active.id);
      const newIndex = channelStreams.findIndex((s) => s.id === over.id);

      const newStreams = arrayMove(channelStreams, oldIndex, newIndex);
      setChannelStreams(newStreams);

      try {
        const updatedChannel = await api.reorderChannelStreams(
          selectedChannelId,
          newStreams.map((s) => s.id)
        );
        onChannelUpdate(updatedChannel);
      } catch (err) {
        console.error('Failed to reorder streams:', err);
        setChannelStreams(channelStreams); // Revert on error
      }
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

  // Close the create modal and reset form state
  const handleCloseCreateModal = () => {
    setShowCreateModal(false);
    setNewChannelName('');
    setNewChannelNumber('');
    setNewChannelGroup('');
    setGroupSearchText('');
    setShowGroupDropdown(false);
  };

  // Handle creating a new channel
  const handleCreateChannel = async () => {
    if (!newChannelName.trim()) return;

    setCreating(true);
    try {
      await onCreateChannel(
        newChannelName.trim(),
        newChannelNumber ? parseFloat(newChannelNumber) : undefined,
        newChannelGroup !== '' ? newChannelGroup : undefined
      );
      handleCloseCreateModal();
    } catch {
      // Error handled in parent
    } finally {
      setCreating(false);
    }
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

  // Handle editing channel number
  const handleStartEditNumber = (e: React.MouseEvent, channel: Channel) => {
    e.stopPropagation();
    setEditingChannelId(channel.id);
    setEditingChannelNumber(channel.channel_number?.toString() ?? '');
  };

  const handleSaveChannelNumber = async (channelId: number) => {
    const newNumber = editingChannelNumber.trim() ? parseFloat(editingChannelNumber) : null;
    try {
      const updatedChannel = await api.updateChannel(channelId, { channel_number: newNumber });
      onChannelUpdate(updatedChannel);
    } catch (err) {
      console.error('Failed to update channel number:', err);
    }
    setEditingChannelId(null);
  };

  const handleCancelEditNumber = () => {
    setEditingChannelId(null);
    setEditingChannelNumber('');
  };

  const toggleGroup = (groupId: number) => {
    setExpandedGroups((prev) => ({ ...prev, [groupId]: !prev[groupId] }));
  };

  const handleStreamDragOver = (e: React.DragEvent, channelId: number) => {
    // Only handle stream drags (from external drag source)
    if (e.dataTransfer.types.includes('streamid')) {
      e.preventDefault();
      setDragOverChannelId(channelId);
    }
  };

  const handleStreamDragLeave = () => {
    setDragOverChannelId(null);
  };

  const handleStreamDrop = (e: React.DragEvent, channelId: number) => {
    e.preventDefault();
    setDragOverChannelId(null);

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

  // Filter out auto-created channels, then group by channel_group_id
  const manualChannels = localChannels.filter((ch) => !ch.auto_created);
  const channelsByGroup = manualChannels.reduce<Record<number | 'ungrouped', Channel[]>>(
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

  // Sort channel groups by their lowest channel number
  const sortedChannelGroups = [...channelGroups]
    .filter((g) => channelsByGroup[g.id]?.length > 0)
    .sort((a, b) => {
      const aMin = channelsByGroup[a.id]?.[0]?.channel_number ?? 9999;
      const bMin = channelsByGroup[b.id]?.[0]?.channel_number ?? 9999;
      return aMin - bMin;
    });

  const handleDragStart = (_event: DragStartEvent) => {
    // Could add visual feedback here
  };

  const handleDragOver = (_event: DragOverEvent) => {
    // Could add drop zone preview here
  };

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;

    if (!over || active.id === over.id) return;

    const activeChannel = localChannels.find((c) => c.id === active.id);
    const overChannel = localChannels.find((c) => c.id === over.id);

    if (!activeChannel || !overChannel) return;

    // Get the group for the channels
    const groupId = activeChannel.channel_group_id ?? 'ungrouped';
    const groupChannels = channelsByGroup[groupId] || [];

    const oldIndex = groupChannels.findIndex((c) => c.id === active.id);
    const newIndex = groupChannels.findIndex((c) => c.id === over.id);

    if (oldIndex === -1 || newIndex === -1) return;

    // Reorder locally for immediate feedback
    const reorderedGroup = [...groupChannels];
    const [removed] = reorderedGroup.splice(oldIndex, 1);
    reorderedGroup.splice(newIndex, 0, removed);

    // Calculate new channel numbers
    // Find the starting number (use the first channel's number or 1)
    const startingNumber = reorderedGroup[0]?.channel_number ?? 1;

    // Update local state immediately
    const updatedChannels = localChannels.map((ch) => {
      const reorderedIndex = reorderedGroup.findIndex((r) => r.id === ch.id);
      if (reorderedIndex !== -1) {
        return { ...ch, channel_number: startingNumber + reorderedIndex };
      }
      return ch;
    });
    setLocalChannels(updatedChannels);

    // Call API to persist the reorder
    onChannelReorder(
      reorderedGroup.map((c) => c.id),
      startingNumber
    );
  };

  const renderGroup = (groupId: number | 'ungrouped', groupName: string, groupChannels: Channel[]) => {
    if (groupChannels.length === 0) return null;
    // If groups are selected, only show those groups (or ungrouped if showing all)
    if (selectedGroups.length > 0 && groupId !== 'ungrouped' && !selectedGroups.includes(groupId)) return null;

    const numericGroupId = groupId === 'ungrouped' ? -1 : groupId;
    const isExpanded = expandedGroups[numericGroupId] === true;

    return (
      <div key={groupId} className="channel-group">
        <div className="group-header" onClick={() => toggleGroup(numericGroupId)}>
          <span className="group-toggle">{isExpanded ? '▼' : '▶'}</span>
          <span className="group-name">{groupName}</span>
          <span className="group-count">{groupChannels.length}</span>
        </div>
        {isExpanded && (
          <DndContext
            sensors={sensors}
            collisionDetection={closestCenter}
            onDragStart={handleDragStart}
            onDragOver={handleDragOver}
            onDragEnd={handleDragEnd}
          >
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
                      editingNumber={editingChannelNumber}
                      onEditingNumberChange={setEditingChannelNumber}
                      onStartEditNumber={(e) => handleStartEditNumber(e, channel)}
                      onSaveNumber={() => handleSaveChannelNumber(channel.id)}
                      onCancelEditNumber={handleCancelEditNumber}
                      onClick={() => handleChannelClick(channel)}
                      onStreamDragOver={(e) => handleStreamDragOver(e, channel.id)}
                      onStreamDragLeave={handleStreamDragLeave}
                      onStreamDrop={(e) => handleStreamDrop(e, channel.id)}
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
          </DndContext>
        )}
      </div>
    );
  };

  return (
    <div className="channels-pane">
      <div className="pane-header">
        <h2>Channels</h2>
        <button className="create-channel-btn" onClick={() => setShowCreateModal(true)}>
          + New
        </button>
      </div>

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
                Channel Number
                <input
                  type="text"
                  value={newChannelNumber}
                  onChange={(e) => setNewChannelNumber(e.target.value)}
                  placeholder="e.g., 100"
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
                      {filteredChannelGroups.map((group) => (
                        <div
                          key={group.id}
                          className={`group-autocomplete-option ${newChannelGroup === group.id ? 'selected' : ''}`}
                          onClick={() => handleSelectGroup(group)}
                        >
                          {group.name}
                        </div>
                      ))}
                      {filteredChannelGroups.length === 0 && groupSearchText && (
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
                disabled={creating || !newChannelName.trim()}
              >
                {creating ? 'Creating...' : 'Create Channel'}
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
        <div className="group-filter-dropdown" ref={dropdownRef}>
          <button
            className="group-filter-button"
            onClick={() => setGroupDropdownOpen(!groupDropdownOpen)}
          >
            <span>
              {selectedGroups.length === 0
                ? 'All Groups'
                : `${selectedGroups.length} group${selectedGroups.length > 1 ? 's' : ''} selected`}
            </span>
            <span className="dropdown-arrow">{groupDropdownOpen ? '▲' : '▼'}</span>
          </button>
          {groupDropdownOpen && (
            <div className="group-filter-menu">
              <div className="group-filter-actions">
                <button
                  className="group-filter-action"
                  onClick={() => {
                    // Select all groups that have channels
                    onSelectedGroupsChange(sortedChannelGroups.map((g) => g.id));
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
                {sortedChannelGroups.map((group) => (
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
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="pane-content">
        {loading ? (
          <div className="loading">Loading channels...</div>
        ) : (
          <>
            {sortedChannelGroups.map((group) =>
              renderGroup(group.id, group.name, channelsByGroup[group.id] || [])
            )}
            {selectedGroups.length === 0 &&
              renderGroup('ungrouped', 'Uncategorized', channelsByGroup.ungrouped)}
          </>
        )}
      </div>
    </div>
  );
}
