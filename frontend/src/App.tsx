import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import {
  ChannelsPane,
  StreamsPane,
  SettingsModal,
  SplitPane,
  EditModeBanner,
  EditModeExitDialog,
} from './components';
import { useChangeHistory, useEditMode } from './hooks';
import * as api from './services/api';
import type { Channel, ChannelGroup, Stream, M3UAccount, Logo, ChangeInfo, EPGData, StreamProfile, EPGSource } from './types';
import './App.css';

function App() {
  // Health check
  const [health, setHealth] = useState<{ status: string; service: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Channels state
  const [channels, setChannels] = useState<Channel[]>([]);
  const [channelGroups, setChannelGroups] = useState<ChannelGroup[]>([]);
  const [selectedChannel, setSelectedChannel] = useState<Channel | null>(null);
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

  // Track if baseline has been initialized
  const baselineInitialized = useRef(false);

  // Edit mode exit dialog state
  const [showExitDialog, setShowExitDialog] = useState(false);

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
    addChannelToWorkingCopy,
    getSummary,
    commit,
    discard,
    localUndo,
    localRedo,
  } = useEditMode({
    channels,
    onChannelsChange: setChannels,
    onCommitComplete: () => {
      loadChannels(); // Refresh from server
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
  }, [commit]);

  const handleDiscardChanges = useCallback(() => {
    discard();
    setShowExitDialog(false);
  }, [discard]);

  const handleKeepEditing = useCallback(() => {
    setShowExitDialog(false);
  }, []);

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

  const handleSettingsSaved = async () => {
    setError(null);
    // Reload settings to get updated auto_rename_channel_number
    try {
      const settings = await api.getSettings();
      setAutoRenameChannelNumber(settings.auto_rename_channel_number);
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

  const loadStreams = async () => {
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

  return (
    <div className="app">
      <header className="header">
        <h1>Enhanced Channel Manager</h1>
        <button className="settings-btn" onClick={() => setSettingsOpen(true)}>
          Settings
        </button>
      </header>
      {isEditMode && (
        <EditModeBanner
          stagedCount={stagedOperationCount}
          duration={editModeDuration}
        />
      )}
      <EditModeExitDialog
        isOpen={showExitDialog}
        summary={getSummary()}
        onApply={handleApplyChanges}
        onDiscard={handleDiscardChanges}
        onKeepEditing={handleKeepEditing}
        isCommitting={isCommitting}
      />
      <SettingsModal
        isOpen={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        onSaved={handleSettingsSaved}
      />
      <main className="main">
        <SplitPane
          left={
            <ChannelsPane
              channelGroups={channelGroups}
              channels={displayChannels}
              streams={streams}
              providers={providers}
              selectedChannelId={selectedChannel?.id ?? null}
              onChannelSelect={handleChannelSelect}
              onChannelUpdate={handleChannelUpdate}
              onChannelDrop={handleStreamDropOnChannel}
              onBulkStreamDrop={handleBulkStreamDropOnChannel}
              onChannelReorder={handleChannelReorder}
              onCreateChannel={handleCreateChannel}
              onDeleteChannel={handleDeleteChannel}
              searchTerm={channelSearch}
              onSearchChange={setChannelSearch}
              selectedGroups={channelGroupFilter}
              onSelectedGroupsChange={setChannelGroupFilter}
              loading={channelsLoading}
              autoRenameChannelNumber={autoRenameChannelNumber}
              isEditMode={isEditMode}
              modifiedChannelIds={modifiedChannelIds}
              onStageUpdateChannel={stageUpdateChannel}
              onStageAddStream={stageAddStream}
              onStageRemoveStream={stageRemoveStream}
              onStageReorderStreams={stageReorderStreams}
              onStageBulkAssignNumbers={stageBulkAssignNumbers}
              // Edit mode toggle props
              onEnterEditMode={enterEditMode}
              onExitEditMode={handleExitEditMode}
              isCommitting={isCommitting}
              stagedOperationCount={stagedOperationCount}
              // History toolbar props
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
              logos={logos}
              onLogosChange={loadLogos}
              // EPG and Stream Profile props
              epgData={epgData}
              epgSources={epgSources}
              streamProfiles={streamProfiles}
              epgDataLoading={epgDataLoading}
            />
          }
          right={
            <StreamsPane
              streams={filteredStreams}
              providers={providers}
              streamGroups={streamGroups}
              searchTerm={streamSearch}
              onSearchChange={setStreamSearch}
              providerFilter={streamProviderFilter}
              onProviderFilterChange={setStreamProviderFilter}
              groupFilter={streamGroupFilter}
              onGroupFilterChange={setStreamGroupFilter}
              loading={streamsLoading}
              selectedProviders={selectedProviderFilters}
              onSelectedProvidersChange={setSelectedProviderFilters}
              selectedStreamGroups={selectedStreamGroupFilters}
              onSelectedStreamGroupsChange={setSelectedStreamGroupFilters}
            />
          }
        />
      </main>
      <footer className="footer">
        {error && <span className="error">API Error: {error}</span>}
        {health && (
          <span className="status">
            API: {health.status} | Service: {health.service}
          </span>
        )}
      </footer>
    </div>
  );
}

export default App;
