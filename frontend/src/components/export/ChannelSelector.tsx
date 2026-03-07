import { useState, useEffect, useCallback, useMemo } from 'react';
import type { Channel, ChannelGroup } from '../../types';
import type { SelectionMode } from '../../types/export';
import * as api from '../../services/api';

interface ChannelSelectorProps {
  selectionMode: SelectionMode;
  selectedGroups: number[];
  selectedChannels: number[];
  onGroupsChange: (groups: number[]) => void;
  onChannelsChange: (channels: number[]) => void;
}

export function ChannelSelector({
  selectionMode,
  selectedGroups,
  selectedChannels,
  onGroupsChange,
  onChannelsChange,
}: ChannelSelectorProps) {
  const [groups, setGroups] = useState<ChannelGroup[]>([]);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [g, c] = await Promise.all([
        api.getChannelGroups(),
        api.getChannels({ pageSize: 10000 }),
      ]);
      setGroups(g);
      setChannels(Array.isArray(c) ? c : []);
    } catch {
      // Errors handled by httpClient
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadData(); }, [loadData]);

  // Group channels by group
  const channelsByGroup = useMemo(() => {
    const map = new Map<number, Channel[]>();
    for (const ch of channels) {
      const gid = ch.channel_group_id ?? 0;
      if (!map.has(gid)) map.set(gid, []);
      map.get(gid)!.push(ch);
    }
    return map;
  }, [channels]);

  const filteredChannels = useMemo(() => {
    if (!search) return channels;
    const q = search.toLowerCase();
    return channels.filter(ch => ch.name.toLowerCase().includes(q));
  }, [channels, search]);

  const totalSelected = selectionMode === 'groups'
    ? groups.filter(g => selectedGroups.includes(g.id)).reduce((sum, g) => sum + (g.channel_count || 0), 0)
    : selectedChannels.length;

  if (loading) {
    return (
      <div className="channel-selector-loading">
        <span className="material-icons spinning">sync</span>
        Loading channels...
      </div>
    );
  }

  if (selectionMode === 'groups') {
    return (
      <div className="channel-selector">
        <div className="channel-selector-header">
          <span className="channel-selector-count">{totalSelected} channels in {selectedGroups.length} groups</span>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => onGroupsChange(selectedGroups.length === groups.length ? [] : groups.map(g => g.id))}
          >
            {selectedGroups.length === groups.length ? 'Deselect All' : 'Select All'}
          </button>
        </div>
        <div className="channel-selector-list">
          {groups.map(group => (
            <label key={group.id} className="channel-selector-item">
              <input
                type="checkbox"
                checked={selectedGroups.includes(group.id)}
                onChange={(e) => {
                  if (e.target.checked) {
                    onGroupsChange([...selectedGroups, group.id]);
                  } else {
                    onGroupsChange(selectedGroups.filter(id => id !== group.id));
                  }
                }}
              />
              <span className="channel-selector-item-name">{group.name}</span>
              <span className="channel-selector-item-count">{group.channel_count || 0} ch</span>
            </label>
          ))}
        </div>
      </div>
    );
  }

  // Channel mode
  const groupMap = new Map(groups.map(g => [g.id, g.name]));

  return (
    <div className="channel-selector">
      <div className="channel-selector-header">
        <input
          type="text"
          className="channel-selector-search"
          placeholder="Search channels..."
          value={search}
          onChange={e => setSearch(e.target.value)}
        />
        <span className="channel-selector-count">{selectedChannels.length} selected</span>
        <button
          type="button"
          className="btn btn-sm"
          onClick={() => onChannelsChange(
            selectedChannels.length === channels.length ? [] : channels.map(c => c.id)
          )}
        >
          {selectedChannels.length === channels.length ? 'Deselect All' : 'Select All'}
        </button>
      </div>
      <div className="channel-selector-list">
        {filteredChannels.map(ch => (
          <label key={ch.id} className="channel-selector-item">
            <input
              type="checkbox"
              checked={selectedChannels.includes(ch.id)}
              onChange={(e) => {
                if (e.target.checked) {
                  onChannelsChange([...selectedChannels, ch.id]);
                } else {
                  onChannelsChange(selectedChannels.filter(id => id !== ch.id));
                }
              }}
            />
            <span className="channel-selector-item-number">{ch.channel_number ?? '—'}</span>
            <span className="channel-selector-item-name">{ch.name}</span>
            <span className="channel-selector-item-group">
              {groupMap.get(ch.channel_group_id ?? 0) || 'Ungrouped'}
            </span>
          </label>
        ))}
      </div>
    </div>
  );
}
