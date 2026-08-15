import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { createPortal } from 'react-dom';
import type { Stream, StreamGroupInfo, M3UAccount, Channel, ChannelGroup, ChannelProfile, M3UGroupSetting } from '../types';
import { useSelection, useExpandCollapse, useAddStreamDedup } from '../hooks';
import { detectRegionalVariants, filterStreamsByTimezone, normalizeStreamNamesWithBackend, stripQualitySuffixes, type TimezonePreference, type NumberSeparator, type PrefixOrder, type SortCriterion, type SortEnabledMap, type M3UAccountPriorities } from '../services/api';
import { naturalCompare } from '../utils/naturalSort';
import { channelNumberInputError, parseChannelNumberInput } from '../utils/channelNumber';
import { categorizeStreamGroups } from '../utils/streamGroupCategories';
import { openInVLC } from '../utils/vlc';
import { useCopyFeedback } from '../hooks/useCopyFeedback';
import { useDropdown } from '../hooks/useDropdown';
import { useKeyboardShortcuts } from '../hooks/useKeyboardShortcuts';
import { CustomSelect } from './CustomSelect';
import { StreamCreateMenu } from './StreamCreateMenu';
import { PreviewStreamModal } from './PreviewStreamModal';
import { ModalOverlay } from './ModalOverlay';
import { useOwnedDialog } from '../hooks/useOwnedDialog';
import { ShowMoreRows } from './ShowMoreRows';
import { StreamDedupModal } from './StreamDedupModal';
import { logger } from '../utils/logger';
import { setStreamDragData, clearStreamDragData } from '../utils/dragStore';
import './StreamsPane.css';

interface StreamGroup {
  name: string;
  streams: Stream[];
  expanded: boolean;
}

// Incremental rendering for large groups (bd-bed9r): expanding a group
// renders at most this many stream rows initially; a ShowMoreRows sentinel
// renders the next chunk on scroll or click. Mirrors ChannelsPane.
const GROUP_RENDER_CHUNK_SIZE = 100;

// Channel defaults from settings
export interface ChannelDefaults {
  includeChannelNumberInName: boolean;
  channelNumberSeparator: string;
  removeCountryPrefix: boolean;
  includeCountryInName: boolean;
  countrySeparator: string;
  timezonePreference: string;
  defaultChannelProfileIds?: number[];
  customNetworkPrefixes?: string[];
  customNetworkSuffixes?: string[];
  streamSortPriority?: SortCriterion[];
  streamSortEnabled?: SortEnabledMap;
  deprioritizeFailedStreams?: boolean;
  m3uAccountPriorities?: M3UAccountPriorities;
}

