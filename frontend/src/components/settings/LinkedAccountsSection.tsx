/**
 * LinkedAccountsSection Component
 *
 * Allows users to view and manage their linked authentication identities.
 * Users can link multiple providers (Local, Dispatcharr, OIDC, SAML, LDAP)
 * to access the same account from different authentication sources.
 */
import { useState, useEffect, useCallback } from 'react';
import * as api from '../../services/api';
import type { UserIdentity, IdentityProvider, AuthStatus } from '../../types';
import { useNotifications } from '../../contexts/NotificationContext';
import { ModalOverlay } from '../ModalOverlay';
import './LinkedAccountsSection.css';
import '../ModalBase.css';

// Provider display configuration
const PROVIDER_CONFIG: Record<IdentityProvider, { icon: string; label: string; description: string }> = {
  local: {
    icon: 'account_circle',
    label: 'Local',
    description: 'Username and password authentication',
  },
  dispatcharr: {
    icon: 'cloud',
    label: 'Dispatcharr',
    description: 'Dispatcharr single sign-on',
  },
  oidc: {
    icon: 'verified_user',
    label: 'OpenID Connect',
    description: 'OpenID Connect provider',
  },
  saml: {
    icon: 'security',
    label: 'SAML',
    description: 'SAML identity provider',
  },
  ldap: {
    icon: 'business',
    label: 'LDAP',
    description: 'LDAP directory service',
  },
};

// Get all supported providers
const ALL_PROVIDERS: IdentityProvider[] = ['local', 'dispatcharr', 'oidc', 'saml', 'ldap'];

interface LinkModalProps {
  provider: IdentityProvider;
  onClose: () => void;
  onLink: (provider: IdentityProvider, username: string, password: string) => Promise<void>;
  loading: boolean;
}

function LinkModal({ provider, onClose, onLink, loading }: LinkModalProps) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username || !password) return;
    await onLink(provider, username, password);
  };

  const config = PROVIDER_CONFIG[provider];

  return (
    <ModalOverlay onClose={onClose}>
      <div className="modal-container modal-sm">
        <div className="modal-header">
          <h2 className="modal-title">
            <span className="material-icons">{config.icon}</span>
            Link {config.label} Account
          </h2>
          <button className="modal-close-btn" onClick={onClose}>
            <span className="material-icons">close</span>
          </button>
        </div>
        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            <p className="modal-description">
              Enter your {config.label} credentials to link this account.
            </p>
            <div className="modal-form-group">
              <label>Username</label>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder={`Enter your ${config.label} username`}
                autoFocus
                required
              />
            </div>
            <div className="modal-form-group">
              <label>Password</label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Enter your password"
                required
              />
            </div>
          </div>
          <div className="modal-footer">
            <button type="button" className="modal-btn modal-btn-secondary" onClick={onClose}>
              Cancel
            </button>
            <button
              type="submit"
              className="modal-btn modal-btn-primary"
              disabled={loading || !username || !password}
            >
              {loading ? (
                <>
                  <span className="material-icons spinning">sync</span>
                  Linking...
                </>
              ) : (
                <>
                  <span className="material-icons">link</span>
                  Link Account
                </>
              )}
            </button>
          </div>
        </form>
      </div>
    </ModalOverlay>
  );
}

