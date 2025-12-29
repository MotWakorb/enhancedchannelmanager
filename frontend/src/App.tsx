import { useState, useEffect, useCallback } from 'react';
import { ChannelsPane, StreamsPane, SettingsModal, SplitPane } from './components';
import * as api from './services/api';
import type { Channel, ChannelGroup, Stream, M3UAccount } from './types';
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

  // Settings state
  const [settingsOpen, setSettingsOpen] = useState(false);

  // Check settings and load initial data
  useEffect(() => {
    const init = async () => {
      try {
        const settings = await api.getSettings();

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
      } catch (err) {
        console.error('Failed to load settings:', err);
        setSettingsOpen(true);
      }
    };
    init();
  }, []);

  const handleSettingsSaved = () => {
    setError(null);
    // Reload all data after settings change
    api.getHealth()
      .then(setHealth)
      .catch((err) => setError(err.message));
    loadChannelGroups();
    loadChannels();
    loadProviders();
    loadStreamGroups();
    loadStreams();
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

  const handleChannelSelect = (channel: Channel | null) => {
    setSelectedChannel(channel);
  };

  const handleChannelUpdate = useCallback((updatedChannel: Channel) => {
    setChannels((prev) =>
      prev.map((ch) => (ch.id === updatedChannel.id ? updatedChannel : ch))
    );
    if (selectedChannel?.id === updatedChannel.id) {
      setSelectedChannel(updatedChannel);
    }
  }, [selectedChannel]);

  const handleStreamDropOnChannel = useCallback(
    async (channelId: number, streamId: number) => {
      try {
        const updatedChannel = await api.addStreamToChannel(channelId, streamId);
        handleChannelUpdate(updatedChannel);
      } catch (err) {
        console.error('Failed to add stream to channel:', err);
        setError('Failed to add stream to channel');
      }
    },
    [handleChannelUpdate]
  );

  const handleBulkStreamDropOnChannel = useCallback(
    async (channelId: number, streamIds: number[]) => {
      try {
        // Add streams sequentially to maintain order
        let updatedChannel: Channel | null = null;
        for (const streamId of streamIds) {
          updatedChannel = await api.addStreamToChannel(channelId, streamId);
        }
        if (updatedChannel) {
          handleChannelUpdate(updatedChannel);
        }
      } catch (err) {
        console.error('Failed to add streams to channel:', err);
        setError('Failed to add streams to channel');
        // Reload to get current state
        loadChannels();
      }
    },
    [handleChannelUpdate]
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
        return newChannel;
      } catch (err) {
        console.error('Failed to create channel:', err);
        setError('Failed to create channel');
        throw err;
      }
    },
    []
  );

  const handleChannelReorder = useCallback(
    async (channelIds: number[], startingNumber: number) => {
      try {
        await api.bulkAssignChannelNumbers(channelIds, startingNumber);
        // Reload channels to get updated numbers from server
        loadChannels();
      } catch (err) {
        console.error('Failed to reorder channels:', err);
        setError('Failed to reorder channels');
        // Reload to revert optimistic update
        loadChannels();
      }
    },
    []
  );

  return (
    <div className="app">
      <header className="header">
        <h1>Enhanced Channel Manager</h1>
        <button className="settings-btn" onClick={() => setSettingsOpen(true)}>
          Settings
        </button>
      </header>
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
              channels={channels}
              providers={providers}
              selectedChannelId={selectedChannel?.id ?? null}
              onChannelSelect={handleChannelSelect}
              onChannelUpdate={handleChannelUpdate}
              onChannelDrop={handleStreamDropOnChannel}
              onBulkStreamDrop={handleBulkStreamDropOnChannel}
              onChannelReorder={handleChannelReorder}
              onCreateChannel={handleCreateChannel}
              searchTerm={channelSearch}
              onSearchChange={setChannelSearch}
              selectedGroups={channelGroupFilter}
              onSelectedGroupsChange={setChannelGroupFilter}
              loading={channelsLoading}
            />
          }
          right={
            <StreamsPane
              streams={streams}
              providers={providers}
              streamGroups={streamGroups}
              searchTerm={streamSearch}
              onSearchChange={setStreamSearch}
              providerFilter={streamProviderFilter}
              onProviderFilterChange={setStreamProviderFilter}
              groupFilter={streamGroupFilter}
              onGroupFilterChange={setStreamGroupFilter}
              loading={streamsLoading}
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
