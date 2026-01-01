import { useState, useEffect } from 'react';
import * as api from '../../services/api';
import './SettingsTab.css';

interface SettingsTabProps {
  onSaved: () => void;
}

export function SettingsTab({ onSaved }: SettingsTabProps) {
  const [url, setUrl] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [autoRenameChannelNumber, setAutoRenameChannelNumber] = useState(false);
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
      setTestResult(null);
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

  return (
    <div className="settings-tab">
      <div className="settings-container">
        <h2>Dispatcharr Connection Settings</h2>

        <div className="settings-section">
          <h3>Connection</h3>

          <div className="form-group">
            <label htmlFor="url">Dispatcharr URL</label>
            <input
              id="url"
              type="text"
              placeholder="http://localhost:9191"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
            />
          </div>

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

        <div className="settings-section">
          <h3>Channel Options</h3>

          <div className="form-group checkbox-group">
            <label htmlFor="autoRename" className="checkbox-label">
              <input
                id="autoRename"
                type="checkbox"
                checked={autoRenameChannelNumber}
                onChange={(e) => setAutoRenameChannelNumber(e.target.checked)}
              />
              <span>Auto-rename channel when number changes</span>
            </label>
            <p className="form-help">
              When enabled, if a channel name contains the old channel number, it will be
              automatically updated to the new number (e.g., "101 Sports Channel" becomes "102 Sports Channel").
            </p>
          </div>
        </div>

        {error && <div className="error-message">{error}</div>}

        {testResult && (
          <div className={`test-result ${testResult.success ? 'success' : 'error'}`}>
            {testResult.message}
          </div>
        )}

        {saveSuccess && (
          <div className="save-success">
            <span className="material-icons">check_circle</span>
            Settings saved successfully
          </div>
        )}

        <div className="settings-actions">
          <button className="btn-test" onClick={handleTest} disabled={testing || loading}>
            {testing ? 'Testing...' : 'Test Connection'}
          </button>
          <button className="btn-primary" onClick={handleSave} disabled={loading}>
            {loading ? 'Saving...' : 'Save Settings'}
          </button>
        </div>
      </div>
    </div>
  );
}
