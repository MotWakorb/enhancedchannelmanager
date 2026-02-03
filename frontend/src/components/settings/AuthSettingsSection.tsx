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
  const [error, setError] = useState<string | null>(null);

  // Form state for each provider
  const [localEnabled, setLocalEnabled] = useState(true);
  const [localAllowRegistration, setLocalAllowRegistration] = useState(false);
  const [localMinPasswordLength, setLocalMinPasswordLength] = useState(8);

  const [dispatcharrEnabled, setDispatcharrEnabled] = useState(false);
  const [dispatcharrAutoCreate, setDispatcharrAutoCreate] = useState(true);

  const [oidcEnabled, setOidcEnabled] = useState(false);
  const [oidcProviderName, setOidcProviderName] = useState('');
  const [oidcDiscoveryUrl, setOidcDiscoveryUrl] = useState('');
  const [oidcClientId, setOidcClientId] = useState('');
  const [oidcClientSecret, setOidcClientSecret] = useState('');
  const [oidcAutoCreate, setOidcAutoCreate] = useState(true);

  const [ldapEnabled, setLdapEnabled] = useState(false);
  const [ldapServerUrl, setLdapServerUrl] = useState('');
  const [ldapUseSsl, setLdapUseSsl] = useState(false);
  const [ldapUseTls, setLdapUseTls] = useState(true);
  const [ldapBindDn, setLdapBindDn] = useState('');
  const [ldapBindPassword, setLdapBindPassword] = useState('');
  const [ldapUserSearchBase, setLdapUserSearchBase] = useState('');
  const [ldapAutoCreate, setLdapAutoCreate] = useState(true);

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
        setLocalAllowRegistration(data.local_allow_registration);
        setLocalMinPasswordLength(data.local_min_password_length);

        setDispatcharrEnabled(data.dispatcharr_enabled);
        setDispatcharrAutoCreate(data.dispatcharr_auto_create_users);

        setOidcEnabled(data.oidc_enabled);
        setOidcProviderName(data.oidc_provider_name);
        setOidcDiscoveryUrl(data.oidc_discovery_url);
        setOidcAutoCreate(data.oidc_auto_create_users);

        setLdapEnabled(data.ldap_enabled);
        setLdapServerUrl(data.ldap_server_url);
        setLdapUseSsl(data.ldap_use_ssl);
        setLdapUseTls(data.ldap_use_tls);
        setLdapUserSearchBase(data.ldap_user_search_base);
        setLdapAutoCreate(data.ldap_auto_create_users);

        setRequireAuth(data.require_auth);
      } catch (err) {
        setError('Failed to load authentication settings');
        console.error('Failed to load auth settings:', err);
      } finally {
        setLoading(false);
      }
    };

    loadSettings();
  }, [isAdmin]);

  const handleSave = useCallback(async () => {
    setSaving(true);
    setError(null);

    const update: AuthSettingsUpdate = {
      require_auth: requireAuth,
      local_enabled: localEnabled,
      local_allow_registration: localAllowRegistration,
      local_min_password_length: localMinPasswordLength,
      dispatcharr_enabled: dispatcharrEnabled,
      dispatcharr_auto_create_users: dispatcharrAutoCreate,
      oidc_enabled: oidcEnabled,
      oidc_provider_name: oidcProviderName,
      oidc_discovery_url: oidcDiscoveryUrl,
      oidc_auto_create_users: oidcAutoCreate,
      ldap_enabled: ldapEnabled,
      ldap_server_url: ldapServerUrl,
      ldap_use_ssl: ldapUseSsl,
      ldap_use_tls: ldapUseTls,
      ldap_user_search_base: ldapUserSearchBase,
      ldap_auto_create_users: ldapAutoCreate,
    };

    // Only include secrets if they were entered
    if (oidcClientId) update.oidc_client_id = oidcClientId;
    if (oidcClientSecret) update.oidc_client_secret = oidcClientSecret;
    if (ldapBindDn) update.ldap_bind_dn = ldapBindDn;
    if (ldapBindPassword) update.ldap_bind_password = ldapBindPassword;

    try {
      await api.updateAuthSettings(update);
      notifications.success('Authentication settings saved');
      // Clear secrets after save
      setOidcClientSecret('');
      setLdapBindPassword('');
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to save settings';
      setError(message);
      notifications.error(message);
    } finally {
      setSaving(false);
    }
  }, [
    requireAuth,
    localEnabled, localAllowRegistration, localMinPasswordLength,
    dispatcharrEnabled, dispatcharrAutoCreate,
    oidcEnabled, oidcProviderName, oidcDiscoveryUrl, oidcClientId, oidcClientSecret, oidcAutoCreate,
    ldapEnabled, ldapServerUrl, ldapUseSsl, ldapUseTls, ldapBindDn, ldapBindPassword, ldapUserSearchBase, ldapAutoCreate,
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

      {error && (
        <div className="auth-settings-error">
          <span className="material-icons">error</span>
          {error}
          <button onClick={() => setError(null)}>
            <span className="material-icons">close</span>
          </button>
        </div>
      )}

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
              <label className="auth-checkbox-label">
                <input
                  type="checkbox"
                  checked={localAllowRegistration}
                  onChange={(e) => setLocalAllowRegistration(e.target.checked)}
                />
                <span>Allow User Registration</span>
              </label>
              <p className="auth-field-hint">
                Allow new users to register accounts themselves.
              </p>
            </div>
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

      {/* OIDC */}
      <div className="auth-provider-card">
        <div className="auth-provider-header">
          <div className="auth-provider-toggle">
            <label className="auth-checkbox-label">
              <input
                type="checkbox"
                checked={oidcEnabled}
                onChange={(e) => setOidcEnabled(e.target.checked)}
              />
              <span>OpenID Connect (OIDC)</span>
            </label>
          </div>
          <span className="auth-provider-badge auth-provider-badge-coming-soon">Coming Soon</span>
        </div>
        {oidcEnabled && (
          <div className="auth-provider-body">
            <div className="auth-field">
              <label>Provider Name</label>
              <input
                type="text"
                value={oidcProviderName}
                onChange={(e) => setOidcProviderName(e.target.value)}
                placeholder="e.g., Google, Okta, Auth0"
              />
            </div>
            <div className="auth-field">
              <label>Discovery URL</label>
              <input
                type="url"
                value={oidcDiscoveryUrl}
                onChange={(e) => setOidcDiscoveryUrl(e.target.value)}
                placeholder="https://provider/.well-known/openid-configuration"
              />
            </div>
            <div className="auth-field">
              <label>Client ID</label>
              <input
                type="text"
                value={oidcClientId}
                onChange={(e) => setOidcClientId(e.target.value)}
                placeholder="Enter client ID"
              />
            </div>
            <div className="auth-field">
              <label>Client Secret</label>
              <input
                type="password"
                value={oidcClientSecret}
                onChange={(e) => setOidcClientSecret(e.target.value)}
                placeholder="Enter to change"
              />
            </div>
            <div className="auth-field">
              <label className="auth-checkbox-label">
                <input
                  type="checkbox"
                  checked={oidcAutoCreate}
                  onChange={(e) => setOidcAutoCreate(e.target.checked)}
                />
                <span>Auto-create Users</span>
              </label>
            </div>
          </div>
        )}
      </div>

      {/* LDAP */}
      <div className="auth-provider-card">
        <div className="auth-provider-header">
          <div className="auth-provider-toggle">
            <label className="auth-checkbox-label">
              <input
                type="checkbox"
                checked={ldapEnabled}
                onChange={(e) => setLdapEnabled(e.target.checked)}
              />
              <span>LDAP / Active Directory</span>
            </label>
          </div>
          <span className="auth-provider-badge auth-provider-badge-coming-soon">Coming Soon</span>
        </div>
        {ldapEnabled && (
          <div className="auth-provider-body">
            <div className="auth-field">
              <label>Server URL</label>
              <input
                type="url"
                value={ldapServerUrl}
                onChange={(e) => setLdapServerUrl(e.target.value)}
                placeholder="ldap://ldap.example.com:389"
              />
            </div>
            <div className="auth-field-row">
              <div className="auth-field">
                <label className="auth-checkbox-label">
                  <input
                    type="checkbox"
                    checked={ldapUseSsl}
                    onChange={(e) => setLdapUseSsl(e.target.checked)}
                  />
                  <span>Use SSL (LDAPS)</span>
                </label>
              </div>
              <div className="auth-field">
                <label className="auth-checkbox-label">
                  <input
                    type="checkbox"
                    checked={ldapUseTls}
                    onChange={(e) => setLdapUseTls(e.target.checked)}
                  />
                  <span>Use StartTLS</span>
                </label>
              </div>
            </div>
            <div className="auth-field">
              <label>Bind DN</label>
              <input
                type="text"
                value={ldapBindDn}
                onChange={(e) => setLdapBindDn(e.target.value)}
                placeholder="cn=admin,dc=example,dc=com"
              />
            </div>
            <div className="auth-field">
              <label>Bind Password</label>
              <input
                type="password"
                value={ldapBindPassword}
                onChange={(e) => setLdapBindPassword(e.target.value)}
                placeholder="Enter to change"
              />
            </div>
            <div className="auth-field">
              <label>User Search Base</label>
              <input
                type="text"
                value={ldapUserSearchBase}
                onChange={(e) => setLdapUserSearchBase(e.target.value)}
                placeholder="ou=users,dc=example,dc=com"
              />
            </div>
            <div className="auth-field">
              <label className="auth-checkbox-label">
                <input
                  type="checkbox"
                  checked={ldapAutoCreate}
                  onChange={(e) => setLdapAutoCreate(e.target.checked)}
                />
                <span>Auto-create Users</span>
              </label>
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
