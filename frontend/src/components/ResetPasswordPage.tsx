/**
 * Reset Password page component.
 *
 * Allows users to set a new password using a reset token from email.
 * Token is passed via URL query parameter.
 */
import React, { useState, FormEvent, useEffect, useMemo } from 'react';
import * as api from '../services/api';
import './LoginPage.css';

// Simple navigation helper (no React Router)
const navigateTo = (path: string) => {
  window.history.pushState({}, '', path);
  window.dispatchEvent(new PopStateEvent('popstate'));
};

export function ResetPasswordPage() {
  // Get token from URL query params
  const token = useMemo(() => {
    const params = new URLSearchParams(window.location.search);
    return params.get('token');
  }, []);

  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Check for token on mount
  useEffect(() => {
    if (!token) {
      setError('Invalid or missing reset token. Please request a new password reset link.');
    }
  }, [token]);

  const validatePassword = (password: string): string | null => {
    if (password.length < 8) {
      return 'Password must be at least 8 characters';
    }
    if (!/[A-Z]/.test(password)) {
      return 'Password must contain at least one uppercase letter';
    }
    if (!/[a-z]/.test(password)) {
      return 'Password must contain at least one lowercase letter';
    }
    if (!/[0-9]/.test(password)) {
      return 'Password must contain at least one number';
    }
    return null;
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);

    if (!token) {
      setError('Invalid or missing reset token');
      return;
    }

    // Validate passwords
    if (!newPassword) {
      setError('Password is required');
      return;
    }

    const passwordError = validatePassword(newPassword);
    if (passwordError) {
      setError(passwordError);
      return;
    }

    if (newPassword !== confirmPassword) {
      setError('Passwords do not match');
      return;
    }

    setIsSubmitting(true);
    try {
      await api.resetPassword(token, newPassword);
      setSuccess(true);
    } catch (err) {
      if (err instanceof Error) {
        // Handle specific error messages from the API
        if (err.message.includes('expired')) {
          setError('This password reset link has expired. Please request a new one.');
        } else if (err.message.includes('invalid')) {
          setError('Invalid reset token. Please request a new password reset link.');
        } else {
          setError(err.message);
        }
      } else {
        setError('Failed to reset password. Please try again.');
      }
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
            <h1>Password Reset</h1>
            <p>Your password has been changed</p>
          </div>

          <div className="login-success">
            <span className="material-icons">check_circle</span>
            <p>
              Your password has been successfully reset. You can now sign in with your new password.
            </p>
          </div>

          <button
            type="button"
            className="login-submit"
            onClick={() => navigateTo('/login')}
          >
            Sign In
          </button>
        </div>
      </div>
    );
  }

  // Show error state if no token
  if (!token) {
    return (
      <div className="login-page">
        <div className="login-container">
          <div className="login-header">
            <h1>Invalid Link</h1>
            <p>This password reset link is not valid</p>
          </div>

          <div className="login-error" role="alert">
            {error || 'Invalid or missing reset token. Please request a new password reset link.'}
          </div>

          <div className="login-links">
            <a href="/forgot-password" onClick={(e) => { e.preventDefault(); navigateTo('/forgot-password'); }} className="login-link">
              <span className="material-icons">mail</span>
              Request New Reset Link
            </a>
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
          <h1>Reset Password</h1>
          <p>Enter your new password</p>
        </div>

        <form className="login-form" onSubmit={handleSubmit}>
          {error && (
            <div className="login-error" role="alert">
              {error}
            </div>
          )}

          <div className="login-field">
            <label htmlFor="new-password">New Password</label>
            <input
              type="password"
              id="new-password"
              name="new-password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              disabled={isSubmitting}
              autoComplete="new-password"
              autoFocus
              onKeyDown={handleKeyDown}
            />
            <p className="login-field-hint">
              At least 8 characters with uppercase, lowercase, and number
            </p>
          </div>

          <div className="login-field">
            <label htmlFor="confirm-password">Confirm Password</label>
            <input
              type="password"
              id="confirm-password"
              name="confirm-password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              disabled={isSubmitting}
              autoComplete="new-password"
              onKeyDown={handleKeyDown}
            />
          </div>

          <button
            type="submit"
            className="login-submit"
            disabled={isSubmitting}
          >
            {isSubmitting ? 'Resetting...' : 'Reset Password'}
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

export default ResetPasswordPage;
