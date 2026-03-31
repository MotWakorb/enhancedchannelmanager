/**
 * MCPSettingsSection Component
 *
 * Admin panel for configuring MCP (Model Context Protocol) integration.
 * Allows generating/revoking API keys and shows connection instructions.
 */
import { logger } from '../../utils/logger';
import { useState, useEffect, useCallback } from 'react';
import * as api from '../../services/api';
import { useNotifications } from '../../contexts/NotificationContext';
import './MCPSettingsSection.css';

interface Props {
  isAdmin: boolean;
}

export function MCPSettingsSection({ isAdmin }: Props) {
  const notifications = useNotifications();
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [revoking, setRevoking] = useState(false);
  const [keyConfigured, setKeyConfigured] = useState(false);
  const [apiKey, setApiKey] = useState('');
  const [showKey, setShowKey] = useState(false);
  const [mcpStatus, setMcpStatus] = useState<{
    reachable: boolean;
    tools_available?: number;
  } | null>(null);

  const loadSettings = useCallback(async () => {
    try {
      const settings = await api.getSettings();
      setKeyConfigured(settings.mcp_api_key_configured);
    } catch (err) {
      logger.error('Failed to load MCP settings:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  const checkMcpStatus = useCallback(async () => {
    try {
      const status = await api.getMCPStatus();
      setMcpStatus(status);
    } catch {
      setMcpStatus({ reachable: false });
    }
  }, []);

  useEffect(() => {
    loadSettings();
    checkMcpStatus();
  }, [loadSettings, checkMcpStatus]);

  const handleGenerate = async () => {
    setGenerating(true);
    try {
      const result = await api.generateMCPApiKey();
      setApiKey(result.mcp_api_key);
      setKeyConfigured(true);
      setShowKey(true);
      notifications.success('MCP API key generated');
    } catch (err) {
      logger.error('Failed to generate MCP API key:', err);
      notifications.error('Failed to generate API key');
    } finally {
      setGenerating(false);
    }
  };

  const handleRevoke = async () => {
    setRevoking(true);
    try {
      await api.revokeMCPApiKey();
      setApiKey('');
      setKeyConfigured(false);
      setShowKey(false);
      notifications.success('MCP API key revoked');
    } catch (err) {
      logger.error('Failed to revoke MCP API key:', err);
      notifications.error('Failed to revoke API key');
    } finally {
      setRevoking(false);
    }
  };

  const handleCopy = (text: string) => {
    navigator.clipboard.writeText(text);
    notifications.success('Copied to clipboard');
  };

  const mcpPort = '6101';
  const claudeDesktopConfig = JSON.stringify({
    mcpServers: {
      ecm: {
        url: `http://YOUR_ECM_HOST:${mcpPort}/sse`,
        env: {}
      }
    }
  }, null, 2);

  if (!isAdmin) {
    return (
      <div className="mcp-settings-section">
        <div className="settings-page-header">
          <h2>MCP Integration</h2>
          <p>Admin access required to manage MCP settings.</p>
        </div>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="mcp-settings-section">
        <div className="loading-state">
          <span className="material-icons spinning">sync</span>
          Loading MCP settings...
        </div>
      </div>
    );
  }

  return (
    <div className="mcp-settings-section">
      <div className="settings-page-header">
        <h2>MCP Integration</h2>
        <p>Connect Claude to ECM via the Model Context Protocol. Claude can list channels, manage streams, refresh M3U accounts, probe stream health, and more — all through natural language.</p>
      </div>

      {/* Server Status */}
      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">dns</span>
          <h3>Server Status</h3>
        </div>
        <div className="mcp-status-row">
          {mcpStatus === null ? (
            <div className="mcp-status-badge mcp-status-checking">
              <span className="material-icons spinning">sync</span>
              <span>Checking MCP server...</span>
            </div>
          ) : mcpStatus.reachable ? (
            <div className="mcp-status-badge mcp-status-online">
              <span className="material-icons">check_circle</span>
              <span>MCP server online — {mcpStatus.tools_available ?? '?'} tools available</span>
            </div>
          ) : (
            <div className="mcp-status-badge mcp-status-offline">
              <span className="material-icons">cancel</span>
              <span>MCP server not reachable</span>
            </div>
          )}
          <button className="btn btn-sm" onClick={checkMcpStatus} title="Refresh status">
            <span className="material-icons">refresh</span>
          </button>
        </div>
      </div>

      {/* API Key Management */}
      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">vpn_key</span>
          <h3>API Key</h3>
        </div>

        <div className="form-group-vertical">
          {keyConfigured ? (
            <div className="mcp-key-status">
              <div className="mcp-key-badge mcp-key-active">
                <span className="material-icons">check_circle</span>
                <span>API key is configured</span>
              </div>

              {apiKey && showKey && (
                <div className="mcp-key-display">
                  <code>{apiKey}</code>
                  <button
                    className="mcp-copy-btn"
                    onClick={() => handleCopy(apiKey)}
                    title="Copy API key"
                  >
                    <span className="material-icons">content_copy</span>
                  </button>
                </div>
              )}

              <div className="mcp-key-actions">
                <button
                  className="btn btn-primary"
                  onClick={handleGenerate}
                  disabled={generating}
                >
                  <span className="material-icons">{generating ? 'sync' : 'refresh'}</span>
                  {generating ? 'Generating...' : 'Regenerate Key'}
                </button>
                <button
                  className="btn btn-danger"
                  onClick={handleRevoke}
                  disabled={revoking}
                >
                  <span className="material-icons">{revoking ? 'sync' : 'block'}</span>
                  {revoking ? 'Revoking...' : 'Revoke Key'}
                </button>
              </div>
            </div>
          ) : (
            <div className="mcp-key-status">
              <div className="mcp-key-badge mcp-key-inactive">
                <span className="material-icons">info</span>
                <span>No API key configured. Generate one to enable MCP access.</span>
              </div>
              <div className="mcp-key-actions">
                <button
                  className="btn btn-primary"
                  onClick={handleGenerate}
                  disabled={generating}
                >
                  <span className="material-icons">{generating ? 'sync' : 'vpn_key'}</span>
                  {generating ? 'Generating...' : 'Generate API Key'}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Connection Instructions */}
      {keyConfigured && (
        <div className="settings-section">
          <div className="settings-section-header">
            <span className="material-icons">link</span>
            <h3>Connection</h3>
          </div>

          <div className="form-group-vertical">
            <p className="form-description">
              The MCP server runs on port <strong>{mcpPort}</strong> alongside ECM. Connect Claude Desktop or Claude Code using the SSE endpoint below.
            </p>

            <label className="form-label">SSE Endpoint</label>
            <div className="mcp-key-display">
              <code>http://YOUR_ECM_HOST:{mcpPort}/sse</code>
              <button
                className="mcp-copy-btn"
                onClick={() => handleCopy(`http://YOUR_ECM_HOST:${mcpPort}/sse`)}
                title="Copy URL"
              >
                <span className="material-icons">content_copy</span>
              </button>
            </div>

            <label className="form-label" style={{ marginTop: '1rem' }}>Claude Desktop Config</label>
            <p className="form-description">
              Add this to your Claude Desktop settings. Replace <code>YOUR_ECM_HOST</code> with your server's IP or hostname.
            </p>
            <div className="mcp-config-block">
              <pre>{claudeDesktopConfig}</pre>
              <button
                className="mcp-copy-btn"
                onClick={() => handleCopy(claudeDesktopConfig)}
                title="Copy config"
              >
                <span className="material-icons">content_copy</span>
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Available Tools */}
      {keyConfigured && (
        <div className="settings-section">
          <div className="settings-section-header">
            <span className="material-icons">build</span>
            <h3>Available Tools (80)</h3>
          </div>
          <div className="mcp-tools-grid">
            {[
              { category: 'Channels', count: 12, icon: 'tv', desc: 'CRUD, streams, merge, bulk numbering' },
              { category: 'Groups', count: 6, icon: 'folder', desc: 'CRUD, hidden, orphaned, auto-created' },
              { category: 'Streams', count: 11, icon: 'stream', desc: 'List, search, probe, health, struck-out' },
              { category: 'M3U', count: 8, icon: 'playlist_play', desc: 'Account CRUD, refresh, group settings' },
              { category: 'EPG', count: 7, icon: 'schedule', desc: 'Source CRUD, grid, refresh, auto-match' },
              { category: 'Auto-Create', count: 9, icon: 'auto_fix_high', desc: 'Rule CRUD, toggle, executions, rollback' },
              { category: 'Export', count: 6, icon: 'file_download', desc: 'Profiles, cloud targets, publish' },
              { category: 'Tasks', count: 7, icon: 'timer', desc: 'Run, cancel, history, schedules' },
              { category: 'Stats', count: 6, icon: 'analytics', desc: 'Top watched, bandwidth, popularity, viewers' },
              { category: 'System', count: 3, icon: 'settings', desc: 'Settings, backup, journal' },
              { category: 'Notifications', count: 3, icon: 'notifications', desc: 'List, mark read, clear' },
              { category: 'Profiles', count: 3, icon: 'tune', desc: 'Channel/stream profiles, bulk assign' },
              { category: 'Normalize', count: 2, icon: 'text_format', desc: 'Test normalization, list rules' },
            ].map(t => (
              <div key={t.category} className="mcp-tool-card">
                <div className="mcp-tool-card-header">
                  <span className="material-icons">{t.icon}</span>
                  <span className="mcp-tool-card-title">{t.category}</span>
                  <span className="mcp-tool-card-count">{t.count}</span>
                </div>
                <p>{t.desc}</p>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
