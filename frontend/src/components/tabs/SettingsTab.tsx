import { useState, useEffect, useMemo, useCallback } from 'react';
import * as api from '../../services/api';
import { NETWORK_PREFIXES, NETWORK_SUFFIXES } from '../../constants/streamNormalization';
import { normalizeStreamName } from '../../services/streamNormalization';
import type { Theme, ProbeHistoryEntry, SortCriterion, SortEnabledMap, GracenoteConflictMode, NormalizationSettings } from '../../services/api';
import { NormalizationTagsSection } from '../settings/NormalizationTagsSection';
import type { ChannelProfile } from '../../types';
import { logger } from '../../utils/logger';
import { copyToClipboard } from '../../utils/clipboard';
import type { LogLevel as FrontendLogLevel } from '../../utils/logger';
import { DeleteOrphanedGroupsModal } from '../DeleteOrphanedGroupsModal';
import { ScheduledTasksSection } from '../ScheduledTasksSection';
import { AlertMethodSettings } from '../AlertMethodSettings';
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  DragEndEvent,
} from '@dnd-kit/core';
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import './SettingsTab.css';

// Sort priority item configuration
const SORT_CRITERION_CONFIG: Record<SortCriterion, { icon: string; label: string; description: string }> = {
  resolution: { icon: 'aspect_ratio', label: 'Resolution', description: '4K > 1080p > 720p' },
  bitrate: { icon: 'speed', label: 'Bitrate', description: 'Higher bitrate first' },
  framerate: { icon: 'slow_motion_video', label: 'Framerate', description: '60fps > 30fps' },
};

// Sortable item component for drag-and-drop
function SortablePriorityItem({
  id,
  index,
  enabled,
  onToggleEnabled
}: {
  id: SortCriterion;
  index: number;
  enabled: boolean;
  onToggleEnabled: (id: SortCriterion) => void;
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id });

  const config = SORT_CRITERION_CONFIG[id];

  // Use inline styles to avoid any CSS conflicts
  const containerStyle: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    display: 'flex',
    alignItems: 'center',
    gap: '0.75rem',
    padding: '0.75rem 1rem',
    backgroundColor: 'var(--input-bg)',
    border: '1px solid var(--border-primary)',
    borderRadius: '6px',
    opacity: isDragging ? 0.5 : enabled ? 1 : 0.6,
    boxShadow: isDragging ? '0 4px 12px rgba(0, 0, 0, 0.3)' : undefined,
  };

  return (
    <div ref={setNodeRef} style={containerStyle}>
      <span
        {...attributes}
        {...listeners}
        style={{
          display: 'flex',
          alignItems: 'center',
          cursor: 'grab',
          color: 'var(--text-muted)',
          touchAction: 'none',
        }}
      >
        <span className="material-icons" style={{ fontSize: '20px' }}>drag_indicator</span>
      </span>
      <input
        type="checkbox"
        checked={enabled}
        onChange={() => onToggleEnabled(id)}
        style={{
          width: '16px',
          height: '16px',
          cursor: 'pointer',
          accentColor: 'var(--accent-primary)',
          flexShrink: 0,
        }}
        title={enabled ? 'Click to disable this sort criterion' : 'Click to enable this sort criterion'}
      />
      <span
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: '24px',
          height: '24px',
          borderRadius: '50%',
          backgroundColor: enabled ? 'var(--accent-primary, #3b82f6)' : 'var(--text-muted, #6b7280)',
          color: 'var(--bg-primary, #1e1e23)',
          fontSize: '0.75rem',
          fontWeight: 600,
          flexShrink: 0,
          lineHeight: 1,
        }}
      >
        {enabled ? index + 1 : '-'}
      </span>
      <span className="material-icons" style={{ fontSize: '20px', color: 'var(--text-secondary)', flexShrink: 0 }}>
        {config.icon}
      </span>
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '0.125rem', minWidth: 0 }}>
        <span style={{ fontSize: '0.9rem', fontWeight: 500, color: 'var(--text-primary)' }}>{config.label}</span>
        <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>{config.description}</span>
      </div>
    </div>
  );
}

interface SettingsTabProps {
  onSaved: () => void;
  onThemeChange?: (theme: Theme) => void;
  channelProfiles?: ChannelProfile[];
  onProbeComplete?: () => void;
}

type SettingsPage = 'general' | 'channel-defaults' | 'normalization' | 'appearance' | 'scheduled-tasks' | 'alert-methods' | 'maintenance';

