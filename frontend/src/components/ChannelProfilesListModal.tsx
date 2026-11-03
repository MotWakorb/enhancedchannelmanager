import { useState, useEffect, useMemo, useCallback, useRef, memo } from 'react';
import type { ChannelProfile, Channel, ChannelGroup, StagedSideEffects } from '../types';
import { EMPTY_STAGED_SIDE_EFFECTS, profileMembershipKey } from '../types/editMode';
import * as api from '../services/api';
import { applyProfileMembership } from '../services/profileMembership';
import { useNotifications } from '../contexts/NotificationContext';
import { naturalCompare } from '../utils/naturalSort';
import { ModalOverlay } from './ModalOverlay';
import { ImmediateActionNote } from './ImmediateActionNote';
import { useOwnedDialog } from '../hooks/useOwnedDialog';
import './ChannelProfilesListModal.css';

interface ChannelProfilesListModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSaved: () => void;
  channels: Channel[];
  channelGroups: ChannelGroup[];
  /**
   * Edit Mode changes what the two channel-assignment buttons do
   * (bead enhancedchannelmanager-kz089, fix round 2). Membership is the same
   * data the selection bar stages, so it stages here too rather than through a
   * second, immediate path the change count never saw and Discard could not
   * reach. Profile create / rename / delete stays immediate per the PO's
   * 2026-08-15 decision — a staged membership operation needs a real profile id
   * to reference — and says so in the list view instead.
   */
  isEditMode?: boolean;
  stagedSideEffects?: StagedSideEffects;
  onStageSetProfileMembership?: (
    profileId: number, channelIds: number[], enabled: boolean, description: string,
  ) => void;
  onStartBatch?: (description: string) => void;
  onEndBatch?: () => void;
}

interface ProfileWithState extends ChannelProfile {
  isEditing?: boolean;
  editName?: string;
  membershipUnknown?: boolean;
}

type ViewMode = 'list' | 'channels';