export function LinkedAccountsSection() {
  const notifications = useNotifications();
  const [identities, setIdentities] = useState<UserIdentity[]>([]);
  const [authStatus, setAuthStatus] = useState<AuthStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [unlinking, setUnlinking] = useState<number | null>(null);
  const [linkingProvider, setLinkingProvider] = useState<IdentityProvider | null>(null);
  const [linkLoading, setLinkLoading] = useState(false);

  // Load identities on mount
  useEffect(() => {
    const loadData = async () => {
      try {
        setLoading(true);
        const [identitiesResponse, statusResponse] = await Promise.all([
          api.getLinkedIdentities(),
          api.getAuthStatus(),
        ]);
        setIdentities(identitiesResponse.identities);
        setAuthStatus(statusResponse);
      } catch (err) {
        notifications.error('Failed to load linked accounts', 'Linked Accounts');
        console.error('Failed to load linked accounts:', err);
      } finally {
        setLoading(false);
      }
    };

    loadData();
  }, []);

  // Get providers that are enabled but not yet linked
  const getAvailableProviders = useCallback((): IdentityProvider[] => {
    if (!authStatus) return [];

    const linkedProviders = new Set(identities.map((i) => i.provider));
    const enabledProviders = authStatus.enabled_providers || [];

    // Filter to only show enabled providers that aren't already linked
    return ALL_PROVIDERS.filter(
      (p) => enabledProviders.includes(p) && !linkedProviders.has(p)
    );
  }, [authStatus, identities]);

  // Unlink an identity
  const handleUnlink = useCallback(async (identityId: number) => {
    if (identities.length <= 1) {
      notifications.error('Cannot unlink your last identity - you would be locked out');
      return;
    }

    try {
      setUnlinking(identityId);
      await api.unlinkIdentity(identityId);
      setIdentities((prev) => prev.filter((i) => i.id !== identityId));
      notifications.success('Identity unlinked successfully');
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to unlink identity';
      notifications.error(message);
    } finally {
      setUnlinking(null);
    }
  }, [identities.length, notifications]);

  // Link a new identity
  const handleLink = useCallback(async (
    provider: IdentityProvider,
    username: string,
    password: string
  ) => {
    try {
      setLinkLoading(true);
      const response = await api.linkIdentity({ provider, username, password });
      setIdentities((prev) => [...prev, response.identity]);
      setLinkingProvider(null);
      notifications.success(`${PROVIDER_CONFIG[provider].label} account linked successfully`);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to link account';
      notifications.error(message);
    } finally {
      setLinkLoading(false);
    }
  }, [notifications]);

  // Format timestamp for display
  const formatDate = (dateStr: string | null): string => {
    if (!dateStr) return 'Never';
    const date = new Date(dateStr);
    return date.toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  const availableProviders = getAvailableProviders();

  if (loading) {
    return (
      <div className="linked-accounts-section">
        <div className="linked-accounts-loading">
          <span className="material-icons spinning">sync</span>
          Loading linked accounts...
        </div>
      </div>
    );
  }

  return (
    <div className="linked-accounts-section">
      <div className="linked-accounts-header">
        <div className="header-info">
          <h3>Linked Accounts</h3>
          <p className="header-description">
            Connect multiple authentication providers to your account.
            You can log in using any linked identity.
          </p>
        </div>
      </div>

      {/* Current Linked Identities */}
      {identities.length === 0 ? (
        <div className="linked-accounts-empty">
          <span className="material-icons">link_off</span>
          No linked accounts found
        </div>
      ) : (
        <div className="identity-list">
          {identities.map((identity) => {
            const config = PROVIDER_CONFIG[identity.provider] || {
              icon: 'account_circle',
              label: identity.provider,
              description: '',
            };
            const isOnlyIdentity = identities.length === 1;

            return (
              <div key={identity.id} className="identity-card">
                <div className="identity-info">
                  <div className="identity-icon">
                    <span className="material-icons">{config.icon}</span>
                  </div>
                  <div className="identity-details">
                    <span className="identity-provider">{config.label}</span>
                    <span className="identity-identifier">{identity.identifier}</span>
                  </div>
                </div>
                <div className="identity-meta">
                  <span title="Last used">
                    {identity.last_used_at ? `Last used ${formatDate(identity.last_used_at)}` : 'Never used'}
                  </span>
                </div>
                <div className="identity-actions">
                  {isOnlyIdentity ? (
                    <span className="only-identity-badge">Primary</span>
                  ) : (
                    <button
                      className="unlink-button"
                      onClick={() => handleUnlink(identity.id)}
                      disabled={unlinking === identity.id}
                      title="Unlink this identity"
                    >
                      {unlinking === identity.id ? (
                        <>
                          <span className="material-icons spinning">sync</span>
                          Unlinking...
                        </>
                      ) : (
                        <>
                          <span className="material-icons">link_off</span>
                          Unlink
                        </>
                      )}
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Link New Account */}
      <div className="link-account-section">
        <h4>Link Another Account</h4>
        {availableProviders.length === 0 ? (
          <p className="no-providers-message">
            All enabled authentication providers are already linked to your account.
          </p>
        ) : (
          <div className="available-providers">
            {availableProviders.map((provider) => {
              const config = PROVIDER_CONFIG[provider];
              // OIDC uses redirect flow, not modal
              if (provider === 'oidc') {
                return (
                  <button
                    key={provider}
                    className="link-provider-button"
                    onClick={() => {
                      // Redirect to OIDC linking endpoint
                      window.location.href = '/api/auth/identities/link/oidc/authorize';
                    }}
                  >
                    <span className="material-icons">{config.icon}</span>
                    Link {config.label}
                  </button>
                );
              }
              return (
                <button
                  key={provider}
                  className="link-provider-button"
                  onClick={() => setLinkingProvider(provider)}
                >
                  <span className="material-icons">{config.icon}</span>
                  Link {config.label}
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* Link Modal */}
      {linkingProvider && (
        <LinkModal
          provider={linkingProvider}
          onClose={() => setLinkingProvider(null)}
          onLink={handleLink}
          loading={linkLoading}
        />
      )}
    </div>
  );
}
