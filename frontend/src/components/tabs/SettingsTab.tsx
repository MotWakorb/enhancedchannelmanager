import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import * as api from '../../services/api';
import { NETWORK_PREFIXES, NETWORK_SUFFIXES } from '../../constants/streamNormalization';
import { useNotifications } from '../../contexts/NotificationContext';
import type { Theme, ProbeHistoryEntry, SortCriterion, SortEnabledMap, GracenoteConflictMode, StreamPreviewMode } from '../../services/api';
import { NormalizationEngineSection } from '../settings/NormalizationEngineSection';
import { TagEngineSection } from '../settings/TagEngineSection';
import { AuthSettingsSection } from '../settings/AuthSettingsSection';
import { UserManagementSection } from '../settings/UserManagementSection';
import { LinkedAccountsSection } from '../settings/LinkedAccountsSection';
import { TLSSettingsSection } from '../settings/TLSSettingsSection';
import { useAuth } from '../../hooks/useAuth';
import type { ChannelProfile, M3UDigestSettings, M3UDigestFrequency } from '../../types';
import { logger } from '../../utils/logger';
import { copyToClipboard } from '../../utils/clipboard';
import type { LogLevel as FrontendLogLevel } from '../../utils/logger';
import { DeleteOrphanedGroupsModal } from '../DeleteOrphanedGroupsModal';
import { ScheduledTasksSection } from '../ScheduledTasksSection';
import { SettingsModal } from '../SettingsModal';
import { CustomSelect } from '../CustomSelect';
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
  m3u_priority: { icon: 'low_priority', label: 'M3U Priority', description: 'Higher priority M3U first' },
  audio_channels: { icon: 'surround_sound', label: 'Audio Channels', description: '5.1 > Stereo > Mono' },
};

// All known sort criteria - used to merge new criteria into saved settings
const ALL_SORT_CRITERIA: SortCriterion[] = ['resolution', 'bitrate', 'framerate', 'm3u_priority', 'audio_channels'];

// Default enabled state for each criterion
const DEFAULT_SORT_ENABLED: SortEnabledMap = {
  resolution: true,
  bitrate: true,
  framerate: true,
  m3u_priority: false,
  audio_channels: false,
};

// Merge saved sort criteria with any new criteria that may have been added
// Preserves saved order and enabled state, appends new criteria at end (disabled)
function mergeSortCriteria(
  savedPriority: SortCriterion[] | undefined,
  savedEnabled: SortEnabledMap | undefined
): { priority: SortCriterion[]; enabled: SortEnabledMap } {
  if (!savedPriority || savedPriority.length === 0) {
    return { priority: ALL_SORT_CRITERIA, enabled: DEFAULT_SORT_ENABLED };
  }

  // Start with saved criteria in their saved order
  const priority = [...savedPriority];
  const enabled = { ...DEFAULT_SORT_ENABLED, ...savedEnabled };

  // Add any new criteria that aren't in the saved list
  for (const criterion of ALL_SORT_CRITERIA) {
    if (!priority.includes(criterion)) {
      priority.push(criterion);
      // New criteria default to disabled
      enabled[criterion] = false;
    }
  }

  return { priority, enabled };
}

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

type SettingsPage = 'general' | 'channel-defaults' | 'normalization' | 'tag-engine' | 'appearance' | 'email' | 'scheduled-tasks' | 'm3u-digest' | 'maintenance' | 'linked-accounts' | 'auth-settings' | 'user-management' | 'tls-settings';

