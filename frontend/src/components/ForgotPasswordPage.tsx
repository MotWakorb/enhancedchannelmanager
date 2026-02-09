/**
 * Forgot Password page component.
 *
 * When SMTP is configured: allows users to request a password reset email.
 * When SMTP is not configured: directs users to the CLI password reset docs.
 * Always shows success message to prevent email enumeration.
 */
import React, { useState, FormEvent } from 'react';
import * as api from '../services/api';
import { useAuth } from '../hooks/useAuth';
import './LoginPage.css';

// Simple navigation helper (no React Router)
const navigateTo = (path: string) => {
  window.history.pushState({}, '', path);
  window.dispatchEvent(new PopStateEvent('popstate'));
};

export function ForgotPasswordPage() {
  const { authStatus } = useAuth();
  const [email, setEmail] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const smtpConfigured = authStatus?.smtp_configured ?? false;

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);

    // Validate email
    if (!email.trim()) {
      setError('Email is required');
      return;
    }

    // Basic email validation
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!emailRegex.test(email)) {
      setError('Please enter a valid email address');
      return;
    }

    setIsSubmitting(true);
    try {
      await api.forgotPassword(email);
      setSuccess(true);
    } catch (err) {
      // Still show success to prevent email enumeration
      // but log the actual error for debugging
      console.error('Forgot password error:', err);
      setSuccess(true);
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

  if (success) {
    return (
      <div className="login-page">
        <div className="login-container">
          <div className="login-header">
            <h1>Check Your Email</h1>
            <p>Password reset instructions sent</p>
          </div>

          <div className="login-success">
            <span className="material-icons">mail</span>
            <p>
              If an account exists for <strong>{email}</strong>, you will receive
              an email with instructions to reset your password.
            </p>
            <p className="login-success-hint">
              The link will expire in 1 hour.
            </p>
          </div>

          <div className="login-links">
            <a href="/login" onClick={(e) => { e.preventDefault(); navigateTo('/login'); }} className="login-link">
              <span className="material-icons">arrow_back</span>
              Back to Sign In
            </a>
          </div>
        </div>
      </div>
    );
  }

  // When SMTP is not configured, show CLI instructions page
  if (!smtpConfigured) {
    return (
      <div className="login-page">
        <div className="login-container">
          <div className="login-header">
            <h1>Reset Your Password</h1>
            <p>Email is not configured on this server</p>
          </div>

          <div className="login-info">
            <span className="material-icons">lock_reset</span>
            <p>
              This ECM instance does not have email configured, so password reset
              emails cannot be sent.
            </p>
            <p>
              You can reset your password from the command line using the built-in
              reset utility. See the documentation for instructions:
            </p>
          </div>

          <a
            href="https://github.com/MotWakorb/enhancedchannelmanager#password-reset-script"
            target="_blank"
            rel="noopener noreferrer"
            className="login-submit"
            style={{ textAlign: 'center', textDecoration: 'none', display: 'block' }}
          >
            View Password Reset Docs
          </a>

          <div className="login-links">
            <a href="/login" onClick={(e) => { e.preventDefault(); navigateTo('/login'); }} className="login-link">
              <span className="material-icons">arrow_back</span>
              Back to Sign In
            </a>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="login-page">
      <div className="login-container">
        <div className="login-header">
          <h1>Forgot Password</h1>
          <p>Enter your email to receive reset instructions</p>
        </div>

        <form className="login-form" onSubmit={handleSubmit}>
          {error && (
            <div className="login-error" role="alert">
              {error}
            </div>
          )}

          <div className="login-field">
            <label htmlFor="email">Email Address</label>
            <input
              type="email"
              id="email"
              name="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              disabled={isSubmitting}
              autoComplete="email"
              autoFocus
              placeholder="you@example.com"
              onKeyDown={handleKeyDown}
            />
          </div>

          <button
            type="submit"
            className="login-submit"
            disabled={isSubmitting}
          >
            {isSubmitting ? 'Sending...' : 'Send Reset Link'}
          </button>
        </form>

        <div className="login-links">
          <a href="/login" onClick={(e) => { e.preventDefault(); navigateTo('/login'); }} className="login-link">
            <span className="material-icons">arrow_back</span>
            Back to Sign In
          </a>
        </div>
      </div>
    </div>
  );
}

export default ForgotPasswordPage;
