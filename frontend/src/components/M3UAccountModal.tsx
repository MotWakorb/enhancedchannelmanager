import { useState, useEffect, memo, useRef } from 'react';
import type { M3UAccount, M3UAccountType, ServerGroup } from '../types';
import * as api from '../services/api';
import { useAsyncOperation } from '../hooks/useAsyncOperation';
import { ModalOverlay } from './ModalOverlay';
import './ModalBase.css';
import './M3UAccountModal.css';

// UI-only account type that includes HDHR (which gets converted to STD for API)
type UIAccountType = M3UAccountType | 'HDHR';

// Helper to detect if a URL is an HD Homerun lineup URL
function isHDHomerunUrl(url: string): boolean {
  if (!url) return false;
  try {
    const parsed = new URL(url);
    return parsed.pathname === '/lineup.m3u' || parsed.pathname === '/lineup.m3u8';
  } catch {
    return false;
  }
}

// Helper to extract IP/host from HD Homerun URL
function extractHDHomerunIP(url: string): string {
  if (!url) return '';
  try {
    const parsed = new URL(url);
    return parsed.host; // Returns host:port or just host
  } catch {
    return '';
  }
}

interface M3UAccountModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSaved: () => void;
  account: M3UAccount | null; // null = creating new
  serverGroups: ServerGroup[];
}

