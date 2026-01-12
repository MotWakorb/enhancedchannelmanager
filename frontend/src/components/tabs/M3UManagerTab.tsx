import { useState, useEffect, useCallback, useMemo } from 'react';
import type { M3UAccount, ServerGroup, EPGSource, ChannelGroup, ChannelProfile, StreamProfile } from '../../types';
import * as api from '../../services/api';
import { naturalCompare } from '../../utils/naturalSort';
import { M3UAccountModal } from '../M3UAccountModal';
import { M3UGroupsModal } from '../M3UGroupsModal';
import { M3UFiltersModal } from '../M3UFiltersModal';
import { M3ULinkedAccountsModal } from '../M3ULinkedAccountsModal';
import { M3UProfileModal } from '../M3UProfileModal';
import './M3UManagerTab.css';

interface M3UManagerTabProps {
  epgSources?: EPGSource[];
  channelGroups?: ChannelGroup[];
  channelProfiles?: ChannelProfile[];
  streamProfiles?: StreamProfile[];
  onChannelGroupsChange?: () => void;
}

interface M3UAccountRowProps {
  account: M3UAccount;
  onEdit: (account: M3UAccount) => void;
  onDelete: (account: M3UAccount) => void;
  onRefresh: (account: M3UAccount) => void;
  onToggleActive: (account: M3UAccount) => void;
  onManageGroups: (account: M3UAccount) => void;
  onManageFilters: (account: M3UAccount) => void;
  onManageProfiles: (account: M3UAccount) => void;
  linkedAccountNames?: string[];  // Names of accounts linked to this one
}

