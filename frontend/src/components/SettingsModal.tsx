import { useState, useEffect, memo } from 'react';
import * as api from '../services/api';
import { useNotifications } from '../contexts/NotificationContext';
import { ModalOverlay } from './ModalOverlay';
import type { Theme } from '../services/api';
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
    } catch (err) {
      console.error('Failed to load settings:', err);
    }
  };

  const handleTest = async () => {
    if (!url || !username || !password) {
      setConnectionVerified(false);
      return;
    }

    setTesting(true);
    setConnectionVerified(null);

    try {
      const result = await api.testConnection({ url, username, password });
      setConnectionVerified(result.success);
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
      notifications.success('Settings saved successfully');
    } catch (err) {
      console.error('Failed to save settings:', err);
      notifications.error('Failed to save settings', 'Save Failed');
    } finally {
      setLoading(false);
    }
  };

  if (!isOpen) return null;

  return (
    <ModalOverlay onClose={onClose}>
      <div className="settings-modal modal-container modal-sm">
        <div className="modal-header">
          <h2>Dispatcharr Connection Settings</h2>
          <button className="modal-close-btn" onClick={onClose}>
            <span className="material-icons">close</span>
          </button>
        </div>

        <div className="modal-body modal-body-compact">
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

        </div>

        <div className="modal-footer">
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
        </div>
      </div>
    </ModalOverlay>
  );
});