export const M3UAccountModal = memo(function M3UAccountModal({
  isOpen,
  onClose,
  onSaved,
  account,
  serverGroups,
}: M3UAccountModalProps) {
  const isEdit = account !== null;

  // Form state
  const [name, setName] = useState('');
  const [accountType, setAccountType] = useState<UIAccountType>('STD');
  const [serverUrl, setServerUrl] = useState('');
  const [hdhrIP, setHdhrIP] = useState(''); // HD Homerun IP address
  const [filePath, setFilePath] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [serverGroup, setServerGroup] = useState<number | null>(null);
  const [maxStreams, setMaxStreams] = useState(0);
  const [refreshInterval, setRefreshInterval] = useState(24);
  const [staleStreamDays, setStaleStreamDays] = useState(7);
  const [enableVod, setEnableVod] = useState(false);
  const [autoEnableLive, setAutoEnableLive] = useState(true);
  const [autoEnableVod, setAutoEnableVod] = useState(false);
  const [autoEnableSeries, setAutoEnableSeries] = useState(false);
  const [isActive, setIsActive] = useState(true);

  // File upload state
  const [uploadedFileName, setUploadedFileName] = useState('');
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Server group dropdown state
  const [serverGroupDropdownOpen, setServerGroupDropdownOpen] = useState(false);
  const serverGroupDropdownRef = useRef<HTMLDivElement>(null);

  // UI state
  const { loading, error, execute, setError, clearError } = useAsyncOperation();

  // Reset form when modal opens/closes or account changes
  useEffect(() => {
    if (isOpen) {
      if (account) {
        // Editing existing account
        setName(account.name);
        setFilePath(account.file_path || '');
        setUploadedFileName('');
        setUsername(account.username || '');
        setPassword(''); // Never pre-fill password
        setServerGroup(account.server_group);
        setMaxStreams(account.max_streams);
        setRefreshInterval(account.refresh_interval);
        setStaleStreamDays(account.stale_stream_days);
        setEnableVod(account.enable_vod);
        setAutoEnableLive(account.auto_enable_new_groups_live);
        setAutoEnableVod(account.auto_enable_new_groups_vod);
        setAutoEnableSeries(account.auto_enable_new_groups_series);
        setIsActive(account.is_active);

        // Detect if this is an HD Homerun account (STD with lineup.m3u URL)
        if (account.account_type === 'STD' && isHDHomerunUrl(account.server_url || '')) {
          setAccountType('HDHR');
          setHdhrIP(extractHDHomerunIP(account.server_url || ''));
          setServerUrl('');
        } else {
          setAccountType(account.account_type);
          setServerUrl(account.server_url || '');
          setHdhrIP('');
        }
      } else {
        // Creating new account - reset to defaults
        setName('');
        setAccountType('STD');
        setServerUrl('');
        setHdhrIP('');
        setFilePath('');
        setUploadedFileName('');
        setUsername('');
        setPassword('');
        setServerGroup(null);
        setMaxStreams(0);
        setRefreshInterval(24);
        setStaleStreamDays(7);
        setEnableVod(false);
        setAutoEnableLive(true);
        setAutoEnableVod(false);
        setAutoEnableSeries(false);
        setIsActive(true);
      }
      clearError();
      setServerGroupDropdownOpen(false);
    }
  }, [isOpen, account, clearError]);

  // Close server group dropdown on outside click
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (serverGroupDropdownRef.current && !serverGroupDropdownRef.current.contains(event.target as Node)) {
        setServerGroupDropdownOpen(false);
      }
    };

    if (serverGroupDropdownOpen) {
      document.addEventListener('mousedown', handleClickOutside);
      return () => document.removeEventListener('mousedown', handleClickOutside);
    }
  }, [serverGroupDropdownOpen]);

  const handleSave = async () => {
    // Validation
    if (!name.trim()) {
      setError('Name is required');
      return;
    }

    if (accountType === 'STD') {
      if (!serverUrl.trim() && !filePath.trim()) {
        setError('Either M3U URL or file path is required');
        return;
      }
    } else if (accountType === 'HDHR') {
      if (!hdhrIP.trim()) {
        setError('HD Homerun IP address is required');
        return;
      }
    } else {
      // XtreamCodes
      if (!serverUrl.trim()) {
        setError('Server URL is required for XtreamCodes accounts');
        return;
      }
      if (!username.trim() || !password.trim()) {
        setError('Username and password are required for XtreamCodes accounts');
        return;
      }
    }

    await execute(async () => {
      // Convert HDHR to STD with constructed URL
      const apiAccountType: M3UAccountType = accountType === 'HDHR' ? 'STD' : accountType;
      const apiServerUrl = accountType === 'HDHR'
        ? `http://${hdhrIP.trim()}/lineup.m3u`
        : (serverUrl.trim() || null);

      const data = {
        name: name.trim(),
        account_type: apiAccountType,
        server_url: apiServerUrl,
        file_path: accountType === 'STD' ? (filePath.trim() || null) : null,
        username: accountType === 'XC' ? (username.trim() || null) : null,
        password: accountType === 'XC' ? (password.trim() || null) : null,
        server_group: serverGroup,
        max_streams: maxStreams,
        refresh_interval: refreshInterval,
        stale_stream_days: staleStreamDays,
        enable_vod: enableVod,
        auto_enable_new_groups_live: autoEnableLive,
        auto_enable_new_groups_vod: autoEnableVod,
        auto_enable_new_groups_series: autoEnableSeries,
        is_active: isActive,
      };

      if (isEdit) {
        await api.updateM3UAccount(account!.id, data);
      } else {
        // Create account and immediately trigger refresh to avoid "Pending Setup" state
        const newAccount = await api.createM3UAccount(data);
        try {
          await api.refreshM3UAccount(newAccount.id);
          // Wait for Dispatcharr to update state before reloading data in ECM
          await new Promise(resolve => setTimeout(resolve, 2500));
        } catch (refreshErr) {
          // Don't fail the whole operation if refresh fails - account was created successfully
          console.warn('Auto-refresh failed after account creation:', refreshErr);
        }
      }

      onSaved();
      onClose();
    });
  };

  const handleFileUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;

    // Validate file type
    if (!file.name.toLowerCase().endsWith('.m3u') && !file.name.toLowerCase().endsWith('.m3u8')) {
      setError('Invalid file type. Only .m3u and .m3u8 files are allowed.');
      return;
    }

    setUploading(true);
    clearError();

    try {
      const result = await api.uploadM3UFile(file);
      setFilePath(result.file_path);
      setUploadedFileName(result.original_name);
      // Clear the URL field since we're using a file
      setServerUrl('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to upload file');
    } finally {
      setUploading(false);
      // Reset the input so the same file can be selected again
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  };

  if (!isOpen) return null;

  return (
    <ModalOverlay onClose={onClose}>
      <div className="modal-container m3u-account-modal">
        <div className="modal-header">
          <h2>{isEdit ? 'Edit M3U Account' : 'Add M3U Account'}</h2>
          <button className="modal-close-btn" onClick={onClose}>
            <span className="material-icons">close</span>
          </button>
        </div>

        <div className="modal-body">
          {/* Account Name */}
          <div className="modal-form-group">
            <label htmlFor="name">Account Name</label>
            <input
              id="name"
              type="text"
              placeholder="My IPTV Provider"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>

          {/* Account Type */}
          <div className="modal-form-group">
            <label>Account Type</label>
            <div className="radio-group">
              <label className="radio-label">
                <input
                  type="radio"
                  name="accountType"
                  checked={accountType === 'STD'}
                  onChange={() => setAccountType('STD')}
                />
                <span>Standard M3U</span>
              </label>
              <label className="radio-label">
                <input
                  type="radio"
                  name="accountType"
                  checked={accountType === 'XC'}
                  onChange={() => setAccountType('XC')}
                />
                <span>XtreamCodes</span>
              </label>
              <label className="radio-label">
                <input
                  type="radio"
                  name="accountType"
                  checked={accountType === 'HDHR'}
                  onChange={() => setAccountType('HDHR')}
                />
                <span>HD Homerun</span>
              </label>
            </div>
          </div>

          {/* Standard M3U Fields */}
          {accountType === 'STD' && (
            <>
              <div className="modal-form-group">
                <label htmlFor="serverUrl">M3U URL</label>
                <input
                  id="serverUrl"
                  type="text"
                  placeholder="http://provider.com/get.php?..."
                  value={serverUrl}
                  onChange={(e) => setServerUrl(e.target.value)}
                />
              </div>
              <div className="form-or">- or -</div>
              <div className="modal-form-group">
                <label>Upload M3U File</label>
                <div className="file-upload-row">
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept=".m3u,.m3u8"
                    onChange={handleFileUpload}
                    style={{ display: 'none' }}
                  />
                  <button
                    type="button"
                    className="modal-btn modal-btn-secondary"
                    onClick={() => fileInputRef.current?.click()}
                    disabled={uploading}
                  >
                    {uploading ? 'Uploading...' : 'Choose File'}
                  </button>
                  {uploadedFileName && (
                    <span className="uploaded-file-name">{uploadedFileName}</span>
                  )}
                </div>
              </div>
              <div className="form-or">- or -</div>
              <div className="modal-form-group">
                <label htmlFor="filePath">Server File Path</label>
                <input
                  id="filePath"
                  type="text"
                  placeholder="/path/to/playlist.m3u"
                  value={filePath}
                  onChange={(e) => {
                    setFilePath(e.target.value);
                    setUploadedFileName(''); // Clear uploaded file name when typing
                  }}
                />
                <span className="form-hint">Path to M3U file on the server</span>
              </div>
            </>
          )}

          {/* XtreamCodes Fields */}
          {accountType === 'XC' && (
            <>
              <div className="modal-form-group">
                <label htmlFor="xcServerUrl">Server URL</label>
                <input
                  id="xcServerUrl"
                  type="text"
                  placeholder="http://xtream.provider.com:8080"
                  value={serverUrl}
                  onChange={(e) => setServerUrl(e.target.value)}
                />
              </div>
              <div className="modal-form-group">
                <label htmlFor="xcUsername">Username</label>
                <input
                  id="xcUsername"
                  type="text"
                  placeholder="username"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                />
              </div>
              <div className="modal-form-group">
                <label htmlFor="xcPassword">Password</label>
                <input
                  id="xcPassword"
                  type="password"
                  placeholder="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </div>
            </>
          )}

          {/* HD Homerun Fields */}
          {accountType === 'HDHR' && (
            <div className="modal-form-group">
              <label htmlFor="hdhrIP">HD Homerun IP Address</label>
              <input
                id="hdhrIP"
                type="text"
                placeholder="192.168.1.100"
                value={hdhrIP}
                onChange={(e) => setHdhrIP(e.target.value)}
              />
              <span className="form-hint">URL will be: http://{hdhrIP || '<ip>'}/lineup.m3u</span>
            </div>
          )}

          <div className="form-group-divider" />

          {/* Common Settings */}
          <div className="modal-form-group">
            <label>Server Group</label>
            <div className="searchable-select-dropdown" ref={serverGroupDropdownRef}>
              <button
                type="button"
                className="dropdown-trigger"
                onClick={() => setServerGroupDropdownOpen(!serverGroupDropdownOpen)}
              >
                <span className="dropdown-value">
                  {serverGroup !== null
                    ? serverGroups.find(sg => sg.id === serverGroup)?.name || 'Unknown'
                    : 'None'}
                </span>
                <span className="material-icons">expand_more</span>
              </button>
              {serverGroupDropdownOpen && (
                <div className="dropdown-menu">
                  <div className="dropdown-options">
                    <div
                      className={`dropdown-option-item${serverGroup === null ? ' selected' : ''}`}
                      onClick={() => { setServerGroup(null); setServerGroupDropdownOpen(false); }}
                    >
                      None
                    </div>
                    {serverGroups.map((sg) => (
                      <div
                        key={sg.id}
                        className={`dropdown-option-item${serverGroup === sg.id ? ' selected' : ''}`}
                        onClick={() => { setServerGroup(sg.id); setServerGroupDropdownOpen(false); }}
                      >
                        {sg.name}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>

          <div className="form-row">
            <div className="modal-form-group">
              <label htmlFor="maxStreams">Max Streams</label>
              <input
                id="maxStreams"
                type="number"
                min="0"
                placeholder="0 = unlimited"
                value={maxStreams}
                onChange={(e) => setMaxStreams(Number(e.target.value) || 0)}
              />
              <span className="form-hint">0 = unlimited</span>
            </div>
            <div className="modal-form-group">
              <label htmlFor="refreshInterval">Refresh (hours)</label>
              <input
                id="refreshInterval"
                type="number"
                min="0"
                value={refreshInterval}
                onChange={(e) => setRefreshInterval(Number(e.target.value))}
              />
              <span className="form-hint">0 = manual refresh only</span>
            </div>
            <div className="modal-form-group">
              <label htmlFor="staleDays">Stale Days</label>
              <input
                id="staleDays"
                type="number"
                min="0"
                value={staleStreamDays}
                onChange={(e) => setStaleStreamDays(Number(e.target.value) || 0)}
              />
            </div>
          </div>

          <div className="form-group-divider" />

          {/* Toggles */}
          <div className="modal-form-group">
            <label className="modal-checkbox-label">
              <input
                type="checkbox"
                checked={enableVod}
                onChange={(e) => setEnableVod(e.target.checked)}
              />
              <span>Enable VOD</span>
            </label>
          </div>

          <div className="modal-form-group">
            <label className="modal-checkbox-label">
              <input
                type="checkbox"
                checked={autoEnableLive}
                onChange={(e) => setAutoEnableLive(e.target.checked)}
              />
              <span>Auto-enable new groups (Live)</span>
            </label>
          </div>

          <div className="modal-form-group">
            <label className="modal-checkbox-label">
              <input
                type="checkbox"
                checked={autoEnableVod}
                onChange={(e) => setAutoEnableVod(e.target.checked)}
              />
              <span>Auto-enable new groups (VOD)</span>
            </label>
          </div>

          <div className="modal-form-group">
            <label className="modal-checkbox-label">
              <input
                type="checkbox"
                checked={autoEnableSeries}
                onChange={(e) => setAutoEnableSeries(e.target.checked)}
              />
              <span>Auto-enable new groups (Series)</span>
            </label>
          </div>

          <div className="modal-form-group">
            <label className="modal-checkbox-label">
              <input
                type="checkbox"
                checked={isActive}
                onChange={(e) => setIsActive(e.target.checked)}
              />
              <span>Active</span>
            </label>
          </div>

          {error && <div className="error-message">{error}</div>}
        </div>

        <div className="modal-footer">
          <button className="modal-btn modal-btn-primary" onClick={handleSave} disabled={loading}>
            {loading ? 'Saving...' : isEdit ? 'Save Changes' : 'Create Account'}
          </button>
        </div>
      </div>
    </ModalOverlay>
  );
});
