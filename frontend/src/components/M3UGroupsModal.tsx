import { logger } from '../utils/logger';
import { useState, useEffect, useMemo, memo } from 'react';
import type { M3UAccount, ChannelGroupM3UAccount, ChannelGroup, AutoSyncCustomProperties, ChannelProfile, StreamProfile, EPGSource } from '../types';
import * as api from '../services/api';
import { useNotifications } from '../contexts/NotificationContext';
import { naturalCompare } from '../utils/naturalSort';
import { AutoSyncSettingsModal } from './AutoSyncSettingsModal';
import { ModalOverlay } from './ModalOverlay';
import './ModalBase.css';
import './M3UGroupsModal.css';

interface M3UGroupsModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSaved: () => void;
  account: M3UAccount;
  allAccounts?: M3UAccount[];         // All M3U accounts for cascading to linked accounts
  linkedAccountGroups?: number[][];   // Link groups from settings
  // For auto-sync settings modal
  epgSources?: EPGSource[];
  channelGroups?: ChannelGroup[];
  channelProfiles?: ChannelProfile[];
  streamProfiles?: StreamProfile[];
  onChannelGroupsChange?: () => void; // Called when channel groups are created/changed
  // bd-dgs64 (GH #591): admin-only global setting (settings.allow_multi_provider_auto_sync).
  // When true, a channel group already auto-synced by another M3U account is
  // NOT locked here — the toggle/Start#/Settings stay usable, with a
  // shared-ownership indicator instead of the "owned by" lock. Default false
  // preserves the original single-owner guard from commit 030c1ef8.
  allowMultiProviderAutoSync?: boolean;
}

// Extended type with name from channel groups lookup
interface GroupWithName extends ChannelGroupM3UAccount {
  name: string;
}