export const ChannelProfilesListModal = memo(function ChannelProfilesListModal({
  isOpen,
  onClose,
  onSaved,
  channels,
  channelGroups,
  isEditMode = false,
  stagedSideEffects = EMPTY_STAGED_SIDE_EFFECTS,
  onStageSetProfileMembership,
  onStartBatch,
  onEndBatch,
}: ChannelProfilesListModalProps) {
  /** True when membership edits stage rather than write. */
  const stagesMembership = isEditMode && !!onStageSetProfileMembership;
  const { titleId, containerRef } = useOwnedDialog(isOpen);
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
  const [membershipUnknown, setMembershipUnknown] = useState(false);
  const [membershipMessage, setMembershipMessage] = useState('');
  const membershipBusy = useRef(false);

  // Separate selection from the pending toggles, sharing the same confirmed
  // readback/save path. Missing membership rows are supported too.
  const [bulkSelectedIds, setBulkSelectedIds] = useState<Set<number>>(new Set());
  const [bulkApplying, setBulkApplying] = useState(false);
  const busy = savingChannels || bulkApplying;
  const handleClose = () => { if (!membershipBusy.current) onClose(); };

  const loadProfiles = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.getChannelProfiles();
      setProfiles(data);
    } catch (err) {
      setProfiles(prev => prev.map(profile => ({ ...profile, membershipUnknown: true })));
      notifications.error(err instanceof Error ? err.message : 'Failed to load profiles', 'Profiles');
    } finally {
      setLoading(false);
    }
  }, [notifications]);

  // Fetch profiles when modal opens
  useEffect(() => {
    if (isOpen) {
      setSearch('');
      setNewProfileName('');
      setViewMode('list');
      setSelectedProfile(null);
      loadProfiles();
    }
  }, [isOpen, loadProfiles]);

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
  const handleOpenChannels = (profile: ProfileWithState) => {
    if (membershipBusy.current) return;
    setSelectedProfile(profile);
    setViewMode('channels');
    setChannelSearch('');
    setHideDisabledChannels(false);
    setChannelChanges(new Map());
    setBulkSelectedIds(new Set());
    setMembershipUnknown(profile.membershipUnknown ?? false);
    setMembershipMessage('');
  };

  const handleBackToList = () => {
    if (membershipBusy.current) return;
    setViewMode('list');
    setSelectedProfile(null);
    setChannelChanges(new Map());
    setBulkSelectedIds(new Set());
    loadProfiles(); // Refresh to get updated channel counts
  };

  // Build channel list with enabled state from profile
  // Profile.channels is the enabled-ID set. It does not enumerate disabled or
  // missing membership rows; neither makes the channel visible in this profile.
  const channelsWithState = useMemo(() => {
    if (!selectedProfile) return [];

    // Create set of enabled channel IDs
    const enabledSet = new Set(selectedProfile.channels);

    return channels.map(ch => {
      // Staged membership WINS over the server's value — that is this view's
      // working copy. Without it a staged change was counted and summarised
      // while this list carried on showing the channel as it was before, which
      // is the failure mode that makes staging worse than the immediate write
      // it replaced (bead …-kz089, fix round 2).
      const staged = stagedSideEffects.profileMembership.get(
        profileMembershipKey(selectedProfile.id, ch.id),
      );
      return {
        ...ch,
        // Channel is enabled only if explicitly in the profile's channels list
        enabled: staged !== undefined ? staged : enabledSet.has(ch.id),
      };
    });
  }, [channels, selectedProfile, stagedSideEffects]);

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
    if (membershipBusy.current) return;
    const channel = channelsWithState.find(ch => ch.id === channelId);
    if (!channel) return;
    setMembershipMessage('');

    setChannelChanges(prev => {
      const newChanges = new Map(prev);
      const currentEnabled = channel.enabled;
      const pendingChange = prev.get(channelId);

      if (pendingChange !== undefined) {
        // Unknown state may already have changed upstream. Keep reversals
        // explicit until readback can prove them satisfied.
        if (membershipUnknown && !stagesMembership) newChanges.set(channelId, !pendingChange);
        else newChanges.delete(channelId);
      } else {
        // Set opposite of current state
        newChanges.set(channelId, !currentEnabled);
      }
      return newChanges;
    });
  };

  const getChannelEnabled = useCallback((channel: { id: number; enabled: boolean }): boolean => {
    const pendingChange = channelChanges.get(channel.id);
    return pendingChange !== undefined ? pendingChange : channel.enabled;
  }, [channelChanges]);

  const handleEnableAllVisible = () => {
    if (membershipBusy.current) return;
    setMembershipMessage('');
    setChannelChanges(prev => {
      const newChanges = new Map(prev);
      for (const ch of filteredChannels) {
        if (!ch.enabled || (membershipUnknown && !stagesMembership)) {
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
    if (membershipBusy.current) return;
    setMembershipMessage('');
    setChannelChanges(prev => {
      const newChanges = new Map(prev);
      for (const ch of filteredChannels) {
        if (ch.enabled || (membershipUnknown && !stagesMembership)) {
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
    if (membershipBusy.current) return;
    setMembershipMessage('');
    setChannelChanges(prev => {
      const newChanges = new Map(prev);
      for (const ch of groupChannels) {
        if (membershipUnknown && !stagesMembership) {
          newChanges.set(ch.id, enable);
          continue;
        }
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

  const persistMembership = async (intents: Map<number, boolean>, bulk: boolean) => {
    if (!selectedProfile || membershipBusy.current) return;
    membershipBusy.current = true;
    if (bulk) setBulkApplying(true);
    else setSavingChannels(true);
    setMembershipMessage('Saving and checking profile membership...');
    try {
      const outcome = await applyProfileMembership(selectedProfile.id, intents);
      if (outcome.profile) setSelectedProfile(outcome.profile);
      setProfiles(prev => prev.map(p => p.id === selectedProfile.id
        ? { ...(outcome.profile ?? p), membershipUnknown: !outcome.verified } : p));
      // Apply to Selected supersedes only its selected IDs. Other local intents
      // are not written, and are dropped only if fresh readback satisfies them.
      setChannelChanges(prev => {
        const next = new Map(prev);
        for (const id of intents.keys()) next.delete(id);
        for (const [id, enabled] of outcome.remaining) next.set(id, enabled);
        if (outcome.verified && outcome.profile) {
          const enabledIds = new Set(outcome.profile.channels);
          for (const [id, enabled] of next) {
            if (enabledIds.has(id) === enabled) next.delete(id);
          }
        }
        return next;
      });
      setBulkSelectedIds(prev => new Set([...prev].filter(id =>
        !intents.has(id) || outcome.remaining.has(id))));
      setMembershipUnknown(!outcome.verified);
      if (!outcome.verified) {
        const message = 'Could not confirm current membership. Pending changes are retained; retry reads the profile before writing.';
        setMembershipMessage(message);
        notifications.error(message, 'Profiles');
      } else if (outcome.remaining.size > 0) {
        const message = `${outcome.remaining.size} change(s) still pending after readback. Confirmed counts are shown; retry to apply the remainder.`;
        setMembershipMessage(message);
        notifications.error(message, 'Profiles');
      } else {
        const message = outcome.failedRequests > 0
          ? 'A request failed, but readback confirmed all requested changes.'
          : 'Readback confirmed all requested changes.';
        setMembershipMessage(message);
        if (outcome.failedRequests > 0) notifications.warning(message, 'Profiles');
        else if (bulk) notifications.success(message, 'Profiles');
      }
      onSaved();
    } finally {
      membershipBusy.current = false;
      setSavingChannels(false);
      setBulkApplying(false);
    }
  };

  const handleSaveChannelChanges = async () => {
    if (!selectedProfile || channelChanges.size === 0 || membershipBusy.current) return;

    // In Edit Mode this stages, exactly as the selection bar's "Profile
    // visibility" does — same data, same wire operation, one change count
    // (bead …-kz089, fix round 2). It used to PATCH every changed membership
    // the moment Save was pressed, from inside a mode whose whole promise is
    // that nothing is real until Apply All.
    if (stagesMembership) {
      const enables = [...channelChanges.entries()].filter(([, on]) => on).map(([id]) => id);
      const disables = [...channelChanges.entries()].filter(([, on]) => !on).map(([id]) => id);
      const description =
        `Stage ${channelChanges.size} channel visibility change` +
        `${channelChanges.size !== 1 ? 's' : ''} in profile "${selectedProfile.name}"`;
      onStartBatch?.(description);
      if (enables.length > 0) {
        onStageSetProfileMembership!(selectedProfile.id, enables, true, description);
      }
      if (disables.length > 0) {
        onStageSetProfileMembership!(selectedProfile.id, disables, false, description);
      }
      onEndBatch?.();
      // The pending-diff is now represented by the staged operations, which
      // `channelsWithState` reads, so the local diff must be dropped or every
      // row would count its change twice.
      setChannelChanges(new Map());
      return;
    }

    await persistMembership(new Map(channelChanges), false);
  };

  // Bulk apply-to-selected (bead hq3de.i). Applies immediately via the bulk
  // endpoint — separate from the pending-diff "Save Changes" flow above.
  const handleBulkApply = async (enabled: boolean) => {
    if (!selectedProfile || bulkSelectedIds.size === 0 || membershipBusy.current) return;

    // Same staging rule as Save Changes above. This path used the BULK
    // endpoint, which made it invisible to the change count by a second route
    // (bead …-kz089, fix round 2).
    if (stagesMembership) {
      const channelIds = Array.from(bulkSelectedIds);
      const description =
        `${enabled ? 'Enable' : 'Disable'} ${channelIds.length} channel` +
        `${channelIds.length !== 1 ? 's' : ''} in profile "${selectedProfile.name}"`;
      onStartBatch?.(description);
      onStageSetProfileMembership!(selectedProfile.id, channelIds, enabled, description);
      onEndBatch?.();
      setBulkSelectedIds(new Set());
      return;
    }

    await persistMembership(new Map([...bulkSelectedIds].map(id => [id, enabled])), true);
  };

  const toggleBulkSelected = (channelId: number) => {
    if (membershipBusy.current) return;
    setBulkSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(channelId)) next.delete(channelId);
      else next.add(channelId);
      return next;
    });
  };

  const enabledCount = useMemo(() => {
    if (!stagesMembership) return selectedProfile?.channels.length ?? 0;
    let count = 0;
    for (const ch of channelsWithState) {
      if (getChannelEnabled(ch)) count++;
    }
    return count;
  }, [channelsWithState, getChannelEnabled, selectedProfile, stagesMembership]);

  if (!isOpen) return null;

  return (
    <ModalOverlay onClose={handleClose} role="dialog" aria-modal="true" aria-labelledby={titleId}>
      <div className="modal-container modal-lg channel-profiles-modal" ref={containerRef}>
        {viewMode === 'list' ? (
          <>
            <div className="modal-header">
              <h2 id={titleId}>Channel Profiles</h2>
              <button className="modal-close-btn" onClick={onClose} aria-label="Close" title="Close">
                <span className="material-icons" aria-hidden="true">close</span>
              </button>
            </div>

            <div className="modal-toolbar">
              {isEditMode && (
                <ImmediateActionNote
                  what="Creating, renaming and deleting a profile"
                  detail="Enabling or disabling channels in a profile does stage — open a profile to do that."
                  testId="profile-admin-immediate-note"
                />
              )}
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
                    <button className="clear-search" onClick={() => setSearch('')} aria-label="Clear search" title="Clear search">
                      <span className="material-icons" aria-hidden="true">close</span>
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
                              {profile.membershipUnknown ? '?' : profile.channels.length}
                            </span>
                          </div>
                          <div className="profile-actions">
                            {profile.isEditing ? (
                              <>
                                <button
                                  className="modal-icon-btn"
                                  onClick={() => handleSaveEdit(profile)}
                                  title="Save"
                                  aria-label="Save profile name"
                                >
                                  <span className="material-icons" aria-hidden="true">check</span>
                                </button>
                                <button
                                  className="modal-icon-btn"
                                  onClick={() => handleCancelEdit(profile.id)}
                                  title="Cancel"
                                  aria-label="Cancel rename"
                                >
                                  <span className="material-icons" aria-hidden="true">close</span>
                                </button>
                              </>
                            ) : (
                              <>
                                <button
                                  className="modal-icon-btn"
                                  onClick={() => handleOpenChannels(profile)}
                                  title="Manage channels"
                                  aria-label="Manage channels"
                                >
                                  <span className="material-icons" aria-hidden="true">tune</span>
                                </button>
                                <button
                                  className="modal-icon-btn"
                                  onClick={() => handleStartEdit(profile)}
                                  title="Rename"
                                  aria-label="Rename"
                                >
                                  <span className="material-icons" aria-hidden="true">edit</span>
                                </button>
                                <button
                                  className="modal-icon-btn danger"
                                  onClick={() => handleDeleteProfile(profile)}
                                  title="Delete"
                                  aria-label="Delete profile"
                                >
                                  <span className="material-icons" aria-hidden="true">delete</span>
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
                <button className="modal-back-btn" onClick={handleBackToList} disabled={busy} aria-label="Back to profiles list" title="Back to profiles list">
                  <span className="material-icons" aria-hidden="true">arrow_back</span>
                </button>
                <div className="modal-header-info">
                  <h2 id={titleId}>Manage Channels</h2>
                  <span className="modal-header-subtitle">{selectedProfile?.name}</span>
                </div>
              </div>
              <button className="modal-close-btn" onClick={handleClose} disabled={busy} aria-label="Close" title="Close">
                <span className="material-icons" aria-hidden="true">close</span>
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
                    <button className="clear-search" onClick={() => setChannelSearch('')} aria-label="Clear search" title="Clear search">
                      <span className="material-icons" aria-hidden="true">close</span>
                    </button>
                  )}
                </div>
                <span className="modal-toolbar-count">
                  {membershipUnknown && !stagesMembership
                    ? `Enabled count unknown (last confirmed: ${enabledCount} / ${channels.length})`
                    : `${enabledCount} / ${channels.length} enabled`}
                </span>
              </div>
              <div className="modal-toolbar-row">
                <div className="modal-toolbar-actions">
                  <button className="modal-btn-small enable" onClick={handleEnableAllVisible} disabled={busy}>
                    Enable Visible
                  </button>
                  <button className="modal-btn-small disable" onClick={handleDisableAllVisible} disabled={busy}>
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
              {!stagesMembership && (membershipMessage || channelChanges.size > 0) && (
                <div className="modal-toolbar-row" role="status">
                  <span>{membershipMessage} {channelChanges.size > 0 &&
                    `${channelChanges.size} pending change(s); toggles show desired state, counts show confirmed state.`}</span>
                </div>
              )}
              {bulkSelectedIds.size > 0 && (
                <div className="modal-toolbar-row bulk-apply-row">
                  <span className="modal-toolbar-count">{bulkSelectedIds.size} selected</span>
                  <div className="modal-toolbar-actions">
                    <button
                      className="modal-btn-small enable"
                      onClick={() => handleBulkApply(true)}
                      disabled={busy}
                      title="Apply this profile (enabled) to the selected channels"
                    >
                      {bulkApplying ? 'Applying...' : `${stagesMembership ? 'Stage' : 'Apply'} to Selected: Enable`}
                    </button>
                    <button
                      className="modal-btn-small disable"
                      onClick={() => handleBulkApply(false)}
                      disabled={busy}
                    >
                      {bulkApplying ? 'Applying...' : `${stagesMembership ? 'Stage' : 'Apply'} to Selected: Disable`}
                    </button>
                    <button
                      className="modal-btn-small"
                      onClick={() => setBulkSelectedIds(new Set())}
                      disabled={busy}
                    >
                      Clear Selection
                    </button>
                  </div>
                </div>
              )}
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
                          disabled={busy}
                          onChange={() => handleToggleGroup(groupChannels, !groupEnabled)}
                        />
                        <span className="modal-toggle-slider"></span>
                      </label>
                      <span className="group-name">{getGroupName(groupId)}</span>
                      <span className="group-channel-count">
                        {membershipUnknown && !stagesMembership ? '?' : groupChannels.filter(ch =>
                          stagesMembership ? getChannelEnabled(ch) : ch.enabled).length} / {groupChannels.length}
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
                            className={`channel-item ${isEnabled ? 'enabled' : ''} ${hasChange ? 'changed' : ''} ${bulkSelectedIds.has(channel.id) ? 'bulk-selected' : ''}`}
                            onClick={() => handleToggleChannel(channel.id)}
                          >
                            <input
                              type="checkbox"
                              className="bulk-select-checkbox"
                              disabled={busy}
                              checked={bulkSelectedIds.has(channel.id)}
                              onChange={() => toggleBulkSelected(channel.id)}
                              onClick={(e) => e.stopPropagation()}
                              aria-label={`Select ${channel.name} for bulk apply`}
                              title="Select for bulk apply"
                            />
                            <label className="modal-toggle" onClick={(e) => e.stopPropagation()}>
                              <input
                                type="checkbox"
                                checked={isEnabled}
                                disabled={busy}
                                aria-label={`Enable ${channel.name}${!stagesMembership && hasChange ? ` (pending; ${membershipUnknown ? 'current state unknown' : `confirmed ${channel.enabled ? 'enabled' : 'not enabled'}`})` : ''}`}
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
              <button className="modal-btn modal-btn-secondary" onClick={handleBackToList} disabled={busy}>
                Back
              </button>
              <button
                className="modal-btn modal-btn-primary"
                onClick={handleSaveChannelChanges}
                disabled={busy || channelChanges.size === 0}
              >
                {savingChannels
                  ? 'Saving...'
                  : `${stagesMembership ? 'Stage Changes' : 'Save Changes'}${channelChanges.size > 0 ? ` (${channelChanges.size})` : ''}`}
              </button>
            </div>
          </>
        )}
      </div>
    </ModalOverlay>
  );
});
