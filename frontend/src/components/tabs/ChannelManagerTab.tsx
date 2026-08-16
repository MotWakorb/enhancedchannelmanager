import { useCallback, useEffect, useMemo, useState } from 'react';
import { SplitPane, ChannelsPane, StreamsPane } from '../';
import { PendingMergesPage } from './PendingMergesPage';
import * as api from '../../services/api';
import { logger } from '../../utils/logger';
import type { Channel, ChannelGroup, ChannelProfile, Stream, StreamGroupInfo, M3UAccount, Logo, EPGData, EPGSource, StreamProfile, M3UGroupSetting, ChannelListFilterSettings, ChangeInfo, SavePoint, ChangeRecord } from '../../types';
import type { TimezonePreference, NumberSeparator, PrefixOrder, ResolvedCreateChannelNames } from '../../services/api';
import type { BulkCreateFromGroupResult, ChannelDefaults } from '../StreamsPane';
import type { DedupDropReport } from '../../hooks/useDedupOnDrop';
import { SourceLoadStatus } from '../SourceLoadStatus';
import type { SourceLoadState } from '../sourceLoadState';
import { aggregateWorkspaceSources, retryFailedSources, type WorkspaceSource } from '../workspaceLoadState';
import './ChannelManagerTab.css';

/**
 * Window-level event the toast action (and any other surface) can dispatch to
 * open the Pending Merges sub-view from outside the React tree. The toast
 * that fires after an M3U refresh queues new dedup candidates (BD-J) is the
 * first consumer; the contract is
 * `dispatchEvent(new CustomEvent('ecm:open-pending-merges'))`.
 *
 * Why a DOM event rather than prop drilling: NotificationCenter lives next
 * to TabNavigation in App.tsx, not inside this tab. A DOM event keeps the
 * cross-tree coupling minimal — no shared context, no callback chain.
 */
export const PENDING_MERGES_EVENT = 'ecm:open-pending-merges';

/**
 * How often the subnav badge re-polls the pending-merges count. 30s mirrors
 * the NotificationCenter's idle-poll cadence — the next-best signal that
 * something queue-affecting just happened (an M3U refresh surfaces via the
 * auto_creation notification on the same cadence). When the operator opens
 * the Pending Merges view the page fetches its own fresh data; the badge
 * poll is solely for the count affordance on the subnav link.
 */
const COUNT_POLL_INTERVAL_MS = 30_000;

type ChannelManagerView = 'default' | 'pending-merges';

export interface ChannelManagerTabProps {
  // Channel Groups
  channelGroups: ChannelGroup[];
  onChannelGroupsChange: () => Promise<void>;
  onDeleteChannelGroup: (groupId: number) => Promise<void>;

  // Channels
  channels: Channel[];
  onChannelsChange?: () => Promise<void>;
  onCSVImportComplete?: () => Promise<void>;
  selectedChannelId: number | null;
  onChannelSelect: (channel: Channel | null) => void;
  onChannelUpdate: (channel: Channel, changeInfo?: ChangeInfo) => void;
  onChannelDrop: (channelId: number, streamId: number) => Promise<void>;
  onBulkStreamDrop: (channelId: number, streamIds: number[]) => Promise<void>;
  onChannelReorder: (channelIds: number[], startingNumber: number) => Promise<void>;
  onCreateChannel: (name: string, channelNumber?: number, groupId?: number, logoId?: number, tvgId?: string, logoUrl?: string) => Promise<Channel>;
  onDeleteChannel: (channelId: number) => Promise<void>;
  channelsLoading: boolean;
  channelsError?: Extract<SourceLoadState, 'error' | 'permission'> | null;
  onRetryChannels?: () => void;
  channelSources?: WorkspaceSource[];

  // Channel Search & Filter
  channelSearch: string;
  onChannelSearchChange: (search: string) => void;
  selectedGroups: number[];
  onSelectedGroupsChange: (groups: number[]) => void;

