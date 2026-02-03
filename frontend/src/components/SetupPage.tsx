/**
 * First-time setup page component.
 *
 * Displayed when no users exist in the system.
 * Allows creating the initial admin account.
 */
import React, { useState, FormEvent } from 'react';
import { completeSetup } from '../services/api';
import './SetupPage.css';

interface SetupPageProps {
  onSetupComplete?: () => void;
}

export function SetupPage({ onSetupComplete }: SetupPageProps) {
  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const validateForm = (): string | null => {
    if (!username.trim()) {
      return 'Username is required';
    }
    if (username.length < 3) {
      return 'Username must be at least 3 characters';
    }
    if (!email.trim()) {
      return 'Email is required';
    }
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      return 'Please enter a valid email address';
    }
    if (!password) {
      return 'Password is required';
    }
    if (password.length < 8) {
      return 'Password must be at least 8 characters';
    }
    if (!/[A-Z]/.test(password)) {
      return 'Password must contain at least one uppercase letter';
    }
    if (!/[a-z]/.test(password)) {
      return 'Password must contain at least one lowercase letter';
    }
    if (!/\d/.test(password)) {
      return 'Password must contain at least one number';
    }
    if (password.toLowerCase().includes(username.toLowerCase())) {
      return 'Password cannot contain your username';
    }
    if (password !== confirmPassword) {
      return 'Passwords do not match';
    }
    return null;
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);

    const validationError = validateForm();
    if (validationError) {
      setError(validationError);
      return;
    }

    setIsSubmitting(true);
    try {
      await completeSetup({ username, email, password });
      onSetupComplete?.();
    } catch (err) {
      if (err instanceof Error) {
        setError(err.message);
      } else {
        setError('Setup failed. Please try again.');
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="setup-page">
      <div className="setup-container">
        <div className="setup-header">
          <h1>Welcome to Enhanced Channel Manager</h1>
          <p>Create your administrator account to get started</p>
        </div>

        <form className="setup-form" onSubmit={handleSubmit}>
          {error && (
            <div className="setup-error" role="alert">
              {error}
            </div>
          )}

          <div className="setup-field">
            <label htmlFor="username">Username</label>
            <input
              type="text"
              id="username"
              name="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              disabled={isSubmitting}
              autoComplete="username"
              autoFocus
              placeholder="admin"
            />
            <span className="setup-hint">Choose a username for your admin account</span>
          </div>

          <div className="setup-field">
            <label htmlFor="email">Email</label>
            <input
              type="email"
              id="email"
              name="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              disabled={isSubmitting}
              autoComplete="email"
              placeholder="admin@example.com"
            />
            <span className="setup-hint">Used for password recovery</span>
          </div>

          <div className="setup-field">
            <label htmlFor="password">Password</label>
            <input
              type="password"
              id="password"
              name="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={isSubmitting}
              autoComplete="new-password"
            />
            <span className="setup-hint">
              At least 8 characters with uppercase, lowercase, and number
            </span>
          </div>

          <div className="setup-field">
            <label htmlFor="confirmPassword">Confirm Password</label>
            <input
              type="password"
              id="confirmPassword"
              name="confirmPassword"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              disabled={isSubmitting}
              autoComplete="new-password"
            />
          </div>

          <button
            type="submit"
            className="setup-submit"
            disabled={isSubmitting}
          >
            {isSubmitting ? 'Creating Account...' : 'Create Admin Account'}
          </button>
        </form>

        <div className="setup-footer">
          <p>This account will have full administrator privileges</p>
        </div>
      </div>
    </div>
  );
}

export default SetupPage;
