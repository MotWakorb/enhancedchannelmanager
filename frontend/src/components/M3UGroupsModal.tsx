import { useState, useEffect, useMemo } from 'react';
import type { M3UAccount, ChannelGroupM3UAccount, ChannelGroup } from '../types';
import * as api from '../services/api';
import { naturalCompare } from '../utils/naturalSort';
import './M3UGroupsModal.css';

interface M3UGroupsModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSaved: () => void;
  account: M3UAccount;
  allAccounts?: M3UAccount[];         // All M3U accounts for cascading to linked accounts
  linkedAccountGroups?: number[][];   // Link groups from settings
}

// Extended type with name from channel groups lookup
interface GroupWithName extends ChannelGroupM3UAccount {
  name: string;
}

export function M3UGroupsModal({
  isOpen,
  onClose,
  onSaved,
  account,
  allAccounts = [],
  linkedAccountGroups = [],
}: M3UGroupsModalProps) {
  const [groups, setGroups] = useState<GroupWithName[]>([]);
  const [search, setSearch] = useState('');
  const [hideDisabled, setHideDisabled] = useState(false);
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasChanges, setHasChanges] = useState(false);

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

  // Fetch fresh account data and channel groups when modal opens
  useEffect(() => {
    if (isOpen && account) {
      setSearch('');
      setError(null);
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
          setError(err instanceof Error ? err.message : 'Failed to load group data');
          // Fall back to showing groups from prop without names
          setGroups(account.channel_groups.map(g => ({
            ...g,
            name: `Group ${g.channel_group}`,
          })));
        })
        .finally(() => setLoading(false));
    }
  }, [isOpen, account?.id]);

  // Filter and sort groups by search and hideDisabled
  const filteredGroups = useMemo(() => {
    let filtered = groups;

    // Filter by hideDisabled
    if (hideDisabled) {
      filtered = filtered.filter(g => g.enabled);
    }

    // Filter by search
    if (search.trim()) {
      const searchLower = search.toLowerCase();
      filtered = filtered.filter(g => g.name.toLowerCase().includes(searchLower));
    }

    // Sort alphabetically with natural sort
    return [...filtered].sort((a, b) => naturalCompare(a.name, b.name));
  }, [groups, search, hideDisabled]);

  const handleToggleEnabled = (groupId: number) => {
    setGroups(prev => prev.map(g =>
      g.channel_group === groupId ? { ...g, enabled: !g.enabled } : g
    ));
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

  const handleSave = async () => {
    setSaving(true);
    setError(null);

    try {
      // Build settings for this account
      // Include id field - Dispatcharr needs this to identify the relationship record
      const groupSettings = groups.map(g => ({
        id: g.id,
        channel_group: g.channel_group,
        enabled: g.enabled,
        auto_channel_sync: g.auto_channel_sync,
        auto_sync_channel_start: g.auto_sync_channel_start,
      }));

      // Save this account first
      await api.updateM3UGroupSettings(account.id, { group_settings: groupSettings });

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

            // Build settings for linked account - match by channel_group ID
            const linkedSettings = linkedAccount.channel_groups.map(lg => {
              // Look up by channel_group ID (the group ID is shared across M3U accounts)
              const matchEnabled = groupEnabledById.get(lg.channel_group);
              return {
                channel_group: lg.channel_group,
                enabled: matchEnabled !== undefined ? matchEnabled : lg.enabled,  // Use this account's setting if matched
                auto_channel_sync: lg.auto_channel_sync,  // Keep linked account's own value
                auto_sync_channel_start: lg.auto_sync_channel_start,  // Keep linked account's own value
              };
            });

            await api.updateM3UGroupSettings(linkedAccountId, { group_settings: linkedSettings });
          } catch (linkedErr) {
            // Log error but continue with other linked accounts
            console.error(`Failed to update linked account ${linkedAccountId}:`, linkedErr);
          }
        }
      }

      onSaved();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save group settings');
    } finally {
      setSaving(false);
    }
  };

  const enabledCount = groups.filter(g => g.enabled).length;

  if (!isOpen) return null;

  return (
    <div className="modal-overlay">
      <div className="modal-content m3u-groups-modal" onClick={(e) => e.stopPropagation()}>
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
          <button className="close-btn" onClick={onClose}>
            &times;
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
              <button className="clear-search" onClick={() => setSearch('')}>
                <span className="material-icons">close</span>
              </button>
            )}
          </div>
          <div className="toolbar-actions">
            <span className="group-count">{enabledCount} / {groups.length} enabled</span>
            <button className="btn-small" onClick={handleEnableAll}>Enable All</button>
            <button className="btn-small" onClick={handleDisableAll}>Disable All</button>
          </div>
        </div>

        <div className="modal-body">
          {loading ? (
            <div className="loading-state">
              <span className="material-icons spinning">sync</span>
              <p>Loading groups...</p>
            </div>
          ) : filteredGroups.length === 0 ? (
            <div className="empty-state">
              {search ? (
                <p>No groups match "{search}"</p>
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
                <span className="col-start">Start Channel</span>
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
                    <label className="toggle">
                      <input
                        type="checkbox"
                        checked={group.auto_channel_sync}
                        onChange={() => handleToggleAutoSync(group.channel_group)}
                        disabled={!group.enabled}
                      />
                      <span className="toggle-slider"></span>
                    </label>
                  </div>
                  <div className="group-start">
                    <input
                      type="number"
                      min="1"
                      placeholder="--"
                      value={group.auto_sync_channel_start ?? ''}
                      onChange={(e) => handleStartChannelChange(group.channel_group, e.target.value)}
                      disabled={!group.auto_channel_sync}
                    />
                  </div>
                </div>
              ))}
            </div>
          )}

          {error && <div className="error-message">{error}</div>}
        </div>

        <div className="modal-footer">
          <label className="hide-disabled-checkbox">
            <input
              type="checkbox"
              checked={hideDisabled}
              onChange={(e) => setHideDisabled(e.target.checked)}
            />
            <span>Hide disabled</span>
          </label>
          <div className="footer-buttons">
            <button className="btn-secondary" onClick={onClose} disabled={saving}>
              Cancel
            </button>
            <button className="btn-primary" onClick={handleSave} disabled={saving || !hasChanges}>
              {saving ? 'Saving...' : 'Save Changes'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
