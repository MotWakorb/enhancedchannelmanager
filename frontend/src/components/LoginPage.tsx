/**
 * Login page component.
 *
 * Displays login options based on enabled auth providers.
 * Supports local authentication and Dispatcharr SSO.
 */
import React, { useState, FormEvent, useEffect } from 'react';
import { useAuth } from '../hooks/useAuth';
import './LoginPage.css';

// Simple navigation helper (no React Router)
const navigateTo = (path: string) => {
  window.history.pushState({}, '', path);
  window.dispatchEvent(new PopStateEvent('popstate'));
};

interface LoginPageProps {
  onLoginSuccess?: () => void;
}

type AuthProvider = 'local' | 'dispatcharr';


export function LoginPage({ onLoginSuccess }: LoginPageProps) {
  const { login, loginWithDispatcharr, authStatus, isLoading: authLoading } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [selectedProvider, setSelectedProvider] = useState<AuthProvider>('local');

  // Check which providers are enabled
  const enabledProviders = authStatus?.enabled_providers || ['local'];
  const hasLocal = enabledProviders.includes('local');
  const hasDispatcharr = enabledProviders.includes('dispatcharr');

  // Check for auth errors in URL params (returned from SSO callback)
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const urlError = params.get('error');
    const urlMessage = params.get('message');
    if (urlError && urlMessage) {
      setError(decodeURIComponent(urlMessage));
      // Clean up URL
      window.history.replaceState({}, '', window.location.pathname);
    }
  }, []);

  const hasMultipleProviders = hasLocal && hasDispatcharr;

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);

    // Validate inputs
    if (!username.trim()) {
      setError('Username is required');
      return;
    }
    if (!password) {
      setError('Password is required');
      return;
    }

    setIsSubmitting(true);
    try {
      if (selectedProvider === 'dispatcharr') {
        await loginWithDispatcharr(username, password);
      } else {
        await login(username, password);
      }
      onLoginSuccess?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed');
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      const form = e.currentTarget.closest('form');
      if (form) form.requestSubmit();
    }
  };

  const isLoading = authLoading || isSubmitting;

  // If only Dispatcharr is enabled, use it by default
  const effectiveProvider = hasLocal ? selectedProvider : 'dispatcharr';

  return (
    <div className="login-page">
      <div className="login-container">
        <div className="login-header">
          <h1>Enhanced Channel Manager</h1>
          <p>Sign in to continue</p>
        </div>

        {/* Provider selection tabs when multiple providers are available */}
        {hasMultipleProviders && (
          <div className="login-providers">
            <button
              type="button"
              className={`login-provider-tab ${selectedProvider === 'local' ? 'active' : ''}`}
              onClick={() => {
                setSelectedProvider('local');
                setError(null);
              }}
              disabled={isLoading}
            >
              <span className="material-icons">person</span>
              Local Account
            </button>
            <button
              type="button"
              className={`login-provider-tab ${selectedProvider === 'dispatcharr' ? 'active' : ''}`}
              onClick={() => {
                setSelectedProvider('dispatcharr');
                setError(null);
              }}
              disabled={isLoading}
            >
              <span className="material-icons">cloud</span>
              Dispatcharr
            </button>
          </div>
        )}

        <form className="login-form" onSubmit={handleSubmit}>
          {error && (
            <div className="login-error" role="alert">
              {error}
            </div>
          )}

          {/* Provider hint for Dispatcharr */}
          {effectiveProvider === 'dispatcharr' && (
            <div className="login-provider-hint">
              <span className="material-icons">info</span>
              Sign in with your Dispatcharr credentials
            </div>
          )}

          <div className="login-field">
            <label htmlFor="username">Username</label>
            <input
              type="text"
              id="username"
              name="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              disabled={isLoading}
              autoComplete="username"
              autoFocus
              onKeyDown={handleKeyDown}
            />
          </div>

          <div className="login-field">
            <label htmlFor="password">Password</label>
            <input
              type="password"
              id="password"
              name="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={isLoading}
              autoComplete="current-password"
              onKeyDown={handleKeyDown}
            />
            {effectiveProvider === 'local' && authStatus?.smtp_configured && (
              <a
                href="/forgot-password"
                className="login-forgot-link"
                onClick={(e) => {
                  e.preventDefault();
                  navigateTo('/forgot-password');
                }}
              >
                Forgot password?
              </a>
            )}
          </div>

          <button
            type="submit"
            className="login-submit"
            disabled={isLoading}
          >
            {isLoading ? 'Signing in...' : (
              effectiveProvider === 'dispatcharr'
                ? 'Sign in with Dispatcharr'
                : 'Sign In'
            )}
          </button>
        </form>

        {/* Alternative: Show Dispatcharr as a separate button below the form */}
        {hasDispatcharr && !hasMultipleProviders && (
          <div className="login-alt-providers">
            <div className="login-divider">
              <span>Dispatcharr authentication enabled</span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default LoginPage;
