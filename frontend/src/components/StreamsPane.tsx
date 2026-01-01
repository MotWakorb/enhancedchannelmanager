import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import type { Stream, M3UAccount, ChannelGroup } from '../types';
import { useSelection } from '../hooks';
import './StreamsPane.css';

interface StreamGroup {
  name: string;
  streams: Stream[];
  expanded: boolean;
}

interface StreamsPaneProps {
  streams: Stream[];
  providers: M3UAccount[];
  streamGroups: string[];
  searchTerm: string;
  onSearchChange: (term: string) => void;
  providerFilter: number | null;
  onProviderFilterChange: (providerId: number | null) => void;
  groupFilter: string | null;
  onGroupFilterChange: (group: string | null) => void;
  loading: boolean;
  onBulkAddToChannel?: (streamIds: number[], channelId: number) => void;
  // Multi-select support
  selectedProviders?: number[];
  onSelectedProvidersChange?: (providerIds: number[]) => void;
  selectedStreamGroups?: string[];
  onSelectedStreamGroupsChange?: (groups: string[]) => void;
  // Bulk channel creation
  isEditMode?: boolean;
  channelGroups?: ChannelGroup[];
  onBulkCreateFromGroup?: (
    streams: Stream[],
    startingNumber: number,
    channelGroupId: number | null,
    newGroupName?: string
  ) => Promise<void>;
}

