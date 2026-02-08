/**
 * Protected route wrapper component.
 *
 * Wraps content that requires authentication.
 * Shows setup page if first-time setup needed, login page if not authenticated.
 * Also handles public password reset pages.
 */
import React, { ReactNode, useState, useEffect, useCallback } from 'react';
import { useAuth, useAuthRequired } from '../hooks/useAuth';
import { LoginPage } from './LoginPage';
import { SetupPage } from './SetupPage';
import { ForgotPasswordPage } from './ForgotPasswordPage';
import { ResetPasswordPage } from './ResetPasswordPage';
import { checkSetupRequired } from '../services/api';
import './ProtectedRoute.css';

interface ProtectedRouteProps {
  children: ReactNode;
  // If true, also requires admin role
  requireAdmin?: boolean;
}

/**
 * ProtectedRoute component.
 *
 * Wraps content that requires authentication:
 * - Shows loading spinner during initial auth check
 * - Shows setup page if first-time setup is required
 * - Shows login page if not authenticated
 * - Shows access denied if requireAdmin and user is not admin
 * - Shows children if authenticated (and admin if required)
 */
export function ProtectedRoute({ children, requireAdmin = false }: ProtectedRouteProps) {
  const { user, isLoading, isAuthenticated, refreshUser } = useAuth();
  const authRequired = useAuthRequired();
  const [setupRequired, setSetupRequired] = useState<boolean | null>(null);
  const [checkingSetup, setCheckingSetup] = useState(true);
  const [currentPath, setCurrentPath] = useState(window.location.pathname);

  // Listen for URL changes (for navigation within password reset flow)
  useEffect(() => {
    const handlePopState = () => {
      setCurrentPath(window.location.pathname);
    };
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  // Check if setup is required on mount
  useEffect(() => {
    const checkSetup = async () => {
      try {
        const result = await checkSetupRequired();
        setSetupRequired(result.required);
      } catch {
        // If we can't check, assume setup is not required
        setSetupRequired(false);
      } finally {
        setCheckingSetup(false);
      }
    };

    checkSetup();
  }, []);

  // Handle setup completion
  const handleSetupComplete = useCallback(async () => {
    setSetupRequired(false);
    // Refresh user after setup - they should now be logged in
    await refreshUser();
  }, [refreshUser]);

  // Show loading spinner during initial checks
  if (isLoading || checkingSetup) {
    return (
      <div className="protected-route-loading">
        <span className="material-icons spinning">sync</span>
        <p>Loading...</p>
      </div>
    );
  }

  // Show setup page if first-time setup is required
  if (setupRequired) {
    return <SetupPage onSetupComplete={handleSetupComplete} />;
  }

  // Handle public password reset pages (accessible without auth)
  if (currentPath === '/forgot-password') {
    return <ForgotPasswordPage />;
  }
  if (currentPath === '/reset-password') {
    return <ResetPasswordPage />;
  }

  // If auth is not required, show children directly
  if (!authRequired) {
    // Clean up auth-related paths when auth is not required
    if (currentPath === '/login' || currentPath === '/forgot-password' || currentPath === '/reset-password') {
      window.history.replaceState({}, '', '/');
    }
    return <>{children}</>;
  }

  // Show login page if not authenticated
  if (!isAuthenticated) {
    return <LoginPage />;
  }

  // If authenticated but on a login/auth page, redirect to home
  if (currentPath === '/login' || currentPath === '/forgot-password') {
    window.history.replaceState({}, '', '/');
  }

  // Check admin requirement
  if (requireAdmin && !user?.is_admin) {
    return (
      <div className="protected-route-denied">
        <h2>Access Denied</h2>
        <p>You do not have permission to view this page.</p>
      </div>
    );
  }

  // User is authenticated (and admin if required)
  return <>{children}</>;
}

export default ProtectedRoute;
