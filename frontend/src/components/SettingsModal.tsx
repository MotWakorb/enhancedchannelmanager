import { logger } from '../utils/logger';
import { useState, useEffect, useRef, memo } from 'react';
import * as api from '../services/api';
import { useNotifications } from '../contexts/NotificationContext';
import { ModalOverlay } from './ModalOverlay';
import type { DispatcharrAuthMethod, Theme } from '../services/api';
import './ModalBase.css';
import './SettingsModal.css';

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSaved: () => void;
}

export const SettingsModal = memo(function SettingsModal({ isOpen, onClose, onSaved }: SettingsModalProps) {
  const notifications = useNotifications();
  const [url, setUrl] = useState('');
  const [authMethod, setAuthMethod] = useState<DispatcharrAuthMethod>('password');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [apiKeyStored, setApiKeyStored] = useState(false);
  // Channel defaults (stored but not edited in this modal - use Settings tab)
  const [includeChannelNumberInName, setIncludeChannelNumberInName] = useState(false);
  const [channelNumberSeparator, setChannelNumberSeparator] = useState('-');
  const [removeCountryPrefix, setRemoveCountryPrefix] = useState(false);
  const [includeCountryInName, setIncludeCountryInName] = useState(false);
  const [countrySeparator, setCountrySeparator] = useState('|');
  const [timezonePreference, setTimezonePreference] = useState('both');
  const [showStreamUrls, setShowStreamUrls] = useState(true);
  const [hideAutoSyncGroups, setHideAutoSyncGroups] = useState(false);
  const [theme, setTheme] = useState<Theme>('dark');
  const [loading, setLoading] = useState(false);
  const [testing, setTesting] = useState(false);
  const [connectionVerified, setConnectionVerified] = useState<boolean | null>(null);

  // Restore from backup state
  const [showRestore, setShowRestore] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Track original URL/username to detect if auth settings changed
  const [, setOriginalUrl] = useState('');
  const [, setOriginalUsername] = useState('');

  // Store full settings so we can pass through fields this modal doesn't edit
  const [fullSettings, setFullSettings] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    if (isOpen) {
      loadSettings();
      setShowRestore(false);
    }
  }, [isOpen]);

  const loadSettings = async () => {
    try {
      const settings = await api.getSettings();
      setFullSettings(settings as unknown as Record<string, unknown>);
      setUrl(settings.url);
      setAuthMethod(settings.auth_method || 'password');
      setUsername(settings.username);
      setOriginalUrl(settings.url);
      setOriginalUsername(settings.username);
      setPassword(''); // Never load password from server
      setApiKey(''); // Never load api_key from server
      setApiKeyStored(settings.api_key_configured);
      setIncludeChannelNumberInName(settings.include_channel_number_in_name);
      setChannelNumberSeparator(settings.channel_number_separator);
      setRemoveCountryPrefix(settings.remove_country_prefix);
      setIncludeCountryInName(settings.include_country_in_name);
      setCountrySeparator(settings.country_separator);
      setTimezonePreference(settings.timezone_preference);
      setShowStreamUrls(settings.show_stream_urls);
      setHideAutoSyncGroups(settings.hide_auto_sync_groups);
      setTheme(settings.theme || 'dark');
      setConnectionVerified(null);
    } catch (err) {
      logger.error('Failed to load settings:', err);
    }
  };

  const handleTest = async () => {
    if (!url) {
      setConnectionVerified(false);
      return;
    }
    if (authMethod === 'password' && (!username || !password)) {
      setConnectionVerified(false);
      return;
    }
    if (authMethod === 'api_key' && !apiKey) {
      setConnectionVerified(false);
      return;
    }

    setTesting(true);
    setConnectionVerified(null);

    try {
      const result = await api.testConnection(
        authMethod === 'api_key'
          ? { url, auth_method: 'api_key', api_key: apiKey }
          : { url, auth_method: 'password', username, password }
      );
      setConnectionVerified(result.success);
      if (!result.success && result.message) {
        notifications.error(result.message, 'Connection Failed');
      }
    } catch (err) {
      setConnectionVerified(false);
    } finally {
      setTesting(false);
    }
  };

  const handleSave = async () => {
    // Connection must be verified before saving (button is disabled anyway, but double-check)
    if (connectionVerified !== true) {
      return;
    }

    setLoading(true);

    try {
      // Spread fullSettings first so fields this modal doesn't edit (exclusions,
      // probe settings, normalization, etc.) are preserved instead of being
      // reset to Pydantic defaults on the backend.
      await api.saveSettings({
        ...fullSettings,
        url,
        auth_method: authMethod,
        username,
        // Only send password / api_key if the user entered a new value;
        // omitting preserves the stored secret on the backend.
        ...(password ? { password } : {}),
        ...(apiKey ? { api_key: apiKey } : {}),
        include_channel_number_in_name: includeChannelNumberInName,
        channel_number_separator: channelNumberSeparator,
        remove_country_prefix: removeCountryPrefix,
        include_country_in_name: includeCountryInName,
        country_separator: countrySeparator,
        timezone_preference: timezonePreference,
        show_stream_urls: showStreamUrls,
        hide_auto_sync_groups: hideAutoSyncGroups,
        theme: theme,
      } as Parameters<typeof api.saveSettings>[0]);
      onSaved();
      onClose();
      notifications.success('Settings saved successfully');
    } catch (err) {
      logger.error('Failed to save settings:', err);
      notifications.error('Failed to save settings', 'Save Failed');
    } finally {
      setLoading(false);
    }
  };

  const handleRestoreFromBackup = async () => {
    const file = fileInputRef.current?.files?.[0];
    if (!file) {
      notifications.error('Please select a backup file', 'No File Selected');
      return;
    }

    if (!file.name.endsWith('.zip')) {
      notifications.error('Please select a .zip backup file', 'Invalid File');
      return;
    }

    setRestoring(true);

    try {
      const result = await api.restoreBackupInitial(file);
      notifications.success(`Restored ${result.restored_files.length} files from backup`);
      onSaved();
      onClose();
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Restore failed', 'Restore Failed');
    } finally {
      setRestoring(false);
    }
  };

  if (!isOpen) return null;

  return (
    <ModalOverlay onClose={onClose}>
      <div className="settings-modal modal-container modal-md">
        <div className="modal-header">
          <h2>Dispatcharr Connection Settings</h2>
          <button className="modal-close-btn" onClick={onClose}>
            <span className="material-icons">close</span>
          </button>
        </div>

        <div className="modal-body modal-body-compact">
          {!showRestore ? (
            <>
              <div className="modal-form-group">
                <label htmlFor="url">Dispatcharr URL</label>
                <input
                  id="url"
                  type="text"
                  placeholder="http://localhost:9191"
                  value={url}
                  onChange={(e) => {
                    setUrl(e.target.value);
                    setConnectionVerified(null);
                  }}
                />
              </div>

              <div className="modal-form-group">
                <label>Authentication Method</label>
                <div className="auth-method-toggle" role="tablist" aria-label="Authentication method">
                  <button
                    type="button"
                    role="tab"
                    aria-selected={authMethod === 'password'}
                    className={`auth-method-option ${authMethod === 'password' ? 'is-active' : ''}`}
                    onClick={() => {
                      setAuthMethod('password');
                      setConnectionVerified(null);
                    }}
                  >
                    Username &amp; Password
                  </button>
                  <button
                    type="button"
                    role="tab"
                    aria-selected={authMethod === 'api_key'}
                    className={`auth-method-option ${authMethod === 'api_key' ? 'is-active' : ''}`}
                    onClick={() => {
                      setAuthMethod('api_key');
                      setConnectionVerified(null);
                    }}
                  >
                    API Key
                  </button>
                </div>
                <span className="setup-hint">
                  {authMethod === 'api_key'
                    ? 'Recommended for Dispatcharr 0.23.0+. Generate a key in Dispatcharr under Account → API Keys.'
                    : 'Legacy mode. Dispatcharr 0.23.0+ limits logins to 3/min per IP — API key auth is unaffected.'}
                </span>
              </div>

              {authMethod === 'password' ? (
                <>
                  <div className="modal-form-group">
                    <label htmlFor="username">Username</label>
                    <input
                      id="username"
                      type="text"
                      placeholder="admin"
                      value={username}
                      onChange={(e) => {
                        setUsername(e.target.value);
                        setConnectionVerified(null);
                      }}
                    />
                  </div>

                  <div className="modal-form-group">
                    <label htmlFor="password">Password</label>
                    <input
                      id="password"
                      type="password"
                      placeholder="Enter password"
                      value={password}
                      onChange={(e) => {
                        setPassword(e.target.value);
                        setConnectionVerified(null);
                      }}
                    />
                  </div>
                </>
              ) : (
                <div className="modal-form-group">
                  <label htmlFor="api-key">API Key</label>
                  <input
                    id="api-key"
                    type="password"
                    placeholder={apiKeyStored ? 'Leave blank to keep stored key' : 'Paste API key'}
                    value={apiKey}
                    onChange={(e) => {
                      setApiKey(e.target.value);
                      setConnectionVerified(null);
                    }}
                    autoComplete="off"
                  />
                </div>
              )}
            </>
          ) : (
            <div className="settings-modal-restore">
              <p className="settings-modal-restore-desc">
                Upload an ECM backup file to restore all settings, database, and configuration.
              </p>
              <input
                ref={fileInputRef}
                type="file"
                accept=".zip"
                disabled={restoring}
                className="settings-modal-restore-input"
              />
            </div>
          )}
        </div>

        <div className="modal-footer">
          {!showRestore ? (
            <>
              <button
                className={`modal-btn btn-test ${connectionVerified === true ? 'btn-test-success' : connectionVerified === false ? 'btn-test-failed' : ''}`}
                onClick={handleTest}
                disabled={testing || loading}
              >
                {testing ? 'Testing...' : connectionVerified === true ? 'Connected' : connectionVerified === false ? 'Failed' : 'Test Connection'}
              </button>
              <button className="modal-btn modal-btn-primary btn-primary" onClick={handleSave} disabled={loading || connectionVerified !== true}>
                {loading ? 'Saving...' : 'Save'}
              </button>
              <button className="modal-btn btn-secondary settings-modal-restore-toggle" onClick={() => setShowRestore(true)}>
                <span className="material-icons" style={{ fontSize: '1rem', marginRight: '0.25rem' }}>restore</span>
                Restore from Backup
              </button>
            </>
          ) : (
            <>
              <button className="modal-btn btn-secondary" onClick={() => setShowRestore(false)} disabled={restoring}>
                Back
              </button>
              <button className="modal-btn modal-btn-primary btn-primary" onClick={handleRestoreFromBackup} disabled={restoring}>
                {restoring ? 'Restoring...' : 'Restore'}
              </button>
            </>
          )}
        </div>
      </div>
    </ModalOverlay>
  );
});
