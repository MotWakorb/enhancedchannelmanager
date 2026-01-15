import { useState, useEffect } from 'react';
import * as api from '../../services/api';
import { NETWORK_PREFIXES, NETWORK_SUFFIXES } from '../../services/api';
import type { Theme, ProbeHistoryEntry, SortCriterion, SortEnabledMap } from '../../services/api';
import type { ChannelProfile } from '../../types';
import { logger } from '../../utils/logger';
import type { LogLevel as FrontendLogLevel } from '../../utils/logger';
import { DeleteOrphanedGroupsModal } from '../DeleteOrphanedGroupsModal';
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

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : enabled ? 1 : 0.5,
  };

  const config = SORT_CRITERION_CONFIG[id];

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`sort-priority-item ${isDragging ? 'dragging' : ''} ${!enabled ? 'disabled' : ''}`}
      {...attributes}
      {...listeners}
    >
      <input
        type="checkbox"
        checked={enabled}
        onChange={() => onToggleEnabled(id)}
        onClick={(e) => e.stopPropagation()}
        onPointerDown={(e) => e.stopPropagation()}
        className="sort-priority-checkbox"
        title={enabled ? 'Click to disable this sort criterion' : 'Click to enable this sort criterion'}
      />
      <span className={`sort-priority-rank ${!enabled ? 'disabled' : ''}`}>{enabled ? index + 1 : '-'}</span>
      <span className="material-icons sort-priority-icon">{config.icon}</span>
      <div className="sort-priority-content">
        <span className="sort-priority-label">{config.label}</span>
        <span className="sort-priority-description">{config.description}</span>
      </div>
      <span className="material-icons sort-priority-drag">drag_indicator</span>
    </div>
  );
}

interface SettingsTabProps {
  onSaved: () => void;
  onThemeChange?: (theme: Theme) => void;
  channelProfiles?: ChannelProfile[];
}

type SettingsPage = 'general' | 'channel-defaults' | 'appearance' | 'maintenance';