export function SettingsTab({ onSaved, onThemeChange, channelProfiles = [], onProbeComplete }: SettingsTabProps) {
  const [activePage, setActivePage] = useState<SettingsPage>('general');

  // Connection settings
  const [url, setUrl] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');

  // Channel defaults
  const [autoRenameChannelNumber, setAutoRenameChannelNumber] = useState(false);
  const [includeChannelNumberInName, setIncludeChannelNumberInName] = useState(false);
  const [channelNumberSeparator, setChannelNumberSeparator] = useState('-');
  const [removeCountryPrefix, setRemoveCountryPrefix] = useState(false);
  const [includeCountryInName, setIncludeCountryInName] = useState(false);
  const [countrySeparator, setCountrySeparator] = useState('|');
  const [timezonePreference, setTimezonePreference] = useState('both');
  const [defaultChannelProfileIds, setDefaultChannelProfileIds] = useState<number[]>([]);
  const [epgAutoMatchThreshold, setEpgAutoMatchThreshold] = useState(80);
  const [customNetworkPrefixes, setCustomNetworkPrefixes] = useState<string[]>([]);
  const [newPrefixInput, setNewPrefixInput] = useState('');
  const [customNetworkSuffixes, setCustomNetworkSuffixes] = useState<string[]>([]);
  const [newSuffixInput, setNewSuffixInput] = useState('');
  // New tag-based normalization settings
  const [normalizationSettings, setNormalizationSettings] = useState<NormalizationSettings>({
    disabledBuiltinTags: [],
    customTags: [],
  });
  const [normalizationPreviewInput, setNormalizationPreviewInput] = useState('');

  // Compute normalized preview based on current settings
  const normalizedPreviewResult = useMemo(() => {
    if (!normalizationPreviewInput.trim()) return '';
    return normalizeStreamName(normalizationPreviewInput, {
      timezonePreference: 'both',
      stripCountryPrefix: false, // Handled by normalization tags
      keepCountryPrefix: includeCountryInName,
      countrySeparator: countrySeparator as '-' | ':' | '|',
      stripNetworkPrefix: true,
      stripNetworkSuffix: true,
      normalizationSettings,
    });
  }, [normalizationPreviewInput, includeCountryInName, countrySeparator, normalizationSettings]);

  const [streamSortPriority, setStreamSortPriority] = useState<SortCriterion[]>(['resolution', 'bitrate', 'framerate']);
  const [streamSortEnabled, setStreamSortEnabled] = useState<SortEnabledMap>({ resolution: true, bitrate: true, framerate: true });
  const [deprioritizeFailedStreams, setDeprioritizeFailedStreams] = useState(true);

  // Appearance settings
  const [showStreamUrls, setShowStreamUrls] = useState(true);
  const [hideAutoSyncGroups, setHideAutoSyncGroups] = useState(false);
  const [hideUngroupedStreams, setHideUngroupedStreams] = useState(true);
  const [hideEpgUrls, setHideEpgUrls] = useState(false);
  const [hideM3uUrls, setHideM3uUrls] = useState(false);
  const [gracenoteConflictMode, setGracenoteConflictMode] = useState<GracenoteConflictMode>('ask');
  const [theme, setTheme] = useState<Theme>('dark');
  const [vlcOpenBehavior, setVlcOpenBehavior] = useState('m3u_fallback');

  // Stats settings
  const [statsPollInterval, setStatsPollInterval] = useState(10);
  const [userTimezone, setUserTimezone] = useState('');

  // Log level settings
  const [backendLogLevel, setBackendLogLevel] = useState('INFO');
  const [frontendLogLevel, setFrontendLogLevel] = useState('INFO');

  // Stream probe settings (scheduled probing is controlled by Task Engine)
  const [streamProbeBatchSize, setStreamProbeBatchSize] = useState(10);
  const [streamProbeTimeout, setStreamProbeTimeout] = useState(30);
  const [bitrateSampleDuration, setBitrateSampleDuration] = useState(10);
  const [parallelProbingEnabled, setParallelProbingEnabled] = useState(true);
  const [maxConcurrentProbes, setMaxConcurrentProbes] = useState(8);
  const [skipRecentlyProbedHours, setSkipRecentlyProbedHours] = useState(0);
  const [refreshM3usBeforeProbe, setRefreshM3usBeforeProbe] = useState(true);
  const [autoReorderAfterProbe, setAutoReorderAfterProbe] = useState(false);
  const [streamFetchPageLimit, setStreamFetchPageLimit] = useState(200);
  const [probingAll, setProbingAll] = useState(false);
  const [probeAllResult, setProbeAllResult] = useState<{ success: boolean; message: string } | null>(null);
  const [totalStreamCount, setTotalStreamCount] = useState(100); // Default to 100, will be updated on load
  const [probeProgress, setProbeProgress] = useState<{
    in_progress: boolean;
    total: number;
    current: number;
    status: string;
    current_stream: string;
    success_count: number;
    failed_count: number;
    skipped_count: number;
    percentage: number;
    rate_limited?: boolean;
    rate_limited_hosts?: Array<{ host: string; backoff_remaining: number; consecutive_429s: number }>;
    max_backoff_remaining?: number;
  } | null>(null);
  const [showProbeResultsModal, setShowProbeResultsModal] = useState(false);
  const [probeResultsType, setProbeResultsType] = useState<'success' | 'failed' | 'skipped'>('success');
  const [probeResults, setProbeResults] = useState<{
    success_streams: Array<{ id: number; name: string; url?: string }>;
    failed_streams: Array<{ id: number; name: string; url?: string; error?: string }>;
    skipped_streams: Array<{ id: number; name: string; url?: string; reason?: string }>;
    success_count: number;
    failed_count: number;
    skipped_count: number;
  } | null>(null);
  const [probeHistory, setProbeHistory] = useState<ProbeHistoryEntry[]>([]);
  const [showReorderModal, setShowReorderModal] = useState(false);
  const [reorderData, setReorderData] = useState<ProbeHistoryEntry['reordered_channels'] | null>(null);
  const [reorderSortConfig, setReorderSortConfig] = useState<ProbeHistoryEntry['sort_config'] | null>(null);
  const [copiedUrl, setCopiedUrl] = useState<string | null>(null);
  const [availableChannelGroups, setAvailableChannelGroups] = useState<Array<{ id: number; name: string }>>([]);
  const [probeChannelGroups, setProbeChannelGroups] = useState<string[]>([]);
  const [showGroupSelectModal, setShowGroupSelectModal] = useState(false);
  const [tempProbeChannelGroups, setTempProbeChannelGroups] = useState<string[]>([]);
  // M3U accounts for guidance on max concurrent probes
  const [m3uAccountsMaxStreams, setM3uAccountsMaxStreams] = useState<{ name: string; max_streams: number }[]>([]);

  // Preserve settings not managed by this tab (to avoid overwriting them on save)
  const [linkedM3UAccounts, setLinkedM3UAccounts] = useState<number[][]>([]);

  // UI state
  const [loading, setLoading] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saveSuccess, setSaveSuccess] = useState(false);

  // Maintenance state
  const [orphanedGroups, setOrphanedGroups] = useState<{ id: number; name: string; reason?: string }[]>([]);
  const [loadingOrphaned, setLoadingOrphaned] = useState(false);
  const [cleaningOrphaned, setCleaningOrphaned] = useState(false);
  const [cleanupResult, setCleanupResult] = useState<string | null>(null);
  const [showDeleteModal, setShowDeleteModal] = useState(false);

  // Track original URL/username to detect if auth settings changed
  const [originalUrl, setOriginalUrl] = useState('');
  const [originalUsername, setOriginalUsername] = useState('');

  // Track original settings to detect if restart is needed
  const [originalPollInterval, setOriginalPollInterval] = useState(10);
  const [originalTimezone, setOriginalTimezone] = useState('');
  const [originalAutoReorder, setOriginalAutoReorder] = useState(false);
  const [originalRefreshM3usBeforeProbe, setOriginalRefreshM3usBeforeProbe] = useState(true);
  const [needsRestart, setNeedsRestart] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [restartResult, setRestartResult] = useState<{ success: boolean; message: string } | null>(null);

  // Handler to save normalization settings immediately when tags change
  // NOTE: This callback must be defined AFTER all state declarations to avoid temporal dead zone errors
  const handleNormalizationSettingsChange = useCallback(async (newSettings: NormalizationSettings) => {
    // Update local state first for immediate UI response
    setNormalizationSettings(newSettings);

    // Save to backend immediately (with all current settings)
    try {
      await api.saveSettings({
        url,
        username,
        auto_rename_channel_number: autoRenameChannelNumber,
        include_channel_number_in_name: includeChannelNumberInName,
        channel_number_separator: channelNumberSeparator,
        remove_country_prefix: removeCountryPrefix,
        include_country_in_name: includeCountryInName,
        country_separator: countrySeparator,
        timezone_preference: timezonePreference,
        show_stream_urls: showStreamUrls,
        hide_auto_sync_groups: hideAutoSyncGroups,
        hide_ungrouped_streams: hideUngroupedStreams,
        hide_epg_urls: hideEpgUrls,
        hide_m3u_urls: hideM3uUrls,
        gracenote_conflict_mode: gracenoteConflictMode,
        theme: theme,
        default_channel_profile_ids: defaultChannelProfileIds,
        epg_auto_match_threshold: epgAutoMatchThreshold,
        custom_network_prefixes: customNetworkPrefixes,
        custom_network_suffixes: customNetworkSuffixes,
        normalization_settings: newSettings, // Use the new settings
        stats_poll_interval: statsPollInterval,
        user_timezone: userTimezone,
        backend_log_level: backendLogLevel,
        frontend_log_level: frontendLogLevel,
        vlc_open_behavior: vlcOpenBehavior,
        linked_m3u_accounts: linkedM3UAccounts,
        stream_probe_batch_size: streamProbeBatchSize,
        stream_probe_timeout: streamProbeTimeout,
        probe_channel_groups: probeChannelGroups,
        bitrate_sample_duration: bitrateSampleDuration,
        parallel_probing_enabled: parallelProbingEnabled,
        max_concurrent_probes: maxConcurrentProbes,
        skip_recently_probed_hours: skipRecentlyProbedHours,
        refresh_m3us_before_probe: refreshM3usBeforeProbe,
        auto_reorder_after_probe: autoReorderAfterProbe,
        stream_fetch_page_limit: streamFetchPageLimit,
        stream_sort_priority: streamSortPriority,
        stream_sort_enabled: streamSortEnabled,
        deprioritize_failed_streams: deprioritizeFailedStreams,
      });
      logger.debug('Normalization settings saved automatically');
    } catch (err) {
      logger.error('Failed to auto-save normalization settings:', err);
      // Don't show error to user for auto-save, just log it
    }
  }, [
    url, username, autoRenameChannelNumber, includeChannelNumberInName,
    channelNumberSeparator, removeCountryPrefix, includeCountryInName,
    countrySeparator, timezonePreference, showStreamUrls, hideAutoSyncGroups,
    hideUngroupedStreams, hideEpgUrls, hideM3uUrls, gracenoteConflictMode,
    theme, defaultChannelProfileIds, epgAutoMatchThreshold,
    customNetworkPrefixes, customNetworkSuffixes, statsPollInterval,
    userTimezone, backendLogLevel, frontendLogLevel, vlcOpenBehavior,
    linkedM3UAccounts, streamProbeBatchSize,
    streamProbeTimeout, probeChannelGroups, bitrateSampleDuration,
    parallelProbingEnabled, maxConcurrentProbes, skipRecentlyProbedHours, refreshM3usBeforeProbe,
    autoReorderAfterProbe, streamFetchPageLimit, streamSortPriority,
    streamSortEnabled, deprioritizeFailedStreams
  ]);

  // DnD sensors for sort priority
  const sensors = useSensors(
    useSensor(PointerSensor),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    })
  );

  const handleSortPriorityDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (over && active.id !== over.id) {
      setStreamSortPriority((items) => {
        const oldIndex = items.indexOf(active.id as SortCriterion);
        const newIndex = items.indexOf(over.id as SortCriterion);
        return arrayMove(items, oldIndex, newIndex);
      });
    }
  };

  useEffect(() => {
    loadSettings();
    loadStreamCount();
    loadAvailableChannelGroups();
    loadProbeHistory();
    checkForOngoingProbe();
    loadM3UAccountsMaxStreams();
  }, []);

  // Load M3U accounts to show guidance for max concurrent probes
  const loadM3UAccountsMaxStreams = async () => {
    try {
      const accounts = await api.getM3UAccounts();
      const maxStreamsList = accounts
        .filter(a => a.is_active && a.max_streams > 0)
        .map(a => ({ name: a.name, max_streams: a.max_streams }));
      setM3uAccountsMaxStreams(maxStreamsList);
    } catch (err) {
      logger.warn('Failed to load M3U accounts for max streams guidance', err);
    }
  };

  // Check if a probe is already in progress (e.g., when returning to Settings tab)
  const checkForOngoingProbe = async () => {
    try {
      const progress = await api.getProbeProgress();
      if (progress.in_progress) {
        // A probe is running - restore the progress state and start polling
        logger.info('Detected ongoing probe, resuming progress display');
        setProbeProgress(progress);
        setProbingAll(true);
      }
    } catch (err) {
      logger.warn('Failed to check for ongoing probe', err);
    }
  };

  // Auto-populate probe channel groups with all groups if empty (default to all checked)
  // Also filter out any stale groups that no longer exist
  useEffect(() => {
    if (availableChannelGroups.length > 0) {
      const availableNames = new Set(availableChannelGroups.map(g => g.name));
      const validGroups = probeChannelGroups.filter(name => availableNames.has(name));

      if (validGroups.length === 0) {
        // If no valid groups are selected, default to all groups
        setProbeChannelGroups(availableChannelGroups.map(g => g.name));
      } else if (validGroups.length !== probeChannelGroups.length) {
        // Filter out stale groups that no longer exist
        setProbeChannelGroups(validGroups);
      }
    }
  }, [availableChannelGroups, probeChannelGroups]);

  // Periodically check for scheduled probes (runs even when probingAll is false)
  useEffect(() => {
    // Don't run this polling if we're already tracking a probe
    if (probingAll) {
      return;
    }

    const checkForScheduledProbe = async () => {
      try {
        const progress = await api.getProbeProgress();
        if (progress.in_progress) {
          // A scheduled probe started - show it in the UI
          logger.info('Detected scheduled probe starting, showing progress');
          setProbeProgress(progress);
          setProbingAll(true);
        }
      } catch (err) {
        // Silently ignore errors - this is background polling
      }
    };

    // Check every 5 seconds for scheduled probes
    const interval = setInterval(checkForScheduledProbe, 5000);

    return () => {
      clearInterval(interval);
    };
  }, [probingAll]);

  // Poll for probe all streams progress (faster polling when actively probing)
  useEffect(() => {
    if (!probingAll) {
      return;
    }

    const pollProgress = async () => {
      try {
        const progress = await api.getProbeProgress();
        setProbeProgress(progress);

        // Stop polling when probe is complete
        if (!progress.in_progress) {
          setProbingAll(false);
          // Reload probe history when probe completes
          loadProbeHistory();
          // Notify parent to reload channels (auto-reorder may have changed stream order)
          onProbeComplete?.();
          if (progress.status === 'completed') {
            const skippedMsg = progress.skipped_count > 0 ? `, Skipped: ${progress.skipped_count}` : '';
            setProbeAllResult({
              success: true,
              message: `Probe completed! ${progress.total} streams probed. Success: ${progress.success_count}, Failed: ${progress.failed_count}${skippedMsg}`
            });
          } else if (progress.status === 'failed') {
            setProbeAllResult({ success: false, message: 'Probe failed' });
          } else if (progress.status === 'cancelled') {
            setProbeAllResult({ success: false, message: 'Probe was cancelled' });
          }
          // Clear result after 8 seconds
          setTimeout(() => setProbeAllResult(null), 8000);
        }
      } catch (err) {
        console.error('Failed to fetch probe progress:', err);
      }
    };

    // Poll immediately, then continue every second
    // Using immediate poll to catch fast probes that might complete quickly
    pollProgress();
    const interval = setInterval(pollProgress, 1000);

    return () => {
      clearInterval(interval);
    };
  }, [probingAll]);

  const loadStreamCount = async () => {
    try {
      // Fetch just the count (page_size=1 to minimize data transfer)
      const result = await api.getStreams({ pageSize: 1 });
      if (result.count) {
        setTotalStreamCount(Math.max(1, result.count));
      }
    } catch (err) {
      logger.warn('Failed to load stream count for batch size max', err);
      // Keep default of 100
    }
  };

  const loadAvailableChannelGroups = async () => {
    try {
      const result = await api.getChannelGroupsWithStreams();
      setAvailableChannelGroups(result.groups);
    } catch (err) {
      logger.warn('Failed to load available channel groups', err);
    }
  };

  const loadProbeHistory = async () => {
    try {
      const history = await api.getProbeHistory();
      setProbeHistory(history);
    } catch (err) {
      logger.warn('Failed to load probe history', err);
    }
  };

  const loadSettings = async () => {
    try {
      const settings = await api.getSettings();
      setUrl(settings.url);
      setUsername(settings.username);
      setOriginalUrl(settings.url);
      setOriginalUsername(settings.username);
      setPassword(''); // Never load password from server
      setAutoRenameChannelNumber(settings.auto_rename_channel_number);
      setIncludeChannelNumberInName(settings.include_channel_number_in_name);
      setChannelNumberSeparator(settings.channel_number_separator);
      setRemoveCountryPrefix(settings.remove_country_prefix);
      setIncludeCountryInName(settings.include_country_in_name);
      setCountrySeparator(settings.country_separator);
      setTimezonePreference(settings.timezone_preference);
      setShowStreamUrls(settings.show_stream_urls);
      setHideAutoSyncGroups(settings.hide_auto_sync_groups);
      setHideUngroupedStreams(settings.hide_ungrouped_streams);
      setHideEpgUrls(settings.hide_epg_urls ?? false);
      setHideM3uUrls(settings.hide_m3u_urls ?? false);
      setGracenoteConflictMode(settings.gracenote_conflict_mode || 'ask');
      setTheme(settings.theme || 'dark');
      setVlcOpenBehavior(settings.vlc_open_behavior || 'm3u_fallback');
      setDefaultChannelProfileIds(settings.default_channel_profile_ids);
      setEpgAutoMatchThreshold(settings.epg_auto_match_threshold ?? 80);
      setCustomNetworkPrefixes(settings.custom_network_prefixes ?? []);
      setCustomNetworkSuffixes(settings.custom_network_suffixes ?? []);
      setNormalizationSettings(settings.normalization_settings ?? {
        disabledBuiltinTags: [],
        customTags: [],
      });
      setStatsPollInterval(settings.stats_poll_interval ?? 10);
      setOriginalPollInterval(settings.stats_poll_interval ?? 10);
      setUserTimezone(settings.user_timezone ?? '');
      setOriginalTimezone(settings.user_timezone ?? '');
      setBackendLogLevel(settings.backend_log_level ?? 'INFO');
      const frontendLevel = settings.frontend_log_level ?? 'INFO';
      setFrontendLogLevel(frontendLevel);
      // Apply frontend log level immediately
      const frontendLogLevel = frontendLevel === 'WARNING' ? 'WARN' : frontendLevel;
      if (['DEBUG', 'INFO', 'WARN', 'ERROR'].includes(frontendLogLevel)) {
        logger.setLevel(frontendLogLevel as FrontendLogLevel);
      }
      setLinkedM3UAccounts(settings.linked_m3u_accounts ?? []);
      // Stream probe settings (scheduled probing is controlled by Task Engine)
      setStreamProbeBatchSize(settings.stream_probe_batch_size ?? 10);
      setStreamProbeTimeout(settings.stream_probe_timeout ?? 30);
      setProbeChannelGroups(settings.probe_channel_groups ?? []);
      setBitrateSampleDuration(settings.bitrate_sample_duration ?? 10);
      setParallelProbingEnabled(settings.parallel_probing_enabled ?? true);
      setMaxConcurrentProbes(settings.max_concurrent_probes ?? 8);
      setSkipRecentlyProbedHours(settings.skip_recently_probed_hours ?? 0);
      setRefreshM3usBeforeProbe(settings.refresh_m3us_before_probe ?? true);
      setOriginalRefreshM3usBeforeProbe(settings.refresh_m3us_before_probe ?? true);
      setAutoReorderAfterProbe(settings.auto_reorder_after_probe ?? false);
      setOriginalAutoReorder(settings.auto_reorder_after_probe ?? false);
      setStreamFetchPageLimit(settings.stream_fetch_page_limit ?? 200);
      setStreamSortPriority(settings.stream_sort_priority ?? ['resolution', 'bitrate', 'framerate']);
      setStreamSortEnabled(settings.stream_sort_enabled ?? { resolution: true, bitrate: true, framerate: true });
      setDeprioritizeFailedStreams(settings.deprioritize_failed_streams ?? true);
      setNeedsRestart(false);
      setRestartResult(null);
      setTestResult(null);
      setError(null);
    } catch (err) {
      console.error('Failed to load settings:', err);
    }
  };

  // Handle theme change with immediate preview
  const handleThemeChange = (newTheme: Theme) => {
    setTheme(newTheme);
    // Apply theme immediately for preview
    document.documentElement.setAttribute('data-theme', newTheme === 'dark' ? '' : newTheme);
    onThemeChange?.(newTheme);
  };

  const handleTest = async () => {
    if (!url || !username || !password) {
      setError('URL, username, and password are required to test connection');
      return;
    }

    setTesting(true);
    setTestResult(null);
    setError(null);
    setSaveSuccess(false);

    try {
      const result = await api.testConnection({ url, username, password });
      setTestResult(result);
    } catch (err) {
      setTestResult({ success: false, message: 'Failed to test connection' });
    } finally {
      setTesting(false);
    }
  };

  const handleSave = async () => {
    // Check if auth settings (URL or username) have changed
    const authChanged = url !== originalUrl || username !== originalUsername;

    // Validate required fields
    if (!url || !username) {
      setError('URL and username are required');
      return;
    }

    // Password is only required if auth settings changed
    if (authChanged && !password) {
      setError('Password is required when changing URL or username');
      return;
    }

    setLoading(true);
    setError(null);
    setSaveSuccess(false);

    try {
      await api.saveSettings({
        url,
        username,
        // Only send password if it was entered
        ...(password ? { password } : {}),
        auto_rename_channel_number: autoRenameChannelNumber,
        include_channel_number_in_name: includeChannelNumberInName,
        channel_number_separator: channelNumberSeparator,
        remove_country_prefix: removeCountryPrefix,
        include_country_in_name: includeCountryInName,
        country_separator: countrySeparator,
        timezone_preference: timezonePreference,
        show_stream_urls: showStreamUrls,
        hide_auto_sync_groups: hideAutoSyncGroups,
        hide_ungrouped_streams: hideUngroupedStreams,
        hide_epg_urls: hideEpgUrls,
        hide_m3u_urls: hideM3uUrls,
        gracenote_conflict_mode: gracenoteConflictMode,
        theme: theme,
        default_channel_profile_ids: defaultChannelProfileIds,
        epg_auto_match_threshold: epgAutoMatchThreshold,
        custom_network_prefixes: customNetworkPrefixes,
        custom_network_suffixes: customNetworkSuffixes,
        normalization_settings: normalizationSettings,
        stats_poll_interval: statsPollInterval,
        user_timezone: userTimezone,
        backend_log_level: backendLogLevel,
        frontend_log_level: frontendLogLevel,
        vlc_open_behavior: vlcOpenBehavior,
        linked_m3u_accounts: linkedM3UAccounts,
        // Stream probe settings (scheduled probing is controlled by Task Engine)
        stream_probe_batch_size: streamProbeBatchSize,
        stream_probe_timeout: streamProbeTimeout,
        probe_channel_groups: probeChannelGroups,
        bitrate_sample_duration: bitrateSampleDuration,
        parallel_probing_enabled: parallelProbingEnabled,
        max_concurrent_probes: maxConcurrentProbes,
        skip_recently_probed_hours: skipRecentlyProbedHours,
        refresh_m3us_before_probe: refreshM3usBeforeProbe,
        auto_reorder_after_probe: autoReorderAfterProbe,
        stream_fetch_page_limit: streamFetchPageLimit,
        stream_sort_priority: streamSortPriority,
        stream_sort_enabled: streamSortEnabled,
        deprioritize_failed_streams: deprioritizeFailedStreams,
      });
      // Apply frontend log level immediately
      const frontendLevel = frontendLogLevel === 'WARNING' ? 'WARN' : frontendLogLevel;
      if (['DEBUG', 'INFO', 'WARN', 'ERROR'].includes(frontendLevel)) {
        logger.setLevel(frontendLevel as FrontendLogLevel);
        logger.info(`Frontend log level changed to ${frontendLevel}`);
      }
      // Update global VLC settings for vlc utility to access
      (window as any).__vlcSettings = { behavior: vlcOpenBehavior };
      setOriginalUrl(url);
      setOriginalUsername(username);
      setPassword('');
      setSaveSuccess(true);
      logger.info('Settings saved successfully');
      // Check if any settings that require a restart have changed
      const pollOrTimezoneChanged = statsPollInterval !== originalPollInterval || userTimezone !== originalTimezone;
      const probeSettingsChanged = autoReorderAfterProbe !== originalAutoReorder ||
                                   refreshM3usBeforeProbe !== originalRefreshM3usBeforeProbe;

      // Debug logging for restart detection
      logger.info(`[RESTART-CHECK] Poll interval: ${statsPollInterval} vs original ${originalPollInterval}`);
      logger.info(`[RESTART-CHECK] Timezone: "${userTimezone}" vs original "${originalTimezone}"`);
      logger.info(`[RESTART-CHECK] Auto-reorder: ${autoReorderAfterProbe} vs original ${originalAutoReorder}`);
      logger.info(`[RESTART-CHECK] Refresh M3Us: ${refreshM3usBeforeProbe} vs original ${originalRefreshM3usBeforeProbe}`);
      logger.info(`[RESTART-CHECK] pollOrTimezoneChanged=${pollOrTimezoneChanged}, probeSettingsChanged=${probeSettingsChanged}`);

      if (pollOrTimezoneChanged || probeSettingsChanged) {
        logger.info('[RESTART-CHECK] Setting needsRestart=true');
        setNeedsRestart(true);
        if (pollOrTimezoneChanged) {
          logger.info('Stats polling or timezone changed - backend restart recommended');
        }
        if (probeSettingsChanged) {
          logger.info('Probe settings changed - backend restart required for schedule changes to take effect');
        }
      } else {
        logger.info('[RESTART-CHECK] No restart-requiring changes detected');
      }
      onSaved();
      // Clear success message after 8 seconds
      setTimeout(() => setSaveSuccess(false), 8000);
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to save settings';
      logger.error('Failed to save settings', err);
      setError(errorMessage);
    } finally {
      setLoading(false);
    }
  };

  const handleRestart = async () => {
    setRestarting(true);
    setRestartResult(null);
    try {
      const result = await api.restartServices();
      setRestartResult(result);
      if (result.success) {
        setOriginalPollInterval(statsPollInterval);
        setOriginalTimezone(userTimezone);
        setOriginalAutoReorder(autoReorderAfterProbe);
        setOriginalRefreshM3usBeforeProbe(refreshM3usBeforeProbe);
        setNeedsRestart(false);
        // Clear result after 3 seconds
        setTimeout(() => setRestartResult(null), 3000);
      }
    } catch (err) {
      setRestartResult({ success: false, message: 'Failed to restart services' });
    } finally {
      setRestarting(false);
    }
  };

  const handleProbeAllStreams = async () => {
    setProbingAll(true);
    setProbeAllResult(null);
    setProbeProgress(null);
    try {
      // Pass currently selected groups (even if not saved)
      const result = await api.probeAllStreams(probeChannelGroups);
      setProbeAllResult({ success: true, message: result.message || 'Background probe started' });
      // Start polling for progress
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to start probe';
      setProbeAllResult({ success: false, message: errorMessage });
      setProbingAll(false);
    }
  };

  const handleCancelProbe = async () => {
    try {
      const result = await api.cancelProbe();
      setProbeAllResult({ success: true, message: result.message || 'Probe cancelled' });
      // Progress polling will detect the cancelled status and update probingAll
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to cancel probe';
      setProbeAllResult({ success: false, message: errorMessage });
    }
  };

  const handleResetProbeState = async () => {
    try {
      const result = await api.resetProbeState();
      setProbeAllResult({ success: true, message: result.message || 'Probe state reset' });
      setProbingAll(false);
      setProbeProgress(null);
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to reset probe state';
      setProbeAllResult({ success: false, message: errorMessage });
    }
  };

  const handleRerunFailed = async () => {
    setShowProbeResultsModal(false);

    // Null check for probeResults
    if (!probeResults || !probeResults.failed_streams) {
      logger.warn('No probe results available');
      return;
    }

    // Extract stream IDs from failed streams
    const failedStreamIds = probeResults.failed_streams.map(stream => stream.id);

    if (failedStreamIds.length === 0) {
      logger.warn('No failed streams to re-probe');
      return;
    }

    logger.info(`Re-probing ${failedStreamIds.length} failed streams`);
    setProbingAll(true);
    setProbeAllResult(null);
    setProbeProgress(null);  // Reset progress to show fresh progress for re-probe

    try {
      // Use probeAllStreams with stream_ids filter for proper progress tracking
      // Skip M3U refresh for re-probes (already have fresh data from initial probe)
      const result = await api.probeAllStreams([], true, failedStreamIds);
      setProbeAllResult({ success: true, message: result.message || `Re-probing ${failedStreamIds.length} failed streams...` });
      // Progress polling will handle the rest - probingAll will be set to false when complete
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to re-probe streams';
      logger.error('Failed to re-probe failed streams', err);
      setProbeAllResult({ success: false, message: errorMessage });
      setProbingAll(false);
    }
  };

  const handleCopyUrl = async (url: string) => {
    const success = await copyToClipboard(url, 'stream URL');
    if (success) {
      setCopiedUrl(url);
      // Clear the "copied" indicator after 2 seconds
      setTimeout(() => setCopiedUrl(null), 2000);
    }
  };

  const handleClearStream = async (streamId: number) => {
    try {
      await api.clearStreamStats([streamId]);
      logger.info(`Cleared stats for stream ${streamId}`);
    } catch (err) {
      logger.error('Failed to clear stream stats', err);
    }
  };

  const handleShowHistoryResults = async (historyEntry: ProbeHistoryEntry, type: 'success' | 'failed' | 'skipped') => {
    // Use the history entry's streams for the modal
    setProbeResults({
      success_streams: historyEntry.success_streams,
      failed_streams: historyEntry.failed_streams,
      skipped_streams: historyEntry.skipped_streams || [],
      success_count: historyEntry.success_count,
      failed_count: historyEntry.failed_count,
      skipped_count: historyEntry.skipped_count || 0,
    });
    setProbeResultsType(type);
    setShowProbeResultsModal(true);
  };

  const handleShowReorderResults = (historyEntry: ProbeHistoryEntry) => {
    setReorderData(historyEntry.reordered_channels || []);
    setReorderSortConfig(historyEntry.sort_config || null);
    setShowReorderModal(true);
  };

  const formatDuration = (seconds: number): string => {
    if (seconds < 60) return `${seconds}s`;
    const minutes = Math.floor(seconds / 60);
    const secs = seconds % 60;
    if (minutes < 60) return `${minutes}m ${secs}s`;
    const hours = Math.floor(minutes / 60);
    const mins = minutes % 60;
    return `${hours}h ${mins}m`;
  };

  const formatTimestamp = (isoTimestamp: string): string => {
    try {
      const date = new Date(isoTimestamp);
      return date.toLocaleString();
    } catch {
      return isoTimestamp;
    }
  };

  const handleLoadOrphanedGroups = async () => {
    setLoadingOrphaned(true);
    setCleanupResult(null);
    try {
      const result = await api.getOrphanedChannelGroups();
      setOrphanedGroups(result.orphaned_groups);
      if (result.orphaned_groups.length === 0) {
        setCleanupResult('No orphaned groups found. Your database is clean!');
      }
    } catch (err) {
      setCleanupResult(`Failed to load orphaned groups: ${err}`);
    } finally {
      setLoadingOrphaned(false);
    }
  };

  const handleCleanupOrphanedGroups = async () => {
    // Show the confirmation modal
    setShowDeleteModal(true);
  };

  const handleConfirmDelete = async (selectedGroupIds: number[]) => {
    setCleaningOrphaned(true);
    setCleanupResult(null);
    try {
      const result = await api.deleteOrphanedChannelGroups(selectedGroupIds);
      setCleanupResult(result.message);

      if (result.deleted_groups.length > 0) {
        // Reload to refresh the list
        await handleLoadOrphanedGroups();
        // Notify parent to refresh data
        onSaved();
      }

      if (result.failed_groups.length > 0) {
        const failedNames = result.failed_groups.map(g => g.name).join(', ');
        setCleanupResult(`${result.message}. Failed to delete: ${failedNames}`);
      }
    } catch (err) {
      setCleanupResult(`Failed to cleanup orphaned groups: ${err}`);
    } finally {
      setCleaningOrphaned(false);
    }
  };

  const renderGeneralPage = () => (
    <div className="settings-page">
      <div className="settings-page-header">
        <h2>General Settings</h2>
        <p>Configure your Dispatcharr connection.</p>
      </div>

      {error && (
        <div className="error-message">
          <span className="material-icons">error</span>
          {error}
        </div>
      )}

      {testResult && (
        <div className={`test-result ${testResult.success ? 'success' : 'error'}`}>
          <span className="material-icons">
            {testResult.success ? 'check_circle' : 'error'}
          </span>
          {testResult.message}
        </div>
      )}

      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">link</span>
          <h3>Dispatcharr Connection</h3>
        </div>

        <div className="form-group-vertical">
          <label htmlFor="url">Server URL</label>
          <span className="form-description">The URL of your Dispatcharr server (e.g., http://localhost:9191)</span>
          <input
            id="url"
            type="text"
            placeholder="http://localhost:9191"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
        </div>

        <div className="form-group-vertical">
          <label htmlFor="username">Username</label>
          <span className="form-description">Your Dispatcharr admin username</span>
          <input
            id="username"
            type="text"
            placeholder="admin"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
        </div>

        <div className="form-group-vertical">
          <label htmlFor="password">Password</label>
          <span className="form-description">Only required when changing URL or username</span>
          <input
            id="password"
            type="password"
            placeholder="Enter password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>

        <div className="form-group-vertical">
          <button className="btn-test" onClick={handleTest} disabled={testing || loading}>
            <span className="material-icons">wifi_tethering</span>
            {testing ? 'Testing...' : 'Test Connection'}
          </button>
        </div>
      </div>

      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">speed</span>
          <h3>Stats Polling</h3>
        </div>

        <div className="form-group-vertical">
          <label htmlFor="statsPollInterval">Poll interval (seconds)</label>
          <span className="form-description">
            How often to poll Dispatcharr for channel statistics and bandwidth tracking.
            Lower values provide more frequent updates but use more resources.
          </span>
          <div className="threshold-input-group">
            <input
              id="statsPollInterval"
              type="number"
              min="5"
              max="300"
              value={statsPollInterval}
              onChange={(e) => {
                const value = Number(e.target.value);
                if (!isNaN(value)) {
                  setStatsPollInterval(value);
                }
              }}
              onBlur={(e) => {
                const value = Math.max(5, Math.min(300, Number(e.target.value) || 10));
                setStatsPollInterval(value);
              }}
              className="threshold-input"
            />
            <span className="threshold-percent">sec</span>
          </div>

          {needsRestart && (
            <div className="restart-notice">
              <span className="material-icons">info</span>
              <span>Stats settings changed. Restart services to apply.</span>
              <button
                className="btn-restart"
                onClick={handleRestart}
                disabled={restarting}
              >
                <span className={`material-icons ${restarting ? 'spinning' : ''}`}>
                  {restarting ? 'sync' : 'restart_alt'}
                </span>
                {restarting ? 'Restarting...' : 'Restart Now'}
              </button>
            </div>
          )}

          {restartResult && (
            <div className={`restart-result ${restartResult.success ? 'success' : 'error'}`}>
              <span className="material-icons">
                {restartResult.success ? 'check_circle' : 'error'}
              </span>
              {restartResult.message}
            </div>
          )}
        </div>

        <div className="form-group-vertical">
          <label htmlFor="userTimezone">Timezone</label>
          <span className="form-description">
            Timezone used for daily bandwidth statistics and scheduled probe times. "Today" will roll over at midnight in your selected timezone, and scheduled probes will run at the configured time in this timezone.
          </span>
          <select
            id="userTimezone"
            value={userTimezone}
            onChange={(e) => setUserTimezone(e.target.value)}
            className="timezone-select"
          >
            <option value="">UTC (Default)</option>
            <optgroup label="US & Canada">
              <option value="America/New_York">Eastern Time (ET)</option>
              <option value="America/Chicago">Central Time (CT)</option>
              <option value="America/Denver">Mountain Time (MT)</option>
              <option value="America/Los_Angeles">Pacific Time (PT)</option>
              <option value="America/Anchorage">Alaska Time (AKT)</option>
              <option value="Pacific/Honolulu">Hawaii Time (HT)</option>
            </optgroup>
            <optgroup label="Europe">
              <option value="Europe/London">London (GMT/BST)</option>
              <option value="Europe/Paris">Paris (CET/CEST)</option>
              <option value="Europe/Berlin">Berlin (CET/CEST)</option>
              <option value="Europe/Amsterdam">Amsterdam (CET/CEST)</option>
              <option value="Europe/Rome">Rome (CET/CEST)</option>
              <option value="Europe/Madrid">Madrid (CET/CEST)</option>
            </optgroup>
            <optgroup label="Asia & Pacific">
              <option value="Asia/Tokyo">Tokyo (JST)</option>
              <option value="Asia/Shanghai">Shanghai (CST)</option>
              <option value="Asia/Hong_Kong">Hong Kong (HKT)</option>
              <option value="Asia/Singapore">Singapore (SGT)</option>
              <option value="Asia/Dubai">Dubai (GST)</option>
              <option value="Australia/Sydney">Sydney (AEST/AEDT)</option>
              <option value="Australia/Melbourne">Melbourne (AEST/AEDT)</option>
              <option value="Pacific/Auckland">Auckland (NZST/NZDT)</option>
            </optgroup>
          </select>
        </div>
      </div>

      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">bug_report</span>
          <h3>Logging</h3>
        </div>

        <div className="form-group-vertical">
          <label htmlFor="backendLogLevel">Backend Log Level</label>
          <span className="form-description">
            Controls Python backend logging level. Changes apply immediately.
            Check Docker logs to see backend messages.
          </span>
          <select
            id="backendLogLevel"
            value={backendLogLevel}
            onChange={(e) => setBackendLogLevel(e.target.value)}
          >
            <option value="DEBUG">DEBUG - Show all messages including debug info</option>
            <option value="INFO">INFO - Show informational messages and above</option>
            <option value="WARNING">WARNING - Show warnings and errors only</option>
            <option value="ERROR">ERROR - Show errors only</option>
            <option value="CRITICAL">CRITICAL - Show only critical errors</option>
          </select>
        </div>

        <div className="form-group-vertical">
          <label htmlFor="frontendLogLevel">Frontend Log Level</label>
          <span className="form-description">
            Controls browser console logging level. Changes apply immediately.
            Open browser DevTools (F12) to see frontend messages.
          </span>
          <select
            id="frontendLogLevel"
            value={frontendLogLevel}
            onChange={(e) => setFrontendLogLevel(e.target.value)}
          >
            <option value="DEBUG">DEBUG - Show all messages including debug info</option>
            <option value="INFO">INFO - Show informational messages and above</option>
            <option value="WARN">WARN - Show warnings and errors only</option>
            <option value="ERROR">ERROR - Show errors only</option>
          </select>
        </div>
      </div>

      {saveSuccess && (
        <div className="save-success">
          <span className="material-icons">check_circle</span>
          Settings saved successfully
        </div>
      )}

      <div className="settings-actions">
        <div className="settings-actions-left" />
        <button className="btn-primary" onClick={handleSave} disabled={loading}>
          <span className="material-icons">save</span>
          {loading ? 'Saving...' : 'Save Settings'}
        </button>
      </div>
    </div>
  );

  const renderAppearancePage = () => (
    <div className="settings-page">
      <div className="settings-page-header">
        <h2>Appearance</h2>
        <p>Customize how the app displays information.</p>
      </div>

      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">palette</span>
          <h3>Theme</h3>
        </div>

        <div className="theme-selector">
          <label className={`theme-option ${theme === 'dark' ? 'active' : ''}`}>
            <input
              type="radio"
              name="theme"
              value="dark"
              checked={theme === 'dark'}
              onChange={() => handleThemeChange('dark')}
            />
            <span className="theme-preview dark-preview">
              <span className="material-icons">dark_mode</span>
            </span>
            <span className="theme-label">Dark</span>
            <span className="theme-description">Default dark theme for low-light environments</span>
          </label>

          <label className={`theme-option ${theme === 'light' ? 'active' : ''}`}>
            <input
              type="radio"
              name="theme"
              value="light"
              checked={theme === 'light'}
              onChange={() => handleThemeChange('light')}
            />
            <span className="theme-preview light-preview">
              <span className="material-icons">light_mode</span>
            </span>
            <span className="theme-label">Light</span>
            <span className="theme-description">Bright theme for well-lit environments</span>
          </label>

          <label className={`theme-option ${theme === 'high-contrast' ? 'active' : ''}`}>
            <input
              type="radio"
              name="theme"
              value="high-contrast"
              checked={theme === 'high-contrast'}
              onChange={() => handleThemeChange('high-contrast')}
            />
            <span className="theme-preview high-contrast-preview">
              <span className="material-icons">contrast</span>
            </span>
            <span className="theme-label">High Contrast</span>
            <span className="theme-description">Maximum contrast for accessibility</span>
          </label>
        </div>
      </div>

      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">visibility</span>
          <h3>Display Options</h3>
        </div>

        <div className="checkbox-group">
          <input
            id="showStreamUrls"
            type="checkbox"
            checked={showStreamUrls}
            onChange={(e) => setShowStreamUrls(e.target.checked)}
          />
          <div className="checkbox-content">
            <label htmlFor="showStreamUrls">Show stream URLs in the UI</label>
            <p>
              Display the full stream URL below each stream and channel. Disable this for cleaner
              screenshots or to hide sensitive URL information.
            </p>
          </div>
        </div>

        <div className="checkbox-group">
          <input
            id="hideAutoSyncGroups"
            type="checkbox"
            checked={hideAutoSyncGroups}
            onChange={(e) => setHideAutoSyncGroups(e.target.checked)}
          />
          <div className="checkbox-content">
            <label htmlFor="hideAutoSyncGroups">Hide auto-sync groups by default</label>
            <p>
              Automatically hide channel groups that are managed by Dispatcharr's M3U auto-sync feature.
              You can still show them using the filter in the Channel Manager tab.
            </p>
          </div>
        </div>

        <div className="checkbox-group">
          <input
            id="hideUngroupedStreams"
            type="checkbox"
            checked={hideUngroupedStreams}
            onChange={(e) => setHideUngroupedStreams(e.target.checked)}
          />
          <div className="checkbox-content">
            <label htmlFor="hideUngroupedStreams">Hide ungrouped streams</label>
            <p>
              Hide streams that don't have a group assigned (no group-title in M3U).
              These streams appear under "Ungrouped" in the Streams pane.
            </p>
          </div>
        </div>

        <div className="checkbox-group">
          <input
            id="hideEpgUrls"
            type="checkbox"
            checked={hideEpgUrls}
            onChange={(e) => setHideEpgUrls(e.target.checked)}
          />
          <div className="checkbox-content">
            <label htmlFor="hideEpgUrls">Hide EPG URLs</label>
            <p>
              Hide EPG source URLs in the EPG Manager tab. Enable this to prevent
              accidental exposure of sensitive EPG URLs in screenshots or screen shares.
            </p>
          </div>
        </div>

        <div className="checkbox-group">
          <input
            id="hideM3uUrls"
            type="checkbox"
            checked={hideM3uUrls}
            onChange={(e) => setHideM3uUrls(e.target.checked)}
          />
          <div className="checkbox-content">
            <label htmlFor="hideM3uUrls">Hide M3U URLs</label>
            <p>
              Hide M3U server URLs in the M3U Manager tab. Enable this to prevent
              accidental exposure of sensitive M3U URLs in screenshots or screen shares.
            </p>
          </div>
        </div>

        <div className="form-group">
          <label htmlFor="gracenoteConflictMode">Gracenote ID Conflict Handling</label>
          <select
            id="gracenoteConflictMode"
            value={gracenoteConflictMode}
            onChange={(e) => setGracenoteConflictMode(e.target.value as GracenoteConflictMode)}
          >
            <option value="ask">Ask me what to do (show conflict dialog)</option>
            <option value="skip">Skip channels with existing IDs</option>
            <option value="overwrite">Automatically overwrite existing IDs</option>
          </select>
          <p className="form-hint">
            When assigning Gracenote IDs, this controls what happens if a channel already has a
            different Gracenote ID. Choose "Ask" to review conflicts, "Skip" to leave existing
            IDs unchanged, or "Overwrite" to always replace with new IDs.
          </p>
        </div>
      </div>

      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">play_circle</span>
          <h3>VLC Integration</h3>
        </div>

        <div className="form-group">
          <label htmlFor="vlcOpenBehavior">Open in VLC Behavior</label>
          <select
            id="vlcOpenBehavior"
            value={vlcOpenBehavior}
            onChange={(e) => setVlcOpenBehavior(e.target.value)}
          >
            <option value="protocol_only">Try VLC Protocol (show helper if it fails)</option>
            <option value="m3u_fallback">Try VLC Protocol, then fallback to M3U download</option>
            <option value="m3u_only">Always download M3U file</option>
          </select>
          <p className="form-hint">
            Controls what happens when you click "Open in VLC". The vlc:// protocol requires
            browser extensions on some platforms. If "protocol_only" fails, a helper modal
            will guide you to install the necessary extension.
          </p>
        </div>
      </div>

      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">notifications</span>
          <h3>Notifications</h3>
        </div>

        <p className="form-hint" style={{ marginBottom: '1rem' }}>
          ECM displays toast notifications for important events like task completions,
          errors, and system messages. Notification history is accessible via the bell
          icon in the header. Configure alert methods in Settings → Alert Methods to
          receive notifications via Discord, Telegram, or email.
        </p>

        <div className="form-group">
          <label>Notification History</label>
          <p className="form-hint">
            Clear old notifications from the history. This removes notifications from the
            dropdown but does not affect alert methods.
          </p>
          <div style={{ display: 'flex', gap: '0.75rem', marginTop: '0.75rem' }}>
            <button
              type="button"
              className="btn-secondary"
              onClick={async () => {
                try {
                  const result = await api.clearNotifications(true);
                  alert(`Cleared ${result.deleted} read notification(s)`);
                } catch (err) {
                  logger.error('Failed to clear notifications', err);
                  alert('Failed to clear notifications');
                }
              }}
            >
              <span className="material-icons" style={{ fontSize: '18px' }}>done_all</span>
              Clear Read
            </button>
            <button
              type="button"
              className="btn-secondary"
              onClick={async () => {
                if (!confirm('Are you sure you want to clear ALL notifications?')) return;
                try {
                  const result = await api.clearNotifications(false);
                  alert(`Cleared ${result.deleted} notification(s)`);
                } catch (err) {
                  logger.error('Failed to clear notifications', err);
                  alert('Failed to clear notifications');
                }
              }}
              style={{ color: 'var(--error)' }}
            >
              <span className="material-icons" style={{ fontSize: '18px' }}>delete_sweep</span>
              Clear All
            </button>
          </div>
        </div>
      </div>

      {saveSuccess && (
        <div className="save-success">
          <span className="material-icons">check_circle</span>
          Settings saved successfully
        </div>
      )}

      <div className="settings-actions">
        <div className="settings-actions-left" />
        <button className="btn-primary" onClick={handleSave} disabled={loading}>
          <span className="material-icons">save</span>
          {loading ? 'Saving...' : 'Save Settings'}
        </button>
      </div>
    </div>
  );

  const renderChannelDefaultsPage = () => (
    <div className="settings-page">
      <div className="settings-page-header">
        <h2>Channel Defaults</h2>
        <p>Configure default options for bulk channel creation.</p>
      </div>

      {error && (
        <div className="error-message">
          <span className="material-icons">error</span>
          {error}
        </div>
      )}

      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">edit</span>
          <h3>Channel Naming</h3>
        </div>

        <div className="checkbox-group">
          <input
            id="autoRename"
            type="checkbox"
            checked={autoRenameChannelNumber}
            onChange={(e) => setAutoRenameChannelNumber(e.target.checked)}
          />
          <div className="checkbox-content">
            <label htmlFor="autoRename">Auto-rename channel when number changes</label>
            <p>
              When enabled, if a channel name contains the old channel number, it will be
              automatically updated to the new number.
            </p>
          </div>
        </div>

        <div className="checkbox-group">
          <input
            id="includeNumber"
            type="checkbox"
            checked={includeChannelNumberInName}
            onChange={(e) => setIncludeChannelNumberInName(e.target.checked)}
          />
          <div className="checkbox-content">
            <label htmlFor="includeNumber">Include channel number in name</label>
            <p>
              Add the channel number as a prefix when creating channels (e.g., "101 - Sports Channel").
            </p>
          </div>
        </div>

        {includeChannelNumberInName && (
          <div className="separator-row indent">
            <span className="separator-row-label">Separator:</span>
            <div className="separator-buttons">
              <button
                type="button"
                className={`separator-btn ${channelNumberSeparator === '-' ? 'active' : ''}`}
                onClick={() => setChannelNumberSeparator('-')}
              >
                -
              </button>
              <button
                type="button"
                className={`separator-btn ${channelNumberSeparator === ':' ? 'active' : ''}`}
                onClick={() => setChannelNumberSeparator(':')}
              >
                :
              </button>
              <button
                type="button"
                className={`separator-btn ${channelNumberSeparator === '|' ? 'active' : ''}`}
                onClick={() => setChannelNumberSeparator('|')}
              >
                |
              </button>
            </div>
            <span className="separator-preview">e.g., "101 {channelNumberSeparator} Sports Channel"</span>
          </div>
        )}
      </div>

      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">schedule</span>
          <h3>Timezone Preference</h3>
        </div>

        <div className="form-group">
          <label htmlFor="timezone">Default timezone for regional channel variants</label>
          <select
            id="timezone"
            value={timezonePreference}
            onChange={(e) => setTimezonePreference(e.target.value)}
          >
            <option value="east">East Coast (prefer East feeds)</option>
            <option value="west">West Coast (prefer West feeds)</option>
            <option value="both">Keep Both (create separate channels)</option>
          </select>
          <p className="form-hint">
            When streams have East/West variants, this determines which to use by default.
          </p>
        </div>
      </div>

      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">people</span>
          <h3>Channel Profiles</h3>
        </div>

        <div className="form-group">
          <label>Default profiles for new channels</label>
          <p className="form-hint" style={{ marginTop: 0, marginBottom: '0.75rem' }}>
            Newly created channels will automatically be added to the selected profiles.
            {channelProfiles.length === 0 && (
              <span className="form-hint-warning"> No profiles available. Create profiles in the Channel Manager.</span>
            )}
          </p>
          {channelProfiles.length > 0 && (
            <div className="profile-checkbox-list">
              {channelProfiles.map((profile) => (
                <label key={profile.id} className="profile-checkbox">
                  <input
                    type="checkbox"
                    checked={defaultChannelProfileIds.includes(profile.id)}
                    onChange={(e) => {
                      if (e.target.checked) {
                        setDefaultChannelProfileIds([...defaultChannelProfileIds, profile.id]);
                      } else {
                        setDefaultChannelProfileIds(defaultChannelProfileIds.filter(id => id !== profile.id));
                      }
                    }}
                  />
                  <span className="profile-checkbox-label">{profile.name}</span>
                </label>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">live_tv</span>
          <h3>EPG Matching</h3>
        </div>

        <div className="form-group">
          <div className="threshold-label-row">
            <label htmlFor="epgThreshold">Auto-match confidence threshold</label>
            <div className="threshold-input-group">
              <input
                id="epgThreshold"
                type="number"
                min="0"
                max="100"
                value={epgAutoMatchThreshold}
                onChange={(e) => {
                  const value = Math.max(0, Math.min(100, Number(e.target.value) || 0));
                  setEpgAutoMatchThreshold(value);
                }}
                className="threshold-input"
              />
              <span className="threshold-percent">%</span>
            </div>
          </div>
          <p className="form-hint">
            EPG matches with a confidence score at or above this threshold will be automatically assigned.
            Lower values match more channels automatically but may be less accurate.
            Set to 0 to require manual review for all matches.
          </p>
        </div>
      </div>

      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">sort</span>
          <h3>Smart Sort Priority</h3>
        </div>

        <div className="form-group">
          <p className="form-hint" style={{ marginTop: 0, marginBottom: '0.75rem' }}>
            Configure which criteria are used for stream sorting. Check/uncheck to enable/disable,
            drag to reorder priority. Enabled criteria appear in the sort dropdown and are used by Smart Sort.
          </p>

          <DndContext
            sensors={sensors}
            collisionDetection={closestCenter}
            onDragEnd={handleSortPriorityDragEnd}
          >
            <SortableContext items={streamSortPriority} strategy={verticalListSortingStrategy}>
              <div className="sort-priority-list">
                {streamSortPriority.map((criterion, index) => (
                  <SortablePriorityItem
                    key={criterion}
                    id={criterion}
                    index={index}
                    enabled={streamSortEnabled[criterion]}
                    onToggleEnabled={(id) => setStreamSortEnabled(prev => ({ ...prev, [id]: !prev[id] }))}
                  />
                ))}
              </div>
            </SortableContext>
          </DndContext>
        </div>

        <div className="form-group">
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={deprioritizeFailedStreams}
              onChange={(e) => setDeprioritizeFailedStreams(e.target.checked)}
            />
            <span>Deprioritize Failed Streams</span>
          </label>
          <p className="form-hint">
            When enabled, streams that fail probe checks (dead/timeout) will automatically be sorted to the bottom when using stream sorting features.
            This ensures working streams are prioritized for playback.
          </p>
        </div>
      </div>

      {saveSuccess && (
        <div className="save-success">
          <span className="material-icons">check_circle</span>
          Settings saved successfully
        </div>
      )}

      <div className="settings-actions">
        <div className="settings-actions-left" />
        <button className="btn-primary" onClick={handleSave} disabled={loading}>
          <span className="material-icons">save</span>
          {loading ? 'Saving...' : 'Save Settings'}
        </button>
      </div>
    </div>
  );

  const renderNormalizationPage = () => (
    <div className="settings-page">
      <div className="settings-page-header">
        <h2>Channel Normalization</h2>
        <p>Configure tag-based patterns for cleaning up channel names during bulk channel creation.</p>
      </div>

      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">public</span>
          <h3>Country Prefix Format</h3>
        </div>
        <p className="form-hint" style={{ marginBottom: '1rem' }}>
          Use the Country tag group below to strip country prefixes. Enable this option to instead
          keep them with a consistent separator format.
        </p>

        <div className="form-group">
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={includeCountryInName}
              onChange={(e) => {
                setIncludeCountryInName(e.target.checked);
                if (e.target.checked) {
                  setRemoveCountryPrefix(false);
                }
              }}
            />
            <span>Normalize country prefix format</span>
          </label>
        </div>

        {includeCountryInName && (
          <div className="separator-row indent">
            <span className="separator-row-label">Separator:</span>
            <div className="separator-buttons">
              <button
                type="button"
                className={`separator-btn ${countrySeparator === '-' ? 'active' : ''}`}
                onClick={() => setCountrySeparator('-')}
              >
                -
              </button>
              <button
                type="button"
                className={`separator-btn ${countrySeparator === ':' ? 'active' : ''}`}
                onClick={() => setCountrySeparator(':')}
              >
                :
              </button>
              <button
                type="button"
                className={`separator-btn ${countrySeparator === '|' ? 'active' : ''}`}
                onClick={() => setCountrySeparator('|')}
              >
                |
              </button>
            </div>
            <span className="separator-preview">e.g., "US {countrySeparator} Sports Channel"</span>
          </div>
        )}
      </div>

      <NormalizationTagsSection
        settings={normalizationSettings}
        onChange={handleNormalizationSettingsChange}
      />

      {/* Preview Section */}
      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">preview</span>
          <h3>Preview Normalization</h3>
        </div>
        <p className="form-hint" style={{ marginBottom: '1rem' }}>
          Test how a stream name will be normalized with the current settings.
        </p>
        <div className="form-group">
          <label htmlFor="normalization-preview-input">Stream Name</label>
          <input
            id="normalization-preview-input"
            type="text"
            value={normalizationPreviewInput}
            onChange={(e) => setNormalizationPreviewInput(e.target.value)}
            placeholder="Enter a stream name to preview (e.g., US: ESPN HD 1080p)"
            style={{ width: '100%' }}
          />
        </div>
        {normalizationPreviewInput.trim() && (
          <div className="normalization-preview-result">
            <div className="normalization-preview-label">Result:</div>
            <div className="normalization-preview-value">
              {normalizedPreviewResult || <span className="text-muted">(empty result)</span>}
            </div>
            {normalizedPreviewResult !== normalizationPreviewInput && (
              <div className="normalization-preview-comparison">
                <span className="material-icons" style={{ fontSize: '16px', color: 'var(--success-color)' }}>check_circle</span>
                <span className="text-muted">Name was normalized</span>
              </div>
            )}
          </div>
        )}
      </div>

      {saveSuccess && (
        <div className="save-success">
          <span className="material-icons">check_circle</span>
          Settings saved successfully
        </div>
      )}

      <div className="settings-actions">
        <div className="settings-actions-left" />
        <button className="btn-primary" onClick={handleSave} disabled={loading}>
          <span className="material-icons">save</span>
          {loading ? 'Saving...' : 'Save Settings'}
        </button>
      </div>
    </div>
  );

  const renderMaintenancePage = () => (
    <div className="settings-page">
      <div className="settings-page-header">
        <h2>Maintenance</h2>
        <p>Stream probing and database cleanup tools.</p>
      </div>

      {/* Stream Probing Section */}
      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">speed</span>
          <h3>Stream Probing</h3>
        </div>
        <p className="form-hint" style={{ marginBottom: '1rem' }}>
          Use ffprobe to gather stream metadata (resolution, FPS, codec, audio channels).
          Scheduled probing is controlled via the Scheduled Tasks section.
        </p>

        <div className="settings-group" style={{ marginTop: '1rem' }}>
            <div className="form-group-vertical">
              <label htmlFor="probeBatchSize">Batch size</label>
              <span className="form-description">Streams to probe per scheduled cycle (1-{totalStreamCount})</span>
              <input
                id="probeBatchSize"
                type="number"
                min="1"
                max={totalStreamCount}
                value={streamProbeBatchSize}
                onChange={(e) => setStreamProbeBatchSize(Math.max(1, Math.min(totalStreamCount, parseInt(e.target.value) || 10)))}
              />
            </div>

            <div className="form-group-vertical">
              <label htmlFor="probeTimeout">Probe timeout (seconds)</label>
              <span className="form-description">Timeout for each probe attempt (5-120 seconds)</span>
              <input
                id="probeTimeout"
                type="number"
                min="5"
                max="120"
                value={streamProbeTimeout}
                onChange={(e) => setStreamProbeTimeout(Math.max(5, Math.min(120, parseInt(e.target.value) || 30)))}
              />
            </div>

            <div className="form-group-vertical">
              <label htmlFor="bitrateSampleDuration">Bitrate measurement duration</label>
              <span className="form-description">How long to sample streams when measuring bitrate</span>
              <select
                id="bitrateSampleDuration"
                value={bitrateSampleDuration}
                onChange={(e) => setBitrateSampleDuration(Number(e.target.value))}
              >
                <option value={10}>10 seconds</option>
                <option value={20}>20 seconds</option>
                <option value={30}>30 seconds</option>
              </select>
            </div>

            <div className="form-group-vertical">
              <label htmlFor="streamFetchPageLimit">Stream fetch page limit</label>
              <span className="form-description">
                Max pages when fetching streams from Dispatcharr (×500 = max streams).
                Default 200 = 100K streams. Increase if you have more streams.
              </span>
              <input
                id="streamFetchPageLimit"
                type="number"
                min="50"
                max="1000"
                value={streamFetchPageLimit}
                onChange={(e) => setStreamFetchPageLimit(Math.max(50, Math.min(1000, parseInt(e.target.value) || 200)))}
              />
            </div>

            <div className="form-group-vertical">
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={parallelProbingEnabled}
                  onChange={(e) => setParallelProbingEnabled(e.target.checked)}
                />
                Enable parallel probing
              </label>
              <span className="form-description">
                When enabled, streams from different M3U accounts are probed simultaneously for faster completion.
                Disable for sequential one-at-a-time probing.
              </span>
            </div>

            {parallelProbingEnabled && (
              <div className="form-group-vertical">
                <label htmlFor="maxConcurrentProbes">Max concurrent probes</label>
                <span className="form-description">
                  Maximum number of streams to probe simultaneously (1-16).
                  {m3uAccountsMaxStreams.length > 0 && (
                    <>
                      {' '}Based on your M3U providers:{' '}
                      {m3uAccountsMaxStreams.map((a, i) => (
                        <span key={a.name}>
                          {i > 0 && ', '}
                          <strong>{a.name}</strong>: {a.max_streams} streams
                        </span>
                      ))}
                      . Set this to the lowest max_streams value to avoid rate limiting.
                    </>
                  )}
                </span>
                <input
                  id="maxConcurrentProbes"
                  type="text"
                  inputMode="numeric"
                  pattern="[0-9]*"
                  value={maxConcurrentProbes}
                  onChange={(e) => {
                    const val = e.target.value.replace(/[^0-9]/g, '');
                    if (val === '') {
                      setMaxConcurrentProbes('' as unknown as number);
                    } else {
                      setMaxConcurrentProbes(parseInt(val, 10));
                    }
                  }}
                  onBlur={(e) => {
                    const val = parseInt(e.target.value, 10);
                    setMaxConcurrentProbes(isNaN(val) ? 8 : Math.max(1, Math.min(16, val)));
                  }}
                />
                {m3uAccountsMaxStreams.length > 0 && (
                  <span className="form-hint">
                    Recommended: {Math.min(...m3uAccountsMaxStreams.map(a => a.max_streams), 8)} (lowest provider limit)
                  </span>
                )}
              </div>
            )}

            <div className="form-group-vertical">
              <label htmlFor="skipRecentlyProbedHours">Skip recently probed streams (hours)</label>
              <span className="form-description">
                Skip streams that were successfully probed within the last N hours. Set to 0 to always probe all streams.
                This prevents excessive probing requests when running multiple checks in succession.
              </span>
              <input
                id="skipRecentlyProbedHours"
                type="number"
                min="0"
                max="168"
                value={skipRecentlyProbedHours}
                onChange={(e) => setSkipRecentlyProbedHours(Math.max(0, Math.min(168, parseInt(e.target.value) || 0)))}
              />
            </div>

            <div className="form-group-vertical">
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={refreshM3usBeforeProbe}
                  onChange={(e) => setRefreshM3usBeforeProbe(e.target.checked)}
                />
                Refresh M3Us before probing
              </label>
              <span className="form-description">
                When enabled, all M3U accounts will be refreshed before starting the probe to ensure latest stream information is used.
              </span>
            </div>

            <div className="form-group-vertical">
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={autoReorderAfterProbe}
                  onChange={(e) => setAutoReorderAfterProbe(e.target.checked)}
                />
                Auto-reorder streams after probe
              </label>
              <span className="form-description">
                When enabled, streams within channels will be automatically reordered using smart sort after probe completes.
                Failed streams are deprioritized, and working streams are sorted by resolution, bitrate, and framerate.
              </span>
            </div>

            {needsRestart && (
              <div className="restart-notice">
                <span className="material-icons">info</span>
                <span>Probe settings changed. Restart services for schedule changes to take effect.</span>
                <button
                  className="btn-restart"
                  onClick={handleRestart}
                  disabled={restarting}
                >
                  <span className={`material-icons ${restarting ? 'spinning' : ''}`}>
                    {restarting ? 'sync' : 'restart_alt'}
                  </span>
                  {restarting ? 'Restarting...' : 'Restart Now'}
                </button>
              </div>
            )}

            {restartResult && (
              <div className={`restart-result ${restartResult.success ? 'success' : 'error'}`}>
                <span className="material-icons">
                  {restartResult.success ? 'check_circle' : 'error'}
                </span>
                {restartResult.message}
              </div>
            )}

            <div className="form-group-vertical">
              <label>Channel groups to probe</label>
              <span className="form-description">
                {availableChannelGroups.length === 0
                  ? 'No groups with streams available.'
                  : 'Select which groups to probe. All groups are selected by default.'}
              </span>
              <button
                type="button"
                className="btn-secondary"
                onClick={() => {
                  setTempProbeChannelGroups([...probeChannelGroups]);
                  setShowGroupSelectModal(true);
                }}
                disabled={availableChannelGroups.length === 0}
              >
                <span className="material-icons">filter_list</span>
                {probeChannelGroups.length === availableChannelGroups.length
                  ? `All ${availableChannelGroups.length} group${availableChannelGroups.length !== 1 ? 's' : ''}`
                  : `${probeChannelGroups.length} of ${availableChannelGroups.length} group${availableChannelGroups.length !== 1 ? 's' : ''}`}
              </button>
            </div>
          </div>
        </div>

      {/* Probe Status Indicator - shows when probing or has result */}
      {(probingAll || probeAllResult) && (
        <div className="settings-section" style={{ padding: '1rem' }}>
          {probingAll && (
            <div style={{
              display: 'flex',
              flexDirection: 'column',
              gap: '0.5rem',
              padding: '0.75rem 1rem',
              backgroundColor: 'var(--bg-tertiary)',
              borderRadius: '6px',
              border: '1px solid var(--accent-primary)'
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                <span className="material-icons spinning" style={{ color: 'var(--accent-primary)' }}>sync</span>
                <span>
                  {probeProgress
                    ? `Probing streams... ${probeProgress.current}/${probeProgress.total} (${probeProgress.percentage}%)`
                    : 'Starting probe...'}
                </span>
              </div>
              {probeProgress && (
                <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginLeft: '2rem' }}>
                  Success: {probeProgress.success_count} | Failed: {probeProgress.failed_count}
                  {probeProgress.skipped_count > 0 && ` | Skipped: ${probeProgress.skipped_count}`}
                </div>
              )}
              {probeProgress?.rate_limited && probeProgress.rate_limited_hosts && probeProgress.rate_limited_hosts.length > 0 && (
                <div style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.5rem',
                  marginTop: '0.25rem',
                  padding: '0.5rem 0.75rem',
                  backgroundColor: 'rgba(243, 156, 18, 0.1)',
                  borderRadius: '4px',
                  border: '1px solid rgba(243, 156, 18, 0.3)',
                  fontSize: '0.85rem',
                  color: '#f39c12'
                }}>
                  <span className="material-icons" style={{ fontSize: '1rem' }}>warning</span>
                  <span>
                    Rate limited by provider{probeProgress.rate_limited_hosts.length > 1 ? 's' : ''}: {' '}
                    {probeProgress.rate_limited_hosts.map((h, i) => (
                      <span key={h.host}>
                        {i > 0 && ', '}
                        {h.host} (waiting {Math.ceil(h.backoff_remaining)}s)
                      </span>
                    ))}
                    {' '}— Consider reducing max concurrent probes
                  </span>
                </div>
              )}
              <div style={{ marginTop: '0.5rem', display: 'flex', justifyContent: 'flex-end' }}>
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={handleCancelProbe}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '0.25rem',
                    padding: '0.4rem 0.8rem',
                    fontSize: '0.85rem'
                  }}
                >
                  <span className="material-icons" style={{ fontSize: '1rem' }}>stop</span>
                  Cancel
                </button>
              </div>
            </div>
          )}
          {!probingAll && probeAllResult && (
            <div style={{
              display: 'flex',
              alignItems: 'center',
              gap: '0.75rem',
              padding: '0.75rem 1rem',
              backgroundColor: 'var(--bg-tertiary)',
              borderRadius: '6px',
              border: `1px solid ${probeAllResult.success ? '#2ecc71' : '#e74c3c'}`
            }}>
              <span className="material-icons" style={{ color: probeAllResult.success ? '#2ecc71' : '#e74c3c' }}>
                {probeAllResult.success ? 'check_circle' : 'error'}
              </span>
              <span>{probeAllResult.message}</span>
              <button
                onClick={() => setProbeAllResult(null)}
                style={{
                  marginLeft: 'auto',
                  background: 'none',
                  border: 'none',
                  cursor: 'pointer',
                  color: 'var(--text-secondary)',
                  fontSize: '1.2rem',
                  padding: '0.25rem'
                }}
                title="Dismiss"
              >
                ×
              </button>
            </div>
          )}
        </div>
      )}

      {/* Reset Stuck Probe Section - only show when NOT actively probing */}
      {!probingAll && (
        <div className="settings-section">
          <div className="settings-section-header">
            <span className="material-icons">restart_alt</span>
            <h3>Reset Probe State</h3>
          </div>
          <p className="form-hint" style={{ marginBottom: '1rem' }}>
            If a probe appears stuck or was interrupted (e.g., browser closed during probe),
            use this button to clear the probe state and allow starting a new probe.
          </p>
          <div className="settings-group">
            <div style={{ display: 'flex', justifyContent: 'flex-start' }}>
              <button
                type="button"
                className="btn-secondary"
                onClick={handleResetProbeState}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.5rem'
                }}
              >
                <span className="material-icons">refresh</span>
                Reset Stuck Probe
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Probe History Section */}
      {probeHistory.length > 0 && (
        <div className="settings-section">
          <div className="settings-section-header">
            <span className="material-icons">history</span>
            <h3>Probe History</h3>
          </div>
          <p className="form-hint" style={{ marginBottom: '1rem' }}>
            Recent probe runs. Click on success/failed counts to view stream details.
          </p>

          <div className="probe-history-list">
            {probeHistory.map((entry, index) => (
              <div key={index} className="probe-history-item" style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '0.75rem 1rem',
                backgroundColor: 'var(--bg-tertiary)',
                borderRadius: '6px',
                marginBottom: '0.5rem',
                border: '1px solid var(--border-color)'
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
                  <span className="material-icons" style={{
                    color: entry.status === 'completed' ? '#2ecc71' : entry.status === 'failed' ? '#e74c3c' : '#f39c12'
                  }}>
                    {entry.status === 'completed' ? 'check_circle' : entry.status === 'failed' ? 'error' : 'warning'}
                  </span>
                  <div>
                    <div style={{ fontWeight: '500', fontSize: '14px' }}>
                      {formatTimestamp(entry.timestamp)}
                    </div>
                    <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                      {entry.total} streams in {formatDuration(entry.duration_seconds)}
                      {entry.error && <span style={{ color: '#e74c3c' }}> - {entry.error}</span>}
                    </div>
                  </div>
                </div>
                <div style={{ display: 'flex', gap: '0.5rem' }}>
                  <button
                    className="probe-history-btn success"
                    onClick={() => handleShowHistoryResults(entry, 'success')}
                    style={{
                      padding: '0.4rem 0.8rem',
                      fontSize: '13px',
                      backgroundColor: 'rgba(46, 204, 113, 0.15)',
                      color: '#2ecc71',
                      border: '1px solid rgba(46, 204, 113, 0.3)',
                      borderRadius: '4px',
                      cursor: 'pointer',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '0.3rem'
                    }}
                    title="View successful streams"
                  >
                    <span className="material-icons" style={{ fontSize: '16px' }}>check</span>
                    {entry.success_count}
                  </button>
                  <button
                    className="probe-history-btn failed"
                    onClick={() => handleShowHistoryResults(entry, 'failed')}
                    style={{
                      padding: '0.4rem 0.8rem',
                      fontSize: '13px',
                      backgroundColor: 'rgba(231, 76, 60, 0.15)',
                      color: '#e74c3c',
                      border: '1px solid rgba(231, 76, 60, 0.3)',
                      borderRadius: '4px',
                      cursor: 'pointer',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '0.3rem'
                    }}
                    title="View failed streams"
                  >
                    <span className="material-icons" style={{ fontSize: '16px' }}>close</span>
                    {entry.failed_count}
                  </button>
                  {(entry.skipped_count ?? 0) > 0 && (
                    <button
                      className="probe-history-btn skipped"
                      onClick={() => handleShowHistoryResults(entry, 'skipped')}
                      style={{
                        padding: '0.4rem 0.8rem',
                        fontSize: '13px',
                        backgroundColor: 'rgba(243, 156, 18, 0.15)',
                        color: '#f39c12',
                        border: '1px solid rgba(243, 156, 18, 0.3)',
                        borderRadius: '4px',
                        cursor: 'pointer',
                        display: 'flex',
                        alignItems: 'center',
                        gap: '0.3rem'
                      }}
                      title="View skipped streams (M3U at max connections)"
                    >
                      <span className="material-icons" style={{ fontSize: '16px' }}>block</span>
                      {entry.skipped_count}
                    </button>
                  )}
                  {(entry.reordered_channels && entry.reordered_channels.length > 0) && (
                    <button
                      className="probe-history-btn reordered"
                      onClick={() => handleShowReorderResults(entry)}
                      style={{
                        padding: '0.4rem 0.8rem',
                        fontSize: '13px',
                        backgroundColor: 'rgba(52, 152, 219, 0.15)',
                        color: '#3498db',
                        border: '1px solid rgba(52, 152, 219, 0.3)',
                        borderRadius: '4px',
                        cursor: 'pointer',
                        display: 'flex',
                        alignItems: 'center',
                        gap: '0.3rem'
                      }}
                      title="View reordered channels"
                    >
                      <span className="material-icons" style={{ fontSize: '16px' }}>sort</span>
                      {entry.reordered_channels.length}
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">folder_delete</span>
          <h3>Orphaned Channel Groups</h3>
        </div>
        <p className="form-hint" style={{ marginBottom: '1rem' }}>
          Channel groups that are not associated with any M3U account and have no content (no streams or channels). These are typically leftover from deleted M3U accounts and are safe to delete.
        </p>

        <div className="settings-group">
          <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
            <button
              className="btn-secondary"
              onClick={handleLoadOrphanedGroups}
              disabled={loadingOrphaned || cleaningOrphaned}
            >
              <span className="material-icons">search</span>
              {loadingOrphaned ? 'Scanning...' : 'Scan for Orphaned Groups'}
            </button>
          </div>

          {orphanedGroups.length > 0 && (
            <div style={{ marginTop: '1rem' }}>
              <p><strong>Found {orphanedGroups.length} orphaned group(s):</strong></p>
              <ul style={{ marginLeft: '1.5rem', marginTop: '0.5rem' }}>
                {orphanedGroups.map(group => (
                  <li key={group.id}>
                    <strong>{group.name}</strong> (ID: {group.id})
                    {group.reason && <span style={{ color: '#888', marginLeft: '0.5rem' }}>- {group.reason}</span>}
                  </li>
                ))}
              </ul>
              <button
                className="btn-danger"
                onClick={handleCleanupOrphanedGroups}
                disabled={cleaningOrphaned || loadingOrphaned}
                style={{ marginTop: '1rem' }}
              >
                <span className="material-icons">delete_forever</span>
                {cleaningOrphaned ? 'Cleaning...' : `Delete ${orphanedGroups.length} Orphaned Group(s)`}
              </button>
            </div>
          )}

          {cleanupResult && (
            <div className={cleanupResult.includes('Failed') ? 'error-message' : 'success-message'} style={{ marginTop: '1rem' }}>
              <span className="material-icons">{cleanupResult.includes('Failed') ? 'error' : 'check_circle'}</span>
              {cleanupResult}
            </div>
          )}
        </div>
      </div>

      {saveSuccess && (
        <div className="save-success">
          <span className="material-icons">check_circle</span>
          Settings saved successfully
        </div>
      )}

      <div className="settings-actions">
        <div className="settings-actions-left" />
        <button className="btn-primary" onClick={handleSave} disabled={loading}>
          <span className="material-icons">save</span>
          {loading ? 'Saving...' : 'Save Settings'}
        </button>
      </div>
    </div>
  );

  return (
    <div className="settings-tab">
      <nav className="settings-sidebar">
        <ul className="settings-nav">
          <li
            className={`settings-nav-item ${activePage === 'general' ? 'active' : ''}`}
            onClick={() => setActivePage('general')}
          >
            <span className="material-icons">settings</span>
            General
          </li>
          <li
            className={`settings-nav-item ${activePage === 'channel-defaults' ? 'active' : ''}`}
            onClick={() => setActivePage('channel-defaults')}
          >
            <span className="material-icons">tv</span>
            Channel Defaults
          </li>
          <li
            className={`settings-nav-item ${activePage === 'normalization' ? 'active' : ''}`}
            onClick={() => setActivePage('normalization')}
          >
            <span className="material-icons">label</span>
            Channel Normalization
          </li>
          <li
            className={`settings-nav-item ${activePage === 'appearance' ? 'active' : ''}`}
            onClick={() => setActivePage('appearance')}
          >
            <span className="material-icons">palette</span>
            Appearance
          </li>
          <li
            className={`settings-nav-item ${activePage === 'scheduled-tasks' ? 'active' : ''}`}
            onClick={() => setActivePage('scheduled-tasks')}
          >
            <span className="material-icons">schedule</span>
            Scheduled Tasks
          </li>
          <li
            className={`settings-nav-item ${activePage === 'alert-methods' ? 'active' : ''}`}
            onClick={() => setActivePage('alert-methods')}
          >
            <span className="material-icons">campaign</span>
            Alert Methods
          </li>
          <li
            className={`settings-nav-item ${activePage === 'maintenance' ? 'active' : ''}`}
            onClick={() => setActivePage('maintenance')}
          >
            <span className="material-icons">build</span>
            Maintenance
          </li>
        </ul>
      </nav>

      <div className="settings-content">
        {activePage === 'general' && renderGeneralPage()}
        {activePage === 'channel-defaults' && renderChannelDefaultsPage()}
        {activePage === 'normalization' && renderNormalizationPage()}
        {activePage === 'appearance' && renderAppearancePage()}
        {activePage === 'scheduled-tasks' && <ScheduledTasksSection userTimezone={userTimezone} />}
        {activePage === 'alert-methods' && <AlertMethodSettings />}
        {activePage === 'maintenance' && renderMaintenancePage()}
      </div>

      <DeleteOrphanedGroupsModal
        isOpen={showDeleteModal}
        onClose={() => setShowDeleteModal(false)}
        onConfirm={handleConfirmDelete}
        groups={orphanedGroups}
      />

      {showProbeResultsModal && probeResults && (
        <div
          className="probe-results-modal-overlay"
          onClick={() => setShowProbeResultsModal(false)}
        >
          <div
            className="probe-results-modal-content"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="probe-results-modal-header">
              <h3 className={probeResultsType === 'success' ? 'success' : probeResultsType === 'skipped' ? 'skipped' : 'failed'}>
                {probeResultsType === 'success' ? '✓ Successful Streams' : probeResultsType === 'skipped' ? '⊘ Skipped Streams' : '✗ Failed Streams'} (
                {probeResultsType === 'success' ? probeResults.success_count : probeResultsType === 'skipped' ? probeResults.skipped_count : probeResults.failed_count})
              </h3>
              <button
                onClick={() => setShowProbeResultsModal(false)}
                className="probe-results-modal-close"
              >
                ×
              </button>
            </div>

            <div className="probe-results-modal-body">
              {(() => {
                const streams = probeResultsType === 'success'
                  ? probeResults.success_streams
                  : probeResultsType === 'skipped'
                  ? probeResults.skipped_streams
                  : probeResults.failed_streams;
                const emptyText = probeResultsType === 'success'
                  ? 'successful'
                  : probeResultsType === 'skipped'
                  ? 'skipped'
                  : 'failed';

                return streams.length === 0 ? (
                  <div className="probe-results-empty">
                    No {emptyText} streams yet
                  </div>
                ) : (
                  <>
                    <div className="probe-results-list">
                      {streams.map((stream) => (
                        <div
                          key={stream.id}
                          className={`probe-result-item ${probeResultsType === 'success' ? 'success' : probeResultsType === 'skipped' ? 'skipped' : 'failed'}`}
                        >
                          <div className="probe-result-item-info">
                            <div className="probe-result-item-name">{stream.name}</div>
                            <div className="probe-result-item-id">ID: {stream.id}</div>
                            {probeResultsType === 'skipped' && 'reason' in stream && (stream as { reason?: string }).reason && (
                              <div className="probe-result-item-reason" style={{ fontSize: '11px', color: '#f39c12', marginTop: '2px' }}>
                                {(stream as { reason?: string }).reason}
                              </div>
                            )}
                            {probeResultsType === 'failed' && 'error' in stream && (stream as { error?: string }).error && (
                              <div className="probe-result-item-error" style={{
                                fontSize: '12px',
                                color: '#e74c3c',
                                marginTop: '4px',
                                padding: '4px 8px',
                                backgroundColor: 'rgba(231, 76, 60, 0.1)',
                                borderRadius: '4px',
                                borderLeft: '3px solid #e74c3c'
                              }}>
                                <strong>Error:</strong> {(stream as { error?: string }).error}
                              </div>
                            )}
                          </div>
                          <div style={{ display: 'flex', gap: '0.25rem', flexShrink: 0 }}>
                            {probeResultsType === 'failed' && (
                              <button
                                onClick={() => handleClearStream(stream.id)}
                                title="Clear probe stats for this stream"
                                style={{
                                  padding: '0.3rem 0.6rem',
                                  fontSize: '12px',
                                  backgroundColor: 'var(--bg-secondary)',
                                  color: 'var(--text-secondary)',
                                  border: '1px solid var(--border-color)',
                                  borderRadius: '4px',
                                  cursor: 'pointer',
                                  display: 'flex',
                                  alignItems: 'center',
                                  gap: '0.25rem'
                                }}
                              >
                                <span className="material-icons" style={{ fontSize: '14px' }}>delete_outline</span>
                                Clear
                              </button>
                            )}
                            {stream.url && (
                              <button
                                className="probe-result-copy-btn"
                                onClick={() => handleCopyUrl(stream.url!)}
                                title={copiedUrl === stream.url ? 'Copied!' : 'Copy stream URL'}
                                style={{
                                  padding: '0.3rem 0.6rem',
                                  fontSize: '12px',
                                  backgroundColor: copiedUrl === stream.url ? 'rgba(46, 204, 113, 0.2)' : 'var(--bg-secondary)',
                                  color: copiedUrl === stream.url ? '#2ecc71' : 'var(--text-secondary)',
                                  border: '1px solid var(--border-color)',
                                  borderRadius: '4px',
                                  cursor: 'pointer',
                                  display: 'flex',
                                  alignItems: 'center',
                                  gap: '0.25rem'
                                }}
                              >
                            <span className="material-icons" style={{ fontSize: '14px' }}>
                              {copiedUrl === stream.url ? 'check' : 'content_copy'}
                            </span>
                            {copiedUrl === stream.url ? 'Copied' : 'Copy URL'}
                          </button>
                        )}
                          </div>
                        </div>
                      ))}
                    </div>
                  </>
                );
              })()}
            </div>

            <div className="probe-results-modal-footer">
              {probeResultsType === 'failed' && probeResults.failed_streams.length > 0 && (
                <button
                  onClick={handleRerunFailed}
                  className="probe-results-rerun-btn"
                  disabled={probingAll}
                >
                  {probingAll ? 'Re-probing...' : 'Re-probe Failed Streams'}
                </button>
              )}
              <button
                onClick={() => setShowProbeResultsModal(false)}
                className="probe-results-close-btn"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Reordered Channels Modal */}
      {showReorderModal && reorderData && (
        <div
          className="probe-results-modal-overlay"
          onClick={() => setShowReorderModal(false)}
        >
          <div
            className="probe-results-modal-content"
            onClick={(e) => e.stopPropagation()}
            style={{ maxWidth: '900px' }}
          >
            <div className="probe-results-modal-header">
              <h3 style={{ color: '#3498db' }}>
                ⇅ Reordered Channels ({reorderData.length})
              </h3>
              <button
                onClick={() => setShowReorderModal(false)}
                className="probe-results-modal-close"
              >
                ×
              </button>
            </div>

            {/* Sort Configuration Summary */}
            {reorderSortConfig && (
              <div style={{
                padding: '0.75rem 1rem',
                backgroundColor: 'rgba(52, 152, 219, 0.1)',
                borderBottom: '1px solid rgba(52, 152, 219, 0.2)',
                fontSize: '13px',
              }}>
                <div style={{ fontWeight: 600, marginBottom: '0.5rem', color: 'var(--text-primary)' }}>
                  Sort Configuration Used:
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem 1rem', color: 'var(--text-secondary)' }}>
                  <span>
                    <strong>Priority:</strong>{' '}
                    {reorderSortConfig.priority
                      .filter(criterion => reorderSortConfig.enabled[criterion])
                      .map(c => c.charAt(0).toUpperCase() + c.slice(1))
                      .join(' → ') || 'None'}
                  </span>
                  <span>
                    <strong>Deprioritize failed:</strong>{' '}
                    {reorderSortConfig.deprioritize_failed ? 'Yes' : 'No'}
                  </span>
                </div>
              </div>
            )}

            <div className="probe-results-modal-body">
              {reorderData.length === 0 ? (
                <div className="probe-results-empty">
                  No channels were reordered
                </div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                  {reorderData.map((channel) => (
                    <div
                      key={channel.channel_id}
                      style={{
                        padding: '1rem',
                        backgroundColor: 'rgba(52, 152, 219, 0.05)',
                        border: '1px solid rgba(52, 152, 219, 0.2)',
                        borderRadius: '8px',
                      }}
                    >
                      <div style={{
                        fontSize: '14px',
                        fontWeight: 600,
                        marginBottom: '0.75rem',
                        color: 'var(--text-primary)',
                      }}>
                        {channel.channel_name} ({channel.stream_count} streams)
                      </div>

                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
                        {/* Before */}
                        <div>
                          <div style={{
                            fontSize: '12px',
                            fontWeight: 600,
                            marginBottom: '0.5rem',
                            color: 'var(--text-secondary)',
                          }}>
                            Before
                          </div>
                          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
                            {channel.streams_before.map((stream, idx) => (
                              <div
                                key={stream.id}
                                style={{
                                  fontSize: '12px',
                                  padding: '0.4rem',
                                  backgroundColor: stream.status === 'failed' ? 'rgba(231, 76, 60, 0.1)' : 'var(--bg-secondary)',
                                  borderLeft: `3px solid ${stream.status === 'failed' ? '#e74c3c' : stream.status === 'success' ? '#2ecc71' : '#95a5a6'}`,
                                  borderRadius: '3px',
                                  display: 'flex',
                                  justifyContent: 'space-between',
                                  alignItems: 'center',
                                }}
                              >
                                <span>
                                  {idx + 1}. {stream.name}
                                </span>
                                <span style={{
                                  fontSize: '11px',
                                  color: 'var(--text-secondary)',
                                  fontFamily: 'monospace',
                                }}>
                                  {stream.resolution || '—'} {stream.bitrate ? `| ${(stream.bitrate / 1000000).toFixed(1)}Mbps` : ''}
                                </span>
                              </div>
                            ))}
                          </div>
                        </div>

                        {/* After */}
                        <div>
                          <div style={{
                            fontSize: '12px',
                            fontWeight: 600,
                            marginBottom: '0.5rem',
                            color: '#3498db',
                          }}>
                            After (Smart Sorted)
                          </div>
                          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
                            {channel.streams_after.map((stream, idx) => (
                              <div
                                key={stream.id}
                                style={{
                                  fontSize: '12px',
                                  padding: '0.4rem',
                                  backgroundColor: stream.status === 'failed' ? 'rgba(231, 76, 60, 0.1)' : 'var(--bg-secondary)',
                                  borderLeft: `3px solid ${stream.status === 'failed' ? '#e74c3c' : stream.status === 'success' ? '#2ecc71' : '#95a5a6'}`,
                                  borderRadius: '3px',
                                  display: 'flex',
                                  justifyContent: 'space-between',
                                  alignItems: 'center',
                                }}
                              >
                                <span>
                                  {idx + 1}. {stream.name}
                                </span>
                                <span style={{
                                  fontSize: '11px',
                                  color: 'var(--text-secondary)',
                                  fontFamily: 'monospace',
                                }}>
                                  {stream.resolution || '—'} {stream.bitrate ? `| ${(stream.bitrate / 1000000).toFixed(1)}Mbps` : ''}
                                </span>
                              </div>
                            ))}
                          </div>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="probe-results-modal-footer">
              <button
                onClick={() => setShowReorderModal(false)}
                className="probe-results-close-btn"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Channel Group Selection Modal */}
      {showGroupSelectModal && (
        <div className="modal-overlay" onClick={() => setShowGroupSelectModal(false)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()} style={{ maxWidth: '500px' }}>
            <div className="modal-header">
              <h3>Select Channel Groups to Probe</h3>
              <button
                onClick={() => setShowGroupSelectModal(false)}
                className="probe-results-modal-close"
              >
                ×
              </button>
            </div>

            <div className="modal-body" style={{ maxHeight: '400px', overflowY: 'auto' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
                <p className="form-hint" style={{ margin: 0 }}>
                  Select which channel groups to probe. Uncheck groups to exclude them from probing.
                </p>
                {availableChannelGroups.length > 0 && (
                  <div style={{ display: 'flex', gap: '0.5rem' }}>
                    <button
                      type="button"
                      className="btn-secondary"
                      style={{ padding: '0.3rem 0.6rem', fontSize: '0.85rem' }}
                      onClick={() => setTempProbeChannelGroups(availableChannelGroups.map(g => g.name))}
                    >
                      Select All
                    </button>
                    <button
                      type="button"
                      className="btn-secondary"
                      style={{ padding: '0.3rem 0.6rem', fontSize: '0.85rem' }}
                      onClick={() => setTempProbeChannelGroups([])}
                    >
                      Deselect All
                    </button>
                  </div>
                )}
              </div>

              {availableChannelGroups.length === 0 ? (
                <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-secondary)' }}>
                  No groups with streams available
                </div>
              ) : (
                <div className="profile-checkbox-list">
                  {availableChannelGroups.map((group) => (
                    <label key={group.id} className="profile-checkbox">
                      <input
                        type="checkbox"
                        checked={tempProbeChannelGroups.includes(group.name)}
                        onChange={(e) => {
                          if (e.target.checked) {
                            setTempProbeChannelGroups([...tempProbeChannelGroups, group.name]);
                          } else {
                            setTempProbeChannelGroups(tempProbeChannelGroups.filter(name => name !== group.name));
                          }
                        }}
                      />
                      <span className="profile-checkbox-label">{group.name}</span>
                    </label>
                  ))}
                </div>
              )}
            </div>

            <div className="modal-footer">
              <button
                onClick={() => setShowGroupSelectModal(false)}
                className="btn-secondary"
              >
                Cancel
              </button>
              <button
                onClick={async () => {
                  // Update state and save immediately
                  setProbeChannelGroups(tempProbeChannelGroups);
                  setShowGroupSelectModal(false);
                  // Save settings with the new probe channel groups
                  // We need to save directly since setState is async
                  try {
                    await api.saveSettings({
                      url,
                      username,
                      auto_rename_channel_number: autoRenameChannelNumber,
                      include_channel_number_in_name: includeChannelNumberInName,
                      channel_number_separator: channelNumberSeparator,
                      remove_country_prefix: removeCountryPrefix,
                      include_country_in_name: includeCountryInName,
                      country_separator: countrySeparator,
                      timezone_preference: timezonePreference,
                      show_stream_urls: showStreamUrls,
                      hide_auto_sync_groups: hideAutoSyncGroups,
                      hide_ungrouped_streams: hideUngroupedStreams,
                      hide_epg_urls: hideEpgUrls,
                      hide_m3u_urls: hideM3uUrls,
                      gracenote_conflict_mode: gracenoteConflictMode,
                      theme: theme,
                      default_channel_profile_ids: defaultChannelProfileIds,
                      epg_auto_match_threshold: epgAutoMatchThreshold,
                      custom_network_prefixes: customNetworkPrefixes,
                      custom_network_suffixes: customNetworkSuffixes,
                      normalization_settings: normalizationSettings,
                      stats_poll_interval: statsPollInterval,
                      user_timezone: userTimezone,
                      backend_log_level: backendLogLevel,
                      frontend_log_level: frontendLogLevel,
                      vlc_open_behavior: vlcOpenBehavior,
                      linked_m3u_accounts: linkedM3UAccounts,
                      stream_probe_batch_size: streamProbeBatchSize,
                      stream_probe_timeout: streamProbeTimeout,
                      probe_channel_groups: tempProbeChannelGroups,
                      bitrate_sample_duration: bitrateSampleDuration,
                      parallel_probing_enabled: parallelProbingEnabled,
                      max_concurrent_probes: maxConcurrentProbes,
                      skip_recently_probed_hours: skipRecentlyProbedHours,
                      refresh_m3us_before_probe: refreshM3usBeforeProbe,
                      auto_reorder_after_probe: autoReorderAfterProbe,
                      stream_fetch_page_limit: streamFetchPageLimit,
                      stream_sort_priority: streamSortPriority,
                      stream_sort_enabled: streamSortEnabled,
                      deprioritize_failed_streams: deprioritizeFailedStreams,
                    });
                    logger.info('Probe channel groups saved');
                  } catch (err) {
                    logger.error('Failed to save probe channel groups', err);
                  }
                }}
                className="btn-primary"
              >
                OK
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
