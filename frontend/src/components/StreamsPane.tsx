import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import type { Stream, M3UAccount, ChannelGroup, ChannelProfile } from '../types';
import { useSelection, useExpandCollapse } from '../hooks';
import { normalizeStreamName, detectRegionalVariants, filterStreamsByTimezone, detectCountryPrefixes, getUniqueCountryPrefixes, detectNetworkPrefixes, detectNetworkSuffixes, type TimezonePreference, type NormalizeOptions, type NumberSeparator, type PrefixOrder, type NormalizationSettings } from '../services/api';
import { naturalCompare } from '../utils/naturalSort';
import { openInVLC } from '../utils/vlc';
import { useCopyFeedback } from '../hooks/useCopyFeedback';
import { useDropdown } from '../hooks/useDropdown';
import { useContextMenu } from '../hooks/useContextMenu';
import { useKeyboardShortcuts } from '../hooks/useKeyboardShortcuts';
import { QuickTagManager } from './QuickTagManager';
import './StreamsPane.css';

interface StreamGroup {
  name: string;
  streams: Stream[];
  expanded: boolean;
}

// Channel defaults from settings
export interface ChannelDefaults {
  includeChannelNumberInName: boolean;
  channelNumberSeparator: string;
  removeCountryPrefix: boolean;
  includeCountryInName: boolean;
  countrySeparator: string;
  timezonePreference: string;
  defaultChannelProfileId?: number | null;
  customNetworkPrefixes?: string[];
  customNetworkSuffixes?: string[];
  streamSortPriority?: ('resolution' | 'bitrate' | 'framerate')[];
  streamSortEnabled?: Record<'resolution' | 'bitrate' | 'framerate', boolean>;
  deprioritizeFailedStreams?: boolean;
  normalizationSettings?: NormalizationSettings;
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
  onClearStreamFilters?: () => void;
  // Bulk channel creation
  isEditMode?: boolean;
  channelGroups?: ChannelGroup[];
  selectedChannelGroups?: number[]; // IDs of enabled/visible channel groups
  channelProfiles?: ChannelProfile[];
  channelDefaults?: ChannelDefaults;
  // External trigger to open bulk create modal for stream groups (set by dropping on channels pane)
  // Supports multiple groups being dropped at once
  externalTriggerGroupNames?: string[] | null;
  // External trigger to open bulk create modal for specific streams (set by dropping streams on channels pane)
  externalTriggerStreamIds?: number[] | null;
  // Target group ID and starting number for pre-filling the bulk create modal
  externalTriggerTargetGroupId?: number | null;
  externalTriggerStartingNumber?: number | null;
  // External trigger to open bulk create modal for manual entry (no streams pre-selected)
  externalTriggerManualEntry?: boolean;
  onExternalTriggerHandled?: () => void;
  onBulkCreateFromGroup?: (
    streams: Stream[],
    startingNumber: number,
    channelGroupId: number | null,
    newGroupName?: string,
    timezonePreference?: TimezonePreference,
    stripCountryPrefix?: boolean,
    addChannelNumber?: boolean,
    numberSeparator?: NumberSeparator,
    keepCountryPrefix?: boolean,
    countrySeparator?: NumberSeparator,
    prefixOrder?: PrefixOrder,
    stripNetworkPrefix?: boolean,
    customNetworkPrefixes?: string[],
    stripNetworkSuffix?: boolean,
    customNetworkSuffixes?: string[],
    profileIds?: number[],
    pushDownOnConflict?: boolean
  ) => Promise<void>;
  // Callback to check for conflicts with existing channel numbers
  // Returns the number of conflicting channels
  onCheckConflicts?: (startingNumber: number, count: number) => number;
  // Callback to get the highest existing channel number (for "insert at end" option)
  onGetHighestChannelNumber?: () => number;
  // Appearance settings
  showStreamUrls?: boolean;
  hideUngroupedStreams?: boolean;
  // Refresh streams (bypasses cache)
  onRefreshStreams?: () => void;
  // Set of stream IDs that are already mapped to channels (for "hide mapped" filter)
  mappedStreamIds?: Set<number>;
  // Callback when a group is expanded (for lazy loading streams)
  // Passes the group name so only that group's streams can be loaded
  onGroupExpand?: (groupName: string) => void;
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
  onClearStreamFilters,
  isEditMode = false,
  channelGroups = [],
  selectedChannelGroups = [],
  channelProfiles = [],
  channelDefaults,
  externalTriggerGroupNames = null,
  externalTriggerStreamIds = null,
  externalTriggerTargetGroupId = null,
  externalTriggerStartingNumber = null,
  externalTriggerManualEntry = false,
  onExternalTriggerHandled,
  onBulkCreateFromGroup,
  onCheckConflicts,
  onGetHighestChannelNumber,
  showStreamUrls = true,
  hideUngroupedStreams = true,
  onRefreshStreams,
  mappedStreamIds,
  onGroupExpand,
}: StreamsPaneProps) {
  // Expand/collapse groups with useExpandCollapse hook
  const {
    expandedIds: expandedGroups,
    isExpanded: isGroupExpanded,
    toggleExpand: toggleGroup,
    expandAll: expandAllGroupsInternal,
    collapseAll: collapseAllGroups,
  } = useExpandCollapse<string>();

  // Hide mapped streams toggle state
  const [hideMappedStreams, setHideMappedStreams] = useState(false);

  // Copy feedback state
  const { copySuccess, copyError, handleCopy } = useCopyFeedback();

  // Filter out mapped streams if toggle is enabled
  const filteredStreams = useMemo(() => {
    if (!hideMappedStreams || !mappedStreamIds || mappedStreamIds.size === 0) {
      return streams;
    }
    return streams.filter(stream => !mappedStreamIds.has(stream.id));
  }, [streams, hideMappedStreams, mappedStreamIds]);

  // Shared memoized grouping logic to avoid duplication
  // Groups and sorts streams, then returns sorted entries
  // Always show all groups from streamGroups, populated with any loaded streams
  const sortedStreamGroups = useMemo((): [string, Stream[]][] => {
    const groups = new Map<string, Stream[]>();

    // First, create empty entries for all known groups from the API
    // This ensures all groups are visible even before their streams are loaded (lazy loading)
    streamGroups.forEach((groupName) => {
      if (!hideUngroupedStreams || groupName !== 'Ungrouped') {
        groups.set(groupName, []);
      }
    });

    // Then populate groups with any loaded/filtered streams
    filteredStreams.forEach((stream) => {
      const groupName = stream.channel_group_name || 'Ungrouped';
      if (!hideUngroupedStreams || groupName !== 'Ungrouped') {
        if (!groups.has(groupName)) {
          // Handle case where stream has a group not in streamGroups list
          groups.set(groupName, []);
        }
        groups.get(groupName)!.push(stream);
      }
    });

    // Sort streams within each group alphabetically with natural sort
    groups.forEach((groupStreams) => {
      if (groupStreams.length > 0) {
        groupStreams.sort((a, b) => naturalCompare(a.name, b.name));
      }
    });

    // Convert to sorted array of [name, streams] tuples
    // Filter out Ungrouped if hideUngroupedStreams is true
    return Array.from(groups.entries())
      .filter(([name]) => !hideUngroupedStreams || name !== 'Ungrouped')
      .sort(([a], [b]) => {
        if (a === 'Ungrouped') return 1;
        if (b === 'Ungrouped') return -1;
        return naturalCompare(a, b);
      });
  }, [filteredStreams, hideUngroupedStreams, streamGroups]);

  // Compute streams in display order (flattened array for selection)
  // This must be computed before useSelection so shift-click works correctly
  const displayOrderStreams = useMemo((): Stream[] => {
    const result: Stream[] = [];
    for (const [, groupStreams] of sortedStreamGroups) {
      result.push(...groupStreams);
    }
    return result;
  }, [sortedStreamGroups]);

  // Use display order for selection so shift-click works correctly
  const {
    selectedIds,
    selectedCount,
    toggleSelect,
    selectMultiple,
    deselectMultiple,
    selectAll,
    clearSelection,
    isSelected,
  } = useSelection(displayOrderStreams);

  // Track selected stream groups (for multi-group bulk creation)
  const [selectedGroupNames, setSelectedGroupNames] = useState<Set<string>>(new Set());

  // Bulk create modal state
  const [bulkCreateModalOpen, setBulkCreateModalOpen] = useState(false);
  const [bulkCreateGroup, setBulkCreateGroup] = useState<StreamGroup | null>(null);
  const [bulkCreateGroups, setBulkCreateGroups] = useState<StreamGroup[]>([]); // For multi-group creation
  const [bulkCreateStreams, setBulkCreateStreams] = useState<Stream[]>([]); // For selected streams
  const [bulkCreateMultiGroupOption, setBulkCreateMultiGroupOption] = useState<'separate' | 'single'>('separate');
  // Custom names for each group when using 'separate' mode (maps original group name to custom name)
  const [bulkCreateCustomGroupNames, setBulkCreateCustomGroupNames] = useState<Map<string, string>>(new Map());
  // Starting channel number for each group when using 'separate' mode (maps original group name to starting number)
  const [bulkCreateGroupStartNumbers, setBulkCreateGroupStartNumbers] = useState<Map<string, string>>(new Map());
  const [bulkCreateStartingNumber, setBulkCreateStartingNumber] = useState<string>('');
  const [bulkCreateGroupOption, setBulkCreateGroupOption] = useState<'same' | 'existing' | 'new'>('same');
  const [bulkCreateSelectedGroupId, setBulkCreateSelectedGroupId] = useState<number | null>(null);
  const [bulkCreateNewGroupName, setBulkCreateNewGroupName] = useState('');
  const [bulkCreateLoading, setBulkCreateLoading] = useState(false);
  const [bulkCreateShowConflict, setBulkCreateShowConflict] = useState(false);
  const [bulkCreateConflictCount, setBulkCreateConflictCount] = useState(0);
  const [bulkCreateEndOfSequenceNumber, setBulkCreateEndOfSequenceNumber] = useState(0);
  const [bulkCreateTimezone, setBulkCreateTimezone] = useState<TimezonePreference>('both');
  const [bulkCreateStripCountry, setBulkCreateStripCountry] = useState(false);
  const [bulkCreateKeepCountry, setBulkCreateKeepCountry] = useState(false);
  const [bulkCreateCountrySeparator, setBulkCreateCountrySeparator] = useState<NumberSeparator>('|');
  const [bulkCreateAddNumber, setBulkCreateAddNumber] = useState(false);
  const [bulkCreateSeparator, setBulkCreateSeparator] = useState<NumberSeparator>('|');
  const [bulkCreatePrefixOrder, setBulkCreatePrefixOrder] = useState<PrefixOrder>('number-first');
  const [bulkCreateStripNetwork, setBulkCreateStripNetwork] = useState(false);
  const [bulkCreateStripSuffix, setBulkCreateStripSuffix] = useState(false);
  const [bulkCreateSelectedProfiles, setBulkCreateSelectedProfiles] = useState<Set<number>>(new Set());
  const [bulkCreateGroupSearch, setBulkCreateGroupSearch] = useState('');
  const [profilesExpanded, setProfilesExpanded] = useState(false);
  const [bulkCreateNormalizationSettings, setBulkCreateNormalizationSettings] = useState<NormalizationSettings>({
    disabledBuiltinTags: [],
    customTags: [],
  });

  // Bulk create group dropdown management
  const {
    isOpen: bulkCreateGroupDropdownOpen,
    setIsOpen: setBulkCreateGroupDropdownOpen,
    dropdownRef: bulkCreateGroupDropdownRef,
  } = useDropdown();
  const [namingOptionsExpanded, setNamingOptionsExpanded] = useState(false);
  const [channelGroupExpanded, setChannelGroupExpanded] = useState(false);
  const [timezoneExpanded, setTimezoneExpanded] = useState(false);

  // Context menu management
  const {
    contextMenu,
    showContextMenu,
    hideContextMenu,
  } = useContextMenu<{ streamIds: number[] }>();

  // Dropdown state
  const [groupSearchFilter, setGroupSearchFilter] = useState('');
  const groupSearchInputRef = useRef<HTMLInputElement>(null);

  // Provider and group dropdown management
  const {
    isOpen: providerDropdownOpen,
    setIsOpen: setProviderDropdownOpen,
    dropdownRef: providerDropdownRef,
  } = useDropdown();

  const {
    isOpen: groupDropdownOpen,
    setIsOpen: setGroupDropdownOpen,
    dropdownRef: groupDropdownRef,
  } = useDropdown();

  // Clear group search filter when group dropdown closes
  useEffect(() => {
    if (!groupDropdownOpen) {
      setGroupSearchFilter('');
    }
  }, [groupDropdownOpen]);

  // Focus search input when group dropdown opens
  useEffect(() => {
    if (groupDropdownOpen && groupSearchInputRef.current) {
      groupSearchInputRef.current.focus();
    }
  }, [groupDropdownOpen]);

  // Determine if we're using multi-select mode
  const useMultiSelectProviders = !!onSelectedProvidersChange;
  const useMultiSelectGroups = !!onSelectedStreamGroupsChange;

  // Group and sort streams
  // Convert sorted stream groups to StreamGroup objects with expanded state
  const groupedStreams = useMemo((): StreamGroup[] => {
    return sortedStreamGroups.map(([name, groupStreams]) => ({
      name,
      streams: groupStreams,
      expanded: isGroupExpanded(name),
    }));
  }, [sortedStreamGroups, isGroupExpanded]);

  // Expand all groups (wrapper to pass group names)
  const expandAllGroups = useCallback(() => {
    expandAllGroupsInternal(groupedStreams.map(g => g.name));
  }, [groupedStreams, expandAllGroupsInternal]);

  // Check if all groups are expanded or collapsed
  const allExpanded = groupedStreams.length > 0 && expandedGroups.size === groupedStreams.length;
  const allCollapsed = expandedGroups.size === 0;

  // Clear selection when streams change (new search/filter)
  useEffect(() => {
    clearSelection();
  }, [searchTerm, providerFilter, groupFilter, clearSelection]);

  // Clear selection when exiting edit mode
  useEffect(() => {
    if (!isEditMode) {
      clearSelection();
      setSelectedGroupNames(new Set());
    }
  }, [isEditMode, clearSelection]);

  // Keyboard shortcuts management
  useKeyboardShortcuts({
    onSelectAll: selectAll,
    onClearSelection: clearSelection,
    contextMenu,
    onCloseContextMenu: hideContextMenu,
  });


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

  // Handle dragging a stream group header (for drop onto channels pane)
  // If multiple groups are selected and we drag one of them, drag all selected groups
  const handleGroupDragStart = useCallback(
    (e: React.DragEvent, group: StreamGroup) => {
      // Set data to identify this as a stream group drag
      e.dataTransfer.setData('streamGroupDrag', 'true');
      e.dataTransfer.effectAllowed = 'copy';

      // Check if the dragged group is part of a multi-group selection
      const isGroupSelected = selectedGroupNames.has(group.name);
      const hasMultipleGroupsSelected = selectedGroupNames.size > 1;

      if (isGroupSelected && hasMultipleGroupsSelected) {
        // Drag all selected groups
        const selectedGroupsList = groupedStreams.filter(g => selectedGroupNames.has(g.name));
        const allGroupNames = selectedGroupsList.map(g => g.name);
        const allStreamIds = selectedGroupsList.flatMap(g => g.streams.map(s => s.id));

        // Trigger lazy load for any groups that don't have streams loaded yet
        if (onGroupExpand) {
          selectedGroupsList.forEach(g => {
            if (g.streams.length === 0) {
              onGroupExpand(g.name);
            }
          });
        }

        e.dataTransfer.setData('streamGroupNames', JSON.stringify(allGroupNames));
        e.dataTransfer.setData('streamGroupStreamIds', JSON.stringify(allStreamIds));

        // Custom drag image showing multi-group info
        const dragEl = document.createElement('div');
        dragEl.className = 'drag-preview';
        const totalStreams = selectedGroupsList.reduce((sum, g) => sum + g.streams.length, 0);
        const hasUnloadedGroups = selectedGroupsList.some(g => g.streams.length === 0);
        // Show "Loading..." if any groups haven't had their streams loaded yet
        const streamCountText = hasUnloadedGroups ? 'Loading...' : `${totalStreams} streams`;
        dragEl.textContent = `${selectedGroupsList.length} groups (${streamCountText})`;
        dragEl.style.cssText = `
          position: absolute;
          top: -1000px;
          background: #a855f7;
          color: white;
          padding: 8px 16px;
          border-radius: 4px;
          font-weight: 500;
        `;
        document.body.appendChild(dragEl);
        e.dataTransfer.setDragImage(dragEl, 50, 20);
        setTimeout(() => document.body.removeChild(dragEl), 0);
      } else {
        // Single group drag
        e.dataTransfer.setData('streamGroupName', group.name);
        e.dataTransfer.setData('streamGroupStreamIds', JSON.stringify(group.streams.map(s => s.id)));

        // Custom drag image showing group info
        const dragEl = document.createElement('div');
        dragEl.className = 'drag-preview';
        // Show "Loading..." if streams haven't been loaded yet
        const streamCountText = group.streams.length === 0 ? 'Loading...' : `${group.streams.length} streams`;
        dragEl.textContent = `${group.name} (${streamCountText})`;
        dragEl.style.cssText = `
          position: absolute;
          top: -1000px;
          background: #22d3ee;
          color: #1e1e1e;
          padding: 8px 16px;
          border-radius: 4px;
          font-weight: 500;
        `;
        document.body.appendChild(dragEl);
        e.dataTransfer.setDragImage(dragEl, 50, 20);
        setTimeout(() => document.body.removeChild(dragEl), 0);
      }
    },
    [selectedGroupNames, groupedStreams, onGroupExpand]
  );

  // Bulk create handlers - apply settings defaults
  const openBulkCreateModal = useCallback((group: StreamGroup, startingNumber?: number | null) => {
    setBulkCreateGroup(group);
    setBulkCreateStreams([]);
    setBulkCreateStartingNumber(startingNumber != null ? startingNumber.toString() : '');
    setBulkCreateGroupOption('same');
    setBulkCreateSelectedGroupId(null);
    setBulkCreateNewGroupName('');
    // Apply settings defaults
    setBulkCreateTimezone((channelDefaults?.timezonePreference as TimezonePreference) || 'both');
    setBulkCreateStripCountry(channelDefaults?.removeCountryPrefix ?? false);
    setBulkCreateKeepCountry(channelDefaults?.includeCountryInName ?? false);
    setBulkCreateCountrySeparator((channelDefaults?.countrySeparator as NumberSeparator) || '|');
    setBulkCreateAddNumber(channelDefaults?.includeChannelNumberInName ?? false);
    setBulkCreateSeparator((channelDefaults?.channelNumberSeparator as NumberSeparator) || '|');
    setBulkCreatePrefixOrder('number-first'); // Default to number first
    setBulkCreateStripNetwork(false); // Default to not stripping network prefixes
    // Apply default channel profile from settings
    setBulkCreateSelectedProfiles(
      channelDefaults?.defaultChannelProfileId ? new Set([channelDefaults.defaultChannelProfileId]) : new Set()
    );
    setNamingOptionsExpanded(false); // Collapse naming options
    setChannelGroupExpanded(false); // Collapse channel group options
    setTimezoneExpanded(false); // Collapse timezone options
    setBulkCreateNormalizationSettings(channelDefaults?.normalizationSettings ?? { disabledBuiltinTags: [], customTags: [] });
    setBulkCreateModalOpen(true);
  }, [channelDefaults]);

  const openBulkCreateModalForSelection = useCallback(() => {
    // Get selected streams in order
    const selectedStreamsList = streams.filter(s => selectedIds.has(s.id));
    setBulkCreateGroup(null);
    setBulkCreateStreams(selectedStreamsList);
    setBulkCreateStartingNumber('');
    setBulkCreateGroupOption('existing'); // Default to existing group for selections
    setBulkCreateSelectedGroupId(null);
    setBulkCreateNewGroupName('');
    // Apply settings defaults
    setBulkCreateTimezone((channelDefaults?.timezonePreference as TimezonePreference) || 'both');
    setBulkCreateStripCountry(channelDefaults?.removeCountryPrefix ?? false);
    setBulkCreateKeepCountry(channelDefaults?.includeCountryInName ?? false);
    setBulkCreateCountrySeparator((channelDefaults?.countrySeparator as NumberSeparator) || '|');
    setBulkCreateAddNumber(channelDefaults?.includeChannelNumberInName ?? false);
    setBulkCreateSeparator((channelDefaults?.channelNumberSeparator as NumberSeparator) || '|');
    setBulkCreatePrefixOrder('number-first'); // Default to number first
    setBulkCreateStripNetwork(false); // Default to not stripping network prefixes
    // Apply default channel profile from settings
    setBulkCreateSelectedProfiles(
      channelDefaults?.defaultChannelProfileId ? new Set([channelDefaults.defaultChannelProfileId]) : new Set()
    );
    setNamingOptionsExpanded(false); // Collapse naming options
    setChannelGroupExpanded(false); // Collapse channel group options
    setTimezoneExpanded(false); // Collapse timezone options
    setBulkCreateNormalizationSettings(channelDefaults?.normalizationSettings ?? { disabledBuiltinTags: [], customTags: [] });
    setBulkCreateModalOpen(true);
  }, [streams, selectedIds, channelDefaults]);

  // Open bulk create modal for specific stream IDs (from external trigger)
  // Optionally accepts target group ID and starting number to pre-fill the modal
  const openBulkCreateModalForStreamIds = useCallback((
    streamIds: number[],
    targetGroupId?: number | null,
    startingNumber?: number | null
  ) => {
    const streamsList = streams.filter(s => streamIds.includes(s.id));
    if (streamsList.length === 0) return;

    setBulkCreateGroup(null);
    setBulkCreateStreams(streamsList);
    // Pre-fill starting number if provided
    setBulkCreateStartingNumber(startingNumber != null ? startingNumber.toString() : '');
    // Pre-select group if provided
    if (targetGroupId != null) {
      setBulkCreateGroupOption('existing');
      setBulkCreateSelectedGroupId(targetGroupId);
    } else {
      setBulkCreateGroupOption('existing');
      setBulkCreateSelectedGroupId(null);
    }
    setBulkCreateNewGroupName('');
    // Apply settings defaults
    setBulkCreateTimezone((channelDefaults?.timezonePreference as TimezonePreference) || 'both');
    setBulkCreateStripCountry(channelDefaults?.removeCountryPrefix ?? false);
    setBulkCreateKeepCountry(channelDefaults?.includeCountryInName ?? false);
    setBulkCreateCountrySeparator((channelDefaults?.countrySeparator as NumberSeparator) || '|');
    setBulkCreateAddNumber(channelDefaults?.includeChannelNumberInName ?? false);
    setBulkCreateSeparator((channelDefaults?.channelNumberSeparator as NumberSeparator) || '|');
    setBulkCreatePrefixOrder('number-first');
    setBulkCreateStripNetwork(false);
    // Apply default channel profile from settings
    setBulkCreateSelectedProfiles(
      channelDefaults?.defaultChannelProfileId ? new Set([channelDefaults.defaultChannelProfileId]) : new Set()
    );
    setNamingOptionsExpanded(false);
    setChannelGroupExpanded(false);
    setTimezoneExpanded(false);
    setBulkCreateNormalizationSettings(channelDefaults?.normalizationSettings ?? { disabledBuiltinTags: [], customTags: [] });
    setBulkCreateModalOpen(true);
  }, [streams, channelDefaults]);

  const closeBulkCreateModal = useCallback(() => {
    setBulkCreateModalOpen(false);
    setBulkCreateGroup(null);
    setBulkCreateGroups([]);
    setBulkCreateStreams([]);
    setBulkCreateCustomGroupNames(new Map());
    setBulkCreateGroupStartNumbers(new Map());
    setBulkCreateSelectedProfiles(new Set());
  }, []);

  // Open bulk create modal for manual entry (no streams pre-selected)
  const openBulkCreateModalForManualEntry = useCallback((
    targetGroupId?: number | null,
    startingNumber?: number | null
  ) => {
    setBulkCreateGroup(null);
    setBulkCreateGroups([]);
    setBulkCreateStreams([]);
    // Pre-fill starting number if provided
    setBulkCreateStartingNumber(startingNumber != null ? startingNumber.toString() : '');
    // Pre-select group if provided
    if (targetGroupId != null) {
      setBulkCreateGroupOption('existing');
      setBulkCreateSelectedGroupId(targetGroupId);
    } else {
      setBulkCreateGroupOption('existing');
      setBulkCreateSelectedGroupId(null);
    }
    setBulkCreateNewGroupName('');
    // Apply settings defaults
    setBulkCreateTimezone((channelDefaults?.timezonePreference as TimezonePreference) || 'both');
    setBulkCreateStripCountry(channelDefaults?.removeCountryPrefix ?? false);
    setBulkCreateKeepCountry(channelDefaults?.includeCountryInName ?? false);
    setBulkCreateCountrySeparator((channelDefaults?.countrySeparator as NumberSeparator) || '|');
    setBulkCreateAddNumber(channelDefaults?.includeChannelNumberInName ?? false);
    setBulkCreateSeparator((channelDefaults?.channelNumberSeparator as NumberSeparator) || '|');
    setBulkCreatePrefixOrder('number-first');
    setBulkCreateStripNetwork(false);
    setBulkCreateStripSuffix(false);
    // Apply default channel profile from settings
    setBulkCreateSelectedProfiles(
      channelDefaults?.defaultChannelProfileId ? new Set([channelDefaults.defaultChannelProfileId]) : new Set()
    );
    setNamingOptionsExpanded(false);
    setChannelGroupExpanded(false);
    setTimezoneExpanded(false);
    setBulkCreateNormalizationSettings(channelDefaults?.normalizationSettings ?? { disabledBuiltinTags: [], customTags: [] });
    setBulkCreateModalOpen(true);
  }, [channelDefaults]);

  // Context menu handlers
  const closeContextMenu = useCallback(() => hideContextMenu(), [hideContextMenu]);

  const handleContextMenu = useCallback((stream: Stream, e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (!isEditMode) return;

    // If right-clicked stream is not selected, select only it
    let streamIds: number[];
    if (!isSelected(stream.id)) {
      clearSelection();
      toggleSelect(stream.id);
      streamIds = [stream.id];
    } else {
      streamIds = Array.from(selectedIds);
    }

    showContextMenu(e.clientX, e.clientY, { streamIds });
  }, [isEditMode, isSelected, clearSelection, toggleSelect, selectedIds]);

  // Handler for "Create channel(s) in existing group" from context menu
  const handleCreateInGroup = useCallback((groupId: number) => {
    if (!contextMenu) return;
    openBulkCreateModalForStreamIds(contextMenu.metadata.streamIds, groupId);
    closeContextMenu();
  }, [contextMenu, openBulkCreateModalForStreamIds, closeContextMenu]);

  // Handler for "Create channel(s) in new group" from context menu
  const handleCreateInNewGroup = useCallback(() => {
    if (!contextMenu) return;
    const streamsList = streams.filter(s => contextMenu.metadata.streamIds.includes(s.id));
    setBulkCreateGroup(null);
    setBulkCreateGroups([]);
    setBulkCreateStreams(streamsList);
    setBulkCreateStartingNumber('');
    setBulkCreateGroupOption('new');
    setBulkCreateSelectedGroupId(null);
    setBulkCreateNewGroupName('');
    // Apply settings defaults
    setBulkCreateTimezone((channelDefaults?.timezonePreference as TimezonePreference) || 'both');
    setBulkCreateStripCountry(channelDefaults?.removeCountryPrefix ?? false);
    setBulkCreateKeepCountry(channelDefaults?.includeCountryInName ?? false);
    setBulkCreateCountrySeparator((channelDefaults?.countrySeparator as NumberSeparator) || '|');
    setBulkCreateAddNumber(channelDefaults?.includeChannelNumberInName ?? false);
    setBulkCreateSeparator((channelDefaults?.channelNumberSeparator as NumberSeparator) || '|');
    setBulkCreatePrefixOrder('number-first');
    setBulkCreateStripNetwork(false);
    // Apply default channel profile from settings
    setBulkCreateSelectedProfiles(
      channelDefaults?.defaultChannelProfileId ? new Set([channelDefaults.defaultChannelProfileId]) : new Set()
    );
    setNamingOptionsExpanded(false);
    setChannelGroupExpanded(true); // Expand channel group section so user sees the "new group" option
    setTimezoneExpanded(false);
    setBulkCreateNormalizationSettings(channelDefaults?.normalizationSettings ?? { disabledBuiltinTags: [], customTags: [] });
    setBulkCreateModalOpen(true);
    closeContextMenu();
  }, [contextMenu, streams, channelDefaults, closeContextMenu]);

  // Handler for right-clicking on a stream group header
  const handleGroupContextMenu = useCallback((group: StreamGroup, e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (!isEditMode) return;

    // Get all stream IDs in this group
    const streamIds = group.streams.map(s => s.id);

    showContextMenu(e.clientX, e.clientY, { streamIds });
  }, [isEditMode]);

  // Toggle group selection (select/deselect all streams in group)
  const toggleGroupSelection = useCallback((group: StreamGroup) => {
    const groupStreamIds = group.streams.map(s => s.id);
    const allSelected = groupStreamIds.every(id => selectedIds.has(id));

    if (allSelected) {
      // Deselect all streams in this group
      deselectMultiple(groupStreamIds);
      setSelectedGroupNames(prev => {
        const next = new Set(prev);
        next.delete(group.name);
        return next;
      });
    } else {
      // Select all streams in this group
      selectMultiple(groupStreamIds);
      setSelectedGroupNames(prev => {
        const next = new Set(prev);
        next.add(group.name);
        return next;
      });
    }
  }, [selectedIds, selectMultiple, deselectMultiple]);

  // Check if all streams in a group are selected
  const isGroupFullySelected = useCallback((group: StreamGroup): boolean => {
    if (group.streams.length === 0) return false;
    return group.streams.every(s => selectedIds.has(s.id));
  }, [selectedIds]);

  // Check if some but not all streams in a group are selected
  const isGroupPartiallySelected = useCallback((group: StreamGroup): boolean => {
    if (group.streams.length === 0) return false;
    const selectedCount = group.streams.filter(s => selectedIds.has(s.id)).length;
    return selectedCount > 0 && selectedCount < group.streams.length;
  }, [selectedIds]);

  // Open bulk create modal for multiple selected groups
  const openBulkCreateModalForGroups = useCallback(() => {
    // Get all groups that have at least one stream selected
    // AND filter each group to only include the streams that are actually selected
    const selectedGroups = groupedStreams
      .map(group => ({
        ...group,
        streams: group.streams.filter(s => selectedIds.has(s.id))
      }))
      .filter(group => group.streams.length > 0);

    setBulkCreateGroup(null);
    setBulkCreateGroups(selectedGroups);
    setBulkCreateStreams([]);
    setBulkCreateMultiGroupOption('separate'); // Default to separate groups
    // Initialize custom group names with the original names
    const initialNames = new Map<string, string>();
    selectedGroups.forEach(g => initialNames.set(g.name, g.name));
    setBulkCreateCustomGroupNames(initialNames);
    // Initialize per-group start numbers (empty by default)
    setBulkCreateGroupStartNumbers(new Map());
    setBulkCreateStartingNumber('');
    setBulkCreateGroupOption('same'); // Default to same name for multi-group
    setBulkCreateSelectedGroupId(null);
    setBulkCreateNewGroupName('');
    // Apply settings defaults
    setBulkCreateTimezone((channelDefaults?.timezonePreference as TimezonePreference) || 'both');
    setBulkCreateStripCountry(channelDefaults?.removeCountryPrefix ?? false);
    setBulkCreateKeepCountry(channelDefaults?.includeCountryInName ?? false);
    setBulkCreateCountrySeparator((channelDefaults?.countrySeparator as NumberSeparator) || '|');
    setBulkCreateAddNumber(channelDefaults?.includeChannelNumberInName ?? false);
    setBulkCreateSeparator((channelDefaults?.channelNumberSeparator as NumberSeparator) || '|');
    setBulkCreatePrefixOrder('number-first');
    setBulkCreateStripNetwork(false);
    // Apply default channel profile from settings
    setBulkCreateSelectedProfiles(
      channelDefaults?.defaultChannelProfileId ? new Set([channelDefaults.defaultChannelProfileId]) : new Set()
    );
    setNamingOptionsExpanded(false);
    setChannelGroupExpanded(false);
    setTimezoneExpanded(false);
    setBulkCreateNormalizationSettings(channelDefaults?.normalizationSettings ?? { disabledBuiltinTags: [], customTags: [] });
    setBulkCreateModalOpen(true);
  }, [groupedStreams, selectedIds, channelDefaults]);

  // Open bulk create modal for explicitly provided groups (used by external trigger)
  const openBulkCreateModalForMultipleGroups = useCallback((groups: StreamGroup[], startingNumber?: number | null) => {
    setBulkCreateGroup(null);
    setBulkCreateGroups(groups);
    setBulkCreateStreams([]);
    setBulkCreateMultiGroupOption('separate'); // Default to separate groups
    // Initialize custom group names with the original names
    const initialNames = new Map<string, string>();
    groups.forEach(g => initialNames.set(g.name, g.name));
    setBulkCreateCustomGroupNames(initialNames);
    // Initialize per-group start numbers (empty by default)
    setBulkCreateGroupStartNumbers(new Map());
    setBulkCreateStartingNumber(startingNumber != null ? startingNumber.toString() : '');
    setBulkCreateGroupOption('same'); // Default to same name for multi-group
    setBulkCreateSelectedGroupId(null);
    setBulkCreateNewGroupName('');
    // Apply settings defaults
    setBulkCreateTimezone((channelDefaults?.timezonePreference as TimezonePreference) || 'both');
    setBulkCreateStripCountry(channelDefaults?.removeCountryPrefix ?? false);
    setBulkCreateKeepCountry(channelDefaults?.includeCountryInName ?? false);
    setBulkCreateCountrySeparator((channelDefaults?.countrySeparator as NumberSeparator) || '|');
    setBulkCreateAddNumber(channelDefaults?.includeChannelNumberInName ?? false);
    setBulkCreateSeparator((channelDefaults?.channelNumberSeparator as NumberSeparator) || '|');
    setBulkCreatePrefixOrder('number-first');
    setBulkCreateStripNetwork(false);
    // Apply default channel profile from settings
    setBulkCreateSelectedProfiles(
      channelDefaults?.defaultChannelProfileId ? new Set([channelDefaults.defaultChannelProfileId]) : new Set()
    );
    setNamingOptionsExpanded(false);
    setChannelGroupExpanded(false);
    setTimezoneExpanded(false);
    setBulkCreateNormalizationSettings(channelDefaults?.normalizationSettings ?? { disabledBuiltinTags: [], customTags: [] });
    setBulkCreateModalOpen(true);
  }, [channelDefaults]);

  // Handle external trigger to open bulk create modal (from dropping stream groups on channels pane)
  // Supports single or multiple groups
  useEffect(() => {
    if (externalTriggerGroupNames && externalTriggerGroupNames.length > 0 && onBulkCreateFromGroup) {
      if (externalTriggerGroupNames.length === 1) {
        // Single group - use single group modal
        const matchingGroup = groupedStreams.find(g => g.name === externalTriggerGroupNames[0]);
        if (matchingGroup) {
          openBulkCreateModal(matchingGroup, externalTriggerStartingNumber);
        }
      } else {
        // Multiple groups - use multi-group modal
        const matchingGroups = groupedStreams.filter(g => externalTriggerGroupNames.includes(g.name));
        if (matchingGroups.length > 0) {
          openBulkCreateModalForMultipleGroups(matchingGroups, externalTriggerStartingNumber);
        }
      }
      // Signal that we've handled the trigger
      onExternalTriggerHandled?.();
    }
  }, [externalTriggerGroupNames, externalTriggerStartingNumber, groupedStreams, openBulkCreateModal, openBulkCreateModalForMultipleGroups, onBulkCreateFromGroup, onExternalTriggerHandled]);

  // Handle external trigger to open bulk create modal for specific stream IDs
  useEffect(() => {
    if (externalTriggerStreamIds && externalTriggerStreamIds.length > 0 && onBulkCreateFromGroup) {
      openBulkCreateModalForStreamIds(
        externalTriggerStreamIds,
        externalTriggerTargetGroupId,
        externalTriggerStartingNumber
      );
      // Signal that we've handled the trigger
      onExternalTriggerHandled?.();
    }
  }, [externalTriggerStreamIds, externalTriggerTargetGroupId, externalTriggerStartingNumber, openBulkCreateModalForStreamIds, onBulkCreateFromGroup, onExternalTriggerHandled]);

  // Handle external trigger to open bulk create modal for manual entry (no streams)
  useEffect(() => {
    if (externalTriggerManualEntry && onBulkCreateFromGroup) {
      openBulkCreateModalForManualEntry(
        externalTriggerTargetGroupId,
        externalTriggerStartingNumber
      );
      // Signal that we've handled the trigger
      onExternalTriggerHandled?.();
    }
  }, [externalTriggerManualEntry, externalTriggerTargetGroupId, externalTriggerStartingNumber, openBulkCreateModalForManualEntry, onBulkCreateFromGroup, onExternalTriggerHandled]);

  // Get the streams to create channels from (either from single group, multiple groups, or selection)
  const streamsToCreate = useMemo(() => {
    if (bulkCreateGroup) {
      return bulkCreateGroup.streams;
    }
    if (bulkCreateGroups.length > 0) {
      // Flatten all streams from all selected groups
      return bulkCreateGroups.flatMap(g => g.streams);
    }
    return bulkCreateStreams;
  }, [bulkCreateGroup, bulkCreateGroups, bulkCreateStreams]);

  const isFromGroup = !!bulkCreateGroup;
  const isFromMultipleGroups = bulkCreateGroups.length > 0;

  // Detect if streams have regional variants (East/West)
  const hasRegionalVariants = useMemo(() => {
    return detectRegionalVariants(streamsToCreate);
  }, [streamsToCreate]);

  // Detect if streams have country prefixes (US, UK, CA, etc.)
  const hasCountryPrefixes = useMemo(() => {
    return detectCountryPrefixes(streamsToCreate);
  }, [streamsToCreate]);

  // Detect if streams have network prefixes (CHAMP, PPV, etc. + custom)
  const hasNetworkPrefixes = useMemo(() => {
    return detectNetworkPrefixes(streamsToCreate, channelDefaults?.customNetworkPrefixes);
  }, [streamsToCreate, channelDefaults?.customNetworkPrefixes]);

  // Detect if streams have network suffixes (ENGLISH, LIVE, BACKUP, etc. + custom)
  const hasNetworkSuffixes = useMemo(() => {
    return detectNetworkSuffixes(streamsToCreate, channelDefaults?.customNetworkSuffixes);
  }, [streamsToCreate, channelDefaults?.customNetworkSuffixes]);

  // Get unique country prefixes for display
  const uniqueCountryPrefixes = useMemo(() => {
    return getUniqueCountryPrefixes(streamsToCreate);
  }, [streamsToCreate]);

  // Compute unique stream names and duplicate count for the modal display
  // Uses normalized names to match quality variants (e.g., "Sports Channel" and "Sports Channel FHD" become one channel)
  // Also applies timezone filtering when a preference is selected
  const bulkCreateStats = useMemo(() => {
    // Filter streams based on timezone preference first
    const filteredStreams = filterStreamsByTimezone(streamsToCreate, bulkCreateTimezone);

    // Build normalize options
    const normalizeOptions: NormalizeOptions = {
      timezonePreference: bulkCreateTimezone,
      stripCountryPrefix: bulkCreateStripCountry,
      keepCountryPrefix: bulkCreateKeepCountry,
      countrySeparator: bulkCreateCountrySeparator,
      stripNetworkPrefix: bulkCreateStripNetwork,
      customNetworkPrefixes: channelDefaults?.customNetworkPrefixes,
      stripNetworkSuffix: bulkCreateStripSuffix,
      customNetworkSuffixes: channelDefaults?.customNetworkSuffixes,
      normalizationSettings: bulkCreateNormalizationSettings,
    };

    const unsortedStreamsByNormalizedName = new Map<string, Stream[]>();
    for (const stream of filteredStreams) {
      const normalizedName = normalizeStreamName(stream.name, normalizeOptions);
      const existing = unsortedStreamsByNormalizedName.get(normalizedName);
      if (existing) {
        existing.push(stream);
      } else {
        unsortedStreamsByNormalizedName.set(normalizedName, [stream]);
      }
    }

    // Sort entries using natural sort (same logic as App.tsx handleBulkCreateFromGroup)
    // This ensures preview matches actual creation order
    const sortedEntries = Array.from(unsortedStreamsByNormalizedName.entries()).sort((a, b) => {
      const nameA = a[0];
      const nameB = b[0];

      // Extract base name and trailing number (if any)
      const matchA = nameA.match(/^(.+?)(\s*\d+)?$/);
      const matchB = nameB.match(/^(.+?)(\s*\d+)?$/);

      const baseA = matchA?.[1]?.trim() || nameA;
      const baseB = matchB?.[1]?.trim() || nameB;
      const numA = matchA?.[2] ? parseInt(matchA[2].trim(), 10) : 0;
      const numB = matchB?.[2] ? parseInt(matchB[2].trim(), 10) : 0;

      // First compare base names
      const baseCompare = baseA.localeCompare(baseB, undefined, { sensitivity: 'base' });
      if (baseCompare !== 0) return baseCompare;

      // If base names are equal, sort by number (0 = no number, comes first)
      return numA - numB;
    });

    // Rebuild Map in sorted order
    const streamsByNormalizedName = new Map<string, Stream[]>(sortedEntries);

    const uniqueCount = streamsByNormalizedName.size;
    const duplicateCount = filteredStreams.length - uniqueCount;
    const hasDuplicates = duplicateCount > 0;
    const excludedCount = streamsToCreate.length - filteredStreams.length;
    return { uniqueCount, duplicateCount, hasDuplicates, streamsByNormalizedName, excludedCount };
  }, [streamsToCreate, bulkCreateTimezone, bulkCreateStripCountry, bulkCreateKeepCountry, bulkCreateCountrySeparator, bulkCreateStripNetwork, bulkCreateStripSuffix, bulkCreateNormalizationSettings]);

  // Actually perform the bulk create with the specified pushDown option
  // startingNumberOverride: optionally override the starting number (used by "insert at end" option)
  const doBulkCreate = useCallback(async (pushDown: boolean, startingNumberOverride?: number) => {
    if (streamsToCreate.length === 0 || !onBulkCreateFromGroup) return;

    const useSeparateMode = isFromMultipleGroups && bulkCreateMultiGroupOption === 'separate';

    setBulkCreateLoading(true);
    setBulkCreateShowConflict(false);

    try {
      // Handle multi-group mode with separate groups
      if (useSeparateMode) {
        // Create channels for each group separately, using per-group starting numbers
        let currentNumber = 0; // Will be set by first group's start number
        for (let i = 0; i < bulkCreateGroups.length; i++) {
          const group = bulkCreateGroups[i];
          // Get per-group start number (or continue from previous)
          const groupStartStr = bulkCreateGroupStartNumbers.get(group.name);
          if (groupStartStr && !isNaN(parseInt(groupStartStr, 10))) {
            currentNumber = parseInt(groupStartStr, 10);
          }
          // Get custom group name (user may have renamed it)
          const customGroupName = bulkCreateCustomGroupNames.get(group.name) || group.name;
          // Find existing group with the custom name, or create new
          const existingGroup = channelGroups.find(g => g.name === customGroupName);
          const groupId = existingGroup?.id ?? null;
          const newGroupName = existingGroup ? undefined : customGroupName;

          await onBulkCreateFromGroup(
            group.streams,
            currentNumber,
            groupId,
            newGroupName,
            bulkCreateTimezone,
            bulkCreateStripCountry,
            bulkCreateAddNumber,
            bulkCreateSeparator,
            bulkCreateKeepCountry,
            bulkCreateCountrySeparator,
            bulkCreatePrefixOrder,
            bulkCreateStripNetwork,
            channelDefaults?.customNetworkPrefixes,
            bulkCreateStripSuffix,
            channelDefaults?.customNetworkSuffixes,
            bulkCreateSelectedProfiles.size > 0 ? Array.from(bulkCreateSelectedProfiles) : undefined,
            pushDown
          );

          // Increment starting number for next group (if no explicit start)
          currentNumber += group.streams.length;
        }
      } else {
        // Single group or combined mode
        // Use parseFloat to support decimal channel numbers (e.g., 38.1, 38.2)
        // If startingNumberOverride is provided (from "insert at end" option), use that instead
        const startingNum = startingNumberOverride !== undefined ? startingNumberOverride : parseFloat(bulkCreateStartingNumber);
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
          newGroupName,
          bulkCreateTimezone,
          bulkCreateStripCountry,
          bulkCreateAddNumber,
          bulkCreateSeparator,
          bulkCreateKeepCountry,
          bulkCreateCountrySeparator,
          bulkCreatePrefixOrder,
          bulkCreateStripNetwork,
          channelDefaults?.customNetworkPrefixes,
          bulkCreateStripSuffix,
          channelDefaults?.customNetworkSuffixes,
          bulkCreateSelectedProfiles.size > 0 ? Array.from(bulkCreateSelectedProfiles) : undefined,
          pushDown
        );
      }

      // Clear selection after successful creation
      if (!isFromGroup) {
        clearSelection();
        setSelectedGroupNames(new Set());
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
    isFromMultipleGroups,
    bulkCreateGroup,
    bulkCreateGroups,
    bulkCreateMultiGroupOption,
    bulkCreateCustomGroupNames,
    bulkCreateGroupStartNumbers,
    bulkCreateStartingNumber,
    bulkCreateGroupOption,
    bulkCreateSelectedGroupId,
    bulkCreateNewGroupName,
    bulkCreateTimezone,
    bulkCreateStripCountry,
    bulkCreateKeepCountry,
    bulkCreateCountrySeparator,
    bulkCreateAddNumber,
    bulkCreateSeparator,
    bulkCreatePrefixOrder,
    bulkCreateStripNetwork,
    bulkCreateStripSuffix,
    bulkCreateSelectedProfiles,
    channelGroups,
    onBulkCreateFromGroup,
    clearSelection,
    closeBulkCreateModal,
  ]);

  // Check for conflicts and show dialog, or proceed directly if no conflicts
  const handleBulkCreate = useCallback(async () => {
    if (streamsToCreate.length === 0 || !onBulkCreateFromGroup) return;

    // For separate groups mode, we use per-group starting numbers
    // For other modes, we need a valid global starting number
    const useSeparateMode = isFromMultipleGroups && bulkCreateMultiGroupOption === 'separate';

    if (useSeparateMode) {
      // Check that at least the first group has a starting number
      const firstGroupStart = bulkCreateGroupStartNumbers.get(bulkCreateGroups[0]?.name);
      if (!firstGroupStart || isNaN(parseFloat(firstGroupStart)) || parseFloat(firstGroupStart) < 0) {
        alert('Please enter a valid starting channel number for the first group');
        return;
      }
    } else {
      const startingNum = parseFloat(bulkCreateStartingNumber);
      if (isNaN(startingNum) || startingNum < 0) {
        alert('Please enter a valid starting channel number');
        return;
      }
    }

    // Check for conflicts before proceeding (use floor for conflict check since it checks integer ranges)
    if (onCheckConflicts && !useSeparateMode) {
      const startingNum = Math.floor(parseFloat(bulkCreateStartingNumber));
      const conflictCount = onCheckConflicts(startingNum, bulkCreateStats.uniqueCount);
      if (conflictCount > 0) {
        // Calculate end-of-sequence number (highest existing + 1)
        const highestNumber = onGetHighestChannelNumber ? onGetHighestChannelNumber() : 0;
        setBulkCreateEndOfSequenceNumber(highestNumber + 1);
        // Show conflict dialog
        setBulkCreateConflictCount(conflictCount);
        setBulkCreateShowConflict(true);
        return;
      }
    }

    // No conflicts or separate mode - proceed with creation
    await doBulkCreate(false);
  }, [
    streamsToCreate,
    isFromMultipleGroups,
    bulkCreateMultiGroupOption,
    bulkCreateGroupStartNumbers,
    bulkCreateGroups,
    bulkCreateStartingNumber,
    bulkCreateStats.uniqueCount,
    onBulkCreateFromGroup,
    onCheckConflicts,
    onGetHighestChannelNumber,
    doBulkCreate,
  ]);

  // Handle copying stream URL to clipboard
  const handleCopyStreamUrl = async (url: string, streamName: string) => {
    await handleCopy(url, `stream URL for "${streamName}"`);
  };

  return (
    <div className="streams-pane">
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

      <div className="pane-header">
        <h2>
          Streams
          {onRefreshStreams && (
            <button
              className="refresh-streams-btn"
              onClick={onRefreshStreams}
              title="Refresh streams from Dispatcharr"
              disabled={loading}
            >
              <span className={`material-icons${loading ? ' spinning' : ''}`}>sync</span>
            </button>
          )}
        </h2>
        {selectedCount > 0 && (
          <div className="selection-info">
            <span className="selection-count">
              {selectedCount} stream{selectedCount !== 1 ? 's' : ''}
              {selectedGroupNames.size > 0 && ` (${selectedGroupNames.size} group${selectedGroupNames.size !== 1 ? 's' : ''})`}
            </span>
            {isEditMode && onBulkCreateFromGroup && (
              <button
                className="create-channels-btn"
                onClick={() => {
                  if (selectedGroupNames.size > 1) {
                    // Multiple groups selected - use multi-group modal
                    openBulkCreateModalForGroups();
                  } else if (selectedGroupNames.size === 1) {
                    // Single group selected - filter to only selected streams
                    const groupName = Array.from(selectedGroupNames)[0];
                    const group = groupedStreams.find(g => g.name === groupName);
                    if (group) {
                      // Create a filtered group with only selected streams
                      const filteredGroup = {
                        ...group,
                        streams: group.streams.filter(s => selectedIds.has(s.id))
                      };
                      openBulkCreateModal(filteredGroup);
                    } else {
                      openBulkCreateModalForSelection();
                    }
                  } else {
                    // Individual streams selected (not grouped) - use selection modal
                    openBulkCreateModalForSelection();
                  }
                }}
                title={selectedGroupNames.size > 1 ? 'Create channels from selected groups' : selectedGroupNames.size === 1 ? 'Create channels from selected group' : 'Create channels from selected streams'}
              >
                <span className="material-icons">playlist_add</span>
                Create
              </button>
            )}
            <button className="clear-selection-btn" onClick={() => {
              clearSelection();
              setSelectedGroupNames(new Set());
            }}>
              Clear
            </button>
          </div>
        )}
      </div>

      <div className="streams-pane-filters">
        <div className="search-row">
          <div className="search-input-wrapper">
            <input
              type="text"
              placeholder="Search streams..."
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
          <div className="expand-collapse-buttons">
            <button
              className="expand-collapse-btn"
              onClick={expandAllGroups}
              disabled={allExpanded || groupedStreams.length === 0}
              title="Expand all groups"
            >
              <span className="material-icons">unfold_more</span>
            </button>
            <button
              className="expand-collapse-btn"
              onClick={collapseAllGroups}
              disabled={allCollapsed || groupedStreams.length === 0}
              title="Collapse all groups"
            >
              <span className="material-icons">unfold_less</span>
            </button>
          </div>
          {mappedStreamIds && mappedStreamIds.size > 0 && (
            <button
              className={`hide-mapped-btn ${hideMappedStreams ? 'active' : ''}`}
              onClick={() => setHideMappedStreams(!hideMappedStreams)}
              title={hideMappedStreams ? 'Show all streams' : 'Hide streams already mapped to channels'}
            >
              <span className="material-icons">{hideMappedStreams ? 'visibility_off' : 'visibility'}</span>
              <span className="hide-mapped-label">{hideMappedStreams ? 'Mapped hidden' : 'Hide mapped'}</span>
            </button>
          )}
        </div>
        <div className="streams-filter-row">
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
                  <div className="filter-dropdown-search">
                    <span className="material-icons search-icon">search</span>
                    <input
                      ref={groupSearchInputRef}
                      type="text"
                      placeholder="Search groups..."
                      value={groupSearchFilter}
                      onChange={(e) => setGroupSearchFilter(e.target.value)}
                      onClick={(e) => e.stopPropagation()}
                    />
                    {groupSearchFilter && (
                      <button
                        className="clear-search"
                        onClick={(e) => {
                          e.stopPropagation();
                          setGroupSearchFilter('');
                          groupSearchInputRef.current?.focus();
                        }}
                      >
                        <span className="material-icons">close</span>
                      </button>
                    )}
                  </div>
                  <div className="filter-dropdown-actions">
                    <button
                      className="filter-dropdown-action"
                      onClick={() => {
                        // Select all visible (filtered) groups
                        const filteredGroups = streamGroups.filter(g =>
                          g.toLowerCase().includes(groupSearchFilter.toLowerCase())
                        );
                        const newSelection = [...new Set([...selectedStreamGroups, ...filteredGroups])];
                        onSelectedStreamGroupsChange!(newSelection);
                      }}
                    >
                      Select All{groupSearchFilter ? ' Visible' : ''}
                    </button>
                    <button
                      className="filter-dropdown-action"
                      onClick={() => {
                        if (groupSearchFilter) {
                          // Clear only visible (filtered) groups
                          const filteredGroups = streamGroups.filter(g =>
                            g.toLowerCase().includes(groupSearchFilter.toLowerCase())
                          );
                          onSelectedStreamGroupsChange!(
                            selectedStreamGroups.filter(g => !filteredGroups.includes(g))
                          );
                        } else {
                          onSelectedStreamGroupsChange!([]);
                        }
                      }}
                    >
                      Clear{groupSearchFilter ? ' Visible' : ' All'}
                    </button>
                  </div>
                  <div className="filter-dropdown-options">
                    {streamGroups
                      .filter(group => group.toLowerCase().includes(groupSearchFilter.toLowerCase()))
                      .map((group) => (
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
                    {streamGroups.filter(group => group.toLowerCase().includes(groupSearchFilter.toLowerCase())).length === 0 && (
                      <div className="filter-dropdown-empty">No groups match "{groupSearchFilter}"</div>
                    )}
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

          {/* Clear Filters Button - show when any filter is active */}
          {onClearStreamFilters && (selectedProviders.length > 0 || selectedStreamGroups.length > 0) && (
            <button
              className="clear-filters-btn"
              onClick={onClearStreamFilters}
              title="Clear all filters"
            >
              <span className="material-icons">filter_alt_off</span>
            </button>
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
                <div key={group.name} className={`stream-group ${isGroupFullySelected(group) && isEditMode ? 'group-selected' : ''}`}>
                  <div
                    className="stream-group-header"
                    onClick={() => {
                      // If group is being expanded (not currently expanded) and we have a callback, trigger lazy load
                      if (!isGroupExpanded(group.name) && onGroupExpand) {
                        onGroupExpand(group.name);
                      }
                      toggleGroup(group.name);
                    }}
                    onContextMenu={(e) => handleGroupContextMenu(group, e)}
                  >
                    {isEditMode && onBulkCreateFromGroup && (
                      <span
                        className="group-drag-handle"
                        title="Drag to Channels pane to bulk create"
                        draggable={true}
                        onDragStart={(e) => {
                          e.stopPropagation();
                          // Trigger lazy load for this group if streams not yet loaded
                          // This ensures streams are available when the drop completes
                          if (group.streams.length === 0 && onGroupExpand) {
                            onGroupExpand(group.name);
                          }
                          handleGroupDragStart(e, group);
                        }}
                      >
                        <span className="material-icons">drag_indicator</span>
                      </span>
                    )}
                    {isEditMode && onBulkCreateFromGroup && (
                      <span
                        className="group-selection-checkbox"
                        onClick={(e) => {
                          e.stopPropagation();
                          e.preventDefault();
                          toggleGroupSelection(group);
                        }}
                        onPointerDown={(e) => e.stopPropagation()}
                        onMouseDown={(e) => e.stopPropagation()}
                        onTouchStart={(e) => e.stopPropagation()}
                        draggable={false}
                        title={isGroupFullySelected(group) ? 'Deselect all streams in group' : 'Select all streams in group'}
                      >
                        <span className="material-icons">
                          {isGroupFullySelected(group)
                            ? 'check_box'
                            : isGroupPartiallySelected(group)
                              ? 'indeterminate_check_box'
                              : 'check_box_outline_blank'}
                        </span>
                      </span>
                    )}
                    <span className="expand-icon">{group.expanded ? '▼︎' : '▶︎'}</span>
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
                          className={`stream-item ${isSelected(stream.id) && isEditMode ? 'selected' : ''} ${isEditMode ? 'edit-mode' : ''}`}
                          onClick={(e) => {
                            // In edit mode, clicking the row does nothing (use checkbox to select)
                            // Outside edit mode, clicking the row does nothing either
                            e.stopPropagation();
                          }}
                          onContextMenu={(e) => handleContextMenu(stream, e)}
                        >
                          {/* Drag handle - only in edit mode, positioned first like channel groups */}
                          {isEditMode && (
                            <span
                              className="drag-handle"
                              draggable={true}
                              onDragStart={(e) => handleDragStart(e, stream)}
                            >
                              ⋮⋮
                            </span>
                          )}
                          {isEditMode && (
                            <span
                              className="selection-checkbox"
                              onClick={(e) => {
                                e.stopPropagation();
                                e.preventDefault();
                                toggleSelect(stream.id);
                              }}
                              onPointerDown={(e) => e.stopPropagation()}
                              onMouseDown={(e) => e.stopPropagation()}
                              onTouchStart={(e) => e.stopPropagation()}
                              draggable={false}
                            >
                              <span className="material-icons">
                                {isSelected(stream.id) ? 'check_box' : 'check_box_outline_blank'}
                              </span>
                            </span>
                          )}
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
                            {showStreamUrls && stream.url && (
                              <span className="stream-url" title={stream.url}>
                                {stream.url}
                              </span>
                            )}
                            {stream.m3u_account && (
                              <span className="stream-provider">
                                {providers.find((p) => p.id === stream.m3u_account)?.name || 'Unknown'}
                              </span>
                            )}
                          </div>
                          {stream.url && (
                            <>
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
                              <button
                                className="copy-url-btn"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleCopyStreamUrl(stream.url!, stream.name);
                                }}
                                title="Copy stream URL"
                              >
                                <span className="material-icons">content_copy</span>
                              </button>
                            </>
                          )}
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

      {/* Context Menu */}
      {contextMenu && (
        <div
          className="streams-context-menu"
          style={{
            position: 'fixed',
            left: contextMenu.x,
            top: contextMenu.y,
            zIndex: 10000,
          }}
        >
          <div
            className="streams-context-menu-item"
            onClick={(e) => {
              e.stopPropagation();
              // Create submenu with channel groups
              const submenu = document.createElement('div');
              submenu.className = 'streams-context-submenu';
              submenu.style.cssText = `position:fixed;left:${contextMenu.x + 220}px;top:${contextMenu.y}px;z-index:10001;`;

              // Add channel groups as options (only enabled/visible groups)
              const enabledGroups = channelGroups.filter(group => selectedChannelGroups.includes(group.id));
              enabledGroups.forEach(group => {
                const option = document.createElement('div');
                option.className = 'streams-context-menu-item';
                option.textContent = group.name;
                option.onclick = () => {
                  handleCreateInGroup(group.id);
                  if (document.body.contains(submenu)) {
                    document.body.removeChild(submenu);
                  }
                };
                submenu.appendChild(option);
              });

              // Add "no groups" message if empty
              if (enabledGroups.length === 0) {
                const noGroups = document.createElement('div');
                noGroups.className = 'streams-context-menu-item disabled';
                noGroups.textContent = 'No enabled channel groups';
                submenu.appendChild(noGroups);
              }

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
              const closeSubmenu = (evt: MouseEvent) => {
                if (!submenu.contains(evt.target as Node)) {
                  if (document.body.contains(submenu)) {
                    document.body.removeChild(submenu);
                  }
                  document.removeEventListener('mousedown', closeSubmenu);
                }
              };
              setTimeout(() => document.addEventListener('mousedown', closeSubmenu), 0);
            }}
          >
            Create channel(s) in group <span className="streams-context-menu-arrow">▶</span>
          </div>
          <div className="streams-context-menu-item" onClick={handleCreateInNewGroup}>
            Create channel(s) in new group
          </div>
        </div>
      )}

      {/* Bulk Create Modal */}
      {bulkCreateModalOpen && streamsToCreate.length > 0 && (
        <div className="modal-overlay">
          <div className="bulk-create-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>
                {isFromGroup
                  ? `Create Channels from "${bulkCreateGroup!.name}"`
                  : isFromMultipleGroups
                    ? `Create Channels from ${bulkCreateGroups.length} Groups`
                    : `Create Channels from ${streamsToCreate.length} Selected Streams`
                }
              </h3>
              <button className="modal-close-btn" onClick={closeBulkCreateModal}>
                <span className="material-icons">close</span>
              </button>
            </div>

            <div className="modal-body">
              {/* Multi-group option - only show when multiple groups selected */}
              {isFromMultipleGroups && (
                <div className="form-group multi-group-option">
                  <div className="multi-group-info">
                    <span className="material-icons">folder_copy</span>
                    <span>
                      <strong>{bulkCreateGroups.length}</strong> groups selected: {bulkCreateGroups.map(g => g.name).join(', ')}
                    </span>
                  </div>
                  <label className="form-label">Channel Group Creation</label>
                  <div className="radio-group">
                    <label className="radio-option">
                      <input
                        type="radio"
                        name="multiGroupOption"
                        checked={bulkCreateMultiGroupOption === 'separate'}
                        onChange={() => setBulkCreateMultiGroupOption('separate')}
                      />
                      <span>Create separate channel groups</span>
                      <span className="option-hint">Each M3U group becomes its own channel group</span>
                    </label>
                    <label className="radio-option">
                      <input
                        type="radio"
                        name="multiGroupOption"
                        checked={bulkCreateMultiGroupOption === 'single'}
                        onChange={() => setBulkCreateMultiGroupOption('single')}
                      />
                      <span>Combine into single channel group</span>
                      <span className="option-hint">All streams go into one channel group</span>
                    </label>
                  </div>

                  {/* Per-group settings when separate mode is selected */}
                  {bulkCreateMultiGroupOption === 'separate' && (
                    <div className="multi-group-names">
                      <label className="form-label">Channel Groups</label>
                      <div className="group-name-list-header">
                        <span className="header-streams">Streams</span>
                        <span className="header-name">Group Name</span>
                        <span className="header-start">Start #</span>
                        <span className="header-status">Status</span>
                      </div>
                      <div className="group-name-list">
                        {bulkCreateGroups.map((group) => {
                          const customName = bulkCreateCustomGroupNames.get(group.name) || group.name;
                          const startNumber = bulkCreateGroupStartNumbers.get(group.name) || '';
                          const existingGroup = channelGroups.find(g => g.name === customName);
                          return (
                            <div key={group.name} className="group-name-row">
                              <span className="group-stream-count">{group.streams.length}</span>
                              <input
                                type="text"
                                value={customName}
                                onChange={(e) => {
                                  const newMap = new Map(bulkCreateCustomGroupNames);
                                  newMap.set(group.name, e.target.value);
                                  setBulkCreateCustomGroupNames(newMap);
                                }}
                                placeholder={group.name}
                                className="form-input group-name-input"
                              />
                              <input
                                type="number"
                                min="0"
                                value={startNumber}
                                onChange={(e) => {
                                  const newMap = new Map(bulkCreateGroupStartNumbers);
                                  newMap.set(group.name, e.target.value);
                                  setBulkCreateGroupStartNumbers(newMap);
                                }}
                                placeholder="Auto"
                                className="form-input group-start-input"
                                title="Starting channel number for this group"
                              />
                              {existingGroup ? (
                                <span className="group-exists-badge" title="Group already exists - channels will be added to it">exists</span>
                              ) : (
                                <span className="group-new-badge" title="New group will be created">new</span>
                              )}
                            </div>
                          );
                        })}
                      </div>
                      <div className="group-start-hint">
                        <span className="material-icons">info_outline</span>
                        Leave start # empty to continue from previous group's last channel
                      </div>
                    </div>
                  )}
                </div>
              )}

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

              {/* Starting Channel Number - hide when multi-group with separate mode (per-group numbers used instead) */}
              {!(isFromMultipleGroups && bulkCreateMultiGroupOption === 'separate') && (
                <div className="form-group">
                  <label>Starting Channel Number</label>
                  <input
                    type="number"
                    min="0"
                    step="any"
                    value={bulkCreateStartingNumber}
                    onChange={(e) => setBulkCreateStartingNumber(e.target.value)}
                    placeholder="e.g., 100 or 38.1"
                    className="form-input"
                    autoFocus
                  />
                  {bulkCreateStartingNumber && !isNaN(parseFloat(bulkCreateStartingNumber)) && (
                    <div className="number-range-preview">
                      {(() => {
                        const startNum = parseFloat(bulkCreateStartingNumber);
                        const hasDecimal = bulkCreateStartingNumber.includes('.');
                        const increment = hasDecimal ? 0.1 : 1;
                        const endNum = startNum + (bulkCreateStats.uniqueCount - 1) * increment;
                        // Format end number to match decimal places of start
                        const endNumStr = hasDecimal ? endNum.toFixed(1) : Math.floor(endNum).toString();
                        return `Channels ${bulkCreateStartingNumber} - ${endNumStr}`;
                      })()}
                    </div>
                  )}
                </div>
              )}

              {/* Channel Group - Collapsible Section */}
              {/* Hide when multi-group with separate option is selected */}
              {!(isFromMultipleGroups && bulkCreateMultiGroupOption === 'separate') && (
              <div className="form-group collapsible-section">
                <div
                  className="collapsible-header"
                  onClick={() => setChannelGroupExpanded(!channelGroupExpanded)}
                >
                  <span className="expand-icon">{channelGroupExpanded ? '▼︎' : '▶︎'}</span>
                  <span className="collapsible-title">Channel Group</span>
                  <span className="collapsible-summary">
                    {(() => {
                      if (bulkCreateGroupOption === 'same' && bulkCreateGroup) {
                        return `"${bulkCreateGroup.name}"`;
                      } else if (bulkCreateGroupOption === 'existing' && bulkCreateSelectedGroupId) {
                        const group = channelGroups.find(g => g.id === bulkCreateSelectedGroupId);
                        return group ? `"${group.name}"` : 'Select group';
                      } else if (bulkCreateGroupOption === 'new' && bulkCreateNewGroupName) {
                        return `New: "${bulkCreateNewGroupName}"`;
                      } else if (bulkCreateGroupOption === 'new') {
                        return 'New group';
                      } else if (bulkCreateGroupOption === 'existing') {
                        return 'Select group';
                      }
                      return 'Same as stream group';
                    })()}
                  </span>
                </div>

                {channelGroupExpanded && (
                  <div className="collapsible-content">
                    <div className="radio-group">
                      {/* Only show "same name" option when creating from a single group */}
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
                        <div className="searchable-dropdown" ref={bulkCreateGroupDropdownRef}>
                          <div
                            className="dropdown-trigger"
                            onClick={() => setBulkCreateGroupDropdownOpen(!bulkCreateGroupDropdownOpen)}
                          >
                            <span className="dropdown-value">
                              {bulkCreateSelectedGroupId
                                ? channelGroups.find(g => g.id === bulkCreateSelectedGroupId)?.name ?? '-- Select a group --'
                                : '-- Select a group --'}
                            </span>
                            <span className="material-icons dropdown-arrow">
                              {bulkCreateGroupDropdownOpen ? 'expand_less' : 'expand_more'}
                            </span>
                          </div>
                          {bulkCreateGroupDropdownOpen && (
                            <div className="dropdown-menu">
                              <div className="dropdown-search">
                                <span className="material-icons">search</span>
                                <input
                                  type="text"
                                  placeholder="Search groups..."
                                  value={bulkCreateGroupSearch}
                                  onChange={(e) => setBulkCreateGroupSearch(e.target.value)}
                                  onClick={(e) => e.stopPropagation()}
                                  autoFocus
                                />
                                {bulkCreateGroupSearch && (
                                  <button
                                    className="clear-search"
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      setBulkCreateGroupSearch('');
                                    }}
                                  >
                                    <span className="material-icons">close</span>
                                  </button>
                                )}
                              </div>
                              <div className="dropdown-options">
                                {channelGroups
                                  .filter(g => !bulkCreateGroupSearch || g.name.toLowerCase().includes(bulkCreateGroupSearch.toLowerCase()))
                                  .map((g) => (
                                    <div
                                      key={g.id}
                                      className={`dropdown-option ${bulkCreateSelectedGroupId === g.id ? 'selected' : ''}`}
                                      onClick={() => {
                                        setBulkCreateSelectedGroupId(g.id);
                                        setBulkCreateGroupDropdownOpen(false);
                                        setBulkCreateGroupSearch('');
                                      }}
                                    >
                                      {g.name}
                                    </div>
                                  ))}
                                {channelGroups.filter(g => !bulkCreateGroupSearch || g.name.toLowerCase().includes(bulkCreateGroupSearch.toLowerCase())).length === 0 && (
                                  <div className="dropdown-no-results">No groups found</div>
                                )}
                              </div>
                            </div>
                          )}
                        </div>
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
                )}
              </div>
              )}

              {/* Timezone preference - Collapsible, only show if regional variants detected */}
              {hasRegionalVariants && (
                <div className="form-group collapsible-section">
                  <div
                    className="collapsible-header"
                    onClick={() => setTimezoneExpanded(!timezoneExpanded)}
                  >
                    <span className="expand-icon">{timezoneExpanded ? '▼︎' : '▶︎'}</span>
                    <span className="collapsible-title">Timezone Preference</span>
                    <span className="collapsible-summary">
                      {bulkCreateTimezone === 'east' ? 'East Coast' : bulkCreateTimezone === 'west' ? 'West Coast' : 'Keep Both'}
                      {channelDefaults?.timezonePreference && channelDefaults.timezonePreference !== 'both' && ' (from settings)'}
                      {bulkCreateStats.excludedCount > 0 && ` (${bulkCreateStats.excludedCount} excluded)`}
                    </span>
                  </div>

                  {timezoneExpanded && (
                    <div className="collapsible-content">
                      <div className="timezone-info">
                        <span className="material-icons">schedule</span>
                        <span>Some channels have East/West variants (e.g., Movies Channel, Movies Channel West)</span>
                      </div>
                      <div className="radio-group">
                        <label className="radio-option">
                          <input
                            type="radio"
                            name="timezoneOption"
                            checked={bulkCreateTimezone === 'east'}
                            onChange={() => setBulkCreateTimezone('east')}
                          />
                          <span>East Coast</span>
                          <span className="timezone-hint">Use East feeds, skip West variants</span>
                        </label>
                        <label className="radio-option">
                          <input
                            type="radio"
                            name="timezoneOption"
                            checked={bulkCreateTimezone === 'west'}
                            onChange={() => setBulkCreateTimezone('west')}
                          />
                          <span>West Coast</span>
                          <span className="timezone-hint">Use West feeds only</span>
                        </label>
                        <label className="radio-option">
                          <input
                            type="radio"
                            name="timezoneOption"
                            checked={bulkCreateTimezone === 'both'}
                            onChange={() => setBulkCreateTimezone('both')}
                          />
                          <span>Keep Both</span>
                          <span className="timezone-hint">Create separate East and West channels</span>
                        </label>
                      </div>
                      {bulkCreateStats.excludedCount > 0 && (
                        <div className="timezone-excluded">
                          {bulkCreateStats.excludedCount} stream{bulkCreateStats.excludedCount !== 1 ? 's' : ''} excluded based on timezone preference
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}

              {/* Normalization - Collapsible Section */}
              <div className="form-group naming-options-section">
                <div
                  className="naming-options-header"
                  onClick={() => setNamingOptionsExpanded(!namingOptionsExpanded)}
                >
                  <span className="expand-icon">{namingOptionsExpanded ? '▼︎' : '▶︎'}</span>
                  <span className="naming-options-title">Normalization</span>
                  <span className="naming-options-summary">
                    {(() => {
                      const options: string[] = [];
                      // Tag normalization
                      const disabledTagCount = bulkCreateNormalizationSettings.disabledBuiltinTags.length;
                      const customTagCount = bulkCreateNormalizationSettings.customTags.length;
                      if (disabledTagCount > 0 || customTagCount > 0) {
                        const tagParts: string[] = [];
                        if (disabledTagCount > 0) tagParts.push(`${disabledTagCount} tags disabled`);
                        if (customTagCount > 0) tagParts.push(`${customTagCount} custom`);
                        options.push(tagParts.join(', '));
                      }
                      // Other normalization options
                      if (bulkCreateStripNetwork) options.push('Strip prefix');
                      if (bulkCreateStripSuffix) options.push('Strip suffix');
                      if (bulkCreateStripCountry) options.push('Remove country');
                      if (bulkCreateKeepCountry) options.push(`Keep country (${bulkCreateCountrySeparator})`);
                      if (bulkCreateAddNumber) options.push(`Add numbers (${bulkCreateSeparator})`);
                      const hasDefaults = channelDefaults && (
                        channelDefaults.removeCountryPrefix ||
                        channelDefaults.includeChannelNumberInName
                      );
                      if (options.length > 0) {
                        return hasDefaults ? `${options.join(', ')} (from settings)` : options.join(', ');
                      }
                      return 'Default';
                    })()}
                  </span>
                </div>

                {namingOptionsExpanded && (
                  <div className="naming-options-content">
                    {/* Tag-Based Normalization */}
                    <div className="naming-option-group">
                      <QuickTagManager
                        settings={bulkCreateNormalizationSettings}
                        onChange={setBulkCreateNormalizationSettings}
                      />
                    </div>

                    {/* Network prefix option - only show if network prefixes detected */}
                    {hasNetworkPrefixes && (
                      <div className="naming-option-group">
                        <label className="checkbox-option">
                          <input
                            type="checkbox"
                            checked={bulkCreateStripNetwork}
                            onChange={(e) => setBulkCreateStripNetwork(e.target.checked)}
                          />
                          <span>Strip network prefixes</span>
                        </label>
                        <span className="option-hint">e.g., "CHAMP | Queens Park Rangers" → "Queens Park Rangers"</span>
                      </div>
                    )}

                    {/* Network suffix option - only show if network suffixes detected */}
                    {hasNetworkSuffixes && (
                      <div className="naming-option-group">
                        <label className="checkbox-option">
                          <input
                            type="checkbox"
                            checked={bulkCreateStripSuffix}
                            onChange={(e) => setBulkCreateStripSuffix(e.target.checked)}
                          />
                          <span>Strip network suffixes</span>
                        </label>
                        <span className="option-hint">e.g., "ESPN (ENGLISH)" → "ESPN"</span>
                      </div>
                    )}

                    {/* Country prefix option - only show if country prefixes detected */}
                    {hasCountryPrefixes && (
                      <div className="naming-option-group">
                        <div className="country-prefix-info">
                          <span className="material-icons">public</span>
                          <span>Country prefixes detected: {uniqueCountryPrefixes.slice(0, 5).join(', ')}{uniqueCountryPrefixes.length > 5 ? ', ...' : ''}</span>
                        </div>
                        <div className="radio-group country-prefix-options">
                          <label className="radio-option">
                            <input
                              type="radio"
                              name="countryPrefixOption"
                              checked={!bulkCreateStripCountry && !bulkCreateKeepCountry}
                              onChange={() => {
                                setBulkCreateStripCountry(false);
                                setBulkCreateKeepCountry(false);
                              }}
                            />
                            <span>Keep as-is</span>
                          </label>
                          <span className="option-hint radio-hint">e.g., "US: Sports Channel" stays "US: Sports Channel"</span>

                          <label className="radio-option">
                            <input
                              type="radio"
                              name="countryPrefixOption"
                              checked={bulkCreateStripCountry && !bulkCreateKeepCountry}
                              onChange={() => {
                                setBulkCreateStripCountry(true);
                                setBulkCreateKeepCountry(false);
                              }}
                            />
                            <span>Remove country prefix</span>
                          </label>
                          <span className="option-hint radio-hint">e.g., "US: Sports Channel" → "Sports Channel"</span>

                          <label className="radio-option">
                            <input
                              type="radio"
                              name="countryPrefixOption"
                              checked={bulkCreateKeepCountry}
                              onChange={() => {
                                setBulkCreateStripCountry(false);
                                setBulkCreateKeepCountry(true);
                              }}
                            />
                            <span>Keep country prefix (normalized)</span>
                          </label>
                          {bulkCreateKeepCountry && (
                            <>
                              <div className="separator-options country-separator">
                                <span className="separator-label">Separator:</span>
                                <button
                                  type="button"
                                  className={`separator-btn ${bulkCreateCountrySeparator === '-' ? 'active' : ''}`}
                                  onClick={() => setBulkCreateCountrySeparator('-')}
                                >
                                  -
                                </button>
                                <button
                                  type="button"
                                  className={`separator-btn ${bulkCreateCountrySeparator === ':' ? 'active' : ''}`}
                                  onClick={() => setBulkCreateCountrySeparator(':')}
                                >
                                  :
                                </button>
                                <button
                                  type="button"
                                  className={`separator-btn ${bulkCreateCountrySeparator === '|' ? 'active' : ''}`}
                                  onClick={() => setBulkCreateCountrySeparator('|')}
                                >
                                  |
                                </button>
                              </div>
                              <span className="option-hint radio-hint">e.g., "US: Sports Channel" → "US {bulkCreateCountrySeparator} Sports Channel"</span>
                            </>
                          )}
                        </div>
                      </div>
                    )}

                    {/* Channel number prefix option */}
                    <div className="naming-option-group">
                      <label className="checkbox-option">
                        <input
                          type="checkbox"
                          checked={bulkCreateAddNumber}
                          onChange={(e) => setBulkCreateAddNumber(e.target.checked)}
                        />
                        <span>Add channel number to name</span>
                      </label>
                      {bulkCreateAddNumber && (
                        <>
                          <div className="separator-options">
                            <span className="separator-label">Separator:</span>
                            <button
                              type="button"
                              className={`separator-btn ${bulkCreateSeparator === '-' ? 'active' : ''}`}
                              onClick={() => setBulkCreateSeparator('-')}
                            >
                              -
                            </button>
                            <button
                              type="button"
                              className={`separator-btn ${bulkCreateSeparator === ':' ? 'active' : ''}`}
                              onClick={() => setBulkCreateSeparator(':')}
                            >
                              :
                            </button>
                            <button
                              type="button"
                              className={`separator-btn ${bulkCreateSeparator === '|' ? 'active' : ''}`}
                              onClick={() => setBulkCreateSeparator('|')}
                            >
                              |
                            </button>
                          </div>
                          <span className="option-hint">e.g., "100 {bulkCreateSeparator} Sports Channel"</span>
                        </>
                      )}
                    </div>

                    {/* Prefix order option - only show when both country and number are enabled */}
                    {bulkCreateKeepCountry && bulkCreateAddNumber && (
                      <div className="naming-option-group prefix-order-group">
                        <div className="prefix-order-label">Prefix Order:</div>
                        <div className="prefix-order-options">
                          <label className="radio-option">
                            <input
                              type="radio"
                              name="prefixOrder"
                              checked={bulkCreatePrefixOrder === 'number-first'}
                              onChange={() => setBulkCreatePrefixOrder('number-first')}
                            />
                            <span>Number first</span>
                          </label>
                          <span className="option-hint radio-hint">e.g., "100 {bulkCreateSeparator} US {bulkCreateCountrySeparator} Sports Channel"</span>
                          <label className="radio-option">
                            <input
                              type="radio"
                              name="prefixOrder"
                              checked={bulkCreatePrefixOrder === 'country-first'}
                              onChange={() => setBulkCreatePrefixOrder('country-first')}
                            />
                            <span>Country first</span>
                          </label>
                          <span className="option-hint radio-hint">e.g., "US {bulkCreateCountrySeparator} 100 {bulkCreateSeparator} Sports Channel"</span>
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* Channel Profiles - Collapsible Section */}
              {channelProfiles.length > 0 && (
                <div className="form-group collapsible-section">
                  <div
                    className="collapsible-header"
                    onClick={() => setProfilesExpanded(!profilesExpanded)}
                  >
                    <span className="expand-icon">{profilesExpanded ? '▼︎' : '▶︎'}</span>
                    <span className="collapsible-title">Channel Profiles</span>
                    <span className="collapsible-summary">
                      {bulkCreateSelectedProfiles.size === 0
                        ? 'None selected'
                        : `${bulkCreateSelectedProfiles.size} profile${bulkCreateSelectedProfiles.size !== 1 ? 's' : ''} selected`}
                    </span>
                  </div>

                  {profilesExpanded && (
                    <div className="collapsible-content">
                      <div className="profiles-info">
                        <span className="material-icons">people</span>
                        <span>Assign new channels to these profiles (optional)</span>
                      </div>
                      <div className="checkbox-group profiles-list">
                        {channelProfiles.map(profile => (
                          <label key={profile.id} className="checkbox-option">
                            <input
                              type="checkbox"
                              checked={bulkCreateSelectedProfiles.has(profile.id)}
                              onChange={(e) => {
                                const newSet = new Set(bulkCreateSelectedProfiles);
                                if (e.target.checked) {
                                  newSet.add(profile.id);
                                } else {
                                  newSet.delete(profile.id);
                                }
                                setBulkCreateSelectedProfiles(newSet);
                              }}
                            />
                            <span>{profile.name}</span>
                            <span className="profile-channel-count">
                              ({profile.channels.length > 0 ? profile.channels.length : 'all'} channels)
                            </span>
                          </label>
                        ))}
                      </div>
                      {bulkCreateSelectedProfiles.size > 0 && (
                        <button
                          className="btn-clear-profiles"
                          onClick={() => setBulkCreateSelectedProfiles(new Set())}
                        >
                          Clear selection
                        </button>
                      )}
                    </div>
                  )}
                </div>
              )}

              {/* Preview - show per-group preview in separate mode, otherwise show combined preview */}
              {isFromMultipleGroups && bulkCreateMultiGroupOption === 'separate' ? (
                <div className="bulk-create-preview">
                  <label>Preview (first 3 channels per group)</label>
                  <div className="preview-list">
                    {bulkCreateGroups.map((group, groupIdx) => {
                      const groupStartStr = bulkCreateGroupStartNumbers.get(group.name);
                      // Calculate starting number: use explicit value, or continue from previous group
                      let startNum: number | null = null;
                      if (groupStartStr && !isNaN(parseInt(groupStartStr, 10))) {
                        startNum = parseInt(groupStartStr, 10);
                      } else if (groupIdx > 0) {
                        // Find the previous group's start and add its stream count
                        let prevEnd = 0;
                        for (let i = 0; i < groupIdx; i++) {
                          const prevStartStr = bulkCreateGroupStartNumbers.get(bulkCreateGroups[i].name);
                          if (prevStartStr && !isNaN(parseInt(prevStartStr, 10))) {
                            prevEnd = parseInt(prevStartStr, 10) + bulkCreateGroups[i].streams.length;
                          } else if (i === 0) {
                            prevEnd = bulkCreateGroups[i].streams.length;
                          } else {
                            prevEnd += bulkCreateGroups[i].streams.length;
                          }
                        }
                        startNum = prevEnd;
                      }
                      const customName = bulkCreateCustomGroupNames.get(group.name) || group.name;
                      return (
                        <div key={group.name} className="preview-group">
                          <div className="preview-group-header">{customName}</div>
                          {group.streams.slice(0, 3).map((stream, idx) => {
                            const num = startNum !== null ? startNum + idx : '?';
                            return (
                              <div key={stream.id} className="preview-item">
                                <span className="preview-number">{num}</span>
                                <span className="preview-name">{stream.name}</span>
                              </div>
                            );
                          })}
                          {group.streams.length > 3 && (
                            <div className="preview-more">... and {group.streams.length - 3} more</div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              ) : (
                <div className="bulk-create-preview">
                  <label>Preview (first 10 channels)</label>
                  <div className="preview-list">
                    {Array.from(bulkCreateStats.streamsByNormalizedName.entries()).slice(0, 10).map(([normalizedName, groupedStreams], idx) => {
                      // Support decimal channel numbers (e.g., 38.1, 38.2, 38.3)
                      let num: string | number = '?';
                      if (bulkCreateStartingNumber) {
                        const startNum = parseFloat(bulkCreateStartingNumber);
                        if (!isNaN(startNum)) {
                          const hasDecimal = bulkCreateStartingNumber.includes('.');
                          const increment = hasDecimal ? 0.1 : 1;
                          const channelNum = startNum + idx * increment;
                          num = hasDecimal ? channelNum.toFixed(1) : Math.floor(channelNum);
                        }
                      }
                      // Build display name based on options and prefix order
                      let displayName = normalizedName;
                      if (bulkCreateAddNumber && bulkCreateKeepCountry) {
                        // Both enabled - extract country from normalized name and apply order
                        const countryMatch = normalizedName.match(new RegExp(`^([A-Z]{2,6})\\s*[${bulkCreateCountrySeparator}]\\s*(.+)$`));
                        if (countryMatch) {
                          const [, countryCode, baseName] = countryMatch;
                          if (bulkCreatePrefixOrder === 'country-first') {
                            displayName = `${countryCode} ${bulkCreateCountrySeparator} ${num} ${bulkCreateSeparator} ${baseName}`;
                          } else {
                            displayName = `${num} ${bulkCreateSeparator} ${countryCode} ${bulkCreateCountrySeparator} ${baseName}`;
                          }
                        } else {
                          displayName = `${num} ${bulkCreateSeparator} ${normalizedName}`;
                        }
                      } else if (bulkCreateAddNumber) {
                        displayName = `${num} ${bulkCreateSeparator} ${normalizedName}`;
                      }
                      return (
                        <div key={normalizedName} className="preview-item">
                          <span className="preview-number">{num}</span>
                          <span className="preview-name">{displayName}</span>
                          {groupedStreams.length > 1 && (
                            <span className="preview-stream-count" title={groupedStreams.map(s => s.name).join('\n')}>
                              {groupedStreams.length} streams
                            </span>
                          )}
                        </div>
                      );
                    })}
                    {bulkCreateStats.streamsByNormalizedName.size > 10 && (
                      <div className="preview-more">
                        ... and {bulkCreateStats.streamsByNormalizedName.size - 10} more channels
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>

            <div className="modal-footer">
              <button className="btn-cancel" onClick={closeBulkCreateModal}>
                Cancel
              </button>
              <button
                className="btn-create"
                onClick={handleBulkCreate}
                disabled={bulkCreateLoading || (
                  // In separate groups mode, check first group has a start number
                  isFromMultipleGroups && bulkCreateMultiGroupOption === 'separate'
                    ? !bulkCreateGroupStartNumbers.get(bulkCreateGroups[0]?.name)
                    : !bulkCreateStartingNumber
                )}
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

      {/* Bulk Create Conflict Dialog */}
      {bulkCreateShowConflict && (
        <div className="modal-overlay">
          <div className="modal-content conflict-dialog" onClick={(e) => e.stopPropagation()}>
            <h3>Channel Number Conflict</h3>
            <div className="conflict-message">
              <p>
                <strong>{bulkCreateConflictCount}</strong> existing channel{bulkCreateConflictCount !== 1 ? 's' : ''} would
                conflict with the new channels (starting at <strong>{bulkCreateStartingNumber}</strong>).
              </p>
              <p>How would you like to proceed?</p>
            </div>
            <div className="conflict-options">
              <button
                className="conflict-option-btn push-down"
                onClick={() => doBulkCreate(true)}
                disabled={bulkCreateLoading}
              >
                <span className="material-icons">vertical_align_bottom</span>
                <div className="conflict-option-text">
                  <strong>Push channels down</strong>
                  <span>Insert at {bulkCreateStartingNumber} and shift existing channels by {bulkCreateStats.uniqueCount}</span>
                </div>
              </button>
              <button
                className="conflict-option-btn insert-at-end"
                onClick={() => doBulkCreate(false, bulkCreateEndOfSequenceNumber)}
                disabled={bulkCreateLoading}
              >
                <span className="material-icons">last_page</span>
                <div className="conflict-option-text">
                  <strong>Insert at end</strong>
                  <span>Start at channel {bulkCreateEndOfSequenceNumber} (after all existing channels)</span>
                </div>
              </button>
              <button
                className="conflict-option-btn add-to-end"
                onClick={() => doBulkCreate(false)}
                disabled={bulkCreateLoading}
              >
                <span className="material-icons">warning</span>
                <div className="conflict-option-text">
                  <strong>Create anyway</strong>
                  <span>Create with duplicate channel numbers (not recommended)</span>
                </div>
              </button>
            </div>
            <div className="modal-actions">
              <button
                className="modal-btn cancel"
                onClick={() => setBulkCreateShowConflict(false)}
                disabled={bulkCreateLoading}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
