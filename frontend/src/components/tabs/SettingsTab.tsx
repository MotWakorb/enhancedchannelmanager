import { useState, useEffect } from 'react';
import * as api from '../../services/api';
import type { Theme } from '../../services/api';
import type { ChannelProfile } from '../../types';
import './SettingsTab.css';

interface SettingsTabProps {
  onSaved: () => void;
  onThemeChange?: (theme: Theme) => void;
  channelProfiles?: ChannelProfile[];
}

type SettingsPage = 'general' | 'channel-defaults' | 'appearance' | 'about';

export function SettingsTab({ onSaved, onThemeChange, channelProfiles = [] }: SettingsTabProps) {
  const [activePage, setActivePage] = useState<SettingsPage>('general');

  // Connection settings
  const [url, setUrl] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');

  // Channel defaults
  const [autoRenameChannelNumber, setAutoRenameChannelNumber] = useState(false);
  const [includeChannelNumberInName, setIncludeChannelNumberInName] = useState(false);
  const [channelNumberSeparator, setChannelNumberSeparator] = useState('-');
  const [removeCountryPrefix, setRemoveCountryPrefix] = useState(false);
  const [includeCountryInName, setIncludeCountryInName] = useState(false);
  const [countrySeparator, setCountrySeparator] = useState('|');
  const [timezonePreference, setTimezonePreference] = useState('both');
  const [defaultChannelProfileId, setDefaultChannelProfileId] = useState<number | null>(null);

  // Appearance settings
  const [showStreamUrls, setShowStreamUrls] = useState(true);
  const [hideAutoSyncGroups, setHideAutoSyncGroups] = useState(false);
  const [hideUngroupedStreams, setHideUngroupedStreams] = useState(true);
  const [theme, setTheme] = useState<Theme>('dark');

  // UI state
  const [loading, setLoading] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saveSuccess, setSaveSuccess] = useState(false);

  // Track original URL/username to detect if auth settings changed
  const [originalUrl, setOriginalUrl] = useState('');
  const [originalUsername, setOriginalUsername] = useState('');

  useEffect(() => {
    loadSettings();
  }, []);

  const loadSettings = async () => {
    try {
      const settings = await api.getSettings();
      setUrl(settings.url);
      setUsername(settings.username);
      setOriginalUrl(settings.url);
      setOriginalUsername(settings.username);
      setPassword(''); // Never load password from server
      setAutoRenameChannelNumber(settings.auto_rename_channel_number);
      setIncludeChannelNumberInName(settings.include_channel_number_in_name);
      setChannelNumberSeparator(settings.channel_number_separator);
      setRemoveCountryPrefix(settings.remove_country_prefix);
      setIncludeCountryInName(settings.include_country_in_name);
      setCountrySeparator(settings.country_separator);
      setTimezonePreference(settings.timezone_preference);
      setShowStreamUrls(settings.show_stream_urls);
      setHideAutoSyncGroups(settings.hide_auto_sync_groups);
      setHideUngroupedStreams(settings.hide_ungrouped_streams);
      setTheme(settings.theme || 'dark');
      setDefaultChannelProfileId(settings.default_channel_profile_id);
      setTestResult(null);
      setError(null);
    } catch (err) {
      console.error('Failed to load settings:', err);
    }
  };

  // Handle theme change with immediate preview
  const handleThemeChange = (newTheme: Theme) => {
    setTheme(newTheme);
    // Apply theme immediately for preview
    document.documentElement.setAttribute('data-theme', newTheme === 'dark' ? '' : newTheme);
    onThemeChange?.(newTheme);
  };

  const handleTest = async () => {
    if (!url || !username || !password) {
      setError('URL, username, and password are required to test connection');
      return;
    }

    setTesting(true);
    setTestResult(null);
    setError(null);
    setSaveSuccess(false);

    try {
      const result = await api.testConnection({ url, username, password });
      setTestResult(result);
    } catch (err) {
      setTestResult({ success: false, message: 'Failed to test connection' });
    } finally {
      setTesting(false);
    }
  };

  const handleSave = async () => {
    // Check if auth settings (URL or username) have changed
    const authChanged = url !== originalUrl || username !== originalUsername;

    // Validate required fields
    if (!url || !username) {
      setError('URL and username are required');
      return;
    }

    // Password is only required if auth settings changed
    if (authChanged && !password) {
      setError('Password is required when changing URL or username');
      return;
    }

    setLoading(true);
    setError(null);
    setSaveSuccess(false);

    try {
      await api.saveSettings({
        url,
        username,
        // Only send password if it was entered
        ...(password ? { password } : {}),
        auto_rename_channel_number: autoRenameChannelNumber,
        include_channel_number_in_name: includeChannelNumberInName,
        channel_number_separator: channelNumberSeparator,
        remove_country_prefix: removeCountryPrefix,
        include_country_in_name: includeCountryInName,
        country_separator: countrySeparator,
        timezone_preference: timezonePreference,
        show_stream_urls: showStreamUrls,
        hide_auto_sync_groups: hideAutoSyncGroups,
        hide_ungrouped_streams: hideUngroupedStreams,
        theme: theme,
        default_channel_profile_id: defaultChannelProfileId,
      });
      setOriginalUrl(url);
      setOriginalUsername(username);
      setPassword('');
      setSaveSuccess(true);
      onSaved();
      // Clear success message after 3 seconds
      setTimeout(() => setSaveSuccess(false), 3000);
    } catch (err) {
      setError('Failed to save settings');
    } finally {
      setLoading(false);
    }
  };

  const renderGeneralPage = () => (
    <div className="settings-page">
      <div className="settings-page-header">
        <h2>General Settings</h2>
        <p>Configure your Dispatcharr connection.</p>
      </div>

      {error && (
        <div className="error-message">
          <span className="material-icons">error</span>
          {error}
        </div>
      )}

      {testResult && (
        <div className={`test-result ${testResult.success ? 'success' : 'error'}`}>
          <span className="material-icons">
            {testResult.success ? 'check_circle' : 'error'}
          </span>
          {testResult.message}
        </div>
      )}

      {saveSuccess && (
        <div className="save-success">
          <span className="material-icons">check_circle</span>
          Settings saved successfully
        </div>
      )}

      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">link</span>
          <h3>Dispatcharr Connection</h3>
        </div>

        <div className="form-group">
          <label htmlFor="url">Server URL</label>
          <input
            id="url"
            type="text"
            placeholder="http://localhost:9191"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
        </div>

        <div className="form-row">
          <div className="form-group">
            <label htmlFor="username">Username</label>
            <input
              id="username"
              type="text"
              placeholder="admin"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
          </div>

          <div className="form-group">
            <label htmlFor="password">Password</label>
            <input
              id="password"
              type="password"
              placeholder="Enter password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <p className="form-hint">Only required when changing URL or username</p>
          </div>
        </div>
      </div>

      <div className="settings-actions">
        <div className="settings-actions-left">
          <button className="btn-test" onClick={handleTest} disabled={testing || loading}>
            <span className="material-icons">wifi_tethering</span>
            {testing ? 'Testing...' : 'Test Connection'}
          </button>
        </div>
        <button className="btn-primary" onClick={handleSave} disabled={loading}>
          <span className="material-icons">save</span>
          {loading ? 'Saving...' : 'Save Settings'}
        </button>
      </div>
    </div>
  );

  const renderAppearancePage = () => (
    <div className="settings-page">
      <div className="settings-page-header">
        <h2>Appearance</h2>
        <p>Customize how the app displays information.</p>
      </div>

      {saveSuccess && (
        <div className="save-success">
          <span className="material-icons">check_circle</span>
          Settings saved successfully
        </div>
      )}

      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">palette</span>
          <h3>Theme</h3>
        </div>

        <div className="theme-selector">
          <label className={`theme-option ${theme === 'dark' ? 'active' : ''}`}>
            <input
              type="radio"
              name="theme"
              value="dark"
              checked={theme === 'dark'}
              onChange={() => handleThemeChange('dark')}
            />
            <span className="theme-preview dark-preview">
              <span className="material-icons">dark_mode</span>
            </span>
            <span className="theme-label">Dark</span>
            <span className="theme-description">Default dark theme for low-light environments</span>
          </label>

          <label className={`theme-option ${theme === 'light' ? 'active' : ''}`}>
            <input
              type="radio"
              name="theme"
              value="light"
              checked={theme === 'light'}
              onChange={() => handleThemeChange('light')}
            />
            <span className="theme-preview light-preview">
              <span className="material-icons">light_mode</span>
            </span>
            <span className="theme-label">Light</span>
            <span className="theme-description">Bright theme for well-lit environments</span>
          </label>

          <label className={`theme-option ${theme === 'high-contrast' ? 'active' : ''}`}>
            <input
              type="radio"
              name="theme"
              value="high-contrast"
              checked={theme === 'high-contrast'}
              onChange={() => handleThemeChange('high-contrast')}
            />
            <span className="theme-preview high-contrast-preview">
              <span className="material-icons">contrast</span>
            </span>
            <span className="theme-label">High Contrast</span>
            <span className="theme-description">Maximum contrast for accessibility</span>
          </label>
        </div>
      </div>

      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">visibility</span>
          <h3>Display Options</h3>
        </div>

        <div className="checkbox-group">
          <input
            id="showStreamUrls"
            type="checkbox"
            checked={showStreamUrls}
            onChange={(e) => setShowStreamUrls(e.target.checked)}
          />
          <div className="checkbox-content">
            <label htmlFor="showStreamUrls">Show stream URLs in the UI</label>
            <p>
              Display the full stream URL below each stream and channel. Disable this for cleaner
              screenshots or to hide sensitive URL information.
            </p>
          </div>
        </div>

        <div className="checkbox-group">
          <input
            id="hideAutoSyncGroups"
            type="checkbox"
            checked={hideAutoSyncGroups}
            onChange={(e) => setHideAutoSyncGroups(e.target.checked)}
          />
          <div className="checkbox-content">
            <label htmlFor="hideAutoSyncGroups">Hide auto-sync groups by default</label>
            <p>
              Automatically hide channel groups that are managed by Dispatcharr's M3U auto-sync feature.
              You can still show them using the filter in the Channel Manager tab.
            </p>
          </div>
        </div>

        <div className="checkbox-group">
          <input
            id="hideUngroupedStreams"
            type="checkbox"
            checked={hideUngroupedStreams}
            onChange={(e) => setHideUngroupedStreams(e.target.checked)}
          />
          <div className="checkbox-content">
            <label htmlFor="hideUngroupedStreams">Hide ungrouped streams</label>
            <p>
              Hide streams that don't have a group assigned (no group-title in M3U).
              These streams appear under "Ungrouped" in the Streams pane.
            </p>
          </div>
        </div>
      </div>

      <div className="settings-actions">
        <div className="settings-actions-left" />
        <button className="btn-primary" onClick={handleSave} disabled={loading}>
          <span className="material-icons">save</span>
          {loading ? 'Saving...' : 'Save Settings'}
        </button>
      </div>
    </div>
  );

  const renderChannelDefaultsPage = () => (
    <div className="settings-page">
      <div className="settings-page-header">
        <h2>Channel Defaults</h2>
        <p>Configure default options for bulk channel creation.</p>
      </div>

      {error && (
        <div className="error-message">
          <span className="material-icons">error</span>
          {error}
        </div>
      )}

      {saveSuccess && (
        <div className="save-success">
          <span className="material-icons">check_circle</span>
          Settings saved successfully
        </div>
      )}

      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">edit</span>
          <h3>Channel Naming</h3>
        </div>

        <div className="checkbox-group">
          <input
            id="autoRename"
            type="checkbox"
            checked={autoRenameChannelNumber}
            onChange={(e) => setAutoRenameChannelNumber(e.target.checked)}
          />
          <div className="checkbox-content">
            <label htmlFor="autoRename">Auto-rename channel when number changes</label>
            <p>
              When enabled, if a channel name contains the old channel number, it will be
              automatically updated to the new number.
            </p>
          </div>
        </div>

        <div className="checkbox-group">
          <input
            id="includeNumber"
            type="checkbox"
            checked={includeChannelNumberInName}
            onChange={(e) => setIncludeChannelNumberInName(e.target.checked)}
          />
          <div className="checkbox-content">
            <label htmlFor="includeNumber">Include channel number in name</label>
            <p>
              Add the channel number as a prefix when creating channels (e.g., "101 - Sports Channel").
            </p>
          </div>
        </div>

        {includeChannelNumberInName && (
          <div className="form-group indent">
            <label htmlFor="separator">Number separator</label>
            <select
              id="separator"
              value={channelNumberSeparator}
              onChange={(e) => setChannelNumberSeparator(e.target.value)}
            >
              <option value="-">Hyphen (101 - Channel)</option>
              <option value=":">Colon (101: Channel)</option>
              <option value="|">Pipe (101 | Channel)</option>
            </select>
          </div>
        )}

        <div className="checkbox-group">
          <input
            id="removeCountry"
            type="checkbox"
            checked={removeCountryPrefix}
            onChange={(e) => {
              setRemoveCountryPrefix(e.target.checked);
              // If enabling remove, disable include
              if (e.target.checked) {
                setIncludeCountryInName(false);
              }
            }}
          />
          <div className="checkbox-content">
            <label htmlFor="removeCountry">Remove country prefix from names</label>
            <p>
              Strip country codes (US, UK, CA, etc.) from channel names when creating channels.
            </p>
          </div>
        </div>

        <div className="checkbox-group">
          <input
            id="includeCountry"
            type="checkbox"
            checked={includeCountryInName}
            onChange={(e) => {
              setIncludeCountryInName(e.target.checked);
              // If enabling include, disable remove
              if (e.target.checked) {
                setRemoveCountryPrefix(false);
              }
            }}
          />
          <div className="checkbox-content">
            <label htmlFor="includeCountry">Include country prefix in name (normalized)</label>
            <p>
              Keep country codes in channel names with a consistent separator (e.g., "US | Sports Channel").
            </p>
          </div>
        </div>

        {includeCountryInName && (
          <div className="form-group indent">
            <label htmlFor="countrySeparator">Country separator</label>
            <select
              id="countrySeparator"
              value={countrySeparator}
              onChange={(e) => setCountrySeparator(e.target.value)}
            >
              <option value="-">Hyphen (US - Channel)</option>
              <option value=":">Colon (US: Channel)</option>
              <option value="|">Pipe (US | Channel)</option>
            </select>
          </div>
        )}
      </div>

      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">schedule</span>
          <h3>Timezone Preference</h3>
        </div>

        <div className="form-group">
          <label htmlFor="timezone">Default timezone for regional channel variants</label>
          <select
            id="timezone"
            value={timezonePreference}
            onChange={(e) => setTimezonePreference(e.target.value)}
          >
            <option value="east">East Coast (prefer East feeds)</option>
            <option value="west">West Coast (prefer West feeds)</option>
            <option value="both">Keep Both (create separate channels)</option>
          </select>
          <p className="form-hint">
            When streams have East/West variants, this determines which to use by default.
          </p>
        </div>
      </div>

      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">people</span>
          <h3>Channel Profiles</h3>
        </div>

        <div className="form-group">
          <label htmlFor="defaultProfile">Default channel profile for new channels</label>
          <select
            id="defaultProfile"
            value={defaultChannelProfileId ?? ''}
            onChange={(e) => setDefaultChannelProfileId(e.target.value ? Number(e.target.value) : null)}
          >
            <option value="">None (don't auto-add to profiles)</option>
            {channelProfiles.map((profile) => (
              <option key={profile.id} value={profile.id}>
                {profile.name}
              </option>
            ))}
          </select>
          <p className="form-hint">
            Newly created channels will automatically be added to this profile.
            {channelProfiles.length === 0 && (
              <span className="form-hint-warning"> No profiles available. Create profiles in the Channel Manager.</span>
            )}
          </p>
        </div>
      </div>

      <div className="settings-actions">
        <div className="settings-actions-left" />
        <button className="btn-primary" onClick={handleSave} disabled={loading}>
          <span className="material-icons">save</span>
          {loading ? 'Saving...' : 'Save Settings'}
        </button>
      </div>
    </div>
  );

  return (
    <div className="settings-tab">
      <nav className="settings-sidebar">
        <ul className="settings-nav">
          <li
            className={`settings-nav-item ${activePage === 'general' ? 'active' : ''}`}
            onClick={() => setActivePage('general')}
          >
            <span className="material-icons">settings</span>
            General
          </li>
          <li
            className={`settings-nav-item ${activePage === 'channel-defaults' ? 'active' : ''}`}
            onClick={() => setActivePage('channel-defaults')}
          >
            <span className="material-icons">tv</span>
            Channel Defaults
          </li>
          <li
            className={`settings-nav-item ${activePage === 'appearance' ? 'active' : ''}`}
            onClick={() => setActivePage('appearance')}
          >
            <span className="material-icons">palette</span>
            Appearance
          </li>
          <li className="settings-nav-item disabled">
            <span className="material-icons">info</span>
            About
          </li>
        </ul>
      </nav>

      <div className="settings-content">
        {activePage === 'general' && renderGeneralPage()}
        {activePage === 'channel-defaults' && renderChannelDefaultsPage()}
        {activePage === 'appearance' && renderAppearancePage()}
      </div>
    </div>
  );
}