export function SettingsTab({ onSaved, onThemeChange, channelProfiles = [] }: SettingsTabProps) {
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
  const [streamSortPriority, setStreamSortPriority] = useState<SortCriterion[]>(['resolution', 'bitrate', 'framerate']);
  const [streamSortEnabled, setStreamSortEnabled] = useState<SortEnabledMap>({ resolution: true, bitrate: true, framerate: true });

  // Appearance settings
  const [showStreamUrls, setShowStreamUrls] = useState(true);
  const [hideAutoSyncGroups, setHideAutoSyncGroups] = useState(false);
  const [hideUngroupedStreams, setHideUngroupedStreams] = useState(true);
  const [theme, setTheme] = useState<Theme>('dark');
  const [vlcOpenBehavior, setVlcOpenBehavior] = useState('m3u_fallback');

  // Stats settings
  const [statsPollInterval, setStatsPollInterval] = useState(10);
  const [userTimezone, setUserTimezone] = useState('');

  // Log level settings
  const [backendLogLevel, setBackendLogLevel] = useState('INFO');
  const [frontendLogLevel, setFrontendLogLevel] = useState('INFO');

  // Stream probe settings
  const [streamProbeEnabled, setStreamProbeEnabled] = useState(true);
  const [streamProbeIntervalHours, setStreamProbeIntervalHours] = useState(24);
  const [streamProbeBatchSize, setStreamProbeBatchSize] = useState(10);
  const [streamProbeTimeout, setStreamProbeTimeout] = useState(30);
  const [streamProbeScheduleTime, setStreamProbeScheduleTime] = useState('03:00');
  const [bitrateSampleDuration, setBitrateSampleDuration] = useState(10);
  const [parallelProbingEnabled, setParallelProbingEnabled] = useState(true);
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
  } | null>(null);
  const [showProbeResultsModal, setShowProbeResultsModal] = useState(false);
  const [probeResultsType, setProbeResultsType] = useState<'success' | 'failed' | 'skipped'>('success');
  const [probeResults, setProbeResults] = useState<{
    success_streams: Array<{ id: number; name: string; url?: string }>;
    failed_streams: Array<{ id: number; name: string; url?: string }>;
    skipped_streams: Array<{ id: number; name: string; url?: string; reason?: string }>;
    success_count: number;
    failed_count: number;
    skipped_count: number;
  } | null>(null);
  const [probeHistory, setProbeHistory] = useState<ProbeHistoryEntry[]>([]);
  const [copiedUrl, setCopiedUrl] = useState<string | null>(null);
  const [availableChannelGroups, setAvailableChannelGroups] = useState<Array<{ id: number; name: string }>>([]);
  const [probeChannelGroups, setProbeChannelGroups] = useState<string[]>([]);
  const [showGroupSelectModal, setShowGroupSelectModal] = useState(false);
  const [tempProbeChannelGroups, setTempProbeChannelGroups] = useState<string[]>([]);

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

  // Track original poll interval and timezone to detect if restart is needed
  const [originalPollInterval, setOriginalPollInterval] = useState(10);
  const [originalTimezone, setOriginalTimezone] = useState('');
  const [needsRestart, setNeedsRestart] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [restartResult, setRestartResult] = useState<{ success: boolean; message: string } | null>(null);

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
  }, []);

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
  useEffect(() => {
    if (availableChannelGroups.length > 0 && probeChannelGroups.length === 0) {
      // If no groups are selected, default to all groups
      const allGroupNames = availableChannelGroups.map(g => g.name);
      setProbeChannelGroups(allGroupNames);
    }
  }, [availableChannelGroups, probeChannelGroups.length]);

  // Poll for probe all streams progress
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

    // Wait a moment for the background task to start, then poll
    const initialDelay = setTimeout(pollProgress, 300);
    const interval = setInterval(pollProgress, 1000);

    return () => {
      clearTimeout(initialDelay);
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
      setTheme(settings.theme || 'dark');
      setVlcOpenBehavior(settings.vlc_open_behavior || 'm3u_fallback');
      setDefaultChannelProfileIds(settings.default_channel_profile_ids);
      setEpgAutoMatchThreshold(settings.epg_auto_match_threshold ?? 80);
      setCustomNetworkPrefixes(settings.custom_network_prefixes ?? []);
      setCustomNetworkSuffixes(settings.custom_network_suffixes ?? []);
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
      // Stream probe settings
      setStreamProbeEnabled(settings.stream_probe_enabled ?? true);
      setStreamProbeIntervalHours(settings.stream_probe_interval_hours ?? 24);
      setStreamProbeBatchSize(settings.stream_probe_batch_size ?? 10);
      setStreamProbeTimeout(settings.stream_probe_timeout ?? 30);
      setStreamProbeScheduleTime(settings.stream_probe_schedule_time ?? '03:00');
      setProbeChannelGroups(settings.probe_channel_groups ?? []);
      setBitrateSampleDuration(settings.bitrate_sample_duration ?? 10);
      setParallelProbingEnabled(settings.parallel_probing_enabled ?? true);
      setStreamSortPriority(settings.stream_sort_priority ?? ['resolution', 'bitrate', 'framerate']);
      setStreamSortEnabled(settings.stream_sort_enabled ?? { resolution: true, bitrate: true, framerate: true });
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
        theme: theme,
        default_channel_profile_ids: defaultChannelProfileIds,
        epg_auto_match_threshold: epgAutoMatchThreshold,
        custom_network_prefixes: customNetworkPrefixes,
        custom_network_suffixes: customNetworkSuffixes,
        stats_poll_interval: statsPollInterval,
        user_timezone: userTimezone,
        backend_log_level: backendLogLevel,
        frontend_log_level: frontendLogLevel,
        vlc_open_behavior: vlcOpenBehavior,
        linked_m3u_accounts: linkedM3UAccounts,
        // Stream probe settings
        stream_probe_enabled: streamProbeEnabled,
        stream_probe_interval_hours: streamProbeIntervalHours,
        stream_probe_batch_size: streamProbeBatchSize,
        stream_probe_timeout: streamProbeTimeout,
        stream_probe_schedule_time: streamProbeScheduleTime,
        probe_channel_groups: probeChannelGroups,
        bitrate_sample_duration: bitrateSampleDuration,
        parallel_probing_enabled: parallelProbingEnabled,
        stream_sort_priority: streamSortPriority,
        stream_sort_enabled: streamSortEnabled,
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
      // Check if poll interval or timezone changed and needs restart
      if (statsPollInterval !== originalPollInterval || userTimezone !== originalTimezone) {
        setNeedsRestart(true);
        logger.info('Stats polling or timezone changed - backend restart recommended');
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

  const handleRerunFailed = async () => {
    setShowProbeResultsModal(false);
    // TODO: Implement re-run functionality for failed streams
    // For now, just trigger a full probe again
    await handleProbeAllStreams();
  };

  const handleCopyUrl = async (url: string) => {
    try {
      await navigator.clipboard.writeText(url);
      setCopiedUrl(url);
      // Clear the "copied" indicator after 2 seconds
      setTimeout(() => setCopiedUrl(null), 2000);
    } catch (err) {
      logger.error('Failed to copy URL to clipboard', err);
    }
  };

  const handleShowHistoryResults = (historyEntry: ProbeHistoryEntry, type: 'success' | 'failed' | 'skipped') => {
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

        <div className="form-group">
          <label htmlFor="url">Server URL</label>
          <input
            id="url"
            type="text"
            placeholder="http://localhost:9191"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
        </div>

        <div className="form-row">
          <div className="form-group">
            <label htmlFor="username">Username</label>
            <input
              id="username"
              type="text"
              placeholder="admin"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
          </div>

          <div className="form-group">
            <label htmlFor="password">Password</label>
            <input
              id="password"
              type="password"
              placeholder="Enter password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <p className="form-hint">Only required when changing URL or username</p>
          </div>
        </div>

        <div className="test-connection-row">
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

        <div className="form-group">
          <div className="threshold-label-row">
            <label htmlFor="statsPollInterval">Poll interval (seconds)</label>
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
          </div>
          <p className="form-hint">
            How often to poll Dispatcharr for channel statistics and bandwidth tracking.
            Lower values provide more frequent updates but use more resources.
          </p>

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

        <div className="form-group">
          <div className="threshold-label-row">
            <label htmlFor="userTimezone">Timezone for stats</label>
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
          <p className="form-hint">
            Timezone used for daily bandwidth statistics. "Today" will roll over at midnight in your selected timezone.
          </p>
        </div>
      </div>

      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">bug_report</span>
          <h3>Logging</h3>
        </div>

        <div className="form-group">
          <label htmlFor="backendLogLevel">Backend Log Level</label>
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
          <p className="form-hint">
            Controls Python backend logging level. Changes apply immediately.
            Check Docker logs to see backend messages.
          </p>
        </div>

        <div className="form-group">
          <label htmlFor="frontendLogLevel">Frontend Log Level</label>
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
          <p className="form-hint">
            Controls browser console logging level. Changes apply immediately.
            Open browser DevTools (F12) to see frontend messages.
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

        <div className="form-group">
          <label>Country prefix handling</label>
          <div className="radio-group">
            <label className="radio-option">
              <input
                type="radio"
                name="countryPrefix"
                checked={removeCountryPrefix}
                onChange={() => {
                  setRemoveCountryPrefix(true);
                  setIncludeCountryInName(false);
                }}
              />
              <span className="radio-label">Remove</span>
              <span className="radio-description">Strip country codes (US, UK, CA) from names</span>
            </label>
            <label className="radio-option">
              <input
                type="radio"
                name="countryPrefix"
                checked={!removeCountryPrefix && !includeCountryInName}
                onChange={() => {
                  setRemoveCountryPrefix(false);
                  setIncludeCountryInName(false);
                }}
              />
              <span className="radio-label">Keep as-is</span>
              <span className="radio-description">Leave country prefixes unchanged</span>
            </label>
            <label className="radio-option">
              <input
                type="radio"
                name="countryPrefix"
                checked={includeCountryInName}
                onChange={() => {
                  setRemoveCountryPrefix(false);
                  setIncludeCountryInName(true);
                }}
              />
              <span className="radio-label">Normalize</span>
              <span className="radio-description">Keep with consistent separator</span>
            </label>
          </div>
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
          <h3>Stream Sort Priority</h3>
        </div>

        <div className="form-group">
          <p className="form-hint" style={{ marginTop: 0, marginBottom: '0.75rem' }}>
            Configure which criteria are used for stream sorting. Check/uncheck to enable/disable,
            drag to reorder priority. Enabled criteria appear in the sort dropdown and are used by "Smart Sort".
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
      </div>

      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">label</span>
          <h3>Custom Network Prefixes</h3>
        </div>

        <div className="form-group">
          <p className="form-hint" style={{ marginTop: 0, marginBottom: '0.75rem' }}>
            Add custom prefixes to strip during bulk channel creation. These are merged with the built-in
            list (CHAMP, PPV, NFL, NBA, etc.) when "Strip network prefixes" is enabled.
          </p>

          <div className="custom-prefix-input-row">
            <input
              type="text"
              placeholder="Enter prefix (e.g., MARQUEE)"
              value={newPrefixInput}
              onChange={(e) => setNewPrefixInput(e.target.value.toUpperCase())}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && newPrefixInput.trim()) {
                  e.preventDefault();
                  const prefix = newPrefixInput.trim();
                  // Check if prefix already exists in custom list or built-in list
                  if (!customNetworkPrefixes.includes(prefix) && !NETWORK_PREFIXES.includes(prefix)) {
                    setCustomNetworkPrefixes([...customNetworkPrefixes, prefix]);
                  }
                  setNewPrefixInput('');
                }
              }}
              className="custom-prefix-input"
            />
            <button
              type="button"
              className="btn-secondary custom-prefix-add-btn"
              onClick={() => {
                const prefix = newPrefixInput.trim();
                // Check if prefix already exists in custom list or built-in list
                if (prefix && !customNetworkPrefixes.includes(prefix) && !NETWORK_PREFIXES.includes(prefix)) {
                  setCustomNetworkPrefixes([...customNetworkPrefixes, prefix]);
                }
                setNewPrefixInput('');
              }}
              disabled={!newPrefixInput.trim() || NETWORK_PREFIXES.includes(newPrefixInput.trim()) || customNetworkPrefixes.includes(newPrefixInput.trim())}
            >
              <span className="material-icons">add</span>
              Add
            </button>
          </div>

          {newPrefixInput.trim() && NETWORK_PREFIXES.includes(newPrefixInput.trim()) && (
            <p className="custom-prefix-warning">
              "{newPrefixInput.trim()}" is already a built-in prefix
            </p>
          )}

          {newPrefixInput.trim() && !NETWORK_PREFIXES.includes(newPrefixInput.trim()) && customNetworkPrefixes.includes(newPrefixInput.trim()) && (
            <p className="custom-prefix-warning">
              "{newPrefixInput.trim()}" is already in your custom list
            </p>
          )}

          {customNetworkPrefixes.length > 0 && (
            <div className="custom-prefix-list">
              {customNetworkPrefixes.map((prefix) => (
                <div key={prefix} className="custom-prefix-tag">
                  <span>{prefix}</span>
                  <button
                    type="button"
                    className="custom-prefix-remove"
                    onClick={() => setCustomNetworkPrefixes(customNetworkPrefixes.filter(p => p !== prefix))}
                    title="Remove prefix"
                  >
                    <span className="material-icons">close</span>
                  </button>
                </div>
              ))}
            </div>
          )}

          {customNetworkPrefixes.length === 0 && (
            <p className="custom-prefix-empty">No custom prefixes defined. Built-in prefixes will be used.</p>
          )}
        </div>
      </div>

      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">label_off</span>
          <h3>Custom Network Suffixes</h3>
        </div>

        <div className="form-group">
          <p className="form-hint" style={{ marginTop: 0, marginBottom: '0.75rem' }}>
            Add custom suffixes to strip during bulk channel creation. These are merged with the built-in
            list (ENGLISH, LIVE, BACKUP, etc.) when "Strip network suffixes" is enabled.
          </p>

          <div className="custom-prefix-input-row">
            <input
              type="text"
              placeholder="Enter suffix (e.g., SIMULCAST)"
              value={newSuffixInput}
              onChange={(e) => setNewSuffixInput(e.target.value.toUpperCase())}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && newSuffixInput.trim()) {
                  e.preventDefault();
                  const suffix = newSuffixInput.trim();
                  // Check if suffix already exists in custom list or built-in list
                  if (!customNetworkSuffixes.includes(suffix) && !NETWORK_SUFFIXES.includes(suffix)) {
                    setCustomNetworkSuffixes([...customNetworkSuffixes, suffix]);
                  }
                  setNewSuffixInput('');
                }
              }}
              className="custom-prefix-input"
            />
            <button
              type="button"
              className="btn-secondary custom-prefix-add-btn"
              onClick={() => {
                const suffix = newSuffixInput.trim();
                // Check if suffix already exists in custom list or built-in list
                if (suffix && !customNetworkSuffixes.includes(suffix) && !NETWORK_SUFFIXES.includes(suffix)) {
                  setCustomNetworkSuffixes([...customNetworkSuffixes, suffix]);
                }
                setNewSuffixInput('');
              }}
              disabled={!newSuffixInput.trim() || NETWORK_SUFFIXES.includes(newSuffixInput.trim()) || customNetworkSuffixes.includes(newSuffixInput.trim())}
            >
              <span className="material-icons">add</span>
              Add
            </button>
          </div>

          {newSuffixInput.trim() && NETWORK_SUFFIXES.includes(newSuffixInput.trim()) && (
            <p className="custom-prefix-warning">
              "{newSuffixInput.trim()}" is already a built-in suffix
            </p>
          )}

          {newSuffixInput.trim() && !NETWORK_SUFFIXES.includes(newSuffixInput.trim()) && customNetworkSuffixes.includes(newSuffixInput.trim()) && (
            <p className="custom-prefix-warning">
              "{newSuffixInput.trim()}" is already in your custom list
            </p>
          )}

          {customNetworkSuffixes.length > 0 && (
            <div className="custom-prefix-list">
              {customNetworkSuffixes.map((suffix) => (
                <div key={suffix} className="custom-prefix-tag">
                  <span>{suffix}</span>
                  <button
                    type="button"
                    className="custom-prefix-remove"
                    onClick={() => setCustomNetworkSuffixes(customNetworkSuffixes.filter(s => s !== suffix))}
                    title="Remove suffix"
                  >
                    <span className="material-icons">close</span>
                  </button>
                </div>
              ))}
            </div>
          )}

          {customNetworkSuffixes.length === 0 && (
            <p className="custom-prefix-empty">No custom suffixes defined. Built-in suffixes will be used.</p>
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
          Scheduled probing runs automatically in the background.
        </p>

        <div className="checkbox-group">
          <input
            id="streamProbeEnabled"
            type="checkbox"
            checked={streamProbeEnabled}
            onChange={(e) => setStreamProbeEnabled(e.target.checked)}
          />
          <div className="checkbox-content">
            <label htmlFor="streamProbeEnabled">Enable scheduled probing</label>
            <p>
              Automatically probe streams on a schedule to keep metadata up to date.
            </p>
          </div>
        </div>

        {streamProbeEnabled && (
          <div className="settings-group" style={{ marginTop: '1rem' }}>
            <div className="form-group">
              <label htmlFor="probeInterval">Probe interval (hours)</label>
              <input
                id="probeInterval"
                type="number"
                min="1"
                max="168"
                value={streamProbeIntervalHours}
                onChange={(e) => setStreamProbeIntervalHours(Math.max(1, Math.min(168, parseInt(e.target.value) || 24)))}
                style={{ width: '100px' }}
              />
              <span className="form-hint">How often to run scheduled probes (1-168 hours)</span>
            </div>

            <div className="form-group">
              <label htmlFor="probeBatchSize">Batch size</label>
              <input
                id="probeBatchSize"
                type="number"
                min="1"
                max={totalStreamCount}
                value={streamProbeBatchSize}
                onChange={(e) => setStreamProbeBatchSize(Math.max(1, Math.min(totalStreamCount, parseInt(e.target.value) || 10)))}
                style={{ width: '100px' }}
              />
              <span className="form-hint">Streams to probe per scheduled cycle (1-{totalStreamCount})</span>
            </div>

            <div className="form-group">
              <label htmlFor="probeTimeout">Probe timeout (seconds)</label>
              <input
                id="probeTimeout"
                type="number"
                min="5"
                max="120"
                value={streamProbeTimeout}
                onChange={(e) => setStreamProbeTimeout(Math.max(5, Math.min(120, parseInt(e.target.value) || 30)))}
                style={{ width: '100px' }}
              />
              <span className="form-hint">Timeout for each probe attempt (5-120 seconds)</span>
            </div>

            <div className="form-group">
              <label htmlFor="probeScheduleTime">Schedule time (local)</label>
              <input
                id="probeScheduleTime"
                type="time"
                value={streamProbeScheduleTime}
                onChange={(e) => setStreamProbeScheduleTime(e.target.value || '03:00')}
                style={{ width: '120px' }}
              />
              <span className="form-hint">Time of day to start scheduled probes (your local time)</span>
            </div>

            <div className="form-group">
              <label htmlFor="bitrateSampleDuration">Bitrate measurement duration</label>
              <select
                id="bitrateSampleDuration"
                value={bitrateSampleDuration}
                onChange={(e) => setBitrateSampleDuration(Number(e.target.value))}
                style={{ width: '120px' }}
              >
                <option value={10}>10 seconds</option>
                <option value={20}>20 seconds</option>
                <option value={30}>30 seconds</option>
              </select>
              <span className="form-hint">How long to sample streams when measuring bitrate</span>
            </div>

            <div className="form-group">
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={parallelProbingEnabled}
                  onChange={(e) => setParallelProbingEnabled(e.target.checked)}
                />
                Enable parallel probing
              </label>
              <span className="form-hint">
                When enabled, streams from different M3U accounts are probed simultaneously for faster completion.
                Disable for sequential one-at-a-time probing.
              </span>
            </div>

            <div className="form-group">
              <label>Channel groups to probe</label>
              <button
                type="button"
                className="btn-secondary"
                onClick={() => {
                  setTempProbeChannelGroups([...probeChannelGroups]);
                  setShowGroupSelectModal(true);
                }}
                disabled={availableChannelGroups.length === 0}
                style={{ marginTop: '0.5rem' }}
              >
                <span className="material-icons">filter_list</span>
                {probeChannelGroups.length === availableChannelGroups.length
                  ? `All ${availableChannelGroups.length} group${availableChannelGroups.length !== 1 ? 's' : ''}`
                  : `${probeChannelGroups.length} of ${availableChannelGroups.length} group${availableChannelGroups.length !== 1 ? 's' : ''}`}
              </button>
              <span className="form-hint" style={{ display: 'block', marginTop: '0.5rem' }}>
                {availableChannelGroups.length === 0
                  ? 'No groups with streams available.'
                  : 'Select which groups to probe. All groups are selected by default.'}
              </span>
            </div>
          </div>
        )}

        <div className="settings-group" style={{ marginTop: '1rem' }}>
          <button
            className="btn-secondary"
            onClick={handleProbeAllStreams}
            disabled={probingAll}
          >
            <span className={`material-icons ${probingAll ? 'spinning' : ''}`}>
              {probingAll ? 'sync' : 'play_arrow'}
            </span>
            {probingAll ? (probeProgress && probeProgress.status === 'probing' ? 'Probing...' : 'Starting...') : 'Probe All Streams Now'}
          </button>
          <span className="form-hint" style={{ marginLeft: '1rem' }}>
            Start a background probe of all streams immediately
          </span>

          {probeAllResult && (
            <div className={probeAllResult.success ? 'success-message' : 'error-message'} style={{ marginTop: '1rem' }}>
              <span className="material-icons">{probeAllResult.success ? 'check_circle' : 'error'}</span>
              {probeAllResult.message}
            </div>
          )}
        </div>
      </div>

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
          <button
            className="btn-secondary"
            onClick={handleLoadOrphanedGroups}
            disabled={loadingOrphaned || cleaningOrphaned}
          >
            <span className="material-icons">search</span>
            {loadingOrphaned ? 'Scanning...' : 'Scan for Orphaned Groups'}
          </button>

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
            className={`settings-nav-item ${activePage === 'appearance' ? 'active' : ''}`}
            onClick={() => setActivePage('appearance')}
          >
            <span className="material-icons">palette</span>
            Appearance
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
        {/* Global probe progress indicator - shows on all pages when probing */}
        {probingAll && probeProgress && (
          <div className="probe-global-progress" style={{
            marginBottom: '1rem',
            padding: '1rem',
            backgroundColor: 'var(--bg-tertiary)',
            borderRadius: '8px',
            border: '1px solid var(--border-color)'
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <span className="material-icons" style={{ color: '#3498db', animation: 'spin 1s linear infinite' }}>
                  sync
                </span>
                <span style={{ fontWeight: '600' }}>Probing Streams...</span>
              </div>
              <span style={{ fontWeight: '700', color: '#3498db' }}>
                {probeProgress.current} / {probeProgress.total} ({probeProgress.percentage}%)
              </span>
            </div>
            {probeProgress.current_stream && (
              <div style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '0.5rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {probeProgress.current_stream}
              </div>
            )}
            <div style={{
              width: '100%',
              height: '8px',
              backgroundColor: '#34495e',
              borderRadius: '4px',
              overflow: 'hidden'
            }}>
              <div style={{
                width: `${probeProgress.percentage}%`,
                height: '100%',
                background: 'linear-gradient(90deg, #3498db 0%, #2ecc71 100%)',
                transition: 'width 0.3s ease',
              }}></div>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '0.5rem' }}>
              <div style={{ display: 'flex', gap: '1rem', fontSize: '12px' }}>
                <span style={{ color: '#2ecc71' }}>✓ {probeProgress.success_count} success</span>
                <span style={{ color: '#e74c3c' }}>✗ {probeProgress.failed_count} failed</span>
                {probeProgress.skipped_count > 0 && (
                  <span style={{ color: '#f39c12' }}>⊘ {probeProgress.skipped_count} skipped</span>
                )}
              </div>
              <button
                onClick={async () => {
                  try {
                    await api.cancelProbe();
                    logger.info('Probe cancellation requested');
                  } catch (err) {
                    logger.error('Failed to cancel probe', err);
                  }
                }}
                style={{
                  padding: '0.25rem 0.75rem',
                  fontSize: '12px',
                  backgroundColor: 'rgba(231, 76, 60, 0.15)',
                  color: '#e74c3c',
                  border: '1px solid rgba(231, 76, 60, 0.3)',
                  borderRadius: '4px',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.25rem'
                }}
                title="Cancel the current probe operation"
              >
                <span className="material-icons" style={{ fontSize: '14px' }}>cancel</span>
                Cancel
              </button>
            </div>
          </div>
        )}

        {activePage === 'general' && renderGeneralPage()}
        {activePage === 'channel-defaults' && renderChannelDefaultsPage()}
        {activePage === 'appearance' && renderAppearancePage()}
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
                        </div>
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
                              gap: '0.25rem',
                              flexShrink: 0
                            }}
                          >
                            <span className="material-icons" style={{ fontSize: '14px' }}>
                              {copiedUrl === stream.url ? 'check' : 'content_copy'}
                            </span>
                            {copiedUrl === stream.url ? 'Copied' : 'Copy URL'}
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                );
              })()}
            </div>

            <div className="probe-results-modal-footer">
              {probeResultsType === 'failed' && probeResults.failed_count > 0 && (
                <button
                  onClick={handleRerunFailed}
                  className="probe-results-rerun-btn"
                >
                  Re-run All Streams
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

      {/* Channel Group Selection Modal */}
      {showGroupSelectModal && (
        <div className="modal-overlay" onClick={() => setShowGroupSelectModal(false)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()} style={{ maxWidth: '500px' }}>
            <div className="modal-header">
              <h3>Select Channel Groups to Probe</h3>
              <button
                onClick={() => setShowGroupSelectModal(false)}
                className="modal-close"
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
                      theme: theme,
                      default_channel_profile_ids: defaultChannelProfileIds,
                      epg_auto_match_threshold: epgAutoMatchThreshold,
                      custom_network_prefixes: customNetworkPrefixes,
                      custom_network_suffixes: customNetworkSuffixes,
                      stats_poll_interval: statsPollInterval,
                      user_timezone: userTimezone,
                      backend_log_level: backendLogLevel,
                      frontend_log_level: frontendLogLevel,
                      vlc_open_behavior: vlcOpenBehavior,
                      linked_m3u_accounts: linkedM3UAccounts,
                      stream_probe_enabled: streamProbeEnabled,
                      stream_probe_interval_hours: streamProbeIntervalHours,
                      stream_probe_batch_size: streamProbeBatchSize,
                      stream_probe_timeout: streamProbeTimeout,
                      stream_probe_schedule_time: streamProbeScheduleTime,
                      probe_channel_groups: tempProbeChannelGroups,
                      bitrate_sample_duration: bitrateSampleDuration,
                      parallel_probing_enabled: parallelProbingEnabled,
                      stream_sort_priority: streamSortPriority,
                      stream_sort_enabled: streamSortEnabled,
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
