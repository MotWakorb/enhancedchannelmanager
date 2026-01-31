import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import type { M3UAccount, ServerGroup, EPGSource, ChannelGroup, ChannelProfile, StreamProfile } from '../../types';
import * as api from '../../services/api';
import { naturalCompare } from '../../utils/naturalSort';
import { M3UAccountModal } from '../M3UAccountModal';
import { M3UGroupsModal } from '../M3UGroupsModal';
import { M3UFiltersModal } from '../M3UFiltersModal';
import { M3ULinkedAccountsModal } from '../M3ULinkedAccountsModal';
import { M3UProfileModal } from '../M3UProfileModal';
import { CustomSelect } from '../CustomSelect';
import { useNotifications } from '../../contexts/NotificationContext';
import './M3UManagerTab.css';

interface M3UManagerTabProps {
  epgSources?: EPGSource[];
  channelGroups?: ChannelGroup[];
  channelProfiles?: ChannelProfile[];
  streamProfiles?: StreamProfile[];
  onChannelGroupsChange?: () => void;
  onAccountsChange?: () => void;  // Called when M3U accounts are added/deleted/modified
  hideM3uUrls?: boolean;
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
  hideM3uUrls?: boolean;
  priority?: number;  // Sort priority for this account (higher = better)
  onPriorityChange?: (accountId: number, priority: number) => void;
  isBeingRefreshed?: boolean;  // Whether we're tracking this account as still refreshing
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
  hideM3uUrls = false,
  priority = 0,
  onPriorityChange,
  isBeingRefreshed = false,
}: M3UAccountRowProps) {
  // Consider refreshing if status says so OR if we're tracking it as refreshing
  const isRefreshing = isBeingRefreshed || account.status === 'fetching' || account.status === 'parsing';

  const getStatusIcon = (status: M3UAccount['status']) => {
    // Show actual status from Dispatcharr - icons reflect real progress
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
    // Show actual status class from Dispatcharr
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
    // Show actual status label from Dispatcharr - users see real progress
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
          {account.server_url && !hideM3uUrls && (
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

      {onPriorityChange && (
        <div className="account-priority">
          <input
            type="text"
            className="priority-input"
            value={priority || ''}
            onChange={(e) => {
              const val = e.target.value;
              // Allow empty or numbers only
              if (val === '' || /^\d+$/.test(val)) {
                const num = parseInt(val) || 0;
                // Clamp to 1-100 range (0 means not set)
                onPriorityChange(account.id, Math.min(100, Math.max(0, num)));
              }
            }}
            placeholder="-"
            title="Sort priority (1-100, higher = better)"
          />
        </div>
      )}

      <div className="account-updated">
        <span className="updated-label">Updated:</span>
        <span className="updated-time">{formatDate(account.updated_at)}</span>
      </div>

      <div className="account-actions">
        <button
          className={`action-btn toggle ${account.is_active ? 'active' : ''}`}
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
  onAccountsChange,
  hideM3uUrls = false,
}: M3UManagerTabProps) {
  const notifications = useNotifications();
  const [accounts, setAccounts] = useState<M3UAccount[]>([]);
  const [serverGroups, setServerGroups] = useState<ServerGroup[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingAccount, setEditingAccount] = useState<M3UAccount | null>(null);
  const pollingRef = useRef<NodeJS.Timeout | null>(null);
  // Track accounts being refreshed - stores initial updated_at to detect completion
  const [refreshingAccounts, setRefreshingAccounts] = useState<Map<number, { initialUpdatedAt: string | null; startTime: number }>>(new Map());

  // Check if any accounts are actively refreshing
  // An account is refreshing if: status is fetching/parsing OR we're waiting for updated_at to change
  const anyRefreshing = accounts.some(
    a => a.is_active && (
      a.status === 'fetching' ||
      a.status === 'parsing' ||
      refreshingAccounts.has(a.id)
    )
  );
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
  const [m3uAccountPriorities, setM3uAccountPriorities] = useState<Record<string, number>>({});
  const [pendingPriorities, setPendingPriorities] = useState<Record<string, number>>({});
  const [savingPriorities, setSavingPriorities] = useState(false);

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
      const priorities = settings.m3u_account_priorities ?? {};
      setM3uAccountPriorities(priorities);
      setPendingPriorities(priorities);
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

  // Poll for refresh status when any account is refreshing
  useEffect(() => {
    // Clear any existing polling
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }

    // Start polling if any accounts are refreshing
    if (anyRefreshing) {
      const REFRESH_TIMEOUT_MS = 5 * 60 * 1000; // 5 minutes

      pollingRef.current = setInterval(async () => {
        try {
          const updatedAccounts = await api.getM3UAccounts();
          setAccounts(updatedAccounts);

          // Check each tracked account to see if refresh completed
          const now = Date.now();
          const completedIds: number[] = [];

          refreshingAccounts.forEach((tracking, accountId) => {
            const account = updatedAccounts.find(a => a.id === accountId);
            if (!account) {
              // Account was deleted
              completedIds.push(accountId);
              return;
            }

            // Refresh is complete if:
            // 1. Status is no longer fetching/parsing (success, error, idle, disabled)
            // 2. Timeout exceeded (safety fallback)
            const isStillRefreshing = account.status === 'fetching' || account.status === 'parsing';
            if (
              !isStillRefreshing ||
              (now - tracking.startTime) > REFRESH_TIMEOUT_MS
            ) {
              completedIds.push(accountId);
            }
          });

          // Remove completed accounts from tracking
          if (completedIds.length > 0) {
            setRefreshingAccounts(prev => {
              const next = new Map(prev);
              completedIds.forEach(id => next.delete(id));
              return next;
            });
          }

          // Also check status-based refreshing for accounts not in our tracking
          const stillRefreshingByStatus = updatedAccounts.some(
            a => a.is_active && (a.status === 'fetching' || a.status === 'parsing')
          );

          // Stop polling when no accounts are refreshing
          if (refreshingAccounts.size === 0 && !stillRefreshingByStatus && pollingRef.current) {
            clearInterval(pollingRef.current);
            pollingRef.current = null;
          }
        } catch (err) {
          console.error('Failed to poll account status:', err);
        }
      }, 2000);
    }

    // Cleanup on unmount
    return () => {
      if (pollingRef.current) {
        clearInterval(pollingRef.current);
        pollingRef.current = null;
      }
    };
  }, [anyRefreshing, refreshingAccounts]);

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
      onAccountsChange?.();  // Notify parent to reload providers
    } catch (err) {
      setError('Failed to delete M3U account');
    }
  };

  const handleRefreshAccount = async (account: M3UAccount) => {
    try {
      // Capture initial updated_at before triggering refresh
      const initialUpdatedAt = account.updated_at || null;

      await api.refreshM3UAccount(account.id);

      // Track this account as refreshing
      setRefreshingAccounts(prev => {
        const next = new Map(prev);
        next.set(account.id, { initialUpdatedAt, startTime: Date.now() });
        return next;
      });

      // Update local state to show refreshing status
      setAccounts(prev => prev.map(a =>
        a.id === account.id ? { ...a, status: 'fetching' } : a
      ));
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
    try {
      // Filter out the hidden "custom" account - don't refresh it
      const accountsToRefresh = accounts.filter(
        a => a.is_active && a.name.toLowerCase() !== 'custom'
      );

      // Capture initial updated_at for all accounts
      const now = Date.now();
      const tracking = new Map<number, { initialUpdatedAt: string | null; startTime: number }>();
      accountsToRefresh.forEach(a => {
        tracking.set(a.id, {
          initialUpdatedAt: a.updated_at || null,
          startTime: now,
        });
      });

      // Trigger refresh for each account individually
      await Promise.all(accountsToRefresh.map(a => api.refreshM3UAccount(a.id)));

      // Track all accounts as refreshing
      setRefreshingAccounts(prev => {
        const next = new Map(prev);
        tracking.forEach((value, key) => next.set(key, value));
        return next;
      });

      // Mark these accounts as fetching
      setAccounts(prev => prev.map(a =>
        a.is_active && a.name.toLowerCase() !== 'custom' ? { ...a, status: 'fetching' } : a
      ));
    } catch (err) {
      setError('Failed to refresh M3U accounts');
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

  // Handle M3U account priority change (local only, not saved until Save button clicked)
  const handlePriorityChange = useCallback((accountId: number, priority: number) => {
    setPendingPriorities(prev => {
      const newPriorities = { ...prev };
      if (priority === 0) {
        delete newPriorities[String(accountId)];
      } else {
        newPriorities[String(accountId)] = priority;
      }
      return newPriorities;
    });
  }, []);

  // Check if there are unsaved priority changes
  const hasPriorityChanges = useMemo(() => {
    const savedKeys = Object.keys(m3uAccountPriorities);
    const pendingKeys = Object.keys(pendingPriorities);
    if (savedKeys.length !== pendingKeys.length) return true;
    return savedKeys.some(key => m3uAccountPriorities[key] !== pendingPriorities[key]);
  }, [m3uAccountPriorities, pendingPriorities]);

  // Save priority changes
  const handleSavePriorities = useCallback(async () => {
    setSavingPriorities(true);
    try {
      // Load current settings and merge with priority changes
      const settings = await api.getSettings();
      await api.saveSettings({
        ...settings,
        m3u_account_priorities: pendingPriorities,
      });
      setM3uAccountPriorities(pendingPriorities);
      notifications.success('M3U account priorities saved successfully.');
    } catch (err) {
      console.error('Failed to save M3U priorities:', err);
      notifications.error('Failed to save M3U priorities.');
    } finally {
      setSavingPriorities(false);
    }
  }, [pendingPriorities, notifications]);

  const handleAccountSaved = () => {
    loadData();
    onAccountsChange?.();  // Notify parent to reload providers
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
          <button
            className="btn-primary save-priorities-btn"
            onClick={handleSavePriorities}
            disabled={savingPriorities || !hasPriorityChanges}
            title={hasPriorityChanges ? "Save priority changes" : "No priority changes to save"}
          >
            <span className={`material-icons ${savingPriorities ? 'spinning' : ''}`}>
              {savingPriorities ? 'sync' : 'save'}
            </span>
            {savingPriorities ? 'Saving...' : 'Save Priorities'}
          </button>
          {serverGroups.length > 0 && (
            <CustomSelect
              className="server-group-filter"
              value={filterServerGroup?.toString() ?? ''}
              onChange={(val) => setFilterServerGroup(val ? Number(val) : null)}
              options={[
                { value: '', label: 'All Server Groups' },
                ...serverGroups.map(sg => ({
                  value: sg.id.toString(),
                  label: sg.name,
                })),
              ]}
            />
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
          <button className="btn-secondary" onClick={handleRefreshAll} disabled={anyRefreshing}>
            <span className={`material-icons ${anyRefreshing ? 'spinning' : ''}`}>sync</span>
            {anyRefreshing ? 'Refreshing...' : 'Refresh All'}
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
            <span className="col-priority" title="Sort priority for Smart Sort (higher = better)">Priority</span>
            <span className="col-updated">Last Updated</span>
            <span className="col-actions">Actions</span>
          </div>

          <div className="m3u-accounts-list-body">
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
                hideM3uUrls={hideM3uUrls}
                priority={pendingPriorities[String(account.id)] ?? 0}
                onPriorityChange={handlePriorityChange}
                isBeingRefreshed={refreshingAccounts.has(account.id)}
              />
            ))}
          </div>
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
