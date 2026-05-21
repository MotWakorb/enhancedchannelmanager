/**
 * OAuth consent screen (bead buiqr.7).
 *
 * The full-page card the ECM admin sees when an OAuth client (Claude Desktop)
 * requests access. The backend Authorization Server (GET /api/oauth/authorize)
 * validates the request and redirects the browser here (CONSENT_ROUTE =
 * /oauth/consent) carrying the validated params + the CSRF `state`.
 *
 * Security posture:
 *  - The client NAME is fetched server-side from the hardcoded registry
 *    (GET /api/oauth/authorize/consent-context) — it is NEVER rendered from the
 *    client_id query parameter, which is attacker-influenceable (threat model
 *    CP1, consent-phishing).
 *  - Anti-framing (X-Frame-Options: DENY + CSP frame-ancestors 'none') is set on
 *    the /oauth/consent response by the backend (CP1, clickjacking).
 *  - Approve submits a real form POST to /api/oauth/authorize/approve carrying
 *    the `state` so the server-side CSRF binding (buiqr.4) is satisfied; the
 *    server mints the code and 302-redirects to the client's registered
 *    redirect_uri. A native form submit (not fetch) is required so the browser
 *    follows the redirect to the client's (possibly custom-scheme) URI.
 *  - Cancel mints no token; it navigates back to the open-redirect-guarded
 *    return_to (OR1) the server resolved.
 */
import { useState, useEffect, useMemo } from 'react';
import * as api from '../services/api';
import type { ConsentContext } from '../services/api';
import { useAuth } from '../hooks/useAuth';
import { MCP_TOOL_CATEGORIES } from './settings/mcpToolCategories';
import './OAuthConsentPage.css';

// The PO-locked one-sentence permission summary (ADR-009 §3, security-reviewed).
// Single 'mcp' scope; names exactly what is granted. DO NOT reword without a
// UX + security-engineer sign-off (bead buiqr.7 AC2).
const PERMISSION_SUMMARY =
  'will be able to read and manage your ECM channels, streams, M3U accounts, and EPG sources.';

/** The approve endpoint the consent form POSTs to (server mints the code). */
const APPROVE_ACTION = '/api/oauth/authorize/approve';