function M3UAccountRow({
  account,
  onEdit,
  onDelete,
  onRefresh,
  onToggleActive,
  onManageGroups,
  onManageFilters,
  onManageProfiles,
  linkedAccountNames,
}: M3UAccountRowProps) {
  const getStatusIcon = (status: M3UAccount['status']) => {
    switch (status) {
      case 'success': return 'check_circle';
      case 'error': return 'error';
      case 'fetching': return 'cloud_download';
      case 'parsing': return 'hourglass_empty';
      case 'disabled': return 'block';
      case 'pending_setup': return 'pending';
      default: return 'schedule';
    }
  };

  const getStatusClass = (status: M3UAccount['status']) => {
    switch (status) {
      case 'success': return 'status-success';
      case 'error': return 'status-error';
      case 'fetching':
      case 'parsing': return 'status-pending';
      case 'disabled': return 'status-disabled';
      case 'pending_setup': return 'status-pending';
      default: return 'status-idle';
    }
  };

  const getStatusLabel = (status: M3UAccount['status']) => {
    switch (status) {
      case 'success': return 'Ready';
      case 'error': return 'Error';
      case 'fetching': return 'Downloading...';
      case 'parsing': return 'Processing...';
      case 'disabled': return 'Disabled';
      case 'pending_setup': return 'Pending Setup';
      default: return 'Idle';
    }
  };

  const isRefreshing = account.status === 'fetching' || account.status === 'parsing';

  const getAccountTypeLabel = (type: M3UAccount['account_type']) => {
    return type === 'XC' ? 'XtreamCodes' : 'Standard M3U';
  };

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return 'Never';
    const date = new Date(dateStr);
    return date.toLocaleString();
  };

  // Count enabled groups and auto-sync groups
  const enabledGroupCount = account.channel_groups.filter(g => g.enabled).length;
  const totalGroupCount = account.channel_groups.length;
  const autoSyncGroupCount = account.channel_groups.filter(g => g.auto_channel_sync).length;

  return (
    <div className={`m3u-account-row ${!account.is_active ? 'inactive' : ''}`}>
      <div className={`account-status ${getStatusClass(account.status)}`} title={account.last_message || ''}>
        <span className={`material-icons ${isRefreshing ? 'spinning' : ''}`}>
          {getStatusIcon(account.status)}
        </span>
        <span className="status-label">{getStatusLabel(account.status)}</span>
      </div>

      <div className="account-info">
        <div className="account-name">
          {linkedAccountNames && linkedAccountNames.length > 0 && (
            <span
              className="link-indicator"
              title={`Linked with: ${linkedAccountNames.join(', ')}`}
            >
              <span className="material-icons">link</span>
            </span>
          )}
          {account.name}
          {account.profiles && account.profiles.length > 1 && (
            <span
              className="profile-indicator"
              title={`${account.profiles.length - 1} additional profile${account.profiles.length - 1 !== 1 ? 's' : ''} configured${account.profiles.filter((p, i) => i > 0 && p.is_active).length > 0 ? ` (${account.profiles.filter((p, i) => i > 0 && p.is_active).length} active)` : ''}`}
            >
              <span className="material-icons">account_circle</span>
              <span className="profile-count">{account.profiles.length - 1}</span>
            </span>
          )}
        </div>
        <div className="account-details">
          <span className={`account-type ${account.account_type.toLowerCase()}`}>
            {getAccountTypeLabel(account.account_type)}
          </span>
          {account.server_url && (
            <span className="account-url" title={account.server_url}>
              {account.server_url}
            </span>
          )}
        </div>
        {account.last_message && account.status === 'error' && (
          <div className="account-message" title={account.last_message}>
            {account.last_message}
          </div>
        )}
      </div>

      <div className="account-groups">
        <span className="group-count" title={`${enabledGroupCount} enabled out of ${totalGroupCount} total groups`}>
          {enabledGroupCount} / {totalGroupCount} groups
        </span>
        {autoSyncGroupCount > 0 && (
          <span className="auto-sync-count" title={`${autoSyncGroupCount} groups set to auto-sync`}>
            <span className="material-icons">sync</span>
            {autoSyncGroupCount} auto-sync
          </span>
        )}
      </div>

      <div className="account-settings">
        <span className="setting-item">
          Streams: {account.max_streams === 0 ? 'Unlimited' : account.max_streams}
        </span>
        <span className="setting-item">
          Refresh: {account.refresh_interval === 0 ? 'Disabled' : `${account.refresh_interval}h`}
        </span>
      </div>

      <div className="account-updated">
        <span className="updated-label">Updated:</span>
        <span className="updated-time">{formatDate(account.updated_at)}</span>
      </div>

      <div className="account-actions">
        <button
          className="action-btn"
          onClick={() => onToggleActive(account)}
          title={account.is_active ? 'Disable' : 'Enable'}
        >
          <span className="material-icons">
            {account.is_active ? 'toggle_on' : 'toggle_off'}
          </span>
        </button>
        <button
          className="action-btn"
          onClick={() => onRefresh(account)}
          title="Refresh"
          disabled={!account.is_active || isRefreshing || account.locked}
        >
          <span className="material-icons">refresh</span>
        </button>
        <button
          className="action-btn"
          onClick={() => onManageGroups(account)}
          title="Manage Groups"
        >
          <span className="material-icons">folder</span>
        </button>
        <button
          className="action-btn"
          onClick={() => onManageProfiles(account)}
          title="Manage Profiles"
        >
          <span className="material-icons">account_circle</span>
        </button>
        <button
          className="action-btn"
          onClick={() => onManageFilters(account)}
          title="Manage Filters"
        >
          <span className="material-icons">filter_alt</span>
        </button>
        <button
          className="action-btn"
          onClick={() => onEdit(account)}
          title="Edit"
          disabled={account.locked}
        >
          <span className="material-icons">edit</span>
        </button>
        <button
          className="action-btn delete"
          onClick={() => onDelete(account)}
          title="Delete"
          disabled={account.locked}
        >
          <span className="material-icons">delete</span>
        </button>
      </div>
    </div>
  );
}

