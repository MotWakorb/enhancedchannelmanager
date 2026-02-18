/**
 * Authentication context and hook for managing user auth state.
 *
 * Provides:
 * - AuthProvider: Wrap app to provide auth context
 * - useAuth: Hook to access auth state and methods
 */
import { createContext, useContext, useState, useEffect, useCallback, ReactNode } from 'react';
import type { User, AuthStatus } from '../types';
import {
  login as apiLogin,
  dispatcharrLogin as apiDispatcharrLogin,
  logout as apiLogout,
  getCurrentUser,
  getAuthStatus,
} from '../services/api';

// Auth context state
interface AuthContextState {
  // Current user (null if not authenticated)
  user: User | null;
  // Auth configuration from server
  authStatus: AuthStatus | null;
  // Loading state during initial auth check
  isLoading: boolean;
  // Whether user is authenticated
  isAuthenticated: boolean;
  // Login with username and password (local auth)
  login: (username: string, password: string) => Promise<void>;
  // Login with Dispatcharr credentials
  loginWithDispatcharr: (username: string, password: string) => Promise<void>;
  // Logout current user
  logout: () => Promise<void>;
  // Refresh current user data
  refreshUser: () => Promise<void>;
}

// Create context with undefined default
const AuthContext = createContext<AuthContextState | undefined>(undefined);

// Provider props
interface AuthProviderProps {
  children: ReactNode;
}

/**
 * AuthProvider component that wraps the app to provide auth context.
 *
 * On mount, checks for existing session and loads user data.
 * Provides login/logout methods and user state to children.
 */
export function AuthProvider({ children }: AuthProviderProps) {
  const [user, setUser] = useState<User | null>(null);
  const [authStatus, setAuthStatus] = useState<AuthStatus | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  // Check for existing session on mount
  useEffect(() => {
    const checkAuth = async () => {
      try {
        // First get auth status to know if auth is required
        try {
          const status = await getAuthStatus();
          setAuthStatus(status);

          // If auth is not required or setup not complete, no need to check user
          if (!status.require_auth || !status.setup_complete) {
            setIsLoading(false);
            return;
          }
        } catch {
          // If getAuthStatus fails (e.g., in tests), continue to try getCurrentUser
          // This allows the hook to work even if the auth status endpoint is unavailable
        }

        // Try to get current user (will use existing cookie)
        const response = await getCurrentUser();
        setUser(response.user);
      } catch {
        // Not authenticated or error - that's fine
        setUser(null);
      } finally {
        setIsLoading(false);
      }
    };

    checkAuth();
  }, []);

  // Login method (local auth)
  const login = useCallback(async (username: string, password: string) => {
    const response = await apiLogin(username, password);
    setUser(response.user);
  }, []);

  // Login with Dispatcharr
  const loginWithDispatcharr = useCallback(async (username: string, password: string) => {
    const response = await apiDispatcharrLogin(username, password);
    setUser(response.user);
  }, []);

  // Logout method
  const logout = useCallback(async () => {
    try {
      await apiLogout();
    } finally {
      // Always clear user state, even if logout API fails
      setUser(null);
    }
  }, []);

  // Refresh user data
  const refreshUser = useCallback(async () => {
    try {
      const response = await getCurrentUser();
      setUser(response.user);
    } catch {
      setUser(null);
    }
  }, []);

  // Context value
  const value: AuthContextState = {
    user,
    authStatus,
    isLoading,
    isAuthenticated: user !== null,
    login,
    loginWithDispatcharr,
    logout,
    refreshUser,
  };

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  );
}

/**
 * Hook to access auth context.
 *
 * Must be used within an AuthProvider.
 *
 * @returns Auth context state and methods
 * @throws Error if used outside AuthProvider
 */
export function useAuth(): AuthContextState {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}

/**
 * Hook to check if auth is required for the app.
 *
 * Returns true if:
 * - Auth settings are loaded AND
 * - require_auth is true AND
 * - setup is complete
 *
 * Returns false if:
 * - Still loading OR
 * - Auth is disabled OR
 * - Setup not complete
 */
export function useAuthRequired(): boolean {
  const { authStatus, isLoading } = useAuth();

  if (isLoading || !authStatus) {
    return false;
  }

  return authStatus.require_auth && authStatus.setup_complete;
}
