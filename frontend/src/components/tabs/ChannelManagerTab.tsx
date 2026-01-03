import { SplitPane, ChannelsPane, StreamsPane } from '../';
import type { Channel, ChannelGroup, ChannelProfile, Stream, M3UAccount, Logo, EPGData, EPGSource, StreamProfile, M3UGroupSetting, ChannelListFilterSettings, ChangeInfo, SavePoint, ChangeRecord } from '../../types';
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
  onCreateChannel: (name: string, channelNumber?: number, groupId?: number, logoId?: number, tvgId?: string) => Promise<Channel>;
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
  channelListFilters: ChannelListFilterSettings;
  onChannelListFiltersChange: (updates: Partial<ChannelListFilterSettings>) => void;
  newlyCreatedGroupIds: Set<number>;
  onTrackNewlyCreatedGroup: (groupId: number) => void;

  // Streams
  streams: Stream[];
  providers: M3UAccount[];
  streamGroups: string[];
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

  // Refresh streams (bypasses cache)
  onRefreshStreams?: () => void;

  // Bulk Create
  channelDefaults?: ChannelDefaults;
  // Stream group drop (for opening bulk create modal) - supports multiple groups
  externalTriggerGroupNames?: string[] | null;
  onExternalTriggerHandled?: () => void;
  onStreamGroupDrop?: (groupNames: string[], streamIds: number[]) => void;
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
    profileIds?: number[]
  ) => Promise<void>;
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
  channelListFilters,
  onChannelListFiltersChange,
  newlyCreatedGroupIds,
  onTrackNewlyCreatedGroup,

  // Streams
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

  // Refresh streams
  onRefreshStreams,

  // Bulk Create
  channelDefaults,
  externalTriggerGroupNames,
  onExternalTriggerHandled,
  onStreamGroupDrop,
  onBulkCreateFromGroup,
}: ChannelManagerTabProps) {
  return (
    <SplitPane
      left={
        <ChannelsPane
          channelGroups={channelGroups}
          channels={channels}
          streams={streams}
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
          showStreamUrls={showStreamUrls}
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
          channelProfiles={channelProfiles}
          channelDefaults={channelDefaults}
          externalTriggerGroupNames={externalTriggerGroupNames}
          onExternalTriggerHandled={onExternalTriggerHandled}
          onBulkCreateFromGroup={onBulkCreateFromGroup}
          showStreamUrls={showStreamUrls}
          onRefreshStreams={onRefreshStreams}
        />
      }
    />
  );
}
