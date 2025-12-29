import { useState, useEffect } from 'react';
import * as api from '../services/api';
import './SettingsModal.css';

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSaved: () => void;
}

export function SettingsModal({ isOpen, onClose, onSaved }: SettingsModalProps) {
  const [url, setUrl] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

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
      setPassword(''); // Never load password from server
      setTestResult(null);
      setError(null);
    } catch (err) {
      console.error('Failed to load settings:', err);
    }
  };

  const handleTest = async () => {
    if (!url || !username || !password) {
      setError('All fields are required');
      return;
    }

    setTesting(true);
    setTestResult(null);
    setError(null);

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
    if (!url || !username || !password) {
      setError('All fields are required');
      return;
    }

    setLoading(true);
    setError(null);

    try {
      await api.saveSettings({ url, username, password });
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
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Dispatcharr Connection Settings</h2>
          <button className="close-btn" onClick={onClose}>
            &times;
          </button>
        </div>

        <div className="modal-body">
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
          </div>

          {error && <div className="error-message">{error}</div>}

          {testResult && (
            <div className={`test-result ${testResult.success ? 'success' : 'error'}`}>
              {testResult.message}
            </div>
          )}
        </div>

        <div className="modal-footer">
          <button className="btn-secondary" onClick={onClose} disabled={loading}>
            Cancel
          </button>
          <button className="btn-test" onClick={handleTest} disabled={testing || loading}>
            {testing ? 'Testing...' : 'Test Connection'}
          </button>
          <button className="btn-primary" onClick={handleSave} disabled={loading}>
            {loading ? 'Saving...' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  );
}
