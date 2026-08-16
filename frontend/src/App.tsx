// Pre-existing react-hooks/exhaustive-deps warnings in this file predate
// PR #70 (ErrorBoundary wiring) and are tracked for cleanup in bead
// enhancedchannelmanager-zjge5. Suppress at file scope so the PR-mode
// lint gate blocks only new violations introduced here, not grandfathered
// ones inherited from dev. Remove this directive once zjge5 lands.
/* eslint-disable react-hooks/exhaustive-deps */
import { useState, useEffect, useCallback, useRef, useMemo, Suspense, lazy } from 'react';
import {
  SettingsModal,
  EditModeExitDialog,
  EditModeRestoreDialog,
  EditModeRestoredBadge,
  TabNavigation,
  PageHeader,
  UserMenu,
  NAVIGATE_TO_ORPHANED_GROUPS_EVENT,
  type TabId,
} from './components';
import { ChannelManagerTab } from './components/tabs/ChannelManagerTab';
import { OperatorDashboard } from './components/tabs/OperatorDashboard';
import { useChangeHistory, useEditMode, useHashRoute, useDedupOnDrop, useServerDataInvalidation } from './hooks';
import { useAuth } from './hooks/useAuth';
import { operatorLedgerKey, planLedgerRestore } from './utils/stagedLedgerStorage';
import type { DedupDropReport } from './hooks';
import { StreamDedupModal } from './components/StreamDedupModal';
import * as api from './services/api';
import type { Channel, ChannelGroup, ChannelProfile, Stream, StreamGroupInfo, M3UAccount, M3UGroupSetting, Logo, ChangeInfo, EPGData, StreamProfile, EPGSource, ChannelListFilterSettings, CommitProgress, CommitFailure } from './types';
import packageJson from '../package.json';
import { logger } from './utils/logger';
import { setDateFormatLocale } from './utils/formatting';
import { OPEN_TASK_EDITOR_EVENT, OPEN_TASK_EDITOR_STORAGE_KEY } from './utils/openTaskEditor';
import { computeAutoRename } from './utils/channelRename';
import { planChannelNumberShift, type PlannedChannelShift } from './utils/channelNumberShift';
import { NumberingConflictDialog } from './components/NumberingConflictDialog';
import type { NumberingConflict, ReconcileDecision } from './utils/channelNumberConcurrency';
import { registerVLCModalCallback, downloadM3U } from './utils/vlc';
import { VLCProtocolHelperModal } from './components/VLCProtocolHelperModal';
import { NotificationCenter } from './components/NotificationCenter';
import { NotificationProvider } from './contexts/NotificationContext';
import { BackupDestinationPromptProvider } from './contexts/BackupDestinationPromptContext';
import { ErrorBoundary } from './components/ErrorBoundary';
import { SkipToMainContent } from './components/AppLandmarks';
import { ROUTE_TITLES } from './components/routeTitles';
import {
  getGuardedPopStateDecision,
  getGuardedRouteDecision,
  getGuardedSignOutDecision,
  isPlainPrimaryActivation,
  resolvePendingRouteResume,
  ROUTE_HIERARCHY,
  type PendingRouteChange,
} from './components/routeHierarchy';
import type { RouteChangeGuardDetail } from './hooks/useHashRoute';
import type { SettingsPage } from './hooks/useHashRoute';
import { useAdminNavVisible } from './hooks/useAuth';
import { settingsSectionHeading } from './components/settingsSections';
import { RouteHeaderTargetProvider } from './components/RouteHeaderSlots';
import { classifySourceLoadError, type SourceLoadState } from './components/sourceLoadState';
import type { WorkspaceSource } from './components/workspaceLoadState';
import {
  setTelemetryRuntimeEnabled,
  withImportTelemetry,
} from './services/clientErrorReporter';
import './App.css';

// All known sort criteria - used to merge new criteria into saved settings
const ALL_SORT_CRITERIA: api.SortCriterion[] = ['resolution', 'bitrate', 'framerate', 'video_codec', 'm3u_priority', 'audio_channels', 'custom_streams', 'catchup'];
const DEFAULT_SORT_ENABLED: api.SortEnabledMap = {
  resolution: true, bitrate: true, framerate: true, video_codec: false, m3u_priority: false, audio_channels: false, custom_streams: false, catchup: false
};

// Merge saved sort criteria with any new criteria that may have been added
function mergeSortCriteria(
  savedPriority: api.SortCriterion[] | undefined,
  savedEnabled: api.SortEnabledMap | undefined
): { priority: api.SortCriterion[]; enabled: api.SortEnabledMap } {
  if (!savedPriority || savedPriority.length === 0) {
    return { priority: ALL_SORT_CRITERIA, enabled: DEFAULT_SORT_ENABLED };
  }
  const priority = [...savedPriority];
  const enabled = { ...DEFAULT_SORT_ENABLED, ...savedEnabled };
  for (const criterion of ALL_SORT_CRITERIA) {
    if (!priority.includes(criterion)) {
      priority.push(criterion);
      enabled[criterion] = false;
    }
  }
  return { priority, enabled };
}

// Lazy load non-primary tabs. Each dynamic import is wrapped in
// ``withImportTelemetry`` so chunk-load failures (stale bundles, 404s
// on deployed hashed chunks) fire an ADR-006 ``kind: 'chunk_load'``
// report in addition to the Vite ``vite:preloadError`` window event.
// The wrapper is a pure side-effect — it re-rejects with the same
// error so Suspense / ErrorBoundary behavior is unchanged.
const M3UManagerTab = lazy(() => withImportTelemetry(import('./components/tabs/M3UManagerTab')).then(m => ({ default: m.M3UManagerTab })));
const EPGManagerTab = lazy(() => withImportTelemetry(import('./components/tabs/EPGManagerTab')).then(m => ({ default: m.EPGManagerTab })));
const GuideTab = lazy(() => withImportTelemetry(import('./components/tabs/GuideTab')).then(m => ({ default: m.GuideTab })));
const LogoManagerTab = lazy(() => withImportTelemetry(import('./components/tabs/LogoManagerTab')).then(m => ({ default: m.LogoManagerTab })));
const M3UChangesTab = lazy(() => withImportTelemetry(import('./components/tabs/M3UChangesTab')).then(m => ({ default: m.M3UChangesTab })));
const JournalTab = lazy(() => withImportTelemetry(import('./components/tabs/JournalTab')).then(m => ({ default: m.JournalTab })));
const StatsTab = lazy(() => withImportTelemetry(import('./components/tabs/StatsTab')).then(m => ({ default: m.StatsTab })));
const SettingsTab = lazy(() => withImportTelemetry(import('./components/tabs/SettingsTab')).then(m => ({ default: m.SettingsTab })));
const ChannelPipelineTab = lazy(() => withImportTelemetry(import('./components/channelPipeline/ChannelPipelineTab')).then(m => ({ default: m.ChannelPipelineTab })));

// Self-contained timer component — updates only itself every second,
// not the entire App tree (which was the previous behavior)
function EditModeTimer({ enteredAt }: { enteredAt: number }) {
  // Initial value is derived from `enteredAt` via lazy init so the first
  // render shows the correct elapsed time without needing a synchronous
  // setState inside the effect (which triggers cascading renders and is
  // flagged by react-hooks/set-state-in-effect). The component remounts
  // whenever edit mode toggles (the callsite renders <EditModeTimer/> only
  // while `editModeEnteredAt !== null`), so `enteredAt` is stable for the
  // lifetime of the component.
  const [seconds, setSeconds] = useState(() => Math.floor((Date.now() - enteredAt) / 1000));

  useEffect(() => {
    const interval = setInterval(() => {
      setSeconds(Math.floor((Date.now() - enteredAt) / 1000));
    }, 1000);
    return () => clearInterval(interval);
  }, [enteredAt]);

  const display = seconds < 60
    ? `${seconds}s`
    : seconds % 60 > 0
      ? `${Math.floor(seconds / 60)}m ${seconds % 60}s`
      : `${Math.floor(seconds / 60)}m`;

  // bd-b2vf5: with no label, "Edit Mode (1m 20s)" reads ambiguously — a
  // first-time user could easily mistake it for a countdown-to-cancel
  // warning instead of what it actually is, elapsed time since Edit Mode
  // was entered (it only ever counts up).
  return (
    <span className="edit-mode-timer" title="Time elapsed since Edit Mode was entered (counts up, not a countdown)">
      ({display})
    </span>
  );
}

type OperationLoadState = { state: SourceLoadState; hasSnapshot: boolean };