  // Multi-select
  selectedChannelIds: Set<number>;
  lastSelectedChannelId: number | null;
  onToggleChannelSelection: (channelId: number, addToSelection: boolean) => void;
  onClearChannelSelection: () => void;
  onSelectChannelRange: (fromId: number, toId: number, groupChannelIds: number[]) => void;
  onSelectGroupChannels: (channelIds: number[], select: boolean) => void;

  // Auto-rename
  autoRenameChannelNumber: boolean;

  // Edit Mode
  isEditMode: boolean;
  isCommitting: boolean;
  modifiedChannelIds: Set<number>;
  onStageUpdateChannel: (channelId: number, updates: Partial<Channel>, description: string) => void;
  onStageAddStream: (channelId: number, streamId: number, description: string) => void;
  /**
   * Staging hooks for the actions Edit Mode used to write through itself
   * (bead enhancedchannelmanager-kz089).
   */
  onStageSetProfileMembership: (profileId: number, channelIds: number[], enabled: boolean, description: string) => void;
  onStageRestoreChannelGroup: (groupId: number, description: string) => void;
  onStageClearStreamStats: (streamIds: number[], description: string) => void;
  onStageRemoveStream: (channelId: number, streamId: number, description: string) => void;
  onStageReorderStreams: (channelId: number, streamIds: number[], description: string) => void;
  onStageBulkAssignNumbers: (channelIds: number[], startingNumber: number, description: string) => void;
  onStageDeleteChannel: (channelId: number, description: string) => void;
  onStageDeleteChannelGroup: (groupId: number, description: string) => void;
  onStageRenameChannelGroup: (groupId: number, newName: string, description: string) => void;
  onStageCreateGroup: (name: string) => number;
  onStartBatch: (description: string) => void;
  onEndBatch: () => void;

  // History
  canUndo: boolean;
  canRedo: boolean;
  undoCount: number;
  redoCount: number;
  lastChange: ChangeRecord | null;
  savePoints: SavePoint[];
  hasUnsavedChanges: boolean;
  isOperationPending: boolean;
  onUndo: () => void;
  onRedo: () => void;
  onCreateSavePoint: (name?: string) => void;
  onRevertToSavePoint: (id: string) => void;
  onDeleteSavePoint: (id: string) => void;

  // Logos
  logos: Logo[];
  onLogosChange: () => Promise<void>;

  // EPG & Stream Profiles
  epgData: EPGData[];
  epgSources: EPGSource[];
  streamProfiles: StreamProfile[];
  epgDataLoading: boolean;

  // Channel Profiles
  channelProfiles: ChannelProfile[];
  onChannelProfilesChange: () => Promise<void>;

  // Provider & Filter Settings
  providerGroupSettings: Record<number, M3UGroupSetting>;
  deletedGroupIds?: Set<number>; // Groups staged for deletion in edit mode
  renamedGroupNames?: Map<number, string>; // Groups staged for rename in edit mode
  channelListFilters: ChannelListFilterSettings;
  onChannelListFiltersChange: (updates: Partial<ChannelListFilterSettings>) => void;
  newlyCreatedGroupIds: Set<number>;
  onTrackNewlyCreatedGroup: (groupId: number) => void;

  // Streams
  allStreams: Stream[];  // All streams (unfiltered) - for ChannelsPane lookups
  seenStreamsMap: Map<number, Stream>;  // Persistent cache across searches - lets ChannelsPane resolve staged stream IDs that aren't in the current search results
  streams: Stream[];     // Filtered streams - for StreamsPane display
  providers: M3UAccount[];
  streamGroups: StreamGroupInfo[];
  streamsLoading: boolean;
  streamsError?: Extract<SourceLoadState, 'error' | 'permission'> | null;
  onRetryStreams?: () => void;
  streamSources?: WorkspaceSource[];
  streamMatchingTotal?: number | null;

