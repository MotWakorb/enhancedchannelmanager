/**
 * AuthSettingsSection Component
 *
 * Admin panel for configuring authentication providers and settings.
 * Allows enabling/disabling auth providers and configuring their options.
 */
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
  const [settings, setSettings] = useState<AuthSettingsPublic | null>(null);
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
        console.error('Failed to load auth settings:', err);
      } finally {
        setLoading(false);
      }
    };

    loadSettings();
  }, [isAdmin]);

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
        <div className="auth-settings-loading">
          <span className="material-icons spinning">sync</span>
          Loading authentication settings...
        </div>
      </div>
    );
  }

  return (
    <div className="auth-settings-section">
      <div className="auth-settings-header">
        <div className="header-info">
          <h3>Authentication</h3>
          <p className="header-description">
            Configure authentication providers and security settings.
          </p>
        </div>
      </div>

      {/* Global Settings */}
      <div className="auth-provider-card">
        <div className="auth-provider-header">
          <h4>Global Settings</h4>
        </div>
        <div className="auth-provider-body">
          <div className="auth-field">
            <label className="auth-checkbox-label">
              <input
                type="checkbox"
                checked={requireAuth}
                onChange={(e) => setRequireAuth(e.target.checked)}
              />
              <span>Require Authentication</span>
            </label>
            <p className="auth-field-hint">
              When disabled, the application runs in open mode (no login required).
            </p>
          </div>
        </div>
      </div>

      {/* Local Authentication */}
      <div className="auth-provider-card">
        <div className="auth-provider-header">
          <div className="auth-provider-toggle">
            <label className="auth-checkbox-label">
              <input
                type="checkbox"
                checked={localEnabled}
                onChange={(e) => setLocalEnabled(e.target.checked)}
              />
              <span>Local Authentication</span>
            </label>
          </div>
          <span className="auth-provider-badge">Username/Password</span>
        </div>
        {localEnabled && (
          <div className="auth-provider-body">
            <div className="auth-field">
              <label>Minimum Password Length</label>
              <input
                type="number"
                min={6}
                max={32}
                value={localMinPasswordLength}
                onChange={(e) => setLocalMinPasswordLength(Number(e.target.value))}
              />
            </div>
          </div>
        )}
      </div>

      {/* Dispatcharr SSO */}
      <div className="auth-provider-card">
        <div className="auth-provider-header">
          <div className="auth-provider-toggle">
            <label className="auth-checkbox-label">
              <input
                type="checkbox"
                checked={dispatcharrEnabled}
                onChange={(e) => setDispatcharrEnabled(e.target.checked)}
              />
              <span>Dispatcharr SSO</span>
            </label>
          </div>
          <span className="auth-provider-badge">External Provider</span>
        </div>
        {dispatcharrEnabled && (
          <div className="auth-provider-body">
            <p className="auth-provider-info">
              Users can log in using their Dispatcharr credentials.
              The Dispatcharr URL is configured in the main settings.
            </p>
            <div className="auth-field">
              <label className="auth-checkbox-label">
                <input
                  type="checkbox"
                  checked={dispatcharrAutoCreate}
                  onChange={(e) => setDispatcharrAutoCreate(e.target.checked)}
                />
                <span>Auto-create Users</span>
              </label>
              <p className="auth-field-hint">
                Automatically create local accounts for Dispatcharr users on first login.
              </p>
            </div>
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