function App() {
  // Who is signed in. Read here only to bind staged Edit Mode work to an
  // identity; every authorisation decision is the backend's.
  const { user: authenticatedUser } = useAuth();

  // Health check and version info
  const [health, setHealth] = useState<api.HealthResponse | null>(null);
  const [healthSourceState, setHealthSourceState] = useState<OperationLoadState>({ state: 'loading', hasSnapshot: false });
  const [error, setError] = useState<string | null>(null);

  // Channels state
  const [channels, setChannels] = useState<Channel[]>([]);
  const [channelInventoryTotal, setChannelInventoryTotal] = useState(0);
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
  // Read the latest committed inventory inside stable async callbacks. Using
  // `streams` directly there captures the render that created the callback,
  // so a later group failure could miss rows loaded by an earlier group.
  const streamsSnapshotRef = useRef<Stream[]>([]);
  const [providers, setProviders] = useState<M3UAccount[]>([]);
  const [providerSourceState, setProviderSourceState] = useState<OperationLoadState>({ state: 'loading', hasSnapshot: false });
  const [streamGroups, setStreamGroups] = useState<StreamGroupInfo[]>([]);
  const [streamInventoryTotal, setStreamInventoryTotal] = useState(0);

  // Accumulates every stream ever returned by a search so ChannelsPane can
  // resolve staged stream IDs even after the stream search term has changed.
  const [seenStreams, setSeenStreams] = useState<Map<number, Stream>>(new Map());
  const rememberSeenStreams = useCallback((list: Stream[]) => {
    if (list.length === 0) return;
    setSeenStreams((prev) => {
      let changed = false;
      const next = new Map(prev);
      for (const s of list) {
        if (!next.has(s.id)) {
          next.set(s.id, s);
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, []);

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
  const [channelSourceStates, setChannelSourceStates] = useState<Record<'groups' | 'channels', OperationLoadState>>({
    groups: { state: 'loading', hasSnapshot: false },
    channels: { state: 'loading', hasSnapshot: false },
  });
  const [channelInventoryState, setChannelInventoryState] = useState<OperationLoadState>({ state: 'loading', hasSnapshot: false });
  const [streamSourceStates, setStreamSourceStates] = useState<Record<string, OperationLoadState>>({
    metadata: { state: 'loading', hasSnapshot: false },
  });
  const [streamInventoryState, setStreamInventoryState] = useState<OperationLoadState>({ state: 'loading', hasSnapshot: false });
  const [streamMatchingTotal, setStreamMatchingTotal] = useState<number | null>(null);
  const streamRetryOperations = useRef<Record<string, () => Promise<unknown>>>({});

  // Settings state
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [autoRenameChannelNumber, setAutoRenameChannelNumber] = useState(false);
  const [dispatcharrUrl, setDispatcharrUrl] = useState('');
  const [showStreamUrls, setShowStreamUrls] = useState(true);
  const [strikeThreshold, setStrikeThreshold] = useState(3);
  const [hideUngroupedStreams, setHideUngroupedStreams] = useState(true);
  const [hideEpgUrls, setHideEpgUrls] = useState(false);
  const [hideM3uUrls, setHideM3uUrls] = useState(false);
  const [gracenoteConflictMode, setGracenoteConflictMode] = useState<'ask' | 'skip' | 'overwrite'>('ask');
  const [epgAutoMatchThreshold, setEpgAutoMatchThreshold] = useState(80);
  // BD-J / bd-gfxrz: when true, suppress the "N streams queued for dedup
  // review" toast that fires after an M3U refresh queues pending merges.
  // Sourced from `settings.dedup_m3u_toast_suppressed` (BD-K Settings UI).
  const [dedupM3uToastSuppressed, setDedupM3uToastSuppressed] = useState(false);
  // bd-dgs64 (GH #591): opt out of the M3UGroupsModal single-owner auto-sync
  // guard — when true, a channel group already auto-synced by another M3U
  // account is no longer locked; the toggle/Start#/Settings stay usable
  // (with a shared-ownership indicator). Sourced from
  // `settings.allow_multi_provider_auto_sync` (admin-only, default false).
  const [allowMultiProviderAutoSync, setAllowMultiProviderAutoSync] = useState(false);
  const [normalizeOnChannelCreate, setNormalizeOnChannelCreate] = useState(false);
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
    streamSortPriority: ['resolution', 'bitrate', 'framerate'] as api.SortCriterion[],
    streamSortEnabled: DEFAULT_SORT_ENABLED as api.SortEnabledMap,
    deprioritizeFailedStreams: true,
    deprioritizeBlackScreen: true,
    deprioritizeLowFps: true,
    m3uAccountPriorities: {} as api.M3UAccountPriorities,
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
    filterMissingLogo: false,
    filterMissingTvgId: false,
    filterMissingEpgData: false,
    filterMissingGracenote: false,
    filterFailedStreams: true,
    filterWorkingStreams: true,
    filterUnprobedStreams: true,
  };
  const [channelListFilters, setChannelListFilters] = useState<ChannelListFilterSettings>(() => {
    const saved = localStorage.getItem('channelListFilters');
    return saved ? { ...defaultFilterSettings, ...JSON.parse(saved) } : defaultFilterSettings;
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

  // Track if streams have been explicitly requested (lazy loading - don't auto-load on mount)
  const streamsExplicitlyRequested = useRef(false);
  // Track which stream groups have been loaded (for per-group lazy loading)
  const loadedStreamGroupsRef = useRef<Set<string>>(new Set());
  // Ref to track current channel groups for comparison in event handlers (avoids stale closures)
  const channelGroupsRef = useRef(channelGroups);
  channelGroupsRef.current = channelGroups;

  // Edit mode exit dialog state
  const [showExitDialog, setShowExitDialog] = useState(false);
  const [commitProgress, setCommitProgress] = useState<CommitProgress | null>(null);
  // Set when a commit does not fully apply, so the exit dialog can say so
  // instead of closing silently (bead enhancedchannelmanager-udq1j).
  const [commitFailure, setCommitFailure] = useState<CommitFailure | null>(null);
  /**
   * Channel numbers somebody else changed while this session was staging, plus
   * the server read they were found in (bead
   * enhancedchannelmanager-ic884.4). Set when Apply refuses to write over them;
   * cleared the moment the operator has answered for every one.
   */
  const [numberingConflicts, setNumberingConflicts] =
    useState<{ conflicts: NumberingConflict[]; serverChannels: Channel[] } | null>(null);
  /**
   * Set when the operator has answered every conflict, so Apply can run again
   * on the NEXT render rather than inside the handler that reconciled.
   *
   * Not a nicety. `commit` closes over `stagedOperations`, so calling it from
   * the same tick as the reconcile would run the OLD plan — find the same
   * conflict, reopen the dialog, and lose the operator's answers to the
   * dialog's own "new conflicts are new questions" reset. The browser guard
   * caught exactly that loop.
   */
  const [applyAfterReconcile, setApplyAfterReconcile] = useState(false);

  // Tab navigation state (hash-based routing)
  const {
    activeTab, settingsPage, m3uChangesHours, setHash, setSettingsPage, resumeRejectedNavigation,
  } = useHashRoute();
  // Gates the administration-only entries in the sidebar's Settings drill-in.
  // This used to be `Boolean(user?.is_admin)`, which hid the whole
  // Administration group on any auth-disabled instance, because `user` is
  // permanently null there (bead enhancedchannelmanager-p388h, absorbing
  // ee5f1). SettingsTab's own gate already reads null as PERMITTED for the
  // same reason (`isAdminUser = !user || user.is_admin`, bead
  // enhancedchannelmanager-9kwzp.10); the navigation into it was the one place
  // still resolving unknown to hidden.
  const adminNavVisible = useAdminNavVisible();
  // `onCancel` undoes whatever the REQUESTER did before asking to navigate, and
  // runs only if the operator keeps editing. A caller that leaves state behind
  // for its destination to pick up (see the task-editor handoff below) needs a
  // way to take it back when the guard refuses the navigation, or a deferred
  // route becomes a stale intent (bead enhancedchannelmanager-6fi7p).
  // A ref rather than state: nothing renders from the pending route, and an
  // event handler has to be able to read the intent it is about to REPLACE. A
  // second deferral — the operator presses Back while the exit dialog from a
  // task-editor handoff is still up — must not drop the first one's `onCancel`
  // on the floor, which would strand exactly the stale sessionStorage intent
  // that callback exists to clear.
  const pendingRouteChangeRef = useRef<PendingRouteChange | null>(null);
  const replacePendingRouteChange = useCallback((next: PendingRouteChange | null) => {
    const outgoing = pendingRouteChangeRef.current;
    if (outgoing && outgoing !== next) outgoing.onCancel?.();
    pendingRouteChangeRef.current = next;
  }, []);
  // Unmounting with a deferred navigation still pending abandons it for good:
  // signing out flips auth state, ProtectedRoute swaps this whole tree for
  // LoginPage, and nothing here gets another render to notice. The in-memory
  // half of that does not matter — it dies with the tree — but `onCancel`
  // clears a sessionStorage handoff, which SURVIVES both the unmount and the
  // next sign-in in the same tab, and would then redirect the operator's next
  // visit to ANY Settings page to Scheduled Tasks. Unmount is the honest
  // trigger: it covers sign-out, session expiry and teardown alike, without
  // App having to observe an auth transition it is never rendered for.
  useEffect(() => () => {
    pendingRouteChangeRef.current?.onCancel?.();
    pendingRouteChangeRef.current = null;
  }, []);
  // Set when the operator asked to sign out with staged changes: the sign-out
  // itself, held until they answer the exit dialog (bead epic
  // enhancedchannelmanager-r93hq). A ref, not state — nothing renders from it,
  // and running a stored continuation from inside a state updater would fire
  // it twice under StrictMode.
  const pendingSignOutRef = useRef<(() => void | Promise<void>) | null>(null);
  const [routeHeaderTargets, setRouteHeaderTargets] = useState({
    'primary-action': null as HTMLDivElement | null,
    status: null as HTMLDivElement | null,
    controls: null as HTMLDivElement | null,
  });
  const setPrimaryActionTarget = useCallback((target: HTMLDivElement | null) => {
    setRouteHeaderTargets((current) => (
      current['primary-action'] === target ? current : { ...current, 'primary-action': target }
    ));
  }, []);
  const setStatusTarget = useCallback((target: HTMLDivElement | null) => {
    setRouteHeaderTargets((current) => (
      current.status === target ? current : { ...current, status: target }
    ));
  }, []);
  const setControlsTarget = useCallback((target: HTMLDivElement | null) => {
    setRouteHeaderTargets((current) => (
      current.controls === target ? current : { ...current, controls: target }
    ));
  }, []);
  const routeHeadingRef = useRef<HTMLHeadingElement>(null);
  const focusHeadingOnRouteChangeRef = useRef(false);

  useEffect(() => {
    document.title = `${ROUTE_TITLES[activeTab]} | Enhanced Channel Manager`;
    if (focusHeadingOnRouteChangeRef.current) {
      focusHeadingOnRouteChangeRef.current = false;
      routeHeadingRef.current?.focus();
    }
  }, [activeTab]);

  // Stream group drop trigger (for opening bulk create modal from channels pane)
  // Supports multiple groups being dropped at once
  const [droppedStreamGroupNames, setDroppedStreamGroupNames] = useState<string[] | null>(null);
  // Stream IDs drop trigger (for opening bulk create modal when dropping individual streams)
  // Includes target group ID and starting channel number for pre-filling the modal
  const [droppedStreamIds, setDroppedStreamIds] = useState<number[] | null>(null);
  const [droppedStreamTargetGroupId, setDroppedStreamTargetGroupId] = useState<number | null>(null);
  const [droppedStreamStartingNumber, setDroppedStreamStartingNumber] = useState<number | null>(null);
  // Manual entry trigger (for opening bulk create modal without pre-selected streams)
  const [manualEntryTrigger, setManualEntryTrigger] = useState(false);

  /**
   * Identity this tab's staged Edit Mode work belongs to (epic
   * enhancedchannelmanager-r93hq).
   *
   * `ProtectedRoute` does not render this component until the auth check has
   * settled, so `user` is resolved by the time `useEditMode` reads this on its
   * first render — which is the render that decides whether a persisted ledger
   * is offered or destroyed.
   */
  const operatorKey = useMemo(() => operatorLedgerKey(authenticatedUser), [authenticatedUser]);

  // Edit mode for staging changes
  const {
    isEditMode,
    isCommitting,
    stagedOperationCount,
    modifiedChannelIds,
    displayChannels,
    stagedGroups,
    deletedGroupIds,
    renamedGroupNames,
    canLocalUndo,
    canLocalRedo,
    editModeEnteredAt,
    pendingRestore,
    restoredFrom,
    restoreStagedLedger,
    dismissPendingRestore,
    enterEditMode,
    exitEditMode: rawExitEditMode,
    stageUpdateChannel,
    stageAddStream,
    stageSetProfileMembership,
    stageRestoreChannelGroup,
    stageClearStreamStats,
    stagedSideEffects,
    stageRemoveStream,
    stageReorderStreams,
    stageBulkAssignNumbers,
    stageCreateChannel,
    stageDeleteChannel,
    stageDeleteChannelGroup,
    stageRenameChannelGroup,
    stageCreateGroup,
    summary,
    commit,
    discard,
    reconcileNumberingConflicts,
    localUndo,
    localRedo,
    startBatch,
    endBatch,
  } = useEditMode({
    channels,
    operatorKey,
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
      setChannelFilters((prev) => {
        const nextGroupFilter = prev.groupFilter.filter(id => id >= 0);
        if (nextGroupFilter.length === prev.groupFilter.length) {
          return prev;
        }
        return { ...prev, groupFilter: nextGroupFilter };
      });
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

  // Every "the operator chose to leave" path ends the same way: honour what
  // the exit dialog was holding back. Deliberately NOT a cancel path — none of
  // these abandon the request, so none of them may fire `onCancel`.
  //
  // A deferred Back/Forward resumes as a real history transition rather than a
  // pushState (bead enhancedchannelmanager-6fi7p): the operator pressed Back,
  // so they should end up on the entry they pressed Back to, not on a new
  // entry with that one still behind them.
  //
  // A history transition is a REQUEST, though, and the browser can decline it
  // silently. The operator answered the dialog either way, so the destination
  // still has to be honoured — by hash, on a new entry, which is the same
  // fallback a navigation that was never a history transition already takes.
  const completeDeferredExit = useCallback(() => {
    const pendingRoute = pendingRouteChangeRef.current;
    if (pendingRoute) {
      pendingRouteChangeRef.current = null;
      const resume = resolvePendingRouteResume(pendingRoute);
      if (resume.kind === 'history') {
        resumeRejectedNavigation(
          resume.delta,
          () => setHash(pendingRoute.tab, pendingRoute.settingsPage),
        );
      } else {
        setHash(resume.tab, resume.settingsPage);
      }
    }
    const pendingSignOut = pendingSignOutRef.current;
    pendingSignOutRef.current = null;
    if (pendingSignOut) void pendingSignOut();
  }, [resumeRejectedNavigation, setHash]);

  // Handle dialog actions
  const handleApplyChanges = useCallback(async () => {
    setCommitProgress({ current: 0, total: 1, currentOperation: 'Starting...' });
    const result = await commit((progress) => {
      setCommitProgress(progress);
    }, { continueOnError: true });
    setCommitProgress(null);

    // NOTHING WAS APPLIED, and this is a question rather than a failure: a
    // channel number this session was about to write has moved on the server
    // since the session's baseline was captured (bead
    // enhancedchannelmanager-ic884.4). The operator answers keep-mine /
    // take-theirs per channel and Apply runs again with those answers folded
    // into the staged plan; the changes that do not overlap are untouched
    // throughout. Handled BEFORE the failure branch so a conflict is never
    // reported as "some changes were not applied", which would be false.
    if (result.numberingConflicts && result.numberingConflicts.length > 0) {
      setNumberingConflicts({
        conflicts: result.numberingConflicts,
        serverChannels: result.serverChannels ?? [],
      });
      return;
    }

    // A commit the server reported as partially applied used to close the
    // dialog exactly like a clean one, leaving the operator to discover the
    // missing channel themselves (bead enhancedchannelmanager-udq1j). Hold the
    // dialog open on the outcome instead.
    if (result.operationsFailed > 0 || !result.success) {
      const messages: string[] = [];
      const seen = new Set<string>();
      for (const err of result.errors) {
        const subject = err.channelName || err.streamName || err.entityName;
        const line = subject ? `${subject}: ${err.error}` : err.error;
        if (seen.has(line)) continue;
        seen.add(line);
        messages.push(line);
        if (messages.length === 5) break;
      }
      for (const issue of result.validationIssues ?? []) {
        if (seen.has(issue.message)) continue;
        seen.add(issue.message);
        messages.push(issue.message);
        if (messages.length === 5) break;
      }
      setCommitFailure({
        applied: result.operationsApplied,
        failed: result.operationsFailed,
        messages,
      });
      return;
    }

    setShowExitDialog(false);
    // Clear selection when exiting edit mode
    setSelectedChannelIds(new Set());
    // Clear checkpoints when exiting edit mode
    clearHistory();
    // Carry the operator wherever they were trying to go — or sign them out
    completeDeferredExit();
  }, [commit, clearHistory, completeDeferredExit]);

  /**
   * Fold the operator's per-channel answers into the staged plan, then Apply
   * again (bead enhancedchannelmanager-ic884.4).
   *
   * The re-Apply is deliberate rather than a shortcut past the check: the
   * reconcile re-baselines the session on the lineup the answers were given
   * against, so the second pass runs the SAME check over the SAME staged queue
   * and finds nothing left to ask about — unless something moved again in the
   * meantime, in which case asking again is exactly right.
   *
   * An operation dropped whole by a take-theirs answer is named to the
   * operator rather than left for them to notice, because a range renumbering
   * disappearing from the change count with no explanation is indistinguishable
   * from a bug.
   */
  const handleReconcileNumbering = useCallback((decisions: ReconcileDecision[]) => {
    const pending = numberingConflicts;
    if (!pending) return;
    const outcome = reconcileNumberingConflicts(decisions, pending.serverChannels);
    setNumberingConflicts(null);
    if (outcome.removed.length > 0) {
      // `setError` is the same channel Apply's own refusals use — `App`
      // renders `NotificationProvider`, so it is outside its own provider and
      // cannot raise a toast. A dropped renumbering has to be SAID: a change
      // disappearing from the count with no explanation is indistinguishable
      // from a bug.
      setError(
        `${outcome.removed.length} staged change${outcome.removed.length !== 1 ? 's were' : ' was'} ` +
        `dropped so the server's channel numbers could be kept: ` +
        `${outcome.removed.map((entry) => entry.description).join('; ')}`,
      );
    }
    setApplyAfterReconcile(true);
  }, [numberingConflicts, reconcileNumberingConflicts]);

  // The re-Apply itself, one render later, when `commit` has been rebuilt from
  // the reconciled operation queue. See `applyAfterReconcile`.
  useEffect(() => {
    if (!applyAfterReconcile) return;
    setApplyAfterReconcile(false);
    void handleApplyChanges();
  }, [applyAfterReconcile]);

  const handleAcknowledgeCommitFailure = useCallback(() => {
    setCommitFailure(null);
    setShowExitDialog(false);
    setSelectedChannelIds(new Set());
    clearHistory();
    completeDeferredExit();
  }, [clearHistory, completeDeferredExit]);

  const handleDiscardChanges = useCallback(() => {
    discard();
    setSelectedChannelIds(new Set());
    setShowExitDialog(false);
    // Clear checkpoints when exiting edit mode
    clearHistory();
    // Carry the operator wherever they were trying to go — or sign them out
    completeDeferredExit();
  }, [discard, clearHistory, completeDeferredExit]);

  // The ONE cancel path. The three handlers above all navigate through to the
  // pending route, so `onCancel` must not fire from any of them: this is the
  // only place the requested navigation is genuinely abandoned.
  const handleKeepEditing = useCallback(() => {
    setShowExitDialog(false);
    replacePendingRouteChange(null);
    // A vetoed Back has ALREADY been rewound to the entry the operator was on
    // by the router, so keeping editing needs to undo nothing here — they are
    // still on Channel Manager, still in Edit Mode, with their staged work.
    pendingSignOutRef.current = null;
    focusHeadingOnRouteChangeRef.current = false;
  }, [replacePendingRouteChange]);

  /**
   * Abandon this Apply and go back to editing, with every staged change intact.
   *
   * Routed through {@link handleKeepEditing} rather than just closing the
   * dialog: the operator asked to Apply, which may have deferred a navigation
   * or a sign-out behind it, and answering "not now" to the conflict question
   * abandons that intent exactly as pressing Keep Editing does. Closing only
   * the dialogs would leave the deferred intent armed with nothing left to
   * complete it.
   */
  const handleKeepNumberingConflicts = useCallback(() => {
    setNumberingConflicts(null);
    handleKeepEditing();
  }, [handleKeepEditing]);

  // Handle tab change - check for edit mode with pending changes
  const handleRouteChange = useCallback((
    newTab: TabId,
    settingsPage?: SettingsPage,
    options?: { onCancel?: () => void },
  ) => {
    // A same-route request with no settings page never navigates and never
    // defers, so it can neither strand an intent nor cancel one — it just moves
    // focus. `onCancel` deliberately does not fire here: nothing was abandoned.
    // Both event callers below pass a settings page, so they cannot reach it.
    if (newTab === activeTab && !settingsPage) {
      routeHeadingRef.current?.focus();
      return;
    }
    focusHeadingOnRouteChangeRef.current = true;

    const decision = getGuardedRouteDecision(isEditMode, stagedOperationCount, newTab);
    if (decision === 'confirm') {
      // Show confirmation dialog and store pending tab change
      setShowExitDialog(true);
      replacePendingRouteChange({ tab: newTab, settingsPage, onCancel: options?.onCancel });
      return;
    }

    if (decision === 'exit-and-navigate') {
      // Exit edit mode when leaving Channel Manager
      rawExitEditMode();
      setSelectedChannelIds(new Set());
    }

    setHash(newTab, settingsPage);
  }, [activeTab, isEditMode, stagedOperationCount, rawExitEditMode, setHash, replacePendingRouteChange]);

  const handleTabChange = useCallback((newTab: TabId) => {
    handleRouteChange(newTab);
  }, [handleRouteChange]);

  // Browser Back/Forward — the one navigation the Edit Mode guard could not
  // see (bead enhancedchannelmanager-6fi7p).
  //
  // `useHashRoute` has always asked permission before a popstate transition,
  // via the cancelable `ecm:before-route-change` event, and always had working
  // machinery to rewind a refused one. Nothing here listened. The only
  // production listener for that event in the whole frontend was SettingsTab's
  // unsaved-form guard, so an operator with staged channel edits who pressed
  // Back left Edit Mode with no dialog and no staged work. `handleRouteChange`
  // is not on that path at all: Back does not go through a tab click.
  //
  // Vetoing is synchronous and the operator's answer is not, so the two halves
  // are split. This handler refuses the transition, which makes the router
  // rewind history to the entry the operator was on — so the app is never left
  // showing a route it did not accept — and opens the exit dialog carrying the
  // delta that re-runs the transition. `completeDeferredExit` replays it on
  // Apply or Discard; Keep Editing simply leaves the rewind in place.
  useEffect(() => {
    const handler = (event: Event) => {
      const detail = (event as CustomEvent<RouteChangeGuardDetail>).detail;
      const decision = getGuardedPopStateDecision(detail, isEditMode, stagedOperationCount);
      if (decision === 'ignore') return;

      if (decision === 'exit-and-navigate') {
        // Nothing staged: let the transition through and tidy up, exactly as
        // a tab click to the same destination would.
        rawExitEditMode();
        setSelectedChannelIds(new Set());
        return;
      }

      event.preventDefault();
      focusHeadingOnRouteChangeRef.current = true;
      setShowExitDialog(true);
      replacePendingRouteChange({
        tab: detail.tab,
        settingsPage: detail.settingsPage ?? undefined,
        historyDelta: detail.historyDelta,
      });
    };
    window.addEventListener('ecm:before-route-change', handler);
    return () => window.removeEventListener('ecm:before-route-change', handler);
  }, [isEditMode, stagedOperationCount, rawExitEditMode, replacePendingRouteChange]);

  // Signing out unmounts the app and takes the in-memory Edit Mode ledger with
  // it, so it discards staged work exactly as navigating away does — but it is
  // an SPA state transition, so `beforeunload` never fires and no route guard
  // can see it (bead epic enhancedchannelmanager-r93hq). UserMenu hands the
  // sign-out here instead of running it, and gets it back only once the
  // operator has answered.
  const requestSignOut = useCallback((proceed: () => void | Promise<void>) => {
    if (getGuardedSignOutDecision(isEditMode, stagedOperationCount) === 'sign-out') {
      void proceed();
      return;
    }
    // A sign-out supersedes any deferred navigation: there will be no app left
    // to navigate. Cancelling it rather than dropping it keeps the requester's
    // handoff state from being stranded.
    replacePendingRouteChange(null);
    pendingSignOutRef.current = proceed;
    setShowExitDialog(true);
  }, [isEditMode, stagedOperationCount, replacePendingRouteChange]);

  // Listen for task editor navigation events from NotificationCenter.
  //
  // Routed through the guard, so an operator with staged channel edits is asked
  // before the work is abandoned (bead enhancedchannelmanager-6fi7p). The guard
  // can DEFER this navigation, and NotificationCenter has already written its
  // sessionStorage handoff by the time the event arrives — so if the operator
  // keeps editing, that entry has to go with the cancelled request. Left behind,
  // it redirects their next visit to ANY Settings page to Scheduled Tasks,
  // because SettingsTab reads the key in a mount-only effect.
  useEffect(() => {
    const handler = () => {
      handleRouteChange('settings', 'scheduled-tasks', {
        onCancel: () => sessionStorage.removeItem(OPEN_TASK_EDITOR_STORAGE_KEY),
      });
    };
    window.addEventListener(OPEN_TASK_EDITOR_EVENT, handler);
    return () => window.removeEventListener(OPEN_TASK_EDITOR_EVENT, handler);
  }, [handleRouteChange]);

  // Listen for "Clean up empty groups" navigation from ChannelsPane's
  // Channel List Filters panel (bead 09x38.15 item 3) — links to Settings →
  // Maintenance → Orphaned Channel Groups rather than embedding the tool.
  useEffect(() => {
    const handler = () => {
      handleRouteChange('settings', 'maintenance');
    };
    window.addEventListener(NAVIGATE_TO_ORPHANED_GROUPS_EVENT, handler);
    return () => window.removeEventListener(NAVIGATE_TO_ORPHANED_GROUPS_EVENT, handler);
  }, [handleRouteChange]);

  // Check settings and load initial data
  useEffect(() => {
    const init = async () => {
      logger.info('Initializing Enhanced Channel Manager', { version: packageJson.version });

      try {
        const settings = await api.getSettings();
        logger.info('Settings loaded', { configured: settings.configured, theme: settings.theme });

        // Mirror the operator telemetry toggle onto the reporter so a
        // flip of settings.telemetry_client_errors_enabled takes effect
        // on the next runtime error without a page reload. Defaults to
        // true when the field is absent (older backend schema).
        setTelemetryRuntimeEnabled(settings.telemetry_client_errors_enabled ?? true);

        setAutoRenameChannelNumber(settings.auto_rename_channel_number);
        setDispatcharrUrl(settings.url);
        setShowStreamUrls(settings.show_stream_urls);
        setStrikeThreshold(settings.strike_threshold ?? 3);
        setHideUngroupedStreams(settings.hide_ungrouped_streams);
        setHideEpgUrls(settings.hide_epg_urls ?? false);
        setHideM3uUrls(settings.hide_m3u_urls ?? false);
        setGracenoteConflictMode(settings.gracenote_conflict_mode || 'ask');
        setEpgAutoMatchThreshold(settings.epg_auto_match_threshold ?? 80);
        setDedupM3uToastSuppressed(settings.dedup_m3u_toast_suppressed ?? false);
        setAllowMultiProviderAutoSync(settings.allow_multi_provider_auto_sync ?? false);
        setNormalizeOnChannelCreate(settings.normalize_on_channel_create ?? false);
        // Store VLC settings globally for vlc utility to access
        const vlcBehavior = (settings.vlc_open_behavior as 'protocol_only' | 'm3u_fallback' | 'm3u_only') || 'm3u_fallback';
        window.__vlcSettings = { behavior: vlcBehavior };
        setChannelDefaults({
          includeChannelNumberInName: settings.include_channel_number_in_name,
          channelNumberSeparator: settings.channel_number_separator,
          removeCountryPrefix: settings.remove_country_prefix,
          includeCountryInName: settings.include_country_in_name,
          countrySeparator: settings.country_separator,
          timezonePreference: settings.timezone_preference,
          defaultChannelProfileIds: settings.default_channel_profile_ids,
          customNetworkPrefixes: settings.custom_network_prefixes ?? [],
          ...(() => {
            const merged = mergeSortCriteria(settings.stream_sort_priority, settings.stream_sort_enabled);
            return { streamSortPriority: merged.priority, streamSortEnabled: merged.enabled };
          })(),
          deprioritizeFailedStreams: settings.deprioritize_failed_streams ?? true,
          deprioritizeBlackScreen: settings.deprioritize_black_screen ?? true,
          deprioritizeLowFps: settings.deprioritize_low_fps ?? true,
          m3uAccountPriorities: settings.m3u_account_priorities ?? {},
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

        // Apply global date-format preference (bd-8j47e) so all date
        // displays share one locale instead of varying per browser.
        setDateFormatLocale(settings.date_format);

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
          .then(healthData => {
            setHealth(healthData);
            setHealthSourceState({ state: 'success', hasSnapshot: true });
            logger.info('Health check passed', healthData);
            // No update check here any more (bead nhkd4). It used to run in the
            // browser to drive the header pill; it now runs server-side
            // (backend/services/version_check.py) and reconciles a single
            // notification-centre entry, so every open tab no longer races to
            // ask GitHub the same question.
          })
          .catch((err) => {
            setHealthSourceState((current) => ({ ...current, state: classifySourceLoadError(err) }));
            setError(err.message);
            logger.error('Health check failed', err);
          });

        loadChannelGroups();
        loadChannels();
        loadProviders();
        loadProviderGroupSettings();
        loadStreamGroups();
        loadStreamInventoryTotal();
        // NOTE: Streams are loaded lazily when user interacts with the streams pane
        // This prevents loading 27,000+ streams on app startup which causes high CPU
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
      setDedupM3uToastSuppressed(settings.dedup_m3u_toast_suppressed ?? false);
      setAllowMultiProviderAutoSync(settings.allow_multi_provider_auto_sync ?? false);
      setChannelDefaults({
        includeChannelNumberInName: settings.include_channel_number_in_name,
        channelNumberSeparator: settings.channel_number_separator,
        removeCountryPrefix: settings.remove_country_prefix,
        includeCountryInName: settings.include_country_in_name,
        countrySeparator: settings.country_separator,
        timezonePreference: settings.timezone_preference,
        defaultChannelProfileIds: settings.default_channel_profile_ids,
        customNetworkPrefixes: settings.custom_network_prefixes ?? [],
        ...(() => {
          const merged = mergeSortCriteria(settings.stream_sort_priority, settings.stream_sort_enabled);
          return { streamSortPriority: merged.priority, streamSortEnabled: merged.enabled };
        })(),
        deprioritizeFailedStreams: settings.deprioritize_failed_streams ?? true,
        deprioritizeBlackScreen: settings.deprioritize_black_screen ?? true,
        deprioritizeLowFps: settings.deprioritize_low_fps ?? true,
        m3uAccountPriorities: settings.m3u_account_priorities ?? {},
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
      .then((healthData) => {
        setHealth(healthData);
        setHealthSourceState({ state: 'success', hasSnapshot: true });
      })
      .catch((err) => {
        setHealthSourceState((current) => ({ ...current, state: classifySourceLoadError(err) }));
        setError(err.message);
      });
    loadChannelGroups();
    loadChannels();
    loadProviders();
    loadProviderGroupSettings();
    loadStreamGroups();
    loadStreamInventoryTotal();
    // Only reset streams if they were already loaded (lazy loading preservation)
    if (streamsExplicitlyRequested.current) {
      resetStreams(false);
    }
    loadLogos();
    loadStreamProfiles();
    loadChannelProfiles();
    loadEpgSources();
    loadEpgData();
  };

  const loadChannelGroups = async () => {
    setChannelSourceStates((current) => ({
      ...current,
      groups: { ...current.groups, state: 'loading' },
    }));
    try {
      const groups = await api.getChannelGroups();
      setChannelGroups(groups);
      setChannelSourceStates((current) => ({
        ...current,
        groups: { state: 'success', hasSnapshot: true },
      }));
    } catch (err) {
      logger.error('Failed to load channel groups:', err);
      setChannelSourceStates((current) => ({
        ...current,
        groups: { ...current.groups, state: classifySourceLoadError(err) },
      }));
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

  // Wrapper functions to persist stream filters to localStorage (also triggers lazy stream loading)
  const updateSelectedProviderFilters = useCallback((providerIds: number[]) => {
    streamsExplicitlyRequested.current = true;
    setStreamFilters(prev => ({ ...prev, selectedProviders: providerIds }));
    localStorage.setItem('streamProviderFilters', JSON.stringify(providerIds));
    // Reload stream groups filtered by provider (if exactly one selected)
    // When 0 or multiple providers selected, load all groups
    const m3uAccountId = providerIds.length === 1 ? providerIds[0] : null;
    loadStreamGroups(m3uAccountId);
  }, []);

  const updateSelectedStreamGroupFilters = useCallback((groups: string[]) => {
    streamsExplicitlyRequested.current = true;
    setStreamFilters(prev => ({ ...prev, selectedGroups: groups }));
    localStorage.setItem('streamGroupFilters', JSON.stringify(groups));
  }, []);

  const clearStreamFilters = useCallback(() => {
    streamsExplicitlyRequested.current = true;
    setStreamFilters(prev => ({ ...prev, selectedProviders: [], selectedGroups: [] }));
    localStorage.removeItem('streamProviderFilters');
    localStorage.removeItem('streamGroupFilters');
    // Reload all stream groups (no provider filter)
    loadStreamGroups(null);
  }, []);

  const trackNewlyCreatedGroup = useCallback((groupId: number) => {
    setNewlyCreatedGroupIds((prev) => new Set([...prev, groupId]));
  }, []);

  const loadChannels = async (signal?: AbortSignal) => {
    const isUnfilteredInventory = channelFilters.search === '';
    if (isUnfilteredInventory) {
      setChannelInventoryState((current) => ({ ...current, state: 'loading' }));
    }
    setLoadingStates(prev => ({ ...prev, channels: true }));
    setChannelSourceStates((current) => ({
      ...current,
      channels: { ...current.channels, state: 'loading' },
    }));
    try {
      // Fetch all pages of channels
      const allChannels: Channel[] = [];
      let responseTotal = 0;
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
        if (page === 1) responseTotal = response.count;
        hasMore = response.next !== null;
        page++;
      }

      setChannels(allChannels);
      if (isUnfilteredInventory) {
        setChannelInventoryTotal(responseTotal);
        setChannelInventoryState({ state: 'success', hasSnapshot: true });
      }
      setChannelSourceStates((current) => ({
        ...current,
        channels: { state: 'success', hasSnapshot: true },
      }));
    } catch (err) {
      // Don't log errors for aborted requests
      if (err instanceof Error && err.name !== 'AbortError') {
        logger.error('Failed to load channels:', err);
        setChannelSourceStates((current) => ({
          ...current,
          channels: { ...current.channels, state: classifySourceLoadError(err) },
        }));
        if (isUnfilteredInventory) {
          setChannelInventoryState((current) => ({ ...current, state: classifySourceLoadError(err) }));
        }
      }
    } finally {
      setLoadingStates(prev => ({ ...prev, channels: false }));
    }
  };

  const loadChannelInventoryTotal = async () => {
    setChannelInventoryState((current) => ({ ...current, state: 'loading' }));
    try {
      const response = await api.getChannels({ page: 1, pageSize: 1 });
      setChannelInventoryTotal(response.count);
      setChannelInventoryState({ state: 'success', hasSnapshot: true });
    } catch (err) {
      setChannelInventoryState((current) => ({ ...current, state: classifySourceLoadError(err) }));
    }
  };

  // Handle CSV import completion - refreshes data and adds new groups to filter
  const handleCSVImportComplete = useCallback(async () => {
    try {
      // Get current group IDs before refresh
      const currentGroupIds = new Set(channelGroups.map(g => g.id));

      // Fetch fresh data
      const [newGroups] = await Promise.all([
        api.getChannelGroups(),
        loadChannels(),
      ]);

      // Update groups state
      setChannelGroups(newGroups);

      // Find newly created groups and add them to filter
      const newGroupIds = newGroups
        .filter(g => !currentGroupIds.has(g.id))
        .map(g => g.id);

      if (newGroupIds.length > 0) {
        logger.debug('[App] Adding new groups from CSV import to filter:', newGroupIds);
        setChannelFilters(prev => ({
          ...prev,
          groupFilter: [...prev.groupFilter, ...newGroupIds],
        }));
      }
    } catch (err) {
      logger.error('Failed to refresh after CSV import:', err);
    }
  }, [channelGroups]);

  const loadProviders = async () => {
    setProviderSourceState((current) => ({ ...current, state: 'loading' }));
    try {
      const accounts = await api.getM3UAccounts();
      setProviders(accounts);
      setProviderSourceState({ state: 'success', hasSnapshot: true });
    } catch (err) {
      logger.error('Failed to load providers:', err);
      setProviderSourceState((current) => ({ ...current, state: classifySourceLoadError(err) }));
    }
  };

  const loadStreamGroups = async (m3uAccountId?: number | null) => {
    streamRetryOperations.current.metadata = () => loadStreamGroups(m3uAccountId);
    setStreamSourceStates((current) => ({
      ...current,
      metadata: { ...current.metadata, state: 'loading' },
    }));
    try {
      const groups = await api.getStreamGroups(false, m3uAccountId);
      setStreamGroups(groups);
      setStreamSourceStates((current) => ({
        ...current,
        metadata: { state: 'success', hasSnapshot: true },
      }));
    } catch (err) {
      logger.error('Failed to load stream groups:', err);
      setStreamSourceStates((current) => ({
        ...current,
        metadata: { ...current.metadata, state: classifySourceLoadError(err) },
      }));
    }
  };

  const loadStreamInventoryTotal = async () => {
    setStreamInventoryState((current) => ({ ...current, state: 'loading' }));
    try {
      const response = await api.getStreams({ page: 1, pageSize: 1 });
      setStreamInventoryTotal(response.count);
      setStreamInventoryState({ state: 'success', hasSnapshot: true });
    } catch (err) {
      setStreamInventoryState((current) => ({ ...current, state: classifySourceLoadError(err) }));
    }
  };

  const loadLogos = async () => {
    // Pagination + per-page diagnostics live in api.getAllLogos (bd-nh50y).
    // The helper logs INFO on start, DEBUG per page, INFO on completion, and
    // ERROR with the page number + partial-results length on failure — see
    // services/api.ts. We deliberately keep the catch here so a failure does
    // not blank the existing logos state; the helper already logged the
    // error with full context.
    try {
      const allLogos = await api.getAllLogos(500);
      setLogos(allLogos);
    } catch (err) {
      logger.error('Failed to load logos:', err);
    }
  };

  // `logos` is the catalogue the Edit Channel picker renders, and Logo Manager
  // can add to or delete from it without this component ever hearing about it
  // (bead enhancedchannelmanager-5z7c9, instance 2). Refetch when it does.
  useServerDataInvalidation('logos', loadLogos);

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

  // `epgData` is loaded once at init, so an EPG source added afterwards left the
  // Edit Channel picker reporting "No EPG data found" for guide rows that
  // demonstrably existed, until a full reload (bead
  // enhancedchannelmanager-3vtim). EPG Manager publishes when a source finishes
  // downloading; this is the refetch.
  useServerDataInvalidation('epg-data', loadEpgData);

  // Same class for the channel-group list: a DBAS restore creates and renames
  // groups from the Settings tab, which this component's copy — the one the
  // Channel Manager group filter renders — cannot see.
  useServerDataInvalidation('channel-groups', loadChannelGroups);

  // And for the channels those groups hold. Refreshing only the filter left an
  // operator looking at "CHANNELS 0" straight after a restore that created 12
  // (bead enhancedchannelmanager-eelgi). Skipped while Edit Mode is active: a
  // refetch mid-session would fight the working copy, and a restore is not
  // something an operator runs from inside an unsaved edit session.
  useServerDataInvalidation('channels', () => {
    if (isEditMode) return;
    void loadChannels();
  });

  // Lightweight reset: clear streams and refresh group metadata.
  // Actual stream data loads per-group on demand via loadStreamGroup().
  const resetStreams = async (_bypassCache: boolean = false) => {
    setLoadingStates(prev => ({ ...prev, streams: true }));
    setStreamSourceStates((current) => ({ metadata: current.metadata }));
    try {
      // Keep the last successful rows until replacement data settles. This
      // makes transient metadata failures explicitly stale instead of blank.
      loadedStreamGroupsRef.current.clear();

      // Refresh stream group metadata (lightweight — just names + counts)
      const m3uAccountId = streamFilters.selectedProviders?.length === 1
        ? streamFilters.selectedProviders[0] : null;
      await loadStreamGroups(m3uAccountId);
    } catch (err) {
      if (err instanceof Error && err.name !== 'AbortError') {
        logger.error('Failed to reset streams:', err);
      }
    } finally {
      setLoadingStates(prev => ({ ...prev, streams: false }));
    }
  };

  // Search streams: fetch just the first page of server-filtered results
  const searchStreams = async (
    signal?: AbortSignal,
    query = {
      search: streamFilters.search,
      providerFilter: streamFilters.providerFilter,
      groupFilter: streamFilters.groupFilter,
    },
  ) => {
    const sourceKey = 'search';
    streamRetryOperations.current[sourceKey] = () => searchStreams(undefined, query);
    setLoadingStates(prev => ({ ...prev, streams: true }));
    setStreamSourceStates((current) => ({
      ...current,
      [sourceKey]: {
        ...(current[sourceKey] ?? { hasSnapshot: streamsSnapshotRef.current.length > 0 }),
        state: 'loading',
      },
    }));
    try {
      const response = await api.getStreams({
        page: 1,
        pageSize: 500,
        search: query.search || undefined,
        m3uAccount: query.providerFilter ?? undefined,
        channelGroup: query.groupFilter ?? undefined,
        signal,
      });
      streamsSnapshotRef.current = response.results;
      setStreams(response.results);
      setStreamMatchingTotal(response.count);
      loadedStreamGroupsRef.current.clear();
      rememberSeenStreams(response.results);
      setStreamSourceStates((current) => ({
        ...current,
        [sourceKey]: { state: 'success', hasSnapshot: true },
      }));
    } catch (err) {
      if (err instanceof Error && err.name !== 'AbortError') {
        logger.error('Failed to search streams:', err);
        setStreamSourceStates((current) => ({
          ...current,
          [sourceKey]: {
            ...(current[sourceKey] ?? { hasSnapshot: false }),
            state: classifySourceLoadError(err),
          },
        }));
      }
    } finally {
      setLoadingStates(prev => ({ ...prev, streams: false }));
    }
  };

  // Force refresh streams from Dispatcharr (bypassing cache)
  const refreshStreams = useCallback(() => {
    streamsExplicitlyRequested.current = true;
    resetStreams(true);
  }, [streamFilters.selectedProviders]);

  // Request streams to be loaded (lazy loading trigger)
  // Call this when user interacts with streams (e.g., expands streams pane, searches)
  // Just sets the flag — actual streams load per-group via loadStreamGroup()
  const requestStreamsLoad = useCallback(() => {
    if (!streamsExplicitlyRequested.current) {
      streamsExplicitlyRequested.current = true;
      // Refresh group metadata so the streams pane shows available groups
      const m3uAccountId = streamFilters.selectedProviders?.length === 1
        ? streamFilters.selectedProviders[0] : null;
      loadStreamGroups(m3uAccountId);
    }
  }, [streamFilters.selectedProviders]);

  // Load streams for a single group (per-group lazy loading)
  // This allows loading only the streams for an expanded group instead of all streams
  // When search is active, loads only matching streams for that group
  const loadStreamGroup = useCallback(async (
    groupName: string,
    force = false,
    search = streamFilters.search,
    provider = streamFilters.selectedProviders.length === 1
      ? streamFilters.selectedProviders[0]
      : streamFilters.providerFilter,
  ) => {
    // Skip if this group's streams are already loaded
    if (!force && loadedStreamGroupsRef.current.has(groupName)) {
      return;
    }

    const sourceKey = `group:${groupName}`;
    const query = { groupName, search, provider };
    streamRetryOperations.current[sourceKey] = () => loadStreamGroup(
      query.groupName,
      true,
      query.search,
      query.provider,
    );
    setStreamSourceStates((current) => ({
      ...current,
      [sourceKey]: {
        ...(current[sourceKey] ?? { hasSnapshot: streamsSnapshotRef.current.length > 0 }),
        state: 'loading',
      },
    }));

    // Mark as loaded immediately to prevent duplicate requests
    loadedStreamGroupsRef.current.add(groupName);

    try {
      // Fetch streams for this specific group (with search filter if active)
      const allGroupStreams: Stream[] = [];
      let page = 1;
      let hasMore = true;

      while (hasMore) {
        const response = await api.getStreams({
          page,
          pageSize: 500,
          channelGroup: groupName,
          search: query.search || undefined,
          m3uAccount: query.provider ?? undefined,
        });
        allGroupStreams.push(...response.results);
        hasMore = response.next !== null;
        page++;
      }

      // Merge with existing streams (avoid duplicates by stream ID)
      setStreams(prevStreams => {
        const existingIds = new Set(prevStreams.map(s => s.id));
        const newStreams = allGroupStreams.filter(s => !existingIds.has(s.id));
        const nextStreams = [...prevStreams, ...newStreams];
        streamsSnapshotRef.current = nextStreams;
        return nextStreams;
      });
      rememberSeenStreams(allGroupStreams);
      setStreamSourceStates((current) => ({
        ...current,
        [sourceKey]: { state: 'success', hasSnapshot: true },
      }));
    } catch (err) {
      // Remove from loaded set on error so user can retry
      loadedStreamGroupsRef.current.delete(groupName);
      if (err instanceof Error && err.name !== 'AbortError') {
        logger.error(`Failed to load streams for group ${groupName}:`, err);
        setStreamSourceStates((current) => ({
          ...current,
          [sourceKey]: {
            ...(current[sourceKey] ?? { hasSnapshot: false }),
            state: classifySourceLoadError(err),
          },
        }));
      }
    }
  }, [
    streamFilters.search,
    streamFilters.providerFilter,
    streamFilters.selectedProviders,
    rememberSeenStreams,
  ]);

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

  // Refresh channels/groups when the channel pipeline modifies them
  // Uses api.* directly and channelGroupsRef to avoid stale closures (empty [] deps)
  useEffect(() => {
    const handleChannelsChanged = async () => {
      try {
        // Snapshot current group IDs before refresh
        const currentGroupIds = new Set(channelGroupsRef.current.map(g => g.id));

        // Fetch fresh channels (all pages) and groups in parallel
        const groupsPromise = api.getChannelGroups();
        const allChannels: Channel[] = [];
        let page = 1;
        let hasMore = true;
        while (hasMore) {
          const response = await api.getChannels({ page, pageSize: 500 });
          allChannels.push(...response.results);
          hasMore = response.next !== null;
          page++;
        }
        setChannels(allChannels);

        const newGroups = await groupsPromise;
        setChannelGroups(newGroups);

        // Add newly created groups to filter so they're immediately visible
        const newGroupIds = newGroups
          .filter(g => !currentGroupIds.has(g.id))
          .map(g => g.id);
        if (newGroupIds.length > 0) {
          setChannelFilters(prev => ({
            ...prev,
            groupFilter: [...prev.groupFilter, ...newGroupIds],
          }));
        }
      } catch (err) {
        if (err instanceof Error && err.name !== 'AbortError') {
          logger.error('Failed to refresh after channel change:', err);
        }
      }
    };
    window.addEventListener('channels-changed', handleChannelsChanged);
    return () => window.removeEventListener('channels-changed', handleChannelsChanged);
  }, []);

  // Reload streams when filters change - but only if explicitly requested OR searching
  // Search: fetch first page of server-filtered results
  // Other filters: just clear and let lazy group loading handle it
  useEffect(() => {
    const hasSearchFilter = streamFilters.search?.trim();

    // Skip loading on initial mount - streams are loaded lazily when user interacts
    // BUT if there's a search term, we should load (server will filter)
    if (!streamsExplicitlyRequested.current && !hasSearchFilter) {
      setLoadingStates(prev => ({ ...prev, streams: false }));
      return;
    }

    // Mark as explicitly requested if searching (so future loads work without search)
    if (hasSearchFilter) {
      streamsExplicitlyRequested.current = true;
    }

    const abortController = new AbortController();
    const timer = setTimeout(() => {
      if (hasSearchFilter) {
        // Search: fetch first page of server-filtered results
        searchStreams(abortController.signal);
      } else {
        // Filter change without search: just reset and let lazy loading handle it
        resetStreams(false);
      }
    }, 500); // Debounce: 500ms for less frequent API requests
    return () => {
      clearTimeout(timer);
      abortController.abort();
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
          // ALWAYS add assignment even if profileIds is empty - this triggers disable on all profiles
          const profilesToAssign = profileIds && profileIds.length > 0
            ? profileIds
            : defaultChannelProfileIds;

          if (channelNumber !== undefined) {
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
              logger.warn(`Failed to add channel ${newChannel.id} to profile ${profileId}:`, err);
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

  /**
   * Stage the channel-number moves a push-down plan calls for.
   *
   * Highest number first, so no staged update lands on a number a later update
   * in the same batch is still about to vacate.
   *
   * Both push-down call sites share this: creating channels from streams and
   * inserting a single manual channel make the operator the same promise, and
   * the second one had no implementation at all until bead
   * `enhancedchannelmanager-fprsq`. Writing it twice is how the two would drift.
   */
  const stagePushDownShifts = useCallback(
    (shifts: readonly PlannedChannelShift<Channel>[]) => {
      for (let i = shifts.length - 1; i >= 0; i--) {
        const { channel: ch, toNumber: newNum } = shifts[i];

        // Apply auto-rename if enabled
        const newName = autoRenameChannelNumber
          ? computeAutoRename(ch.name, ch.channel_number, newNum)
          : undefined;

        if (newName) {
          stageUpdateChannel(
            ch.id,
            { channel_number: newNum, name: newName },
            `Shifted "${ch.name}" to "${newName}" (channel ${ch.channel_number} → ${newNum})`
          );
        } else {
          stageUpdateChannel(
            ch.id,
            { channel_number: newNum },
            `Shifted channel ${ch.channel_number} to ${newNum} to make room`
          );
        }
      }
    },
    [autoRenameChannelNumber, stageUpdateChannel]
  );

  // Create channel for manual entry mode (from StreamsPane bulk create modal)
  // This supports creating a new group if newGroupName is provided.
  //
  // `pushDownOnConflict` is the manual-entry half of the same promise the bulk
  // path makes: inserting at an occupied number moves whatever is already there
  // rather than creating a duplicate. It used to be unreachable here, because
  // the manual path never reached the conflict dialog at all
  // (bead enhancedchannelmanager-fprsq).
  //
  // `name` arrives FINAL. StreamsPane has already put the operator's typed
  // name through `resolveCreateChannelNames`, which is the one place the
  // "Normalization Rules" question is answered, so neither branch below may
  // normalize again and neither sets the API's `normalize` flag — doing either
  // would normalize an already-normalized name a second time
  // (bead enhancedchannelmanager-e9e5o).
  const handleCreateChannelManual = useCallback(
    async (
      name: string,
      channelNumber?: number,
      groupId?: number,
      newGroupName?: string,
      pushDownOnConflict?: boolean,
    ) => {
      try {
        if (!isEditMode && pushDownOnConflict) {
          // A push-down works by STAGING shifts, which only edit mode has. The
          // manual-create button is rendered inside ChannelsPane's `isEditMode`
          // block, so nothing can reach this; refusing loudly is what keeps it
          // that way, rather than creating the channel on top of the occupied
          // number and reporting success. Mirrors the edit-mode refusal
          // `handleBulkCreateFromGroup` already makes.
          setError('Pushing existing channels down requires edit mode');
          return;
        }
        if (isEditMode) {
          // Make room BEFORE staging the creation, using the same planner and
          // the same highest-number-first ordering as the bulk path, so no
          // staged update lands on a number a later update is still about to
          // vacate. One channel, so the plan claims exactly one number; the
          // step follows the number the operator typed, which is what puts a
          // `38.1` insert on the tenths grid instead of the whole-number one.
          const shifts =
            pushDownOnConflict && channelNumber !== undefined
              ? planChannelNumberShift({
                  channels: displayChannels,
                  startingNumber: channelNumber,
                  count: 1,
                  step: channelNumber % 1 !== 0 ? 0.1 : 1,
                }).shifts
              : [];

          if (shifts.length > 0) {
            startBatch(`Insert channel "${name}" at ${channelNumber}`);
            stagePushDownShifts(shifts);
          }

          // In edit mode, stage the creation
          // stageCreateChannel handles newGroupName internally
          const tempId = stageCreateChannel(
            name,
            channelNumber,
            groupId,
            newGroupName,  // Pass newGroupName to stage creation
            undefined,     // logoId
            undefined,     // logoUrl
            undefined,     // tvgId
            undefined      // tvcGuideStationId
          );

          // Track profile assignments (use default profiles for manual creation)
          if (channelNumber !== undefined && defaultChannelProfileIds.length > 0) {
            pendingProfileAssignmentsRef.current.push({
              startNumber: channelNumber,
              count: 1,
              profileIds: defaultChannelProfileIds,
              increment: 1,
            });
          }

          // Track the newly created group so it appears in the filter
          if (newGroupName) {
            trackNewlyCreatedGroup(tempId);  // tempId acts as marker for new group
          }

          if (shifts.length > 0) {
            endBatch();
          }

          // Refresh UI
          await loadChannels();
        } else {
          // Non-edit mode - create immediately
          let targetGroupId = groupId ?? null;

          // Create new group if needed
          if (newGroupName && !targetGroupId) {
            const newGroup = await api.createChannelGroup(newGroupName);
            targetGroupId = newGroup.id;
            await loadChannelGroups();
          }

          // Create the channel
          await api.createChannel({
            name,
            channel_number: channelNumber,
            channel_group_id: targetGroupId ?? undefined,
          });

          await loadChannels();
        }
      } catch (err) {
        logger.error('Failed to create channel:', err);
        throw err;
      }
    },
    [
      isEditMode,
      stageCreateChannel,
      stagePushDownShifts,
      startBatch,
      endBatch,
      displayChannels,
      defaultChannelProfileIds,
      loadChannels,
      loadChannelGroups,
      trackNewlyCreatedGroup,
    ]
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

  // How many existing channels a "Push channels down" would renumber. The
  // conflict count above only covers the numbers the new channels claim, which
  // understates the blast radius whenever the insert has to ripple further up
  // (bead enhancedchannelmanager-i85dg).
  const handleCountPushDownShift = useCallback((startingNumber: number, count: number): number => {
    return planChannelNumberShift({
      channels: displayChannels,
      startingNumber,
      count,
      step: startingNumber % 1 !== 0 ? 0.1 : 1,
    }).shifts.length;
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
      // `| undefined` rather than `?` so `nameResolution` below can be
      // REQUIRED; see `StreamsPaneProps.onBulkCreateFromGroup`.
      newGroupName: string | undefined,
      timezonePreference: api.TimezonePreference | undefined,
      _stripCountryPrefix: boolean | undefined,
      addChannelNumber: boolean | undefined,
      numberSeparator: api.NumberSeparator | undefined,
      keepCountryPrefix: boolean | undefined,
      countrySeparator: api.NumberSeparator | undefined,
      prefixOrder: api.PrefixOrder | undefined,
      _stripNetworkPrefix: boolean | undefined,
      _customNetworkPrefixes: string[] | undefined,
      _stripNetworkSuffix: boolean | undefined,
      _customNetworkSuffixes: string[] | undefined,
      profileIds: number[] | undefined,
      pushDownOnConflict: boolean | undefined,
      nameResolution: api.ResolvedCreateChannelNames
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
        // Filter streams by timezone preference
        const filteredStreams = api.filterStreamsByTimezone(streamsToCreate, timezonePreference ?? 'both');

        // The names these channels will be created with, as ALREADY RESOLVED
        // by the dialog (bead enhancedchannelmanager-e9e5o).
        //
        // This used to receive the operator's toggle and call
        // `resolveCreateChannelNames` itself, which made two independent
        // answers to one question: the dialog resolved for its preview and
        // count, this resolved again at submit, and the two were free to
        // disagree — by timing, by the rules changing in between, or because
        // the dialog grouped its answer differently. Consuming the resolution
        // the operator was SHOWN is what removes that gap. The names are
        // FINAL: nothing here or downstream may normalize them again.
        //
        // The resolution is REQUIRED and it is asked whether it covers these
        // streams before anything is staged. It used to be optional, defaulted
        // to an empty map, and read per stream with `|| stream.name` — so a
        // caller that forgot it, or a resolution built from a different set of
        // streams, silently created every channel under its raw provider name.
        // Refusing here is atomic: no batch has been started, so nothing is
        // half-staged (bead enhancedchannelmanager-e9e5o, fix round 4).
        const normalizationFailed = nameResolution.normalizationFailed;
        const unresolved = filteredStreams.filter((s) => !nameResolution.has(s.name));
        if (unresolved.length > 0) {
          logger.error('[BulkCreate] Unresolved stream names, refusing to create', unresolved.map((s) => s.name));
          setError(
            `Cannot create channels: ${unresolved.length} stream name(s) were not resolved, ` +
            'so the names on screen are not the names that would be created. Close the dialog and try again.'
          );
          return;
        }

        // Group streams by normalized base name (also stripping quality suffixes to merge variants)
        // The grouping key is the normalized name with quality suffixes stripped
        // The channel name will be the normalized name (without quality stripping)
        const streamsByBaseName = new Map<string, { normalizedName: string; streams: Stream[] }>();
        for (const stream of filteredStreams) {
          // Total by construction: the coverage refusal above already
          // established that the resolution answers for every one of these.
          const normalizedName = nameResolution.nameFor(stream.name);
          // Strip quality suffixes for grouping (so HD/FHD/4K/SD variants merge together)
          const groupingKey = api.stripQualitySuffixes(normalizedName);
          const existing = streamsByBaseName.get(groupingKey);
          if (existing) {
            existing.streams.push(stream);
          } else {
            streamsByBaseName.set(groupingKey, { normalizedName, streams: [stream] });
          }
        }

        const mergedCount = filteredStreams.length - streamsByBaseName.size;
        const channelCount = streamsByBaseName.size;

        // Fetch M3U metadata to get tvc-guide-stationid (Gracenote ID) for streams
        // This data isn't exposed by Dispatcharr's API, so we parse the M3U file directly
        const m3uMetadataMap = new Map<string, string>(); // tvg_id -> tvc-guide-stationid
        try {
          // Get unique M3U account IDs from the streams
          const m3uAccountIds = new Set<number>();
          for (const stream of filteredStreams) {
            if (stream.m3u_account !== null) {
              m3uAccountIds.add(stream.m3u_account);
            }
          }

          // Fetch metadata for each M3U account
          logger.debug(`Fetching M3U metadata for ${m3uAccountIds.size} account(s): ${Array.from(m3uAccountIds).join(', ')}`);
          const metadataPromises = Array.from(m3uAccountIds).map(async (accountId) => {
            try {
              const response = await api.getM3UStreamMetadata(accountId);
              logger.debug(`M3U metadata for account ${accountId}: ${response.count} entries`);
              return response.metadata;
            } catch (err) {
              logger.warn(`Failed to fetch M3U metadata for account ${accountId}:`, err);
              return null;
            }
          });

          const metadataResults = await Promise.all(metadataPromises);

          // Build combined map of tvg_id -> tvc-guide-stationid
          for (const metadata of metadataResults) {
            if (metadata) {
              for (const [tvgId, entry] of Object.entries(metadata)) {
                if (entry['tvc-guide-stationid']) {
                  m3uMetadataMap.set(tvgId, entry['tvc-guide-stationid']);
                }
              }
            }
          }

          if (m3uMetadataMap.size > 0) {
            logger.debug(`Loaded ${m3uMetadataMap.size} Gracenote ID mappings from M3U metadata`);
          } else {
            logger.debug('No Gracenote ID mappings found in M3U metadata');
          }
        } catch (err) {
          logger.warn('Failed to fetch M3U metadata for Gracenote IDs:', err);
          // Continue without Gracenote IDs - not a critical error
        }

        // Start a batch for all channel operations
        startBatch(`Create ${channelCount} channels from streams`);

        // Only push down channels if explicitly requested via pushDownOnConflict
        if (pushDownOnConflict) {
          // Plan the push-down purely from which channel numbers are occupied
          // in the staged working copy. Group membership decided WHERE the
          // operator is inserting; it plays no part in the arithmetic, because
          // groups are free to contain holes and outliers, to overlap and to
          // interleave. See utils/channelNumberShift.ts for why the group
          // interval model this replaced could not be repaired
          // (bead enhancedchannelmanager-i85dg).
          const shiftPlan = planChannelNumberShift({
            channels: displayChannels,
            startingNumber,
            count: channelCount,
            step: startingNumber % 1 !== 0 ? 0.1 : 1,
          });

          stagePushDownShifts(shiftPlan.shifts);
        }

        // Create channels and assign streams
        // Sort entries alphabetically by normalized name for consistent ordering
        // Use natural sort so "C-SPAN" comes before "C-SPAN 2" which comes before "C-SPAN 3"
        const sortedEntries = Array.from(streamsByBaseName.entries()).sort((a, b) => {
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
        for (const [_groupingKey, { normalizedName, streams: groupedStreams }] of sortedEntries) {
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

          // Find logo URL, tvg_id, and tvc_guide_stationid from the first stream that has them
          let logoUrl: string | undefined;
          let tvgId: string | undefined;
          let tvcGuideStationId: string | undefined;
          for (const stream of groupedStreams) {
            if (!logoUrl && stream.logo_url) {
              logoUrl = stream.logo_url;
            }
            if (!tvgId && stream.tvg_id) {
              tvgId = stream.tvg_id;
            }
            // Extract Gracenote ID from custom_properties (stored as tvc-guide-stationid from M3U)
            if (!tvcGuideStationId && stream.custom_properties) {
              const stationId = stream.custom_properties['tvc-guide-stationid'];
              if (typeof stationId === 'string') {
                tvcGuideStationId = stationId;
              }
            }
            // Stop early if we found all three
            if (logoUrl && tvgId && tvcGuideStationId) break;
          }

          // If we didn't find Gracenote ID from stream but have tvg_id, look up from M3U metadata
          // This gets the data directly from the M3U file since Dispatcharr doesn't expose it via API
          if (!tvcGuideStationId && tvgId && m3uMetadataMap.has(tvgId)) {
            tvcGuideStationId = m3uMetadataMap.get(tvgId);
            logger.debug(`Found Gracenote ID from M3U metadata for tvg_id "${tvgId}": ${tvcGuideStationId}`);
          }

          // Debug: Log what we're passing to stageCreateChannel
          logger.debug(`Creating channel "${channelName}": tvgId=${tvgId}, tvcGuideStationId=${tvcGuideStationId}, m3uMetadataMap.has(tvgId)=${tvgId ? m3uMetadataMap.has(tvgId) : 'N/A'}`);

          // Create the channel (returns temp ID)
          // If targetNewGroupName is set, pass it so the commit logic can create the group first
          // Pass logoUrl - the commit logic will create the logo if needed
          // Pass tvgId and tvcGuideStationId - auto-populate from stream metadata for EPG matching
          // `channelName` is already the final name — resolveCreateChannelNames
          // above answered the normalization toggle, and the number/country
          // prefixes were applied on top of its answer. That is why no
          // normalize flag travels with the staged operation: a backend-side
          // pass would normalize an already-normalized name a second time
          // (bead enhancedchannelmanager-e9e5o).
          const tempChannelId = stageCreateChannel(
            channelName,
            channelNumber,
            targetGroupId ?? undefined,
            targetNewGroupName,
            undefined, // logoId - will be resolved during commit
            logoUrl,
            tvgId,
            tvcGuideStationId
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
        alert(`Staged ${streamsByBaseName.size} channels for creation!${mergeInfo}${groupInfo}\n\nThey will be created in Dispatcharr when you click "Done".`);

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
        // ALWAYS add assignment even if profileIds is empty - this triggers disable on all profiles
        const profileIdsToApply = (profileIds && profileIds.length > 0)
          ? profileIds
          : defaultChannelProfileIds;

        pendingProfileAssignmentsRef.current.push({
          startNumber: startingNumber,
          count: streamsByBaseName.size,
          profileIds: profileIdsToApply,
          increment, // Use the same increment calculated for channel creation
        });

        // The caller owns the operator-facing message: StreamsPane sits inside
        // the notification provider, App renders it (bead
        // enhancedchannelmanager-e9e5o).
        return { normalizationFailed };
      } catch (err) {
        logger.error('Bulk create failed:', err);
        setError('Failed to bulk create channels');
        throw err;
      }
    },
    [isEditMode, stageCreateChannel, stageAddStream, stageUpdateChannel, stagePushDownShifts, startBatch, endBatch, displayChannels, defaultChannelProfileIds]
  );

  // Handle stream group drop on channels pane (triggers bulk create modal in streams pane)
  // Supports multiple groups being dropped at once
  // Now includes optional target group and suggested starting number for positional drops
  const handleStreamGroupDrop = useCallback((groupNames: string[], _streamIds: number[], targetGroupId?: number, suggestedStartingNumber?: number) => {
    // Set the dropped group names - StreamsPane will react to this and open the modal
    setDroppedStreamGroupNames(groupNames);
    setDroppedStreamTargetGroupId(targetGroupId ?? null);
    setDroppedStreamStartingNumber(suggestedStartingNumber ?? null);
  }, []);

  // Dedup-on-drop integration (bd-u6ftw / BD-H, ADR-008 §D1).
  // Wraps the single-stream drop-into-group flow with the BD-D candidates
  // lookup. Multi-stream drops bypass dedup entirely — bulk dedup is a
  // separate epic surface (bd-a5lb2 / bulk M3U dedup hook).
  // In Edit Mode the merge this hook offers stages like every other stream
  // assignment (bead enhancedchannelmanager-kz089); outside it, it writes as
  // before. Passing `undefined` rather than a no-op keeps the hook's own
  // "am I staging?" test a simple presence check.
  const dedupOnDrop = useDedupOnDrop({
    reloadChannels: loadChannels,
    stageAddStream: isEditMode ? stageAddStream : undefined,
  });

  // Handle bulk streams drop on channels pane (triggers bulk create modal for specific streams)
  //
  // Every branch below now NAMES what the duplicate check did (bead
  // enhancedchannelmanager-ok8tj), closing the sibling of the defect commit
  // `941d9087` fixed on the `Create in…` trigger path. Silence covered five
  // different stories here — a candidate was found, nothing was similar
  // enough, the lookup broke, the drop carried more than one stream, or the
  // client could not read the stream's name — and the first was the only one
  // the operator could actually see.
  //
  // The report is RETURNED rather than toasted here: `App` renders
  // `NotificationProvider`, so it is outside its own provider and cannot call
  // `useNotifications()`. `ChannelsPane`, which owns the drop target and knows
  // the group's name, turns the report into the message.
  const handleBulkStreamsDrop = useCallback(async (
    streamIds: number[],
    groupId: number | null,
    startingNumber: number,
  ): Promise<DedupDropReport> => {
    const proceedWithCreate = () => {
      // Set the dropped stream IDs and target info - StreamsPane will react to this and open the modal
      setDroppedStreamIds(streamIds);
      setDroppedStreamTargetGroupId(groupId);
      setDroppedStreamStartingNumber(startingNumber);
    };

    if (streamIds.length !== 1) {
      // Multi-stream drops keep the existing bulk-create flow unchanged —
      // bulk dedup is a separate surface. Say so, rather than leaving the
      // absent merge prompt to be read as "nothing matched".
      proceedWithCreate();
      return { outcome: 'skipped_multi_stream', streamName: null, streamCount: streamIds.length };
    }

    const streamId = streamIds[0];
    const stream = streams.find((s) => s.id === streamId) ?? seenStreams.get(streamId);
    if (!stream) {
      // No stream metadata available — proceed with the existing flow so
      // the drop is never silently dropped on the floor.
      logger.warn('[DEDUP] dropped stream id %s not found in client cache; skipping dedup lookup', streamId);
      proceedWithCreate();
      return { outcome: 'skipped_unknown_stream', streamName: null, streamCount: 1 };
    }

    const outcome = await dedupOnDrop.handleSingleStreamDrop(
      {
        streamId,
        streamName: stream.name,
        targetGroupId: groupId,
      },
      proceedWithCreate,
    );
    return { outcome, streamName: stream.name, streamCount: 1 };
  }, [streams, seenStreams, dedupOnDrop]);

  // Handle open create channel modal (triggers bulk create modal in manual entry mode)
  const handleOpenCreateChannelModal = useCallback(() => {
    setManualEntryTrigger(true);
  }, []);

  // Clear the dropped stream group/streams trigger after it's been handled
  const handleStreamGroupTriggerHandled = useCallback(() => {
    setDroppedStreamGroupNames(null);
    setDroppedStreamTargetGroupId(null);
    setDroppedStreamStartingNumber(null);
    setDroppedStreamIds(null);
    setDroppedStreamTargetGroupId(null);
    setDroppedStreamStartingNumber(null);
    setManualEntryTrigger(false);
  }, []);

  // Filter streams based on multi-select filters (client-side)
  // Note: search term is handled server-side via loadStreams() API call
  const filteredStreams = useMemo(() => {
    let result = streams;

    // Filter by selected providers (multi-select, client-side)
    if (streamFilters.selectedProviders.length > 0) {
      result = result.filter((s) => s.m3u_account !== null && streamFilters.selectedProviders.includes(s.m3u_account));
    }

    // Filter by selected stream groups (multi-select, client-side)
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


  // Merge real channel groups with staged groups when in edit mode
  const displayChannelGroups = isEditMode && stagedGroups.length > 0
    ? [...channelGroups, ...stagedGroups]
    : channelGroups;

  const channelWorkspaceSources: WorkspaceSource[] = [
    {
      key: 'groups',
      label: 'channel groups',
      ...channelSourceStates.groups,
      retry: loadChannelGroups,
    },
    {
      key: 'channels',
      label: 'channels',
      ...channelSourceStates.channels,
      retry: () => loadChannels(),
    },
  ];
  const streamWorkspaceSources: WorkspaceSource[] = Object.entries(streamSourceStates).map(([key, value]) => ({
    key,
    label: key === 'metadata' ? 'stream groups' : key === 'search' ? 'stream search' : key.slice('group:'.length),
    ...value,
    retry: streamRetryOperations.current[key] ?? (() => Promise.resolve()),
  }));
  const workspaceSources = [...channelWorkspaceSources, ...streamWorkspaceSources];
  const workspacePermissionDenied = workspaceSources
    .some((source) => source.state === 'permission');
  const workspaceEditUnavailable = channelWorkspaceSources.some((source) =>
    source.state === 'loading' || (source.state === 'error' && !source.hasSnapshot));

  /**
   * What a staged ledger left behind by a dead session can still be applied
   * against (epic enhancedchannelmanager-r93hq).
   *
   * Planned against the channel and group lists as the server reports them
   * NOW, and only once those lists are actually loaded: `workspaceEditUnavailable`
   * is the same signal that holds Enter Edit Mode disabled, and planning against
   * a half-loaded list would report every operation as stale.
   *
   * Recomputed rather than cached because the operator can leave the offer on
   * screen while a refresh lands underneath it; the plan they act on must be
   * the plan they are reading.
   */
  const restorePlan = useMemo(() => {
    if (pendingRestore === null || workspaceEditUnavailable || workspacePermissionDenied) {
      return null;
    }
    return planLedgerRestore(pendingRestore.operations, {
      channels,
      channelGroups,
      profileIds: channelProfiles.map((profile) => profile.id),
    });
  }, [pendingRestore, workspaceEditUnavailable, workspacePermissionDenied, channels, channelGroups, channelProfiles]);

  const channelManagerPageAction = activeTab === 'channel-manager'
    && !workspacePermissionDenied && (
    isEditMode ? (
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
        <EditModeRestoredBadge restoredFrom={restoredFrom} />
        {editModeEnteredAt !== null && <EditModeTimer enteredAt={editModeEnteredAt} />}
        <div className="edit-mode-buttons">
          <button
            className="edit-mode-done-btn"
            onClick={handleExitEditMode}
            disabled={isCommitting}
            title="Apply changes"
          >
            <span className="material-icons" style={{ fontSize: '16px', marginRight: '4px' }}>check</span>
            Done
            {stagedOperationCount > 0 && <span className="edit-mode-done-count">{stagedOperationCount}</span>}
          </button>
          <button
            className="edit-mode-cancel-btn"
            onClick={() => {
              if (stagedOperationCount > 0) {
                if (confirm(`You have ${stagedOperationCount} pending change${stagedOperationCount !== 1 ? 's' : ''} that will be lost. Are you sure you want to cancel?`)) {
                  discard();
                  setSelectedChannelIds(new Set());
                }
              } else {
                discard();
                setSelectedChannelIds(new Set());
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
        disabled={workspaceEditUnavailable}
        title={workspaceEditUnavailable
          ? 'Edit Mode is unavailable until channel data loads'
          : 'Enter Edit Mode to make changes'}
      >
        <span className="material-icons" style={{ fontSize: '16px', marginRight: '4px' }}>edit</span>
        Edit Mode
      </button>
    )
  );

  // Header service indicator. Replaces the removed footer status line: an API
  // error outranks a stale successful health payload, and any health status the
  // backend does not report as healthy surfaces verbatim rather than as "Online".
  const serviceStatus: { tone: 'online' | 'degraded' | 'offline' | 'pending'; label: string; detail: string } = error
    ? { tone: 'offline', label: 'Offline', detail: `API error: ${error}` }
    : !health
      ? { tone: 'pending', label: 'Connecting', detail: 'Checking ECM service status' }
      : /^(ok|okay|healthy|up|running)$/i.test(health.status ?? '')
        ? { tone: 'online', label: 'Online', detail: `${health.service || 'ECM'} · ${health.status}` }
        : { tone: 'degraded', label: health.status || 'Degraded', detail: `${health.service || 'ECM'} · ${health.status || 'status unavailable'}` };

  // Settings carries a third breadcrumb crumb for the active section, e.g.
  // SYSTEM / SETTINGS / GENERAL SETTINGS, with that section's own descriptive
  // line beneath it. The section's heading block was removed from the content
  // pane, so this header is now its only rendering.
  const routeHeading = activeTab === 'settings'
    ? (() => {
      const section = settingsSectionHeading(settingsPage ?? 'general');
      return {
        group: `${ROUTE_HIERARCHY.settings.group} / ${ROUTE_TITLES.settings.toUpperCase()}`,
        title: section.title.toUpperCase(),
        description: section.description ?? ROUTE_HIERARCHY.settings.purpose,
      };
    })()
    : {
      group: ROUTE_HIERARCHY[activeTab].group,
      title: ROUTE_TITLES[activeTab].toUpperCase(),
      description: ROUTE_HIERARCHY[activeTab].purpose,
    };

  return (
    <NotificationProvider position="top-right">
    <BackupDestinationPromptProvider>
    <div className="app">
      <SkipToMainContent />
      <header className={`header ${isEditMode ? 'edit-mode-active' : ''}`}>
        {/* Reading order (bead 57pp3, amended by nhkd4): the status indicator
            sits left of the action icons, so the row reads "what is running"
            -> the controls that act on it. The "what changed" slot used to be
            an "Update available" pill; the PO moved that signal into the
            notification centre (the bell further along this same row), so the
            upgrade prompt now arrives where every other system message does
            instead of as a second, differently-shaped status chip. */}
        <div className="header-actions">
          <span
            className={`service-status service-status-${serviceStatus.tone}`}
            role="status"
            title={serviceStatus.detail}
          >
            <span className="service-status-dot" aria-hidden="true" />
            <span className="service-status-sr">ECM service status: </span>
            <span className="service-status-label">{serviceStatus.label}</span>
            <span className="service-status-version">v{packageJson.version}</span>
          </span>
          <a
            href="https://github.com/MotWakorb/enhancedchannelmanager/blob/main/USER_GUIDE.md"
            target="_blank"
            rel="noopener noreferrer"
            className="header-icon-link"
            title="User Guide"
          >
            <span className="material-icons">help_outline</span>
          </a>
          <a
            href="https://github.com/MotWakorb/enhancedchannelmanager"
            target="_blank"
            rel="noopener noreferrer"
            className="header-icon-link"
            title="GitHub Repository"
          >
            {/* Sized by .header-icon-link svg in App.css so the mark tracks the
                Material icons beside it from a single source of truth. */}
            <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <path d="M12 2C6.477 2 2 6.477 2 12c0 4.42 2.865 8.17 6.839 9.49.5.092.682-.217.682-.482 0-.237-.009-.866-.013-1.7-2.782.604-3.369-1.34-3.369-1.34-.454-1.156-1.11-1.463-1.11-1.463-.908-.62.069-.608.069-.608 1.003.07 1.531 1.03 1.531 1.03.892 1.529 2.341 1.087 2.91.831.092-.646.35-1.086.636-1.336-2.22-.253-4.555-1.11-4.555-4.943 0-1.091.39-1.984 1.029-2.683-.103-.253-.446-1.27.098-2.647 0 0 .84-.269 2.75 1.025A9.578 9.578 0 0112 6.836a9.59 9.59 0 012.504.337c1.909-1.294 2.747-1.025 2.747-1.025.546 1.377.203 2.394.1 2.647.64.699 1.028 1.592 1.028 2.683 0 3.842-2.339 4.687-4.566 4.935.359.309.678.919.678 1.852 0 1.336-.012 2.415-.012 2.743 0 .267.18.578.688.48C19.138 20.167 22 16.418 22 12c0-5.523-4.477-10-10-10z"/>
            </svg>
          </a>
          <NotificationCenter dedupM3uToastSuppressed={dedupM3uToastSuppressed} />
          <UserMenu onRequestSignOut={requestSignOut} />
        </div>
      </header>

      <TabNavigation
        activeTab={activeTab}
        onTabChange={handleTabChange}
        disabled={isCommitting}
        editModeActive={isEditMode}
        settingsPage={settingsPage}
        onSettingsPageChange={(page) => handleRouteChange('settings', page)}
        isAdmin={adminNavVisible}
      />

      {/* The one Edit Mode exit that cannot be guarded is the session dying
          under the app. This is the way back from it (epic
          enhancedchannelmanager-r93hq): the ledger survived in sessionStorage,
          bound to the operator who staged it, and the operator decides whether
          it comes back — after reading what of it still applies. */}
      <EditModeRestoreDialog
        isOpen={restorePlan !== null && pendingRestore !== null}
        savedAt={pendingRestore?.savedAt ?? 0}
        restorable={restorePlan?.restorable ?? []}
        dropped={restorePlan?.dropped ?? []}
        withdrawnAcknowledgements={restorePlan?.withdrawnAcknowledgements ?? []}
        onRestore={() => {
          if (pendingRestore === null || restorePlan === null) return;
          restoreStagedLedger({
            operations: restorePlan.restorable,
            undoGroups: pendingRestore.undoGroups,
            savedAt: pendingRestore.savedAt,
          });
        }}
        onDiscard={dismissPendingRestore}
      />

      <EditModeExitDialog
        isOpen={showExitDialog}
        summary={summary}
        onApply={handleApplyChanges}
        onDiscard={handleDiscardChanges}
        onKeepEditing={handleKeepEditing}
        isCommitting={isCommitting}
        commitProgress={commitProgress}
        commitFailure={commitFailure}
        onAcknowledgeFailure={handleAcknowledgeCommitFailure}
      />

      {/* AFTER the exit dialog, deliberately: both render an
          `.edit-mode-dialog-overlay`, so the later sibling is the one that
          receives the operator's clicks. Nothing has been applied at this
          point — this is a question, not a result — so it has to sit on top of
          the Apply dialog that asked it (bead enhancedchannelmanager-ic884.4). */}
      <NumberingConflictDialog
        isOpen={numberingConflicts !== null}
        conflicts={numberingConflicts?.conflicts ?? []}
        isCommitting={isCommitting}
        onKeepEditing={handleKeepNumberingConflicts}
        onReconcile={handleReconcileNumbering}
      />

      {/* Keep SettingsModal for first-run configuration */}
      <SettingsModal
        isOpen={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        onSaved={handleSettingsSaved}
      />

      {/* Stream dedup decision surface (bd-u6ftw / BD-H). Opens when a
          single-stream drop onto a group finds a candidate channel above
          the §D2 confidence floor. Multi-stream drops never reach this. */}
      <StreamDedupModal
        isOpen={dedupOnDrop.modalState !== null}
        streamName={dedupOnDrop.modalState?.streamName ?? ''}
        candidate={dedupOnDrop.modalState?.candidate ?? null}
        trigger="drag_drop"
        onMerge={dedupOnDrop.handleMerge}
        onCreateNew={dedupOnDrop.handleCreateNew}
        onCancel={dedupOnDrop.handleCancel}
      />

      <main id="main-content" className="main" tabIndex={-1}>
        <RouteHeaderTargetProvider targets={routeHeaderTargets}>
        <PageHeader
          className="route-page-header"
          headingLevel={1}
          headingRef={routeHeadingRef}
          group={routeHeading.group}
          title={routeHeading.title}
          description={routeHeading.description}
          actions={(
            <>
              {channelManagerPageAction}
              <div
                className="route-page-action-outlet"
                ref={setPrimaryActionTarget}
              />
            </>
          )}
          status={(
            <div
              className="route-page-status-outlet"
              ref={setStatusTarget}
            />
          )}
          controls={(
            <div
              className="route-page-controls-outlet"
              ref={setControlsTarget}
            />
          )}
          relatedLinks={ROUTE_HIERARCHY[activeTab].settingsLinks?.map((link) => ({
            ...link,
            onClick: (event) => {
              if (!isPlainPrimaryActivation(event.nativeEvent)) return;
              event.preventDefault();
              handleRouteChange('settings', link.href.slice('#settings/'.length) as SettingsPage);
            },
          }))}
        />
        <Suspense fallback={<div className="tab-loading"><span className="material-icons spinning">sync</span><p>Loading...</p></div>}>
          {activeTab === 'dashboard' && (
            <OperatorDashboard
              health={{
                value: health,
                ...healthSourceState,
                retry: () => {
                  setHealthSourceState((current) => ({ ...current, state: 'loading' }));
                  void api.getHealth().then((healthData) => {
                    setHealth(healthData);
                    setHealthSourceState({ state: 'success', hasSnapshot: true });
                  }).catch((err) => {
                    setHealthSourceState((current) => ({ ...current, state: classifySourceLoadError(err) }));
                  });
                },
              }}
              channels={{ value: channelInventoryTotal, ...channelInventoryState, retry: () => { void loadChannelInventoryTotal(); } }}
              streams={{
                value: streamInventoryTotal,
                ...streamInventoryState,
                retry: () => { void loadStreamInventoryTotal(); },
              }}
              providers={{ value: providers.length, ...providerSourceState, retry: () => { void loadProviders(); } }}
            />
          )}
          {activeTab === 'channel-manager' && (
            <ErrorBoundary key="tab-channel-manager" scopeLabel="Channel Manager tab" reloadMode="reset">
            <ChannelManagerTab
              // Channel Groups
              channelGroups={displayChannelGroups}
              onChannelGroupsChange={loadChannelGroups}
              onDeleteChannelGroup={handleDeleteChannelGroup}

              // Channels
              channels={displayChannels}
              onChannelsChange={loadChannels}
              onCSVImportComplete={handleCSVImportComplete}
              selectedChannelId={selectedChannel?.id ?? null}
              onChannelSelect={handleChannelSelect}
              onChannelUpdate={handleChannelUpdate}
              onChannelDrop={handleStreamDropOnChannel}
              onBulkStreamDrop={handleBulkStreamDropOnChannel}
              onChannelReorder={handleChannelReorder}
              onCreateChannel={handleCreateChannel}
              onDeleteChannel={handleDeleteChannel}
              channelsLoading={loadingStates.channels}
              channelSources={channelWorkspaceSources}

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
              onStageSetProfileMembership={stageSetProfileMembership}
              stagedSideEffects={stagedSideEffects}
              onStageRestoreChannelGroup={stageRestoreChannelGroup}
              onStageClearStreamStats={stageClearStreamStats}
              onStageRemoveStream={stageRemoveStream}
              onStageReorderStreams={stageReorderStreams}
              onStageBulkAssignNumbers={stageBulkAssignNumbers}
              onStageDeleteChannel={stageDeleteChannel}
              onStageDeleteChannelGroup={stageDeleteChannelGroup}
              onStageRenameChannelGroup={stageRenameChannelGroup}
              onStageCreateGroup={stageCreateGroup}
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
              deletedGroupIds={deletedGroupIds}
              renamedGroupNames={renamedGroupNames}
              channelListFilters={channelListFilters}
              onChannelListFiltersChange={updateChannelListFilters}
              newlyCreatedGroupIds={newlyCreatedGroupIds}
              onTrackNewlyCreatedGroup={trackNewlyCreatedGroup}

              // Streams
              allStreams={streams}
              seenStreamsMap={seenStreams}
              streams={filteredStreams}
              providers={providers}
              streamGroups={streamGroups}
              streamsLoading={loadingStates.streams}
              streamSources={streamWorkspaceSources}
              streamMatchingTotal={streamMatchingTotal}

              // Stream Search & Filter (server-side search via useEffect debounce)
              streamSearch={streamFilters.search}
              onStreamSearchChange={(search) => setStreamFilters(prev => ({ ...prev, search }))}
              streamProviderFilter={streamFilters.providerFilter}
              onStreamProviderFilterChange={(providerFilter) => { requestStreamsLoad(); setStreamFilters(prev => ({ ...prev, providerFilter })); }}
              streamGroupFilter={streamFilters.groupFilter}
              onStreamGroupFilterChange={(groupFilter) => { requestStreamsLoad(); setStreamFilters(prev => ({ ...prev, groupFilter })); }}
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
              externalTriggerManualEntry={manualEntryTrigger}
              onExternalTriggerHandled={handleStreamGroupTriggerHandled}
              onStreamGroupDrop={handleStreamGroupDrop}
              onBulkStreamsDrop={handleBulkStreamsDrop}
              onOpenCreateChannelModal={handleOpenCreateChannelModal}
              onBulkCreateFromGroup={handleBulkCreateFromGroup}
              onCreateChannelManual={handleCreateChannelManual}
              defaultNormalizeOnCreate={normalizeOnChannelCreate}
              onCheckConflicts={handleCheckConflicts}
              onCountPushDownShift={handleCountPushDownShift}
              onGetHighestChannelNumber={handleGetHighestChannelNumber}

              // Dispatcharr URL for channel stream URLs
              dispatcharrUrl={dispatcharrUrl}

              // Appearance settings
              showStreamUrls={showStreamUrls}
              strikeThreshold={strikeThreshold}
              hideUngroupedStreams={hideUngroupedStreams}

              // EPG matching settings
              epgAutoMatchThreshold={epgAutoMatchThreshold}

              // Gracenote conflict handling
              gracenoteConflictMode={gracenoteConflictMode}

              // Refresh streams (bypasses cache)
              onRefreshStreams={refreshStreams}

              // Stream dedup cancel-pulse highlight (bd-u6ftw / BD-H)
              dedupReturningStreamIds={dedupOnDrop.returningStreamIds}

              // Refresh channels after BD-I dedup merge (bd-1lznl) so the
              // mapped-streams set reflects the new channel→stream binding.
              onChannelsChanged={loadChannels}

              // External trigger to open edit modal from Guide tab
              externalChannelToEdit={channelToEditFromGuide}
              onExternalChannelEditHandled={handleExternalChannelEditHandled}

              // Lazy loading - load only the expanded group's streams
              onStreamGroupExpand={loadStreamGroup}
            />
            </ErrorBoundary>
          )}
          {activeTab === 'm3u-manager' && (
            <ErrorBoundary key="tab-m3u-manager" scopeLabel="M3U Manager tab" reloadMode="reset">
            <M3UManagerTab
              epgSources={epgSources}
              channelGroups={channelGroups}
              channelProfiles={channelProfiles}
              streamProfiles={streamProfiles}
              onChannelGroupsChange={loadChannelGroups}
              onAccountsChange={() => { loadProviders(); loadStreamGroups(); }}
              onStreamProfilesChange={loadStreamProfiles}
              hideM3uUrls={hideM3uUrls}
              allowMultiProviderAutoSync={allowMultiProviderAutoSync}
            />
            </ErrorBoundary>
          )}
          {activeTab === 'epg-manager' && (
            <ErrorBoundary key="tab-epg-manager" scopeLabel="EPG Manager tab" reloadMode="reset">
              <EPGManagerTab onSourcesChange={loadEpgSources} hideEpgUrls={hideEpgUrls} />
            </ErrorBoundary>
          )}
          {activeTab === 'guide' && (
            <ErrorBoundary key="tab-guide" scopeLabel="Guide tab" reloadMode="reset">
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
            </ErrorBoundary>
          )}
          {activeTab === 'logo-manager' && (
            <ErrorBoundary key="tab-logo-manager" scopeLabel="Logo Manager tab" reloadMode="reset">
              <LogoManagerTab />
            </ErrorBoundary>
          )}
          {activeTab === 'm3u-changes' && (
            <ErrorBoundary key="tab-m3u-changes" scopeLabel="M3U Changes tab" reloadMode="reset">
              <M3UChangesTab initialHours={m3uChangesHours ?? undefined} />
            </ErrorBoundary>
          )}
          {activeTab === 'channel-pipeline' && (
            <ErrorBoundary key="tab-channel-pipeline" scopeLabel="Channel Pipeline tab" reloadMode="reset">
              <ChannelPipelineTab />
            </ErrorBoundary>
          )}
          {activeTab === 'journal' && (
            <ErrorBoundary key="tab-journal" scopeLabel="Journal tab" reloadMode="reset">
              <JournalTab />
            </ErrorBoundary>
          )}
          {activeTab === 'stats' && (
            <ErrorBoundary key="tab-stats" scopeLabel="Stats tab" reloadMode="reset">
              <StatsTab />
            </ErrorBoundary>
          )}
          {activeTab === 'settings' && (
            <ErrorBoundary key="tab-settings" scopeLabel="Settings tab" reloadMode="reset">
              <SettingsTab onSaved={handleSettingsSaved} channelProfiles={channelProfiles} onProbeComplete={loadChannels} initialSettingsPage={settingsPage} onSettingsPageChange={setSettingsPage} />
            </ErrorBoundary>
          )}
        </Suspense>
        </RouteHeaderTargetProvider>
      </main>

      <VLCProtocolHelperModal
        isOpen={showVLCHelperModal}
        onClose={() => setShowVLCHelperModal(false)}
        onDownloadM3U={() => downloadM3U(vlcModalStreamUrl, vlcModalStreamName)}
        streamName={vlcModalStreamName || 'Stream'}
      />
    </div>
    </BackupDestinationPromptProvider>
    </NotificationProvider>
  );
}

export default App;