export function StreamsPane({
  streams,
  providers,
  streamGroups,
  searchTerm,
  onSearchChange,
  providerFilter,
  onProviderFilterChange,
  groupFilter,
  onGroupFilterChange,
  loading,
  selectedProviders = [],
  onSelectedProvidersChange,
  selectedStreamGroups = [],
  onSelectedStreamGroupsChange,
  isEditMode = false,
  channelGroups = [],
  onBulkCreateFromGroup,
}: StreamsPaneProps) {
  const {
    selectedIds,
    selectedCount,
    handleSelect,
    toggleSelect,
    selectAll,
    clearSelection,
    isSelected,
  } = useSelection(streams);

  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());

  // Bulk create modal state
  const [bulkCreateModalOpen, setBulkCreateModalOpen] = useState(false);
  const [bulkCreateGroup, setBulkCreateGroup] = useState<StreamGroup | null>(null);
  const [bulkCreateStreams, setBulkCreateStreams] = useState<Stream[]>([]); // For selected streams
  const [bulkCreateStartingNumber, setBulkCreateStartingNumber] = useState<string>('');
  const [bulkCreateGroupOption, setBulkCreateGroupOption] = useState<'same' | 'existing' | 'new'>('same');
  const [bulkCreateSelectedGroupId, setBulkCreateSelectedGroupId] = useState<number | null>(null);
  const [bulkCreateNewGroupName, setBulkCreateNewGroupName] = useState('');
  const [bulkCreateLoading, setBulkCreateLoading] = useState(false);

  // Dropdown state
  const [providerDropdownOpen, setProviderDropdownOpen] = useState(false);
  const [groupDropdownOpen, setGroupDropdownOpen] = useState(false);
  const providerDropdownRef = useRef<HTMLDivElement>(null);
  const groupDropdownRef = useRef<HTMLDivElement>(null);

  // Close dropdowns when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (providerDropdownRef.current && !providerDropdownRef.current.contains(event.target as Node)) {
        setProviderDropdownOpen(false);
      }
      if (groupDropdownRef.current && !groupDropdownRef.current.contains(event.target as Node)) {
        setGroupDropdownOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Determine if we're using multi-select mode
  const useMultiSelectProviders = !!onSelectedProvidersChange;
  const useMultiSelectGroups = !!onSelectedStreamGroupsChange;

  // Group and sort streams
  const groupedStreams = useMemo((): StreamGroup[] => {
    const groups = new Map<string, Stream[]>();

    // Group streams by channel_group_name
    streams.forEach((stream) => {
      const groupName = stream.channel_group_name || 'Ungrouped';
      if (!groups.has(groupName)) {
        groups.set(groupName, []);
      }
      groups.get(groupName)!.push(stream);
    });

    // Sort streams within each group alphabetically
    groups.forEach((groupStreams) => {
      groupStreams.sort((a, b) => a.name.localeCompare(b.name));
    });

    // Convert to array and sort groups alphabetically (Ungrouped at end)
    const sortedGroups = Array.from(groups.entries())
      .sort(([a], [b]) => {
        if (a === 'Ungrouped') return 1;
        if (b === 'Ungrouped') return -1;
        return a.localeCompare(b);
      })
      .map(([name, groupStreams]) => ({
        name,
        streams: groupStreams,
        expanded: expandedGroups.has(name),
      }));

    return sortedGroups;
  }, [streams, expandedGroups]);


  const toggleGroup = useCallback((groupName: string) => {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(groupName)) {
        next.delete(groupName);
      } else {
        next.add(groupName);
      }
      return next;
    });
  }, []);

  // Clear selection when streams change (new search/filter)
  useEffect(() => {
    clearSelection();
  }, [searchTerm, providerFilter, groupFilter, clearSelection]);

  // Keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Ctrl/Cmd+A to select all
      if ((e.ctrlKey || e.metaKey) && e.key === 'a') {
        const activeElement = document.activeElement;
        if (activeElement?.tagName !== 'INPUT' && activeElement?.tagName !== 'TEXTAREA') {
          e.preventDefault();
          selectAll();
        }
      }
      // Escape to clear selection
      if (e.key === 'Escape') {
        clearSelection();
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [selectAll, clearSelection]);

  const handleDragStart = useCallback(
    (e: React.DragEvent, stream: Stream) => {
      // If dragging a selected item, drag all selected
      if (isSelected(stream.id) && selectedCount > 1) {
        const selectedStreamIds = Array.from(selectedIds);
        e.dataTransfer.setData('streamIds', JSON.stringify(selectedStreamIds));
        e.dataTransfer.setData('streamId', String(stream.id)); // Fallback for single
        e.dataTransfer.setData('bulkDrag', 'true');
        e.dataTransfer.effectAllowed = 'copy';

        // Custom drag image showing count
        const dragEl = document.createElement('div');
        dragEl.className = 'drag-preview';
        dragEl.textContent = `${selectedCount} streams`;
        dragEl.style.cssText = `
          position: absolute;
          top: -1000px;
          background: #646cff;
          color: white;
          padding: 8px 16px;
          border-radius: 4px;
          font-weight: 500;
        `;
        document.body.appendChild(dragEl);
        e.dataTransfer.setDragImage(dragEl, 50, 20);
        setTimeout(() => document.body.removeChild(dragEl), 0);
      } else {
        e.dataTransfer.setData('streamId', String(stream.id));
        e.dataTransfer.setData('streamName', stream.name);
        e.dataTransfer.effectAllowed = 'copy';
      }
    },
    [isSelected, selectedCount, selectedIds]
  );

  const handleItemClick = useCallback(
    (e: React.MouseEvent, stream: Stream) => {
      handleSelect(stream.id, e);
    },
    [handleSelect]
  );

  // Bulk create handlers
  const openBulkCreateModal = useCallback((group: StreamGroup) => {
    setBulkCreateGroup(group);
    setBulkCreateStreams([]);
    setBulkCreateStartingNumber('');
    setBulkCreateGroupOption('same');
    setBulkCreateSelectedGroupId(null);
    setBulkCreateNewGroupName('');
    setBulkCreateModalOpen(true);
  }, []);

  const openBulkCreateModalForSelection = useCallback(() => {
    // Get selected streams in order
    const selectedStreamsList = streams.filter(s => selectedIds.has(s.id));
    setBulkCreateGroup(null);
    setBulkCreateStreams(selectedStreamsList);
    setBulkCreateStartingNumber('');
    setBulkCreateGroupOption('existing'); // Default to existing group for selections
    setBulkCreateSelectedGroupId(null);
    setBulkCreateNewGroupName('');
    setBulkCreateModalOpen(true);
  }, [streams, selectedIds]);

  const closeBulkCreateModal = useCallback(() => {
    setBulkCreateModalOpen(false);
    setBulkCreateGroup(null);
    setBulkCreateStreams([]);
  }, []);

  // Get the streams to create channels from (either from group or selection)
  const streamsToCreate = bulkCreateGroup ? bulkCreateGroup.streams : bulkCreateStreams;
  const isFromGroup = !!bulkCreateGroup;

  // Compute unique stream names and duplicate count for the modal display
  const bulkCreateStats = useMemo(() => {
    const streamsByName = new Map<string, Stream[]>();
    for (const stream of streamsToCreate) {
      const existing = streamsByName.get(stream.name);
      if (existing) {
        existing.push(stream);
      } else {
        streamsByName.set(stream.name, [stream]);
      }
    }
    const uniqueCount = streamsByName.size;
    const duplicateCount = streamsToCreate.length - uniqueCount;
    const hasDuplicates = duplicateCount > 0;
    return { uniqueCount, duplicateCount, hasDuplicates, streamsByName };
  }, [streamsToCreate]);

  const handleBulkCreate = useCallback(async () => {
    if (streamsToCreate.length === 0 || !onBulkCreateFromGroup) return;

    const startingNum = parseInt(bulkCreateStartingNumber, 10);
    if (isNaN(startingNum) || startingNum < 0) {
      alert('Please enter a valid starting channel number');
      return;
    }

    setBulkCreateLoading(true);

    try {
      let groupId: number | null = null;
      let newGroupName: string | undefined;

      if (bulkCreateGroupOption === 'same' && bulkCreateGroup) {
        // Find existing group with same name, or create new
        const existingGroup = channelGroups.find(g => g.name === bulkCreateGroup.name);
        if (existingGroup) {
          groupId = existingGroup.id;
        } else {
          newGroupName = bulkCreateGroup.name;
        }
      } else if (bulkCreateGroupOption === 'existing') {
        groupId = bulkCreateSelectedGroupId;
      } else if (bulkCreateGroupOption === 'new') {
        if (!bulkCreateNewGroupName.trim()) {
          alert('Please enter a name for the new group');
          setBulkCreateLoading(false);
          return;
        }
        newGroupName = bulkCreateNewGroupName.trim();
      }

      await onBulkCreateFromGroup(
        streamsToCreate,
        startingNum,
        groupId,
        newGroupName
      );

      // Clear selection after successful creation
      if (!isFromGroup) {
        clearSelection();
      }

      closeBulkCreateModal();
    } catch (error) {
      console.error('Bulk create failed:', error);
      alert(`Bulk create failed: ${error}`);
    } finally {
      setBulkCreateLoading(false);
    }
  }, [
    streamsToCreate,
    isFromGroup,
    bulkCreateGroup,
    bulkCreateStartingNumber,
    bulkCreateGroupOption,
    bulkCreateSelectedGroupId,
    bulkCreateNewGroupName,
    channelGroups,
    onBulkCreateFromGroup,
    clearSelection,
    closeBulkCreateModal,
  ]);

  return (
    <div className="streams-pane">
      <div className="pane-header">
        <h2>Streams</h2>
        {selectedCount > 0 && (
          <div className="selection-info">
            <span className="selection-count">{selectedCount} selected</span>
            {isEditMode && onBulkCreateFromGroup && (
              <button
                className="create-channels-btn"
                onClick={openBulkCreateModalForSelection}
                title="Create channels from selected streams"
              >
                <span className="material-icons">playlist_add</span>
                Create
              </button>
            )}
            <button className="clear-selection-btn" onClick={clearSelection}>
              Clear
            </button>
          </div>
        )}
      </div>

      <div className="pane-filters">
        <input
          type="text"
          placeholder="Search streams..."
          value={searchTerm}
          onChange={(e) => onSearchChange(e.target.value)}
          className="search-input"
        />
        <div className="filter-row">
          {/* Provider Filter Dropdown */}
          {useMultiSelectProviders ? (
            <div className="filter-dropdown" ref={providerDropdownRef}>
              <button
                className="filter-dropdown-button"
                onClick={() => setProviderDropdownOpen(!providerDropdownOpen)}
              >
                <span>
                  {selectedProviders.length === 0
                    ? 'All Providers'
                    : `${selectedProviders.length} provider${selectedProviders.length > 1 ? 's' : ''}`}
                </span>
                <span className="dropdown-arrow">{providerDropdownOpen ? '▲' : '▼'}</span>
              </button>
              {providerDropdownOpen && (
                <div className="filter-dropdown-menu">
                  <div className="filter-dropdown-actions">
                    <button
                      className="filter-dropdown-action"
                      onClick={() => onSelectedProvidersChange!(providers.map((p) => p.id))}
                    >
                      Select All
                    </button>
                    <button
                      className="filter-dropdown-action"
                      onClick={() => onSelectedProvidersChange!([])}
                    >
                      Clear All
                    </button>
                  </div>
                  <div className="filter-dropdown-options">
                    {providers.map((provider) => (
                      <label key={provider.id} className="filter-dropdown-option">
                        <input
                          type="checkbox"
                          checked={selectedProviders.includes(provider.id)}
                          onChange={(e) => {
                            if (e.target.checked) {
                              onSelectedProvidersChange!([...selectedProviders, provider.id]);
                            } else {
                              onSelectedProvidersChange!(selectedProviders.filter((id) => id !== provider.id));
                            }
                          }}
                        />
                        <span className="filter-option-name">{provider.name}</span>
                      </label>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ) : (
            <select
              value={providerFilter ?? ''}
              onChange={(e) =>
                onProviderFilterChange(e.target.value ? parseInt(e.target.value, 10) : null)
              }
              className="filter-select"
            >
              <option value="">All Providers</option>
              {providers.map((provider) => (
                <option key={provider.id} value={provider.id}>
                  {provider.name}
                </option>
              ))}
            </select>
          )}

          {/* Group Filter Dropdown */}
          {useMultiSelectGroups ? (
            <div className="filter-dropdown" ref={groupDropdownRef}>
              <button
                className="filter-dropdown-button"
                onClick={() => setGroupDropdownOpen(!groupDropdownOpen)}
              >
                <span>
                  {selectedStreamGroups.length === 0
                    ? 'All Groups'
                    : `${selectedStreamGroups.length} group${selectedStreamGroups.length > 1 ? 's' : ''}`}
                </span>
                <span className="dropdown-arrow">{groupDropdownOpen ? '▲' : '▼'}</span>
              </button>
              {groupDropdownOpen && (
                <div className="filter-dropdown-menu">
                  <div className="filter-dropdown-actions">
                    <button
                      className="filter-dropdown-action"
                      onClick={() => onSelectedStreamGroupsChange!(streamGroups)}
                    >
                      Select All
                    </button>
                    <button
                      className="filter-dropdown-action"
                      onClick={() => onSelectedStreamGroupsChange!([])}
                    >
                      Clear All
                    </button>
                  </div>
                  <div className="filter-dropdown-options">
                    {streamGroups.map((group) => (
                      <label key={group} className="filter-dropdown-option">
                        <input
                          type="checkbox"
                          checked={selectedStreamGroups.includes(group)}
                          onChange={(e) => {
                            if (e.target.checked) {
                              onSelectedStreamGroupsChange!([...selectedStreamGroups, group]);
                            } else {
                              onSelectedStreamGroupsChange!(selectedStreamGroups.filter((g) => g !== group));
                            }
                          }}
                        />
                        <span className="filter-option-name">{group}</span>
                      </label>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ) : (
            <select
              value={groupFilter ?? ''}
              onChange={(e) => onGroupFilterChange(e.target.value || null)}
              className="filter-select"
            >
              <option value="">All Groups</option>
              {streamGroups.map((group) => (
                <option key={group} value={group}>
                  {group}
                </option>
              ))}
            </select>
          )}
        </div>
      </div>

      <div className="pane-content">
        {loading && streams.length === 0 ? (
          <div className="loading">Loading streams...</div>
        ) : (
          <>
            <div className="streams-list">
              {groupedStreams.map((group) => (
                <div key={group.name} className="stream-group">
                  <div
                    className="stream-group-header"
                    onClick={() => toggleGroup(group.name)}
                  >
                    <span className="expand-icon">{group.expanded ? '▼' : '▶'}</span>
                    <span className="group-name">{group.name}</span>
                    <span className="group-count">{group.streams.length}</span>
                    {isEditMode && onBulkCreateFromGroup && (
                      <button
                        className="bulk-create-btn"
                        onClick={(e) => {
                          e.stopPropagation();
                          openBulkCreateModal(group);
                        }}
                        title="Create channels from this group"
                      >
                        <span className="material-icons">playlist_add</span>
                      </button>
                    )}
                  </div>
                  {group.expanded && (
                    <div className="stream-group-items">
                      {group.streams.map((stream) => (
                        <div
                          key={stream.id}
                          className={`stream-item ${isSelected(stream.id) ? 'selected' : ''}`}
                          draggable
                          onClick={(e) => handleItemClick(e, stream)}
                          onDragStart={(e) => handleDragStart(e, stream)}
                        >
                          <span
                            className="selection-checkbox"
                            onClick={(e) => {
                              e.stopPropagation();
                              toggleSelect(stream.id);
                            }}
                          >
                            {isSelected(stream.id) ? '☑' : '☐'}
                          </span>
                          {stream.logo_url && (
                            <img
                              src={stream.logo_url}
                              alt=""
                              className="stream-logo"
                              onError={(e) => {
                                (e.target as HTMLImageElement).style.display = 'none';
                              }}
                            />
                          )}
                          <div className="stream-info">
                            <span className="stream-name">{stream.name}</span>
                            {stream.m3u_account && (
                              <span className="stream-provider">
                                {providers.find((p) => p.id === stream.m3u_account)?.name || 'Unknown'}
                              </span>
                            )}
                          </div>
                          <span className="drag-handle">⋮⋮</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </>
        )}
      </div>

      {/* Bulk Create Modal */}
      {bulkCreateModalOpen && streamsToCreate.length > 0 && (
        <div className="modal-overlay" onClick={closeBulkCreateModal}>
          <div className="bulk-create-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>
                {isFromGroup
                  ? `Create Channels from "${bulkCreateGroup!.name}"`
                  : `Create Channels from ${streamsToCreate.length} Selected Streams`
                }
              </h3>
              <button className="modal-close-btn" onClick={closeBulkCreateModal}>
                <span className="material-icons">close</span>
              </button>
            </div>

            <div className="modal-body">
              <div className="bulk-create-info">
                <span className="material-icons">info</span>
                {bulkCreateStats.hasDuplicates ? (
                  <span>
                    <strong>{bulkCreateStats.uniqueCount}</strong> channels will be created from {streamsToCreate.length} streams
                    <br />
                    <span className="duplicate-info">
                      ({bulkCreateStats.duplicateCount} duplicate names will be merged — same-name streams from different providers get assigned to one channel)
                    </span>
                  </span>
                ) : (
                  <span>{streamsToCreate.length} channels will be created, each with its stream assigned</span>
                )}
              </div>

              <div className="form-group">
                <label>Starting Channel Number</label>
                <input
                  type="number"
                  min="0"
                  value={bulkCreateStartingNumber}
                  onChange={(e) => setBulkCreateStartingNumber(e.target.value)}
                  placeholder="e.g., 100"
                  className="form-input"
                  autoFocus
                />
                {bulkCreateStartingNumber && !isNaN(parseInt(bulkCreateStartingNumber, 10)) && (
                  <div className="number-range-preview">
                    Channels {bulkCreateStartingNumber} - {parseInt(bulkCreateStartingNumber, 10) + bulkCreateStats.uniqueCount - 1}
                  </div>
                )}
              </div>

              <div className="form-group">
                <label>Channel Group</label>
                <div className="radio-group">
                  {/* Only show "same name" option when creating from a group */}
                  {isFromGroup && bulkCreateGroup && (
                    <label className="radio-option">
                      <input
                        type="radio"
                        name="groupOption"
                        checked={bulkCreateGroupOption === 'same'}
                        onChange={() => setBulkCreateGroupOption('same')}
                      />
                      <span>Use same name "{bulkCreateGroup.name}"</span>
                      {channelGroups.find(g => g.name === bulkCreateGroup.name) ? (
                        <span className="group-exists-badge">exists</span>
                      ) : (
                        <span className="group-new-badge">will create</span>
                      )}
                    </label>
                  )}

                  <label className="radio-option">
                    <input
                      type="radio"
                      name="groupOption"
                      checked={bulkCreateGroupOption === 'existing'}
                      onChange={() => setBulkCreateGroupOption('existing')}
                    />
                    <span>Select existing group</span>
                  </label>
                  {bulkCreateGroupOption === 'existing' && (
                    <select
                      value={bulkCreateSelectedGroupId ?? ''}
                      onChange={(e) => setBulkCreateSelectedGroupId(e.target.value ? parseInt(e.target.value, 10) : null)}
                      className="form-select"
                    >
                      <option value="">-- Select a group --</option>
                      {channelGroups.map((g) => (
                        <option key={g.id} value={g.id}>{g.name}</option>
                      ))}
                    </select>
                  )}

                  <label className="radio-option">
                    <input
                      type="radio"
                      name="groupOption"
                      checked={bulkCreateGroupOption === 'new'}
                      onChange={() => setBulkCreateGroupOption('new')}
                    />
                    <span>Create new group</span>
                  </label>
                  {bulkCreateGroupOption === 'new' && (
                    <input
                      type="text"
                      value={bulkCreateNewGroupName}
                      onChange={(e) => setBulkCreateNewGroupName(e.target.value)}
                      placeholder="New group name"
                      className="form-input"
                    />
                  )}
                </div>
              </div>

              <div className="bulk-create-preview">
                <label>Preview (first 10)</label>
                <div className="preview-list">
                  {streamsToCreate.slice(0, 10).map((stream, idx) => {
                    const num = bulkCreateStartingNumber ? parseInt(bulkCreateStartingNumber, 10) + idx : '?';
                    return (
                      <div key={stream.id} className="preview-item">
                        <span className="preview-number">{num}</span>
                        <span className="preview-name">{stream.name}</span>
                      </div>
                    );
                  })}
                  {streamsToCreate.length > 10 && (
                    <div className="preview-more">
                      ... and {streamsToCreate.length - 10} more
                    </div>
                  )}
                </div>
              </div>
            </div>

            <div className="modal-footer">
              <button className="btn-cancel" onClick={closeBulkCreateModal}>
                Cancel
              </button>
              <button
                className="btn-create"
                onClick={handleBulkCreate}
                disabled={bulkCreateLoading || !bulkCreateStartingNumber}
              >
                {bulkCreateLoading ? (
                  <>
                    <span className="material-icons spinning">sync</span>
                    Creating...
                  </>
                ) : (
                  <>
                    <span className="material-icons">add</span>
                    Create {bulkCreateStats.uniqueCount} Channels
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
