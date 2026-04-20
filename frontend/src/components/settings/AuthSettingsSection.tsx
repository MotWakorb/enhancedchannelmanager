/**
 * AuthSettingsSection Component
 *
 * Admin panel for configuring authentication providers and settings.
 * Allows enabling/disabling auth providers and configuring their options.
 */
import { logger } from '../../utils/logger';
import { useState, useEffect, useCallback } from 'react';
import * as api from '../../services/api';
import type { AuthSettingsPublic, AuthSettingsUpdate } from '../../types';
import { useNotifications } from '../../contexts/NotificationContext';
import './AuthSettingsSection.css';

interface Props {
  isAdmin: boolean;
}

export function AuthSettingsSection({ isAdmin }: Props) {
  const notifications = useNotifications();
  const [, setSettings] = useState<AuthSettingsPublic | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  // Form state for each provider
  const [localEnabled, setLocalEnabled] = useState(true);
  const [localMinPasswordLength, setLocalMinPasswordLength] = useState(8);

  const [dispatcharrEnabled, setDispatcharrEnabled] = useState(false);
  const [dispatcharrAutoCreate, setDispatcharrAutoCreate] = useState(true);

  const [requireAuth, setRequireAuth] = useState(true);

  // Load settings on mount
  useEffect(() => {
    if (!isAdmin) return;

    const loadSettings = async () => {
      try {
        setLoading(true);
        const data = await api.getAuthSettings();
        setSettings(data);

        // Populate form state
        setLocalEnabled(data.local_enabled);
        setLocalMinPasswordLength(data.local_min_password_length);

        setDispatcharrEnabled(data.dispatcharr_enabled);
        setDispatcharrAutoCreate(data.dispatcharr_auto_create_users);

        setRequireAuth(data.require_auth);
      } catch (err) {
        notifications.error('Failed to load authentication settings', 'Auth Settings');
        logger.error('Failed to load auth settings:', err);
      } finally {
        setLoading(false);
      }
    };

    loadSettings();
  }, [isAdmin, notifications]);

  const handleSave = useCallback(async () => {
    setSaving(true);

    const update: AuthSettingsUpdate = {
      require_auth: requireAuth,
      local_enabled: localEnabled,
      local_min_password_length: localMinPasswordLength,
      dispatcharr_enabled: dispatcharrEnabled,
      dispatcharr_auto_create_users: dispatcharrAutoCreate,
    };

    try {
      await api.updateAuthSettings(update);
      notifications.success('Authentication settings saved');
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to save settings';
      notifications.error(message, 'Auth Settings');
    } finally {
      setSaving(false);
    }
  }, [
    requireAuth,
    localEnabled, localMinPasswordLength,
    dispatcharrEnabled, dispatcharrAutoCreate,
    notifications,
  ]);

  if (!isAdmin) {
    return (
      <div className="auth-settings-section">
        <p className="auth-settings-no-access">Admin access required to view authentication settings.</p>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="auth-settings-section">
        <div className="loading-state">
          <span className="material-icons spinning">sync</span>
          Loading authentication settings...
        </div>
      </div>
    );
  }

  return (
    <div className="auth-settings-section">
      <div className="settings-page-header">
        <h2>Authentication</h2>
        <p>Configure authentication providers and security settings.</p>
      </div>

      {/* Global Settings */}
      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">security</span>
          <h3>Global Settings</h3>
        </div>
        <div className="form-group-vertical">
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={requireAuth}
              onChange={(e) => setRequireAuth(e.target.checked)}
            />
            <span>Require Authentication</span>
          </label>
          <p className="form-description">
            When disabled, the application runs in open mode (no login required).
          </p>
        </div>
      </div>

      {/* Local Authentication */}
      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">password</span>
          <h3>Local Authentication</h3>
        </div>
        <div className="form-group-vertical">
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={localEnabled}
              onChange={(e) => setLocalEnabled(e.target.checked)}
            />
            <span>Enable local authentication</span>
          </label>
          <p className="form-description">
            Allow users to log in with a username and password stored locally.
          </p>
        </div>
        {localEnabled && (
          <div className="form-group-vertical">
            <label>Minimum Password Length</label>
            <span className="form-description">Minimum number of characters required for user passwords (6-32).</span>
            <input
              type="number"
              min={6}
              max={32}
              value={localMinPasswordLength}
              onChange={(e) => setLocalMinPasswordLength(Number(e.target.value))}
            />
          </div>
        )}
      </div>

      {/* Dispatcharr SSO */}
      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">link</span>
          <h3>Dispatcharr SSO</h3>
        </div>
        <div className="form-group-vertical">
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={dispatcharrEnabled}
              onChange={(e) => setDispatcharrEnabled(e.target.checked)}
            />
            <span>Enable Dispatcharr SSO</span>
          </label>
          <p className="form-description">
            Allow users to log in using their Dispatcharr credentials. The Dispatcharr URL is configured in General settings.
          </p>
        </div>
        {dispatcharrEnabled && (
          <div className="form-group-vertical">
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={dispatcharrAutoCreate}
                onChange={(e) => setDispatcharrAutoCreate(e.target.checked)}
              />
              <span>Auto-create Users</span>
            </label>
            <p className="form-description">
              Automatically create local accounts for Dispatcharr users on first login.
            </p>
          </div>
        )}
      </div>

      {/* Save Button */}
      <div className="auth-settings-actions">
        <button
          className="auth-save-button"
          onClick={handleSave}
          disabled={saving}
        >
          {saving ? 'Saving...' : 'Save Authentication Settings'}
        </button>
      </div>
    </div>
  );
}

export default AuthSettingsSection;