export function M3UManagerTab({
  epgSources = [],
  channelGroups = [],
  channelProfiles = [],
  streamProfiles = [],
  onChannelGroupsChange,
}: M3UManagerTabProps) {
  const [accounts, setAccounts] = useState<M3UAccount[]>([]);
  const [serverGroups, setServerGroups] = useState<ServerGroup[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingAccount, setEditingAccount] = useState<M3UAccount | null>(null);
  const [refreshingAll, setRefreshingAll] = useState(false);
  const [filterServerGroup, setFilterServerGroup] = useState<number | null>(null);
  const [groupsModalOpen, setGroupsModalOpen] = useState(false);
  const [groupsAccount, setGroupsAccount] = useState<M3UAccount | null>(null);
  const [filtersModalOpen, setFiltersModalOpen] = useState(false);
  const [filtersAccount, setFiltersAccount] = useState<M3UAccount | null>(null);
  const [profilesModalOpen, setProfilesModalOpen] = useState(false);
  const [profilesAccount, setProfilesAccount] = useState<M3UAccount | null>(null);
  const [linkedAccountsModalOpen, setLinkedAccountsModalOpen] = useState(false);
  const [linkedM3UAccounts, setLinkedM3UAccounts] = useState<number[][]>([]);
  const [syncingGroups, setSyncingGroups] = useState(false);

  const loadData = useCallback(async () => {
    try {
      const [accountsData, serverGroupsData, settings] = await Promise.all([
        api.getM3UAccounts(),
        api.getServerGroups(),
        api.getSettings(),
      ]);
      setAccounts(accountsData);
      setServerGroups(serverGroupsData);
      setLinkedM3UAccounts(settings.linked_m3u_accounts ?? []);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load M3U accounts');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleAddAccount = () => {
    setEditingAccount(null);
    setModalOpen(true);
  };

  const handleEditAccount = (account: M3UAccount) => {
    setEditingAccount(account);
    setModalOpen(true);
  };

  const handleDeleteAccount = async (account: M3UAccount) => {
    if (!confirm(`Are you sure you want to delete "${account.name}"?`)) {
      return;
    }

    try {
      await api.deleteM3UAccount(account.id);
      await loadData();
    } catch (err) {
      setError('Failed to delete M3U account');
    }
  };

  const handleRefreshAccount = async (account: M3UAccount) => {
    try {
      await api.refreshM3UAccount(account.id);
      // Update local state to show refreshing
      setAccounts(prev => prev.map(a =>
        a.id === account.id ? { ...a, status: 'fetching' } : a
      ));
      // Poll for status updates
      const pollInterval = setInterval(async () => {
        const updatedAccounts = await api.getM3UAccounts();
        setAccounts(updatedAccounts);
        const updatedAccount = updatedAccounts.find(a => a.id === account.id);
        if (updatedAccount && (updatedAccount.status === 'success' || updatedAccount.status === 'error')) {
          clearInterval(pollInterval);
        }
      }, 2000);
      // Stop polling after 5 minutes
      setTimeout(() => clearInterval(pollInterval), 300000);
    } catch (err) {
      setError('Failed to refresh M3U account');
    }
  };

  const handleToggleActive = async (account: M3UAccount) => {
    try {
      await api.patchM3UAccount(account.id, { is_active: !account.is_active });
      await loadData();
    } catch (err) {
      setError('Failed to update M3U account');
    }
  };

  const handleRefreshAll = async () => {
    setRefreshingAll(true);
    try {
      // Filter out the hidden "custom" account - don't refresh it
      const accountsToRefresh = accounts.filter(
        a => a.is_active && a.name.toLowerCase() !== 'custom'
      );

      // Trigger refresh for each account individually
      await Promise.all(accountsToRefresh.map(a => api.refreshM3UAccount(a.id)));

      // Mark these accounts as fetching
      setAccounts(prev => prev.map(a =>
        a.is_active && a.name.toLowerCase() !== 'custom' ? { ...a, status: 'fetching' } : a
      ));
      // Poll for status updates
      const pollInterval = setInterval(async () => {
        const updatedAccounts = await api.getM3UAccounts();
        setAccounts(updatedAccounts);
        const stillRefreshing = updatedAccounts.some(
          a => a.is_active && a.name.toLowerCase() !== 'custom' && (a.status === 'fetching' || a.status === 'parsing')
        );
        if (!stillRefreshing) {
          clearInterval(pollInterval);
          setRefreshingAll(false);
        }
      }, 2000);
      // Stop polling after 10 minutes
      setTimeout(() => {
        clearInterval(pollInterval);
        setRefreshingAll(false);
      }, 600000);
    } catch (err) {
      setError('Failed to refresh M3U accounts');
      setRefreshingAll(false);
    }
  };

  const handleManageGroups = (account: M3UAccount) => {
    setGroupsAccount(account);
    setGroupsModalOpen(true);
  };

  const handleGroupsSaved = () => {
    loadData();
  };

  const handleManageFilters = (account: M3UAccount) => {
    setFiltersAccount(account);
    setFiltersModalOpen(true);
  };

  const handleFiltersSaved = () => {
    loadData();
  };

  const handleManageProfiles = (account: M3UAccount) => {
    setProfilesAccount(account);
    setProfilesModalOpen(true);
  };

  const handleProfilesSaved = () => {
    loadData();
  };

  const handleAccountSaved = () => {
    loadData();
  };

  const handleSaveLinkedAccounts = async (linkGroups: number[][]) => {
    try {
      // Load current settings and update linked_m3u_accounts
      const settings = await api.getSettings();
      await api.saveSettings({
        ...settings,
        linked_m3u_accounts: linkGroups,
      });
      setLinkedM3UAccounts(linkGroups);
    } catch (err) {
      setError('Failed to save linked accounts');
    }
  };

  // Sync groups across all linked M3U accounts using Union (OR) logic
  // If a group is enabled in ANY linked account, enable it in ALL linked accounts
  const handleSyncGroups = async () => {
    if (linkedM3UAccounts.length === 0) {
      setError('No linked accounts configured. Use "Manage Links" to set up linked accounts first.');
      return;
    }

    setSyncingGroups(true);
    setError(null);

    try {
      // Process each link group
      for (const linkGroup of linkedM3UAccounts) {
        if (linkGroup.length < 2) continue;

        // Fetch fresh data for all accounts in this link group
        const accountsData = await Promise.all(
          linkGroup.map(id => api.getM3UAccount(id))
        );

        // Build a map of channel_group ID -> enabled (union of all accounts)
        // If enabled in ANY account, it should be enabled in ALL
        const groupEnabledUnion = new Map<number, boolean>();

        for (const account of accountsData) {
          for (const group of account.channel_groups) {
            const currentEnabled = groupEnabledUnion.get(group.channel_group) ?? false;
            // Union (OR): if enabled in this account OR already marked enabled, keep enabled
            groupEnabledUnion.set(group.channel_group, currentEnabled || group.enabled);
          }
        }

        // Update each account with the union result
        for (const account of accountsData) {
          const groupSettings = account.channel_groups.map(g => ({
            channel_group: g.channel_group,
            enabled: groupEnabledUnion.get(g.channel_group) ?? g.enabled,
            auto_channel_sync: g.auto_channel_sync,
            auto_sync_channel_start: g.auto_sync_channel_start,
          }));

          await api.updateM3UGroupSettings(account.id, { group_settings: groupSettings });
        }
      }

      // Reload data to show updated state
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to sync groups');
    } finally {
      setSyncingGroups(false);
    }
  };

  // Create a map from account ID to linked account names
  const linkedAccountNamesMap = useMemo(() => {
    const map = new Map<number, string[]>();
    const accountNameMap = new Map<number, string>();
    accounts.forEach(a => accountNameMap.set(a.id, a.name));

    for (const group of linkedM3UAccounts) {
      for (const accountId of group) {
        // Get names of OTHER accounts in this group
        const otherNames = group
          .filter(id => id !== accountId)
          .map(id => accountNameMap.get(id) ?? `Account ${id}`);
        if (otherNames.length > 0) {
          map.set(accountId, otherNames);
        }
      }
    }
    return map;
  }, [accounts, linkedM3UAccounts]);

  // Filter and sort accounts: hide "custom" account, optionally filter by server group,
  // then sort with Standard M3U first, XtreamCodes second, natural sort within each type
  const filteredAccounts = useMemo(() => {
    return accounts
      .filter(a => a.name.toLowerCase() !== 'custom')
      .filter(a => filterServerGroup === null || a.server_group === filterServerGroup)
      .sort((a, b) => {
        // First sort by type: STD (Standard M3U) before XC (XtreamCodes)
        if (a.account_type !== b.account_type) {
          return a.account_type === 'STD' ? -1 : 1;
        }
        // Then natural sort by name within each type
        return naturalCompare(a.name, b.name);
      });
  }, [accounts, filterServerGroup]);

  if (loading) {
    return (
      <div className="m3u-manager-tab">
        <div className="loading-state">
          <span className="material-icons spinning">sync</span>
          <p>Loading M3U accounts...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="m3u-manager-tab">
      <div className="m3u-header">
        <div className="header-title">
          <h2>M3U Accounts</h2>
          <p className="header-description">
            Manage your M3U playlist sources and XtreamCodes accounts.
          </p>
        </div>
        <div className="header-actions">
          {serverGroups.length > 0 && (
            <select
              className="server-group-filter"
              value={filterServerGroup ?? ''}
              onChange={(e) => setFilterServerGroup(e.target.value ? Number(e.target.value) : null)}
            >
              <option value="">All Server Groups</option>
              {serverGroups.map(sg => (
                <option key={sg.id} value={sg.id}>{sg.name}</option>
              ))}
            </select>
          )}
          <button className="btn-secondary" onClick={() => setLinkedAccountsModalOpen(true)}>
            <span className="material-icons">link</span>
            Manage Links
          </button>
          <button
            className="btn-secondary"
            onClick={handleSyncGroups}
            disabled={syncingGroups || linkedM3UAccounts.length === 0}
            title={linkedM3UAccounts.length === 0 ? 'No linked accounts configured' : 'Sync enabled groups across all linked accounts'}
          >
            <span className={`material-icons ${syncingGroups ? 'spinning' : ''}`}>sync_alt</span>
            {syncingGroups ? 'Syncing...' : 'Sync Groups'}
          </button>
          <button className="btn-secondary" onClick={handleRefreshAll} disabled={refreshingAll}>
            <span className={`material-icons ${refreshingAll ? 'spinning' : ''}`}>sync</span>
            {refreshingAll ? 'Refreshing...' : 'Refresh All'}
          </button>
          <button className="btn-primary" onClick={handleAddAccount}>
            <span className="material-icons">add</span>
            Add M3U Account
          </button>
        </div>
      </div>

      {error && (
        <div className="error-banner">
          <span className="material-icons">error</span>
          {error}
          <button onClick={() => setError(null)}>&times;</button>
        </div>
      )}

      {filteredAccounts.length === 0 ? (
        <div className="empty-state">
          <span className="material-icons">playlist_play</span>
          <h3>No M3U Accounts</h3>
          <p>Add an M3U account to start importing streams.</p>
          <button className="btn-primary" onClick={handleAddAccount}>
            <span className="material-icons">add</span>
            Add M3U Account
          </button>
        </div>
      ) : (
        <div className="m3u-accounts-list">
          <div className="list-header">
            <span className="col-status">Status</span>
            <span className="col-info">Account</span>
            <span className="col-groups">Groups</span>
            <span className="col-settings">Settings</span>
            <span className="col-updated">Last Updated</span>
            <span className="col-actions">Actions</span>
          </div>

          {filteredAccounts.map(account => (
            <M3UAccountRow
              key={account.id}
              account={account}
              onEdit={handleEditAccount}
              onDelete={handleDeleteAccount}
              onRefresh={handleRefreshAccount}
              onToggleActive={handleToggleActive}
              onManageGroups={handleManageGroups}
              onManageFilters={handleManageFilters}
              onManageProfiles={handleManageProfiles}
              linkedAccountNames={linkedAccountNamesMap.get(account.id)}
            />
          ))}
        </div>
      )}

      <M3UAccountModal
        isOpen={modalOpen}
        onClose={() => setModalOpen(false)}
        onSaved={handleAccountSaved}
        account={editingAccount}
        serverGroups={serverGroups}
      />

      {groupsAccount && (
        <M3UGroupsModal
          isOpen={groupsModalOpen}
          onClose={() => setGroupsModalOpen(false)}
          onSaved={handleGroupsSaved}
          account={groupsAccount}
          allAccounts={accounts}
          linkedAccountGroups={linkedM3UAccounts}
          epgSources={epgSources}
          channelGroups={channelGroups}
          channelProfiles={channelProfiles}
          streamProfiles={streamProfiles}
          onChannelGroupsChange={onChannelGroupsChange}
        />
      )}

      {filtersAccount && (
        <M3UFiltersModal
          isOpen={filtersModalOpen}
          onClose={() => setFiltersModalOpen(false)}
          onSaved={handleFiltersSaved}
          account={filtersAccount}
        />
      )}

      {profilesAccount && (
        <M3UProfileModal
          isOpen={profilesModalOpen}
          onClose={() => setProfilesModalOpen(false)}
          onSaved={handleProfilesSaved}
          account={profilesAccount}
        />
      )}

      <M3ULinkedAccountsModal
        isOpen={linkedAccountsModalOpen}
        onClose={() => setLinkedAccountsModalOpen(false)}
        onSave={handleSaveLinkedAccounts}
        accounts={accounts}
        linkGroups={linkedM3UAccounts}
      />
    </div>
  );
}
