/**
 * Forgot Password page component.
 *
 * Allows users to request a password reset email.
 * Always shows success message to prevent email enumeration.
 */
import React, { useState, FormEvent } from 'react';
import * as api from '../services/api';
import './LoginPage.css';

// Simple navigation helper (no React Router)
const navigateTo = (path: string) => {
  window.history.pushState({}, '', path);
  window.dispatchEvent(new PopStateEvent('popstate'));
};

export function ForgotPasswordPage() {
  const [email, setEmail] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

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
