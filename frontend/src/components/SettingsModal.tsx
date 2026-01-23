import { useState, useEffect, memo } from 'react';
import * as api from '../services/api';
import type { Theme } from '../services/api';
import './ModalBase.css';
import './SettingsModal.css';

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSaved: () => void;
}

export const SettingsModal = memo(function SettingsModal({ isOpen, onClose, onSaved }: SettingsModalProps) {
  const [url, setUrl] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
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
  const [error, setError] = useState<string | null>(null);

  // Track original URL/username to detect if auth settings changed
  const [originalUrl, setOriginalUrl] = useState('');
  const [originalUsername, setOriginalUsername] = useState('');

  useEffect(() => {
    if (isOpen) {
      loadSettings();
    }
  }, [isOpen]);

  const loadSettings = async () => {
    try {
      const settings = await api.getSettings();
      setUrl(settings.url);
      setUsername(settings.username);
      setOriginalUrl(settings.url);
      setOriginalUsername(settings.username);
      setPassword(''); // Never load password from server
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
      setError(null);
    } catch (err) {
      console.error('Failed to load settings:', err);
    }
  };

  const handleTest = async () => {
    if (!url || !username || !password) {
      setError('URL, username, and password are required to test connection');
      return;
    }

    setTesting(true);
    setConnectionVerified(null);
    setError(null);

    try {
      const result = await api.testConnection({ url, username, password });
      setConnectionVerified(result.success);
      if (!result.success) {
        setError(result.message || 'Connection failed');
      }
    } catch (err) {
      setConnectionVerified(false);
      setError('Failed to test connection');
    } finally {
      setTesting(false);
    }
  };

  const handleSave = async () => {
    // Check if auth settings (URL or username) have changed
    const authChanged = url !== originalUrl || username !== originalUsername;
    const isNewSetup = !originalUrl && !originalUsername;

    // Validate required fields
    if (!url || !username) {
      setError('URL and username are required');
      return;
    }

    // Password is only required if auth settings changed or new setup
    if ((authChanged || isNewSetup) && !password) {
      setError('Password is required when changing URL or username');
      return;
    }

    // Connection must be verified before saving
    if (connectionVerified !== true) {
      setError('Please test the connection before saving');
      return;
    }

    setLoading(true);
    setError(null);

    try {
      await api.saveSettings({
        url,
        username,
        // Only send password if it was entered
        ...(password ? { password } : {}),
        include_channel_number_in_name: includeChannelNumberInName,
        channel_number_separator: channelNumberSeparator,
        remove_country_prefix: removeCountryPrefix,
        include_country_in_name: includeCountryInName,
        country_separator: countrySeparator,
        timezone_preference: timezonePreference,
        show_stream_urls: showStreamUrls,
        hide_auto_sync_groups: hideAutoSyncGroups,
        theme: theme,
      });
      onSaved();
      onClose();
    } catch (err) {
      setError('Failed to save settings');
    } finally {
      setLoading(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="modal-overlay">
      <div className="modal-container modal-sm modal-content" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Dispatcharr Connection Settings</h2>
          <button className="modal-close-btn close-btn" onClick={onClose}>
            <span className="material-icons">close</span>
          </button>
        </div>

        <div className="modal-body modal-body-compact">
          <div className="modal-form-group form-group">
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

          <div className="modal-form-group form-group">
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

          <div className="modal-form-group form-group">
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

          {error && <div className="error-message">{error}</div>}
        </div>

        <div className="modal-footer">
          <button className="modal-btn modal-btn-secondary btn-secondary" onClick={onClose} disabled={loading}>
            Cancel
          </button>
          <button
            className={`modal-btn btn-test ${connectionVerified === true ? 'btn-test-success' : connectionVerified === false ? 'btn-test-failed' : ''}`}
            onClick={handleTest}
            disabled={testing || loading}
          >
            <span className={`material-icons ${testing ? 'spinning' : ''}`}>
              {testing ? 'sync' : connectionVerified === true ? 'check_circle' : connectionVerified === false ? 'error' : 'wifi_tethering'}
            </span>
            {testing ? 'Testing...' : connectionVerified === true ? 'Connected' : connectionVerified === false ? 'Failed' : 'Test Connection'}
          </button>
          <button className="modal-btn modal-btn-primary btn-primary" onClick={handleSave} disabled={loading || connectionVerified !== true}>
            {loading ? 'Saving...' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  );
});
