import { useState, useEffect, useCallback, useRef, useMemo, Suspense, lazy } from 'react';
import {
  SettingsModal,
  EditModeExitDialog,
  TabNavigation,
  type TabId,
} from './components';
import { ChannelManagerTab } from './components/tabs/ChannelManagerTab';
import { useChangeHistory, useEditMode } from './hooks';
import * as api from './services/api';
import type { Channel, ChannelGroup, Stream, M3UAccount, M3UGroupSetting, Logo, ChangeInfo, EPGData, StreamProfile, EPGSource, ChannelListFilterSettings } from './types';
import packageJson from '../package.json';
import './App.css';

// Lazy load non-primary tabs
const M3UManagerTab = lazy(() => import('./components/tabs/M3UManagerTab').then(m => ({ default: m.M3UManagerTab })));
const EPGManagerTab = lazy(() => import('./components/tabs/EPGManagerTab').then(m => ({ default: m.EPGManagerTab })));
const LogoManagerTab = lazy(() => import('./components/tabs/LogoManagerTab').then(m => ({ default: m.LogoManagerTab })));
const SettingsTab = lazy(() => import('./components/tabs/SettingsTab').then(m => ({ default: m.SettingsTab })));

function App() {
  // Health check
  const [health, setHealth] = useState<{ status: string; service: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Channels state
  const [channels, setChannels] = useState<Channel[]>([]);
  const [channelGroups, setChannelGroups] = useState<ChannelGroup[]>([]);
  const [selectedChannel, setSelectedChannel] = useState<Channel | null>(null);
  const [selectedChannelIds, setSelectedChannelIds] = useState<Set<number>>(new Set());
  const [lastSelectedChannelId, setLastSelectedChannelId] = useState<number | null>(null);
  const [channelsLoading, setChannelsLoading] = useState(true);
  const [channelSearch, setChannelSearch] = useState('');
  const [channelGroupFilter, setChannelGroupFilter] = useState<number[]>([]);

  // Streams state
  const [streams, setStreams] = useState<Stream[]>([]);
  const [providers, setProviders] = useState<M3UAccount[]>([]);
  const [streamGroups, setStreamGroups] = useState<string[]>([]);
  const [streamsLoading, setStreamsLoading] = useState(true);
  const [streamSearch, setStreamSearch] = useState('');
  const [streamProviderFilter, setStreamProviderFilter] = useState<number | null>(null);
  const [streamGroupFilter, setStreamGroupFilter] = useState<string | null>(null);
  // Multi-select filter state for streams pane (UI filtering)
  const [selectedProviderFilters, setSelectedProviderFilters] = useState<number[]>([]);
  const [selectedStreamGroupFilters, setSelectedStreamGroupFilters] = useState<string[]>([]);

  // Logos state
  const [logos, setLogos] = useState<Logo[]>([]);

  // EPG Data, EPG Sources, and Stream Profiles state
  const [epgData, setEpgData] = useState<EPGData[]>([]);
  const [epgSources, setEpgSources] = useState<EPGSource[]>([]);
  const [streamProfiles, setStreamProfiles] = useState<StreamProfile[]>([]);
  const [epgDataLoading, setEpgDataLoading] = useState(false);

  // Settings state
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [autoRenameChannelNumber, setAutoRenameChannelNumber] = useState(false);
  const [dispatcharrUrl, setDispatcharrUrl] = useState('');
  const [showStreamUrls, setShowStreamUrls] = useState(true);
  const [channelDefaults, setChannelDefaults] = useState({
    includeChannelNumberInName: false,
    channelNumberSeparator: '-',
    removeCountryPrefix: false,
    includeCountryInName: false,
    countrySeparator: '|',
    timezonePreference: 'both',
  });

  // Provider group settings (for identifying auto channel sync groups)
  const [providerGroupSettings, setProviderGroupSettings] = useState<Record<number, M3UGroupSetting>>({});

  // Channel list filter settings (persisted to localStorage)
  const defaultFilterSettings: ChannelListFilterSettings = {
    showEmptyGroups: false,
    showNewlyCreatedGroups: true,
    showProviderGroups: true,
    showManualGroups: true,
    showAutoChannelGroups: true,
  };
  const [channelListFilters, setChannelListFilters] = useState<ChannelListFilterSettings>(() => {
    const saved = localStorage.getItem('channelListFilters');
    return saved ? JSON.parse(saved) : defaultFilterSettings;
  });

  // Track newly created group IDs in this session
  const [newlyCreatedGroupIds, setNewlyCreatedGroupIds] = useState<Set<number>>(new Set());

  // Track if baseline has been initialized
  const baselineInitialized = useRef(false);

  // Track if channel group filter has been auto-initialized
  const channelGroupFilterInitialized = useRef(false);

  // Edit mode exit dialog state
  const [showExitDialog, setShowExitDialog] = useState(false);

  // Tab navigation state
  const [activeTab, setActiveTab] = useState<TabId>('channel-manager');
  const [pendingTabChange, setPendingTabChange] = useState<TabId | null>(null);

  // Stream group drop trigger (for opening bulk create modal from channels pane)
  const [droppedStreamGroupName, setDroppedStreamGroupName] = useState<string | null>(null);

  // Edit mode for staging changes
  const {
    isEditMode,
    isCommitting,
    stagedOperationCount,
    modifiedChannelIds,
    displayChannels,
    canLocalUndo,
    canLocalRedo,
    editModeDuration,
    enterEditMode,
    exitEditMode: rawExitEditMode,
    stageUpdateChannel,
    stageAddStream,
    stageRemoveStream,
    stageReorderStreams,
    stageBulkAssignNumbers,
    stageDeleteChannel,
    stageDeleteChannelGroup,
    addChannelToWorkingCopy,
    getSummary,
    commit,
    discard,
    localUndo,
    localRedo,
    startBatch,
    endBatch,
  } = useEditMode({
    channels,
    onChannelsChange: setChannels,
    onCommitComplete: () => {
      loadChannels(); // Refresh from server
      loadChannelGroups(); // Refresh groups (for deleted groups)
    },
    onError: setError,
  });

  // Wrap exit to show dialog if there are staged changes
  const handleExitEditMode = useCallback(() => {
    if (stagedOperationCount > 0) {
      setShowExitDialog(true);
    } else {
      rawExitEditMode();
    }
  }, [stagedOperationCount, rawExitEditMode]);

  // Handle dialog actions
  const handleApplyChanges = useCallback(async () => {
    await commit();
    setShowExitDialog(false);
    // Switch to pending tab if there was one
    if (pendingTabChange) {
      setActiveTab(pendingTabChange);
      setPendingTabChange(null);
    }
  }, [commit, pendingTabChange]);

  const handleDiscardChanges = useCallback(() => {
    discard();
    setShowExitDialog(false);
    // Switch to pending tab if there was one
    if (pendingTabChange) {
      setActiveTab(pendingTabChange);
      setPendingTabChange(null);
    }
  }, [discard, pendingTabChange]);

  const handleKeepEditing = useCallback(() => {
    setShowExitDialog(false);
    setPendingTabChange(null);
  }, []);

  // Handle tab change - check for edit mode with pending changes
  const handleTabChange = useCallback((newTab: TabId) => {
    if (isEditMode && stagedOperationCount > 0 && newTab !== 'channel-manager') {
      // Show confirmation dialog and store pending tab change
      setShowExitDialog(true);
      setPendingTabChange(newTab);
      return;
    }

    if (isEditMode && newTab !== 'channel-manager') {
      // Exit edit mode when leaving Channel Manager
      rawExitEditMode();
    }

    setActiveTab(newTab);
  }, [isEditMode, stagedOperationCount, rawExitEditMode]);

  // Change history for undo/redo
  const {
    canUndo,
    canRedo,
    undoCount,
    redoCount,
    savePoints,
    hasUnsavedChanges,
    lastChange,
    isOperationPending,
    recordChange,
    undo,
    redo,
    createSavePoint,
    revertToSavePoint,
    deleteSavePoint,
    initializeBaseline,
  } = useChangeHistory({
    channels,
    onChannelsRestore: setChannels,
    onError: setError,
  });

  // Check settings and load initial data
  useEffect(() => {
    const init = async () => {
      try {
        const settings = await api.getSettings();
        setAutoRenameChannelNumber(settings.auto_rename_channel_number);
        setDispatcharrUrl(settings.url);
        setShowStreamUrls(settings.show_stream_urls);
        setChannelDefaults({
          includeChannelNumberInName: settings.include_channel_number_in_name,
          channelNumberSeparator: settings.channel_number_separator,
          removeCountryPrefix: settings.remove_country_prefix,
          includeCountryInName: settings.include_country_in_name,
          countrySeparator: settings.country_separator,
          timezonePreference: settings.timezone_preference,
        });

        // Apply hide_auto_sync_groups setting to channelListFilters
        if (settings.hide_auto_sync_groups) {
          setChannelListFilters(prev => ({
            ...prev,
            showAutoChannelGroups: false,
          }));
        }

        // Apply theme setting
        if (settings.theme && settings.theme !== 'dark') {
          document.documentElement.setAttribute('data-theme', settings.theme);
        }

        if (!settings.configured) {
          setSettingsOpen(true);
          return;
        }

        api.getHealth()
          .then(setHealth)
          .catch((err) => setError(err.message));

        loadChannelGroups();
        loadChannels();
        loadProviders();
        loadProviderGroupSettings();
        loadStreamGroups();
        loadStreams();
        loadLogos();
        loadStreamProfiles();
        loadEpgSources();
        loadEpgData();
      } catch (err) {
        console.error('Failed to load settings:', err);
        setSettingsOpen(true);
      }
    };
    init();
  }, []);

  // Auto-select channel groups that have channels when data first loads
  useEffect(() => {
    if (channelGroupFilterInitialized.current) return;
    if (channels.length === 0 || channelGroups.length === 0) return;

    // Build set of auto-sync related groups (same logic as ChannelsPane)
    const autoSyncRelatedGroups = new Set<number>();
    const settingsMap = providerGroupSettings as unknown as Record<string, M3UGroupSetting> | undefined;
    if (settingsMap) {
      for (const setting of Object.values(settingsMap)) {
        if (setting.auto_channel_sync) {
          autoSyncRelatedGroups.add(setting.channel_group);
          if (setting.custom_properties?.group_override) {
            autoSyncRelatedGroups.add(setting.custom_properties.group_override);
          }
        }
      }
    }

    // Get unique group IDs from channels
    const groupsWithChannels = new Set<number>();
    channels.forEach((ch) => {
      if (ch.channel_group_id !== null) {
        groupsWithChannels.add(ch.channel_group_id);
      }
    });

    // Auto-select groups that have channels, respecting showAutoChannelGroups filter
    let groupIds = Array.from(groupsWithChannels);
    if (channelListFilters.showAutoChannelGroups === false) {
      groupIds = groupIds.filter(id => !autoSyncRelatedGroups.has(id));
    }
    setChannelGroupFilter(groupIds);
    channelGroupFilterInitialized.current = true;
  }, [channels, channelGroups, providerGroupSettings, channelListFilters.showAutoChannelGroups]);

  // Track previous showAutoChannelGroups value to detect changes
  const prevShowAutoChannelGroups = useRef(channelListFilters.showAutoChannelGroups);

  // When showAutoChannelGroups filter is toggled, update the group selection
  useEffect(() => {
    // Skip if this is the initial render or value hasn't changed
    if (prevShowAutoChannelGroups.current === channelListFilters.showAutoChannelGroups) return;
    prevShowAutoChannelGroups.current = channelListFilters.showAutoChannelGroups;

    // Build set of auto-sync related groups
    const autoSyncRelatedGroups = new Set<number>();
    const settingsMap = providerGroupSettings as unknown as Record<string, M3UGroupSetting> | undefined;
    if (settingsMap) {
      for (const setting of Object.values(settingsMap)) {
        if (setting.auto_channel_sync) {
          autoSyncRelatedGroups.add(setting.channel_group);
          if (setting.custom_properties?.group_override) {
            autoSyncRelatedGroups.add(setting.custom_properties.group_override);
          }
        }
      }
    }
    if (autoSyncRelatedGroups.size === 0) return;

    // Get auto-sync groups that have channels
    const autoSyncGroupsWithChannels = new Set<number>();
    channels.forEach((ch) => {
      if (ch.channel_group_id !== null && autoSyncRelatedGroups.has(ch.channel_group_id)) {
        autoSyncGroupsWithChannels.add(ch.channel_group_id);
      }
    });

    if (channelListFilters.showAutoChannelGroups) {
      // Add auto-sync groups to selection
      setChannelGroupFilter(prev => {
        const newSet = new Set(prev);
        autoSyncGroupsWithChannels.forEach(id => newSet.add(id));
        return Array.from(newSet);
      });
    } else {
      // Remove auto-sync groups from selection
      setChannelGroupFilter(prev => prev.filter(id => !autoSyncRelatedGroups.has(id)));
    }
  }, [channelListFilters.showAutoChannelGroups, providerGroupSettings, channels]);

  const handleSettingsSaved = async () => {
    setError(null);
    // Reload settings to get updated values
    try {
      const settings = await api.getSettings();
      setAutoRenameChannelNumber(settings.auto_rename_channel_number);
      setDispatcharrUrl(settings.url);
      setShowStreamUrls(settings.show_stream_urls);
      setChannelDefaults({
        includeChannelNumberInName: settings.include_channel_number_in_name,
        channelNumberSeparator: settings.channel_number_separator,
        removeCountryPrefix: settings.remove_country_prefix,
        includeCountryInName: settings.include_country_in_name,
        countrySeparator: settings.country_separator,
        timezonePreference: settings.timezone_preference,
      });

      // Apply hide_auto_sync_groups setting to channelListFilters
      // The useEffect watching showAutoChannelGroups will handle removing groups from selection
      if (settings.hide_auto_sync_groups) {
        setChannelListFilters(prev => ({
          ...prev,
          showAutoChannelGroups: false,
        }));
      }
    } catch (err) {
      console.error('Failed to reload settings:', err);
    }
    // Reload all data after settings change
    api.getHealth()
      .then(setHealth)
      .catch((err) => setError(err.message));
    loadChannelGroups();
    loadChannels();
    loadProviders();
    loadProviderGroupSettings();
    loadStreamGroups();
    loadStreams();
    loadLogos();
    loadStreamProfiles();
    loadEpgSources();
    loadEpgData();
  };

  const loadChannelGroups = async () => {
    try {
      const groups = await api.getChannelGroups();
      setChannelGroups(groups);
    } catch (err) {
      console.error('Failed to load channel groups:', err);
    }
  };

  const handleDeleteChannelGroup = async (groupId: number) => {
    await api.deleteChannelGroup(groupId);
    // Immediately update local state to reflect deletion
    setChannelGroups((prev) => prev.filter((g) => g.id !== groupId));
    // Also reload channels since they may have been moved to ungrouped
    await loadChannels();
  };

  const loadProviderGroupSettings = async () => {
    try {
      const settings = await api.getProviderGroupSettings();
      setProviderGroupSettings(settings);
    } catch (err) {
      console.error('Failed to load provider group settings:', err);
    }
  };

  const updateChannelListFilters = useCallback((updates: Partial<ChannelListFilterSettings>) => {
    setChannelListFilters((prev) => {
      const newFilters = { ...prev, ...updates };
      localStorage.setItem('channelListFilters', JSON.stringify(newFilters));
      return newFilters;
    });
  }, []);

  const trackNewlyCreatedGroup = useCallback((groupId: number) => {
    setNewlyCreatedGroupIds((prev) => new Set([...prev, groupId]));
  }, []);

  const loadChannels = async () => {
    setChannelsLoading(true);
    try {
      // Fetch all pages of channels
      const allChannels: Channel[] = [];
      let page = 1;
      let hasMore = true;

      while (hasMore) {
        const response = await api.getChannels({
          page,
          pageSize: 500,
          search: channelSearch || undefined,
        });
        allChannels.push(...response.results);
        hasMore = response.next !== null;
        page++;
      }

      setChannels(allChannels);
    } catch (err) {
      console.error('Failed to load channels:', err);
    } finally {
      setChannelsLoading(false);
    }
  };

  const loadProviders = async () => {
    try {
      const accounts = await api.getM3UAccounts();
      setProviders(accounts);
    } catch (err) {
      console.error('Failed to load providers:', err);
    }
  };

  const loadStreamGroups = async () => {
    try {
      const groups = await api.getStreamGroups();
      setStreamGroups(groups);
    } catch (err) {
      console.error('Failed to load stream groups:', err);
    }
  };

  const loadLogos = async () => {
    try {
      // Fetch all logos
      const allLogos: Logo[] = [];
      let page = 1;
      let hasMore = true;

      while (hasMore) {
        const response = await api.getLogos({ page, pageSize: 500 });
        allLogos.push(...response.results);
        hasMore = response.next !== null;
        page++;
      }

      setLogos(allLogos);
    } catch (err) {
      console.error('Failed to load logos:', err);
    }
  };

  const loadStreamProfiles = async () => {
    try {
      const profiles = await api.getStreamProfiles();
      setStreamProfiles(profiles);
    } catch (err) {
      console.error('Failed to load stream profiles:', err);
    }
  };

  const loadEpgSources = async () => {
    try {
      const sources = await api.getEPGSources();
      setEpgSources(sources);
    } catch (err) {
      console.error('Failed to load EPG sources:', err);
    }
  };

  const loadEpgData = async () => {
    setEpgDataLoading(true);
    try {
      const data = await api.getEPGData();
      setEpgData(data);
    } catch (err) {
      console.error('Failed to load EPG data:', err);
    } finally {
      setEpgDataLoading(false);
    }
  };

  const loadStreams = async (bypassCache: boolean = false) => {
    setStreamsLoading(true);
    try {
      // Fetch all pages of streams (like channels)
      const allStreams: Stream[] = [];
      let page = 1;
      let hasMore = true;

      while (hasMore) {
        const response = await api.getStreams({
          page,
          pageSize: 500,
          search: streamSearch || undefined,
          m3uAccount: streamProviderFilter ?? undefined,
          channelGroup: streamGroupFilter ?? undefined,
          bypassCache,
        });
        allStreams.push(...response.results);
        hasMore = response.next !== null;
        page++;
      }

      setStreams(allStreams);
    } catch (err) {
      console.error('Failed to load streams:', err);
    } finally {
      setStreamsLoading(false);
    }
  };

  // Force refresh streams from Dispatcharr (bypassing cache)
  const refreshStreams = useCallback(() => {
    loadStreams(true);
  }, [streamSearch, streamProviderFilter, streamGroupFilter]);

  // Reload channels when search changes
  useEffect(() => {
    const timer = setTimeout(() => {
      loadChannels();
    }, 300); // Debounce
    return () => clearTimeout(timer);
  }, [channelSearch]);

  // Reload streams when filters change
  useEffect(() => {
    const timer = setTimeout(() => {
      loadStreams();
    }, 300); // Debounce
    return () => clearTimeout(timer);
  }, [streamSearch, streamProviderFilter, streamGroupFilter]);

  // Initialize baseline when channels first load
  useEffect(() => {
    if (channels.length > 0 && !channelsLoading && !baselineInitialized.current) {
      initializeBaseline(channels);
      baselineInitialized.current = true;
    }
  }, [channels, channelsLoading, initializeBaseline]);

  // Keyboard shortcuts for undo/redo
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Don't trigger when typing in inputs
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) {
        return;
      }

      // Cmd/Ctrl+Z for undo
      if ((e.metaKey || e.ctrlKey) && e.key === 'z' && !e.shiftKey) {
        e.preventDefault();
        // In edit mode, use local undo; otherwise global undo
        if (isEditMode) {
          if (canLocalUndo) localUndo();
        } else {
          if (canUndo && !isOperationPending) undo();
        }
      }

      // Cmd/Ctrl+Shift+Z for redo
      if ((e.metaKey || e.ctrlKey) && e.key === 'z' && e.shiftKey) {
        e.preventDefault();
        // In edit mode, use local redo; otherwise global redo
        if (isEditMode) {
          if (canLocalRedo) localRedo();
        } else {
          if (canRedo && !isOperationPending) redo();
        }
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [canUndo, canRedo, isOperationPending, undo, redo, isEditMode, canLocalUndo, canLocalRedo, localUndo, localRedo]);

  // Warn before leaving with unsaved changes or staged edit mode changes
  useEffect(() => {
    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      if (hasUnsavedChanges || (isEditMode && stagedOperationCount > 0)) {
        e.preventDefault();
        e.returnValue = 'You have unsaved changes. Are you sure you want to leave?';
        return e.returnValue;
      }
    };

    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => window.removeEventListener('beforeunload', handleBeforeUnload);
  }, [hasUnsavedChanges, isEditMode, stagedOperationCount]);

  const handleChannelSelect = (channel: Channel | null) => {
    setSelectedChannel(channel);
  };

  // Multi-select handlers
  const handleToggleChannelSelection = useCallback((channelId: number, addToSelection: boolean) => {
    setSelectedChannelIds((prev) => {
      const newSet = new Set(prev);
      if (addToSelection) {
        if (newSet.has(channelId)) {
          newSet.delete(channelId);
        } else {
          newSet.add(channelId);
        }
      } else {
        // Single select - clear others and select this one
        newSet.clear();
        newSet.add(channelId);
      }
      return newSet;
    });
    setLastSelectedChannelId(channelId);
  }, []);

  const handleClearChannelSelection = useCallback(() => {
    setSelectedChannelIds(new Set());
    setLastSelectedChannelId(null);
  }, []);

  const handleSelectChannelRange = useCallback((fromId: number, toId: number, groupChannelIds: number[]) => {
    // Select all channels between fromId and toId within the given group's channels (in display order)
    const fromIndex = groupChannelIds.indexOf(fromId);
    const toIndex = groupChannelIds.indexOf(toId);

    if (fromIndex === -1 || toIndex === -1) return;

    const startIndex = Math.min(fromIndex, toIndex);
    const endIndex = Math.max(fromIndex, toIndex);

    const rangeIds = groupChannelIds.slice(startIndex, endIndex + 1);

    setSelectedChannelIds((prev) => {
      const newSet = new Set(prev);
      rangeIds.forEach((id) => newSet.add(id));
      return newSet;
    });
    setLastSelectedChannelId(toId);
  }, []);

  const handleSelectGroupChannels = useCallback((channelIds: number[], select: boolean) => {
    setSelectedChannelIds((prev) => {
      const newSet = new Set(prev);
      if (select) {
        // Add all channels in the group
        channelIds.forEach((id) => newSet.add(id));
      } else {
        // Remove all channels in the group
        channelIds.forEach((id) => newSet.delete(id));
      }
      return newSet;
    });
    // Set last selected to the first channel in the group if selecting
    if (select && channelIds.length > 0) {
      setLastSelectedChannelId(channelIds[0]);
    }
  }, []);

  const handleChannelUpdate = useCallback(
    (updatedChannel: Channel, changeInfo?: ChangeInfo) => {
      const originalChannel = channels.find((ch) => ch.id === updatedChannel.id);

      // Record change if change info provided and original channel exists
      if (changeInfo && originalChannel) {
        recordChange({
          type: changeInfo.type,
          description: changeInfo.description,
          channelIds: [updatedChannel.id],
          before: [
            {
              id: originalChannel.id,
              channel_number: originalChannel.channel_number,
              name: originalChannel.name,
              channel_group_id: originalChannel.channel_group_id,
              streams: [...originalChannel.streams],
            },
          ],
          after: [
            {
              id: updatedChannel.id,
              channel_number: updatedChannel.channel_number,
              name: updatedChannel.name,
              channel_group_id: updatedChannel.channel_group_id,
              streams: [...updatedChannel.streams],
            },
          ],
        });
      }

      setChannels((prev) =>
        prev.map((ch) => (ch.id === updatedChannel.id ? updatedChannel : ch))
      );
      if (selectedChannel?.id === updatedChannel.id) {
        setSelectedChannel(updatedChannel);
      }
    },
    [selectedChannel, channels, recordChange]
  );

  const handleStreamDropOnChannel = useCallback(
    async (channelId: number, streamId: number) => {
      // Require edit mode for stream operations
      if (!isEditMode) return;

      const originalChannel = displayChannels.find((ch) => ch.id === channelId);
      if (!originalChannel) return;

      const description = `Added stream to "${originalChannel.name}"`;
      stageAddStream(channelId, streamId, description);
    },
    [displayChannels, isEditMode, stageAddStream]
  );

  const handleBulkStreamDropOnChannel = useCallback(
    async (channelId: number, streamIds: number[]) => {
      // Require edit mode for stream operations
      if (!isEditMode) return;

      const originalChannel = displayChannels.find((ch) => ch.id === channelId);
      if (!originalChannel) return;

      // Stage each stream add operation
      for (const streamId of streamIds) {
        stageAddStream(channelId, streamId, `Added stream to "${originalChannel.name}"`);
      }
    },
    [displayChannels, isEditMode, stageAddStream]
  );

  const handleCreateChannel = useCallback(
    async (name: string, channelNumber?: number, groupId?: number) => {
      try {
        const newChannel = await api.createChannel({
          name,
          channel_number: channelNumber,
          channel_group_id: groupId,
        });
        setChannels((prev) => [...prev, newChannel]);
        // In edit mode, also add to the working copy so it can be used immediately
        if (isEditMode) {
          addChannelToWorkingCopy(newChannel);
        }
        return newChannel;
      } catch (err) {
        console.error('Failed to create channel:', err);
        setError('Failed to create channel');
        throw err;
      }
    },
    [isEditMode, addChannelToWorkingCopy]
  );

  const handleBulkCreateFromGroup = useCallback(
    async (
      streamsToCreate: Stream[],
      startingNumber: number,
      channelGroupId: number | null,
      newGroupName?: string,
      timezonePreference?: api.TimezonePreference,
      stripCountryPrefix?: boolean,
      addChannelNumber?: boolean,
      numberSeparator?: api.NumberSeparator,
      keepCountryPrefix?: boolean,
      countrySeparator?: api.NumberSeparator,
      prefixOrder?: api.PrefixOrder,
      stripNetworkPrefix?: boolean
    ) => {
      try {
        // If we need to create a new group first
        let targetGroupId = channelGroupId;
        let newGroupCreated = false;
        if (newGroupName) {
          const newGroup = await api.createChannelGroup(newGroupName);
          targetGroupId = newGroup.id;
          newGroupCreated = true;
          // Refresh channel groups
          const updatedGroups = await api.getChannelGroups();
          setChannelGroups(updatedGroups);
        }

        // Create channels with streams (include logo_url for auto-assignment)
        const result = await api.bulkCreateChannelsFromStreams(
          streamsToCreate.map(s => ({ id: s.id, name: s.name, logo_url: s.logo_url })),
          startingNumber,
          targetGroupId,
          {
            timezonePreference: timezonePreference ?? 'both',
            stripCountryPrefix: stripCountryPrefix ?? false,
            keepCountryPrefix: keepCountryPrefix ?? false,
            countrySeparator: countrySeparator ?? '|',
            stripNetworkPrefix: stripNetworkPrefix ?? false,
            addChannelNumber: addChannelNumber ?? false,
            numberSeparator: numberSeparator ?? '|',
            prefixOrder: prefixOrder ?? 'number-first',
          }
        );

        // Update channels state with new channels
        if (result.created.length > 0) {
          setChannels((prev) => [...prev, ...result.created]);

          // In edit mode, also add to working copy
          if (isEditMode) {
            result.created.forEach(ch => addChannelToWorkingCopy(ch));
          }
        }

        // Show results
        const mergeInfo = result.mergedCount > 0
          ? `\n(${result.mergedCount} streams merged from duplicate names)`
          : '';
        if (result.errors.length > 0) {
          alert(`Created ${result.created.length} channels.${mergeInfo}\n\nErrors:\n${result.errors.join('\n')}`);
        } else {
          alert(`Successfully created ${result.created.length} channels!${mergeInfo}`);
        }

        // Refresh channel groups to update counts
        const updatedGroups = await api.getChannelGroups();
        setChannelGroups(updatedGroups);

        // If a new group was created or we used an existing group, add it to the visible filter
        if (targetGroupId !== null) {
          setChannelGroupFilter((prev) => {
            if (!prev.includes(targetGroupId!)) {
              return [...prev, targetGroupId!];
            }
            return prev;
          });
        }

        // Track as newly created if it was a new group
        if (newGroupCreated && targetGroupId !== null) {
          trackNewlyCreatedGroup(targetGroupId);
        }

      } catch (err) {
        console.error('Bulk create failed:', err);
        setError('Failed to bulk create channels');
        throw err;
      }
    },
    [isEditMode, addChannelToWorkingCopy, trackNewlyCreatedGroup]
  );

  // Handle stream group drop on channels pane (triggers bulk create modal in streams pane)
  const handleStreamGroupDrop = useCallback((groupName: string, _streamIds: number[]) => {
    // Set the dropped group name - StreamsPane will react to this and open the modal
    setDroppedStreamGroupName(groupName);
  }, []);

  // Clear the dropped stream group trigger after it's been handled
  const handleStreamGroupTriggerHandled = useCallback(() => {
    setDroppedStreamGroupName(null);
  }, []);

  // Filter streams based on multi-select filters (client-side)
  const filteredStreams = useMemo(() => {
    let result = streams;

    // Filter by selected providers
    if (selectedProviderFilters.length > 0) {
      result = result.filter((s) => s.m3u_account !== null && selectedProviderFilters.includes(s.m3u_account));
    }

    // Filter by selected stream groups
    if (selectedStreamGroupFilters.length > 0) {
      result = result.filter((s) => selectedStreamGroupFilters.includes(s.channel_group_name || ''));
    }

    return result;
  }, [streams, selectedProviderFilters, selectedStreamGroupFilters]);

  const handleDeleteChannel = useCallback(
    async (channelId: number) => {
      try {
        await api.deleteChannel(channelId);
        setChannels((prev) => prev.filter((ch) => ch.id !== channelId));
        if (selectedChannel?.id === channelId) {
          setSelectedChannel(null);
        }
      } catch (err) {
        console.error('Failed to delete channel:', err);
        setError('Failed to delete channel');
        throw err;
      }
    },
    [selectedChannel]
  );

  const handleChannelReorder = useCallback(
    async (channelIds: number[], startingNumber: number) => {
      // Use displayChannels in edit mode, channels in normal mode
      const channelSource = isEditMode ? displayChannels : channels;

      // Capture before state for all affected channels
      const beforeSnapshots = channelIds.map((id) => {
        const ch = channelSource.find((c) => c.id === id)!;
        return {
          id: ch.id,
          channel_number: ch.channel_number,
          name: ch.name,
          channel_group_id: ch.channel_group_id,
          streams: [...ch.streams],
        };
      });

      // Calculate after state
      const afterSnapshots = channelIds.map((id, index) => {
        const ch = channelSource.find((c) => c.id === id)!;
        return {
          id: ch.id,
          channel_number: startingNumber + index,
          name: ch.name,
          channel_group_id: ch.channel_group_id,
          streams: [...ch.streams],
        };
      });

      const description = `Reordered ${channelIds.length} channel${channelIds.length > 1 ? 's' : ''} starting at ${startingNumber}`;

      if (isEditMode) {
        // Stage the bulk assign operation
        stageBulkAssignNumbers(channelIds, startingNumber, description);
      } else {
        // Normal mode - call API directly
        try {
          await api.bulkAssignChannelNumbers(channelIds, startingNumber);

          // Record the change
          recordChange({
            type: 'channel_reorder',
            description,
            channelIds,
            before: beforeSnapshots,
            after: afterSnapshots,
          });

          // Reload channels to get updated numbers from server
          loadChannels();
        } catch (err) {
          console.error('Failed to reorder channels:', err);
          setError('Failed to reorder channels');
          // Reload to revert optimistic update
          loadChannels();
        }
      }
    },
    [channels, displayChannels, isEditMode, stageBulkAssignNumbers, recordChange]
  );

  // Format duration for display
  const formatDuration = (seconds: number): string => {
    if (seconds < 60) return `${seconds}s`;
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return secs > 0 ? `${mins}m ${secs}s` : `${mins}m`;
  };

  return (
    <div className="app">
      <header className={`header ${isEditMode ? 'edit-mode-active' : ''}`}>
        <h1>Enhanced Channel Manager</h1>
        <div className="header-actions">
          {/* Edit Mode Controls - only show on Channel Manager tab */}
          {activeTab === 'channel-manager' && (
            <>
              {isEditMode ? (
                <div className="edit-mode-header-controls">
                  <span className="edit-mode-label">
                    <span className="material-icons" style={{ fontSize: '18px', marginRight: '4px' }}>edit</span>
                    Edit Mode
                  </span>
                  {stagedOperationCount > 0 && (
                    <span className="edit-mode-changes">
                      {stagedOperationCount} change{stagedOperationCount !== 1 ? 's' : ''}
                    </span>
                  )}
                  {editModeDuration !== null && (
                    <span className="edit-mode-timer">
                      ({formatDuration(editModeDuration)})
                    </span>
                  )}
                  <div className="edit-mode-buttons">
                    <button
                      className="edit-mode-done-btn"
                      onClick={handleExitEditMode}
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
                      onClick={() => {
                        if (stagedOperationCount > 0) {
                          if (confirm(`You have ${stagedOperationCount} pending change${stagedOperationCount !== 1 ? 's' : ''} that will be lost. Are you sure you want to cancel?`)) {
                            discard();
                          }
                        } else {
                          discard();
                        }
                      }}
                      disabled={isCommitting}
                      title="Cancel and discard changes"
                    >
                      <span className="material-icons" style={{ fontSize: '16px', marginRight: '4px' }}>close</span>
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <button
                  className="enter-edit-mode-btn"
                  onClick={enterEditMode}
                  title="Enter Edit Mode to make changes"
                >
                  <span className="material-icons" style={{ fontSize: '16px', marginRight: '4px' }}>edit</span>
                  Edit Mode
                </button>
              )}
            </>
          )}
        </div>
      </header>

      <TabNavigation
        activeTab={activeTab}
        onTabChange={handleTabChange}
        disabled={isCommitting}
        editModeActive={isEditMode}
      />

      <EditModeExitDialog
        isOpen={showExitDialog}
        summary={getSummary()}
        onApply={handleApplyChanges}
        onDiscard={handleDiscardChanges}
        onKeepEditing={handleKeepEditing}
        isCommitting={isCommitting}
      />

      {/* Keep SettingsModal for first-run configuration */}
      <SettingsModal
        isOpen={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        onSaved={handleSettingsSaved}
      />

      <main className="main">
        <Suspense fallback={<div className="tab-loading"><span className="material-icons">hourglass_empty</span> Loading...</div>}>
          {activeTab === 'channel-manager' && (
            <ChannelManagerTab
              // Channel Groups
              channelGroups={channelGroups}
              onChannelGroupsChange={loadChannelGroups}
              onDeleteChannelGroup={handleDeleteChannelGroup}

              // Channels
              channels={displayChannels}
              selectedChannelId={selectedChannel?.id ?? null}
              onChannelSelect={handleChannelSelect}
              onChannelUpdate={handleChannelUpdate}
              onChannelDrop={handleStreamDropOnChannel}
              onBulkStreamDrop={handleBulkStreamDropOnChannel}
              onChannelReorder={handleChannelReorder}
              onCreateChannel={handleCreateChannel}
              onDeleteChannel={handleDeleteChannel}
              channelsLoading={channelsLoading}

              // Channel Search & Filter
              channelSearch={channelSearch}
              onChannelSearchChange={setChannelSearch}
              selectedGroups={channelGroupFilter}
              onSelectedGroupsChange={setChannelGroupFilter}

              // Multi-select
              selectedChannelIds={selectedChannelIds}
              lastSelectedChannelId={lastSelectedChannelId}
              onToggleChannelSelection={handleToggleChannelSelection}
              onClearChannelSelection={handleClearChannelSelection}
              onSelectChannelRange={handleSelectChannelRange}
              onSelectGroupChannels={handleSelectGroupChannels}

              // Auto-rename
              autoRenameChannelNumber={autoRenameChannelNumber}

              // Edit Mode
              isEditMode={isEditMode}
              isCommitting={isCommitting}
              modifiedChannelIds={modifiedChannelIds}
              onStageUpdateChannel={stageUpdateChannel}
              onStageAddStream={stageAddStream}
              onStageRemoveStream={stageRemoveStream}
              onStageReorderStreams={stageReorderStreams}
              onStageBulkAssignNumbers={stageBulkAssignNumbers}
              onStageDeleteChannel={stageDeleteChannel}
              onStageDeleteChannelGroup={stageDeleteChannelGroup}
              onStartBatch={startBatch}
              onEndBatch={endBatch}

              // History
              canUndo={isEditMode ? canLocalUndo : canUndo}
              canRedo={isEditMode ? canLocalRedo : canRedo}
              undoCount={isEditMode ? stagedOperationCount : undoCount}
              redoCount={isEditMode ? 0 : redoCount}
              lastChange={lastChange}
              savePoints={savePoints}
              hasUnsavedChanges={hasUnsavedChanges}
              isOperationPending={isOperationPending}
              onUndo={isEditMode ? localUndo : undo}
              onRedo={isEditMode ? localRedo : redo}
              onCreateSavePoint={createSavePoint}
              onRevertToSavePoint={revertToSavePoint}
              onDeleteSavePoint={deleteSavePoint}

              // Logos
              logos={logos}
              onLogosChange={loadLogos}

              // EPG & Stream Profiles
              epgData={epgData}
              epgSources={epgSources}
              streamProfiles={streamProfiles}
              epgDataLoading={epgDataLoading}

              // Provider & Filter Settings
              providerGroupSettings={providerGroupSettings}
              channelListFilters={channelListFilters}
              onChannelListFiltersChange={updateChannelListFilters}
              newlyCreatedGroupIds={newlyCreatedGroupIds}
              onTrackNewlyCreatedGroup={trackNewlyCreatedGroup}

              // Streams
              streams={filteredStreams}
              providers={providers}
              streamGroups={streamGroups}
              streamsLoading={streamsLoading}

              // Stream Search & Filter
              streamSearch={streamSearch}
              onStreamSearchChange={setStreamSearch}
              streamProviderFilter={streamProviderFilter}
              onStreamProviderFilterChange={setStreamProviderFilter}
              streamGroupFilter={streamGroupFilter}
              onStreamGroupFilterChange={setStreamGroupFilter}
              selectedProviders={selectedProviderFilters}
              onSelectedProvidersChange={setSelectedProviderFilters}
              selectedStreamGroups={selectedStreamGroupFilters}
              onSelectedStreamGroupsChange={setSelectedStreamGroupFilters}

              // Bulk Create
              channelDefaults={channelDefaults}
              externalTriggerGroupName={droppedStreamGroupName}
              onExternalTriggerHandled={handleStreamGroupTriggerHandled}
              onStreamGroupDrop={handleStreamGroupDrop}
              onBulkCreateFromGroup={handleBulkCreateFromGroup}

              // Dispatcharr URL for channel stream URLs
              dispatcharrUrl={dispatcharrUrl}

              // Appearance settings
              showStreamUrls={showStreamUrls}

              // Refresh streams (bypasses cache)
              onRefreshStreams={refreshStreams}
            />
          )}
          {activeTab === 'm3u-manager' && <M3UManagerTab />}
          {activeTab === 'epg-manager' && <EPGManagerTab />}
          {activeTab === 'logo-manager' && <LogoManagerTab />}
          {activeTab === 'settings' && <SettingsTab onSaved={handleSettingsSaved} />}
        </Suspense>
      </main>

      <footer className="footer">
        <div className="footer-left">
          {error && <span className="error">API Error: {error}</span>}
          {health && (
            <span className="status">
              API: {health.status} | Service: {health.service}
            </span>
          )}
        </div>
        <div className="footer-right">
          <span className="version">v{packageJson.version}</span>
        </div>
      </footer>
    </div>
  );
}

export default App;