interface StreamsPaneProps {
  streams: Stream[];
  providers: M3UAccount[];
  streamGroups: StreamGroupInfo[];
  searchTerm: string;
  onSearchChange: (term: string) => void;
  providerFilter: number | null;
  onProviderFilterChange: (providerId: number | null) => void;
  groupFilter: string | null;
  onGroupFilterChange: (group: string | null) => void;
  loading: boolean;
  /** Total matches reported by the server; may exceed the loaded page. */
  matchingTotal?: number | null;
  onBulkAddToChannel?: (streamIds: number[], channelId: number) => void;
  channels?: Channel[];
  onKeyboardCreateFromGroup?: (
    groupNames: string[],
    streamIds: number[],
    targetGroupId?: number,
  ) => void;
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
  providerGroupSettings?: Record<number, M3UGroupSetting>; // For filtering out M3U-created groups
  deletedGroupIds?: Set<number>; // Groups staged for deletion in edit mode
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
    pushDownOnConflict?: boolean,
    normalize?: boolean
  ) => Promise<void>;
  // Create a single channel (for manual entry mode)
  onCreateChannel?: (name: string, channelNumber?: number, groupId?: number, newGroupName?: string) => Promise<void>;
  // Default value for normalize toggle (from settings)
  defaultNormalizeOnCreate?: boolean;
  // Callback to check for conflicts with existing channel numbers
  // Returns the number of conflicting channels
  onCheckConflicts?: (startingNumber: number, count: number) => number;
  // Callback to count how many EXISTING channels a "Push channels down" would
  // renumber. Always at least the conflict count, and often far more: the
  // push-down ripples upward until it reaches a wide enough run of free
  // numbers (bead enhancedchannelmanager-i85dg).
  onCountPushDownShift?: (startingNumber: number, count: number) => number;
  // Callback to get the highest existing channel number (for "insert at end" option)
  onGetHighestChannelNumber?: () => number;
  // Appearance settings
  showStreamUrls?: boolean;
  hideUngroupedStreams?: boolean;
  // Refresh streams (bypasses cache)
  onRefreshStreams?: () => void;
  // Optional callback fired after a dedup-modal merge (BD-I / bd-1lznl)
  // appends a stream to an existing channel, so the parent can re-fetch
  // the channels list and the mapped-streams set. Distinct from
  // `onRefreshStreams` (which only re-pulls the stream catalog).
  onChannelsChanged?: () => void;
  // Set of stream IDs that are already mapped to channels (for "hide mapped" filter)
  mappedStreamIds?: Set<number>;
  // Callback when a group is expanded (for lazy loading streams)
  // Passes the group name so only that group's streams can be loaded
  onGroupExpand?: (groupName: string) => void;
  // Number of consecutive failures before deprioritizing a stream
  strikeThreshold?: number;
  // Stream IDs currently rendering the dedup cancel-pulse highlight
  // (bd-u6ftw / BD-H). Each id in this set gets the `.is-dedup-returning`
  // class for the brief outline pulse after the operator cancels the
  // dedup modal. Respects prefers-reduced-motion at the source (the App-
  // level hook never adds the id when the user has reduced motion).
  dedupReturningStreamIds?: Set<number>;
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
  matchingTotal = null,
  onBulkAddToChannel,
  channels = [],
  onKeyboardCreateFromGroup,
  selectedProviders = [],
  onSelectedProvidersChange,
  selectedStreamGroups = [],
  onSelectedStreamGroupsChange,
  onClearStreamFilters,
  isEditMode = false,
  channelGroups = [],
  selectedChannelGroups = [],
  providerGroupSettings,
  deletedGroupIds,
  channelProfiles = [],
  channelDefaults,
  externalTriggerGroupNames = null,
  externalTriggerStreamIds = null,
  externalTriggerTargetGroupId = null,
  externalTriggerStartingNumber = null,
  externalTriggerManualEntry = false,
  onExternalTriggerHandled,
  onBulkCreateFromGroup,
  onCreateChannel,
  onCheckConflicts,
  onCountPushDownShift,
  onGetHighestChannelNumber,
  showStreamUrls = true,
  hideUngroupedStreams = true,
  onRefreshStreams,
  onChannelsChanged,
  mappedStreamIds,
  onGroupExpand,
  defaultNormalizeOnCreate = false,
  dedupReturningStreamIds,
}: StreamsPaneProps) {
  const [keyboardDrag, setKeyboardDrag] = useState<
    | { kind: 'stream'; label: string; streamIds: number[] }
    | { kind: 'group'; label: string; groupNames: string[]; streamIds: number[] }
    | null
  >(null);
  const [keyboardDragAnnouncement, setKeyboardDragAnnouncement] = useState('');
  const keyboardDragTriggerRef = useRef<HTMLElement | null>(null);
  const bulkCreateReturnFocusRef = useRef<HTMLElement | null>(null);
  const keyboardDestinationRef = useRef<HTMLDivElement>(null);

  const cancelKeyboardDrag = useCallback(() => {
    const label = keyboardDrag?.label;
    setKeyboardDrag(null);
    setKeyboardDragAnnouncement(label ? `Cancelled dragging ${label}.` : 'Drag cancelled.');
    requestAnimationFrame(() => keyboardDragTriggerRef.current?.focus());
  }, [keyboardDrag]);

  useEffect(() => {
    if (!keyboardDrag) return;
    requestAnimationFrame(() => {
      keyboardDestinationRef.current
        ?.querySelector<HTMLButtonElement>('[role="menuitem"]:not(:disabled)')
        ?.focus();
    });
  }, [keyboardDrag]);

  const beginKeyboardDrag = (
    event: React.KeyboardEvent<HTMLElement>,
    drag:
      | { kind: 'stream'; label: string; streamIds: number[] }
      | { kind: 'group'; label: string; groupNames: string[]; streamIds: number[] },
  ) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    event.stopPropagation();
    keyboardDragTriggerRef.current = event.currentTarget;
    setKeyboardDrag(drag);
    setKeyboardDragAnnouncement(
      `Picked up ${drag.label}. Use Up and Down Arrow keys to choose a destination, Enter to drop, or Escape to cancel.`,
    );
  };

  const handleKeyboardDestinationKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const items = [
      ...(keyboardDestinationRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not(:disabled)') ?? []),
    ];
    const current = items.indexOf(document.activeElement as HTMLButtonElement);
    if (event.key === 'Escape') {
      event.preventDefault();
      cancelKeyboardDrag();
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

  const completeKeyboardDrag = (
    destinationLabel: string,
    action: () => void,
    returnFocusToTrigger = true,
  ) => {
    const draggedLabel = keyboardDrag?.label ?? 'item';
    action();
    setKeyboardDrag(null);
    setKeyboardDragAnnouncement(`Dropped ${draggedLabel} on ${destinationLabel}.`);
    if (returnFocusToTrigger) {
      requestAnimationFrame(() => keyboardDragTriggerRef.current?.focus());
    }
  };
  // BD-I / bd-1lznl: dedup integration for the single-stream "Add Stream"
  // surface (context-menu "Create channel(s) in group"). On a single-stream
  // selection the hook intercepts the click, checks for a candidate, and
  // either opens StreamDedupModal or falls through to the original
  // openBulkCreateModalForStreamIds path. Multi-stream selections proceed
  // unchanged — bulk dedup is BD-J's surface.
  const addStreamDedup = useAddStreamDedup();
  // Expand/collapse groups with useExpandCollapse hook
  const {
    expandedIds: expandedGroups,
    isExpanded: isGroupExpanded,
    toggleExpand: toggleGroup,
    expandAll: expandAllGroupsInternal,
    collapseAll: collapseAllGroupsInternal,
  } = useExpandCollapse<string>();

  // Per-group render limit for incremental rendering (bd-bed9r). Absent key
  // means the initial chunk size; reset when the group is toggled.
  const [groupRenderLimits, setGroupRenderLimits] = useState<Record<string, number>>({});

  // Toggle a group and reset its incremental-render limit so a re-expanded
  // group starts at the initial chunk again (bd-bed9r).
  const handleToggleGroup = useCallback((groupName: string) => {
    toggleGroup(groupName);
    setGroupRenderLimits((prev) => {
      if (!(groupName in prev)) return prev;
      const next = { ...prev };
      delete next[groupName];
      return next;
    });
  }, [toggleGroup]);

  const collapseAllGroups = useCallback(() => {
    collapseAllGroupsInternal();
    setGroupRenderLimits({});
    // "Collapse all" collapses categories too (bead 09x38.5) -- otherwise
    // the button would silently do nothing when categories are already
    // hiding every group.
    setExpandedCategoryNames(new Set());
  }, [collapseAllGroupsInternal]);

  // Hide mapped streams toggle state (persisted in localStorage)
  const [hideMappedStreams, setHideMappedStreams] = useState(() => {
    const stored = localStorage.getItem('ecm-hide-mapped-streams');
    return stored === 'true';
  });

  // Persist hide mapped state to localStorage
  useEffect(() => {
    localStorage.setItem('ecm-hide-mapped-streams', String(hideMappedStreams));
  }, [hideMappedStreams]);

  // Category header collapse/expand state (bead 09x38.5), persisted in
  // localStorage following the same idiom as hideMappedStreams above.
  // Default is collapsed -- a category name is only "expanded" once the
  // operator has explicitly opened it (or it's auto-surfaced by search,
  // handled separately below without touching this persisted set).
  const [expandedCategoryNames, setExpandedCategoryNames] = useState<Set<string>>(() => {
    try {
      const stored = localStorage.getItem('ecm-streams-category-expanded');
      if (stored) return new Set(JSON.parse(stored));
    } catch {
      // Corrupt/old localStorage value -- fall back to all-collapsed.
    }
    return new Set();
  });

  useEffect(() => {
    localStorage.setItem('ecm-streams-category-expanded', JSON.stringify(Array.from(expandedCategoryNames)));
  }, [expandedCategoryNames]);

  const toggleCategoryExpanded = useCallback((category: string) => {
    setExpandedCategoryNames((prev) => {
      const next = new Set(prev);
      if (next.has(category)) {
        next.delete(category);
      } else {
        next.add(category);
      }
      return next;
    });
  }, []);

  // Copy feedback state
  const { copySuccess, copyError, handleCopy } = useCopyFeedback();

  // Filter out mapped streams if toggle is enabled
  // Note: Provider filtering is handled by App.tsx before streams reach this component
  const filteredStreams = useMemo(() => {
    if (!hideMappedStreams || !mappedStreamIds || mappedStreamIds.size === 0) {
      return streams;
    }
    return streams.filter(stream => !mappedStreamIds.has(stream.id));
  }, [streams, hideMappedStreams, mappedStreamIds]);

  // Filter channel groups to exclude M3U-created and soft-deleted groups (for bulk create dropdown)
  // Only show active user-created groups
  const userCreatedChannelGroups = useMemo(() => {
    // Get set of channel group IDs that are associated with M3U providers
    const m3uGroupIds = new Set<number>();
    if (providerGroupSettings) {
      Object.values(providerGroupSettings).forEach(setting => {
        m3uGroupIds.add(setting.channel_group);
        // Also include group_override targets
        if (setting.custom_properties?.group_override) {
          m3uGroupIds.add(setting.custom_properties.group_override);
        }
      });
    }

    // DEBUG: Log filtering info
    logger.debug('[StreamsPane] Channel groups filter debug:', {
      allGroups: channelGroups.map(g => ({ id: g.id, name: g.name })),
      m3uGroupIds: Array.from(m3uGroupIds),
      deletedGroupIds: deletedGroupIds ? Array.from(deletedGroupIds) : 'undefined',
      isEditMode,
    });

    // Return only groups that:
    // 1. Are NOT created by M3U providers
    // 2. Are NOT staged for deletion (soft-deleted in edit mode)
    const filtered = channelGroups.filter(group =>
      !m3uGroupIds.has(group.id) && !deletedGroupIds?.has(group.id)
    );

    logger.debug('[StreamsPane] Filtered groups:', filtered.map(g => ({ id: g.id, name: g.name })));

    return filtered;
  }, [channelGroups, providerGroupSettings, deletedGroupIds, isEditMode]);

  // Whether a search is currently active. The `streams` prop itself is
  // already search-filtered server-side (App.tsx sends `search` to the
  // API), so this only governs local display decisions: whether to include
  // empty lazy-load placeholder groups, and (bead 09x38.5) whether category
  // headers auto-surface regardless of their persisted collapse state.
  const isSearching = searchTerm.trim().length > 0;

  // Shared memoized grouping logic to avoid duplication
  // Groups and sorts streams, then returns sorted entries
  // When searching: only show groups with matching streams
  // Create a map of group name -> count from the API-provided stream groups
  // This is used to display counts even before streams are lazy-loaded
  const streamGroupCounts = useMemo((): Map<string, number> => {
    const counts = new Map<string, number>();
    streamGroups.forEach((groupInfo) => {
      counts.set(groupInfo.name, groupInfo.count);
    });
    return counts;
  }, [streamGroups]);

  // When not searching: show all groups for lazy loading
  // Note: streamGroups is already filtered by provider from the API
  const sortedStreamGroups = useMemo((): [string, Stream[]][] => {
    const groups = new Map<string, Stream[]>();
    const hasGroupFilter = selectedStreamGroups.length > 0;

    // When NOT searching, create empty entries for groups from the API
    // This ensures groups are visible even before their streams are loaded (lazy loading)
    // When searching, skip this - only show groups that have matching streams
    // When filtering by groups, only show selected groups
    if (!isSearching) {
      streamGroups.forEach((groupInfo) => {
        if (!hideUngroupedStreams || groupInfo.name !== 'Ungrouped') {
          // If filtering by groups, only include selected groups
          if (!hasGroupFilter || selectedStreamGroups.includes(groupInfo.name)) {
            groups.set(groupInfo.name, []);
          }
        }
      });
    }

    // Populate groups with loaded/filtered streams
    filteredStreams.forEach((stream) => {
      const groupName = stream.channel_group_name || 'Ungrouped';
      if (!hideUngroupedStreams || groupName !== 'Ungrouped') {
        // When filtering by groups, only include streams from selected groups
        if (hasGroupFilter && !selectedStreamGroups.includes(groupName)) {
          return;
        }
        if (!groups.has(groupName)) {
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
    // When searching, also filter out empty groups (no matching streams)
    return Array.from(groups.entries())
      .filter(([name, streams]) => {
        if (hideUngroupedStreams && name === 'Ungrouped') return false;
        if (isSearching && streams.length === 0) return false;
        return true;
      })
      .sort(([a], [b]) => {
        if (a === 'Ungrouped') return 1;
        if (b === 'Ungrouped') return -1;
        return naturalCompare(a, b);
      });
  }, [filteredStreams, hideUngroupedStreams, streamGroups, isSearching, selectedStreamGroups]);

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

  // Cache selected stream objects so they persist across search filter changes.
  // When the user selects streams under one search, then changes the search,
  // the `streams` prop no longer contains the previously selected streams.
  // This cache ensures we can still resolve those IDs to full Stream objects.
  const selectedStreamsCacheRef = useRef<Map<number, Stream>>(new Map());
  useEffect(() => {
    const cache = selectedStreamsCacheRef.current;
    // Add any currently visible selected streams to cache
    for (const stream of streams) {
      if (selectedIds.has(stream.id)) {
        cache.set(stream.id, stream);
      }
    }
    // Remove deselected streams from cache
    for (const id of cache.keys()) {
      if (!selectedIds.has(id)) {
        cache.delete(id);
      }
    }
  }, [streams, selectedIds]);

  // Track selected stream groups (for multi-group bulk creation)
  const [selectedGroupNames, setSelectedGroupNames] = useState<Set<string>>(new Set());

  // Bulk create modal state
  const [bulkCreateModalOpen, setBulkCreateModalOpen] = useState(false);
  const [bulkCreateGroup, setBulkCreateGroup] = useState<StreamGroup | null>(null);
  const [bulkCreateGroups, setBulkCreateGroups] = useState<StreamGroup[]>([]); // For multi-group creation
  const [bulkCreateStreams, setBulkCreateStreams] = useState<Stream[]>([]); // For selected streams
  const [isManualEntry, setIsManualEntry] = useState(false); // For creating channels without streams
  const [manualEntryChannelName, setManualEntryChannelName] = useState(''); // Channel name for manual entry

  // Stream preview modal state
  const [previewStream, setPreviewStream] = useState<Stream | null>(null);
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
  const { titleId: conflictTitleId, containerRef: conflictContainerRef } = useOwnedDialog(bulkCreateShowConflict);
  const [bulkCreateConflictCount, setBulkCreateConflictCount] = useState(0);
  // How many existing channels the push-down option would renumber. `null`
  // means the parent did not supply a counter, in which case the dialog falls
  // back to describing the shift without a figure.
  const [bulkCreatePushDownCount, setBulkCreatePushDownCount] = useState<number | null>(null);
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
  // Normalization toggle and preview
  const [bulkCreateNormalize, setBulkCreateNormalize] = useState(defaultNormalizeOnCreate);
  const [normalizedNamesPreview, setNormalizedNamesPreview] = useState<Map<string, string>>(new Map());
  const [normalizationPreviewLoading, setNormalizationPreviewLoading] = useState(false);
  const [normalizationExpanded, setNormalizationExpanded] = useState(false);

  // Sync normalization default when settings change
  useEffect(() => {
    setBulkCreateNormalize(defaultNormalizeOnCreate);
  }, [defaultNormalizeOnCreate]);

  // Bulk create group dropdown management
  const {
    isOpen: bulkCreateGroupDropdownOpen,
    setIsOpen: setBulkCreateGroupDropdownOpen,
    dropdownRef: bulkCreateGroupDropdownRef,
  } = useDropdown();
  const [, setNamingOptionsExpanded] = useState(false);
  const [channelGroupExpanded, setChannelGroupExpanded] = useState(false);
  const [timezoneExpanded, setTimezoneExpanded] = useState(false);

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

  // Enabled/visible channel groups offered by the "Create in…" menu — same
  // filter the deleted right-click submenu applied (bead zwhw4).
  const enabledChannelGroups = useMemo(
    () => channelGroups.filter((group) => selectedChannelGroups.includes(group.id)),
    [channelGroups, selectedChannelGroups]
  );

  // Group and sort streams
  // Convert sorted stream groups to StreamGroup objects with expanded state
  const groupedStreams = useMemo((): StreamGroup[] => {
    return sortedStreamGroups.map(([name, groupStreams]) => ({
      name,
      streams: groupStreams,
      expanded: isGroupExpanded(name),
    }));
  }, [sortedStreamGroups, isGroupExpanded]);

  // Bucket the visible (already filtered) groups under their derived
  // category (bead 09x38.5). Categories apply AFTER provider/group filters
  // and search have already narrowed `groupedStreams`, so a category only
  // ever shows groups the operator can currently see.
  const categorizedGroups = useMemo(() => categorizeStreamGroups(groupedStreams), [groupedStreams]);

  // A category is visually expanded if the operator has toggled it open, OR
  // a search is active. Search results are already narrowed to matching
  // groups (see `isSearching` above), so forcing every remaining category
  // open surfaces matches immediately instead of requiring the operator to
  // also expand a collapsed category header to see them. This does not
  // mutate the persisted expandedCategoryNames set -- clearing the search
  // restores whatever collapse state the operator had before.
  const isCategoryExpanded = useCallback(
    (category: string) => isSearching || expandedCategoryNames.has(category),
    [isSearching, expandedCategoryNames]
  );

  // Expand all groups (wrapper to pass group names). Also expands every
  // category (bead 09x38.5) so the newly-expanded groups are actually
  // visible instead of hidden behind still-collapsed category headers.
  const expandAllGroups = useCallback(() => {
    expandAllGroupsInternal(groupedStreams.map(g => g.name));
    setExpandedCategoryNames(new Set(categorizedGroups.map(c => c.category)));
  }, [groupedStreams, categorizedGroups, expandAllGroupsInternal]);

  // Check if all groups AND all categories are expanded/collapsed, so the
  // expand-all/collapse-all buttons reflect the true fully-expanded state.
  const allExpanded =
    groupedStreams.length > 0 &&
    expandedGroups.size === groupedStreams.length &&
    (isSearching || expandedCategoryNames.size === categorizedGroups.length);
  const allCollapsed = expandedGroups.size === 0 && (!isSearching && expandedCategoryNames.size === 0);

  // Clear selection when exiting edit mode
  useEffect(() => {
    if (!isEditMode) {
      clearSelection();
      setSelectedGroupNames(new Set());
    }
  }, [isEditMode, clearSelection]);

  // Keyboard shortcuts management. The StreamCreateMenu handles its own
  // Escape internally (close panel, refocus trigger) and stops propagation,
  // so this document-level Escape only ever clears the selection.
  useKeyboardShortcuts({
    onSelectAll: selectAll,
    onClearSelection: clearSelection,
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

        // Store in drag store as backup (workaround for browsers that clear dataTransfer.types)
        setStreamDragData({
          type: 'stream',
          streamIds: selectedStreamIds,
        });

        // Debug logging
        const typesAfterSet = Array.from(e.dataTransfer.types);
        logger.debug(`[DRAG-DEBUG] Drag started (bulk)`, {
          streamId: stream.id,
          selectedCount,
          types: typesAfterSet,
          effectAllowed: e.dataTransfer.effectAllowed
        });

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

        // Store in drag store as backup (workaround for browsers that clear dataTransfer.types)
        setStreamDragData({
          type: 'stream',
          streamIds: [stream.id],
        });

        // Debug logging
        const typesAfterSet = Array.from(e.dataTransfer.types);
        logger.debug(`[DRAG-DEBUG] Drag started (single)`, {
          streamId: stream.id,
          streamName: stream.name,
          types: typesAfterSet,
          effectAllowed: e.dataTransfer.effectAllowed
        });
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
        // Use API counts for accurate display even before streams are loaded
        const totalStreams = selectedGroupsList.reduce((sum, g) => sum + (streamGroupCounts.get(g.name) ?? g.streams.length), 0);
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
        // Use API count for accurate display even before streams are loaded
        const streamCount = streamGroupCounts.get(group.name) ?? group.streams.length;
        dragEl.textContent = `${group.name} (${streamCount} streams)`;
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
    [selectedGroupNames, groupedStreams, onGroupExpand, streamGroupCounts]
  );

  // Bulk create handlers - apply settings defaults
  const openBulkCreateModal = useCallback((
    group: StreamGroup,
    startingNumber?: number | null,
    targetGroupId?: number | null,
  ) => {
    setBulkCreateGroup(group);
    setBulkCreateStreams([]);
    setBulkCreateStartingNumber(startingNumber != null ? startingNumber.toString() : '');
    setBulkCreateGroupOption(targetGroupId != null ? 'existing' : 'same');
    setBulkCreateSelectedGroupId(targetGroupId ?? null);
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
      channelDefaults?.defaultChannelProfileIds?.length ? new Set(channelDefaults.defaultChannelProfileIds) : new Set()
    );
    setNamingOptionsExpanded(false); // Collapse naming options
    setChannelGroupExpanded(false); // Collapse channel group options
    setTimezoneExpanded(false); // Collapse timezone options
    setBulkCreateModalOpen(true);
  }, [channelDefaults]);

  const openBulkCreateModalForSelection = useCallback(() => {
    // Get selected streams, using cache for streams no longer in the current search results
    const cache = selectedStreamsCacheRef.current;
    const selectedStreamsList = Array.from(selectedIds)
      .map(id => streams.find(s => s.id === id) || cache.get(id))
      .filter((s): s is Stream => s !== undefined);
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
      channelDefaults?.defaultChannelProfileIds?.length ? new Set(channelDefaults.defaultChannelProfileIds) : new Set()
    );
    setNamingOptionsExpanded(false); // Collapse naming options
    setChannelGroupExpanded(false); // Collapse channel group options
    setTimezoneExpanded(false); // Collapse timezone options
    setBulkCreateModalOpen(true);
  }, [streams, selectedIds, channelDefaults]);

  // Open bulk create modal for specific stream IDs (from external trigger)
  // Optionally accepts target group ID and starting number to pre-fill the modal
  const openBulkCreateModalForStreamIds = useCallback((
    streamIds: number[],
    targetGroupId?: number | null,
    startingNumber?: number | null
  ) => {
    // Use cache for streams no longer in the current search results
    const cache = selectedStreamsCacheRef.current;
    const streamsList = streamIds
      .map(id => streams.find(s => s.id === id) || cache.get(id))
      .filter((s): s is Stream => s !== undefined);
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
      channelDefaults?.defaultChannelProfileIds?.length ? new Set(channelDefaults.defaultChannelProfileIds) : new Set()
    );
    setNamingOptionsExpanded(false);
    setChannelGroupExpanded(false);
    setTimezoneExpanded(false);
    setBulkCreateModalOpen(true);
  }, [streams, channelDefaults]);

  const closeBulkCreateModal = useCallback(() => {
    setBulkCreateModalOpen(false);
    setBulkCreateGroup(null);
    setBulkCreateGroups([]);
    setBulkCreateStreams([]);
    setIsManualEntry(false);
    setManualEntryChannelName('');
    setBulkCreateCustomGroupNames(new Map());
    setBulkCreateGroupStartNumbers(new Map());
    setBulkCreateSelectedProfiles(new Set());
    const returnTarget = bulkCreateReturnFocusRef.current;
    bulkCreateReturnFocusRef.current = null;
    if (returnTarget) {
      requestAnimationFrame(() => returnTarget.focus());
    }
  }, []);

  // Open bulk create modal for manual entry (no streams pre-selected)
  const openBulkCreateModalForManualEntry = useCallback((
    targetGroupId?: number | null,
    startingNumber?: number | null
  ) => {
    setBulkCreateGroup(null);
    setBulkCreateGroups([]);
    setBulkCreateStreams([]);
    setIsManualEntry(true);
    setManualEntryChannelName('');
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
      channelDefaults?.defaultChannelProfileIds?.length ? new Set(channelDefaults.defaultChannelProfileIds) : new Set()
    );
    setNamingOptionsExpanded(false);
    setChannelGroupExpanded(false);
    setTimezoneExpanded(false);
    setBulkCreateModalOpen(true);
  }, [channelDefaults]);

  // Handler for "Create channel(s) in <existing group>" from the selection
  // strip's "Create in…" menu (bead zwhw4 — migrated from the deleted
  // right-click context menu with unchanged semantics; acts on the current
  // stream selection).
  //
  // BD-I / bd-1lznl integration (ADR-008 §D1, trigger_context='add_stream'):
  // For a SINGLE-stream selection the dedup hook intercepts the click to
  // check for a duplicate-channel candidate first. If a candidate clears
  // the §D2 floor the operator sees StreamDedupModal and chooses Merge /
  // Create New / Cancel. For multi-stream selections we proceed unchanged
  // — bulk dedup is BD-J's surface, not this one.
  const handleCreateInGroup = useCallback((groupId: number) => {
    const streamIds = Array.from(selectedIds);
    if (streamIds.length === 0) return;

    if (streamIds.length === 1) {
      const cache = selectedStreamsCacheRef.current;
      const stream = streams.find(s => s.id === streamIds[0]) || cache.get(streamIds[0]);
      if (stream) {
        // Route through the dedup hook; on no-candidate / lookup-failure
        // it falls through to the bulk-create modal exactly as before.
        void addStreamDedup.requestAddStream(
          { id: stream.id, name: stream.name },
          groupId,
          () => openBulkCreateModalForStreamIds(streamIds, groupId),
        );
        return;
      }
    }

    // Multi-stream selection or stream lookup miss — preserve the prior
    // behavior. The miss case is defensive: if the stream is not in the
    // current page and not in the cache, the dedup hook would have no
    // stream_name to look up, so we let the bulk-create modal handle it.
    openBulkCreateModalForStreamIds(streamIds, groupId);
  }, [selectedIds, openBulkCreateModalForStreamIds, streams, addStreamDedup]);

  // Handler for "Create in new group…" from the selection strip's
  // "Create in…" menu (bead zwhw4 — migrated from the deleted right-click
  // context menu). Uses the selection cache so streams filtered out of the
  // current page since being selected are still included, matching
  // openBulkCreateModalForSelection.
  const handleCreateInNewGroup = useCallback(() => {
    const cache = selectedStreamsCacheRef.current;
    const streamsList = Array.from(selectedIds)
      .map(id => streams.find(s => s.id === id) || cache.get(id))
      .filter((s): s is Stream => s !== undefined);
    if (streamsList.length === 0) return;
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
      channelDefaults?.defaultChannelProfileIds?.length ? new Set(channelDefaults.defaultChannelProfileIds) : new Set()
    );
    setNamingOptionsExpanded(false);
    setChannelGroupExpanded(true); // Expand channel group section so user sees the "new group" option
    setTimezoneExpanded(false);
    setBulkCreateModalOpen(true);
  }, [selectedIds, streams, channelDefaults]);

  // Toggle group selection (select/deselect all streams in group)
  const toggleGroupSelection = useCallback((group: StreamGroup) => {
    // If streams aren't loaded yet, trigger lazy load and mark group as selected
    if (group.streams.length === 0) {
      if (onGroupExpand) {
        onGroupExpand(group.name);
      }
      // Toggle group in selectedGroupNames even without streams loaded
      // This provides visual feedback and the streams will be selected when they load
      setSelectedGroupNames(prev => {
        const next = new Set(prev);
        if (next.has(group.name)) {
          next.delete(group.name);
        } else {
          next.add(group.name);
        }
        return next;
      });
      return;
    }

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
  }, [selectedIds, selectMultiple, deselectMultiple, onGroupExpand]);

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

  // When streams load for a group that was marked as selected (but had no streams), select those streams
  useEffect(() => {
    selectedGroupNames.forEach(groupName => {
      const group = groupedStreams.find(g => g.name === groupName);
      if (group && group.streams.length > 0) {
        // Check if streams are already selected
        const allSelected = group.streams.every(s => selectedIds.has(s.id));
        if (!allSelected) {
          // Select all streams in this group
          const streamIds = group.streams.map(s => s.id);
          selectMultiple(streamIds);
        }
      }
    });
  }, [groupedStreams, selectedGroupNames, selectedIds, selectMultiple]);

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
      channelDefaults?.defaultChannelProfileIds?.length ? new Set(channelDefaults.defaultChannelProfileIds) : new Set()
    );
    setNamingOptionsExpanded(false);
    setChannelGroupExpanded(false);
    setTimezoneExpanded(false);
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
      channelDefaults?.defaultChannelProfileIds?.length ? new Set(channelDefaults.defaultChannelProfileIds) : new Set()
    );
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
          openBulkCreateModal(
            matchingGroup,
            externalTriggerStartingNumber,
            externalTriggerTargetGroupId,
          );
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
  }, [externalTriggerGroupNames, externalTriggerStartingNumber, externalTriggerTargetGroupId, groupedStreams, openBulkCreateModal, openBulkCreateModalForMultipleGroups, onBulkCreateFromGroup, onExternalTriggerHandled]);

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


  // Compute stream stats for the modal display
  // Applies timezone filtering when a preference is selected
  // Note: Actual channel naming/grouping is handled by the backend normalization engine
  const bulkCreateStats = useMemo(() => {
    // Filter streams based on timezone preference
    const filteredStreams = filterStreamsByTimezone(streamsToCreate, bulkCreateTimezone);
    const streamCount = filteredStreams.length;
    const excludedCount = streamsToCreate.length - filteredStreams.length;

    // Compute deduplicated channel count by grouping streams with the same name
    // (after quality suffix stripping). This mirrors the dedup logic in handleBulkCreateFromGroup.
    const channelMap = new Map<string, { name: string; streams: Stream[] }>();
    for (const stream of filteredStreams) {
      const groupingKey = stripQualitySuffixes(stream.name);
      const existing = channelMap.get(groupingKey);
      if (existing) {
        existing.streams.push(stream);
      } else {
        channelMap.set(groupingKey, { name: stream.name, streams: [stream] });
      }
    }
    const channelCount = channelMap.size;
    const mergedCount = streamCount - channelCount;

    return { streamCount, channelCount, mergedCount, excludedCount, filteredStreams, channelMap };
  }, [streamsToCreate, bulkCreateTimezone]);

  // Update normalize default when prop changes
  useEffect(() => {
    setBulkCreateNormalize(defaultNormalizeOnCreate);
  }, [defaultNormalizeOnCreate]);

  // Fetch normalized names preview when normalize toggle is enabled
  useEffect(() => {
    if (!bulkCreateNormalize || !bulkCreateModalOpen || bulkCreateStats.filteredStreams.length === 0) {
      setNormalizedNamesPreview(new Map());
      return;
    }

    const fetchPreview = async () => {
      setNormalizationPreviewLoading(true);
      try {
        const streamNames = bulkCreateStats.filteredStreams.map(s => s.name);
        const normalizedMap = await normalizeStreamNamesWithBackend(streamNames);
        setNormalizedNamesPreview(normalizedMap);
      } catch (error) {
        logger.error('Failed to fetch normalization preview:', error);
        setNormalizedNamesPreview(new Map());
      } finally {
        setNormalizationPreviewLoading(false);
      }
    };

    fetchPreview();
  }, [bulkCreateNormalize, bulkCreateModalOpen, bulkCreateStats.filteredStreams]);

  // Count how many names will change with normalization
  const normalizationChangeCount = useMemo(() => {
    if (!bulkCreateNormalize || normalizedNamesPreview.size === 0) return 0;
    let count = 0;
    for (const [original, normalized] of normalizedNamesPreview) {
      if (original !== normalized) count++;
    }
    return count;
  }, [bulkCreateNormalize, normalizedNamesPreview]);

  /**
   * The starting channel number each group gets in separate-group mode.
   *
   * `null` means "not determined yet", which happens only while the first
   * group has no entry; the Create button is disabled in that state. An empty
   * entry on any later group means "continue from the previous group", so its
   * number is derived here rather than left to the caller.
   *
   * Preview, the disabled state and the creation call all read this one
   * computation. They used to derive the number three separate ways, and two
   * of them used `parseInt`, so a validated `38.1` was created as `38`. That
   * is the silent normalisation the channel-number contract exists to prevent
   * (bead enhancedchannelmanager-ic884.1). `handleBulkCreateFromGroup` walks a
   * decimal run in tenths, so the continuation walks in the same units.
   */
  const separateGroupStartNumbers = useMemo(() => {
    const resolved = new Map<string, number | null>();
    let current: number | null = null;
    let step = 1;
    for (const group of bulkCreateGroups) {
      const parsed = parseChannelNumberInput(bulkCreateGroupStartNumbers.get(group.name) ?? '');
      if (parsed.ok && parsed.value !== null) {
        current = parsed.value;
        step = current % 1 !== 0 ? 0.1 : 1;
      }
      resolved.set(group.name, current);
      if (current !== null) {
        // Snap back onto the tenths grid: 38.1 + 3 * 0.1 is 38.400000000000006.
        current = Math.round((current + group.streams.length * step) * 10) / 10;
      }
    }
    return resolved;
  }, [bulkCreateGroups, bulkCreateGroupStartNumbers]);

  /** The rejection message for each group whose start number is out of contract. */
  const separateGroupStartErrors = useMemo(() => {
    const errors = new Map<string, string>();
    for (const group of bulkCreateGroups) {
      const message = channelNumberInputError(bulkCreateGroupStartNumbers.get(group.name) ?? '');
      if (message) errors.set(group.name, message);
    }
    return errors;
  }, [bulkCreateGroups, bulkCreateGroupStartNumbers]);

  // Actually perform the bulk create with the specified pushDown option
  // startingNumberOverride: optionally override the starting number (used by "insert at end" option)
  const doBulkCreate = useCallback(async (pushDown: boolean, startingNumberOverride?: number) => {
    // Handle manual entry mode - create a single channel without streams
    if (isManualEntry && onCreateChannel) {
      if (!manualEntryChannelName.trim()) return;

      setBulkCreateLoading(true);
      try {
        // Determine group
        let groupId: number | null = null;
        let newGroupName: string | undefined;

        if (bulkCreateGroupOption === 'existing') {
          groupId = bulkCreateSelectedGroupId;
        } else if (bulkCreateGroupOption === 'new') {
          if (bulkCreateNewGroupName.trim()) {
            newGroupName = bulkCreateNewGroupName.trim();
          }
        }

        // Parse channel number (optional). Manual entry reaches the API without
        // going through `handleBulkCreate`'s guard, so it applies the canonical
        // contract itself (bead enhancedchannelmanager-ic884.1).
        const parsedNumber = parseChannelNumberInput(bulkCreateStartingNumber);
        if (!parsedNumber.ok) {
          alert(parsedNumber.message);
          return;
        }
        const channelNumber = parsedNumber.value ?? undefined;

        // Create the channel
        await onCreateChannel(
          manualEntryChannelName.trim(),
          channelNumber,
          groupId ?? undefined,
          newGroupName
        );

        closeBulkCreateModal();
      } catch (err) {
        logger.error('Failed to create channel:', err);
        alert('Failed to create channel: ' + (err instanceof Error ? err.message : 'Unknown error'));
      } finally {
        setBulkCreateLoading(false);
      }
      return;
    }

    if (streamsToCreate.length === 0 || !onBulkCreateFromGroup) return;

    const useSeparateMode = isFromMultipleGroups && bulkCreateMultiGroupOption === 'separate';

    setBulkCreateLoading(true);
    setBulkCreateShowConflict(false);

    try {
      // Handle multi-group mode with separate groups
      if (useSeparateMode) {
        // Create channels for each group separately, using per-group starting
        // numbers. `separateGroupStartNumbers` already applied the canonical
        // contract and the continue-from-previous rule, so nothing here parses
        // or truncates the operator's entry a second time.
        for (let i = 0; i < bulkCreateGroups.length; i++) {
          const group = bulkCreateGroups[i];
          const currentNumber = separateGroupStartNumbers.get(group.name) ?? 0;
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
            pushDown,
            bulkCreateNormalize
          );
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
          pushDown,
          bulkCreateNormalize
        );
      }

      // Clear selection after successful creation
      if (!isFromGroup) {
        clearSelection();
        setSelectedGroupNames(new Set());
      }

      closeBulkCreateModal();
    } catch (error) {
      logger.error('Bulk create failed:', error);
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
    separateGroupStartNumbers,
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
    bulkCreateNormalize,
    channelGroups,
    onBulkCreateFromGroup,
    clearSelection,
    closeBulkCreateModal,
    isManualEntry,
    manualEntryChannelName,
    onCreateChannel,
    channelDefaults?.customNetworkPrefixes,
    channelDefaults?.customNetworkSuffixes,
  ]);

  // Check for conflicts and show dialog, or proceed directly if no conflicts
  const handleBulkCreate = useCallback(async () => {
    // Handle manual entry mode separately
    if (isManualEntry) {
      if (!manualEntryChannelName.trim()) {
        alert('Please enter a channel name');
        return;
      }
      await doBulkCreate(false);
      return;
    }

    if (streamsToCreate.length === 0 || !onBulkCreateFromGroup) return;

    // For separate groups mode, we use per-group starting numbers
    // For other modes, we need a valid global starting number
    const useSeparateMode = isFromMultipleGroups && bulkCreateMultiGroupOption === 'separate';

    if (useSeparateMode) {
      // Each group's start number is a channel number, so each one is held to
      // the canonical contract and refused with the same sentence the API
      // would return. This used to accept anything `parseFloat` read as
      // non-negative and then convert it with `parseInt`, so `1.05` created a
      // run beginning at `1`; and it only ever looked at the first group, so a
      // later group's entry reached the conversion unchecked. An empty entry
      // means "continue from the previous group" and is allowed on every group
      // except the first. Bead enhancedchannelmanager-ic884.1.
      const firstGroupStart = bulkCreateGroupStartNumbers.get(bulkCreateGroups[0]?.name);
      if (!firstGroupStart || !firstGroupStart.trim()) {
        alert('Please enter a valid starting channel number for the first group');
        return;
      }
      for (const group of bulkCreateGroups) {
        const message = separateGroupStartErrors.get(group.name);
        if (message) {
          alert(message);
          return;
        }
      }
    } else {
      // The starting number is a channel number, so it is held to the canonical
      // contract (non-negative, at most one decimal place). An out-of-contract
      // entry is refused with the same sentence the API would return rather
      // than being rounded onto a neighbouring tenth, and the whole created run
      // inherits the starting value, so this one check covers every channel the
      // operation would create. Bead enhancedchannelmanager-ic884.1.
      const parsedStart = parseChannelNumberInput(bulkCreateStartingNumber, { allowEmpty: false });
      if (!parsedStart.ok) {
        alert(parsedStart.message);
        return;
      }
    }

    // Check for conflicts before proceeding (use floor for conflict check since it checks integer ranges)
    if (onCheckConflicts && !useSeparateMode) {
      const startingNum = Math.floor(parseFloat(bulkCreateStartingNumber));
      const conflictCount = onCheckConflicts(startingNum, bulkCreateStats.channelCount);
      if (conflictCount > 0) {
        // Calculate end-of-sequence number (highest existing + 1)
        const highestNumber = onGetHighestChannelNumber ? onGetHighestChannelNumber() : 0;
        setBulkCreateEndOfSequenceNumber(highestNumber + 1);
        // Show conflict dialog. The push-down count is planned from the
        // unfloored starting number, because that is what the push-down
        // itself uses; the conflict count above stays on the floored
        // integer range it has always used.
        setBulkCreateConflictCount(conflictCount);
        setBulkCreatePushDownCount(
          onCountPushDownShift
            ? onCountPushDownShift(parseFloat(bulkCreateStartingNumber), bulkCreateStats.channelCount)
            : null
        );
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
    separateGroupStartErrors,
    bulkCreateGroups,
    bulkCreateStartingNumber,
    bulkCreateStats.channelCount,
    onBulkCreateFromGroup,
    onCheckConflicts,
    onCountPushDownShift,
    onGetHighestChannelNumber,
    doBulkCreate,
    isManualEntry,
    manualEntryChannelName,
  ]);

  // Handle copying stream URL to clipboard
  const handleCopyStreamUrl = async (url: string, streamName: string) => {
    await handleCopy(url, `stream URL for "${streamName}"`);
  };

  const inventoryCount = searchTerm.trim()
    ? matchingTotal ?? streams.length
    : selectedStreamGroups.length > 0
      ? streamGroups
        .filter((group) => selectedStreamGroups.includes(group.name))
        .reduce((total, group) => total + group.count, 0)
      : streamGroups.reduce((total, group) => total + group.count, 0);
  const inventoryCountKind = searchTerm.trim()
    ? 'matching'
    : selectedStreamGroups.length > 0
      ? 'filtered'
      : 'total';
  const inventoryCountLabel = `${inventoryCount} ${inventoryCountKind} ${inventoryCount === 1 ? 'stream' : 'streams'}`;

  return (
    <div className="streams-pane" aria-labelledby="streams-pane-heading">
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
        <h2 id="streams-pane-heading">
          Streams
          {onRefreshStreams && (
            <button
              className="refresh-streams-btn"
              onClick={onRefreshStreams}
              title="Refresh streams from Dispatcharr"
              disabled={loading}
              aria-label="Refresh streams from Dispatcharr"
            >
              <span className={`material-icons${loading ? ' spinning' : ''}`} aria-hidden="true">sync</span>
            </button>
          )}
        </h2>
        <span className="pane-item-count" aria-label={inventoryCountLabel}>
          {inventoryCount}
        </span>
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
            {isEditMode && onBulkCreateFromGroup && (
              <StreamCreateMenu
                groups={enabledChannelGroups}
                onCreateInGroup={handleCreateInGroup}
                onCreateInNewGroup={handleCreateInNewGroup}
              />
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
              aria-label="Search streams"
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
                aria-label="Clear search"
              >
                <span className="material-icons" aria-hidden="true">close</span>
              </button>
            )}
          </div>
          <div className="expand-collapse-buttons">
            <button
              className="expand-collapse-btn"
              onClick={expandAllGroups}
              disabled={allExpanded || groupedStreams.length === 0}
              title="Expand all groups"
              aria-label="Expand all groups"
            >
              <span className="material-icons" aria-hidden="true">unfold_more</span>
            </button>
            <button
              className="expand-collapse-btn"
              onClick={collapseAllGroups}
              disabled={allCollapsed || groupedStreams.length === 0}
              title="Collapse all groups"
              aria-label="Collapse all groups"
            >
              <span className="material-icons" aria-hidden="true">unfold_less</span>
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
            <CustomSelect
              value={String(providerFilter ?? '')}
              onChange={(val) =>
                onProviderFilterChange(val ? parseInt(val, 10) : null)
              }
              className="streams-filter-select"
              options={[
                { value: '', label: 'All Providers' },
                ...providers.map((provider) => ({
                  value: String(provider.id),
                  label: provider.name,
                })),
              ]}
            />
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
                        aria-label="Clear search"
                        title="Clear search"
                      >
                        <span className="material-icons" aria-hidden="true">close</span>
                      </button>
                    )}
                  </div>
                  <div className="filter-dropdown-actions">
                    <button
                      className="filter-dropdown-action"
                      onClick={() => {
                        // Select all visible (filtered) groups
                        const filteredGroups = streamGroups
                          .filter(g => g.name.toLowerCase().includes(groupSearchFilter.toLowerCase()))
                          .map(g => g.name);
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
                          const filteredGroups = streamGroups
                            .filter(g => g.name.toLowerCase().includes(groupSearchFilter.toLowerCase()))
                            .map(g => g.name);
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
                      .filter(groupInfo => groupInfo.name.toLowerCase().includes(groupSearchFilter.toLowerCase()))
                      .map((groupInfo) => (
                        <label key={groupInfo.name} className="filter-dropdown-option">
                          <input
                            type="checkbox"
                            checked={selectedStreamGroups.includes(groupInfo.name)}
                            onChange={(e) => {
                              if (e.target.checked) {
                                onSelectedStreamGroupsChange!([...selectedStreamGroups, groupInfo.name]);
                              } else {
                                onSelectedStreamGroupsChange!(selectedStreamGroups.filter((g) => g !== groupInfo.name));
                              }
                            }}
                          />
                          <span className="filter-option-name">{groupInfo.name}</span>
                        </label>
                      ))}
                    {streamGroups.filter(groupInfo => groupInfo.name.toLowerCase().includes(groupSearchFilter.toLowerCase())).length === 0 && (
                      <div className="filter-dropdown-empty">No groups match "{groupSearchFilter}"</div>
                    )}
                  </div>
                </div>
              )}
            </div>
          ) : (
            <CustomSelect
              value={groupFilter ?? ''}
              onChange={(val) => onGroupFilterChange(val || null)}
              className="streams-filter-select"
              searchable
              searchPlaceholder="Search groups..."
              options={[
                { value: '', label: 'All Groups' },
                ...streamGroups.map((groupInfo) => ({
                  value: groupInfo.name,
                  label: groupInfo.name,
                })),
              ]}
            />
          )}

          {/* Clear Filters Button - show when any filter is active */}
          {onClearStreamFilters && (selectedProviders.length > 0 || selectedStreamGroups.length > 0) && (
            <button
              className="clear-filters-btn"
              onClick={onClearStreamFilters}
              title="Clear all filters"
              aria-label="Clear all filters"
            >
              <span className="material-icons" aria-hidden="true">filter_alt_off</span>
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
              {categorizedGroups.map(({ category, groups }) => (
                <div key={category} className="stream-category">
                  <button
                    type="button"
                    className="stream-category-header"
                    onClick={() => toggleCategoryExpanded(category)}
                    aria-expanded={isCategoryExpanded(category)}
                  >
                    <span className="material-icons expand-icon" aria-hidden="true">
                      {isCategoryExpanded(category) ? 'expand_more' : 'chevron_right'}
                    </span>
                    <span className="category-name">{category}</span>
                    <span className="category-count">{groups.length}</span>
                  </button>
                  {isCategoryExpanded(category) && (
                    <div className="stream-category-groups">
                      {groups.map((group) => (
                <div key={group.name} className={`stream-group ${(isGroupFullySelected(group) || (group.streams.length === 0 && selectedGroupNames.has(group.name))) && isEditMode ? 'group-selected' : ''}`}>
                  <div
                    className="stream-group-header"
                    onClick={() => {
                      // If group is being expanded (not currently expanded) and we have a callback, trigger lazy load
                      if (!isGroupExpanded(group.name) && onGroupExpand) {
                        onGroupExpand(group.name);
                      }
                      handleToggleGroup(group.name);
                    }}
                  >
                    {isEditMode && onBulkCreateFromGroup && (
                      <button
                        type="button"
                        className="group-drag-handle"
                        aria-label={`Drag stream group ${group.name} to Channels pane to create channels`}
                        title={`Drag stream group ${group.name} to Channels pane to create channels`}
                        draggable={true}
                        onClick={(e) => e.stopPropagation()}
                        onKeyDown={(e) => beginKeyboardDrag(e, {
                          kind: 'group',
                          label: `stream group ${group.name}`,
                          groupNames: [group.name],
                          streamIds: group.streams.map((stream) => stream.id),
                        })}
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
                        <span className="material-icons" aria-hidden="true">drag_indicator</span>
                      </button>
                    )}
                    {isEditMode && onBulkCreateFromGroup && (() => {
                      // Semantic, keyboard-operable group select-all
                      // (round-2 review of bead enhancedchannelmanager-s8xpd's
                      // PR): aria-checked now carries the true tri-state
                      // (true/false/"mixed") instead of the boolean
                      // aria-pressed the zwhw4 pass shipped, which announced
                      // "none selected" and "some selected" identically.
                      // ChannelsPane's equivalent group-checkbox got the same
                      // fix in the same round, keeping both panes' group-
                      // header semantics consistent.
                      const fullySelected = isGroupFullySelected(group) || (group.streams.length === 0 && selectedGroupNames.has(group.name));
                      const partiallySelected = isGroupPartiallySelected(group);
                      return (
                        <button
                          type="button"
                          role="checkbox"
                          aria-checked={fullySelected ? true : partiallySelected ? 'mixed' : false}
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
                          title={fullySelected ? 'Deselect all streams in group' : 'Select all streams in group'}
                          aria-label={fullySelected ? 'Deselect all streams in group' : 'Select all streams in group'}
                        >
                          <span className="material-icons" aria-hidden="true">
                            {fullySelected ? 'check_box' : partiallySelected ? 'indeterminate_check_box' : 'check_box_outline_blank'}
                          </span>
                        </button>
                      );
                    })()}
                    {/* Expand/collapse toggle, restructured to a sibling
                        <button> (round-2 review of bead
                        enhancedchannelmanager-s8xpd's PR): the group
                        select-all button above used to be nested inside
                        this row's own role="button" div -- see the matching
                        comment in ChannelsPane's DroppableGroupHeader for
                        the full nesting-conflict rationale and why a
                        non-focusable row plus a real nested <button> is
                        structurally safer than the old bd-6n14l target-check
                        guard. The row keeps onClick above as a mouse-only
                        "click anywhere" convenience; this button stops
                        propagation so that handler can't double-fire. */}
                    <button
                      type="button"
                      className="group-toggle-btn"
                      aria-expanded={group.expanded}
                      onClick={(e) => {
                        e.stopPropagation();
                        if (!isGroupExpanded(group.name) && onGroupExpand) {
                          onGroupExpand(group.name);
                        }
                        handleToggleGroup(group.name);
                      }}
                    >
                      <span className="expand-icon">{group.expanded ? '▼︎' : '▶︎'}</span>
                      <span className="group-name">{group.name}</span>
                    </button>
                    <span className="group-count">{streamGroupCounts.get(group.name) ?? group.streams.length}</span>
                    {isEditMode && onBulkCreateFromGroup && (
                      <button
                        className="bulk-create-btn"
                        onClick={(e) => {
                          e.stopPropagation();
                          openBulkCreateModal(group);
                        }}
                        title="Create channels from this group"
                        aria-label="Create channels from this group"
                      >
                        <span className="material-icons" aria-hidden="true">playlist_add</span>
                      </button>
                    )}
                  </div>
                  {group.expanded && (() => {
                    // Incremental rendering (bd-bed9r): cap rows in the DOM;
                    // the ShowMoreRows sentinel renders the next chunk on
                    // scroll/click. Selection/drag logic keeps operating on
                    // the FULL group.streams list — only rendering is capped.
                    const renderLimit = groupRenderLimits[group.name] ?? GROUP_RENDER_CHUNK_SIZE;
                    const isTruncated = group.streams.length > renderLimit;
                    const visibleStreams = isTruncated ? group.streams.slice(0, renderLimit) : group.streams;
                    return (
                    <div className="stream-group-items">
                      {visibleStreams.map((stream) => (
                        <div
                          key={stream.id}
                          data-stream-id={stream.id}
                          className={`stream-item ${isSelected(stream.id) && isEditMode ? 'selected' : ''} ${isEditMode ? 'edit-mode' : ''} ${dedupReturningStreamIds?.has(stream.id) ? 'is-dedup-returning' : ''}`}
                          onClick={(e) => {
                            // In edit mode, clicking the row does nothing (use checkbox to select)
                            // Outside edit mode, clicking the row does nothing either
                            e.stopPropagation();
                          }}
                        >
                          {/* Drag handle - only in edit mode, positioned first like channel groups */}
                          {isEditMode && (
                            <button
                              type="button"
                              className="drag-handle"
                              aria-label={`Drag inventory stream ${stream.name} to assign it to a channel`}
                              title={`Drag inventory stream ${stream.name} to assign it to a channel`}
                              draggable={true}
                              onClick={(e) => e.stopPropagation()}
                              onKeyDown={(e) => beginKeyboardDrag(e, {
                                kind: 'stream',
                                label: `inventory stream ${stream.name}`,
                                streamIds: [stream.id],
                              })}
                              onDragStart={(e) => handleDragStart(e, stream)}
                              onDragEnd={() => clearStreamDragData()}
                            >
                              ⋮⋮
                            </button>
                          )}
                          {isEditMode && (
                            /* Semantic, keyboard-operable selector (bead
                               zwhw4 review): a real <button> is natively
                               focusable and Space/Enter fire click;
                               role="checkbox" + aria-checked announce the
                               actual selection state. The group-header
                               select-all above is already a semantic
                               <button aria-pressed>. */
                            <button
                              type="button"
                              role="checkbox"
                              aria-checked={isSelected(stream.id)}
                              aria-label={`Select stream ${stream.name}`}
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
                              <span className="material-icons" aria-hidden="true">
                                {isSelected(stream.id) ? 'check_box' : 'check_box_outline_blank'}
                              </span>
                            </button>
                          )}
                          <div className="stream-artwork-slot" aria-hidden="true">
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
                          </div>
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
                          <div className="stream-actions">
                          {stream.url && (
                            <>
                              <button
                                className="preview-btn"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setPreviewStream(stream);
                                }}
                                title="Preview stream in browser"
                                aria-label="Preview stream in browser"
                              >
                                <span className="material-icons" aria-hidden="true">visibility</span>
                              </button>
                              <button
                                className="vlc-btn"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  openInVLC(stream.url!, stream.name);
                                }}
                                title="Open in VLC"
                                aria-label="Open in VLC"
                              >
                                <span className="material-icons" aria-hidden="true">play_circle</span>
                              </button>
                              <button
                                className="copy-url-btn"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleCopyStreamUrl(stream.url!, stream.name);
                                }}
                                title="Copy stream URL"
                                aria-label="Copy stream URL"
                              >
                                <span className="material-icons" aria-hidden="true">content_copy</span>
                              </button>
                            </>
                          )}
                          </div>
                        </div>
                      ))}
                      {/* Incremental rendering sentinel (bd-bed9r) */}
                      {isTruncated && (
                        <ShowMoreRows
                          remaining={group.streams.length - visibleStreams.length}
                          noun="streams"
                          onShowMore={() => {
                            setGroupRenderLimits((prev) => ({
                              ...prev,
                              [group.name]: (prev[group.name] ?? GROUP_RENDER_CHUNK_SIZE) + GROUP_RENDER_CHUNK_SIZE,
                            }));
                          }}
                        />
                      )}
                    </div>
                    );
                  })()}
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
      {bulkCreateModalOpen && (streamsToCreate.length > 0 || isManualEntry) && (
        <ModalOverlay
          onClose={closeBulkCreateModal}
          role="dialog"
          aria-modal="true"
          aria-labelledby="bulk-create-modal-title"
        >
          <div className="bulk-create-modal">
            <div className="modal-header">
              <h3 id="bulk-create-modal-title">
                {isManualEntry
                  ? 'Create Channel'
                  : isFromGroup
                    ? `Create Channels from "${bulkCreateGroup!.name}"`
                    : isFromMultipleGroups
                      ? `Create Channels from ${bulkCreateGroups.length} Groups`
                      : `Create Channels from ${streamsToCreate.length} Selected Streams`
                }
              </h3>
              <button className="modal-close-btn" onClick={closeBulkCreateModal} aria-label="Close" title="Close">
                <span className="material-icons" aria-hidden="true">close</span>
              </button>
            </div>

            <div className="modal-body">
              {/* Manual entry mode - channel name input */}
              {isManualEntry && (
                <div className="form-group">
                  <label className="form-label">Channel Name</label>
                  <input
                    type="text"
                    className="form-input"
                    value={manualEntryChannelName}
                    onChange={(e) => setManualEntryChannelName(e.target.value)}
                    placeholder="Enter channel name"
                    autoFocus
                  />
                </div>
              )}

              {/* Multi-group option - only show when multiple groups selected */}
              {isFromMultipleGroups && !isManualEntry && (
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
                          const startError = separateGroupStartErrors.get(group.name);
                          const existingGroup = channelGroups.find(g => g.name === customName);
                          return (
                            <div key={group.name} className="group-name-row">
                              <span className="group-stream-count">{streamGroupCounts.get(group.name) ?? group.streams.length}</span>
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
                              {startError && (
                                <div className="field-error" role="alert">{startError}</div>
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
                <span>
                  {bulkCreateStats.mergedCount > 0 ? (
                    <>
                      <strong>{bulkCreateStats.streamCount}</strong> stream{bulkCreateStats.streamCount !== 1 ? 's' : ''} → <strong>{bulkCreateStats.channelCount}</strong> channel{bulkCreateStats.channelCount !== 1 ? 's' : ''} ({bulkCreateStats.mergedCount} duplicate{bulkCreateStats.mergedCount !== 1 ? 's' : ''} merged)
                    </>
                  ) : (
                    <>
                      <strong>{bulkCreateStats.streamCount}</strong> stream{bulkCreateStats.streamCount !== 1 ? 's' : ''} selected
                    </>
                  )}
                  {bulkCreateStats.excludedCount > 0 && (
                    <span className="excluded-info"> ({bulkCreateStats.excludedCount} excluded by timezone filter)</span>
                  )}
                </span>
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
                  {(() => {
                    const startError = channelNumberInputError(bulkCreateStartingNumber);
                    return startError ? (
                      <div className="field-error" role="alert">{startError}</div>
                    ) : null;
                  })()}
                  {bulkCreateStartingNumber && !isNaN(parseFloat(bulkCreateStartingNumber)) && (
                    <div className="number-range-preview">
                      {(() => {
                        const startNum = parseFloat(bulkCreateStartingNumber);
                        const hasDecimal = bulkCreateStartingNumber.includes('.');
                        const increment = hasDecimal ? 0.1 : 1;
                        const endNum = startNum + (bulkCreateStats.channelCount - 1) * increment;
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
                                    aria-label="Clear search"
                                    title="Clear search"
                                  >
                                    <span className="material-icons" aria-hidden="true">close</span>
                                  </button>
                                )}
                              </div>
                              <div className="dropdown-options">
                                {userCreatedChannelGroups
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
                                {userCreatedChannelGroups.filter(g => !bulkCreateGroupSearch || g.name.toLowerCase().includes(bulkCreateGroupSearch.toLowerCase())).length === 0 && (
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

              {/* Channel Number in Name Option */}
              <div className="form-group">
                <label className="checkbox-option">
                  <input
                    type="checkbox"
                    checked={bulkCreateAddNumber}
                    onChange={(e) => setBulkCreateAddNumber(e.target.checked)}
                  />
                  <span>Add channel number to name</span>
                </label>
                {bulkCreateAddNumber && (
                  <div className="separator-options" style={{ marginTop: '0.5rem', marginLeft: '1.5rem' }}>
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
                    <span className="option-hint" style={{ marginLeft: '0.5rem' }}>e.g., "100 {bulkCreateSeparator} ESPN"</span>
                  </div>
                )}
              </div>

              {/* Normalization Rules - Collapsible Section */}
              <div className="form-group collapsible-section">
                <div
                  className="collapsible-header"
                  onClick={() => setNormalizationExpanded(!normalizationExpanded)}
                >
                  <span className="expand-icon">{normalizationExpanded ? '▼︎' : '▶︎'}</span>
                  <span className="collapsible-title">Normalization Rules</span>
                  <span className="collapsible-summary">
                    {bulkCreateNormalize
                      ? normalizationPreviewLoading
                        ? 'Loading...'
                        : normalizationChangeCount > 0
                          ? `${normalizationChangeCount} name${normalizationChangeCount !== 1 ? 's' : ''} will change`
                          : 'Enabled (no changes)'
                      : 'Disabled'}
                  </span>
                </div>

                {normalizationExpanded && (
                  <div className="collapsible-content">
                    <div className="normalization-info">
                      <span className="material-icons">auto_fix_high</span>
                      <span>Apply normalization rules to clean up channel names (strips quality suffixes, formats consistently)</span>
                    </div>
                    <label className="checkbox-option normalization-toggle">
                      <input
                        type="checkbox"
                        checked={bulkCreateNormalize}
                        onChange={(e) => setBulkCreateNormalize(e.target.checked)}
                      />
                      <span>Apply normalization rules</span>
                    </label>

                    {/* Preview of normalized names */}
                    {bulkCreateNormalize && (
                      <div className="normalization-preview">
                        {normalizationPreviewLoading ? (
                          <div className="normalization-loading">
                            <span className="material-icons spinning">sync</span>
                            <span>Loading preview...</span>
                          </div>
                        ) : normalizationChangeCount > 0 ? (
                          <>
                            <div className="normalization-summary">
                              {normalizationChangeCount} of {bulkCreateStats.streamCount} names will be normalized
                            </div>
                            <div className="normalization-changes">
                              {Array.from(normalizedNamesPreview.entries())
                                .filter(([original, normalized]) => original !== normalized)
                                .slice(0, 5)
                                .map(([original, normalized]) => (
                                  <div key={original} className="normalization-change-item">
                                    <span className="original-name">{original}</span>
                                    <span className="material-icons arrow-icon">arrow_forward</span>
                                    <span className="normalized-name">{normalized}</span>
                                  </div>
                                ))}
                              {normalizationChangeCount > 5 && (
                                <div className="normalization-more">
                                  ... and {normalizationChangeCount - 5} more
                                </div>
                              )}
                            </div>
                          </>
                        ) : (
                          <div className="normalization-no-changes">
                            <span className="material-icons">check_circle</span>
                            <span>No names will change (already normalized or no matching rules)</span>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* Preview - show per-group preview in separate mode, otherwise show combined preview */}
              {isFromMultipleGroups && bulkCreateMultiGroupOption === 'separate' ? (
                <div className="bulk-create-preview">
                  <label>Preview (first 3 channels per group)</label>
                  <div className="preview-list">
                    {bulkCreateGroups.map((group) => {
                      // The preview reads the same resolution the creation call
                      // uses, so what the operator is shown is what gets
                      // created. It used to re-derive the numbers with
                      // `parseInt`, which previewed a decimal start truncated.
                      const startNum = separateGroupStartNumbers.get(group.name) ?? null;
                      const step = startNum !== null && startNum % 1 !== 0 ? 0.1 : 1;
                      const customName = bulkCreateCustomGroupNames.get(group.name) || group.name;
                      return (
                        <div key={group.name} className="preview-group">
                          <div className="preview-group-header">{customName}</div>
                          {group.streams.slice(0, 3).map((stream, idx) => {
                            const num =
                              startNum !== null
                                ? Math.round((startNum + idx * step) * 10) / 10
                                : '?';
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
                  <label>Channels (first 10)</label>
                  <div className="preview-list">
                    {Array.from(bulkCreateStats.channelMap.entries()).slice(0, 10).map(([key, { name, streams }], idx) => {
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
                      return (
                        <div key={key} className="preview-item">
                          <span className="preview-number">{num}</span>
                          <span className="preview-name">{name}</span>
                          {streams.length > 1 && (
                            <span className="preview-merge-badge">{streams.length} streams</span>
                          )}
                        </div>
                      );
                    })}
                    {bulkCreateStats.channelCount > 10 && (
                      <div className="preview-more">
                        ... and {bulkCreateStats.channelCount - 10} more
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
                  isManualEntry
                    // Manual entry: require channel name
                    ? !manualEntryChannelName.trim()
                    // In separate groups mode, check first group has a start number
                    // Separate groups mode: the first group must have a start
                    // number, and no group may carry an out-of-contract one.
                    : isFromMultipleGroups && bulkCreateMultiGroupOption === 'separate'
                      ? !bulkCreateGroupStartNumbers.get(bulkCreateGroups[0]?.name) ||
                        separateGroupStartErrors.size > 0
                      : !bulkCreateStartingNumber || !!channelNumberInputError(bulkCreateStartingNumber)
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
                    {isManualEntry ? 'Create Channel' : `Create ${bulkCreateStats.channelCount} Channels`}
                  </>
                )}
              </button>
            </div>
          </div>
        </ModalOverlay>
      )}

      {/* Bulk Create Conflict Dialog */}
      {bulkCreateShowConflict && (
        <ModalOverlay onClose={bulkCreateLoading ? () => {} : () => setBulkCreateShowConflict(false)} role="dialog" aria-modal="true" aria-labelledby={conflictTitleId}>
          <div className="modal-content conflict-dialog" ref={conflictContainerRef}>
            <h3 id={conflictTitleId}>Channel Number Conflict</h3>
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
                  <span>
                    {bulkCreatePushDownCount === null
                      ? `Insert at ${bulkCreateStartingNumber} and shift existing channels by ${bulkCreateStats.channelCount}`
                      : `Insert at ${bulkCreateStartingNumber}, renumbering ${bulkCreatePushDownCount} existing channel${bulkCreatePushDownCount === 1 ? '' : 's'} upward by ${bulkCreateStats.channelCount}`}
                  </span>
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
                className="modal-btn modal-btn-secondary"
                onClick={() => setBulkCreateShowConflict(false)}
                disabled={bulkCreateLoading}
              >
                Cancel
              </button>
            </div>
          </div>
        </ModalOverlay>
      )}

      {/* Stream Preview Modal */}
      <div
        className="keyboard-drag-status"
        role="status"
        aria-live="polite"
        aria-atomic="true"
      >
        {keyboardDragAnnouncement}
      </div>
      {keyboardDrag && createPortal(
        <div
          ref={keyboardDestinationRef}
          className="keyboard-drag-destinations"
          role="menu"
          aria-label={keyboardDrag.kind === 'stream' ? 'Choose channel destination' : 'Choose channel group destination'}
          onKeyDown={handleKeyboardDestinationKeyDown}
        >
          <div className="keyboard-drag-destinations-title">
            {keyboardDrag.kind === 'stream' ? 'Assign to channel' : 'Create channels in group'}
          </div>
          {keyboardDrag.kind === 'stream'
            ? channels.map((channel) => (
                <button
                  key={channel.id}
                  type="button"
                  role="menuitem"
                  onClick={() => completeKeyboardDrag(
                    `channel ${channel.name}`,
                    () => onBulkAddToChannel?.(keyboardDrag.streamIds, channel.id),
                  )}
                  disabled={!onBulkAddToChannel}
                >
                  <span className="material-icons" aria-hidden="true">live_tv</span>
                  {channel.name}
                </button>
              ))
            : channelGroups.map((group) => (
                <button
                  key={group.id}
                  type="button"
                  role="menuitem"
                  onClick={() => completeKeyboardDrag(
                    `channel group ${group.name}`,
                    () => {
                      bulkCreateReturnFocusRef.current = keyboardDragTriggerRef.current;
                      onKeyboardCreateFromGroup?.(
                        keyboardDrag.groupNames,
                        keyboardDrag.streamIds,
                        group.id,
                      );
                    },
                    false,
                  )}
                  disabled={!onKeyboardCreateFromGroup}
                >
                  <span className="material-icons" aria-hidden="true">folder</span>
                  {group.name}
                </button>
              ))}
          <button type="button" role="menuitem" onClick={cancelKeyboardDrag}>
            <span className="material-icons" aria-hidden="true">close</span>
            Cancel drag
          </button>
        </div>,
        document.body,
      )}
      <PreviewStreamModal
        isOpen={previewStream !== null}
        onClose={() => setPreviewStream(null)}
        stream={previewStream}
        providerName={previewStream?.m3u_account ? providers.find((p) => p.id === previewStream.m3u_account)?.name : undefined}
      />

      {/* Stream Dedup Modal (BD-I / bd-1lznl, ADR-008 §D1 trigger_context='add_stream') */}
      <StreamDedupModal
        isOpen={addStreamDedup.modalState.isOpen}
        streamName={addStreamDedup.modalState.streamName}
        candidate={addStreamDedup.modalState.candidate}
        trigger="add_stream"
        onMerge={async (channelId) => {
          await addStreamDedup.handleMerge(channelId);
          // After a successful merge the channel now owns this stream, so the
          // mapped-streams set the parent computes against the channels list
          // is stale until the next channels fetch. Surface a refresh hint so
          // the "hide mapped streams" toggle reflects reality immediately.
          onChannelsChanged?.();
        }}
        onCreateNew={async () => {
          await addStreamDedup.handleCreateNew();
        }}
        onCancel={addStreamDedup.handleCancel}
      />
    </div>
  );
}
