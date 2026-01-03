import { useState, useEffect, useCallback } from 'react';
import type { M3UAccount, ServerGroup } from '../../types';
import * as api from '../../services/api';
import { M3UAccountModal } from '../M3UAccountModal';
import { M3UGroupsModal } from '../M3UGroupsModal';
import { M3UFiltersModal } from '../M3UFiltersModal';
import './M3UManagerTab.css';

interface M3UAccountRowProps {
  account: M3UAccount;
  onEdit: (account: M3UAccount) => void;
  onDelete: (account: M3UAccount) => void;
  onRefresh: (account: M3UAccount) => void;
  onToggleActive: (account: M3UAccount) => void;
  onManageGroups: (account: M3UAccount) => void;
  onManageFilters: (account: M3UAccount) => void;
}

function M3UAccountRow({
  account,
  onEdit,
  onDelete,
  onRefresh,
  onToggleActive,
  onManageGroups,
  onManageFilters,
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

  // Count enabled groups
  const enabledGroupCount = account.channel_groups.filter(g => g.enabled).length;
  const totalGroupCount = account.channel_groups.length;

  return (
    <div className={`m3u-account-row ${!account.is_active ? 'inactive' : ''}`}>
      <div className={`account-status ${getStatusClass(account.status)}`} title={account.last_message || ''}>
        <span className={`material-icons ${isRefreshing ? 'spinning' : ''}`}>
          {getStatusIcon(account.status)}
        </span>
        <span className="status-label">{getStatusLabel(account.status)}</span>
      </div>

      <div className="account-info">
        <div className="account-name">{account.name}</div>
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
        <span className="group-count">
          {enabledGroupCount} / {totalGroupCount} groups
        </span>
      </div>

      <div className="account-settings">
        <span className="setting-item">
          <span className="material-icons" title="Max Streams">stream</span>
          {account.max_streams === 0 ? 'Unlimited' : account.max_streams}
        </span>
        <span className="setting-item">
          <span className="material-icons" title="Refresh Interval">schedule</span>
          {account.refresh_interval}h
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

export function M3UManagerTab() {
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

  const loadData = useCallback(async () => {
    try {
      const [accountsData, serverGroupsData] = await Promise.all([
        api.getM3UAccounts(),
        api.getServerGroups(),
      ]);
      setAccounts(accountsData);
      setServerGroups(serverGroupsData);
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
      await api.refreshAllM3UAccounts();
      // Mark all active accounts as fetching
      setAccounts(prev => prev.map(a =>
        a.is_active ? { ...a, status: 'fetching' } : a
      ));
      // Poll for status updates
      const pollInterval = setInterval(async () => {
        const updatedAccounts = await api.getM3UAccounts();
        setAccounts(updatedAccounts);
        const stillRefreshing = updatedAccounts.some(
          a => a.is_active && (a.status === 'fetching' || a.status === 'parsing')
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

  const handleAccountSaved = () => {
    loadData();
  };

  // Filter accounts by server group if selected
  const filteredAccounts = filterServerGroup !== null
    ? accounts.filter(a => a.server_group === filterServerGroup)
    : accounts;

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
    </div>
  );
}