  // Stream Search & Filter
  streamSearch: string;
  onStreamSearchChange: (search: string) => void;
  streamProviderFilter: number | null;
  onStreamProviderFilterChange: (provider: number | null) => void;
  streamGroupFilter: string | null;
  onStreamGroupFilterChange: (group: string | null) => void;
  selectedProviders: number[];
  onSelectedProvidersChange: (providers: number[]) => void;
  selectedStreamGroups: string[];
  onSelectedStreamGroupsChange: (groups: string[]) => void;
  onClearStreamFilters?: () => void;

  // Dispatcharr URL (for constructing channel stream URLs)
  dispatcharrUrl: string;

  // Appearance settings
  showStreamUrls?: boolean;
  strikeThreshold?: number;
  hideUngroupedStreams?: boolean;

  // EPG matching settings
  epgAutoMatchThreshold?: number;

  // Gracenote conflict handling
  gracenoteConflictMode?: 'ask' | 'skip' | 'overwrite';

  // Refresh streams (bypasses cache)
  onRefreshStreams?: () => void;

  // Stream IDs currently rendering the dedup cancel-pulse highlight
  // (bd-u6ftw / BD-H). Empty Set when no animation is active.
  dedupReturningStreamIds?: Set<number>;

  // Refresh channels after BD-I dedup merge appends a stream to an existing
  // channel (bd-1lznl), so the mapped-streams set reflects the new binding.
  onChannelsChanged?: () => void;

  // Bulk Create
  channelDefaults?: ChannelDefaults;
  // Stream group drop (for opening bulk create modal) - supports multiple groups
  externalTriggerGroupNames?: string[] | null;
  // Stream IDs drop (for opening bulk create modal when dropping individual streams)
  externalTriggerStreamIds?: number[] | null;
  // Target group ID and starting number for pre-filling the bulk create modal
  externalTriggerTargetGroupId?: number | null;
  externalTriggerStartingNumber?: number | null;
  // Manual entry trigger (opens bulk create modal without pre-selected streams)
  externalTriggerManualEntry?: boolean;
  onExternalTriggerHandled?: () => void;
  onStreamGroupDrop?: (
    groupNames: string[],
    streamIds: number[],
    targetGroupId?: number,
    suggestedStartingNumber?: number,
  ) => void;
  // Bulk streams drop (for opening bulk create modal when dropping multiple streams)
  // Includes target group ID and starting channel number for pre-filling the modal.
  // Resolves with what the duplicate check did so ChannelsPane can say so
  // (bead enhancedchannelmanager-ok8tj); pass-through only.
  onBulkStreamsDrop?: (
    streamIds: number[],
    groupId: number | null,
    startingNumber: number,
  ) => void | Promise<DedupDropReport | void>;
  // Callback to open create channel modal (routes to bulk create modal in manual entry mode)
  onOpenCreateChannelModal?: () => void;
  onBulkCreateFromGroup: (
    streams: Stream[],
    startingNumber: number,
    channelGroupId: number | null,
    // `| undefined` rather than `?` so `nameResolution` can be required; see
    // `StreamsPaneProps.onBulkCreateFromGroup`.
    newGroupName: string | undefined,
    timezonePreference: TimezonePreference | undefined,
    stripCountryPrefix: boolean | undefined,
    addChannelNumber: boolean | undefined,
    numberSeparator: NumberSeparator | undefined,
    keepCountryPrefix: boolean | undefined,
    countrySeparator: NumberSeparator | undefined,
    prefixOrder: PrefixOrder | undefined,
    stripNetworkPrefix: boolean | undefined,
    customNetworkPrefixes: string[] | undefined,
    stripNetworkSuffix: boolean | undefined,
    customNetworkSuffixes: string[] | undefined,
    profileIds: number[] | undefined,
    pushDownOnConflict: boolean | undefined,
    // The names, already resolved by the dialog. REQUIRED pass-through; see
    // `StreamsPaneProps.onBulkCreateFromGroup`
    // (bead enhancedchannelmanager-e9e5o).
    nameResolution: ResolvedCreateChannelNames
  ) => Promise<BulkCreateFromGroupResult | void>;
  // Create a single channel (for manual entry mode - supports new group
  // creation). `pushDownOnConflict` moves whatever already occupies
  // `channelNumber` out of the way instead of creating a duplicate
  // (bead enhancedchannelmanager-fprsq).
  onCreateChannelManual?: (name: string, channelNumber?: number, groupId?: number, newGroupName?: string, pushDownOnConflict?: boolean) => Promise<void>;
  // Default value for normalization toggle (from settings)
  defaultNormalizeOnCreate?: boolean;
  // Callback to check for conflicts with existing channel numbers
  onCheckConflicts?: (startingNumber: number, count: number) => number;
  // Callback to count how many existing channels a push-down would renumber
  onCountPushDownShift?: (startingNumber: number, count: number) => number;
  // Callback to get the highest existing channel number (for "insert at end" option)
  onGetHighestChannelNumber?: () => number;

