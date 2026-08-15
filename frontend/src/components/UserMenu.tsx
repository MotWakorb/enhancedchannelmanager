/**
 * User menu component for header.
 *
 * Shows current user info, profile editing, password change, and logout.
 *
 * With no signed-in user it falls back to a "Sign in" button when the instance
 * has an operator identity to sign in as, and renders nothing otherwise. This
 * is the app shell's only sign-in affordance; see the branch below.
 */
import { logger } from '../utils/logger';
import React, { useState, useRef, useEffect } from 'react';
import { useAuth } from '../hooks/useAuth';
import { useNotifications } from '../contexts/NotificationContext';
import * as api from '../services/api';
import { ModalOverlay } from './ModalOverlay';
import { useOwnedDialog } from '../hooks/useOwnedDialog';
import './UserMenu.css';

export interface UserMenuProps {
  /**
   * Ask the host whether the session may end, handing it the sign-out to run.
   *
   * Signing out unmounts the app and takes the in-memory Edit Mode ledger with
   * it, discarding staged channel work with no warning — an SPA state
   * transition, so `beforeunload` never fires (bead epic
   * enhancedchannelmanager-r93hq). The host either runs `proceed` immediately
   * or holds it until the operator answers its exit dialog.
   *
   * Optional so the dev harness, and this component's own tests, can render a
   * UserMenu with no host: without it the sign-out runs unguarded, which is
   * correct in a context that has no staged work to lose.
   */
  onRequestSignOut?: (proceed: () => void | Promise<void>) => void;
}

export function UserMenu({ onRequestSignOut }: UserMenuProps = {}) {
  const { user, authStatus, logout, isLoading, refreshUser } = useAuth();
  const notifications = useNotifications();
  const [isOpen, setIsOpen] = useState(false);
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  // Modal states
  const [showProfileModal, setShowProfileModal] = useState(false);
  const [showPasswordModal, setShowPasswordModal] = useState(false);
  const { titleId: profileTitleId, containerRef: profileContainerRef } = useOwnedDialog(showProfileModal);
  const { titleId: passwordTitleId, containerRef: passwordContainerRef } = useOwnedDialog(showPasswordModal);

  // Profile form state
  const [displayName, setDisplayName] = useState('');
  const [email, setEmail] = useState('');
  const [savingProfile, setSavingProfile] = useState(false);

  // Password form state
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [savingPassword, setSavingPassword] = useState(false);
  const closeProfile = () => { if (!savingProfile) setShowProfileModal(false); };
  const closePassword = () => { if (!savingPassword) setShowPasswordModal(false); };

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

  // Show whenever there is a real session to act on.
  //
  // This used to also require useAuthRequired(), which was harmless only
  // because `user` was necessarily null whenever auth was not required. That
  // is no longer true: bead enhancedchannelmanager-p388h makes /login
  // reachable on an auth-disabled instance that holds an operator identity,
  // so an operator can now hold a genuine session in that mode (which is how
  // bead jy006's three gated surfaces are meant to be reached). Keeping the
  // extra condition would have shown that operator no identity and, worse, no
  // way to sign out. `user` alone is the correct gate, and it already covers
  // the unresolved and auth-disabled cases, where it stays null.
  if (isLoading) {
    return null;
  }

  // No session, but this instance HAS an operator identity to sign in as.
  //
  // Live QA on an auth-disabled instance (bead enhancedchannelmanager-9kwzp)
  // found the app shell offered zero sign-in affordances in this state: this
  // component correctly returned null with no user, nothing else in the header
  // links to /login, and ProtectedRoute's auth-off branch only renders the
  // login page for a path the operator has to type by hand. The route p388h
  // reopened was therefore reachable only by someone who already knew the URL
  // existed.
  //
  // `authStatus.setup_complete` is the whole condition. Without it there is no
  // account to sign in as and the button would lead to a login form nobody can
  // satisfy. When auth IS required and there is no session, ProtectedRoute
  // renders the login page instead of the app shell, so this button is not
  // reachable in that mode and does not duplicate it.
  if (!user) {
    if (!authStatus?.setup_complete) {
      return null;
    }
    return (
      <div className="user-menu">
        {/* `.user-menu-trigger` alone: this is the same header-band chrome as
            the signed-in trigger and wants no visual variant, so it carries no
            modifier class that the stylesheet would never define. */}
        <button
          className="user-menu-trigger"
          onClick={() => {
            // Same SPA navigation idiom LoginPage and ProtectedRoute use:
            // push the path, then wake the popstate listeners.
            window.history.pushState({}, '', '/login');
            window.dispatchEvent(new PopStateEvent('popstate'));
          }}
          title="Sign in"
        >
          <span className="material-icons user-menu-icon">login</span>
          <span className="user-menu-name">Sign in</span>
        </button>
      </div>
    );
  }

  const performLogout = async () => {
    setIsLoggingOut(true);
    try {
      await logout();
      // Page will redirect to login via ProtectedRoute
    } catch (err) {
      logger.error('Logout failed:', err);
    } finally {
      setIsLoggingOut(false);
      setIsOpen(false);
    }
  };

  // Ask before ending the session, never after. `logout()` used to be called
  // straight from here; by the time it resolved, ProtectedRoute had swapped
  // the app for the login page and any staged Edit Mode work was gone (bead
  // epic enhancedchannelmanager-r93hq). The menu closes either way — a
  // dropdown left hanging over the host's exit dialog is nobody's idea of a
  // confirmation — and `performLogout` is handed over rather than run, so the
  // host can hold it until the operator answers.
  const handleLogout = () => {
    setIsOpen(false);
    if (onRequestSignOut) {
      onRequestSignOut(performLogout);
      return;
    }
    void performLogout();
  };

  const handleOpenProfile = () => {
    setIsOpen(false);
    menuRef.current?.querySelector<HTMLButtonElement>('.user-menu-trigger')?.focus();
    setShowProfileModal(true);
  };

  const handleOpenPassword = () => {
    setIsOpen(false);
    menuRef.current?.querySelector<HTMLButtonElement>('.user-menu-trigger')?.focus();
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
        <ModalOverlay onClose={closeProfile} className="user-modal-overlay" role="dialog" aria-modal="true" aria-labelledby={profileTitleId}>
          <div className="user-modal" ref={profileContainerRef}>
            <div className="user-modal-header">
              <h3 id={profileTitleId}>Edit Profile</h3>
              <button
                className="user-modal-close"
                onClick={closeProfile}
                disabled={savingProfile}
                aria-label="Close profile dialog"
                title="Close profile dialog"
              >
                <span className="material-icons" aria-hidden="true">close</span>
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
                  onClick={closeProfile}
                  disabled={savingProfile}
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
        <ModalOverlay onClose={closePassword} className="user-modal-overlay" role="dialog" aria-modal="true" aria-labelledby={passwordTitleId}>
          <div className="user-modal" ref={passwordContainerRef}>
            <div className="user-modal-header">
              <h3 id={passwordTitleId}>Change Password</h3>
              <button
                className="user-modal-close"
                onClick={closePassword}
                disabled={savingPassword}
                aria-label="Close password dialog"
                title="Close password dialog"
              >
                <span className="material-icons" aria-hidden="true">close</span>
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
                  {/* Same enforced policy as SetupPage; bead enhancedchannelmanager-mkocf. */}
                  <p className="user-modal-hint">
                    At least 8 characters. Common and previously breached passwords are
                    rejected, and it cannot contain your username.
                  </p>
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
                  onClick={closePassword}
                  disabled={savingPassword}
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
