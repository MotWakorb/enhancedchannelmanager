/**
 * UserManagementSection Component
 *
 * Admin panel for managing user accounts.
 * Allows viewing, editing, and deleting users.
 */
import { useState, useEffect, useCallback } from 'react';
import * as api from '../../services/api';
import type { User, UserUpdateRequest } from '../../types';
import { useNotifications } from '../../contexts/NotificationContext';
import './UserManagementSection.css';

interface Props {
  isAdmin: boolean;
  currentUserId: number;
}

interface EditingUser {
  id: number;
  is_admin: boolean;
  is_active: boolean;
  display_name: string;
  email: string;
}

export function UserManagementSection({ isAdmin, currentUserId }: Props) {
  const notifications = useNotifications();
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [editingUser, setEditingUser] = useState<EditingUser | null>(null);
  const [saving, setSaving] = useState(false);
  const [deletingUserId, setDeletingUserId] = useState<number | null>(null);

  // Load users on mount
  useEffect(() => {
    if (!isAdmin) return;

    const loadUsers = async () => {
      try {
        setLoading(true);
        const response = await api.listUsers();
        setUsers(response.users);
      } catch (err) {
        setError('Failed to load users');
        console.error('Failed to load users:', err);
      } finally {
        setLoading(false);
      }
    };

    loadUsers();
  }, [isAdmin]);

  const handleEditUser = useCallback((user: User) => {
    setEditingUser({
      id: user.id,
      is_admin: user.is_admin,
      is_active: user.is_active,
      display_name: user.display_name || '',
      email: user.email || '',
    });
  }, []);

  const handleCancelEdit = useCallback(() => {
    setEditingUser(null);
  }, []);

  const handleSaveUser = useCallback(async () => {
    if (!editingUser) return;

    setSaving(true);
    try {
      const update: UserUpdateRequest = {
        is_admin: editingUser.is_admin,
        is_active: editingUser.is_active,
        display_name: editingUser.display_name || undefined,
        email: editingUser.email || undefined,
      };

      const response = await api.updateUser(editingUser.id, update);

      // Update local state
      setUsers(prev => prev.map(u =>
        u.id === editingUser.id ? response.user : u
      ));

      setEditingUser(null);
      notifications.success('User updated successfully');
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to update user';
      notifications.error(message);
    } finally {
      setSaving(false);
    }
  }, [editingUser, notifications]);

  const handleDeleteUser = useCallback(async (userId: number, username: string) => {
    if (userId === currentUserId) {
      notifications.error('Cannot delete your own account');
      return;
    }

    if (!confirm(`Are you sure you want to delete user "${username}"? This action cannot be undone.`)) {
      return;
    }

    setDeletingUserId(userId);
    try {
      await api.deleteUser(userId);
      setUsers(prev => prev.filter(u => u.id !== userId));
      notifications.success(`User "${username}" deleted`);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to delete user';
      notifications.error(message);
    } finally {
      setDeletingUserId(null);
    }
  }, [currentUserId, notifications]);

  const handleToggleActive = useCallback(async (user: User) => {
    if (user.id === currentUserId) {
      notifications.error('Cannot deactivate your own account');
      return;
    }

    try {
      const response = await api.updateUser(user.id, { is_active: !user.is_active });
      setUsers(prev => prev.map(u =>
        u.id === user.id ? response.user : u
      ));
      notifications.success(
        response.user.is_active
          ? `User "${user.username}" activated`
          : `User "${user.username}" deactivated`
      );
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to update user';
      notifications.error(message);
    }
  }, [currentUserId, notifications]);

  if (!isAdmin) {
    return (
      <div className="user-management-section">
        <p className="user-management-no-access">Admin access required to manage users.</p>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="user-management-section">
        <div className="user-management-loading">
          <span className="material-icons spinning">sync</span>
          Loading users...
        </div>
      </div>
    );
  }

  return (
    <div className="user-management-section">
      <div className="user-management-header">
        <div className="header-info">
          <h3>Users</h3>
          <p className="header-description">
            Manage user accounts, roles, and access.
          </p>
        </div>
        <div className="header-stats">
          <span className="stat">
            <span className="material-icons">people</span>
            {users.length} users
          </span>
          <span className="stat">
            <span className="material-icons">admin_panel_settings</span>
            {users.filter(u => u.is_admin).length} admins
          </span>
        </div>
      </div>

      {error && (
        <div className="user-management-error">
          <span className="material-icons">error</span>
          {error}
          <button onClick={() => setError(null)}>
            <span className="material-icons">close</span>
          </button>
        </div>
      )}

      <div className="user-list">
        <div className="user-list-header">
          <span className="user-col-username">Username</span>
          <span className="user-col-email">Email</span>
          <span className="user-col-provider">Provider</span>
          <span className="user-col-status">Status</span>
          <span className="user-col-role">Role</span>
          <span className="user-col-actions">Actions</span>
        </div>

        {users.map(user => (
          <div
            key={user.id}
            className={`user-list-row ${!user.is_active ? 'user-inactive' : ''} ${user.id === currentUserId ? 'user-current' : ''}`}
          >
            {editingUser?.id === user.id ? (
              // Edit mode
              <>
                <span className="user-col-username">
                  <strong>{user.username}</strong>
                  {user.id === currentUserId && <span className="user-badge-you">(You)</span>}
                </span>
                <span className="user-col-email">
                  <input
                    type="email"
                    value={editingUser.email}
                    onChange={(e) => setEditingUser(prev => prev ? { ...prev, email: e.target.value } : null)}
                    placeholder="Email"
                    className="user-edit-input"
                  />
                </span>
                <span className="user-col-provider">{user.auth_provider}</span>
                <span className="user-col-status">
                  <label className="user-checkbox-label">
                    <input
                      type="checkbox"
                      checked={editingUser.is_active}
                      onChange={(e) => setEditingUser(prev => prev ? { ...prev, is_active: e.target.checked } : null)}
                      disabled={user.id === currentUserId}
                    />
                    <span>Active</span>
                  </label>
                </span>
                <span className="user-col-role">
                  <label className="user-checkbox-label">
                    <input
                      type="checkbox"
                      checked={editingUser.is_admin}
                      onChange={(e) => setEditingUser(prev => prev ? { ...prev, is_admin: e.target.checked } : null)}
                      disabled={user.id === currentUserId}
                    />
                    <span>Admin</span>
                  </label>
                </span>
                <span className="user-col-actions">
                  <button
                    className="user-action-btn user-action-save"
                    onClick={handleSaveUser}
                    disabled={saving}
                  >
                    {saving ? 'Saving...' : 'Save'}
                  </button>
                  <button
                    className="user-action-btn user-action-cancel"
                    onClick={handleCancelEdit}
                    disabled={saving}
                  >
                    Cancel
                  </button>
                </span>
              </>
            ) : (
              // View mode
              <>
                <span className="user-col-username">
                  <strong>{user.username}</strong>
                  {user.display_name && <span className="user-display-name">({user.display_name})</span>}
                  {user.id === currentUserId && <span className="user-badge-you">(You)</span>}
                </span>
                <span className="user-col-email">{user.email || '-'}</span>
                <span className="user-col-provider">
                  <span className={`user-provider-badge user-provider-${user.auth_provider}`}>
                    {user.auth_provider}
                  </span>
                </span>
                <span className="user-col-status">
                  <button
                    className={`user-status-badge ${user.is_active ? 'user-status-active' : 'user-status-inactive'}`}
                    onClick={() => handleToggleActive(user)}
                    disabled={user.id === currentUserId}
                    title={user.id === currentUserId ? 'Cannot deactivate yourself' : (user.is_active ? 'Click to deactivate' : 'Click to activate')}
                  >
                    {user.is_active ? 'Active' : 'Inactive'}
                  </button>
                </span>
                <span className="user-col-role">
                  <span className={`user-role-badge ${user.is_admin ? 'user-role-admin' : 'user-role-user'}`}>
                    {user.is_admin ? 'Admin' : 'User'}
                  </span>
                </span>
                <span className="user-col-actions">
                  <button
                    className="user-action-btn user-action-edit"
                    onClick={() => handleEditUser(user)}
                  >
                    Edit
                  </button>
                  <button
                    className="user-action-btn user-action-delete"
                    onClick={() => handleDeleteUser(user.id, user.username)}
                    disabled={user.id === currentUserId || deletingUserId === user.id}
                    title={user.id === currentUserId ? 'Cannot delete yourself' : 'Delete user'}
                  >
                    {deletingUserId === user.id ? 'Deleting...' : 'Delete'}
                  </button>
                </span>
              </>
            )}
          </div>
        ))}

        {users.length === 0 && (
          <div className="user-list-empty">No users found.</div>
        )}
      </div>

      <div className="user-management-summary">
        <span className="stat">
          <span className="material-icons">check_circle</span>
          {users.filter(u => u.is_active).length} active
        </span>
        <span className="stat">
          <span className="material-icons">cancel</span>
          {users.filter(u => !u.is_active).length} inactive
        </span>
      </div>
    </div>
  );
}

export default UserManagementSection;