export function OAuthConsentPage() {
  const { user, isLoading: authLoading } = useAuth();

  // Validated authorization params handed over by the backend /authorize
  // redirect. The consent page round-trips these (especially `state`) on the
  // approve POST so the server-side CSRF binding is satisfied (buiqr.4).
  const params = useMemo(() => {
    const sp = new URLSearchParams(window.location.search);
    return {
      client_id: sp.get('client_id') ?? '',
      redirect_uri: sp.get('redirect_uri') ?? '',
      code_challenge: sp.get('code_challenge') ?? '',
      code_challenge_method: sp.get('code_challenge_method') ?? '',
      scope: sp.get('scope') ?? 'mcp',
      state: sp.get('state') ?? '',
    };
  }, []);

  const [context, setContext] = useState<ConsentContext | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showAccessList, setShowAccessList] = useState(false);
  const [reauthorizing, setReauthorizing] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      if (!params.client_id) {
        setError('Missing authorization request. Start the connection from the app you want to authorize.');
        setLoading(false);
        return;
      }
      try {
        const ctx = await api.getConsentContext(params.client_id);
        if (!cancelled) setContext(ctx);
      } catch {
        if (!cancelled) {
          setError('This authorization request is not valid. The requesting app is not recognized by ECM.');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [params.client_id]);

  // Cancel: no token is minted. Return to the server-resolved (open-redirect-
  // guarded) target, defaulting to the app home.
  const handleCancel = () => {
    const target = context?.return_to ?? '/';
    window.location.assign(target);
  };

  // "Keep existing connection" on the returning-user state — same as cancel
  // (no new token; the existing grant stays live).
  const handleKeepExisting = handleCancel;

  // "Re-authorize": revoke the old grant, then proceed to mint a new one. We
  // revoke first (DELETE /grants/{id}) so we never accumulate token families,
  // then submit the approve form for the fresh grant.
  const handleReauthorize = async () => {
    if (!context?.existing_grant) return;
    setReauthorizing(true);
    try {
      await api.revokeOAuthGrant(context.existing_grant.id);
    } catch {
      setError('Could not revoke the existing connection. Please try again.');
      setReauthorizing(false);
      return;
    }
    submitApprove();
  };

  // Submit the hidden approve form natively so the browser follows the server's
  // 302 to the client's registered redirect_uri (which may be a custom scheme).
  const submitApprove = () => {
    const form = document.getElementById('oauth-approve-form') as HTMLFormElement | null;
    if (form) form.requestSubmit();
  };

  // ── render states ─────────────────────────────────────────────────────────

  // Admin-auth gate: the consent screen is admin-only (threat model SP3). The
  // backend /authorize already enforced the admin session before redirecting
  // here; this is the client-side belt to that suspenders.
  if (authLoading || loading) {
    return (
      <div className="oauth-consent-page">
        <div className="oauth-consent-card" data-testid="oauth-consent-loading">
          <span className="material-icons spinning">sync</span>
          <p>Loading authorization request…</p>
        </div>
      </div>
    );
  }

  if (user && !user.is_admin) {
    return (
      <div className="oauth-consent-page">
        <div className="oauth-consent-card" role="alert" data-testid="oauth-consent-denied">
          <h1>Admin access required</h1>
          <p>Only an ECM administrator can authorize app connections.</p>
        </div>
      </div>
    );
  }

  if (error || !context) {
    return (
      <div className="oauth-consent-page">
        <div className="oauth-consent-card" role="alert" data-testid="oauth-consent-error">
          <span className="material-icons oauth-consent-error-icon">error</span>
          <h1>Authorization request not valid</h1>
          <p>{error ?? 'This authorization request could not be processed.'}</p>
          <button type="button" className="btn" onClick={() => window.location.assign('/')}>
            Back to ECM
          </button>
        </div>
      </div>
    );
  }

  // The hidden form carries the validated params + state to the approve POST.
  const approveForm = (
    <form id="oauth-approve-form" method="POST" action={APPROVE_ACTION} hidden>
      <input type="hidden" name="client_id" value={params.client_id} />
      <input type="hidden" name="redirect_uri" value={params.redirect_uri} />
      <input type="hidden" name="code_challenge" value={params.code_challenge} />
      <input type="hidden" name="code_challenge_method" value={params.code_challenge_method} />
      <input type="hidden" name="scope" value={params.scope} />
      <input type="hidden" name="state" value={params.state} />
    </form>
  );

  // Returning-user state (bead buiqr.7 (c)) — an active grant already exists.
  if (context.already_connected) {
    return (
      <div className="oauth-consent-page">
        <div className="oauth-consent-card" data-testid="oauth-consent-already-connected">
          {approveForm}
          <div className="oauth-consent-header">
            <span className="material-icons oauth-consent-icon">link</span>
            <h1>Already connected</h1>
          </div>
          <p className="oauth-consent-summary">
            <strong>{context.client_name}</strong> already has access to this ECM deployment.
          </p>
          <div className="oauth-consent-actions">
            <button
              type="button"
              className="btn btn-primary"
              onClick={handleKeepExisting}
              data-testid="oauth-keep-existing"
            >
              Keep existing connection
            </button>
            <button
              type="button"
              className="btn btn-danger"
              onClick={handleReauthorize}
              disabled={reauthorizing}
              data-testid="oauth-reauthorize"
            >
              {reauthorizing ? 'Re-authorizing…' : 'Re-authorize'}
            </button>
          </div>
          <p className="oauth-consent-fineprint">
            Re-authorizing revokes the current connection and issues a new one.
          </p>
        </div>
      </div>
    );
  }

  // First-time consent state.
  return (
    <div className="oauth-consent-page">
      <div className="oauth-consent-card" data-testid="oauth-consent-card">
        {approveForm}
        <div className="oauth-consent-header">
          <span className="material-icons oauth-consent-icon">verified_user</span>
          <h1>Authorize {context.client_name}</h1>
        </div>

        <p className="oauth-consent-summary" data-testid="oauth-consent-summary">
          <strong>{context.client_name}</strong> {PERMISSION_SUMMARY}
        </p>

        <button
          type="button"
          className="oauth-consent-disclosure"
          onClick={() => setShowAccessList((v) => !v)}
          aria-expanded={showAccessList}
          aria-controls="oauth-consent-access-list"
          data-testid="oauth-consent-disclosure"
        >
          <span className="material-icons">{showAccessList ? 'expand_less' : 'expand_more'}</span>
          See full access list
        </button>

        {showAccessList && (
          <div
            id="oauth-consent-access-list"
            className="oauth-consent-tools"
            data-testid="oauth-consent-access-list"
          >
            {MCP_TOOL_CATEGORIES.map((t) => (
              <div key={t.category} className="oauth-consent-tool">
                <span className="material-icons" aria-hidden="true">{t.icon}</span>
                <div className="oauth-consent-tool-text">
                  <span className="oauth-consent-tool-name">{t.category}</span>
                  <span className="oauth-consent-tool-desc">{t.desc}</span>
                </div>
              </div>
            ))}
          </div>
        )}

        <div className="oauth-consent-actions">
          <button
            type="button"
            className="btn"
            onClick={handleCancel}
            data-testid="oauth-consent-cancel"
          >
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={submitApprove}
            data-testid="oauth-consent-approve"
          >
            Authorize
          </button>
        </div>
      </div>
    </div>
  );
}

export default OAuthConsentPage;
