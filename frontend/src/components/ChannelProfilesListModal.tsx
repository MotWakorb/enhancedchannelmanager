import { useState, useEffect, useMemo, memo } from 'react';
import type { ChannelProfile, Channel, ChannelGroup } from '../types';
import * as api from '../services/api';
import { useNotifications } from '../contexts/NotificationContext';
import { naturalCompare } from '../utils/naturalSort';
import { ModalOverlay } from './ModalOverlay';
import './ChannelProfilesListModal.css';

interface ChannelProfilesListModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSaved: () => void;
  channels: Channel[];
  channelGroups: ChannelGroup[];
}

interface ProfileWithState extends ChannelProfile {
  isEditing?: boolean;
  editName?: string;
}

type ViewMode = 'list' | 'channels';

export const ChannelProfilesListModal = memo(function ChannelProfilesListModal({
  isOpen,
  onClose,
  onSaved,
  channels,
  channelGroups,
}: ChannelProfilesListModalProps) {
  const notifications = useNotifications();
  const [profiles, setProfiles] = useState<ProfileWithState[]>([]);
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(false);
  const [newProfileName, setNewProfileName] = useState('');
  const [isCreating, setIsCreating] = useState(false);

  // View mode: list (profile CRUD) or channels (channel assignment)
  const [viewMode, setViewMode] = useState<ViewMode>('list');
  const [selectedProfile, setSelectedProfile] = useState<ChannelProfile | null>(null);

  // Channel assignment state
  const [channelSearch, setChannelSearch] = useState('');
  const [hideDisabledChannels, setHideDisabledChannels] = useState(false);
  const [channelChanges, setChannelChanges] = useState<Map<number, boolean>>(new Map());
  const [savingChannels, setSavingChannels] = useState(false);

  // Fetch profiles when modal opens
  useEffect(() => {
    if (isOpen) {
      setSearch('');
      setNewProfileName('');
      setViewMode('list');
      setSelectedProfile(null);
      loadProfiles();
    }
  }, [isOpen]);

  const loadProfiles = async () => {
    setLoading(true);
    try {
      const data = await api.getChannelProfiles();
      setProfiles(data);
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Failed to load profiles', 'Profiles');
    } finally {
      setLoading(false);
    }
  };

  // Filter profiles by search
  const filteredProfiles = useMemo(() => {
    if (!search.trim()) return profiles;
    const searchLower = search.toLowerCase();
    return profiles.filter(p => p.name.toLowerCase().includes(searchLower));
  }, [profiles, search]);

  // Handle profile CRUD
  const handleCreateProfile = async () => {
    if (!newProfileName.trim()) return;
    setIsCreating(true);
    try {
      const newProfile = await api.createChannelProfile({ name: newProfileName.trim() });
      setProfiles(prev => [...prev, newProfile]);
      setNewProfileName('');
      onSaved();
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Failed to create profile', 'Profiles');
    } finally {
      setIsCreating(false);
    }
  };

  const handleStartEdit = (profile: ProfileWithState) => {
    setProfiles(prev => prev.map(p =>
      p.id === profile.id ? { ...p, isEditing: true, editName: p.name } : { ...p, isEditing: false }
    ));
  };

  const handleCancelEdit = (profileId: number) => {
    setProfiles(prev => prev.map(p =>
      p.id === profileId ? { ...p, isEditing: false, editName: undefined } : p
    ));
  };

  const handleEditNameChange = (profileId: number, value: string) => {
    setProfiles(prev => prev.map(p =>
      p.id === profileId ? { ...p, editName: value } : p
    ));
  };

  const handleSaveEdit = async (profile: ProfileWithState) => {
    if (!profile.editName?.trim() || profile.editName.trim() === profile.name) {
      handleCancelEdit(profile.id);
      return;
    }
    try {
      const updated = await api.updateChannelProfile(profile.id, { name: profile.editName.trim() });
      setProfiles(prev => prev.map(p =>
        p.id === profile.id ? { ...updated, isEditing: false, editName: undefined } : p
      ));
      onSaved();
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Failed to update profile', 'Profiles');
    }
  };

  const handleDeleteProfile = async (profile: ChannelProfile) => {
    if (!confirm(`Delete profile "${profile.name}"? This cannot be undone.`)) return;
    try {
      await api.deleteChannelProfile(profile.id);
      setProfiles(prev => prev.filter(p => p.id !== profile.id));
      onSaved();
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Failed to delete profile', 'Profiles');
    }
  };

  // Channel assignment view
  const handleOpenChannels = (profile: ChannelProfile) => {
    setSelectedProfile(profile);
    setViewMode('channels');
    setChannelSearch('');
    setHideDisabledChannels(false);
    setChannelChanges(new Map());
  };

  const handleBackToList = () => {
    setViewMode('list');
    setSelectedProfile(null);
    setChannelChanges(new Map());
    loadProfiles(); // Refresh to get updated channel counts
  };

  // Build channel list with enabled state from profile
  // NOTE: Dispatcharr uses ChannelProfileMembership records to track channel-profile relationships
  // - Empty channels array = no membership records exist (no channels assigned to profile)
  // - Non-empty array = only those channels have membership records with enabled=true
  const channelsWithState = useMemo(() => {
    if (!selectedProfile) return [];

    // Create set of enabled channel IDs
    const enabledSet = new Set(selectedProfile.channels);

    return channels.map(ch => ({
      ...ch,
      // Channel is enabled only if explicitly in the profile's channels list
      enabled: enabledSet.has(ch.id),
    }));
  }, [channels, selectedProfile]);

  // Filter channels
  const filteredChannels = useMemo(() => {
    let filtered = channelsWithState;

    if (hideDisabledChannels) {
      // Show only channels that are enabled OR have pending changes to be enabled
      filtered = filtered.filter(ch => {
        const pendingChange = channelChanges.get(ch.id);
        return pendingChange === true || (pendingChange === undefined && ch.enabled);
      });
    }

    if (channelSearch.trim()) {
      const searchLower = channelSearch.toLowerCase();
      filtered = filtered.filter(ch => ch.name.toLowerCase().includes(searchLower));
    }

    return filtered;
  }, [channelsWithState, channelSearch, hideDisabledChannels, channelChanges]);

  // Group channels by channel group
  const groupedChannels = useMemo(() => {
    const groups = new Map<number | null, typeof filteredChannels>();
    for (const ch of filteredChannels) {
      const groupId = ch.channel_group_id;
      if (!groups.has(groupId)) {
        groups.set(groupId, []);
      }
      groups.get(groupId)!.push(ch);
    }
    return groups;
  }, [filteredChannels]);

  const getGroupName = (groupId: number | null): string => {
    if (groupId === null) return 'Ungrouped';
    return channelGroups.find(g => g.id === groupId)?.name || `Group ${groupId}`;
  };

  const handleToggleChannel = (channelId: number) => {
    const channel = channelsWithState.find(ch => ch.id === channelId);
    if (!channel) return;

    setChannelChanges(prev => {
      const newChanges = new Map(prev);
      const currentEnabled = channel.enabled;
      const pendingChange = prev.get(channelId);

      if (pendingChange !== undefined) {
        // Toggle back to original state
        newChanges.delete(channelId);
      } else {
        // Set opposite of current state
        newChanges.set(channelId, !currentEnabled);
      }
      return newChanges;
    });
  };

  const getChannelEnabled = (channel: { id: number; enabled: boolean }): boolean => {
    const pendingChange = channelChanges.get(channel.id);
    return pendingChange !== undefined ? pendingChange : channel.enabled;
  };

  const handleEnableAllVisible = () => {
    setChannelChanges(prev => {
      const newChanges = new Map(prev);
      for (const ch of filteredChannels) {
        if (!ch.enabled) {
          newChanges.set(ch.id, true);
        } else {
          // Remove any pending disable
          if (newChanges.get(ch.id) === false) {
            newChanges.delete(ch.id);
          }
        }
      }
      return newChanges;
    });
  };

  const handleDisableAllVisible = () => {
    setChannelChanges(prev => {
      const newChanges = new Map(prev);
      for (const ch of filteredChannels) {
        if (ch.enabled) {
          newChanges.set(ch.id, false);
        } else {
          // Remove any pending enable
          if (newChanges.get(ch.id) === true) {
            newChanges.delete(ch.id);
          }
        }
      }
      return newChanges;
    });
  };

  // Toggle all channels in a group
  const handleToggleGroup = (groupChannels: typeof channelsWithState, enable: boolean) => {
    setChannelChanges(prev => {
      const newChanges = new Map(prev);
      for (const ch of groupChannels) {
        if (enable) {
          // Enable: if currently disabled, add change; if already enabled, remove any pending disable
          if (!ch.enabled) {
            newChanges.set(ch.id, true);
          } else if (newChanges.get(ch.id) === false) {
            newChanges.delete(ch.id);
          }
        } else {
          // Disable: if currently enabled, add change; if already disabled, remove any pending enable
          if (ch.enabled) {
            newChanges.set(ch.id, false);
          } else if (newChanges.get(ch.id) === true) {
            newChanges.delete(ch.id);
          }
        }
      }
      return newChanges;
    });
  };

  // Check if all channels in a group are enabled
  const isGroupEnabled = (groupChannels: typeof channelsWithState): boolean => {
    return groupChannels.every(ch => getChannelEnabled(ch));
  };

  const handleSaveChannelChanges = async () => {
    if (!selectedProfile || channelChanges.size === 0) return;

    setSavingChannels(true);

    try {
      // Use individual channel updates to ensure membership records are created
      // (Dispatcharr's bulk API only updates existing records, doesn't create new ones)
      const updatePromises = Array.from(channelChanges.entries()).map(
        ([channelId, enabled]) =>
          api.updateProfileChannel(selectedProfile.id, channelId, { enabled })
      );

      // Run updates in parallel for performance
      await Promise.all(updatePromises);

      // Refresh the profile to get updated channel list
      const updated = await api.getChannelProfile(selectedProfile.id);
      setSelectedProfile(updated);
      setChannelChanges(new Map());
      onSaved();
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Failed to save channel changes', 'Profiles');
    } finally {
      setSavingChannels(false);
    }
  };

  const enabledCount = useMemo(() => {
    let count = 0;
    for (const ch of channelsWithState) {
      if (getChannelEnabled(ch)) count++;
    }
    return count;
  }, [channelsWithState, channelChanges]);

  if (!isOpen) return null;

  return (
    <ModalOverlay onClose={onClose}>
      <div className="modal-container modal-lg channel-profiles-modal">
        {viewMode === 'list' ? (
          <>
            <div className="modal-header">
              <h2>Channel Profiles</h2>
              <button className="modal-close-btn" onClick={onClose}>
                <span className="material-icons">close</span>
              </button>
            </div>

            <div className="modal-toolbar">
              <div className="modal-toolbar-row">
                <div className="modal-search-box">
                  <span className="material-icons">search</span>
                  <input
                    type="text"
                    placeholder="Search profiles..."
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                  />
                  {search && (
                    <button className="clear-search" onClick={() => setSearch('')}>
                      <span className="material-icons">close</span>
                    </button>
                  )}
                </div>
                <span className="modal-toolbar-count">{profiles.length} profile{profiles.length !== 1 ? 's' : ''}</span>
              </div>
              <div className="modal-toolbar-row create-row">
                <input
                  type="text"
                  className="create-input"
                  placeholder="New profile name..."
                  value={newProfileName}
                  onChange={(e) => setNewProfileName(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleCreateProfile()}
                />
                <button
                  className="modal-btn-small create-btn"
                  onClick={handleCreateProfile}
                  disabled={!newProfileName.trim() || isCreating}
                >
                  {isCreating ? 'Creating...' : 'Create'}
                </button>
              </div>
            </div>

            <div className="modal-body">
              {loading ? (
                <div className="modal-loading">
                  <span className="material-icons">sync</span>
                  <p>Loading profiles...</p>
                </div>
              ) : (
                <>
                  {/* Profile list */}
                  {filteredProfiles.length === 0 ? (
                    <div className="modal-empty-state">
                      {search ? (
                        <p>No profiles match "{search}"</p>
                      ) : (
                        <p>No profiles yet. Create one using the field above.</p>
                      )}
                    </div>
                  ) : (
                    <div className="profiles-list">
                      {filteredProfiles.map(profile => (
                        <div key={profile.id} className="profile-row">
                          <div className="profile-name">
                            {profile.isEditing ? (
                              <input
                                type="text"
                                value={profile.editName || ''}
                                onChange={(e) => handleEditNameChange(profile.id, e.target.value)}
                                onKeyDown={(e) => {
                                  if (e.key === 'Enter') handleSaveEdit(profile);
                                  if (e.key === 'Escape') handleCancelEdit(profile.id);
                                }}
                                autoFocus
                              />
                            ) : (
                              <span
                                className="name-text"
                                onClick={() => handleOpenChannels(profile)}
                                title="Click to manage channels"
                              >
                                {profile.name}
                              </span>
                            )}
                          </div>
                          <div className="profile-channels">
                            <span
                              className="channel-count"
                              onClick={() => handleOpenChannels(profile)}
                              title="Click to manage channels"
                            >
                              {profile.channels.length}
                            </span>
                          </div>
                          <div className="profile-actions">
                            {profile.isEditing ? (
                              <>
                                <button
                                  className="modal-icon-btn"
                                  onClick={() => handleSaveEdit(profile)}
                                  title="Save"
                                >
                                  <span className="material-icons">check</span>
                                </button>
                                <button
                                  className="modal-icon-btn"
                                  onClick={() => handleCancelEdit(profile.id)}
                                  title="Cancel"
                                >
                                  <span className="material-icons">close</span>
                                </button>
                              </>
                            ) : (
                              <>
                                <button
                                  className="modal-icon-btn"
                                  onClick={() => handleOpenChannels(profile)}
                                  title="Manage channels"
                                >
                                  <span className="material-icons">tune</span>
                                </button>
                                <button
                                  className="modal-icon-btn"
                                  onClick={() => handleStartEdit(profile)}
                                  title="Rename"
                                >
                                  <span className="material-icons">edit</span>
                                </button>
                                <button
                                  className="modal-icon-btn danger"
                                  onClick={() => handleDeleteProfile(profile)}
                                  title="Delete"
                                >
                                  <span className="material-icons">delete</span>
                                </button>
                              </>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </>
              )}

            </div>

            <div className="modal-footer">
            </div>
          </>
        ) : (
          <>
            {/* Channel assignment view */}
            <div className="modal-header">
              <div className="modal-header-with-back">
                <button className="modal-back-btn" onClick={handleBackToList}>
                  <span className="material-icons">arrow_back</span>
                </button>
                <div className="modal-header-info">
                  <h2>Manage Channels</h2>
                  <span className="modal-header-subtitle">{selectedProfile?.name}</span>
                </div>
              </div>
              <button className="modal-close-btn" onClick={onClose}>
                <span className="material-icons">close</span>
              </button>
            </div>

            <div className="modal-toolbar">
              <div className="modal-toolbar-row">
                <div className="modal-search-box">
                  <span className="material-icons">search</span>
                  <input
                    type="text"
                    placeholder="Search channels..."
                    value={channelSearch}
                    onChange={(e) => setChannelSearch(e.target.value)}
                  />
                  {channelSearch && (
                    <button className="clear-search" onClick={() => setChannelSearch('')}>
                      <span className="material-icons">close</span>
                    </button>
                  )}
                </div>
                <span className="modal-toolbar-count">{enabledCount} / {channels.length} enabled</span>
              </div>
              <div className="modal-toolbar-row">
                <div className="modal-toolbar-actions">
                  <button className="modal-btn-small enable" onClick={handleEnableAllVisible}>
                    Enable Visible
                  </button>
                  <button className="modal-btn-small disable" onClick={handleDisableAllVisible}>
                    Disable Visible
                  </button>
                </div>
                <label className="hide-disabled-checkbox">
                  <input
                    type="checkbox"
                    checked={hideDisabledChannels}
                    onChange={(e) => setHideDisabledChannels(e.target.checked)}
                  />
                  <span>Hide disabled</span>
                </label>
              </div>
            </div>

            <div className="modal-body channels-view">
              {Array.from(groupedChannels.entries())
                .sort((a, b) => {
                  // Sort by lowest channel number in each group
                  const aMin = Math.min(...a[1].map(ch => ch.channel_number ?? Infinity));
                  const bMin = Math.min(...b[1].map(ch => ch.channel_number ?? Infinity));
                  if (aMin !== bMin) return aMin - bMin;
                  // Fall back to group name if same channel numbers
                  return getGroupName(a[0]).localeCompare(getGroupName(b[0]));
                })
                .map(([groupId, groupChannels]) => {
                  const groupEnabled = isGroupEnabled(groupChannels);
                  return (
                  <div key={groupId ?? 'ungrouped'} className="channel-group-section">
                    <div className="channel-group-header">
                      <label className="modal-toggle" onClick={(e) => e.stopPropagation()}>
                        <input
                          type="checkbox"
                          checked={groupEnabled}
                          onChange={() => handleToggleGroup(groupChannels, !groupEnabled)}
                        />
                        <span className="modal-toggle-slider"></span>
                      </label>
                      <span className="group-name">{getGroupName(groupId)}</span>
                      <span className="group-channel-count">
                        {groupChannels.filter(ch => getChannelEnabled(ch)).length} / {groupChannels.length}
                      </span>
                    </div>
                    <div className="channel-list">
                      {[...groupChannels].sort((a, b) => {
                        const aNum = a.channel_number ?? Infinity;
                        const bNum = b.channel_number ?? Infinity;
                        if (aNum !== bNum) return aNum - bNum;
                        return naturalCompare(a.name, b.name);
                      }).map(channel => {
                        const isEnabled = getChannelEnabled(channel);
                        const hasChange = channelChanges.has(channel.id);
                        return (
                          <div
                            key={channel.id}
                            className={`channel-item ${isEnabled ? 'enabled' : ''} ${hasChange ? 'changed' : ''}`}
                            onClick={() => handleToggleChannel(channel.id)}
                          >
                            <label className="modal-toggle" onClick={(e) => e.stopPropagation()}>
                              <input
                                type="checkbox"
                                checked={isEnabled}
                                onChange={() => handleToggleChannel(channel.id)}
                              />
                              <span className="modal-toggle-slider"></span>
                            </label>
                            <span className="channel-number">
                              {channel.channel_number ?? '--'}
                            </span>
                            <span className="channel-name" title={channel.name}>
                              {channel.name}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                  );
                })}
            </div>

            <div className="modal-footer">
              <button className="modal-btn modal-btn-secondary" onClick={handleBackToList} disabled={savingChannels}>
                Back
              </button>
              <button
                className="modal-btn modal-btn-primary"
                onClick={handleSaveChannelChanges}
                disabled={savingChannels || channelChanges.size === 0}
              >
                {savingChannels ? 'Saving...' : `Save Changes${channelChanges.size > 0 ? ` (${channelChanges.size})` : ''}`}
              </button>
            </div>
          </>
        )}
      </div>
    </ModalOverlay>
  );
});
