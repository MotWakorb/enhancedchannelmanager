import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import type { Stream, M3UAccount } from '../types';
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
}: StreamsPaneProps) {
  const {
    selectedIds,
    selectedCount,
    handleSelect,
    selectAll,
    clearSelection,
    isSelected,
  } = useSelection(streams);

  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());

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

  return (
    <div className="streams-pane">
      <div className="pane-header">
        <h2>Streams</h2>
        {selectedCount > 0 && (
          <div className="selection-info">
            <span className="selection-count">{selectedCount} selected</span>
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
                          <span className="selection-checkbox">
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
    </div>
  );
}