export const M3UGroupsModal = memo(function M3UGroupsModal({
  isOpen,
  onClose,
  onSaved,
  account,
  allAccounts = [],
  linkedAccountGroups = [],
  epgSources = [],
  channelGroups: allChannelGroups = [],
  channelProfiles = [],
  streamProfiles = [],
  onChannelGroupsChange,
  allowMultiProviderAutoSync = false,
}: M3UGroupsModalProps) {
  const notifications = useNotifications();
  const [groups, setGroups] = useState<GroupWithName[]>([]);
  const [search, setSearch] = useState('');
  const [hideDisabled, setHideDisabled] = useState(false);
  const [showOnlyAutoSync, setShowOnlyAutoSync] = useState(false);
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(false);
  const [hasChanges, setHasChanges] = useState(false);
  // Auto-sync settings modal state
  const [settingsModalGroup, setSettingsModalGroup] = useState<GroupWithName | null>(null);

  // Find linked accounts for this account
  const linkedAccountInfo = useMemo(() => {
    // Find the link group containing this account
    const linkGroup = linkedAccountGroups.find(group => group.includes(account.id));
    if (!linkGroup) return { isLinked: false, linkedAccountIds: [], linkedAccountNames: [] };

    // Get the other account IDs in this group
    const linkedAccountIds = linkGroup.filter(id => id !== account.id);
    const linkedAccountNames = linkedAccountIds.map(id => {
      const acc = allAccounts.find(a => a.id === id);
      return acc?.name ?? `Account ${id}`;
    });

    return { isLinked: true, linkedAccountIds, linkedAccountNames };
  }, [account.id, linkedAccountGroups, allAccounts]);

  // Find groups that are already auto-synced on OTHER accounts
  // These should not be allowed to have auto-sync enabled on this account
  const autoSyncedByOtherAccounts = useMemo(() => {
    const result = new Map<number, string>(); // channel_group ID -> account name that owns it

    for (const otherAccount of allAccounts) {
      if (otherAccount.id === account.id) continue; // Skip current account

      for (const group of otherAccount.channel_groups) {
        if (group.auto_channel_sync) {
          result.set(group.channel_group, otherAccount.name);
        }
      }
    }

    return result;
  }, [allAccounts, account.id]);

  // Fetch fresh account data and channel groups when modal opens
  useEffect(() => {
    if (isOpen && account) {
      setSearch('');
      setHasChanges(false);
      setLoading(true);

      // Fetch both fresh account data AND channel groups to get names
      // This ensures we always have the latest state from the server
      Promise.all([
        api.getM3UAccount(account.id),
        api.getChannelGroups(),
      ])
        .then(([freshAccount, channelGroups]: [typeof account, ChannelGroup[]]) => {
          // Create a map of channel_group ID -> name
          const nameMap = new Map<number, string>();
          channelGroups.forEach(g => nameMap.set(g.id, g.name));

          // Merge names into fresh account's channel_groups
          const groupsWithNames: GroupWithName[] = freshAccount.channel_groups.map(g => ({
            ...g,
            name: nameMap.get(g.channel_group) || `Group ${g.channel_group}`,
          }));

          setGroups(groupsWithNames);
        })
        .catch(err => {
          notifications.error(err instanceof Error ? err.message : 'Failed to load group data', 'M3U Groups');
          // Fall back to showing groups from prop without names
          setGroups(account.channel_groups.map(g => ({
            ...g,
            name: `Group ${g.channel_group}`,
          })));
        })
        .finally(() => setLoading(false));
    }
  }, [isOpen, account, notifications]);

  // Filter and sort groups by search, hideDisabled, and showOnlyAutoSync
  const filteredGroups = useMemo(() => {
    let filtered = groups;

    // Filter by hideDisabled
    if (hideDisabled) {
      filtered = filtered.filter(g => g.enabled);
    }

    // Filter by showOnlyAutoSync
    if (showOnlyAutoSync) {
      filtered = filtered.filter(g => g.auto_channel_sync);
    }

    // Filter by search
    if (search.trim()) {
      const searchLower = search.toLowerCase();
      filtered = filtered.filter(g => g.name.toLowerCase().includes(searchLower));
    }

    // Sort alphabetically with natural sort
    return [...filtered].sort((a, b) => naturalCompare(a.name, b.name));
  }, [groups, search, hideDisabled, showOnlyAutoSync]);

  const handleToggleEnabled = (groupId: number) => {
    setGroups(prev => prev.map(g => {
      if (g.channel_group !== groupId) return g;
      const newEnabled = !g.enabled;
      // If disabling the group, also disable auto-sync
      return {
        ...g,
        enabled: newEnabled,
        auto_channel_sync: newEnabled ? g.auto_channel_sync : false,
      };
    }));
    setHasChanges(true);
  };

  const handleToggleAutoSync = (groupId: number) => {
    setGroups(prev => prev.map(g =>
      g.channel_group === groupId ? { ...g, auto_channel_sync: !g.auto_channel_sync } : g
    ));
    setHasChanges(true);
  };

  const handleStartChannelChange = (groupId: number, value: string) => {
    const numValue = value === '' ? null : parseInt(value, 10);
    setGroups(prev => prev.map(g =>
      g.channel_group === groupId ? { ...g, auto_sync_channel_start: numValue } : g
    ));
    setHasChanges(true);
  };

  const handleEnableAll = () => {
    setGroups(prev => prev.map(g => ({ ...g, enabled: true })));
    setHasChanges(true);
  };

  const handleDisableAll = () => {
    setGroups(prev => prev.map(g => ({ ...g, enabled: false })));
    setHasChanges(true);
  };

  // Handle auto-sync settings save
  const handleAutoSyncSettingsSave = (groupId: number, customProperties: AutoSyncCustomProperties) => {
    setGroups(prev => prev.map(g =>
      g.channel_group === groupId
        ? { ...g, custom_properties: Object.keys(customProperties).length > 0 ? customProperties : null }
        : g
    ));
    setHasChanges(true);
  };

  // Check if a group has custom properties configured
  const hasCustomProperties = (group: GroupWithName): boolean => {
    if (!group.custom_properties) return false;
    return Object.keys(group.custom_properties).some(key => {
      const value = group.custom_properties?.[key as keyof AutoSyncCustomProperties];
      if (Array.isArray(value)) return value.length > 0;
      return value !== undefined && value !== null && value !== '';
    });
  };

  const handleSave = async () => {
    setSaving(true);

    try {
      // Build settings for this account.
      // Include id field - Dispatcharr needs this to identify the relationship record.
      // Dispatcharr's group-settings upsert is FULL-ROW (omitted fields are
      // reset to defaults), so every row must carry the complete field set —
      // including auto_sync_channel_end and custom_properties verbatim
      // (bead enhancedchannelmanager-igqcy).
      const groupSettings = groups.map(g => ({
        id: g.id,
        channel_group: g.channel_group,
        enabled: g.enabled,
        auto_channel_sync: g.auto_channel_sync,
        auto_sync_channel_start: g.auto_sync_channel_start,
        auto_sync_channel_end: g.auto_sync_channel_end,
        custom_properties: g.custom_properties,
      }));

      // Save this account first. Capture the response so we can warn if the
      // downstream channel-profile apply was incomplete (#9).
      const primaryResp = await api.updateM3UGroupSettings(account.id, { group_settings: groupSettings });
      // Accumulate the apply summary across EVERY save path (primary + linked)
      // so a partial/conflict/degraded apply from any of them is surfaced with
      // status-specific recovery guidance (#9 / Should-Fix 4).
      const applySummary = [...(primaryResp?.ecm_profile_apply ?? [])];
      // Linked-account SAVE failures tracked separately from profile-apply
      // outcomes so they are labeled correctly (finding), not as apply errors.
      const linkedSaveFailures: number[] = [];

      // Accounts to refresh after a successful save (Dispatcharr parity:
      // its modal's only save action is Save & Refresh — settings take
      // effect only when the M3U refreshes).
      const refreshAccountIds: number[] = [account.id];

      // Cascade to linked accounts if any
      if (linkedAccountInfo.isLinked && linkedAccountInfo.linkedAccountIds.length > 0) {
        // Build a map of channel_group ID -> enabled state from this account's settings
        // Use channel_group (the ID) for matching since linked accounts share the same group IDs
        const groupEnabledById = new Map<number, boolean>();
        groups.forEach(g => groupEnabledById.set(g.channel_group, g.enabled));

        // Update each linked account
        for (const linkedAccountId of linkedAccountInfo.linkedAccountIds) {
          try {
            // Fetch the linked account's current groups
            const linkedAccount = await api.getM3UAccount(linkedAccountId);

            // Build settings for linked account - match by channel_group ID.
            // Full rows: only `enabled` is overlaid; every other field is the
            // linked account's own current value, passed through verbatim.
            const linkedSettings = linkedAccount.channel_groups.map(lg => {
              // Look up by channel_group ID (the group ID is shared across M3U accounts)
              const matchEnabled = groupEnabledById.get(lg.channel_group);
              return {
                channel_group: lg.channel_group,
                enabled: matchEnabled !== undefined ? matchEnabled : lg.enabled,  // Use this account's setting if matched
                auto_channel_sync: lg.auto_channel_sync,  // Keep linked account's own value
                auto_sync_channel_start: lg.auto_sync_channel_start,  // Keep linked account's own value
                auto_sync_channel_end: lg.auto_sync_channel_end,  // Keep linked account's own value
                custom_properties: lg.custom_properties,  // Keep linked account's own value
              };
            });

            const linkedResp = await api.updateM3UGroupSettings(linkedAccountId, { group_settings: linkedSettings });
            applySummary.push(...(linkedResp?.ecm_profile_apply ?? []));
            refreshAccountIds.push(linkedAccountId);
          } catch (linkedErr) {
            // Finding: a linked-account SAVE failure is a save failure, NOT a
            // profile-application failure — track it distinctly (do not fold it
            // into the profile-apply summary, which would mislabel it).
            logger.error(`Failed to update linked account ${linkedAccountId}:`, linkedErr);
            linkedSaveFailures.push(linkedAccountId);
          }
        }
      }

      // Chain the M3U refresh (Save & Refresh, mirroring Dispatcharr's
      // native modal). Only fires after a successful save; a refresh
      // failure does not undo the save.
      const applyWarning = api.profileApplyWarningMessage(applySummary);
      const linkedWarning = linkedSaveFailures.length
        ? `Saved, but ${linkedSaveFailures.length} linked account(s) could not be saved — retry from those accounts.`
        : null;
      // Finding: surface EVERY present warning rather than letting one hide
      // another (a refresh failure must not mask an incomplete-apply warning,
      // and vice versa). Returns true if any warning was emitted.
      const emitWarnings = (refreshWarning?: string | null): boolean => {
        const msgs = [refreshWarning, applyWarning, linkedWarning].filter(Boolean) as string[];
        msgs.forEach(m => notifications.warning(m, 'M3U Groups'));
        return msgs.length > 0;
      };
      try {
        await Promise.all(refreshAccountIds.map(id => api.refreshM3UAccount(id)));
        if (!emitWarnings()) {
          notifications.success(
            `Group settings saved — M3U refresh started for ${account.name}`,
            'M3U Groups'
          );
        }
      } catch (refreshErr) {
        logger.error('Failed to start M3U refresh after group settings save:', refreshErr);
        emitWarnings(
          'Group settings saved, but the M3U refresh failed to start — changes take effect on the next refresh'
        );
      }

      onSaved();
      onClose();
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Failed to save group settings', 'M3U Groups');
    } finally {
      setSaving(false);
    }
  };

  const enabledCount = groups.filter(g => g.enabled).length;

  // bd 09x38.7: X and Escape both route through this shared close-request
  // handler. Dirty state (hasChanges) guards against silently discarding
  // unsaved toggles — the modal has no Cancel button, so X/Escape were the
  // only ways to lose a batch of pending changes with zero confirmation.
  // Outside-click close does not exist for this modal (ModalOverlay disables
  // backdrop-click-to-close unless an `onClick` handler is passed in, and none
  // is here), so there's no third path to guard.
  const handleRequestClose = () => {
    if (hasChanges && !window.confirm('Discard unsaved changes?')) {
      return;
    }
    onClose();
  };

  if (!isOpen) return null;

  return (
    <ModalOverlay onClose={handleRequestClose}>
      <div className="modal-container modal-lg m3u-groups-modal" style={{ height: '80vh', minHeight: '80vh', maxHeight: '80vh' }}>
        <div className="modal-header">
          <div className="header-info">
            <h2>Manage Groups</h2>
            <span className="account-name">{account.name}</span>
            {linkedAccountInfo.isLinked && (
              <span className="linked-info">
                <span className="material-icons">link</span>
                Linked with: {linkedAccountInfo.linkedAccountNames.join(', ')}
              </span>
            )}
          </div>
          <button className="modal-close-btn" onClick={handleRequestClose} aria-label="Close" title="Close">
            <span className="material-icons" aria-hidden="true">close</span>
          </button>
        </div>

        <div className="modal-toolbar">
          <div className="search-box">
            <span className="material-icons">search</span>
            <input
              type="text"
              placeholder="Search groups..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            {search && (
              <button className="clear-search" onClick={() => setSearch('')} aria-label="Clear search" title="Clear search">
                <span className="material-icons" aria-hidden="true">close</span>
              </button>
            )}
          </div>
          <div className="toolbar-actions">
            <span className="group-count">{enabledCount} / {groups.length} enabled</span>
            <div className="toolbar-buttons">
              <button className="btn-small" onClick={handleEnableAll}>Enable All</button>
              <button className="btn-small" onClick={handleDisableAll}>Disable All</button>
            </div>
          </div>
        </div>

        <div className="modal-body">
          {loading ? (
            <div className="modal-loading">
              <span className="material-icons">sync</span>
              <p>Loading groups...</p>
            </div>
          ) : filteredGroups.length === 0 ? (
            <div className="modal-empty-state">
              {search ? (
                <p>No groups match "{search}"</p>
              ) : showOnlyAutoSync ? (
                <p>No auto-sync groups. Uncheck "Auto-sync only" to see all groups.</p>
              ) : hideDisabled ? (
                <p>No enabled groups. Uncheck "Hide disabled" to see all groups.</p>
              ) : (
                <p>No groups available for this account.</p>
              )}
            </div>
          ) : (
            <div className="groups-list">
              <div className="groups-header">
                <span className="col-name">Group Name</span>
                <span className="col-enabled">Enabled</span>
                <span className="col-autosync">Auto-Sync</span>
                <span className="col-start">Start #</span>
                <span className="col-settings">Settings</span>
              </div>
              {filteredGroups.map(group => (
                <div key={group.channel_group} className="group-row">
                  <div className="group-name" title={group.name}>
                    {group.name}
                  </div>
                  <div className="group-enabled">
                    <label className="toggle">
                      <input
                        type="checkbox"
                        checked={group.enabled}
                        onChange={() => handleToggleEnabled(group.channel_group)}
                      />
                      <span className="toggle-slider"></span>
                    </label>
                  </div>
                  <div className="group-autosync">
                    {autoSyncedByOtherAccounts.has(group.channel_group) && !allowMultiProviderAutoSync ? (
                      <div className="autosync-owned" title={`Auto-synced by: ${autoSyncedByOtherAccounts.get(group.channel_group)}`}>
                        <span className="material-icons">link</span>
                        <span className="owned-text">{autoSyncedByOtherAccounts.get(group.channel_group)}</span>
                      </div>
                    ) : (
                      <div className="autosync-toggle-wrapper">
                        <label className="toggle">
                          <input
                            type="checkbox"
                            checked={group.auto_channel_sync}
                            onChange={() => handleToggleAutoSync(group.channel_group)}
                            disabled={!group.enabled}
                          />
                          <span className="toggle-slider"></span>
                        </label>
                        {autoSyncedByOtherAccounts.has(group.channel_group) && (
                          <span
                            className="material-icons autosync-shared-indicator"
                            title={`Also auto-synced by: ${autoSyncedByOtherAccounts.get(group.channel_group)} — may create duplicate channels`}
                          >
                            link
                          </span>
                        )}
                      </div>
                    )}
                  </div>
                  <div className="group-start">
                    <input
                      type="number"
                      min="1"
                      placeholder="--"
                      value={group.auto_sync_channel_start ?? ''}
                      onChange={(e) => handleStartChannelChange(group.channel_group, e.target.value)}
                      disabled={!group.auto_channel_sync || (autoSyncedByOtherAccounts.has(group.channel_group) && !allowMultiProviderAutoSync)}
                    />
                  </div>
                  <div className="group-settings">
                    <button
                      className={`settings-btn ${hasCustomProperties(group) ? 'has-settings' : ''}`}
                      onClick={() => setSettingsModalGroup(group)}
                      disabled={!group.auto_channel_sync || (autoSyncedByOtherAccounts.has(group.channel_group) && !allowMultiProviderAutoSync)}
                      title={
                        autoSyncedByOtherAccounts.has(group.channel_group) && !allowMultiProviderAutoSync
                          ? `Auto-synced by: ${autoSyncedByOtherAccounts.get(group.channel_group)}`
                          : autoSyncedByOtherAccounts.has(group.channel_group)
                            ? `Also auto-synced by: ${autoSyncedByOtherAccounts.get(group.channel_group)} — may create duplicate channels`
                            : group.auto_channel_sync
                              ? 'Configure auto-sync settings'
                              : group.enabled
                                ? 'Turn on Auto-Sync to configure settings'
                                : 'Enable this group and turn on Auto-Sync to configure settings'
                      }
                      aria-label={
                        autoSyncedByOtherAccounts.has(group.channel_group) && !allowMultiProviderAutoSync
                          ? `Auto-synced by: ${autoSyncedByOtherAccounts.get(group.channel_group)}`
                          : autoSyncedByOtherAccounts.has(group.channel_group)
                            ? `Also auto-synced by: ${autoSyncedByOtherAccounts.get(group.channel_group)} — may create duplicate channels`
                            : group.auto_channel_sync
                              ? 'Configure auto-sync settings'
                              : group.enabled
                                ? 'Turn on Auto-Sync to configure settings'
                                : 'Enable this group and turn on Auto-Sync to configure settings'
                      }
                    >
                      <span className="material-icons" aria-hidden="true">settings</span>
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}

        </div>

        <div className="modal-footer">
          <div className="footer-filters">
            <label className="filter-checkbox">
              <input
                type="checkbox"
                checked={hideDisabled}
                onChange={(e) => setHideDisabled(e.target.checked)}
              />
              <span>Hide disabled</span>
            </label>
            <label className="filter-checkbox">
              <input
                type="checkbox"
                checked={showOnlyAutoSync}
                onChange={(e) => setShowOnlyAutoSync(e.target.checked)}
              />
              <span>Auto-sync only</span>
            </label>
          </div>
          <button
            className="modal-btn modal-btn-primary"
            onClick={handleSave}
            disabled={saving || !hasChanges}
            title="Save group settings and refresh this M3U account so they take effect"
          >
            {saving ? 'Saving...' : 'Save & Refresh'}
          </button>
        </div>
      </div>

      {/* Auto-Sync Settings Modal */}
      {settingsModalGroup && (
        <AutoSyncSettingsModal
          isOpen={true}
          onClose={() => setSettingsModalGroup(null)}
          onSave={(customProperties) => {
            handleAutoSyncSettingsSave(settingsModalGroup.channel_group, customProperties);
            setSettingsModalGroup(null);
          }}
          groupName={settingsModalGroup.name}
          customProperties={settingsModalGroup.custom_properties}
          epgSources={epgSources}
          channelGroups={allChannelGroups}
          channelProfiles={channelProfiles}
          streamProfiles={streamProfiles}
          onGroupsChange={onChannelGroupsChange}
        />
      )}
    </ModalOverlay>
  );
});
