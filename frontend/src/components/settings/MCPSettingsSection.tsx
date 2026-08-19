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
import { copyToClipboard } from '../../utils/clipboard';
import { MCP_TOOL_CATEGORIES } from './mcpToolCategories';
import {
  SettingsSectionHeader,
  SettingsSectionPlaceholders,
  type SettingsSectionMeta,
} from './SettingsSectionHeader';
import './MCPSettingsSection.css';

/**
 * The sections this page always has, in render order. Single authority for
 * both the loading placeholders and the loaded cards, so the Settings section
 * rail is complete from first paint and its anchor ids never move
 * (see SettingsSectionHeader.tsx; bead enhancedchannelmanager-b32co).
 *
 * "Connection" and "Available Tools" are absent on purpose: they are gated on
 * a key being configured, which is data rather than a loading window.
 */
const SECTIONS = {
  serverStatus: { icon: 'dns', label: 'Server Status' },
  apiKey: { icon: 'vpn_key', label: 'API Key' },
} as const satisfies Record<string, SettingsSectionMeta>;

const ALWAYS_PRESENT: readonly SettingsSectionMeta[] = [SECTIONS.serverStatus, SECTIONS.apiKey];

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
    // Self-diagnosing /health diagnostic (bd-ix1g6) — when reachable=true
    // but api_key_configured=false, api_key_status tells the operator WHY
    // (file_not_found / invalid_key / field_empty), and setup_hint carries a
    // remediation matching the cause. The two settings.json-era values are
    // still accepted from pre-…-04c0u.8 sidecar images.
    api_key_configured?: boolean;
    api_key_status?: 'ok' | 'file_not_found' | 'invalid_key' | 'field_empty' | 'invalid_json' | 'field_missing';
    setup_hint?: string;
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

  const handleCopy = async (text: string, label: string) => {
    const ok = await copyToClipboard(text, label);
    if (ok) {
      notifications.success('Copied to clipboard');
    } else {
      notifications.error('Failed to copy to clipboard');
    }
  };

  const mcpPort = '6101';
  const mcpEndpoint = `http://localhost:${mcpPort}/mcp`;
  const claudeDesktopConfig = JSON.stringify({
    mcpServers: {
      ecm: {
        command: 'npx',
        args: ['mcp-remote', mcpEndpoint, '--header', 'Authorization:${ECM_MCP_AUTH}', '--allow-http']
      }
    }
  }, null, 2);
  const claudeCodeConfig = JSON.stringify({
    mcpServers: {
      ecm: {
        type: 'http',
        url: mcpEndpoint,
        headers: { Authorization: 'Bearer ${ECM_MCP_API_KEY}' }
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

  // The placeholders are what keep this page's two rail entries — and the
  // anchors a shared `?section=` link names — present while the fetch is in
  // flight. Without them the rail appears from nothing when it settles, and a
  // deep link scrolls the reader away from wherever they were reading. The
  // `!isAdmin` branch above deliberately has none: that page really has no
  // sections, and it never resolves into one that does.
  if (loading) {
    return (
      <div className="mcp-settings-section">
        <div className="loading-state">
          <span className="material-icons spinning">sync</span>
          Loading MCP settings...
        </div>
        <SettingsSectionPlaceholders sections={ALWAYS_PRESENT} />
      </div>
    );
  }

  return (
    <div className="mcp-settings-section">
      {/* Server Status */}
      <div className="settings-section">
        <SettingsSectionHeader section={SECTIONS.serverStatus} />
        <div className="mcp-status-row">
          {mcpStatus === null ? (
            <div className="mcp-status-badge mcp-status-checking">
              <span className="material-icons spinning">sync</span>
              <span>Checking MCP server...</span>
            </div>
          ) : mcpStatus.reachable && mcpStatus.api_key_configured ? (
            <div className="mcp-status-badge mcp-status-online">
              <span className="material-icons">check_circle</span>
              <span>MCP server online — {mcpStatus.tools_available ?? '?'} tools available</span>
            </div>
          ) : mcpStatus.reachable ? (
            // Reachable but unconfigured — surface the diagnostic so the
            // operator can tell apart deployment misconfiguration (volume
            // mount, corrupted file) from a not-yet-generated key.
            // bd-ix1g6.
            <div className="mcp-status-badge mcp-status-warning" data-testid="mcp-status-unconfigured">
              <span className="material-icons">warning</span>
              <span>
                MCP server online but API key not configured
                {mcpStatus.api_key_status && mcpStatus.api_key_status !== 'ok' && (
                  <> — <code data-testid="mcp-api-key-status">{mcpStatus.api_key_status}</code></>
                )}
              </span>
            </div>
          ) : (
            <div className="mcp-status-badge mcp-status-offline">
              <span className="material-icons">cancel</span>
              <span>MCP server not reachable</span>
            </div>
          )}
          <button className="btn btn-sm" onClick={checkMcpStatus} title="Refresh status" aria-label="Refresh status">
            <span className="material-icons" aria-hidden="true">refresh</span>
          </button>
        </div>
        {mcpStatus?.reachable && !mcpStatus.api_key_configured && mcpStatus.setup_hint && (
          <p
            className="mcp-status-hint"
            data-testid="mcp-status-hint"
            style={{ marginTop: '0.5rem', fontSize: '0.875rem', color: 'var(--text-secondary, #888)' }}
          >
            {mcpStatus.setup_hint}
          </p>
        )}
      </div>

      {/* API Key Management */}
      <div className="settings-section">
        <SettingsSectionHeader section={SECTIONS.apiKey} />

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
                    onClick={() => handleCopy(apiKey, 'API key')}
                    title="Copy API key"
                    aria-label="Copy API key"
                  >
                    <span className="material-icons" aria-hidden="true">content_copy</span>
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
              The MCP server is published on host loopback by default at port <strong>{mcpPort}</strong>. Authentication uses an <code>Authorization: Bearer</code> header; credentials in URLs are rejected so they cannot leak through histories or access logs.
            </p>

            {/* mcp-remote bridge (Node) — Claude Desktop static-key path */}
            <label className="form-label" style={{ marginTop: '1.25rem' }}>Claude Desktop — mcp-remote bridge (Node required, private-network OK)</label>
            <p className="form-description" style={{ color: 'var(--accent-green, #4caf50)' }}>
              ✅ Runs entirely on your machine — works on a LAN/VPN-only ECM with no public exposure.
            </p>
            <p className="form-description">
              If you have Node.js installed on the same machine as Claude Desktop (LTS 18+ — install from{' '}
              <a href="https://nodejs.org/" target="_blank" rel="noopener noreferrer">nodejs.org</a>
              {', '}
              <code>winget install OpenJS.NodeJS.LTS</code>, <code>brew install node</code>, or <code>apt install nodejs npm</code>), add this to your <code>claude_desktop_config.json</code>.
              Set the operating-system environment variable <code>ECM_MCP_AUTH</code> to <code>Bearer &lt;your key&gt;</code> before launching Claude Desktop. The generated config intentionally contains no credential. Without Node on PATH, Claude Desktop&apos;s logs show <code>spawn npx ENOENT</code>.
            </p>
            <div className="mcp-config-block">
              <pre>{claudeDesktopConfig}</pre>
              <button
                className="mcp-copy-btn"
                onClick={() => handleCopy(claudeDesktopConfig, 'Claude Desktop config')}
                title="Copy config"
                aria-label="Copy config"
              >
                <span className="material-icons" aria-hidden="true">content_copy</span>
              </button>
            </div>
            <p className="form-description" style={{ marginTop: '0.5rem', fontSize: '0.8rem', color: 'var(--text-secondary, #888)' }}>
              (<code>--allow-http</code> is safe here only because the default endpoint is host loopback. Remote access must use the documented HTTPS profile.)
            </p>

            <label className="form-label" style={{ marginTop: '1rem' }}>MCP Endpoint (reference)</label>
            <div className="mcp-key-display">
              <code>http://localhost:{mcpPort}/mcp</code>
              <button
                className="mcp-copy-btn"
                onClick={() => handleCopy(mcpEndpoint, 'MCP endpoint URL')}
                title="Copy URL"
                aria-label="Copy URL"
              >
                <span className="material-icons" aria-hidden="true">content_copy</span>
              </button>
            </div>

            <label className="form-label" style={{ marginTop: '1rem' }}>Claude Code Config (.mcp.json) — private-network OK, no Node</label>
            <p className="form-description" style={{ color: 'var(--accent-green, #4caf50)' }}>
              ✅ Connects directly over HTTP from your machine — works on a LAN/VPN-only ECM with no public exposure and no Node.js.
            </p>
            <p className="form-description">
              Save this as <code>.mcp.json</code> in a project directory where you want ECM tools available.
            </p>
            <div className="mcp-config-block">
              <pre>{claudeCodeConfig}</pre>
              <button
                className="mcp-copy-btn"
                onClick={() => handleCopy(claudeCodeConfig, 'Claude Code config')}
                title="Copy config"
                aria-label="Copy config"
              >
                <span className="material-icons" aria-hidden="true">content_copy</span>
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
            {MCP_TOOL_CATEGORIES.map(t => (
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
