import { useState, useEffect, useCallback, memo } from 'react';
import type { Channel, ChannelGroup, DummyEPGChannelAssignment } from '../types';
import * as api from '../services/api';
import { ModalOverlay } from './ModalOverlay';
import { CustomSelect } from './CustomSelect';
import { useNotifications } from '../contexts/NotificationContext';
import './ModalBase.css';
import './DummyEPGChannelPicker.css';

interface DummyEPGChannelPickerProps {
  isOpen: boolean;
  profileId: number;
  profileName: string;
  onClose: () => void;
  onChanged: () => void;
}

export const DummyEPGChannelPicker = memo(function DummyEPGChannelPicker({
  isOpen,
  profileId,
  profileName,
  onClose,
  onChanged,
}: DummyEPGChannelPickerProps) {
  const notifications = useNotifications();
  const [channels, setChannels] = useState<Channel[]>([]);
  const [groups, setGroups] = useState<ChannelGroup[]>([]);
  const [assignments, setAssignments] = useState<DummyEPGChannelAssignment[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [groupFilter, setGroupFilter] = useState<string>('all');
  const [adding, setAdding] = useState(false);

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [channelData, groupData, assignmentData] = await Promise.all([
        api.getChannels({ pageSize: 10000 }),
        api.getChannelGroups(),
        api.getDummyEPGChannels(profileId),
      ]);
      setChannels(channelData.results);
      setGroups(groupData);
      setAssignments(assignmentData);
    } catch (err) {
      notifications.error('Failed to load channel data', 'Channel Picker');
    } finally {
      setLoading(false);
    }
  }, [profileId]);

  useEffect(() => {
    if (isOpen) {
      loadData();
      setSearch('');
      setGroupFilter('all');
    }
  }, [isOpen, loadData]);

  const assignedChannelIds = new Set(assignments.map(a => a.channel_id));

  const availableChannels = channels
    .filter(ch => !assignedChannelIds.has(ch.id))
    .filter(ch => {
      if (groupFilter !== 'all') {
        if (ch.channel_group_id !== Number(groupFilter)) return false;
      }
      if (search) {
        const term = search.toLowerCase();
        return ch.name.toLowerCase().includes(term) ||
               String(ch.channel_number || '').includes(term);
      }
      return true;
    })
    .sort((a, b) => (a.channel_number || 0) - (b.channel_number || 0));

  const handleAssignChannels = useCallback(async (channelIds: number[]) => {
    if (channelIds.length === 0) return;
    setAdding(true);
    try {
      const result = await api.assignDummyEPGChannels(profileId, channelIds);
      notifications.success(`Added ${result.created} channel(s)`, 'Channel Picker');
      await loadData();
      onChanged();
    } catch (err) {
      notifications.error('Failed to assign channels', 'Channel Picker');
    } finally {
      setAdding(false);
    }
  }, [profileId, loadData, onChanged]);

  const handleRemoveChannel = useCallback(async (channelId: number) => {
    try {
      await api.removeDummyEPGChannel(profileId, channelId);
      setAssignments(prev => prev.filter(a => a.channel_id !== channelId));
      onChanged();
    } catch (err) {
      notifications.error('Failed to remove channel', 'Channel Picker');
    }
  }, [profileId, onChanged]);

  const handleBulkFromGroup = useCallback(async (groupId: number) => {
    setAdding(true);
    try {
      const result = await api.assignDummyEPGChannelsFromGroup(profileId, groupId);
      notifications.success(`Added ${result.created} channel(s) from group`, 'Channel Picker');
      await loadData();
      onChanged();
    } catch (err) {
      notifications.error('Failed to add channels from group', 'Channel Picker');
    } finally {
      setAdding(false);
    }
  }, [profileId, loadData, onChanged]);

  const [selectedAvailable, setSelectedAvailable] = useState<Set<number>>(new Set());

  const toggleAvailableSelection = (channelId: number) => {
    setSelectedAvailable(prev => {
      const next = new Set(prev);
      if (next.has(channelId)) next.delete(channelId);
      else next.add(channelId);
      return next;
    });
  };

  const handleAddSelected = () => {
    handleAssignChannels(Array.from(selectedAvailable));
    setSelectedAvailable(new Set());
  };

  if (!isOpen) return null;

  const groupOptions = [
    { value: 'all', label: 'All Groups' },
    ...groups.map(g => ({ value: String(g.id), label: `${g.name} (${g.channel_count})` })),
  ];

  return (
    <ModalOverlay onClose={onClose}>
      <div className="modal-container modal-xl channel-picker-modal">
        <div className="modal-header">
          <h2>Channels - {profileName}</h2>
          <button className="modal-close-btn" onClick={onClose}>
            <span className="material-icons">close</span>
          </button>
        </div>

        {loading ? (
          <div className="modal-body modal-loading">
            <span className="material-icons spinning">sync</span>
            <p>Loading channels...</p>
          </div>
        ) : (
          <div className="channel-picker-body">
            {/* Assigned channels */}
            <div className="channel-picker-panel">
              <div className="channel-picker-panel-header">
                <h3>Assigned ({assignments.length})</h3>
              </div>
              <div className="channel-picker-list">
                {assignments.length === 0 ? (
                  <p className="channel-picker-empty">No channels assigned to this profile.</p>
                ) : (
                  assignments
                    .sort((a, b) => a.channel_name.localeCompare(b.channel_name))
                    .map(assignment => (
                    <div key={assignment.id} className="channel-picker-item assigned">
                      <span className="channel-picker-name">{assignment.channel_name}</span>
                      <button
                        className="channel-picker-remove"
                        onClick={() => handleRemoveChannel(assignment.channel_id)}
                        title="Remove"
                      >
                        <span className="material-icons">remove_circle_outline</span>
                      </button>
                    </div>
                  ))
                )}
              </div>
            </div>

            {/* Available channels */}
            <div className="channel-picker-panel">
              <div className="channel-picker-panel-header">
                <h3>Available ({availableChannels.length})</h3>
                <div className="channel-picker-controls">
                  <div className="channel-picker-search">
                    <span className="material-icons">search</span>
                    <input
                      type="text"
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                      placeholder="Search channels..."
                    />
                    {search && (
                      <button className="clear-search" onClick={() => setSearch('')}>
                        <span className="material-icons">close</span>
                      </button>
                    )}
                  </div>
                  <div className="channel-picker-group-filter">
                    <CustomSelect
                      value={groupFilter}
                      onChange={setGroupFilter}
                      options={groupOptions}
                    />
                  </div>
                </div>
              </div>
              <div className="channel-picker-actions-bar">
                {selectedAvailable.size > 0 && (
                  <button
                    className="modal-btn modal-btn-primary"
                    onClick={handleAddSelected}
                    disabled={adding}
                    style={{ fontSize: '0.8rem', padding: '0.375rem 0.75rem' }}
                  >
                    Add {selectedAvailable.size} Selected
                  </button>
                )}
                {groupFilter !== 'all' && (
                  <button
                    className="modal-btn modal-btn-secondary"
                    onClick={() => handleBulkFromGroup(Number(groupFilter))}
                    disabled={adding}
                    style={{ fontSize: '0.8rem', padding: '0.375rem 0.75rem' }}
                  >
                    Add All from Group
                  </button>
                )}
              </div>
              <div className="channel-picker-list">
                {availableChannels.length === 0 ? (
                  <p className="channel-picker-empty">
                    {search || groupFilter !== 'all' ? 'No matching channels found.' : 'All channels are already assigned.'}
                  </p>
                ) : (
                  availableChannels.map(channel => (
                    <div
                      key={channel.id}
                      className={`channel-picker-item available ${selectedAvailable.has(channel.id) ? 'selected' : ''}`}
                      onClick={() => toggleAvailableSelection(channel.id)}
                    >
                      <input
                        type="checkbox"
                        checked={selectedAvailable.has(channel.id)}
                        onChange={() => toggleAvailableSelection(channel.id)}
                        onClick={(e) => e.stopPropagation()}
                      />
                      <span className="channel-picker-number">{channel.channel_number || '-'}</span>
                      <span className="channel-picker-name">{channel.name}</span>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>
        )}

        <div className="modal-footer">
          <button className="modal-btn modal-btn-secondary" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </ModalOverlay>
  );
});
