/**
 * User menu component for header.
 *
 * Shows current user info, profile editing, password change, and logout.
 * Hidden when auth is not required or user not logged in.
 */
import React, { useState, useRef, useEffect } from 'react';
import { useAuth, useAuthRequired } from '../hooks/useAuth';
import { useNotifications } from '../contexts/NotificationContext';
import * as api from '../services/api';
import { ModalOverlay } from './ModalOverlay';
import './UserMenu.css';

export function UserMenu() {
  const { user, logout, isLoading, refreshUser } = useAuth();
  const authRequired = useAuthRequired();
  const notifications = useNotifications();
  const [isOpen, setIsOpen] = useState(false);
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  // Modal states
  const [showProfileModal, setShowProfileModal] = useState(false);
  const [showPasswordModal, setShowPasswordModal] = useState(false);

  // Profile form state
  const [displayName, setDisplayName] = useState('');
  const [email, setEmail] = useState('');
  const [savingProfile, setSavingProfile] = useState(false);

  // Password form state
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [savingPassword, setSavingPassword] = useState(false);

  // Close menu when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }

    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [isOpen]);

  // Populate profile form when opening
  useEffect(() => {
    if (showProfileModal && user) {
      setDisplayName(user.display_name || '');
      setEmail(user.email || '');
    }
  }, [showProfileModal, user]);

  // Don't show if loading, auth not required, or user not logged in
  if (isLoading || !authRequired || !user) {
    return null;
  }

  const handleLogout = async () => {
    setIsLoggingOut(true);
    try {
      await logout();
      // Page will redirect to login via ProtectedRoute
    } catch (err) {
      console.error('Logout failed:', err);
    } finally {
      setIsLoggingOut(false);
      setIsOpen(false);
    }
  };

  const handleOpenProfile = () => {
    setIsOpen(false);
    setShowProfileModal(true);
  };

  const handleOpenPassword = () => {
    setIsOpen(false);
    setShowPasswordModal(true);
    setCurrentPassword('');
    setNewPassword('');
    setConfirmPassword('');
  };

  const handleSaveProfile = async (e: React.FormEvent) => {
    e.preventDefault();
    setSavingProfile(true);

    try {
      await api.updateProfile({
        display_name: displayName || undefined,
        email: email || undefined,
      });
      await refreshUser();
      setShowProfileModal(false);
      notifications.success('Profile updated');
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to update profile';
      notifications.error(message);
    } finally {
      setSavingProfile(false);
    }
  };

  const handleChangePassword = async (e: React.FormEvent) => {
    e.preventDefault();

    if (newPassword !== confirmPassword) {
      notifications.error('New passwords do not match');
      return;
    }

    if (newPassword.length < 8) {
      notifications.error('Password must be at least 8 characters');
      return;
    }

    setSavingPassword(true);

    try {
      await api.changePassword({
        current_password: currentPassword,
        new_password: newPassword,
      });
      setShowPasswordModal(false);
      notifications.success('Password changed successfully');
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to change password';
      notifications.error(message);
    } finally {
      setSavingPassword(false);
    }
  };

  return (
    <>
      <div className="user-menu" ref={menuRef}>
        <button
          className="user-menu-trigger"
          onClick={() => setIsOpen(!isOpen)}
          title={`Logged in as ${user.username}`}
        >
          <span className="material-icons user-menu-icon">account_circle</span>
          <span className="user-menu-name">{user.display_name || user.username}</span>
          <span className="material-icons user-menu-arrow">
            {isOpen ? 'expand_less' : 'expand_more'}
          </span>
        </button>

        {isOpen && (
          <div className="user-menu-dropdown">
            <div className="user-menu-info">
              <div className="user-menu-username">{user.username}</div>
              {user.email && <div className="user-menu-email">{user.email}</div>}
              <div className="user-menu-badges">
                {user.is_admin && (
                  <span className="user-menu-badge user-menu-badge-admin">Admin</span>
                )}
                <span className="user-menu-badge user-menu-badge-provider">{user.auth_provider}</span>
              </div>
            </div>
            <div className="user-menu-divider" />
            <button className="user-menu-item" onClick={handleOpenProfile}>
              <span className="material-icons">person</span>
              Edit Profile
            </button>
            {user.auth_provider === 'local' && (
              <button className="user-menu-item" onClick={handleOpenPassword}>
                <span className="material-icons">lock</span>
                Change Password
              </button>
            )}
            <div className="user-menu-divider" />
            <button
              className="user-menu-item user-menu-logout"
              onClick={handleLogout}
              disabled={isLoggingOut}
            >
              <span className="material-icons">logout</span>
              {isLoggingOut ? 'Signing out...' : 'Sign out'}
            </button>
          </div>
        )}
      </div>

      {/* Profile Edit Modal */}
      {showProfileModal && (
        <ModalOverlay onClose={() => setShowProfileModal(false)} className="user-modal-overlay">
          <div className="user-modal">
            <div className="user-modal-header">
              <h3>Edit Profile</h3>
              <button
                className="user-modal-close"
                onClick={() => setShowProfileModal(false)}
              >
                <span className="material-icons">close</span>
              </button>
            </div>
            <form onSubmit={handleSaveProfile}>
              <div className="user-modal-body">
                <div className="user-modal-field">
                  <label>Username</label>
                  <input type="text" value={user.username} disabled />
                  <p className="user-modal-hint">Username cannot be changed</p>
                </div>
                <div className="user-modal-field">
                  <label htmlFor="profile-display-name">Display Name</label>
                  <input
                    type="text"
                    id="profile-display-name"
                    value={displayName}
                    onChange={(e) => setDisplayName(e.target.value)}
                    placeholder="Enter display name"
                  />
                </div>
                <div className="user-modal-field">
                  <label htmlFor="profile-email">Email</label>
                  <input
                    type="email"
                    id="profile-email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="Enter email address"
                  />
                </div>
              </div>
              <div className="user-modal-footer">
                <button
                  type="button"
                  className="user-modal-btn user-modal-btn-secondary"
                  onClick={() => setShowProfileModal(false)}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="user-modal-btn user-modal-btn-primary"
                  disabled={savingProfile}
                >
                  {savingProfile ? 'Saving...' : 'Save Changes'}
                </button>
              </div>
            </form>
          </div>
        </ModalOverlay>
      )}

      {/* Change Password Modal */}
      {showPasswordModal && (
        <ModalOverlay onClose={() => setShowPasswordModal(false)} className="user-modal-overlay">
          <div className="user-modal">
            <div className="user-modal-header">
              <h3>Change Password</h3>
              <button
                className="user-modal-close"
                onClick={() => setShowPasswordModal(false)}
              >
                <span className="material-icons">close</span>
              </button>
            </div>
            <form onSubmit={handleChangePassword}>
              <div className="user-modal-body">
                <div className="user-modal-field">
                  <label htmlFor="current-password">Current Password</label>
                  <input
                    type="password"
                    id="current-password"
                    value={currentPassword}
                    onChange={(e) => setCurrentPassword(e.target.value)}
                    placeholder="Enter current password"
                    required
                  />
                </div>
                <div className="user-modal-field">
                  <label htmlFor="new-password">New Password</label>
                  <input
                    type="password"
                    id="new-password"
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    placeholder="Enter new password"
                    required
                    minLength={8}
                  />
                  <p className="user-modal-hint">Minimum 8 characters</p>
                </div>
                <div className="user-modal-field">
                  <label htmlFor="confirm-password">Confirm New Password</label>
                  <input
                    type="password"
                    id="confirm-password"
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    placeholder="Confirm new password"
                    required
                  />
                </div>
              </div>
              <div className="user-modal-footer">
                <button
                  type="button"
                  className="user-modal-btn user-modal-btn-secondary"
                  onClick={() => setShowPasswordModal(false)}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="user-modal-btn user-modal-btn-primary"
                  disabled={savingPassword}
                >
                  {savingPassword ? 'Changing...' : 'Change Password'}
                </button>
              </div>
            </form>
          </div>
        </ModalOverlay>
      )}
    </>
  );
}

export default UserMenu;
