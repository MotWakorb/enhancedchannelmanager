import { useState, useEffect, useMemo } from 'react';
import type { M3UAccount, ChannelGroupM3UAccount } from '../types';
import * as api from '../services/api';
import './M3UGroupsModal.css';

interface M3UGroupsModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSaved: () => void;
  account: M3UAccount;
}

export function M3UGroupsModal({
  isOpen,
  onClose,
  onSaved,
  account,
}: M3UGroupsModalProps) {
  const [groups, setGroups] = useState<ChannelGroupM3UAccount[]>([]);
  const [search, setSearch] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasChanges, setHasChanges] = useState(false);

  // Reset state when modal opens
  useEffect(() => {
    if (isOpen) {
      setGroups([...account.channel_groups]);
      setSearch('');
      setError(null);
      setHasChanges(false);
    }
  }, [isOpen, account]);

  // Filter groups by search
  const filteredGroups = useMemo(() => {
    if (!search.trim()) return groups;
    const searchLower = search.toLowerCase();
    return groups.filter(g =>
      g.channel_group_name.toLowerCase().includes(searchLower)
    );
  }, [groups, search]);

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

      await api.updateM3UGroupSettings(account.id, { channel_groups: groupSettings });
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
    <div className="modal-overlay" onClick={onClose}>
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
          {filteredGroups.length === 0 ? (
            <div className="empty-state">
              {search ? (
                <p>No groups match "{search}"</p>
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
                  <div className="group-name" title={group.channel_group_name}>
                    {group.channel_group_name}
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
          <button className="btn-secondary" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button className="btn-primary" onClick={handleSave} disabled={saving || !hasChanges}>
            {saving ? 'Saving...' : 'Save Changes'}
          </button>
        </div>
      </div>
    </div>
  );
}
