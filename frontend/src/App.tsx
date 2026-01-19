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
import type { Channel, ChannelGroup, ChannelProfile, Stream, M3UAccount, M3UGroupSetting, Logo, ChangeInfo, EPGData, StreamProfile, EPGSource, ChannelListFilterSettings, CommitProgress } from './types';
import packageJson from '../package.json';
import { logger } from './utils/logger';
import { registerVLCModalCallback, downloadM3U } from './utils/vlc';
import { VLCProtocolHelperModal } from './components/VLCProtocolHelperModal';
import { NotificationCenter } from './components/NotificationCenter';
import { NotificationProvider } from './contexts/NotificationContext';
import ECMLogo from './assets/ECMLogo.png';
import './App.css';

// Lazy load non-primary tabs
const M3UManagerTab = lazy(() => import('./components/tabs/M3UManagerTab').then(m => ({ default: m.M3UManagerTab })));
const EPGManagerTab = lazy(() => import('./components/tabs/EPGManagerTab').then(m => ({ default: m.EPGManagerTab })));
const GuideTab = lazy(() => import('./components/tabs/GuideTab').then(m => ({ default: m.GuideTab })));
const LogoManagerTab = lazy(() => import('./components/tabs/LogoManagerTab').then(m => ({ default: m.LogoManagerTab })));
const JournalTab = lazy(() => import('./components/tabs/JournalTab').then(m => ({ default: m.JournalTab })));
const StatsTab = lazy(() => import('./components/tabs/StatsTab').then(m => ({ default: m.StatsTab })));
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
  const [channelToEditFromGuide, setChannelToEditFromGuide] = useState<Channel | null>(null);

  // Channel filters - grouped state
  const [channelFilters, setChannelFilters] = useState({
    search: '',
    groupFilter: [] as number[],
  });

  // Streams state
  const [streams, setStreams] = useState<Stream[]>([]);
  const [providers, setProviders] = useState<M3UAccount[]>([]);
  const [streamGroups, setStreamGroups] = useState<string[]>([]);

  // Stream filters - grouped state (with localStorage initialization)
  const [streamFilters, setStreamFilters] = useState(() => {
    const savedProviders = localStorage.getItem('streamProviderFilters');
    const savedGroups = localStorage.getItem('streamGroupFilters');
    return {
      search: '',
      providerFilter: null as number | null,
      groupFilter: null as string | null,
      selectedProviders: savedProviders ? JSON.parse(savedProviders) : [] as number[],
      selectedGroups: savedGroups ? JSON.parse(savedGroups) : [] as string[],
    };
  });

  // Logos state
  const [logos, setLogos] = useState<Logo[]>([]);

  // EPG Data, EPG Sources, Stream Profiles, and Channel Profiles state
  const [epgData, setEpgData] = useState<EPGData[]>([]);
  const [epgSources, setEpgSources] = useState<EPGSource[]>([]);
  const [streamProfiles, setStreamProfiles] = useState<StreamProfile[]>([]);
  const [channelProfiles, setChannelProfiles] = useState<ChannelProfile[]>([]);

  // Loading states - grouped state
  const [loadingStates, setLoadingStates] = useState({
    channels: true,
    streams: true,
    epgData: false,
  });

  // Settings state
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [autoRenameChannelNumber, setAutoRenameChannelNumber] = useState(false);
  const [dispatcharrUrl, setDispatcharrUrl] = useState('');
  const [showStreamUrls, setShowStreamUrls] = useState(true);
  const [hideUngroupedStreams, setHideUngroupedStreams] = useState(true);
  const [hideEpgUrls, setHideEpgUrls] = useState(false);
  const [hideM3uUrls, setHideM3uUrls] = useState(false);
  const [gracenoteConflictMode, setGracenoteConflictMode] = useState<'ask' | 'skip' | 'overwrite'>('ask');
  const [epgAutoMatchThreshold, setEpgAutoMatchThreshold] = useState(80);
  const [showVLCHelperModal, setShowVLCHelperModal] = useState(false);
  const [vlcModalStreamUrl, setVlcModalStreamUrl] = useState('');
  const [vlcModalStreamName, setVlcModalStreamName] = useState('');
  const [channelDefaults, setChannelDefaults] = useState({
    includeChannelNumberInName: false,
    channelNumberSeparator: '-',
    removeCountryPrefix: false,
    includeCountryInName: false,
    countrySeparator: '|',
    timezonePreference: 'both',
    defaultChannelProfileIds: [] as number[],
    customNetworkPrefixes: [] as string[],
    streamSortPriority: ['resolution', 'bitrate', 'framerate'] as ('resolution' | 'bitrate' | 'framerate')[],
    streamSortEnabled: { resolution: true, bitrate: true, framerate: true } as Record<'resolution' | 'bitrate' | 'framerate', boolean>,
    deprioritizeFailedStreams: true,
  });
  // Also keep separate state for use in callbacks (to avoid stale closure issues)
  const [defaultChannelProfileIds, setDefaultChannelProfileIds] = useState<number[]>([]);

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

  // Pending profile assignments (to be applied after commit)
  // Stores { startNumber, count, profileIds, increment } for each bulk create
  const pendingProfileAssignmentsRef = useRef<Array<{ startNumber: number; count: number; profileIds: number[]; increment: number }>>([]);

  // Track if baseline has been initialized
  const baselineInitialized = useRef(false);

  // Track if channel group filter has been auto-initialized
  const channelGroupFilterInitialized = useRef(false);

  // Edit mode exit dialog state
  const [showExitDialog, setShowExitDialog] = useState(false);
  const [commitProgress, setCommitProgress] = useState<CommitProgress | null>(null);

  // Tab navigation state
  const [activeTab, setActiveTab] = useState<TabId>('channel-manager');
  const [pendingTabChange, setPendingTabChange] = useState<TabId | null>(null);

  // Stream group drop trigger (for opening bulk create modal from channels pane)
  // Supports multiple groups being dropped at once
  const [droppedStreamGroupNames, setDroppedStreamGroupNames] = useState<string[] | null>(null);
  // Stream IDs drop trigger (for opening bulk create modal when dropping individual streams)
  // Includes target group ID and starting channel number for pre-filling the modal
  const [droppedStreamIds, setDroppedStreamIds] = useState<number[] | null>(null);
  const [droppedStreamTargetGroupId, setDroppedStreamTargetGroupId] = useState<number | null>(null);
  const [droppedStreamStartingNumber, setDroppedStreamStartingNumber] = useState<number | null>(null);

  // Edit mode for staging changes
  const {
    isEditMode,
    isCommitting,
    stagedOperationCount,
    modifiedChannelIds,
    displayChannels,
    stagedGroups,
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
    stageCreateChannel,
    stageDeleteChannel,
    stageDeleteChannelGroup,
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
    onCommitComplete: async (createdGroupIds) => {
      // Refresh data from server
      await Promise.all([
        loadChannels(),
        loadChannelGroups(),
        loadLogos(),
      ]);

      // Add newly created groups to the filter so they're visible
      if (createdGroupIds.length > 0) {
        setChannelFilters((prev) => {
          const newIds = createdGroupIds.filter(id => !prev.groupFilter.includes(id));
          if (newIds.length > 0) {
            return { ...prev, groupFilter: [...prev.groupFilter, ...newIds] };
          }
          return prev;
        });
      }

      // Apply pending profile assignments
      if (pendingProfileAssignmentsRef.current.length > 0) {
        try {
          // Get fresh channel list to find channels by number
          const freshChannels = await api.getChannels({ page: 1, pageSize: 5000 });
          const channelsByNumber = new Map<number, Channel>();
          for (const ch of freshChannels.results) {
            if (ch.channel_number !== null) {
              channelsByNumber.set(ch.channel_number, ch);
            }
          }

          // Get all profile IDs for disabling channels in non-selected profiles
          const freshProfiles = await api.getChannelProfiles();
          const allProfileIds = freshProfiles.map(p => p.id);

          // Process each pending assignment
          for (const assignment of pendingProfileAssignmentsRef.current) {
            const { startNumber, count, profileIds, increment } = assignment;
            const channelIds: number[] = [];

            // Find channels by number range using the correct increment (integer or decimal)
            for (let i = 0; i < count; i++) {
              const rawNumber = startNumber + i * increment;
              // Round to 1 decimal place to handle floating point precision
              const channelNumber = increment < 1 ? Math.round(rawNumber * 10) / 10 : rawNumber;
              const channel = channelsByNumber.get(channelNumber);
              if (channel) {
                channelIds.push(channel.id);
              }
            }

            // Enable channels in selected profiles
            for (const profileId of profileIds) {
              for (const channelId of channelIds) {
                try {
                  await api.updateProfileChannel(profileId, channelId, { enabled: true });
                } catch (err) {
                  logger.warn(`Failed to enable channel ${channelId} in profile ${profileId}:`, err);
                }
              }
            }

            // Disable channels in non-selected profiles
            // (Dispatcharr may auto-enable new channels in all profiles)
            const nonSelectedProfileIds = allProfileIds.filter(id => !profileIds.includes(id));
            for (const profileId of nonSelectedProfileIds) {
              for (const channelId of channelIds) {
                try {
                  await api.updateProfileChannel(profileId, channelId, { enabled: false });
                } catch (err) {
                  logger.warn(`Failed to disable channel ${channelId} in profile ${profileId}:`, err);
                }
              }
            }
          }

          // Clear pending assignments
          pendingProfileAssignmentsRef.current = [];

          // Refresh channel profiles to reflect changes
          loadChannelProfiles();
        } catch (err) {
          logger.error('Failed to apply profile assignments:', err);
        }
      }
    },
    onError: setError,
  });

  // Auto-add staged groups to the channel group filter so they're visible
  // Also clean up temp group IDs (negative) when edit mode ends
  useEffect(() => {
    if (stagedGroups.length > 0) {
      // Add new staged groups to filter
      const stagedGroupIds = stagedGroups.map(g => g.id);
      setChannelFilters((prev) => {
        const newIds = stagedGroupIds.filter(id => !prev.groupFilter.includes(id));
        if (newIds.length > 0) {
          return { ...prev, groupFilter: [...prev.groupFilter, ...newIds] };
        }
        return prev;
      });
    } else if (!isEditMode) {
      // Edit mode ended - clean up any temp group IDs (negative numbers)
      setChannelFilters((prev) => ({
        ...prev,
        groupFilter: prev.groupFilter.filter(id => id >= 0)
      }));
    }
  }, [stagedGroups, isEditMode]);

  // Wrap exit to show dialog if there are staged changes
  const handleExitEditMode = useCallback(() => {
    if (stagedOperationCount > 0) {
      setShowExitDialog(true);
    } else {
      rawExitEditMode();
      setSelectedChannelIds(new Set());
    }
  }, [stagedOperationCount, rawExitEditMode]);

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
    clearHistory,
  } = useChangeHistory({
    channels,
    onChannelsRestore: setChannels,
    onError: setError,
  });

  // Handle dialog actions
  const handleApplyChanges = useCallback(async () => {
    setCommitProgress({ current: 0, total: 1, currentOperation: 'Starting...' });
    await commit((progress) => {
      setCommitProgress(progress);
    });
    setCommitProgress(null);
    setShowExitDialog(false);
    // Clear checkpoints when exiting edit mode
    clearHistory();
    // Switch to pending tab if there was one
    if (pendingTabChange) {
      setActiveTab(pendingTabChange);
      setPendingTabChange(null);
    }
  }, [commit, clearHistory, pendingTabChange]);

  const handleDiscardChanges = useCallback(() => {
    discard();
    setSelectedChannelIds(new Set());
    setShowExitDialog(false);
    // Clear checkpoints when exiting edit mode
    clearHistory();
    // Switch to pending tab if there was one
    if (pendingTabChange) {
      setActiveTab(pendingTabChange);
      setPendingTabChange(null);
    }
  }, [discard, clearHistory, pendingTabChange]);

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
      setSelectedChannelIds(new Set());
    }

    setActiveTab(newTab);
  }, [isEditMode, stagedOperationCount, rawExitEditMode]);

  // Check settings and load initial data
  useEffect(() => {
    const init = async () => {
      logger.info('Initializing Enhanced Channel Manager', { version: packageJson.version });

      try {
        const settings = await api.getSettings();
        logger.info('Settings loaded', { configured: settings.configured, theme: settings.theme });

        setAutoRenameChannelNumber(settings.auto_rename_channel_number);
        setDispatcharrUrl(settings.url);
        setShowStreamUrls(settings.show_stream_urls);
        setHideUngroupedStreams(settings.hide_ungrouped_streams);
        setHideEpgUrls(settings.hide_epg_urls ?? false);
        setHideM3uUrls(settings.hide_m3u_urls ?? false);
        setGracenoteConflictMode(settings.gracenote_conflict_mode || 'ask');
        setEpgAutoMatchThreshold(settings.epg_auto_match_threshold ?? 80);
        // Store VLC settings globally for vlc utility to access
        const vlcBehavior = (settings.vlc_open_behavior as 'protocol_only' | 'm3u_fallback' | 'm3u_only') || 'm3u_fallback';
        (window as any).__vlcSettings = { behavior: vlcBehavior };
        setChannelDefaults({
          includeChannelNumberInName: settings.include_channel_number_in_name,
          channelNumberSeparator: settings.channel_number_separator,
          removeCountryPrefix: settings.remove_country_prefix,
          includeCountryInName: settings.include_country_in_name,
          countrySeparator: settings.country_separator,
          timezonePreference: settings.timezone_preference,
          defaultChannelProfileIds: settings.default_channel_profile_ids,
          customNetworkPrefixes: settings.custom_network_prefixes ?? [],
          streamSortPriority: settings.stream_sort_priority ?? ['resolution', 'bitrate', 'framerate'],
          streamSortEnabled: settings.stream_sort_enabled ?? { resolution: true, bitrate: true, framerate: true },
          deprioritizeFailedStreams: settings.deprioritize_failed_streams ?? true,
        });
        setDefaultChannelProfileIds(settings.default_channel_profile_ids);

        // Apply hide_auto_sync_groups setting to channelListFilters
        setChannelListFilters(prev => ({
          ...prev,
          showAutoChannelGroups: !settings.hide_auto_sync_groups,
        }));

        // Apply theme setting
        if (settings.theme && settings.theme !== 'dark') {
          document.documentElement.setAttribute('data-theme', settings.theme);
          logger.debug(`Applied theme: ${settings.theme}`);
        }

        // Apply log levels from settings
        if (settings.frontend_log_level) {
          const frontendLevel = settings.frontend_log_level === 'WARNING' ? 'WARN' : settings.frontend_log_level;
          if (['DEBUG', 'INFO', 'WARN', 'ERROR'].includes(frontendLevel)) {
            logger.setLevel(frontendLevel as 'DEBUG' | 'INFO' | 'WARN' | 'ERROR');
            logger.info(`Frontend log level set to ${frontendLevel}`);
          }
        }

        if (!settings.configured) {
          logger.warn('Settings not configured, opening settings modal');
          setSettingsOpen(true);
          return;
        }

        logger.debug('Loading initial data...');
        api.getHealth()
          .then(health => {
            setHealth(health);
            logger.info('Health check passed', health);
          })
          .catch((err) => {
            setError(err.message);
            logger.error('Health check failed', err);
          });

        loadChannelGroups();
        loadChannels();
        loadProviders();
        loadProviderGroupSettings();
        loadStreamGroups();
        loadStreams();
        loadLogos();
        loadStreamProfiles();
        loadChannelProfiles();
        loadEpgSources();
        loadEpgData();
      } catch (err) {
        logger.exception('Failed to load settings', err as Error);
        setSettingsOpen(true);
      }
    };
    init();
  }, []);

  // Register VLC modal callback
  useEffect(() => {
    const unregister = registerVLCModalCallback((url, name) => {
      setVlcModalStreamUrl(url);
      setVlcModalStreamName(name || '');
      setShowVLCHelperModal(true);
    });
    return unregister;
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
    setChannelFilters(prev => ({ ...prev, groupFilter: groupIds }));
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
      setChannelFilters(prev => {
        const newSet = new Set(prev.groupFilter);
        autoSyncGroupsWithChannels.forEach(id => newSet.add(id));
        return { ...prev, groupFilter: Array.from(newSet) };
      });
    } else {
      // Remove auto-sync groups from selection
      setChannelFilters(prev => ({
        ...prev,
        groupFilter: prev.groupFilter.filter(id => !autoSyncRelatedGroups.has(id))
      }));
    }
  }, [channelListFilters.showAutoChannelGroups, providerGroupSettings, channels]);

  // Clean up channelGroupFilter when groups are deleted
  useEffect(() => {
    const existingGroupIds = new Set(channelGroups.map(g => g.id));

    setChannelFilters(prev => {
      if (prev.groupFilter.length === 0) return prev;

      const hasDeletedGroups = prev.groupFilter.some(id => !existingGroupIds.has(id));

      // If some group IDs no longer exist, remove them from the filter
      if (hasDeletedGroups) {
        const validGroupIds = prev.groupFilter.filter(id => existingGroupIds.has(id));
        return { ...prev, groupFilter: validGroupIds };
      }

      return prev;
    });
  }, [channelGroups]);

  const handleSettingsSaved = async () => {
    setError(null);
    // Reload settings to get updated values
    try {
      const settings = await api.getSettings();
      setAutoRenameChannelNumber(settings.auto_rename_channel_number);
      setDispatcharrUrl(settings.url);
      setShowStreamUrls(settings.show_stream_urls);
      setHideUngroupedStreams(settings.hide_ungrouped_streams);
      setHideEpgUrls(settings.hide_epg_urls ?? false);
      setHideM3uUrls(settings.hide_m3u_urls ?? false);
      setGracenoteConflictMode(settings.gracenote_conflict_mode || 'ask');
      setEpgAutoMatchThreshold(settings.epg_auto_match_threshold ?? 80);
      setChannelDefaults({
        includeChannelNumberInName: settings.include_channel_number_in_name,
        channelNumberSeparator: settings.channel_number_separator,
        removeCountryPrefix: settings.remove_country_prefix,
        includeCountryInName: settings.include_country_in_name,
        countrySeparator: settings.country_separator,
        timezonePreference: settings.timezone_preference,
        defaultChannelProfileIds: settings.default_channel_profile_ids,
        customNetworkPrefixes: settings.custom_network_prefixes ?? [],
        streamSortPriority: settings.stream_sort_priority ?? ['resolution', 'bitrate', 'framerate'],
        streamSortEnabled: settings.stream_sort_enabled ?? { resolution: true, bitrate: true, framerate: true },
        deprioritizeFailedStreams: settings.deprioritize_failed_streams ?? true,
      });
      setDefaultChannelProfileIds(settings.default_channel_profile_ids);

      // Apply hide_auto_sync_groups setting to channelListFilters
      // The useEffect watching showAutoChannelGroups will handle updating group selection
      setChannelListFilters(prev => ({
        ...prev,
        showAutoChannelGroups: !settings.hide_auto_sync_groups,
      }));
    } catch (err) {
      logger.error('Failed to reload settings:', err);
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
    loadChannelProfiles();
    loadEpgSources();
    loadEpgData();
  };

  const loadChannelGroups = async () => {
    try {
      const groups = await api.getChannelGroups();
      setChannelGroups(groups);
    } catch (err) {
      logger.error('Failed to load channel groups:', err);
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
      logger.error('Failed to load provider group settings:', err);
    }
  };

  const updateChannelListFilters = useCallback((updates: Partial<ChannelListFilterSettings>) => {
    setChannelListFilters((prev) => {
      const newFilters = { ...prev, ...updates };
      localStorage.setItem('channelListFilters', JSON.stringify(newFilters));
      return newFilters;
    });
  }, []);

  // Wrapper functions to persist stream filters to localStorage
  const updateSelectedProviderFilters = useCallback((providerIds: number[]) => {
    setStreamFilters(prev => ({ ...prev, selectedProviders: providerIds }));
    localStorage.setItem('streamProviderFilters', JSON.stringify(providerIds));
  }, []);

  const updateSelectedStreamGroupFilters = useCallback((groups: string[]) => {
    setStreamFilters(prev => ({ ...prev, selectedGroups: groups }));
    localStorage.setItem('streamGroupFilters', JSON.stringify(groups));
  }, []);

  const clearStreamFilters = useCallback(() => {
    setStreamFilters(prev => ({ ...prev, selectedProviders: [], selectedGroups: [] }));
    localStorage.removeItem('streamProviderFilters');
    localStorage.removeItem('streamGroupFilters');
  }, []);

  const trackNewlyCreatedGroup = useCallback((groupId: number) => {
    setNewlyCreatedGroupIds((prev) => new Set([...prev, groupId]));
  }, []);

  const loadChannels = async (signal?: AbortSignal) => {
    setLoadingStates(prev => ({ ...prev, channels: true }));
    try {
      // Fetch all pages of channels
      const allChannels: Channel[] = [];
      let page = 1;
      let hasMore = true;

      while (hasMore) {
        const response = await api.getChannels({
          page,
          pageSize: 500,
          search: channelFilters.search || undefined,
          signal,
        });
        allChannels.push(...response.results);
        hasMore = response.next !== null;
        page++;
      }

      setChannels(allChannels);
    } catch (err) {
      // Don't log errors for aborted requests
      if (err instanceof Error && err.name !== 'AbortError') {
        logger.error('Failed to load channels:', err);
      }
    } finally {
      setLoadingStates(prev => ({ ...prev, channels: false }));
    }
  };

  const loadProviders = async () => {
    try {
      const accounts = await api.getM3UAccounts();
      setProviders(accounts);
    } catch (err) {
      logger.error('Failed to load providers:', err);
    }
  };

  const loadStreamGroups = async () => {
    try {
      const groups = await api.getStreamGroups();
      setStreamGroups(groups);
    } catch (err) {
      logger.error('Failed to load stream groups:', err);
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
      logger.error('Failed to load logos:', err);
    }
  };

  const loadStreamProfiles = async () => {
    try {
      const profiles = await api.getStreamProfiles();
      setStreamProfiles(profiles);
    } catch (err) {
      logger.error('Failed to load stream profiles:', err);
    }
  };

  const loadChannelProfiles = async () => {
    try {
      const profiles = await api.getChannelProfiles();
      setChannelProfiles(profiles);
    } catch (err) {
      logger.error('Failed to load channel profiles:', err);
    }
  };

  const loadEpgSources = async () => {
    try {
      const sources = await api.getEPGSources();
      setEpgSources(sources);
    } catch (err) {
      logger.error('Failed to load EPG sources:', err);
    }
  };

  const loadEpgData = async () => {
    setLoadingStates(prev => ({ ...prev, epgData: true }));
    try {
      const data = await api.getEPGData();
      setEpgData(data);
    } catch (err) {
      logger.error('Failed to load EPG data:', err);
    } finally {
      setLoadingStates(prev => ({ ...prev, epgData: false }));
    }
  };

  const loadStreams = async (bypassCache: boolean = false, signal?: AbortSignal) => {
    setLoadingStates(prev => ({ ...prev, streams: true }));
    try {
      // Fetch all pages of streams (like channels)
      const allStreams: Stream[] = [];
      let page = 1;
      let hasMore = true;

      while (hasMore) {
        const response = await api.getStreams({
          page,
          pageSize: 500,
          search: streamFilters.search || undefined,
          m3uAccount: streamFilters.providerFilter ?? undefined,
          channelGroup: streamFilters.groupFilter ?? undefined,
          bypassCache,
          signal,
        });
        allStreams.push(...response.results);
        hasMore = response.next !== null;
        page++;
      }

      setStreams(allStreams);
    } catch (err) {
      // Don't log errors for aborted requests
      if (err instanceof Error && err.name !== 'AbortError') {
        logger.error('Failed to load streams:', err);
      }
    } finally {
      setLoadingStates(prev => ({ ...prev, streams: false }));
    }
  };

  // Force refresh streams from Dispatcharr (bypassing cache)
  const refreshStreams = useCallback(() => {
    loadStreams(true);
  }, [streamFilters.search, streamFilters.providerFilter, streamFilters.groupFilter]);

  // Reload channels when search changes
  useEffect(() => {
    const abortController = new AbortController();
    const timer = setTimeout(() => {
      loadChannels(abortController.signal);
    }, 500); // Debounce: 500ms for less frequent API requests
    return () => {
      clearTimeout(timer);
      abortController.abort(); // Cancel in-flight request when search changes
    };
  }, [channelFilters.search]);

  // Reload streams when filters change
  useEffect(() => {
    const abortController = new AbortController();
    const timer = setTimeout(() => {
      loadStreams(false, abortController.signal);
    }, 500); // Debounce: 500ms for less frequent API requests
    return () => {
      clearTimeout(timer);
      abortController.abort(); // Cancel in-flight request when filters change
    };
  }, [streamFilters.search, streamFilters.providerFilter, streamFilters.groupFilter]);

  // Initialize baseline when channels first load
  useEffect(() => {
    if (channels.length > 0 && !loadingStates.channels && !baselineInitialized.current) {
      initializeBaseline(channels);
      baselineInitialized.current = true;
    }
  }, [channels, loadingStates.channels, initializeBaseline]);

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

  // Handle channel update from Guide tab edit modal
  const handleGuideChannelUpdate = useCallback(async (channel: Channel, changes: {
    channel_number?: number;
    name?: string;
    logo_id?: number | null;
    tvg_id?: string | null;
    tvc_guide_stationid?: string | null;
    epg_data_id?: number | null;
    stream_profile_id?: number | null;
  }) => {
    try {
      // Update the channel via API
      const updatedChannel = await api.updateChannel(channel.id, changes);

      // Update local state
      setChannels(prev => prev.map(ch =>
        ch.id === channel.id ? { ...ch, ...updatedChannel } : ch
      ));
    } catch (err) {
      logger.error('Failed to update channel from Guide:', err);
      throw err;
    }
  }, []);

  // Clear the external channel edit trigger after it's been handled
  const handleExternalChannelEditHandled = useCallback(() => {
    setChannelToEditFromGuide(null);
  }, []);

  // Handle logo creation from URL (for Guide tab edit modal)
  const handleLogoCreate = useCallback(async (url: string) => {
    const logo = await api.createLogo({ url, name: url.split('/').pop() || 'Logo' });
    return logo;
  }, []);

  // Handle logo upload (for Guide tab edit modal)
  const handleLogoUpload = useCallback(async (file: File) => {
    const logo = await api.uploadLogo(file);
    return logo;
  }, []);

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
    async (name: string, channelNumber?: number, groupId?: number, logoId?: number, tvgId?: string, logoUrl?: string, profileIds?: number[]) => {
      try {
        if (isEditMode) {
          // In edit mode, stage the creation without calling Dispatcharr API
          // Pass logoId, logoUrl, and tvgId so the staged channel has the metadata
          // logoUrl is used as fallback if logoId is not found - the commit will create/find the logo
          const tempId = stageCreateChannel(name, channelNumber, groupId, undefined, logoId, logoUrl, tvgId);

          // Track profile assignments for after commit
          // Use passed profileIds if provided, otherwise fall back to default profiles
          const profilesToAssign = profileIds && profileIds.length > 0
            ? profileIds
            : defaultChannelProfileIds;

          if (profilesToAssign.length > 0 && channelNumber !== undefined) {
            pendingProfileAssignmentsRef.current.push({
              startNumber: channelNumber,
              count: 1,
              profileIds: profilesToAssign,
              increment: 1, // Single channel, increment doesn't matter but include for type consistency
            });
          }

          // Create a temporary channel object to return (for compatibility)
          const tempChannel: Channel = {
            id: tempId,
            channel_number: channelNumber ?? null,
            name,
            channel_group_id: groupId ?? null,
            tvg_id: tvgId ?? null,
            tvc_guide_stationid: null,
            epg_data_id: null,
            streams: [],
            stream_profile_id: null,
            uuid: `temp-${tempId}`,
            logo_id: logoId ?? null,
            auto_created: false,
            auto_created_by: null,
            auto_created_by_name: null,
          };
          return tempChannel;
        } else {
          // Normal mode - create immediately via API
          const newChannel = await api.createChannel({
            name,
            channel_number: channelNumber,
            channel_group_id: groupId,
            logo_id: logoId,
            tvg_id: tvgId,
          });
          setChannels((prev) => [...prev, newChannel]);

          // Apply profile assignments - use passed profileIds if provided, otherwise fall back to defaults
          const profilesToAssign = profileIds && profileIds.length > 0
            ? profileIds
            : defaultChannelProfileIds;

          for (const profileId of profilesToAssign) {
            try {
              await api.updateProfileChannel(profileId, newChannel.id, { enabled: true });
            } catch (err) {
              console.warn(`Failed to add channel ${newChannel.id} to profile ${profileId}:`, err);
            }
          }

          return newChannel;
        }
      } catch (err) {
        logger.error('Failed to create channel:', err);
        setError('Failed to create channel');
        throw err;
      }
    },
    [isEditMode, stageCreateChannel, defaultChannelProfileIds]
  );

  // Check for conflicts with existing channel numbers
  // Returns the count of conflicting channels
  const handleCheckConflicts = useCallback((startingNumber: number, count: number): number => {
    const endNumber = startingNumber + count - 1;
    const conflictingChannels = displayChannels.filter(
      (ch) => ch.channel_number !== null &&
              ch.channel_number >= startingNumber &&
              ch.channel_number <= endNumber
    );
    return conflictingChannels.length;
  }, [displayChannels]);

  // Get the highest existing channel number (for "insert at end" option)
  const handleGetHighestChannelNumber = useCallback((): number => {
    let highest = 0;
    displayChannels.forEach((ch) => {
      if (ch.channel_number !== null && ch.channel_number > highest) {
        highest = ch.channel_number;
      }
    });
    return highest;
  }, [displayChannels]);

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
      stripNetworkPrefix?: boolean,
      customNetworkPrefixes?: string[],
      stripNetworkSuffix?: boolean,
      customNetworkSuffixes?: string[],
      profileIds?: number[],
      pushDownOnConflict?: boolean
    ) => {
      try {
        // Bulk creation requires edit mode
        if (!isEditMode) {
          setError('Bulk channel creation requires edit mode');
          return;
        }

        // Determine target group: either an existing group ID or a new group name
        // If newGroupName is provided, we'll stage the group creation and use newGroupName
        // when staging channels. The commit logic will create the group first and map the ID.
        const targetGroupId = channelGroupId;
        const targetNewGroupName = newGroupName;

        // Create channels locally without calling Dispatcharr API (edit mode only)
        // Build options for filtering/grouping
        const options: api.NormalizeOptions = {
          timezonePreference: timezonePreference ?? 'both',
          stripCountryPrefix: stripCountryPrefix ?? false,
          keepCountryPrefix: keepCountryPrefix ?? false,
          countrySeparator: countrySeparator ?? '|',
          stripNetworkPrefix: stripNetworkPrefix ?? false,
          customNetworkPrefixes: customNetworkPrefixes,
          stripNetworkSuffix: stripNetworkSuffix ?? false,
          customNetworkSuffixes: customNetworkSuffixes,
        };

        // Filter streams by timezone preference
        const filteredStreams = api.filterStreamsByTimezone(streamsToCreate, timezonePreference ?? 'both');

        // Group streams by normalized name
        const streamsByNormalizedName = new Map<string, Stream[]>();
        for (const stream of filteredStreams) {
          const normalizedName = api.normalizeStreamName(stream.name, options);
          const existing = streamsByNormalizedName.get(normalizedName);
          if (existing) {
            existing.push(stream);
          } else {
            streamsByNormalizedName.set(normalizedName, [stream]);
          }
        }

        const mergedCount = filteredStreams.length - streamsByNormalizedName.size;
        const channelCount = streamsByNormalizedName.size;

        // Start a batch for all channel operations
        startBatch(`Create ${channelCount} channels from streams`);

        // Only push down channels if explicitly requested via pushDownOnConflict
        if (pushDownOnConflict) {
          // Calculate the decimal/integer mode for shifting
          const hasDecimalShift = startingNumber % 1 !== 0;
          const incrementShift = hasDecimalShift ? 0.1 : 1;

          // Calculate the ending number of the new channel range
          const rawEndingNumber = startingNumber + (channelCount - 1) * incrementShift;
          const endingNumber = hasDecimalShift
            ? Math.round(rawEndingNumber * 10) / 10
            : rawEndingNumber;

          // Only shift channels that actually conflict with the new channel range
          // (not ALL channels >= startingNumber)
          const channelsToShift = displayChannels
            .filter((ch) => ch.channel_number !== null &&
                    ch.channel_number >= startingNumber &&
                    ch.channel_number <= endingNumber)
            .sort((a, b) => (b.channel_number ?? 0) - (a.channel_number ?? 0)); // Sort descending to avoid conflicts

          // Shift amount is the total range taken by new channels
          const shiftAmount = channelCount * incrementShift;

          // Shift each conflicting channel to just after the new range
          for (const ch of channelsToShift) {
            const rawNewNum = ch.channel_number! + shiftAmount;
            const newNum = hasDecimalShift
              ? Math.round(rawNewNum * 10) / 10
              : rawNewNum;
            stageUpdateChannel(ch.id, { channel_number: newNum }, `Shifted channel ${ch.channel_number} to ${newNum} to make room`);
          }
        }

        // Create channels and assign streams
        // Sort entries alphabetically by normalized name for consistent ordering
        // Use natural sort so "C-SPAN" comes before "C-SPAN 2" which comes before "C-SPAN 3"
        const sortedEntries = Array.from(streamsByNormalizedName.entries()).sort((a, b) => {
          // Natural sort comparison that handles trailing numbers properly
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

        // Detect if we should use decimal increments (e.g., 38.1 -> 38.2 -> 38.3)
        // A number like 38.1 has a decimal part, so we increment by 0.1
        const hasDecimal = startingNumber % 1 !== 0;
        const increment = hasDecimal ? 0.1 : 1;

        let channelIndex = 0;
        for (const [normalizedName, groupedStreams] of sortedEntries) {
          // Calculate channel number with proper decimal handling
          const rawChannelNumber = startingNumber + channelIndex * increment;
          // Round to 1 decimal place to avoid floating point precision issues
          const channelNumber = hasDecimal ? Math.round(rawChannelNumber * 10) / 10 : rawChannelNumber;
          channelIndex++;

          // Build channel name with proper prefixes
          // First, strip any existing channel number prefix from the name
          // Pattern: number (with optional decimal) followed by separator (|, -, :, space+letter)
          // Examples: "123 | ESPN" -> "ESPN", "45.1 - CNN" -> "CNN", "7: ABC" -> "ABC"
          const stripChannelNumber = (name: string): string => {
            const match = name.match(/^\d+(?:\.\d+)?\s*[|\-:]\s*(.+)$/);
            return match ? match[1] : name;
          };

          let channelName = normalizedName;
          if (addChannelNumber && keepCountryPrefix) {
            // Strip existing channel number before checking for country prefix
            const nameWithoutNumber = stripChannelNumber(normalizedName);
            const countryMatch = nameWithoutNumber.match(new RegExp(`^([A-Z]{2,6})\\s*[${countrySeparator ?? '|'}]\\s*(.+)$`));
            if (countryMatch) {
              const [, countryCode, baseName] = countryMatch;
              if (prefixOrder === 'country-first') {
                channelName = `${countryCode} ${countrySeparator} ${channelNumber} ${numberSeparator} ${baseName}`;
              } else {
                channelName = `${channelNumber} ${numberSeparator} ${countryCode} ${countrySeparator} ${baseName}`;
              }
            } else {
              channelName = `${channelNumber} ${numberSeparator} ${nameWithoutNumber}`;
            }
          } else if (addChannelNumber) {
            const nameWithoutNumber = stripChannelNumber(normalizedName);
            channelName = `${channelNumber} ${numberSeparator} ${nameWithoutNumber}`;
          }

          // Find logo URL from the first stream that has one
          let logoUrl: string | undefined;
          for (const stream of groupedStreams) {
            if (stream.logo_url) {
              logoUrl = stream.logo_url;
              break;
            }
          }

          // Create the channel (returns temp ID)
          // If targetNewGroupName is set, pass it so the commit logic can create the group first
          // Pass logoUrl - the commit logic will create the logo if needed
          const tempChannelId = stageCreateChannel(
            channelName,
            channelNumber,
            targetGroupId ?? undefined,
            targetNewGroupName,
            undefined, // logoId - will be resolved during commit
            logoUrl
          );

          // Assign all streams in this group to the new channel
          for (const stream of groupedStreams) {
            stageAddStream(tempChannelId, stream.id, `Assign stream to "${channelName}"`);
          }
        }

        // End the batch
        endBatch();

        // Show results
        const mergeInfo = mergedCount > 0
          ? `\n(${mergedCount} streams will be merged from duplicate names)`
          : '';
        const groupInfo = targetNewGroupName
          ? `\n\nA new group "${targetNewGroupName}" will be created.`
          : '';
        alert(`Staged ${streamsByNormalizedName.size} channels for creation!${mergeInfo}${groupInfo}\n\nThey will be created in Dispatcharr when you click "Done".`);

        // If we used an existing group, add it to the visible filter now
        // (New groups will be added to filter after commit when they actually exist)
        if (targetGroupId !== null) {
          setChannelFilters((prev) => {
            if (!prev.groupFilter.includes(targetGroupId!)) {
              return { ...prev, groupFilter: [...prev.groupFilter, targetGroupId!] };
            }
            return prev;
          });
        }

        // Store pending profile assignments to be applied after commit
        // Use explicit profileIds if provided, otherwise fall back to default profiles
        const profileIdsToApply = (profileIds && profileIds.length > 0)
          ? profileIds
          : defaultChannelProfileIds;

        if (profileIdsToApply.length > 0) {
          pendingProfileAssignmentsRef.current.push({
            startNumber: startingNumber,
            count: streamsByNormalizedName.size,
            profileIds: profileIdsToApply,
            increment, // Use the same increment calculated for channel creation
          });
        }

      } catch (err) {
        logger.error('Bulk create failed:', err);
        setError('Failed to bulk create channels');
        throw err;
      }
    },
    [isEditMode, stageCreateChannel, stageAddStream, stageUpdateChannel, startBatch, endBatch, displayChannels, defaultChannelProfileIds]
  );

  // Handle stream group drop on channels pane (triggers bulk create modal in streams pane)
  // Supports multiple groups being dropped at once
  // Now includes optional target group and suggested starting number for positional drops
  const handleStreamGroupDrop = useCallback((groupNames: string[], _streamIds: number[], _targetGroupId?: number, suggestedStartingNumber?: number) => {
    // Set the dropped group names - StreamsPane will react to this and open the modal
    setDroppedStreamGroupNames(groupNames);
    // If a suggested starting number was provided, use it
    if (suggestedStartingNumber !== undefined) {
      setDroppedStreamStartingNumber(suggestedStartingNumber);
    }
  }, []);

  // Handle bulk streams drop on channels pane (triggers bulk create modal for specific streams)
  const handleBulkStreamsDrop = useCallback((streamIds: number[], groupId: number | null, startingNumber: number) => {
    // Set the dropped stream IDs and target info - StreamsPane will react to this and open the modal
    setDroppedStreamIds(streamIds);
    setDroppedStreamTargetGroupId(groupId);
    setDroppedStreamStartingNumber(startingNumber);
  }, []);

  // Clear the dropped stream group/streams trigger after it's been handled
  const handleStreamGroupTriggerHandled = useCallback(() => {
    setDroppedStreamGroupNames(null);
    setDroppedStreamIds(null);
    setDroppedStreamTargetGroupId(null);
    setDroppedStreamStartingNumber(null);
  }, []);

  // Filter streams based on multi-select filters (client-side)
  const filteredStreams = useMemo(() => {
    let result = streams;

    // Filter by selected providers
    if (streamFilters.selectedProviders.length > 0) {
      result = result.filter((s) => s.m3u_account !== null && streamFilters.selectedProviders.includes(s.m3u_account));
    }

    // Filter by selected stream groups
    if (streamFilters.selectedGroups.length > 0) {
      result = result.filter((s) => streamFilters.selectedGroups.includes(s.channel_group_name || ''));
    }

    return result;
  }, [streams, streamFilters.selectedProviders, streamFilters.selectedGroups]);

  const handleDeleteChannel = useCallback(
    async (channelId: number) => {
      try {
        await api.deleteChannel(channelId);
        setChannels((prev) => prev.filter((ch) => ch.id !== channelId));
        if (selectedChannel?.id === channelId) {
          setSelectedChannel(null);
        }
      } catch (err) {
        logger.error('Failed to delete channel:', err);
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
          logger.error('Failed to reorder channels:', err);
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

  // Merge real channel groups with staged groups when in edit mode
  const displayChannelGroups = isEditMode && stagedGroups.length > 0
    ? [...channelGroups, ...stagedGroups]
    : channelGroups;

  return (
    <NotificationProvider position="top-right">
    <div className="app">
      <header className={`header ${isEditMode ? 'edit-mode-active' : ''}`}>
        <h1>
          <img src={ECMLogo} alt="ECM Logo" className="header-logo" />
          Enhanced Channel Manager
        </h1>
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
          <NotificationCenter />
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
        commitProgress={commitProgress}
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
              channelGroups={displayChannelGroups}
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
              channelsLoading={loadingStates.channels}

              // Channel Search & Filter
              channelSearch={channelFilters.search}
              onChannelSearchChange={(search) => setChannelFilters(prev => ({ ...prev, search }))}
              selectedGroups={channelFilters.groupFilter}
              onSelectedGroupsChange={(groupFilter) => setChannelFilters(prev => ({ ...prev, groupFilter }))}

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
              epgDataLoading={loadingStates.epgData}

              // Channel Profiles
              channelProfiles={channelProfiles}
              onChannelProfilesChange={loadChannelProfiles}

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
              streamsLoading={loadingStates.streams}

              // Stream Search & Filter
              streamSearch={streamFilters.search}
              onStreamSearchChange={(search) => setStreamFilters(prev => ({ ...prev, search }))}
              streamProviderFilter={streamFilters.providerFilter}
              onStreamProviderFilterChange={(providerFilter) => setStreamFilters(prev => ({ ...prev, providerFilter }))}
              streamGroupFilter={streamFilters.groupFilter}
              onStreamGroupFilterChange={(groupFilter) => setStreamFilters(prev => ({ ...prev, groupFilter }))}
              selectedProviders={streamFilters.selectedProviders}
              onSelectedProvidersChange={updateSelectedProviderFilters}
              selectedStreamGroups={streamFilters.selectedGroups}
              onSelectedStreamGroupsChange={updateSelectedStreamGroupFilters}
              onClearStreamFilters={clearStreamFilters}

              // Bulk Create
              channelDefaults={channelDefaults}
              externalTriggerGroupNames={droppedStreamGroupNames}
              externalTriggerStreamIds={droppedStreamIds}
              externalTriggerTargetGroupId={droppedStreamTargetGroupId}
              externalTriggerStartingNumber={droppedStreamStartingNumber}
              onExternalTriggerHandled={handleStreamGroupTriggerHandled}
              onStreamGroupDrop={handleStreamGroupDrop}
              onBulkStreamsDrop={handleBulkStreamsDrop}
              onBulkCreateFromGroup={handleBulkCreateFromGroup}
              onCheckConflicts={handleCheckConflicts}
              onGetHighestChannelNumber={handleGetHighestChannelNumber}

              // Dispatcharr URL for channel stream URLs
              dispatcharrUrl={dispatcharrUrl}

              // Appearance settings
              showStreamUrls={showStreamUrls}
              hideUngroupedStreams={hideUngroupedStreams}

              // EPG matching settings
              epgAutoMatchThreshold={epgAutoMatchThreshold}

              // Gracenote conflict handling
              gracenoteConflictMode={gracenoteConflictMode}

              // Refresh streams (bypasses cache)
              onRefreshStreams={refreshStreams}

              // External trigger to open edit modal from Guide tab
              externalChannelToEdit={channelToEditFromGuide}
              onExternalChannelEditHandled={handleExternalChannelEditHandled}
            />
          )}
          {activeTab === 'm3u-manager' && (
            <M3UManagerTab
              epgSources={epgSources}
              channelGroups={channelGroups}
              channelProfiles={channelProfiles}
              streamProfiles={streamProfiles}
              onChannelGroupsChange={loadChannelGroups}
              hideM3uUrls={hideM3uUrls}
            />
          )}
          {activeTab === 'epg-manager' && <EPGManagerTab onSourcesChange={loadEpgSources} hideEpgUrls={hideEpgUrls} />}
          {activeTab === 'guide' && (
            <GuideTab
              channels={channels}
              logos={logos}
              epgData={epgData}
              epgSources={epgSources}
              streamProfiles={streamProfiles}
              epgDataLoading={loadingStates.epgData}
              onChannelUpdate={handleGuideChannelUpdate}
              onLogoCreate={handleLogoCreate}
              onLogoUpload={handleLogoUpload}
              onLogosChange={loadLogos}
            />
          )}
          {activeTab === 'logo-manager' && <LogoManagerTab />}
          {activeTab === 'journal' && <JournalTab />}
          {activeTab === 'stats' && <StatsTab />}
          {activeTab === 'settings' && <SettingsTab onSaved={handleSettingsSaved} channelProfiles={channelProfiles} onProbeComplete={loadChannels} />}
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

      <VLCProtocolHelperModal
        isOpen={showVLCHelperModal}
        onClose={() => setShowVLCHelperModal(false)}
        onDownloadM3U={() => downloadM3U(vlcModalStreamUrl, vlcModalStreamName)}
        streamName={vlcModalStreamName || 'Stream'}
      />
    </div>
    </NotificationProvider>
  );
}

export default App;
