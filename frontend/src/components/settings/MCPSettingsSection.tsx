/**
 * MCPSettingsSection Component
 *
 * Admin panel for configuring MCP (Model Context Protocol) integration.
 * Allows generating/revoking API keys and shows connection instructions.
 */
import { logger } from '../../utils/logger';
import { useState, useEffect, useCallback } from 'react';
import * as api from '../../services/api';
import type { OAuthGrant } from '../../services/api';
import { useNotifications } from '../../contexts/NotificationContext';
import { copyToClipboard } from '../../utils/clipboard';
import { MCP_TOOL_CATEGORIES } from './mcpToolCategories';
import './MCPSettingsSection.css';

interface Props {
  isAdmin: boolean;
}

/** Format an epoch-seconds timestamp for the Active Grants list (locale date+time). */
function formatGrantTime(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toLocaleString();
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
    // (file_not_found / invalid_json / field_missing / field_empty), and
    // setup_hint carries a remediation matching the cause.
    api_key_configured?: boolean;
    api_key_status?: 'ok' | 'file_not_found' | 'invalid_json' | 'field_missing' | 'field_empty';
    setup_hint?: string;
    // bd-buiqr10 (Option-A slice): OAuth signing key diagnostic.
    // signing_key_status='ok' means the HS256 secret is present in settings.json
    // and offline JWT verification is possible. 'signing_key_missing' means
    // the secret is absent — OAuth Bearer-JWT auth cannot work until it is set.
    signing_key_status?: 'ok' | 'signing_key_missing' | 'file_not_found' | 'invalid_json';
    signing_key_hint?: string;
  } | null>(null);

  // Active Grants (bead buiqr.7 (d)) — OAuth authorizations the admin made to
  // Claude clients. Listed with inline-confirm revoke; the section is absent
  // entirely when there are no grants (no empty state).
  const [grants, setGrants] = useState<OAuthGrant[]>([]);
  const [confirmingGrantId, setConfirmingGrantId] = useState<string | null>(null);
  const [revokingGrantId, setRevokingGrantId] = useState<string | null>(null);

  // Bulk-revoke state machine (buiqr.12):
  // null → idle; 'confirm1' → first Are-you-sure prompt;
  // 'confirm2' → type-REVOKE gate; 'revoking' → in-flight.
  type BulkRevokeStage = null | 'confirm1' | 'confirm2' | 'revoking';
  const [bulkRevokeStage, setBulkRevokeStage] = useState<BulkRevokeStage>(null);
  const [bulkRevokeInput, setBulkRevokeInput] = useState('');

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

  const loadGrants = useCallback(async () => {
    try {
      const result = await api.getOAuthGrants();
      setGrants(result.grants);
    } catch (err) {
      // A missing/empty grants surface is non-fatal — leave the list empty so
      // the section simply doesn't render (no scary error for an absent feature).
      logger.error('Failed to load OAuth grants:', err);
      setGrants([]);
    }
  }, []);

  const handleRevokeGrant = async (grantId: string) => {
    setRevokingGrantId(grantId);
    try {
      await api.revokeOAuthGrant(grantId);
      setGrants((prev) => prev.filter((g) => g.id !== grantId));
      notifications.success('Connection revoked');
    } catch (err) {
      logger.error('Failed to revoke OAuth grant:', err);
      notifications.error('Failed to revoke connection');
    } finally {
      setRevokingGrantId(null);
      setConfirmingGrantId(null);
    }
  };

  const handleRevokeAll = async () => {
    // Guard: only proceed from the confirm2 stage with exact typed confirmation.
    if (bulkRevokeStage !== 'confirm2' || bulkRevokeInput !== 'REVOKE') return;
    setBulkRevokeStage('revoking');
    try {
      const result = await api.revokeAllOAuthGrants();
      setGrants([]);
      notifications.success(
        `All active connections revoked (${result.revoked} total)`
      );
    } catch (err) {
      logger.error('Failed to bulk-revoke OAuth grants:', err);
      notifications.error('Failed to revoke all connections');
    } finally {
      setBulkRevokeStage(null);
      setBulkRevokeInput('');
    }
  };

  const cancelBulkRevoke = () => {
    setBulkRevokeStage(null);
    setBulkRevokeInput('');
  };

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
    loadGrants();
  }, [loadSettings, checkMcpStatus, loadGrants]);

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
  const mcpEndpoint = `http://YOUR_ECM_HOST:${mcpPort}/mcp?api_key=YOUR_API_KEY`;
  const claudeDesktopConfig = JSON.stringify({
    mcpServers: {
      ecm: {
        command: 'npx',
        args: ['mcp-remote', mcpEndpoint, '--allow-http']
      }
    }
  }, null, 2);
  const claudeCodeConfig = JSON.stringify({
    mcpServers: {
      ecm: {
        type: 'http',
        url: mcpEndpoint
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
          <button className="btn btn-sm" onClick={checkMcpStatus} title="Refresh status">
            <span className="material-icons">refresh</span>
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
        {/* bd-buiqr10: OAuth signing key hint — shown when the HS256 secret is
            absent from settings.json. Rendered identically to the api_key setup_hint
            pattern (bd-ix1g6) so the operator sees a consistent diagnostic UI. */}
        {mcpStatus?.reachable && mcpStatus.signing_key_status && mcpStatus.signing_key_status !== 'ok' && mcpStatus.signing_key_hint && (
          <p
            className="mcp-status-hint"
            data-testid="mcp-signing-key-hint"
            style={{ marginTop: '0.5rem', fontSize: '0.875rem', color: 'var(--text-secondary, #888)' }}
          >
            {mcpStatus.signing_key_hint}
          </p>
        )}
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
                    onClick={() => handleCopy(apiKey, 'API key')}
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
              The MCP server runs on port <strong>{mcpPort}</strong> alongside ECM, using the Streamable HTTP transport on a single <code>/mcp</code> endpoint. Connect Claude Desktop or Claude Code using the endpoint below.
            </p>

            <label className="form-label">MCP Endpoint</label>
            <div className="mcp-key-display">
              <code>http://YOUR_ECM_HOST:{mcpPort}/mcp?api_key=YOUR_API_KEY</code>
              <button
                className="mcp-copy-btn"
                onClick={() => handleCopy(mcpEndpoint, 'MCP endpoint URL')}
                title="Copy URL"
              >
                <span className="material-icons">content_copy</span>
              </button>
            </div>

            <label className="form-label" style={{ marginTop: '1rem' }}>Claude Desktop Config</label>
            <p className="form-description">
              Add this to your Claude Desktop settings. Replace <code>YOUR_ECM_HOST</code> with your server's IP or hostname and <code>YOUR_API_KEY</code> with the key above. (Claude Desktop reaches remote MCP servers through the <code>mcp-remote</code> bridge; <code>--allow-http</code> is needed for plain-HTTP endpoints.)
            </p>
            <p className="form-description mcp-prereq-note">
              <span className="material-icons" aria-hidden="true">info</span>
              <span>
                <strong>Prerequisite:</strong> Claude Desktop does not bundle Node.js. The <code>mcp-remote</code> bridge below runs via <code>npx</code>, so Node.js (LTS, 18+) must be installed on the same machine as Claude Desktop. Install from <a href="https://nodejs.org/" target="_blank" rel="noopener noreferrer">nodejs.org</a> or via a package manager (<code>winget install OpenJS.NodeJS.LTS</code>, <code>brew install node</code>, <code>apt install nodejs npm</code>). Without Node on PATH, Claude Desktop's logs show <code>spawn npx ENOENT</code>.
                <br /><br />
                <strong>Why not Custom Connectors?</strong> Claude Desktop's Settings &gt; Connectors UI requires OAuth 2.1; ECM's MCP server uses a static API key, so the no-Node Custom Connector path is not supported yet. Claude Code (below) talks to the server directly and does not need Node.
              </span>
            </p>
            <div className="mcp-config-block">
              <pre>{claudeDesktopConfig}</pre>
              <button
                className="mcp-copy-btn"
                onClick={() => handleCopy(claudeDesktopConfig, 'Claude Desktop config')}
                title="Copy config"
              >
                <span className="material-icons">content_copy</span>
              </button>
            </div>

            <label className="form-label" style={{ marginTop: '1rem' }}>Claude Code Config (.mcp.json)</label>
            <p className="form-description">
              Save this as <code>.mcp.json</code> in a project directory where you want ECM tools available.
            </p>
            <div className="mcp-config-block">
              <pre>{claudeCodeConfig}</pre>
              <button
                className="mcp-copy-btn"
                onClick={() => handleCopy(claudeCodeConfig, 'Claude Code config')}
                title="Copy config"
              >
                <span className="material-icons">content_copy</span>
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Active Grants (bead buiqr.7 (d)) — OAuth connections (Claude Desktop,
          etc.). Absent entirely when there are no grants; no empty state. */}
      {grants.length > 0 && (
        <div className="settings-section" data-testid="oauth-grants-section">
          <div className="settings-section-header">
            <span className="material-icons">verified_user</span>
            <h3>Active Connections</h3>
          </div>
          <p className="form-description">
            Apps you have authorized to access ECM via OAuth. Revoking a
            connection immediately invalidates its access — the app must be
            re-authorized to connect again.
          </p>
          <ul className="mcp-grants-list">
            {grants.map((grant) => (
              <li
                key={grant.id}
                className="mcp-grant-row"
                data-testid="oauth-grant-row"
              >
                <div className="mcp-grant-info">
                  <span className="material-icons mcp-grant-icon" aria-hidden="true">
                    desktop_windows
                  </span>
                  <div className="mcp-grant-meta">
                    <span className="mcp-grant-name">{grant.client_name}</span>
                    <span className="mcp-grant-times">
                      Granted {formatGrantTime(grant.granted_at)} · Last used{' '}
                      {formatGrantTime(grant.last_used)}
                    </span>
                  </div>
                </div>
                {confirmingGrantId === grant.id ? (
                  <div className="mcp-grant-confirm" role="group" aria-label="Confirm revoke">
                    <span className="mcp-grant-confirm-label">Revoke?</span>
                    <button
                      className="btn btn-sm btn-danger"
                      onClick={() => handleRevokeGrant(grant.id)}
                      disabled={revokingGrantId === grant.id}
                    >
                      {revokingGrantId === grant.id ? 'Revoking...' : 'Yes, revoke'}
                    </button>
                    <button
                      className="btn btn-sm"
                      onClick={() => setConfirmingGrantId(null)}
                      disabled={revokingGrantId === grant.id}
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <button
                    className="btn btn-sm btn-danger"
                    onClick={() => setConfirmingGrantId(grant.id)}
                    data-testid="oauth-grant-revoke"
                  >
                    <span className="material-icons">block</span>
                    Revoke
                  </button>
                )}
              </li>
            ))}
          </ul>

          {/* Bulk-revoke panic button (buiqr.12 AC1-3) — double-confirmation
              because this is destructive: kills ALL active connections at once. */}
          <div className="mcp-bulk-revoke" data-testid="oauth-bulk-revoke-section">
            {bulkRevokeStage === null && (
              <button
                className="btn btn-sm btn-danger"
                onClick={() => setBulkRevokeStage('confirm1')}
                data-testid="oauth-bulk-revoke-btn"
              >
                <span className="material-icons">block</span>
                Revoke all active tokens
              </button>
            )}

            {/* Step 1: Are you sure? */}
            {bulkRevokeStage === 'confirm1' && (
              <div
                className="mcp-bulk-revoke-confirm"
                role="group"
                aria-label="Confirm revoke all"
                data-testid="oauth-bulk-revoke-confirm1"
              >
                <span className="mcp-grant-confirm-label">
                  This will disconnect ALL apps. Are you sure?
                </span>
                <button
                  className="btn btn-sm btn-danger"
                  onClick={() => setBulkRevokeStage('confirm2')}
                  data-testid="oauth-bulk-revoke-confirm1-yes"
                >
                  Yes, continue
                </button>
                <button
                  className="btn btn-sm"
                  onClick={cancelBulkRevoke}
                  data-testid="oauth-bulk-revoke-cancel"
                >
                  Cancel
                </button>
              </div>
            )}

            {/* Step 2: Type REVOKE to confirm */}
            {(bulkRevokeStage === 'confirm2' || bulkRevokeStage === 'revoking') && (
              <div
                className="mcp-bulk-revoke-confirm"
                role="group"
                aria-label="Type REVOKE to confirm"
                data-testid="oauth-bulk-revoke-confirm2"
              >
                <span className="mcp-grant-confirm-label">
                  Type <strong>REVOKE</strong> to confirm:
                </span>
                <input
                  type="text"
                  className="mcp-bulk-revoke-input"
                  value={bulkRevokeInput}
                  onChange={(e) => setBulkRevokeInput(e.target.value)}
                  placeholder="REVOKE"
                  disabled={bulkRevokeStage === 'revoking'}
                  data-testid="oauth-bulk-revoke-input"
                  autoFocus
                />
                <button
                  className="btn btn-sm btn-danger"
                  onClick={handleRevokeAll}
                  disabled={bulkRevokeInput !== 'REVOKE' || bulkRevokeStage === 'revoking'}
                  data-testid="oauth-bulk-revoke-confirm2-yes"
                >
                  {bulkRevokeStage === 'revoking' ? 'Revoking...' : 'Revoke all'}
                </button>
                <button
                  className="btn btn-sm"
                  onClick={cancelBulkRevoke}
                  disabled={bulkRevokeStage === 'revoking'}
                  data-testid="oauth-bulk-revoke-cancel"
                >
                  Cancel
                </button>
              </div>
            )}
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