export function SettingsTab({ onSaved, onThemeChange, channelProfiles = [], onProbeComplete }: SettingsTabProps) {
  const [activePage, setActivePage] = useState<SettingsPage>('general');
  const notifications = useNotifications();
  const { user } = useAuth();
  const restartToastIdRef = useRef<string | null>(null);

  // Listen for restart events from NotificationCenter to dismiss the restart toast
  useEffect(() => {
    const handleServicesRestarted = () => {
      if (restartToastIdRef.current) {
        notifications.dismiss(restartToastIdRef.current);
        restartToastIdRef.current = null;
      }
    };
    window.addEventListener('services-restarted', handleServicesRestarted);
    return () => window.removeEventListener('services-restarted', handleServicesRestarted);
  }, [notifications]);

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
  const [normalizeOnChannelCreate, setNormalizeOnChannelCreate] = useState(false);

  const [streamSortPriority, setStreamSortPriority] = useState<SortCriterion[]>(['resolution', 'bitrate', 'framerate', 'm3u_priority', 'audio_channels']);
  const [streamSortEnabled, setStreamSortEnabled] = useState<SortEnabledMap>({ resolution: true, bitrate: true, framerate: true, m3u_priority: false, audio_channels: false });
  const [m3uAccountPriorities, setM3uAccountPriorities] = useState<Record<string, number>>({});
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
  const [streamPreviewMode, setStreamPreviewMode] = useState<StreamPreviewMode>('passthrough');

  // Stats settings
  const [statsPollInterval, setStatsPollInterval] = useState(10);
  const [userTimezone, setUserTimezone] = useState('');

  // Log level settings
  const [backendLogLevel, setBackendLogLevel] = useState('INFO');
  const [frontendLogLevel, setFrontendLogLevel] = useState('INFO');

  // M3U Digest settings
  const [digestSettings, setDigestSettings] = useState<M3UDigestSettings | null>(null);
  const [digestLoading, setDigestLoading] = useState(false);
  const [digestError, setDigestError] = useState<string | null>(null);
  const [digestSaving, setDigestSaving] = useState(false);
  const [digestTestResult, setDigestTestResult] = useState<{ success: boolean; message: string } | null>(null);

  // Shared SMTP (Email) settings
  const [smtpHost, setSmtpHost] = useState('');
  const [smtpPort, setSmtpPort] = useState(587);
  const [smtpUser, setSmtpUser] = useState('');
  const [smtpPassword, setSmtpPassword] = useState('');
  const [smtpFromEmail, setSmtpFromEmail] = useState('');
  const [smtpFromName, setSmtpFromName] = useState('ECM Alerts');
  const [smtpUseTls, setSmtpUseTls] = useState(true);
  const [smtpUseSsl, setSmtpUseSsl] = useState(false);
  const [smtpConfigured, setSmtpConfigured] = useState(false);
  const [smtpTestEmail, setSmtpTestEmail] = useState('');
  const [smtpTesting, setSmtpTesting] = useState(false);
  const [smtpTestResult, setSmtpTestResult] = useState<{ success: boolean; message: string } | null>(null);

  // Shared Discord settings
  const [discordWebhookUrl, setDiscordWebhookUrl] = useState('');
  const [discordConfigured, setDiscordConfigured] = useState(false);
  const [discordTesting, setDiscordTesting] = useState(false);

  // Shared Telegram settings
  const [telegramBotToken, setTelegramBotToken] = useState('');
  const [telegramChatId, setTelegramChatId] = useState('');
  const [telegramConfigured, setTelegramConfigured] = useState(false);
  const [telegramTesting, setTelegramTesting] = useState(false);

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
  // M3U accounts for guidance on max concurrent probes
  const [m3uAccountsMaxStreams, setM3uAccountsMaxStreams] = useState<{ name: string; max_streams: number }[]>([]);

  // Preserve settings not managed by this tab (to avoid overwriting them on save)
  const [linkedM3UAccounts, setLinkedM3UAccounts] = useState<number[][]>([]);

  // UI state
  const [loading, setLoading] = useState(false);
  const [testing, setTesting] = useState(false);

  // Maintenance state
  const [orphanedGroups, setOrphanedGroups] = useState<{ id: number; name: string; reason?: string }[]>([]);
  const [loadingOrphaned, setLoadingOrphaned] = useState(false);
  const [cleaningOrphaned, setCleaningOrphaned] = useState(false);
  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [showConnectionModal, setShowConnectionModal] = useState(false);
  const [resettingStats, setResettingStats] = useState(false);

  // Auto-created channels maintenance state
  const [autoCreatedGroups, setAutoCreatedGroups] = useState<api.AutoCreatedGroup[]>([]);
  const [totalAutoCreatedChannels, setTotalAutoCreatedChannels] = useState(0);
  const [loadingAutoCreated, setLoadingAutoCreated] = useState(false);
  const [selectedAutoCreatedGroups, setSelectedAutoCreatedGroups] = useState<Set<number>>(new Set());
  const [clearingAutoCreated, setClearingAutoCreated] = useState(false);

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
    loadProbeHistory();
    checkForOngoingProbe();
    loadM3UAccountsMaxStreams();
  }, []);

  // Load M3U digest settings when that page is activated
  useEffect(() => {
    if (activePage === 'm3u-digest' && !digestSettings && !digestLoading) {
      loadDigestSettings();
    }
  }, [activePage, digestSettings, digestLoading]);

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
      setStreamPreviewMode(settings.stream_preview_mode || 'passthrough');
      setDefaultChannelProfileIds(settings.default_channel_profile_ids);
      setEpgAutoMatchThreshold(settings.epg_auto_match_threshold ?? 80);
      setCustomNetworkPrefixes(settings.custom_network_prefixes ?? []);
      setCustomNetworkSuffixes(settings.custom_network_suffixes ?? []);
      setNormalizeOnChannelCreate(settings.normalize_on_channel_create ?? false);
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
      setBitrateSampleDuration(settings.bitrate_sample_duration ?? 10);
      setParallelProbingEnabled(settings.parallel_probing_enabled ?? true);
      setMaxConcurrentProbes(settings.max_concurrent_probes ?? 8);
      setSkipRecentlyProbedHours(settings.skip_recently_probed_hours ?? 0);
      setRefreshM3usBeforeProbe(settings.refresh_m3us_before_probe ?? true);
      setOriginalRefreshM3usBeforeProbe(settings.refresh_m3us_before_probe ?? true);
      setAutoReorderAfterProbe(settings.auto_reorder_after_probe ?? false);
      setOriginalAutoReorder(settings.auto_reorder_after_probe ?? false);
      setStreamFetchPageLimit(settings.stream_fetch_page_limit ?? 200);
      // Merge saved criteria with any new criteria that may have been added in updates
      const merged = mergeSortCriteria(settings.stream_sort_priority, settings.stream_sort_enabled);
      setStreamSortPriority(merged.priority);
      setStreamSortEnabled(merged.enabled);
      setM3uAccountPriorities(settings.m3u_account_priorities ?? {});
      setDeprioritizeFailedStreams(settings.deprioritize_failed_streams ?? true);
      // Shared SMTP settings
      setSmtpHost(settings.smtp_host ?? '');
      setSmtpPort(settings.smtp_port ?? 587);
      setSmtpUser(settings.smtp_user ?? '');
      setSmtpPassword(''); // Never load password from server
      setSmtpFromEmail(settings.smtp_from_email ?? '');
      setSmtpFromName(settings.smtp_from_name ?? 'ECM Alerts');
      setSmtpUseTls(settings.smtp_use_tls ?? true);
      setSmtpUseSsl(settings.smtp_use_ssl ?? false);
      setSmtpConfigured(settings.smtp_configured ?? false);
      // Shared Discord settings
      setDiscordWebhookUrl(settings.discord_webhook_url ?? '');
      setDiscordConfigured(settings.discord_configured ?? false);
      // Shared Telegram settings
      setTelegramBotToken(settings.telegram_bot_token ?? '');
      setTelegramChatId(settings.telegram_chat_id ?? '');
      setTelegramConfigured(settings.telegram_configured ?? false);
      setNeedsRestart(false);
      setRestartResult(null);
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
      notifications.error('URL, username, and password are required to test connection');
      return;
    }

    setTesting(true);

    try {
      const result = await api.testConnection({ url, username, password });
      if (result.success) {
        notifications.success(result.message, 'Connection Test');
      } else {
        notifications.error(result.message, 'Connection Test');
      }
    } catch (err) {
      notifications.error('Failed to test connection', 'Connection Test');
    } finally {
      setTesting(false);
    }
  };

  const handleResetStats = async () => {
    if (!confirm('This will clear all channel/stream statistics, watch history, and hidden groups. Use this when switching Dispatcharr servers. Continue?')) {
      return;
    }

    setResettingStats(true);
    try {
      const result = await api.resetStats();
      if (result.success) {
        notifications.success(result.message, 'Reset Statistics');
      } else {
        notifications.error('Failed to reset statistics', 'Reset Statistics');
      }
    } catch (err) {
      notifications.error('Failed to reset statistics', 'Reset Statistics');
    } finally {
      setResettingStats(false);
    }
  };

  const handleSave = async () => {
    // Check if auth settings (URL or username) have changed
    const authChanged = url !== originalUrl || username !== originalUsername;

    // Validate required fields
    if (!url || !username) {
      notifications.error('URL and username are required');
      return;
    }

    // Password is only required if auth settings changed
    if (authChanged && !password) {
      notifications.error('Password is required when changing URL or username');
      return;
    }

    setLoading(true);

    try {
      const result = await api.saveSettings({
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
        normalize_on_channel_create: normalizeOnChannelCreate,
        stats_poll_interval: statsPollInterval,
        user_timezone: userTimezone,
        backend_log_level: backendLogLevel,
        frontend_log_level: frontendLogLevel,
        vlc_open_behavior: vlcOpenBehavior,
        stream_preview_mode: streamPreviewMode,
        linked_m3u_accounts: linkedM3UAccounts,
        // Stream probe settings (scheduled probing is controlled by Task Engine)
        stream_probe_batch_size: streamProbeBatchSize,
        stream_probe_timeout: streamProbeTimeout,
        bitrate_sample_duration: bitrateSampleDuration,
        parallel_probing_enabled: parallelProbingEnabled,
        max_concurrent_probes: maxConcurrentProbes,
        skip_recently_probed_hours: skipRecentlyProbedHours,
        refresh_m3us_before_probe: refreshM3usBeforeProbe,
        auto_reorder_after_probe: autoReorderAfterProbe,
        stream_fetch_page_limit: streamFetchPageLimit,
        stream_sort_priority: streamSortPriority,
        stream_sort_enabled: streamSortEnabled,
        m3u_account_priorities: m3uAccountPriorities,
        deprioritize_failed_streams: deprioritizeFailedStreams,
        // Shared SMTP settings
        smtp_host: smtpHost,
        smtp_port: smtpPort,
        smtp_user: smtpUser,
        // Only send SMTP password if it was entered
        ...(smtpPassword ? { smtp_password: smtpPassword } : {}),
        smtp_from_email: smtpFromEmail,
        smtp_from_name: smtpFromName,
        smtp_use_tls: smtpUseTls,
        smtp_use_ssl: smtpUseSsl,
        // Shared Discord settings
        discord_webhook_url: discordWebhookUrl,
        // Shared Telegram settings
        telegram_bot_token: telegramBotToken,
        telegram_chat_id: telegramChatId,
      });
      // If server URL changed, all data was invalidated - reload the page
      if (result.server_changed) {
        logger.info('Dispatcharr server URL changed - reloading page to refresh all data');
        window.location.reload();
        return;
      }
      // Clear SMTP password field after save
      setSmtpPassword('');
      // Update SMTP configured status
      setSmtpConfigured(!!(smtpHost && smtpFromEmail));
      // Update Discord configured status
      setDiscordConfigured(!!discordWebhookUrl);
      // Update Telegram configured status
      setTelegramConfigured(!!(telegramBotToken && telegramChatId));
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

        // Show toast notification with restart action
        const message = pollOrTimezoneChanged
          ? 'Stats or timezone settings changed. Restart services to apply.'
          : 'Probe settings changed. Restart services to apply.';

        // Dismiss any previous restart toast before showing new one
        if (restartToastIdRef.current) {
          notifications.dismiss(restartToastIdRef.current);
        }

        restartToastIdRef.current = notifications.notify({
          type: 'warning',
          title: 'Restart Required',
          message,
          duration: 0, // Don't auto-dismiss
          action: {
            label: 'Restart Now',
            onClick: handleRestart,
          },
        });

        if (pollOrTimezoneChanged) {
          logger.info('Stats polling or timezone changed - backend restart recommended');
        }
        if (probeSettingsChanged) {
          logger.info('Probe settings changed - backend restart required for schedule changes to take effect');
        }
      } else {
        logger.info('[RESTART-CHECK] No restart-requiring changes detected');
        // Show success toast only if no restart is required (restart toast handles its own messaging)
        notifications.success('Settings saved successfully');
      }
      onSaved();
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to save settings';
      logger.error('Failed to save settings', err);
      notifications.error(errorMessage, 'Save Failed');
    } finally {
      setLoading(false);
    }
  };

  // M3U Digest Settings Management
  const loadDigestSettings = async () => {
    setDigestLoading(true);
    setDigestError(null);
    try {
      const settings = await api.getM3UDigestSettings();
      setDigestSettings(settings);
    } catch (err) {
      setDigestError(err instanceof Error ? err.message : 'Failed to load digest settings');
    } finally {
      setDigestLoading(false);
    }
  };

  const handleDigestSettingChange = <K extends keyof M3UDigestSettings>(
    key: K,
    value: M3UDigestSettings[K]
  ) => {
    if (!digestSettings) return;
    setDigestSettings({ ...digestSettings, [key]: value });
  };

  const handleSaveDigestSettings = async () => {
    if (!digestSettings) return;
    setDigestSaving(true);
    setDigestError(null);
    try {
      const updated = await api.updateM3UDigestSettings({
        enabled: digestSettings.enabled,
        frequency: digestSettings.frequency,
        email_recipients: digestSettings.email_recipients,
        include_group_changes: digestSettings.include_group_changes,
        include_stream_changes: digestSettings.include_stream_changes,
        show_detailed_list: digestSettings.show_detailed_list,
        min_changes_threshold: digestSettings.min_changes_threshold,
      });
      setDigestSettings(updated);
      notifications.success('Settings saved successfully');
    } catch (err) {
      setDigestError(err instanceof Error ? err.message : 'Failed to save digest settings');
      notifications.error('Failed to save digest settings', 'Save Failed');
    } finally {
      setDigestSaving(false);
    }
  };

  const handleSendTestDigest = async () => {
    if (!digestSettings) return;
    setDigestTestResult(null);
    setDigestSaving(true);
    try {
      // Save settings first to ensure recipients are in the database
      await api.updateM3UDigestSettings({
        enabled: digestSettings.enabled,
        frequency: digestSettings.frequency,
        email_recipients: digestSettings.email_recipients,
        include_group_changes: digestSettings.include_group_changes,
        include_stream_changes: digestSettings.include_stream_changes,
        show_detailed_list: digestSettings.show_detailed_list,
        min_changes_threshold: digestSettings.min_changes_threshold,
      });
      // Now send the test
      const result = await api.sendTestM3UDigest();
      setDigestTestResult(result);
      setTimeout(() => setDigestTestResult(null), 5000);
    } catch (err) {
      setDigestTestResult({
        success: false,
        message: err instanceof Error ? err.message : 'Failed to send test digest',
      });
    } finally {
      setDigestSaving(false);
    }
  };

  const handleAddDigestRecipient = (email: string) => {
    if (!digestSettings || !email.trim()) return;
    const trimmed = email.trim().toLowerCase();
    if (digestSettings.email_recipients.includes(trimmed)) return;
    setDigestSettings({
      ...digestSettings,
      email_recipients: [...digestSettings.email_recipients, trimmed],
    });
  };

  const handleRemoveDigestRecipient = (email: string) => {
    if (!digestSettings) return;
    setDigestSettings({
      ...digestSettings,
      email_recipients: digestSettings.email_recipients.filter(e => e !== email),
    });
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

        // Dismiss the restart toast
        if (restartToastIdRef.current) {
          notifications.dismiss(restartToastIdRef.current);
          restartToastIdRef.current = null;
        }

        // Show success notification
        notifications.success('Services restarted successfully with new settings.', 'Restart Complete');

        // Clear result after 3 seconds
        setTimeout(() => setRestartResult(null), 3000);
      } else {
        notifications.error(result.message || 'Failed to restart services', 'Restart Failed');
      }
    } catch (err) {
      setRestartResult({ success: false, message: 'Failed to restart services' });
      notifications.error('Failed to restart services', 'Restart Failed');
    } finally {
      setRestarting(false);
    }
  };

  const handleProbeAllStreams = async () => {
    setProbingAll(true);
    setProbeAllResult(null);
    setProbeProgress(null);
    try {
      // Probe all streams (empty array = all groups)
      const result = await api.probeAllStreams([]);
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

  const handleClearAllProbeStats = async () => {
    try {
      const result = await api.clearAllStreamStats();
      setProbeAllResult({ success: true, message: `Cleared ${result.cleared} probe stats` });
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to clear probe stats';
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
    try {
      const result = await api.getOrphanedChannelGroups();
      setOrphanedGroups(result.orphaned_groups);
      if (result.orphaned_groups.length === 0) {
        notifications.success('No orphaned groups found. Your database is clean!');
      }
    } catch (err) {
      notifications.error(`Failed to load orphaned groups: ${err}`);
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
    try {
      const result = await api.deleteOrphanedChannelGroups(selectedGroupIds);

      if (result.failed_groups.length > 0) {
        const failedNames = result.failed_groups.map(g => g.name).join(', ');
        notifications.warning(`${result.message}. Failed to delete: ${failedNames}`);
      } else {
        notifications.success(result.message);
      }

      if (result.deleted_groups.length > 0) {
        // Reload to refresh the list
        await handleLoadOrphanedGroups();
        // Notify parent to refresh data
        onSaved();
      }
    } catch (err) {
      notifications.error(`Failed to cleanup orphaned groups: ${err}`);
    } finally {
      setCleaningOrphaned(false);
    }
  };

  // Auto-created channels handlers
  const handleLoadAutoCreatedGroups = async () => {
    setLoadingAutoCreated(true);
    try {
      const result = await api.getGroupsWithAutoCreatedChannels();
      setAutoCreatedGroups(result.groups);
      setTotalAutoCreatedChannels(result.total_auto_created_channels);
      setSelectedAutoCreatedGroups(new Set()); // Clear selection
      if (result.groups.length === 0) {
        notifications.success('No groups with auto_created channels found.');
      }
    } catch (err) {
      notifications.error(`Failed to load groups: ${err}`);
    } finally {
      setLoadingAutoCreated(false);
    }
  };

  const handleToggleAutoCreatedGroup = (groupId: number) => {
    setSelectedAutoCreatedGroups(prev => {
      const newSet = new Set(prev);
      if (newSet.has(groupId)) {
        newSet.delete(groupId);
      } else {
        newSet.add(groupId);
      }
      return newSet;
    });
  };

  const handleSelectAllAutoCreatedGroups = () => {
    if (selectedAutoCreatedGroups.size === autoCreatedGroups.length) {
      setSelectedAutoCreatedGroups(new Set());
    } else {
      setSelectedAutoCreatedGroups(new Set(autoCreatedGroups.map(g => g.id)));
    }
  };

  const handleClearAutoCreatedFlag = async () => {
    if (selectedAutoCreatedGroups.size === 0) return;

    setClearingAutoCreated(true);
    try {
      const result = await api.clearAutoCreatedFlag(Array.from(selectedAutoCreatedGroups));

      if (result.failed_channels.length > 0) {
        notifications.warning(`${result.message}. ${result.failed_channels.length} channel(s) failed to update.`);
      } else {
        notifications.success(result.message);
      }

      if (result.updated_count > 0) {
        // Reload to refresh the list
        await handleLoadAutoCreatedGroups();
        // Notify parent to refresh data (channels may have changed)
        onSaved();
      }
    } catch (err) {
      notifications.error(`Failed to clear auto_created flag: ${err}`);
    } finally {
      setClearingAutoCreated(false);
    }
  };

  const renderGeneralPage = () => (
    <div className="settings-page">
      <div className="settings-page-header">
        <h2>General Settings</h2>
        <p>Configure your Dispatcharr connection.</p>
      </div>

      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">link</span>
          <h3>Dispatcharr Connection</h3>
          <button
            className="btn-edit-connection"
            onClick={() => setShowConnectionModal(true)}
            title="Edit connection settings"
          >
            <span className="material-icons">edit</span>
            Edit
          </button>
        </div>

        <div className="connection-info-display">
          <div className="connection-info-row">
            <span className="connection-label">Server URL:</span>
            <span className="connection-value">{url || 'Not configured'}</span>
          </div>
          <div className="connection-info-row">
            <span className="connection-label">Username:</span>
            <span className="connection-value">{username || 'Not configured'}</span>
          </div>
          <div className="connection-info-row">
            <span className="connection-label">Password:</span>
            <span className="connection-value">••••••••</span>
          </div>
        </div>
        <div className="connection-actions">
          <button
            className="btn-reset-stats"
            onClick={handleResetStats}
            disabled={resettingStats}
            title="Clear all statistics when switching Dispatcharr servers"
          >
            <span className="material-icons">{resettingStats ? 'sync' : 'refresh'}</span>
            {resettingStats ? 'Resetting...' : 'Reset Statistics'}
          </button>
          <span className="form-description">
            Clear all channel/stream statistics when switching servers.
          </span>
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

        </div>

        <div className="form-group-vertical">
          <label htmlFor="userTimezone">Timezone</label>
          <span className="form-description">
            Timezone used for daily bandwidth statistics and scheduled probe times. "Today" will roll over at midnight in your selected timezone, and scheduled probes will run at the configured time in this timezone.
          </span>
          <CustomSelect
            value={userTimezone}
            onChange={(val) => setUserTimezone(val)}
            className="timezone-select"
            searchable
            searchPlaceholder="Search timezones..."
            options={[
              { value: '', label: 'UTC (Default)' },
              // US & Canada
              { value: 'America/New_York', label: 'US: Eastern Time (ET)' },
              { value: 'America/Chicago', label: 'US: Central Time (CT)' },
              { value: 'America/Denver', label: 'US: Mountain Time (MT)' },
              { value: 'America/Los_Angeles', label: 'US: Pacific Time (PT)' },
              { value: 'America/Anchorage', label: 'US: Alaska Time (AKT)' },
              { value: 'Pacific/Honolulu', label: 'US: Hawaii Time (HT)' },
              // Europe
              { value: 'Europe/London', label: 'EU: London (GMT/BST)' },
              { value: 'Europe/Paris', label: 'EU: Paris (CET/CEST)' },
              { value: 'Europe/Berlin', label: 'EU: Berlin (CET/CEST)' },
              { value: 'Europe/Amsterdam', label: 'EU: Amsterdam (CET/CEST)' },
              { value: 'Europe/Rome', label: 'EU: Rome (CET/CEST)' },
              { value: 'Europe/Madrid', label: 'EU: Madrid (CET/CEST)' },
              // Asia & Pacific
              { value: 'Asia/Tokyo', label: 'Asia: Tokyo (JST)' },
              { value: 'Asia/Shanghai', label: 'Asia: Shanghai (CST)' },
              { value: 'Asia/Hong_Kong', label: 'Asia: Hong Kong (HKT)' },
              { value: 'Asia/Singapore', label: 'Asia: Singapore (SGT)' },
              { value: 'Asia/Dubai', label: 'Asia: Dubai (GST)' },
              { value: 'Australia/Sydney', label: 'AU: Sydney (AEST/AEDT)' },
              { value: 'Australia/Melbourne', label: 'AU: Melbourne (AEST/AEDT)' },
              { value: 'Pacific/Auckland', label: 'NZ: Auckland (NZST/NZDT)' },
            ]}
          />
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
          <CustomSelect
            value={backendLogLevel}
            onChange={(val) => setBackendLogLevel(val)}
            options={[
              { value: 'DEBUG', label: 'DEBUG - Show all messages including debug info' },
              { value: 'INFO', label: 'INFO - Show informational messages and above' },
              { value: 'WARNING', label: 'WARNING - Show warnings and errors only' },
              { value: 'ERROR', label: 'ERROR - Show errors only' },
              { value: 'CRITICAL', label: 'CRITICAL - Show only critical errors' },
            ]}
          />
        </div>

        <div className="form-group-vertical">
          <label htmlFor="frontendLogLevel">Frontend Log Level</label>
          <span className="form-description">
            Controls browser console logging level. Changes apply immediately.
            Open browser DevTools (F12) to see frontend messages.
          </span>
          <CustomSelect
            value={frontendLogLevel}
            onChange={(val) => setFrontendLogLevel(val)}
            options={[
              { value: 'DEBUG', label: 'DEBUG - Show all messages including debug info' },
              { value: 'INFO', label: 'INFO - Show informational messages and above' },
              { value: 'WARN', label: 'WARN - Show warnings and errors only' },
              { value: 'ERROR', label: 'ERROR - Show errors only' },
            ]}
          />
        </div>
      </div>

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
          <CustomSelect
            value={gracenoteConflictMode}
            onChange={(val) => setGracenoteConflictMode(val as GracenoteConflictMode)}
            options={[
              { value: 'ask', label: 'Ask me what to do (show conflict dialog)' },
              { value: 'skip', label: 'Skip channels with existing IDs' },
              { value: 'overwrite', label: 'Automatically overwrite existing IDs' },
            ]}
          />
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
          <CustomSelect
            value={vlcOpenBehavior}
            onChange={(val) => setVlcOpenBehavior(val)}
            options={[
              { value: 'protocol_only', label: 'Try VLC Protocol (show helper if it fails)' },
              { value: 'm3u_fallback', label: 'Try VLC Protocol, then fallback to M3U download' },
              { value: 'm3u_only', label: 'Always download M3U file' },
            ]}
          />
          <p className="form-hint">
            Controls what happens when you click "Open in VLC". The vlc:// protocol requires
            browser extensions on some platforms. If "protocol_only" fails, a helper modal
            will guide you to install the necessary extension.
          </p>
        </div>
      </div>

      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">play_circle</span>
          <h3>Stream Preview</h3>
        </div>

        <div className="form-group">
          <label htmlFor="streamPreviewMode">Browser Playback Mode</label>
          <CustomSelect
            value={streamPreviewMode}
            onChange={(value) => setStreamPreviewMode(value as StreamPreviewMode)}
            options={[
              { value: 'passthrough', label: 'Direct Playback (may fail on AC-3/E-AC-3 audio)' },
              { value: 'transcode', label: 'Transcode Audio to AAC (CPU intensive, best compatibility)' },
              { value: 'video_only', label: 'Video Only - No Audio (fast preview)' },
            ]}
          />
          <p className="form-hint">
            Controls how streams are played in the browser preview. Many IPTV streams use
            AC-3 or E-AC-3 audio codecs which aren't supported by Chrome. Use "Transcode"
            for best compatibility, or "Video Only" for quick visual previews without audio.
            Transcoding requires FFmpeg on the backend and uses more CPU.
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
          <CustomSelect
            value={timezonePreference}
            onChange={(val) => setTimezonePreference(val)}
            options={[
              { value: 'east', label: 'East Coast (prefer East feeds)' },
              { value: 'west', label: 'West Coast (prefer West feeds)' },
              { value: 'both', label: 'Keep Both (create separate channels)' },
            ]}
          />
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
          <span className="material-icons">auto_fix_high</span>
          <h3>Default Behavior</h3>
        </div>
        <p className="form-hint" style={{ marginBottom: '1rem' }}>
          When this is enabled, the "Apply normalization rules" checkbox will be checked by default
          when creating channels from streams. You can still toggle it per-operation.
        </p>

        <div className="form-group">
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={normalizeOnChannelCreate}
              onChange={(e) => setNormalizeOnChannelCreate(e.target.checked)}
            />
            <span>Apply normalization by default when creating channels</span>
          </label>
        </div>
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

      {/* Advanced Normalization Rules Engine */}
      <NormalizationEngineSection />

      <div className="settings-actions">
        <div className="settings-actions-left" />
        <button className="btn-primary" onClick={handleSave} disabled={loading}>
          <span className="material-icons">save</span>
          {loading ? 'Saving...' : 'Save Settings'}
        </button>
      </div>
    </div>
  );

  // State for new email input in digest settings
  const [newDigestEmail, setNewDigestEmail] = useState('');

  // Handle SMTP test
  const handleTestSmtp = async () => {
    if (!smtpHost || !smtpFromEmail || !smtpTestEmail) {
      setSmtpTestResult({ success: false, message: 'SMTP host, from email, and test recipient are required' });
      return;
    }

    setSmtpTesting(true);
    setSmtpTestResult(null);

    try {
      const result = await api.testSmtpConnection({
        smtp_host: smtpHost,
        smtp_port: smtpPort,
        smtp_user: smtpUser,
        smtp_password: smtpPassword,
        smtp_from_email: smtpFromEmail,
        smtp_from_name: smtpFromName,
        smtp_use_tls: smtpUseTls,
        smtp_use_ssl: smtpUseSsl,
        to_email: smtpTestEmail,
      });
      setSmtpTestResult(result);
    } catch (err) {
      setSmtpTestResult({ success: false, message: 'Failed to test SMTP connection' });
    } finally {
      setSmtpTesting(false);
    }
  };

  // Handle Discord webhook test
  const handleTestDiscord = async () => {
    if (!discordWebhookUrl) {
      notifications.error('Discord webhook URL is required', 'Discord Test');
      return;
    }

    // Basic validation - accept discord.com, discordapp.com, and variants (canary, ptb)
    const discordPattern = /^https:\/\/(discord\.com|discordapp\.com|canary\.discord\.com|ptb\.discord\.com)\/api\/webhooks\//;
    if (!discordPattern.test(discordWebhookUrl)) {
      notifications.error('Invalid Discord webhook URL format', 'Discord Test');
      return;
    }

    setDiscordTesting(true);

    try {
      const result = await api.testDiscordWebhook(discordWebhookUrl);
      if (result.success) {
        notifications.success(result.message, 'Discord Test');
      } else {
        notifications.error(result.message, 'Discord Test');
      }
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to test Discord webhook';
      console.error('Discord test error:', err);
      notifications.error(errorMessage, 'Discord Test');
    } finally {
      setDiscordTesting(false);
    }
  };

  const handleTestTelegram = async () => {
    if (!telegramBotToken) {
      notifications.error('Telegram bot token is required', 'Telegram Test');
      return;
    }
    if (!telegramChatId) {
      notifications.error('Telegram chat ID is required', 'Telegram Test');
      return;
    }

    setTelegramTesting(true);

    try {
      const result = await api.testTelegramBot(telegramBotToken, telegramChatId);
      if (result.success) {
        notifications.success(result.message, 'Telegram Test');
      } else {
        notifications.error(result.message, 'Telegram Test');
      }
    } catch (err) {
      notifications.error('Failed to test Telegram bot', 'Telegram Test');
    } finally {
      setTelegramTesting(false);
    }
  };

  const renderEmailSettingsPage = () => (
    <div className="settings-page">
      <div className="settings-page-header">
        <h2>Notification Settings</h2>
        <p>Configure notification channels (Email, Discord, Telegram) for alerts and reports.</p>
      </div>

      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">mail_outline</span>
          <h3>SMTP Configuration</h3>
          <span className={`config-badge ${smtpConfigured ? 'configured' : 'unconfigured'}`}>
            {smtpConfigured ? 'Configured' : 'Unconfigured'}
          </span>
        </div>
        <p className="section-description">
          Configure your SMTP server to enable email features. These settings are used by M3U Digest
          reports and Email alert methods.
        </p>

        <div className="form-group-vertical">
          <label htmlFor="smtpHost">SMTP Host</label>
          <input
            type="text"
            id="smtpHost"
            value={smtpHost}
            onChange={(e) => setSmtpHost(e.target.value)}
            placeholder="smtp.example.com"
          />
          <p className="field-hint">Your email provider's SMTP server address (e.g., smtp.gmail.com)</p>
        </div>

        <div className="form-row">
          <div className="form-group-vertical">
            <label htmlFor="smtpPort">SMTP Port</label>
            <input
              type="number"
              id="smtpPort"
              value={smtpPort}
              onChange={(e) => setSmtpPort(parseInt(e.target.value, 10) || 587)}
              min={1}
              max={65535}
            />
            <p className="field-hint">Usually 587 (TLS), 465 (SSL), or 25 (unencrypted)</p>
          </div>

          <div className="form-group-vertical">
            <label htmlFor="smtpSecurity">Security</label>
            <select
              id="smtpSecurity"
              value={smtpUseSsl ? 'ssl' : smtpUseTls ? 'tls' : 'none'}
              onChange={(e) => {
                const val = e.target.value;
                setSmtpUseTls(val === 'tls');
                setSmtpUseSsl(val === 'ssl');
              }}
            >
              <option value="tls">TLS (STARTTLS)</option>
              <option value="ssl">SSL</option>
              <option value="none">None</option>
            </select>
            <p className="field-hint">TLS is recommended for most providers</p>
          </div>
        </div>

        <div className="form-row">
          <div className="form-group-vertical">
            <label htmlFor="smtpUser">Username (optional)</label>
            <input
              type="text"
              id="smtpUser"
              value={smtpUser}
              onChange={(e) => setSmtpUser(e.target.value)}
              placeholder="user@example.com"
            />
            <p className="field-hint">Usually your email address</p>
          </div>

          <div className="form-group-vertical">
            <label htmlFor="smtpPassword">Password (optional)</label>
            <input
              type="password"
              id="smtpPassword"
              value={smtpPassword}
              onChange={(e) => setSmtpPassword(e.target.value)}
              placeholder="••••••••"
            />
            <p className="field-hint">App password recommended for Gmail/OAuth providers</p>
          </div>
        </div>

        <div className="form-row">
          <div className="form-group-vertical">
            <label htmlFor="smtpFromEmail">From Email</label>
            <input
              type="email"
              id="smtpFromEmail"
              value={smtpFromEmail}
              onChange={(e) => setSmtpFromEmail(e.target.value)}
              placeholder="noreply@example.com"
            />
            <p className="field-hint">The sender email address</p>
          </div>

          <div className="form-group-vertical">
            <label htmlFor="smtpFromName">From Name</label>
            <input
              type="text"
              id="smtpFromName"
              value={smtpFromName}
              onChange={(e) => setSmtpFromName(e.target.value)}
              placeholder="ECM Alerts"
            />
            <p className="field-hint">Display name for the sender</p>
          </div>
        </div>
      </div>

      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">send</span>
          <h3>Test Connection</h3>
        </div>
        <p className="section-description">
          Send a test email to verify your SMTP settings are configured correctly.
        </p>

        <div className="form-group-vertical">
          <label htmlFor="smtpTestEmail">Test Recipient Email</label>
          <div className="input-with-button">
            <input
              type="email"
              id="smtpTestEmail"
              value={smtpTestEmail}
              onChange={(e) => setSmtpTestEmail(e.target.value)}
              placeholder="your@email.com"
            />
            <button
              className="btn-test"
              onClick={handleTestSmtp}
              disabled={smtpTesting || !smtpHost || !smtpFromEmail || !smtpTestEmail}
            >
              {smtpTesting ? (
                <>
                  <span className="material-icons spinning">sync</span>
                  Testing...
                </>
              ) : (
                <>
                  <span className="material-icons">send</span>
                  Send Test Email
                </>
              )}
            </button>
          </div>
        </div>

        {smtpTestResult && (
          <div className={`test-result ${smtpTestResult.success ? 'success' : 'error'}`}>
            <span className="material-icons">
              {smtpTestResult.success ? 'check_circle' : 'error'}
            </span>
            {smtpTestResult.message}
          </div>
        )}
      </div>

      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">discord</span>
          <h3>Discord Webhook</h3>
          <span className={`config-badge ${discordConfigured ? 'configured' : 'unconfigured'}`}>
            {discordConfigured ? 'Configured' : 'Unconfigured'}
          </span>
        </div>
        <p className="section-description">
          Configure a Discord webhook to receive notifications. Used by M3U Digest and other features
          that support Discord notifications.
        </p>

        <div className="form-group-vertical">
          <label htmlFor="discordWebhookUrl">Webhook URL</label>
          <input
            type="text"
            id="discordWebhookUrl"
            value={discordWebhookUrl}
            onChange={(e) => setDiscordWebhookUrl(e.target.value)}
            placeholder="https://discord.com/api/webhooks/..."
          />
          <p className="field-hint">
            Create a webhook in your Discord server: Server Settings → Integrations → Webhooks → New Webhook
          </p>
        </div>

        <div className="form-group-vertical">
          <button
            className="btn-test"
            onClick={handleTestDiscord}
            disabled={discordTesting || !discordWebhookUrl}
          >
            {discordTesting ? (
              <>
                <span className="material-icons spinning">sync</span>
                Testing...
              </>
            ) : (
              <>
                <span className="material-icons">send</span>
                Send Test Message
              </>
            )}
          </button>
        </div>

      </div>

      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">telegram</span>
          <h3>Telegram Bot</h3>
          <span className={`config-badge ${telegramConfigured ? 'configured' : 'unconfigured'}`}>
            {telegramConfigured ? 'Configured' : 'Unconfigured'}
          </span>
        </div>
        <p className="section-description">
          Configure a Telegram bot to receive notifications. Used by M3U Digest and other features
          that support Telegram notifications.
        </p>

        <div className="form-group-vertical">
          <label htmlFor="telegramBotToken">Bot Token</label>
          <input
            type="password"
            id="telegramBotToken"
            value={telegramBotToken}
            onChange={(e) => setTelegramBotToken(e.target.value)}
            placeholder="123456789:ABCdefGHIjklMNOpqrsTUVwxyz..."
          />
          <p className="field-hint">
            Create a bot via @BotFather on Telegram and copy the token
          </p>
        </div>

        <div className="form-group-vertical">
          <label htmlFor="telegramChatId">Chat ID</label>
          <input
            type="text"
            id="telegramChatId"
            value={telegramChatId}
            onChange={(e) => setTelegramChatId(e.target.value)}
            placeholder="-1001234567890"
          />
          <p className="field-hint">
            Use @userinfobot or @RawDataBot to get your chat ID. For groups, use a negative number.
          </p>
        </div>

        <div className="form-group-vertical">
          <button
            className="btn-test"
            onClick={handleTestTelegram}
            disabled={telegramTesting || !telegramBotToken || !telegramChatId}
          >
            {telegramTesting ? (
              <>
                <span className="material-icons spinning">sync</span>
                Testing...
              </>
            ) : (
              <>
                <span className="material-icons">send</span>
                Send Test Message
              </>
            )}
          </button>
        </div>

      </div>

      <div className="settings-actions">
        <button
          className="btn-primary"
          onClick={handleSave}
          disabled={loading}
        >
          {loading ? 'Saving...' : 'Save Settings'}
        </button>
      </div>
    </div>
  );

  const renderM3UDigestPage = () => (
    <div className="settings-page">
      <div className="settings-page-header">
        <h2>M3U Change Digest</h2>
        <p>Configure email notifications for M3U playlist changes.</p>
      </div>

      {digestLoading && (
        <div className="loading-state">
          <span className="material-icons spinning">sync</span>
          <span>Loading digest settings...</span>
        </div>
      )}

      {digestError && (
        <div className="error-banner">
          <span className="material-icons">error</span>
          {digestError}
          <button onClick={loadDigestSettings}>Retry</button>
        </div>
      )}

      {digestSettings && !digestLoading && (
        <>
          {/* Enable/Disable Section */}
          <div className="settings-section">
            <div className="settings-section-header">
              <span className="material-icons">notifications</span>
              <h3>Digest Notifications</h3>
            </div>

            <div className="settings-group">
              <div className="checkbox-group">
                <input
                  id="digestEnabled"
                  type="checkbox"
                  checked={digestSettings.enabled}
                  onChange={(e) => handleDigestSettingChange('enabled', e.target.checked)}
                />
                <div className="checkbox-content">
                  <label htmlFor="digestEnabled">Enable M3U digest emails</label>
                  <p>Send email notifications when M3U playlists change (groups or streams added/removed).</p>
                </div>
              </div>
            </div>
          </div>

          {/* Frequency Section */}
          <div className="settings-section">
            <div className="settings-section-header">
              <span className="material-icons">schedule</span>
              <h3>Frequency</h3>
            </div>

            <div className="settings-group">
              <div className="form-group-vertical">
                <label htmlFor="digestFrequency">Digest frequency</label>
                <span className="form-description">
                  How often to send digest emails. "Immediate" sends right after each M3U refresh.
                </span>
                <CustomSelect
                  value={digestSettings.frequency}
                  onChange={(val) => handleDigestSettingChange('frequency', val as M3UDigestFrequency)}
                  options={[
                    { value: 'immediate', label: 'Immediate (after each refresh)' },
                    { value: 'hourly', label: 'Hourly' },
                    { value: 'daily', label: 'Daily' },
                    { value: 'weekly', label: 'Weekly' },
                  ]}
                />
              </div>

              <div className="form-group-vertical">
                <label htmlFor="digestThreshold">Minimum changes threshold</label>
                <span className="form-description">
                  Only send digest if at least this many changes occurred.
                </span>
                <input
                  id="digestThreshold"
                  type="number"
                  min="1"
                  max="100"
                  value={digestSettings.min_changes_threshold}
                  onChange={(e) => handleDigestSettingChange('min_changes_threshold', Math.max(1, parseInt(e.target.value) || 1))}
                />
              </div>
            </div>
          </div>

          {/* Content Filters Section */}
          <div className="settings-section">
            <div className="settings-section-header">
              <span className="material-icons">filter_list</span>
              <h3>Content Filters</h3>
            </div>

            <div className="settings-group">
              <div className="checkbox-group">
                <input
                  id="includeGroupChanges"
                  type="checkbox"
                  checked={digestSettings.include_group_changes}
                  onChange={(e) => handleDigestSettingChange('include_group_changes', e.target.checked)}
                />
                <div className="checkbox-content">
                  <label htmlFor="includeGroupChanges">Include group changes</label>
                  <p>Include notifications when groups are added or removed.</p>
                </div>
              </div>

              <div className="checkbox-group">
                <input
                  id="includeStreamChanges"
                  type="checkbox"
                  checked={digestSettings.include_stream_changes}
                  onChange={(e) => handleDigestSettingChange('include_stream_changes', e.target.checked)}
                />
                <div className="checkbox-content">
                  <label htmlFor="includeStreamChanges">Include stream changes</label>
                  <p>Include notifications when streams are added or removed within groups.</p>
                </div>
              </div>

              <div className="checkbox-group">
                <input
                  id="showDetailedList"
                  type="checkbox"
                  checked={digestSettings.show_detailed_list}
                  onChange={(e) => handleDigestSettingChange('show_detailed_list', e.target.checked)}
                />
                <div className="checkbox-content">
                  <label htmlFor="showDetailedList">Show detailed list</label>
                  <p>Include the full list of changed groups and streams in the digest. Disable to show only summary counts.</p>
                </div>
              </div>
            </div>
          </div>

          {/* Recipients Section */}
          <div className="settings-section">
            <div className="settings-section-header">
              <span className="material-icons">mail</span>
              <h3>Email Recipients</h3>
            </div>

            {!smtpConfigured && (
              <div className="warning-banner">
                <span className="material-icons">warning</span>
                <span>
                  SMTP is not configured. Please configure your email server in{' '}
                  <button
                    className="link-button"
                    onClick={() => setActivePage('email')}
                  >
                    Notification Settings
                  </button>{' '}
                  before sending digests.
                </span>
              </div>
            )}

            <div className="settings-group">
              <div className="form-group-vertical">
                <label>Recipients</label>
                <span className="form-description">
                  Email addresses to receive digest notifications.
                </span>
                <div className="email-recipients-list">
                  {digestSettings.email_recipients.length === 0 ? (
                    <span className="no-recipients">No recipients configured</span>
                  ) : (
                    digestSettings.email_recipients.map((email) => (
                      <span key={email} className="email-recipient-tag">
                        {email}
                        <button
                          className="remove-btn"
                          onClick={() => handleRemoveDigestRecipient(email)}
                          title="Remove recipient"
                        >
                          <span className="material-icons">close</span>
                        </button>
                      </span>
                    ))
                  )}
                </div>
                <div className="add-email-row">
                  <input
                    type="email"
                    placeholder="Enter email address"
                    value={newDigestEmail}
                    onChange={(e) => setNewDigestEmail(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && newDigestEmail.trim()) {
                        handleAddDigestRecipient(newDigestEmail);
                        setNewDigestEmail('');
                      }
                    }}
                  />
                  <button
                    className="btn-secondary"
                    onClick={() => {
                      if (newDigestEmail.trim()) {
                        handleAddDigestRecipient(newDigestEmail);
                        setNewDigestEmail('');
                      }
                    }}
                    disabled={!newDigestEmail.trim()}
                  >
                    <span className="material-icons">add</span>
                    Add
                  </button>
                </div>
              </div>
            </div>
          </div>

          {/* Discord Notification Section */}
          <div className="settings-section">
            <div className="settings-section-header">
              <span className="material-icons">forum</span>
              <h3>Discord Notification</h3>
            </div>

            {!discordConfigured && (
              <div className="warning-banner">
                <span className="material-icons">warning</span>
                <span>
                  Discord webhook is not configured. Please configure your Discord webhook in{' '}
                  <button
                    className="link-button"
                    onClick={() => setActivePage('email')}
                  >
                    Notification Settings
                  </button>{' '}
                  before enabling Discord notifications.
                </span>
              </div>
            )}

            <div className="settings-group">
              <div className="checkbox-group">
                <input
                  id="sendToDiscord"
                  type="checkbox"
                  checked={digestSettings.send_to_discord}
                  onChange={(e) => handleDigestSettingChange('send_to_discord', e.target.checked)}
                  disabled={!discordConfigured}
                />
                <div className="checkbox-content">
                  <label htmlFor="sendToDiscord">Send digest to Discord</label>
                  <p>Post M3U change digest to Discord using the shared webhook configured in Notification Settings.</p>
                </div>
              </div>
            </div>
          </div>

          {/* Last Digest Info */}
          {digestSettings.last_digest_at && (
            <div className="settings-section">
              <div className="settings-section-header">
                <span className="material-icons">history</span>
                <h3>Last Digest</h3>
              </div>
              <p className="form-hint">
                Last digest sent: {new Date(digestSettings.last_digest_at).toLocaleString()}
              </p>
            </div>
          )}

          {/* Test Result */}
          {digestTestResult && (
            <div className={`result-banner ${digestTestResult.success ? 'success' : 'error'}`}>
              <span className="material-icons">
                {digestTestResult.success ? 'check_circle' : 'error'}
              </span>
              {digestTestResult.message}
            </div>
          )}

          {/* Actions */}
          <div className="settings-actions">
            <button
              className="btn-secondary"
              onClick={handleSendTestDigest}
              disabled={digestSaving || ((!smtpConfigured || digestSettings.email_recipients.length === 0) && (!discordConfigured || !digestSettings.send_to_discord))}
            >
              <span className="material-icons">send</span>
              Send Test Digest
            </button>
            <button
              className="btn-primary"
              onClick={handleSaveDigestSettings}
              disabled={digestSaving}
            >
              <span className="material-icons">save</span>
              {digestSaving ? 'Saving...' : 'Save Digest Settings'}
            </button>
          </div>
        </>
      )}
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
              <CustomSelect
                value={String(bitrateSampleDuration)}
                onChange={(val) => setBitrateSampleDuration(Number(val))}
                options={[
                  { value: '10', label: '10 seconds' },
                  { value: '20', label: '20 seconds' },
                  { value: '30', label: '30 seconds' },
                ]}
              />
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
            <div style={{ display: 'flex', justifyContent: 'flex-start', gap: '0.75rem' }}>
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
              <button
                type="button"
                className="btn-secondary"
                onClick={handleClearAllProbeStats}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.5rem'
                }}
                title="Delete all probe statistics from the database"
              >
                <span className="material-icons">delete_sweep</span>
                Clear All Probe Stats
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
              <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '1rem' }}>
                <button
                  className="btn-danger"
                  onClick={handleCleanupOrphanedGroups}
                  disabled={cleaningOrphaned || loadingOrphaned}
                >
                  <span className="material-icons">delete_forever</span>
                  {cleaningOrphaned ? 'Cleaning...' : `Delete ${orphanedGroups.length} Orphaned Group(s)`}
                </button>
              </div>
            </div>
          )}

        </div>
      </div>

      {/* Auto-Created Channels Section */}
      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">auto_fix_high</span>
          <h3>Auto-Created Channels</h3>
        </div>
        <p className="form-hint" style={{ marginBottom: '1rem' }}>
          Channels marked as "auto_created" are hidden from the Channel Manager unless their group has Auto Channel Sync enabled.
          Use this tool to convert auto_created channels to manual channels, making them visible in all groups.
        </p>

        <div className="settings-group">
          <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
            <button
              className="btn-secondary"
              onClick={handleLoadAutoCreatedGroups}
              disabled={loadingAutoCreated || clearingAutoCreated}
            >
              <span className="material-icons">search</span>
              {loadingAutoCreated ? 'Scanning...' : 'Scan for Auto-Created Channels'}
            </button>
          </div>

          {autoCreatedGroups.length > 0 && (
            <div style={{ marginTop: '1rem' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.75rem' }}>
                <p style={{ margin: 0 }}>
                  <strong>Found {totalAutoCreatedChannels} auto_created channel(s) in {autoCreatedGroups.length} group(s):</strong>
                </p>
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={handleSelectAllAutoCreatedGroups}
                  style={{ padding: '0.4rem 0.8rem', fontSize: '0.85rem' }}
                >
                  {selectedAutoCreatedGroups.size === autoCreatedGroups.length ? 'Deselect All' : 'Select All'}
                </button>
              </div>

              <div style={{
                maxHeight: '300px',
                overflowY: 'auto',
                border: '1px solid var(--border-color)',
                borderRadius: '6px',
                backgroundColor: 'var(--bg-secondary)'
              }}>
                {autoCreatedGroups.map(group => (
                  <div
                    key={group.id}
                    style={{
                      display: 'flex',
                      alignItems: 'flex-start',
                      padding: '0.75rem 1rem',
                      borderBottom: '1px solid var(--border-color)',
                      cursor: 'pointer',
                      backgroundColor: selectedAutoCreatedGroups.has(group.id) ? 'var(--bg-tertiary)' : 'transparent'
                    }}
                    onClick={() => handleToggleAutoCreatedGroup(group.id)}
                  >
                    <input
                      type="checkbox"
                      checked={selectedAutoCreatedGroups.has(group.id)}
                      onChange={() => handleToggleAutoCreatedGroup(group.id)}
                      onClick={(e) => e.stopPropagation()}
                      style={{ marginRight: '0.75rem', marginTop: '0.2rem' }}
                    />
                    <div style={{ flex: 1 }}>
                      <div style={{ fontWeight: 500 }}>
                        {group.name}
                        <span style={{ color: 'var(--text-secondary)', marginLeft: '0.5rem', fontWeight: 400 }}>
                          ({group.auto_created_count} channel{group.auto_created_count !== 1 ? 's' : ''})
                        </span>
                      </div>
                      {group.sample_channels.length > 0 && (
                        <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginTop: '0.25rem' }}>
                          {group.sample_channels.slice(0, 3).map((ch, i) => (
                            <span key={ch.id}>
                              {i > 0 && ', '}
                              #{ch.channel_number} {ch.name}
                            </span>
                          ))}
                          {group.auto_created_count > 3 && <span>, ...</span>}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>

              <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', marginTop: '1rem' }}>
                <button
                  className="btn-primary"
                  onClick={handleClearAutoCreatedFlag}
                  disabled={clearingAutoCreated || loadingAutoCreated || selectedAutoCreatedGroups.size === 0}
                >
                  <span className="material-icons">
                    {clearingAutoCreated ? 'sync' : 'check_circle'}
                  </span>
                  {clearingAutoCreated
                    ? 'Converting...'
                    : `Convert ${selectedAutoCreatedGroups.size > 0
                        ? autoCreatedGroups
                            .filter(g => selectedAutoCreatedGroups.has(g.id))
                            .reduce((sum, g) => sum + g.auto_created_count, 0)
                        : 0} Channel(s) to Manual`}
                </button>
                {selectedAutoCreatedGroups.size > 0 && (
                  <span style={{ color: 'var(--text-secondary)', fontSize: '0.9rem' }}>
                    {selectedAutoCreatedGroups.size} group{selectedAutoCreatedGroups.size !== 1 ? 's' : ''} selected
                  </span>
                )}
              </div>
            </div>
          )}

        </div>
      </div>

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
            <span className="material-icons">auto_fix_high</span>
            Channel Normalization
          </li>
          <li
            className={`settings-nav-item ${activePage === 'tag-engine' ? 'active' : ''}`}
            onClick={() => setActivePage('tag-engine')}
          >
            <span className="material-icons">label</span>
            Tags
          </li>
          <li
            className={`settings-nav-item ${activePage === 'appearance' ? 'active' : ''}`}
            onClick={() => setActivePage('appearance')}
          >
            <span className="material-icons">palette</span>
            Appearance
          </li>
          <li
            className={`settings-nav-item ${activePage === 'email' ? 'active' : ''}`}
            onClick={() => setActivePage('email')}
          >
            <span className="material-icons">notifications</span>
            Notification Settings
          </li>
          <li
            className={`settings-nav-item ${activePage === 'scheduled-tasks' ? 'active' : ''}`}
            onClick={() => setActivePage('scheduled-tasks')}
          >
            <span className="material-icons">schedule</span>
            Scheduled Tasks
          </li>
          <li
            className={`settings-nav-item ${activePage === 'm3u-digest' ? 'active' : ''}`}
            onClick={() => setActivePage('m3u-digest')}
          >
            <span className="material-icons">mail</span>
            M3U Digest
          </li>
          <li
            className={`settings-nav-item ${activePage === 'maintenance' ? 'active' : ''}`}
            onClick={() => setActivePage('maintenance')}
          >
            <span className="material-icons">build</span>
            Maintenance
          </li>
          <li
            className={`settings-nav-item ${activePage === 'linked-accounts' ? 'active' : ''}`}
            onClick={() => setActivePage('linked-accounts')}
          >
            <span className="material-icons">link</span>
            Linked Accounts
          </li>
          {user?.is_admin && (
            <>
              <li className="settings-nav-divider">Administration</li>
              <li
                className={`settings-nav-item ${activePage === 'auth-settings' ? 'active' : ''}`}
                onClick={() => setActivePage('auth-settings')}
              >
                <span className="material-icons">security</span>
                Authentication
              </li>
              <li
                className={`settings-nav-item ${activePage === 'user-management' ? 'active' : ''}`}
                onClick={() => setActivePage('user-management')}
              >
                <span className="material-icons">people</span>
                User Management
              </li>
              <li
                className={`settings-nav-item ${activePage === 'tls-settings' ? 'active' : ''}`}
                onClick={() => setActivePage('tls-settings')}
              >
                <span className="material-icons">https</span>
                TLS Certificates
              </li>
            </>
          )}
        </ul>
      </nav>

      <div className="settings-content">
        {activePage === 'general' && renderGeneralPage()}
        {activePage === 'channel-defaults' && renderChannelDefaultsPage()}
        {activePage === 'normalization' && renderNormalizationPage()}
        {activePage === 'tag-engine' && <TagEngineSection />}
        {activePage === 'appearance' && renderAppearancePage()}
        {activePage === 'email' && renderEmailSettingsPage()}
        {activePage === 'scheduled-tasks' && <ScheduledTasksSection userTimezone={userTimezone} />}
        {activePage === 'm3u-digest' && renderM3UDigestPage()}
        {activePage === 'maintenance' && renderMaintenancePage()}
        {activePage === 'linked-accounts' && <LinkedAccountsSection />}
        {activePage === 'auth-settings' && <AuthSettingsSection isAdmin={user?.is_admin ?? false} />}
        {activePage === 'user-management' && <UserManagementSection isAdmin={user?.is_admin ?? false} currentUserId={user?.id ?? 0} />}
        {activePage === 'tls-settings' && <TLSSettingsSection isAdmin={user?.is_admin ?? false} />}
      </div>

      <DeleteOrphanedGroupsModal
        isOpen={showDeleteModal}
        onClose={() => setShowDeleteModal(false)}
        onConfirm={handleConfirmDelete}
        groups={orphanedGroups}
      />

      {showProbeResultsModal && probeResults && (
        <div
          className="modal-overlay"
          onClick={() => setShowProbeResultsModal(false)}
        >
          <div
            className="modal-container modal-lg probe-results-modal"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-header">
              <h2 className={probeResultsType === 'success' ? 'success' : probeResultsType === 'skipped' ? 'skipped' : 'failed'}>
                {probeResultsType === 'success' ? 'Successful Streams' : probeResultsType === 'skipped' ? 'Skipped Streams' : 'Failed Streams'} (
                {probeResultsType === 'success' ? probeResults.success_count : probeResultsType === 'skipped' ? probeResults.skipped_count : probeResults.failed_count})
              </h2>
              <button
                onClick={() => setShowProbeResultsModal(false)}
                className="modal-close-btn"
              >
                <span className="material-icons">close</span>
              </button>
            </div>

            <div className="modal-body probe-results-body">
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
                  <div className="empty-state">
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

            {probeResultsType === 'failed' && probeResults.failed_streams.length > 0 && (
              <div className="modal-footer">
                <button
                  onClick={handleRerunFailed}
                  className="btn-primary"
                  disabled={probingAll}
                >
                  {probingAll ? 'Re-probing...' : 'Re-probe Failed Streams'}
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Reordered Channels Modal */}
      {showReorderModal && reorderData && (
        <div
          className="modal-overlay"
          onClick={() => setShowReorderModal(false)}
        >
          <div
            className="modal-container modal-xl reorder-modal"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-header">
              <h2>
                Reordered Channels ({reorderData.length})
              </h2>
              <button
                onClick={() => setShowReorderModal(false)}
                className="modal-close-btn"
              >
                <span className="material-icons">close</span>
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

            <div className="modal-body reorder-body">
              {reorderData.length === 0 ? (
                <div className="empty-state">
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

          </div>
        </div>
      )}

      <SettingsModal
        isOpen={showConnectionModal}
        onClose={() => setShowConnectionModal(false)}
        onSaved={() => {
          setShowConnectionModal(false);
          loadSettings();
        }}
      />
    </div>
  );
}
