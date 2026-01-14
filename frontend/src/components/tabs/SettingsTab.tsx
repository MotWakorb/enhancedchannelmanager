import { useState, useEffect } from 'react';
import * as api from '../../services/api';
import { NETWORK_PREFIXES, NETWORK_SUFFIXES } from '../../services/api';
import type { Theme } from '../../services/api';
import type { ChannelProfile } from '../../types';
import { logger } from '../../utils/logger';
import type { LogLevel as FrontendLogLevel } from '../../utils/logger';
import { DeleteOrphanedGroupsModal } from '../DeleteOrphanedGroupsModal';
import './SettingsTab.css';

interface SettingsTabProps {
  onSaved: () => void;
  onThemeChange?: (theme: Theme) => void;
  channelProfiles?: ChannelProfile[];
}

type SettingsPage = 'general' | 'channel-defaults' | 'appearance' | 'maintenance';

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
  const [defaultChannelProfileIds, setDefaultChannelProfileIds] = useState<number[]>([]);
  const [epgAutoMatchThreshold, setEpgAutoMatchThreshold] = useState(80);
  const [customNetworkPrefixes, setCustomNetworkPrefixes] = useState<string[]>([]);
  const [newPrefixInput, setNewPrefixInput] = useState('');
  const [customNetworkSuffixes, setCustomNetworkSuffixes] = useState<string[]>([]);
  const [newSuffixInput, setNewSuffixInput] = useState('');

  // Appearance settings
  const [showStreamUrls, setShowStreamUrls] = useState(true);
  const [hideAutoSyncGroups, setHideAutoSyncGroups] = useState(false);
  const [hideUngroupedStreams, setHideUngroupedStreams] = useState(true);
  const [theme, setTheme] = useState<Theme>('dark');
  const [vlcOpenBehavior, setVlcOpenBehavior] = useState('m3u_fallback');

  // Stats settings
  const [statsPollInterval, setStatsPollInterval] = useState(10);
  const [userTimezone, setUserTimezone] = useState('');

  // Log level settings
  const [backendLogLevel, setBackendLogLevel] = useState('INFO');
  const [frontendLogLevel, setFrontendLogLevel] = useState('INFO');

  // Preserve settings not managed by this tab (to avoid overwriting them on save)
  const [linkedM3UAccounts, setLinkedM3UAccounts] = useState<number[][]>([]);

  // UI state
  const [loading, setLoading] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saveSuccess, setSaveSuccess] = useState(false);

  // Maintenance state
  const [orphanedGroups, setOrphanedGroups] = useState<{ id: number; name: string; reason?: string }[]>([]);
  const [loadingOrphaned, setLoadingOrphaned] = useState(false);
  const [cleaningOrphaned, setCleaningOrphaned] = useState(false);
  const [cleanupResult, setCleanupResult] = useState<string | null>(null);
  const [showDeleteModal, setShowDeleteModal] = useState(false);

  // Track original URL/username to detect if auth settings changed
  const [originalUrl, setOriginalUrl] = useState('');
  const [originalUsername, setOriginalUsername] = useState('');

  // Track original poll interval and timezone to detect if restart is needed
  const [originalPollInterval, setOriginalPollInterval] = useState(10);
  const [originalTimezone, setOriginalTimezone] = useState('');
  const [needsRestart, setNeedsRestart] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [restartResult, setRestartResult] = useState<{ success: boolean; message: string } | null>(null);

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
      setVlcOpenBehavior(settings.vlc_open_behavior || 'm3u_fallback');
      setDefaultChannelProfileIds(settings.default_channel_profile_ids);
      setEpgAutoMatchThreshold(settings.epg_auto_match_threshold ?? 80);
      setCustomNetworkPrefixes(settings.custom_network_prefixes ?? []);
      setCustomNetworkSuffixes(settings.custom_network_suffixes ?? []);
      setStatsPollInterval(settings.stats_poll_interval ?? 10);
      setOriginalPollInterval(settings.stats_poll_interval ?? 10);
      setUserTimezone(settings.user_timezone ?? '');
      setOriginalTimezone(settings.user_timezone ?? '');
      setBackendLogLevel(settings.backend_log_level ?? 'INFO');
      const frontendLevel = settings.frontend_log_level ?? 'INFO';
      setFrontendLogLevel(frontendLevel);
      // Apply frontend log level immediately
      const frontendLogLevel = frontendLevel === 'WARNING' ? 'WARN' : frontendLevel;
      if (['DEBUG', 'INFO', 'WARN', 'ERROR'].includes(frontendLogLevel)) {
        logger.setLevel(frontendLogLevel as FrontendLogLevel);
      }
      setLinkedM3UAccounts(settings.linked_m3u_accounts ?? []);
      setNeedsRestart(false);
      setRestartResult(null);
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
        default_channel_profile_ids: defaultChannelProfileIds,
        epg_auto_match_threshold: epgAutoMatchThreshold,
        custom_network_prefixes: customNetworkPrefixes,
        custom_network_suffixes: customNetworkSuffixes,
        stats_poll_interval: statsPollInterval,
        user_timezone: userTimezone,
        backend_log_level: backendLogLevel,
        frontend_log_level: frontendLogLevel,
        vlc_open_behavior: vlcOpenBehavior,
        linked_m3u_accounts: linkedM3UAccounts,
      });
      // Apply frontend log level immediately
      const frontendLevel = frontendLogLevel === 'WARNING' ? 'WARN' : frontendLogLevel;
      if (['DEBUG', 'INFO', 'WARN', 'ERROR'].includes(frontendLevel)) {
        logger.setLevel(frontendLevel as FrontendLogLevel);
        logger.info(`Frontend log level changed to ${frontendLevel}`);
      }
      // Update global VLC settings for vlc utility to access
      (window as any).__vlcSettings = { behavior: vlcOpenBehavior };
      setOriginalUrl(url);
      setOriginalUsername(username);
      setPassword('');
      setSaveSuccess(true);
      logger.info('Settings saved successfully');
      // Check if poll interval or timezone changed and needs restart
      if (statsPollInterval !== originalPollInterval || userTimezone !== originalTimezone) {
        setNeedsRestart(true);
        logger.info('Stats polling or timezone changed - backend restart recommended');
      }
      onSaved();
      // Clear success message after 8 seconds
      setTimeout(() => setSaveSuccess(false), 8000);
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to save settings';
      logger.error('Failed to save settings', err);
      setError(errorMessage);
    } finally {
      setLoading(false);
    }
  };

  const handleRestart = async () => {
    setRestarting(true);
    setRestartResult(null);
    try {
      const result = await api.restartServices();
      setRestartResult(result);
      if (result.success) {
        setOriginalPollInterval(statsPollInterval);
        setOriginalTimezone(userTimezone);
        setNeedsRestart(false);
        // Clear result after 3 seconds
        setTimeout(() => setRestartResult(null), 3000);
      }
    } catch (err) {
      setRestartResult({ success: false, message: 'Failed to restart services' });
    } finally {
      setRestarting(false);
    }
  };

  const handleLoadOrphanedGroups = async () => {
    setLoadingOrphaned(true);
    setCleanupResult(null);
    try {
      const result = await api.getOrphanedChannelGroups();
      setOrphanedGroups(result.orphaned_groups);
      if (result.orphaned_groups.length === 0) {
        setCleanupResult('No orphaned groups found. Your database is clean!');
      }
    } catch (err) {
      setCleanupResult(`Failed to load orphaned groups: ${err}`);
    } finally {
      setLoadingOrphaned(false);
    }
  };

  const handleCleanupOrphanedGroups = async () => {
    // Show the confirmation modal
    setShowDeleteModal(true);
  };

  const handleConfirmDelete = async (selectedGroupIds: number[]) => {
    setCleaningOrphaned(true);
    setCleanupResult(null);
    try {
      const result = await api.deleteOrphanedChannelGroups(selectedGroupIds);
      setCleanupResult(result.message);

      if (result.deleted_groups.length > 0) {
        // Reload to refresh the list
        await handleLoadOrphanedGroups();
        // Notify parent to refresh data
        onSaved();
      }

      if (result.failed_groups.length > 0) {
        const failedNames = result.failed_groups.map(g => g.name).join(', ');
        setCleanupResult(`${result.message}. Failed to delete: ${failedNames}`);
      }
    } catch (err) {
      setCleanupResult(`Failed to cleanup orphaned groups: ${err}`);
    } finally {
      setCleaningOrphaned(false);
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

        <div className="test-connection-row">
          <button className="btn-test" onClick={handleTest} disabled={testing || loading}>
            <span className="material-icons">wifi_tethering</span>
            {testing ? 'Testing...' : 'Test Connection'}
          </button>
        </div>
      </div>

      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">speed</span>
          <h3>Stats Polling</h3>
        </div>

        <div className="form-group">
          <div className="threshold-label-row">
            <label htmlFor="statsPollInterval">Poll interval (seconds)</label>
            <div className="threshold-input-group">
              <input
                id="statsPollInterval"
                type="number"
                min="5"
                max="300"
                value={statsPollInterval}
                onChange={(e) => {
                  const value = Number(e.target.value);
                  if (!isNaN(value)) {
                    setStatsPollInterval(value);
                  }
                }}
                onBlur={(e) => {
                  const value = Math.max(5, Math.min(300, Number(e.target.value) || 10));
                  setStatsPollInterval(value);
                }}
                className="threshold-input"
              />
              <span className="threshold-percent">sec</span>
            </div>
          </div>
          <p className="form-hint">
            How often to poll Dispatcharr for channel statistics and bandwidth tracking.
            Lower values provide more frequent updates but use more resources.
          </p>

          {needsRestart && (
            <div className="restart-notice">
              <span className="material-icons">info</span>
              <span>Stats settings changed. Restart services to apply.</span>
              <button
                className="btn-restart"
                onClick={handleRestart}
                disabled={restarting}
              >
                <span className={`material-icons ${restarting ? 'spinning' : ''}`}>
                  {restarting ? 'sync' : 'restart_alt'}
                </span>
                {restarting ? 'Restarting...' : 'Restart Now'}
              </button>
            </div>
          )}

          {restartResult && (
            <div className={`restart-result ${restartResult.success ? 'success' : 'error'}`}>
              <span className="material-icons">
                {restartResult.success ? 'check_circle' : 'error'}
              </span>
              {restartResult.message}
            </div>
          )}
        </div>

        <div className="form-group">
          <div className="threshold-label-row">
            <label htmlFor="userTimezone">Timezone for stats</label>
            <select
              id="userTimezone"
              value={userTimezone}
              onChange={(e) => setUserTimezone(e.target.value)}
              className="timezone-select"
            >
              <option value="">UTC (Default)</option>
              <optgroup label="US & Canada">
                <option value="America/New_York">Eastern Time (ET)</option>
                <option value="America/Chicago">Central Time (CT)</option>
                <option value="America/Denver">Mountain Time (MT)</option>
                <option value="America/Los_Angeles">Pacific Time (PT)</option>
                <option value="America/Anchorage">Alaska Time (AKT)</option>
                <option value="Pacific/Honolulu">Hawaii Time (HT)</option>
              </optgroup>
              <optgroup label="Europe">
                <option value="Europe/London">London (GMT/BST)</option>
                <option value="Europe/Paris">Paris (CET/CEST)</option>
                <option value="Europe/Berlin">Berlin (CET/CEST)</option>
                <option value="Europe/Amsterdam">Amsterdam (CET/CEST)</option>
                <option value="Europe/Rome">Rome (CET/CEST)</option>
                <option value="Europe/Madrid">Madrid (CET/CEST)</option>
              </optgroup>
              <optgroup label="Asia & Pacific">
                <option value="Asia/Tokyo">Tokyo (JST)</option>
                <option value="Asia/Shanghai">Shanghai (CST)</option>
                <option value="Asia/Hong_Kong">Hong Kong (HKT)</option>
                <option value="Asia/Singapore">Singapore (SGT)</option>
                <option value="Asia/Dubai">Dubai (GST)</option>
                <option value="Australia/Sydney">Sydney (AEST/AEDT)</option>
                <option value="Australia/Melbourne">Melbourne (AEST/AEDT)</option>
                <option value="Pacific/Auckland">Auckland (NZST/NZDT)</option>
              </optgroup>
            </select>
          </div>
          <p className="form-hint">
            Timezone used for daily bandwidth statistics. "Today" will roll over at midnight in your selected timezone.
          </p>
        </div>
      </div>

      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">bug_report</span>
          <h3>Logging</h3>
        </div>

        <div className="form-group">
          <label htmlFor="backendLogLevel">Backend Log Level</label>
          <select
            id="backendLogLevel"
            value={backendLogLevel}
            onChange={(e) => setBackendLogLevel(e.target.value)}
          >
            <option value="DEBUG">DEBUG - Show all messages including debug info</option>
            <option value="INFO">INFO - Show informational messages and above</option>
            <option value="WARNING">WARNING - Show warnings and errors only</option>
            <option value="ERROR">ERROR - Show errors only</option>
            <option value="CRITICAL">CRITICAL - Show only critical errors</option>
          </select>
          <p className="form-hint">
            Controls Python backend logging level. Changes apply immediately.
            Check Docker logs to see backend messages.
          </p>
        </div>

        <div className="form-group">
          <label htmlFor="frontendLogLevel">Frontend Log Level</label>
          <select
            id="frontendLogLevel"
            value={frontendLogLevel}
            onChange={(e) => setFrontendLogLevel(e.target.value)}
          >
            <option value="DEBUG">DEBUG - Show all messages including debug info</option>
            <option value="INFO">INFO - Show informational messages and above</option>
            <option value="WARN">WARN - Show warnings and errors only</option>
            <option value="ERROR">ERROR - Show errors only</option>
          </select>
          <p className="form-hint">
            Controls browser console logging level. Changes apply immediately.
            Open browser DevTools (F12) to see frontend messages.
          </p>
        </div>
      </div>

      {saveSuccess && (
        <div className="save-success">
          <span className="material-icons">check_circle</span>
          Settings saved successfully
        </div>
      )}

      <div className="settings-actions">
        <div className="settings-actions-left" />
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

      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">play_circle</span>
          <h3>VLC Integration</h3>
        </div>

        <div className="form-group">
          <label htmlFor="vlcOpenBehavior">Open in VLC Behavior</label>
          <select
            id="vlcOpenBehavior"
            value={vlcOpenBehavior}
            onChange={(e) => setVlcOpenBehavior(e.target.value)}
          >
            <option value="protocol_only">Try VLC Protocol (show helper if it fails)</option>
            <option value="m3u_fallback">Try VLC Protocol, then fallback to M3U download</option>
            <option value="m3u_only">Always download M3U file</option>
          </select>
          <p className="form-hint">
            Controls what happens when you click "Open in VLC". The vlc:// protocol requires
            browser extensions on some platforms. If "protocol_only" fails, a helper modal
            will guide you to install the necessary extension.
          </p>
        </div>
      </div>

      {saveSuccess && (
        <div className="save-success">
          <span className="material-icons">check_circle</span>
          Settings saved successfully
        </div>
      )}

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
          <div className="separator-row indent">
            <span className="separator-row-label">Separator:</span>
            <div className="separator-buttons">
              <button
                type="button"
                className={`separator-btn ${channelNumberSeparator === '-' ? 'active' : ''}`}
                onClick={() => setChannelNumberSeparator('-')}
              >
                -
              </button>
              <button
                type="button"
                className={`separator-btn ${channelNumberSeparator === ':' ? 'active' : ''}`}
                onClick={() => setChannelNumberSeparator(':')}
              >
                :
              </button>
              <button
                type="button"
                className={`separator-btn ${channelNumberSeparator === '|' ? 'active' : ''}`}
                onClick={() => setChannelNumberSeparator('|')}
              >
                |
              </button>
            </div>
            <span className="separator-preview">e.g., "101 {channelNumberSeparator} Sports Channel"</span>
          </div>
        )}

        <div className="form-group">
          <label>Country prefix handling</label>
          <div className="radio-group">
            <label className="radio-option">
              <input
                type="radio"
                name="countryPrefix"
                checked={removeCountryPrefix}
                onChange={() => {
                  setRemoveCountryPrefix(true);
                  setIncludeCountryInName(false);
                }}
              />
              <span className="radio-label">Remove</span>
              <span className="radio-description">Strip country codes (US, UK, CA) from names</span>
            </label>
            <label className="radio-option">
              <input
                type="radio"
                name="countryPrefix"
                checked={!removeCountryPrefix && !includeCountryInName}
                onChange={() => {
                  setRemoveCountryPrefix(false);
                  setIncludeCountryInName(false);
                }}
              />
              <span className="radio-label">Keep as-is</span>
              <span className="radio-description">Leave country prefixes unchanged</span>
            </label>
            <label className="radio-option">
              <input
                type="radio"
                name="countryPrefix"
                checked={includeCountryInName}
                onChange={() => {
                  setRemoveCountryPrefix(false);
                  setIncludeCountryInName(true);
                }}
              />
              <span className="radio-label">Normalize</span>
              <span className="radio-description">Keep with consistent separator</span>
            </label>
          </div>
        </div>

        {includeCountryInName && (
          <div className="separator-row indent">
            <span className="separator-row-label">Separator:</span>
            <div className="separator-buttons">
              <button
                type="button"
                className={`separator-btn ${countrySeparator === '-' ? 'active' : ''}`}
                onClick={() => setCountrySeparator('-')}
              >
                -
              </button>
              <button
                type="button"
                className={`separator-btn ${countrySeparator === ':' ? 'active' : ''}`}
                onClick={() => setCountrySeparator(':')}
              >
                :
              </button>
              <button
                type="button"
                className={`separator-btn ${countrySeparator === '|' ? 'active' : ''}`}
                onClick={() => setCountrySeparator('|')}
              >
                |
              </button>
            </div>
            <span className="separator-preview">e.g., "US {countrySeparator} Sports Channel"</span>
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
          <label>Default profiles for new channels</label>
          <p className="form-hint" style={{ marginTop: 0, marginBottom: '0.75rem' }}>
            Newly created channels will automatically be added to the selected profiles.
            {channelProfiles.length === 0 && (
              <span className="form-hint-warning"> No profiles available. Create profiles in the Channel Manager.</span>
            )}
          </p>
          {channelProfiles.length > 0 && (
            <div className="profile-checkbox-list">
              {channelProfiles.map((profile) => (
                <label key={profile.id} className="profile-checkbox">
                  <input
                    type="checkbox"
                    checked={defaultChannelProfileIds.includes(profile.id)}
                    onChange={(e) => {
                      if (e.target.checked) {
                        setDefaultChannelProfileIds([...defaultChannelProfileIds, profile.id]);
                      } else {
                        setDefaultChannelProfileIds(defaultChannelProfileIds.filter(id => id !== profile.id));
                      }
                    }}
                  />
                  <span className="profile-checkbox-label">{profile.name}</span>
                </label>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">live_tv</span>
          <h3>EPG Matching</h3>
        </div>

        <div className="form-group">
          <div className="threshold-label-row">
            <label htmlFor="epgThreshold">Auto-match confidence threshold</label>
            <div className="threshold-input-group">
              <input
                id="epgThreshold"
                type="number"
                min="0"
                max="100"
                value={epgAutoMatchThreshold}
                onChange={(e) => {
                  const value = Math.max(0, Math.min(100, Number(e.target.value) || 0));
                  setEpgAutoMatchThreshold(value);
                }}
                className="threshold-input"
              />
              <span className="threshold-percent">%</span>
            </div>
          </div>
          <p className="form-hint">
            EPG matches with a confidence score at or above this threshold will be automatically assigned.
            Lower values match more channels automatically but may be less accurate.
            Set to 0 to require manual review for all matches.
          </p>
        </div>
      </div>

      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">label</span>
          <h3>Custom Network Prefixes</h3>
        </div>

        <div className="form-group">
          <p className="form-hint" style={{ marginTop: 0, marginBottom: '0.75rem' }}>
            Add custom prefixes to strip during bulk channel creation. These are merged with the built-in
            list (CHAMP, PPV, NFL, NBA, etc.) when "Strip network prefixes" is enabled.
          </p>

          <div className="custom-prefix-input-row">
            <input
              type="text"
              placeholder="Enter prefix (e.g., MARQUEE)"
              value={newPrefixInput}
              onChange={(e) => setNewPrefixInput(e.target.value.toUpperCase())}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && newPrefixInput.trim()) {
                  e.preventDefault();
                  const prefix = newPrefixInput.trim();
                  // Check if prefix already exists in custom list or built-in list
                  if (!customNetworkPrefixes.includes(prefix) && !NETWORK_PREFIXES.includes(prefix)) {
                    setCustomNetworkPrefixes([...customNetworkPrefixes, prefix]);
                  }
                  setNewPrefixInput('');
                }
              }}
              className="custom-prefix-input"
            />
            <button
              type="button"
              className="btn-secondary custom-prefix-add-btn"
              onClick={() => {
                const prefix = newPrefixInput.trim();
                // Check if prefix already exists in custom list or built-in list
                if (prefix && !customNetworkPrefixes.includes(prefix) && !NETWORK_PREFIXES.includes(prefix)) {
                  setCustomNetworkPrefixes([...customNetworkPrefixes, prefix]);
                }
                setNewPrefixInput('');
              }}
              disabled={!newPrefixInput.trim() || NETWORK_PREFIXES.includes(newPrefixInput.trim()) || customNetworkPrefixes.includes(newPrefixInput.trim())}
            >
              <span className="material-icons">add</span>
              Add
            </button>
          </div>

          {newPrefixInput.trim() && NETWORK_PREFIXES.includes(newPrefixInput.trim()) && (
            <p className="custom-prefix-warning">
              "{newPrefixInput.trim()}" is already a built-in prefix
            </p>
          )}

          {newPrefixInput.trim() && !NETWORK_PREFIXES.includes(newPrefixInput.trim()) && customNetworkPrefixes.includes(newPrefixInput.trim()) && (
            <p className="custom-prefix-warning">
              "{newPrefixInput.trim()}" is already in your custom list
            </p>
          )}

          {customNetworkPrefixes.length > 0 && (
            <div className="custom-prefix-list">
              {customNetworkPrefixes.map((prefix) => (
                <div key={prefix} className="custom-prefix-tag">
                  <span>{prefix}</span>
                  <button
                    type="button"
                    className="custom-prefix-remove"
                    onClick={() => setCustomNetworkPrefixes(customNetworkPrefixes.filter(p => p !== prefix))}
                    title="Remove prefix"
                  >
                    <span className="material-icons">close</span>
                  </button>
                </div>
              ))}
            </div>
          )}

          {customNetworkPrefixes.length === 0 && (
            <p className="custom-prefix-empty">No custom prefixes defined. Built-in prefixes will be used.</p>
          )}
        </div>
      </div>

      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">label_off</span>
          <h3>Custom Network Suffixes</h3>
        </div>

        <div className="form-group">
          <p className="form-hint" style={{ marginTop: 0, marginBottom: '0.75rem' }}>
            Add custom suffixes to strip during bulk channel creation. These are merged with the built-in
            list (ENGLISH, LIVE, BACKUP, etc.) when "Strip network suffixes" is enabled.
          </p>

          <div className="custom-prefix-input-row">
            <input
              type="text"
              placeholder="Enter suffix (e.g., SIMULCAST)"
              value={newSuffixInput}
              onChange={(e) => setNewSuffixInput(e.target.value.toUpperCase())}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && newSuffixInput.trim()) {
                  e.preventDefault();
                  const suffix = newSuffixInput.trim();
                  // Check if suffix already exists in custom list or built-in list
                  if (!customNetworkSuffixes.includes(suffix) && !NETWORK_SUFFIXES.includes(suffix)) {
                    setCustomNetworkSuffixes([...customNetworkSuffixes, suffix]);
                  }
                  setNewSuffixInput('');
                }
              }}
              className="custom-prefix-input"
            />
            <button
              type="button"
              className="btn-secondary custom-prefix-add-btn"
              onClick={() => {
                const suffix = newSuffixInput.trim();
                // Check if suffix already exists in custom list or built-in list
                if (suffix && !customNetworkSuffixes.includes(suffix) && !NETWORK_SUFFIXES.includes(suffix)) {
                  setCustomNetworkSuffixes([...customNetworkSuffixes, suffix]);
                }
                setNewSuffixInput('');
              }}
              disabled={!newSuffixInput.trim() || NETWORK_SUFFIXES.includes(newSuffixInput.trim()) || customNetworkSuffixes.includes(newSuffixInput.trim())}
            >
              <span className="material-icons">add</span>
              Add
            </button>
          </div>

          {newSuffixInput.trim() && NETWORK_SUFFIXES.includes(newSuffixInput.trim()) && (
            <p className="custom-prefix-warning">
              "{newSuffixInput.trim()}" is already a built-in suffix
            </p>
          )}

          {newSuffixInput.trim() && !NETWORK_SUFFIXES.includes(newSuffixInput.trim()) && customNetworkSuffixes.includes(newSuffixInput.trim()) && (
            <p className="custom-prefix-warning">
              "{newSuffixInput.trim()}" is already in your custom list
            </p>
          )}

          {customNetworkSuffixes.length > 0 && (
            <div className="custom-prefix-list">
              {customNetworkSuffixes.map((suffix) => (
                <div key={suffix} className="custom-prefix-tag">
                  <span>{suffix}</span>
                  <button
                    type="button"
                    className="custom-prefix-remove"
                    onClick={() => setCustomNetworkSuffixes(customNetworkSuffixes.filter(s => s !== suffix))}
                    title="Remove suffix"
                  >
                    <span className="material-icons">close</span>
                  </button>
                </div>
              ))}
            </div>
          )}

          {customNetworkSuffixes.length === 0 && (
            <p className="custom-prefix-empty">No custom suffixes defined. Built-in suffixes will be used.</p>
          )}
        </div>
      </div>

      {saveSuccess && (
        <div className="save-success">
          <span className="material-icons">check_circle</span>
          Settings saved successfully
        </div>
      )}

      <div className="settings-actions">
        <div className="settings-actions-left" />
        <button className="btn-primary" onClick={handleSave} disabled={loading}>
          <span className="material-icons">save</span>
          {loading ? 'Saving...' : 'Save Settings'}
        </button>
      </div>
    </div>
  );

  const renderMaintenancePage = () => (
    <div className="settings-page">
      <div className="settings-page-header">
        <h2>Maintenance</h2>
        <p>Database cleanup and maintenance tools.</p>
      </div>

      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">folder_delete</span>
          <h3>Orphaned Channel Groups</h3>
        </div>
        <p className="form-hint" style={{ marginBottom: '1rem' }}>
          Channel groups that are not associated with any M3U account and have no content (no streams or channels). These are typically leftover from deleted M3U accounts and are safe to delete.
        </p>

        <div className="settings-group">
          <button
            className="btn-secondary"
            onClick={handleLoadOrphanedGroups}
            disabled={loadingOrphaned || cleaningOrphaned}
          >
            <span className="material-icons">search</span>
            {loadingOrphaned ? 'Scanning...' : 'Scan for Orphaned Groups'}
          </button>

          {orphanedGroups.length > 0 && (
            <div style={{ marginTop: '1rem' }}>
              <p><strong>Found {orphanedGroups.length} orphaned group(s):</strong></p>
              <ul style={{ marginLeft: '1.5rem', marginTop: '0.5rem' }}>
                {orphanedGroups.map(group => (
                  <li key={group.id}>
                    <strong>{group.name}</strong> (ID: {group.id})
                    {group.reason && <span style={{ color: '#888', marginLeft: '0.5rem' }}>- {group.reason}</span>}
                  </li>
                ))}
              </ul>
              <button
                className="btn-danger"
                onClick={handleCleanupOrphanedGroups}
                disabled={cleaningOrphaned || loadingOrphaned}
                style={{ marginTop: '1rem' }}
              >
                <span className="material-icons">delete_forever</span>
                {cleaningOrphaned ? 'Cleaning...' : `Delete ${orphanedGroups.length} Orphaned Group(s)`}
              </button>
            </div>
          )}

          {cleanupResult && (
            <div className={cleanupResult.includes('Failed') ? 'error-message' : 'success-message'} style={{ marginTop: '1rem' }}>
              <span className="material-icons">{cleanupResult.includes('Failed') ? 'error' : 'check_circle'}</span>
              {cleanupResult}
            </div>
          )}
        </div>
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
          <li
            className={`settings-nav-item ${activePage === 'maintenance' ? 'active' : ''}`}
            onClick={() => setActivePage('maintenance')}
          >
            <span className="material-icons">build</span>
            Maintenance
          </li>
        </ul>
      </nav>

      <div className="settings-content">
        {activePage === 'general' && renderGeneralPage()}
        {activePage === 'channel-defaults' && renderChannelDefaultsPage()}
        {activePage === 'appearance' && renderAppearancePage()}
        {activePage === 'maintenance' && renderMaintenancePage()}
      </div>

      <DeleteOrphanedGroupsModal
        isOpen={showDeleteModal}
        onClose={() => setShowDeleteModal(false)}
        onConfirm={handleConfirmDelete}
        groups={orphanedGroups}
      />
    </div>
  );
}
