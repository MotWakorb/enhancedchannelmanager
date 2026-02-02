import { useMemo } from 'react';
import { SplitPane, ChannelsPane, StreamsPane } from '../';
import type { Channel, ChannelGroup, ChannelProfile, Stream, StreamGroupInfo, M3UAccount, Logo, EPGData, EPGSource, StreamProfile, M3UGroupSetting, ChannelListFilterSettings, ChangeInfo, SavePoint, ChangeRecord } from '../../types';
import type { TimezonePreference, NumberSeparator, PrefixOrder } from '../../services/api';
import type { ChannelDefaults } from '../StreamsPane';

export interface ChannelManagerTabProps {
  // Channel Groups
  channelGroups: ChannelGroup[];
  onChannelGroupsChange: () => Promise<void>;
  onDeleteChannelGroup: (groupId: number) => Promise<void>;

  // Channels
  channels: Channel[];
  selectedChannelId: number | null;
  onChannelSelect: (channel: Channel | null) => void;
  onChannelUpdate: (channel: Channel, changeInfo?: ChangeInfo) => void;
  onChannelDrop: (channelId: number, streamId: number) => Promise<void>;
  onBulkStreamDrop: (channelId: number, streamIds: number[]) => Promise<void>;
  onChannelReorder: (channelIds: number[], startingNumber: number) => Promise<void>;
  onCreateChannel: (name: string, channelNumber?: number, groupId?: number, logoId?: number, tvgId?: string, logoUrl?: string) => Promise<Channel>;
  onDeleteChannel: (channelId: number) => Promise<void>;
  channelsLoading: boolean;

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
  onStageRemoveStream: (channelId: number, streamId: number, description: string) => void;
  onStageReorderStreams: (channelId: number, streamIds: number[], description: string) => void;
  onStageBulkAssignNumbers: (channelIds: number[], startingNumber: number, description: string) => void;
  onStageDeleteChannel: (channelId: number, description: string) => void;
  onStageDeleteChannelGroup: (groupId: number, description: string) => void;
  onStageRenameChannelGroup: (groupId: number, newName: string, description: string) => void;
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
  streams: Stream[];     // Filtered streams - for StreamsPane display
  providers: M3UAccount[];
  streamGroups: StreamGroupInfo[];
  streamsLoading: boolean;

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
  hideUngroupedStreams?: boolean;

  // EPG matching settings
  epgAutoMatchThreshold?: number;

  // Gracenote conflict handling
  gracenoteConflictMode?: 'ask' | 'skip' | 'overwrite';

  // Refresh streams (bypasses cache)
  onRefreshStreams?: () => void;

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
  onStreamGroupDrop?: (groupNames: string[], streamIds: number[]) => void;
  // Bulk streams drop (for opening bulk create modal when dropping multiple streams)
  // Includes target group ID and starting channel number for pre-filling the modal
  onBulkStreamsDrop?: (streamIds: number[], groupId: number | null, startingNumber: number) => void;
  // Callback to open create channel modal (routes to bulk create modal in manual entry mode)
  onOpenCreateChannelModal?: () => void;
  onBulkCreateFromGroup: (
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
  // Create a single channel (for manual entry mode - supports new group creation)
  onCreateChannelManual?: (name: string, channelNumber?: number, groupId?: number, newGroupName?: string) => Promise<void>;
  // Default value for normalization toggle (from settings)
  defaultNormalizeOnCreate?: boolean;
  // Callback to check for conflicts with existing channel numbers
  onCheckConflicts?: (startingNumber: number, count: number) => number;
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
  selectedChannelId,
  onChannelSelect,
  onChannelUpdate,
  onChannelDrop,
  onBulkStreamDrop,
  onChannelReorder,
  onCreateChannel,
  onDeleteChannel,
  channelsLoading,

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
  onStageRemoveStream,
  onStageReorderStreams,
  onStageBulkAssignNumbers,
  onStageDeleteChannel,
  onStageDeleteChannelGroup,
  onStageRenameChannelGroup,
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
  streams,
  providers,
  streamGroups,
  streamsLoading,

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
  hideUngroupedStreams = true,

  // EPG matching settings
  epgAutoMatchThreshold = 80,

  // Gracenote conflict handling
  gracenoteConflictMode = 'ask',

  // Refresh streams
  onRefreshStreams,

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

  return (
    <SplitPane
      left={
        <ChannelsPane
          channelGroups={channelGroups}
          channels={channels}
          streams={allStreams}
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
          onStageRemoveStream={onStageRemoveStream}
          onStageReorderStreams={onStageReorderStreams}
          onStageBulkAssignNumbers={onStageBulkAssignNumbers}
          onStageDeleteChannel={onStageDeleteChannel}
          onStageDeleteChannelGroup={onStageDeleteChannelGroup}
          onStageRenameChannelGroup={onStageRenameChannelGroup}
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
          epgAutoMatchThreshold={epgAutoMatchThreshold}
          gracenoteConflictMode={gracenoteConflictMode}
          externalChannelToEdit={externalChannelToEdit}
          onExternalChannelEditHandled={onExternalChannelEditHandled}
        />
      }
      right={
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
          onBulkCreateFromGroup={onBulkCreateFromGroup}
          onCreateChannel={onCreateChannelManual}
          onCheckConflicts={onCheckConflicts}
          onGetHighestChannelNumber={onGetHighestChannelNumber}
          showStreamUrls={showStreamUrls}
          hideUngroupedStreams={hideUngroupedStreams}
          onRefreshStreams={onRefreshStreams}
          mappedStreamIds={mappedStreamIds}
          onGroupExpand={onStreamGroupExpand}
          defaultNormalizeOnCreate={defaultNormalizeOnCreate}
        />
      }
    />
  );
}
