import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import type { Stream, M3UAccount, ChannelGroup, ChannelProfile } from '../types';
import { useSelection } from '../hooks';
import { normalizeStreamName, detectRegionalVariants, filterStreamsByTimezone, detectCountryPrefixes, getUniqueCountryPrefixes, detectNetworkPrefixes, type TimezonePreference, type NormalizeOptions, type NumberSeparator, type PrefixOrder } from '../services/api';
import { naturalCompare } from '../utils/naturalSort';
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
  channelProfiles?: ChannelProfile[];
  channelDefaults?: ChannelDefaults;
  // External trigger to open bulk create modal for stream groups (set by dropping on channels pane)
  // Supports multiple groups being dropped at once
  externalTriggerGroupNames?: string[] | null;
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
    profileIds?: number[]
  ) => Promise<void>;
  // Appearance settings
  showStreamUrls?: boolean;
  // Refresh streams (bypasses cache)
  onRefreshStreams?: () => void;
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
  channelProfiles = [],
  channelDefaults,
  externalTriggerGroupNames = null,
  onExternalTriggerHandled,
  onBulkCreateFromGroup,
  showStreamUrls = true,
  onRefreshStreams,
}: StreamsPaneProps) {
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());

  // Compute streams in display order (grouped and sorted alphabetically)
  // This must be computed before useSelection so shift-click works correctly
  const displayOrderStreams = useMemo((): Stream[] => {
    const groups = new Map<string, Stream[]>();

    // Group streams by channel_group_name
    streams.forEach((stream) => {
      const groupName = stream.channel_group_name || 'Ungrouped';
      if (!groups.has(groupName)) {
        groups.set(groupName, []);
      }
      groups.get(groupName)!.push(stream);
    });

    // Sort streams within each group alphabetically with natural sort
    groups.forEach((groupStreams) => {
      groupStreams.sort((a, b) => naturalCompare(a.name, b.name));
    });

    // Sort groups alphabetically with natural sort (Ungrouped at end) and flatten
    const sortedGroupNames = Array.from(groups.keys()).sort((a, b) => {
      if (a === 'Ungrouped') return 1;
      if (b === 'Ungrouped') return -1;
      return naturalCompare(a, b);
    });

    // Flatten to single array in display order
    const result: Stream[] = [];
    for (const groupName of sortedGroupNames) {
      result.push(...groups.get(groupName)!);
    }
    return result;
  }, [streams]);

  // Use display order for selection so shift-click works correctly
  const {
    selectedIds,
    selectedCount,
    handleSelect,
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
  const [bulkCreateTimezone, setBulkCreateTimezone] = useState<TimezonePreference>('both');
  const [bulkCreateStripCountry, setBulkCreateStripCountry] = useState(false);
  const [bulkCreateKeepCountry, setBulkCreateKeepCountry] = useState(false);
  const [bulkCreateCountrySeparator, setBulkCreateCountrySeparator] = useState<NumberSeparator>('|');
  const [bulkCreateAddNumber, setBulkCreateAddNumber] = useState(false);
  const [bulkCreateSeparator, setBulkCreateSeparator] = useState<NumberSeparator>('|');
  const [bulkCreatePrefixOrder, setBulkCreatePrefixOrder] = useState<PrefixOrder>('number-first');
  const [bulkCreateStripNetwork, setBulkCreateStripNetwork] = useState(false);
  const [bulkCreateSelectedProfiles, setBulkCreateSelectedProfiles] = useState<Set<number>>(new Set());
  const [profilesExpanded, setProfilesExpanded] = useState(false);
  const [namingOptionsExpanded, setNamingOptionsExpanded] = useState(false);
  const [channelGroupExpanded, setChannelGroupExpanded] = useState(false);
  const [timezoneExpanded, setTimezoneExpanded] = useState(false);

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

    // Sort streams within each group alphabetically with natural sort
    groups.forEach((groupStreams) => {
      groupStreams.sort((a, b) => naturalCompare(a.name, b.name));
    });

    // Convert to array and sort groups alphabetically with natural sort (Ungrouped at end)
    const sortedGroups = Array.from(groups.entries())
      .sort(([a], [b]) => {
        if (a === 'Ungrouped') return 1;
        if (b === 'Ungrouped') return -1;
        return naturalCompare(a, b);
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

        e.dataTransfer.setData('streamGroupNames', JSON.stringify(allGroupNames));
        e.dataTransfer.setData('streamGroupStreamIds', JSON.stringify(allStreamIds));

        // Custom drag image showing multi-group info
        const dragEl = document.createElement('div');
        dragEl.className = 'drag-preview';
        const totalStreams = selectedGroupsList.reduce((sum, g) => sum + g.streams.length, 0);
        dragEl.textContent = `${selectedGroupsList.length} groups (${totalStreams} streams)`;
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
        dragEl.textContent = `${group.name} (${group.streams.length} streams)`;
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
    [selectedGroupNames, groupedStreams]
  );

  // Bulk create handlers - apply settings defaults
  const openBulkCreateModal = useCallback((group: StreamGroup) => {
    setBulkCreateGroup(group);
    setBulkCreateStreams([]);
    setBulkCreateStartingNumber('');
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
    setNamingOptionsExpanded(false); // Collapse naming options
    setChannelGroupExpanded(false); // Collapse channel group options
    setTimezoneExpanded(false); // Collapse timezone options
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
    setNamingOptionsExpanded(false); // Collapse naming options
    setChannelGroupExpanded(false); // Collapse channel group options
    setTimezoneExpanded(false); // Collapse timezone options
    setBulkCreateModalOpen(true);
  }, [streams, selectedIds, channelDefaults]);

  const closeBulkCreateModal = useCallback(() => {
    setBulkCreateModalOpen(false);
    setBulkCreateGroup(null);
    setBulkCreateGroups([]);
    setBulkCreateStreams([]);
    setBulkCreateCustomGroupNames(new Map());
    setBulkCreateGroupStartNumbers(new Map());
    setBulkCreateSelectedProfiles(new Set());
  }, []);

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
    const selectedGroups = groupedStreams.filter(group =>
      group.streams.some(s => selectedIds.has(s.id))
    );

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
    setNamingOptionsExpanded(false);
    setChannelGroupExpanded(false);
    setTimezoneExpanded(false);
    setBulkCreateModalOpen(true);
  }, [groupedStreams, selectedIds, channelDefaults]);

  // Open bulk create modal for explicitly provided groups (used by external trigger)
  const openBulkCreateModalForMultipleGroups = useCallback((groups: StreamGroup[]) => {
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
    setNamingOptionsExpanded(false);
    setChannelGroupExpanded(false);
    setTimezoneExpanded(false);
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
          openBulkCreateModal(matchingGroup);
        }
      } else {
        // Multiple groups - use multi-group modal
        const matchingGroups = groupedStreams.filter(g => externalTriggerGroupNames.includes(g.name));
        if (matchingGroups.length > 0) {
          openBulkCreateModalForMultipleGroups(matchingGroups);
        }
      }
      // Signal that we've handled the trigger
      onExternalTriggerHandled?.();
    }
  }, [externalTriggerGroupNames, groupedStreams, openBulkCreateModal, openBulkCreateModalForMultipleGroups, onBulkCreateFromGroup, onExternalTriggerHandled]);

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

  // Detect if streams have network prefixes (CHAMP, PPV, etc.)
  const hasNetworkPrefixes = useMemo(() => {
    return detectNetworkPrefixes(streamsToCreate);
  }, [streamsToCreate]);

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
    };

    const streamsByNormalizedName = new Map<string, Stream[]>();
    for (const stream of filteredStreams) {
      const normalizedName = normalizeStreamName(stream.name, normalizeOptions);
      const existing = streamsByNormalizedName.get(normalizedName);
      if (existing) {
        existing.push(stream);
      } else {
        streamsByNormalizedName.set(normalizedName, [stream]);
      }
    }
    const uniqueCount = streamsByNormalizedName.size;
    const duplicateCount = filteredStreams.length - uniqueCount;
    const hasDuplicates = duplicateCount > 0;
    const excludedCount = streamsToCreate.length - filteredStreams.length;
    return { uniqueCount, duplicateCount, hasDuplicates, streamsByNormalizedName, excludedCount };
  }, [streamsToCreate, bulkCreateTimezone, bulkCreateStripCountry, bulkCreateKeepCountry, bulkCreateCountrySeparator, bulkCreateStripNetwork]);

  const handleBulkCreate = useCallback(async () => {
    if (streamsToCreate.length === 0 || !onBulkCreateFromGroup) return;

    // For separate groups mode, we use per-group starting numbers
    // For other modes, we need a valid global starting number
    const useSeparateMode = isFromMultipleGroups && bulkCreateMultiGroupOption === 'separate';

    if (useSeparateMode) {
      // Check that at least the first group has a starting number
      const firstGroupStart = bulkCreateGroupStartNumbers.get(bulkCreateGroups[0]?.name);
      if (!firstGroupStart || isNaN(parseInt(firstGroupStart, 10)) || parseInt(firstGroupStart, 10) < 0) {
        alert('Please enter a valid starting channel number for the first group');
        return;
      }
    } else {
      const startingNum = parseInt(bulkCreateStartingNumber, 10);
      if (isNaN(startingNum) || startingNum < 0) {
        alert('Please enter a valid starting channel number');
        return;
      }
    }

    setBulkCreateLoading(true);

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
            bulkCreateSelectedProfiles.size > 0 ? Array.from(bulkCreateSelectedProfiles) : undefined
          );

          // Increment starting number for next group (if no explicit start)
          currentNumber += group.streams.length;
        }
      } else {
        // Single group or combined mode
        const startingNum = parseInt(bulkCreateStartingNumber, 10);
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
          bulkCreateSelectedProfiles.size > 0 ? Array.from(bulkCreateSelectedProfiles) : undefined
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
    bulkCreateSelectedProfiles,
    channelGroups,
    onBulkCreateFromGroup,
    clearSelection,
    closeBulkCreateModal,
  ]);

  return (
    <div className="streams-pane">
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
                onClick={selectedGroupNames.size > 1 ? openBulkCreateModalForGroups : openBulkCreateModalForSelection}
                title={selectedGroupNames.size > 1 ? 'Create channels from selected groups' : 'Create channels from selected streams'}
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
                    onClick={() => toggleGroup(group.name)}
                    draggable={isEditMode && !!onBulkCreateFromGroup}
                    onDragStart={(e) => {
                      if (isEditMode && onBulkCreateFromGroup) {
                        handleGroupDragStart(e, group);
                      }
                    }}
                  >
                    {isEditMode && onBulkCreateFromGroup && (
                      <span className="group-drag-handle" title="Drag to Channels pane to bulk create">
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
                          className={`stream-item ${isSelected(stream.id) && isEditMode ? 'selected' : ''}`}
                          draggable
                          onClick={(e) => handleItemClick(e, stream)}
                          onDragStart={(e) => handleDragStart(e, stream)}
                        >
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
                            <button
                              className="copy-url-btn"
                              onClick={(e) => {
                                e.stopPropagation();
                                navigator.clipboard.writeText(stream.url!);
                              }}
                              title="Copy stream URL"
                            >
                              <span className="material-icons">content_copy</span>
                            </button>
                          )}
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
              )}

              {/* Channel Group - Collapsible Section */}
              {/* Hide when multi-group with separate option is selected */}
              {!(isFromMultipleGroups && bulkCreateMultiGroupOption === 'separate') && (
              <div className="form-group collapsible-section">
                <div
                  className="collapsible-header"
                  onClick={() => setChannelGroupExpanded(!channelGroupExpanded)}
                >
                  <span className="expand-icon">{channelGroupExpanded ? '▼' : '▶'}</span>
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
                    <span className="expand-icon">{timezoneExpanded ? '▼' : '▶'}</span>
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

              {/* Naming Options - Collapsible Section */}
              <div className="form-group naming-options-section">
                <div
                  className="naming-options-header"
                  onClick={() => setNamingOptionsExpanded(!namingOptionsExpanded)}
                >
                  <span className="expand-icon">{namingOptionsExpanded ? '▼' : '▶'}</span>
                  <span className="naming-options-title">Naming Options</span>
                  <span className="naming-options-summary">
                    {(() => {
                      const options: string[] = [];
                      if (bulkCreateStripNetwork) options.push('Strip network');
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
                    <span className="expand-icon">{profilesExpanded ? '▼' : '▶'}</span>
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
                      const num = bulkCreateStartingNumber ? parseInt(bulkCreateStartingNumber, 10) + idx : '?';
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
    </div>
  );
}
