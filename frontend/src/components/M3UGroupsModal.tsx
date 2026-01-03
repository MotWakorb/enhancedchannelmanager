import { useState, useEffect, useMemo } from 'react';
import type { M3UAccount, ChannelGroupM3UAccount, ChannelGroup } from '../types';
import * as api from '../services/api';
import './M3UGroupsModal.css';

interface M3UGroupsModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSaved: () => void;
  account: M3UAccount;
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
}: M3UGroupsModalProps) {
  const [groups, setGroups] = useState<GroupWithName[]>([]);
  const [search, setSearch] = useState('');
  const [hideDisabled, setHideDisabled] = useState(false);
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasChanges, setHasChanges] = useState(false);

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

  // Filter groups by search and hideDisabled
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

    return filtered;
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
      // Only send the fields that are editable
      const groupSettings = groups.map(g => ({
        channel_group: g.channel_group,
        enabled: g.enabled,
        auto_channel_sync: g.auto_channel_sync,
        auto_sync_channel_start: g.auto_sync_channel_start,
      }));

      await api.updateM3UGroupSettings(account.id, { group_settings: groupSettings });
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