  // External trigger to open edit modal from Guide tab
  externalChannelToEdit?: Channel | null;
  onExternalChannelEditHandled?: () => void;

  // Lazy loading - callback when a stream group is expanded
  // Passes the group name so only that group's streams can be loaded
  onStreamGroupExpand?: (groupName: string) => void;
}

export function ChannelManagerTab({
  // Channel Groups
  channelGroups,
  onChannelGroupsChange,
  onDeleteChannelGroup,

  // Channels
  channels,
  onChannelsChange,
  onCSVImportComplete,
  selectedChannelId,
  onChannelSelect,
  onChannelUpdate,
  onChannelDrop,
  onBulkStreamDrop,
  onChannelReorder,
  onCreateChannel,
  onDeleteChannel,
  channelsLoading,
  channelsError = null,
  onRetryChannels,
  channelSources,

  // Channel Search & Filter
  channelSearch,
  onChannelSearchChange,
  selectedGroups,
  onSelectedGroupsChange,

  // Multi-select
  selectedChannelIds,
  lastSelectedChannelId,
  onToggleChannelSelection,
  onClearChannelSelection,
  onSelectChannelRange,
  onSelectGroupChannels,

  // Auto-rename
  autoRenameChannelNumber,

  // Edit Mode
  isEditMode,
  isCommitting,
  modifiedChannelIds,
  onStageUpdateChannel,
  onStageAddStream,
  onStageSetProfileMembership,
  onStageRestoreChannelGroup,
  onStageClearStreamStats,
  onStageRemoveStream,
  onStageReorderStreams,
  onStageBulkAssignNumbers,
  onStageDeleteChannel,
  onStageDeleteChannelGroup,
  onStageRenameChannelGroup,
  onStageCreateGroup,
  onStartBatch,
  onEndBatch,

  // History
  canUndo,
  canRedo,
  undoCount,
  redoCount,
  lastChange,
  savePoints,
  hasUnsavedChanges,
  isOperationPending,
  onUndo,
  onRedo,
  onCreateSavePoint,
  onRevertToSavePoint,
  onDeleteSavePoint,

  // Logos
  logos,
  onLogosChange,

  // EPG & Stream Profiles
  epgData,
  epgSources,
  streamProfiles,
  epgDataLoading,

  // Channel Profiles
  channelProfiles,
  onChannelProfilesChange,

  // Provider & Filter Settings
  providerGroupSettings,
  deletedGroupIds,
  renamedGroupNames,
  channelListFilters,
  onChannelListFiltersChange,
  newlyCreatedGroupIds,
  onTrackNewlyCreatedGroup,

  // Streams
  allStreams,
  seenStreamsMap,
  streams,
  providers,
  streamGroups,
  streamsLoading,
  streamsError = null,
  onRetryStreams,
  streamSources,
  streamMatchingTotal = null,

  // Stream Search & Filter
  streamSearch,
  onStreamSearchChange,
  streamProviderFilter,
  onStreamProviderFilterChange,
  streamGroupFilter,
  onStreamGroupFilterChange,
  selectedProviders,
  onSelectedProvidersChange,
  selectedStreamGroups,
  onSelectedStreamGroupsChange,
  onClearStreamFilters,

  // Dispatcharr URL
  dispatcharrUrl,

  // Appearance settings
  showStreamUrls = true,
  strikeThreshold = 3,
  hideUngroupedStreams = true,

  // EPG matching settings
  epgAutoMatchThreshold = 80,

  // Gracenote conflict handling
  gracenoteConflictMode = 'ask',

  // Refresh streams
  onRefreshStreams,

  // Dedup cancel-pulse highlight (bd-u6ftw / BD-H)
  dedupReturningStreamIds,

  // Refresh channels after BD-I dedup merge (bd-1lznl)
  onChannelsChanged,

  // Bulk Create
  channelDefaults,
  externalTriggerGroupNames,
  externalTriggerStreamIds,
  externalTriggerTargetGroupId,
  externalTriggerStartingNumber,
  externalTriggerManualEntry,
  onExternalTriggerHandled,
  onStreamGroupDrop,
  onBulkStreamsDrop,
  onOpenCreateChannelModal,
  onBulkCreateFromGroup,
  onCreateChannelManual,
  defaultNormalizeOnCreate = false,
  onCheckConflicts,
  onCountPushDownShift,
  onGetHighestChannelNumber,

  // External trigger to open edit modal from Guide tab
  externalChannelToEdit,
  onExternalChannelEditHandled,

  // Lazy loading
  onStreamGroupExpand,
}: ChannelManagerTabProps) {
  // Compute set of stream IDs that are already mapped to channels
  const mappedStreamIds = useMemo(() => {
    const ids = new Set<number>();
    channels.forEach(ch => {
      ch.streams.forEach(streamId => ids.add(streamId));
    });
    return ids;
  }, [channels]);

  // Pending Merges sub-view state (BD-J / bd-gfxrz, ADR-008 §D1). The view
  // toggles between the default channels/streams split-pane and the
  // PendingMergesPage. The subnav link is the only entry point from this
  // tab; outside-tree entry points (e.g. the M3U-refresh toast) fire the
  // PENDING_MERGES_EVENT, which the listener below handles.
  const [view, setView] = useState<ChannelManagerView>('default');
  const [pendingMergesCount, setPendingMergesCount] = useState(0);

  // Poll the pending-merges count so the subnav badge stays current without
  // requiring the operator to switch into the page. We use the same list
  // endpoint with page_size=1 — we only need `total`, not the rows. A poll
  // failure is logged and the badge stays at its last-known value rather
  // than dropping to 0 (which would falsely hide the affordance).
  const refreshCount = useCallback(async () => {
    try {
      const response = await api.getPendingMerges({
        status: 'pending',
        page: 1,
        pageSize: 1,
      });
      setPendingMergesCount(response.total);
    } catch (err) {
      logger.debug('ChannelManagerTab: pending-merges count poll failed', err);
    }
  }, []);

  useEffect(() => {
    refreshCount();
    const interval = window.setInterval(refreshCount, COUNT_POLL_INTERVAL_MS);
    return () => window.clearInterval(interval);
  }, [refreshCount]);

  // Listen for the cross-tree open-page event (toast action, MCP redirect,
  // future deep-links). Switches into the pending-merges view and triggers
  // an immediate count refresh — the toast that just fired is the strongest
  // signal that the queue depth just changed.
  useEffect(() => {
    const handler = () => {
      setView('pending-merges');
      refreshCount();
    };
    window.addEventListener(PENDING_MERGES_EVENT, handler);
    return () => window.removeEventListener(PENDING_MERGES_EVENT, handler);
  }, [refreshCount]);

  // Subnav link visibility per spec: render only when count > 0 OR when the
  // operator is already on the page (so a single-resolve doesn't strand the
  // operator on a view with no way back to the default panes via the subnav).
  const showSubnavLink = pendingMergesCount > 0 || view === 'pending-merges';
  const effectiveChannelSources = channelSources ?? [{
    key: 'channels',
    label: 'channels',
    state: channelsError ?? (channelsLoading ? 'loading' : 'success'),
    hasSnapshot: channelsError === 'error' && channels.length > 0,
    retry: onRetryChannels ?? (() => undefined),
  }];
  const effectiveStreamSources = streamSources ?? [{
    key: 'streams',
    label: 'streams',
    state: streamsError ?? (streamsLoading ? 'loading' : 'success'),
    hasSnapshot: streamsError === 'error' && streams.length > 0,
    retry: onRetryStreams ?? (() => undefined),
  }];
  const channelLoad = aggregateWorkspaceSources(effectiveChannelSources);
  const streamLoad = aggregateWorkspaceSources(effectiveStreamSources);
  const permissionDenied = channelLoad.state === 'permission' || streamLoad.state === 'permission';

  const unavailablePane = (
    heading: 'Channels' | 'Streams',
    state: Extract<SourceLoadState, 'error' | 'permission'>,
    onRetry?: () => void,
  ) => (
    <section className="channel-workspace-state" aria-labelledby={`${heading.toLowerCase()}-state-heading`}>
      <h2 id={`${heading.toLowerCase()}-state-heading`}>{heading}</h2>
      <SourceLoadStatus
        state={state}
        successText={`${heading} loaded`}
        sourceName={heading.toLowerCase()}
        onRetry={state === 'error' ? onRetry : undefined}
      />
    </section>
  );

  return (
    <div className="channel-manager-tab">
      {showSubnavLink && (
        <nav className="channel-manager-subnav" aria-label="Channel Manager views">
          <button
            type="button"
            className={`channel-manager-subnav-link ${view === 'default' ? 'is-active' : ''}`}
            onClick={() => setView('default')}
          >
            Channels &amp; Streams
          </button>
          <button
            type="button"
            className={`channel-manager-subnav-link ${view === 'pending-merges' ? 'is-active' : ''}`}
            onClick={() => setView('pending-merges')}
            aria-label={
              pendingMergesCount > 0
                ? `Pending Merges (${pendingMergesCount})`
                : 'Pending Merges'
            }
          >
            Pending Merges
            {pendingMergesCount > 0 && (
              <span
                className="channel-manager-subnav-badge"
                data-testid="pending-merges-badge"
              >
                {pendingMergesCount}
              </span>
            )}
          </button>
        </nav>
      )}

      {view === 'pending-merges' ? (
        <PendingMergesPage />
      ) : permissionDenied ? (
        <div className="channel-workspace-permission">
          {unavailablePane('Channels', 'permission')}
          {unavailablePane('Streams', 'permission')}
        </div>
      ) : (
        <SplitPane
      /* Even split, stated here rather than left to SplitPane's own default.
         That default is 58, and Channel Manager is SplitPane's only consumer,
         so 58 was in practice this page's ratio: measured at 1920 it rendered
         972px of channels against 698px of streams. The panes hold comparable
         amounts of information and neither earns the extra 137px, so the
         starting point is even and the divider is still draggable across the
         35-70% range (bead enhancedchannelmanager-vh6hh, PO decision). */
      defaultLeftWidth={50}
      leftLabel="Channels"
      rightLabel="Streams"
      left={
        channelLoad.state === 'error' && !channelLoad.stale
          ? unavailablePane('Channels', 'error', () => { void retryFailedSources(effectiveChannelSources); })
          : <div className="channel-workspace-pane-content">
          {channelLoad.state === 'error' && (
            <SourceLoadStatus
              state="error"
              stale
              successText="Channels loaded"
              sourceName="channels"
              onRetry={() => { void retryFailedSources(effectiveChannelSources); }}
            />
          )}
          {channelLoad.state === 'success' && channels.length === 0 && channelGroups.length === 0 && (
            <p className="channel-workspace-empty empty-inline" role="status">No channels are configured.</p>
          )}
          <ChannelsPane
          channelGroups={channelGroups}
          channels={channels}
          streams={allStreams}
          seenStreamsMap={seenStreamsMap}
          providers={providers}
          selectedChannelId={selectedChannelId}
          onChannelSelect={onChannelSelect}
          onChannelUpdate={onChannelUpdate}
          onChannelDrop={onChannelDrop}
          onBulkStreamDrop={onBulkStreamDrop}
          onChannelReorder={onChannelReorder}
          onCreateChannel={onCreateChannel}
          onDeleteChannel={onDeleteChannel}
          searchTerm={channelSearch}
          onSearchChange={onChannelSearchChange}
          selectedGroups={selectedGroups}
          onSelectedGroupsChange={onSelectedGroupsChange}
          loading={channelsLoading}
          autoRenameChannelNumber={autoRenameChannelNumber}
          isEditMode={isEditMode}
          modifiedChannelIds={modifiedChannelIds}
          onStageUpdateChannel={onStageUpdateChannel}
          onStageAddStream={onStageAddStream}
          onStageSetProfileMembership={onStageSetProfileMembership}
          onStageRestoreChannelGroup={onStageRestoreChannelGroup}
          onStageClearStreamStats={onStageClearStreamStats}
          onStageRemoveStream={onStageRemoveStream}
          onStageReorderStreams={onStageReorderStreams}
          onStageBulkAssignNumbers={onStageBulkAssignNumbers}
          onStageDeleteChannel={onStageDeleteChannel}
          onStageDeleteChannelGroup={onStageDeleteChannelGroup}
          onStageRenameChannelGroup={onStageRenameChannelGroup}
          onStageCreateGroup={onStageCreateGroup}
          onStartBatch={onStartBatch}
          onEndBatch={onEndBatch}
          isCommitting={isCommitting}
          canUndo={canUndo}
          canRedo={canRedo}
          undoCount={undoCount}
          redoCount={redoCount}
          lastChange={lastChange}
          savePoints={savePoints}
          hasUnsavedChanges={hasUnsavedChanges}
          isOperationPending={isOperationPending}
          onUndo={onUndo}
          onRedo={onRedo}
          onCreateSavePoint={onCreateSavePoint}
          onRevertToSavePoint={onRevertToSavePoint}
          onDeleteSavePoint={onDeleteSavePoint}
          logos={logos}
          onLogosChange={onLogosChange}
          onChannelGroupsChange={onChannelGroupsChange}
          onChannelsChange={onChannelsChange}
          onCSVImportComplete={onCSVImportComplete}
          onDeleteChannelGroup={onDeleteChannelGroup}
          epgData={epgData}
          epgSources={epgSources}
          streamProfiles={streamProfiles}
          epgDataLoading={epgDataLoading}
          channelProfiles={channelProfiles}
          onChannelProfilesChange={onChannelProfilesChange}
          channelDefaults={channelDefaults}
          providerGroupSettings={providerGroupSettings}
          renamedGroupNames={renamedGroupNames}
          channelListFilters={channelListFilters}
          onChannelListFiltersChange={onChannelListFiltersChange}
          newlyCreatedGroupIds={newlyCreatedGroupIds}
          onTrackNewlyCreatedGroup={onTrackNewlyCreatedGroup}
          selectedChannelIds={selectedChannelIds}
          lastSelectedChannelId={lastSelectedChannelId}
          onToggleChannelSelection={onToggleChannelSelection}
          onClearChannelSelection={onClearChannelSelection}
          onSelectChannelRange={onSelectChannelRange}
          onSelectGroupChannels={onSelectGroupChannels}
          dispatcharrUrl={dispatcharrUrl}
          onStreamGroupDrop={onStreamGroupDrop}
          onBulkStreamsDrop={onBulkStreamsDrop}
          onOpenCreateChannelModal={onOpenCreateChannelModal}
          showStreamUrls={showStreamUrls}
          strikeThreshold={strikeThreshold}
          epgAutoMatchThreshold={epgAutoMatchThreshold}
          gracenoteConflictMode={gracenoteConflictMode}
          externalChannelToEdit={externalChannelToEdit}
          onExternalChannelEditHandled={onExternalChannelEditHandled}
        /></div>
      }
      right={
        streamLoad.state === 'error' && !streamLoad.stale
          ? unavailablePane('Streams', 'error', () => { void retryFailedSources(effectiveStreamSources); })
          : <div className="channel-workspace-pane-content">
          {streamLoad.state === 'error' && (
            <SourceLoadStatus
              state="error"
              stale
              successText="Streams loaded"
              sourceName="streams"
              onRetry={() => { void retryFailedSources(effectiveStreamSources); }}
            />
          )}
          {streamLoad.state === 'success' && streams.length === 0 && streamGroups.length === 0 && (
            <p className="channel-workspace-empty empty-inline" role="status">No source streams are available.</p>
          )}
          <StreamsPane
          streams={streams}
          providers={providers}
          streamGroups={streamGroups}
          searchTerm={streamSearch}
          onSearchChange={onStreamSearchChange}
          providerFilter={streamProviderFilter}
          onProviderFilterChange={onStreamProviderFilterChange}
          groupFilter={streamGroupFilter}
          onGroupFilterChange={onStreamGroupFilterChange}
          loading={streamsLoading}
          matchingTotal={streamMatchingTotal}
          channels={channels}
          onBulkAddToChannel={(streamIds, channelId) => {
            void onBulkStreamDrop(channelId, streamIds);
          }}
          onKeyboardCreateFromGroup={onStreamGroupDrop}
          selectedProviders={selectedProviders}
          onSelectedProvidersChange={onSelectedProvidersChange}
          selectedStreamGroups={selectedStreamGroups}
          onSelectedStreamGroupsChange={onSelectedStreamGroupsChange}
          onClearStreamFilters={onClearStreamFilters}
          isEditMode={isEditMode}
          channelGroups={channelGroups}
          selectedChannelGroups={selectedGroups}
          providerGroupSettings={providerGroupSettings}
          deletedGroupIds={deletedGroupIds}
          channelProfiles={channelProfiles}
          channelDefaults={channelDefaults}
          externalTriggerGroupNames={externalTriggerGroupNames}
          externalTriggerStreamIds={externalTriggerStreamIds}
          externalTriggerTargetGroupId={externalTriggerTargetGroupId}
          externalTriggerStartingNumber={externalTriggerStartingNumber}
          externalTriggerManualEntry={externalTriggerManualEntry}
          onExternalTriggerHandled={onExternalTriggerHandled}
          onStageAddStream={onStageAddStream}
          onBulkCreateFromGroup={onBulkCreateFromGroup}
          onCreateChannel={onCreateChannelManual}
          onCheckConflicts={onCheckConflicts}
          onCountPushDownShift={onCountPushDownShift}
          onGetHighestChannelNumber={onGetHighestChannelNumber}
          showStreamUrls={showStreamUrls}
          strikeThreshold={strikeThreshold}
          hideUngroupedStreams={hideUngroupedStreams}
          onRefreshStreams={onRefreshStreams}
          onChannelsChanged={onChannelsChanged}
          mappedStreamIds={mappedStreamIds}
          onGroupExpand={onStreamGroupExpand}
          defaultNormalizeOnCreate={defaultNormalizeOnCreate}
          dedupReturningStreamIds={dedupReturningStreamIds}
        /></div>
      }
    />
      )}
    </div>
  );
}
