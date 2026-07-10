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
import { HttpError, subscribeTokenRefresh, tryRefreshToken } from '../services/httpClient';

// Proactive access-token refresh (bd-3ymo4). The access token is an httpOnly
// cookie the client cannot read, so the backend reports its lifetime as
// read-only metadata (access_token_expires_in) on login//me/refresh
// responses. We refresh at 80% of that lifetime so background polls never
// hit the reactive 401-then-retry path after expiry.
const DEFAULT_ACCESS_TOKEN_LIFETIME_SECONDS = 30 * 60; // matches backend ACCESS_TOKEN_EXPIRE_MINUTES default
const PROACTIVE_REFRESH_FRACTION = 0.8;
// Floor so a tiny/zero reported lifetime can't hot-loop refresh requests.
const MIN_PROACTIVE_REFRESH_DELAY_MS = 30_000;

// Timer schedule descriptor. `refreshedAt` exists purely to give the object a
// new identity each time a refresh happens, so the scheduling effect re-runs
// (and resets its timer) even when the reported lifetime value is unchanged.
interface TokenSchedule {
  expiresInSeconds: number | null;
  refreshedAt: number;
}

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
  // Proactive-refresh schedule (bd-3ymo4): null until an auth response
  // reports token expiry. Replaced (new object identity) on every login,
  // auth check, and successful refresh so the timer effect resets.
  const [tokenSchedule, setTokenSchedule] = useState<TokenSchedule | null>(null);

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
        // /me reports the REMAINING lifetime of the current token, so the
        // proactive refresh timer stays accurate mid-lifetime (bd-3ymo4).
        setTokenSchedule({
          expiresInSeconds: response.access_token_expires_in ?? null,
          refreshedAt: Date.now(),
        });
      } catch (error) {
        // Only clear user for auth errors (401/403). Server errors (500, network)
        // should not boot the user to the login page.
        if (error instanceof HttpError && (error.status === 401 || error.status === 403)) {
          setUser(null);
        }
        // For non-auth errors, leave user state as-is (null on initial mount, preserved on re-check)
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
    setTokenSchedule({
      expiresInSeconds: response.access_token_expires_in ?? null,
      refreshedAt: Date.now(),
    });
  }, []);

  // Login with Dispatcharr
  const loginWithDispatcharr = useCallback(async (username: string, password: string) => {
    const response = await apiDispatcharrLogin(username, password);
    setUser(response.user);
    setTokenSchedule({
      expiresInSeconds: response.access_token_expires_in ?? null,
      refreshedAt: Date.now(),
    });
  }, []);

  // Logout method
  const logout = useCallback(async () => {
    try {
      await apiLogout();
    } finally {
      // Always clear user state, even if logout API fails
      setUser(null);
      setTokenSchedule(null);
    }
  }, []);

  // Refresh user data
  const refreshUser = useCallback(async () => {
    try {
      const response = await getCurrentUser();
      setUser(response.user);
      setTokenSchedule({
        expiresInSeconds: response.access_token_expires_in ?? null,
        refreshedAt: Date.now(),
      });
    } catch (error) {
      // Only clear user for auth errors; server/network errors should not log out
      if (error instanceof HttpError && (error.status === 401 || error.status === 403)) {
        setUser(null);
      }
    }
  }, []);

  // Reschedule the proactive refresh timer after ANY successful token
  // refresh — proactive (our timer below) or reactive (httpClient's
  // 401-retry path) — so the timer always tracks the newest token.
  useEffect(() => {
    return subscribeTokenRefresh((expiresInSeconds) => {
      setTokenSchedule({ expiresInSeconds, refreshedAt: Date.now() });
    });
  }, []);

  // Proactive access-token refresh timer (bd-3ymo4). Fires at 80% of the
  // token lifetime and reuses httpClient's tryRefreshToken (single refresh
  // path, shared mutex). On success the subscribeTokenRefresh listener above
  // reschedules; on failure we deliberately do NOT retry here — the reactive
  // 401 path remains the fallback, and a later successful reactive refresh
  // re-arms this timer via the same listener. Cleared on logout/unmount
  // (user becomes null → cleanup runs, no new timer scheduled).
  useEffect(() => {
    if (!user) return;
    const lifetimeSeconds = tokenSchedule?.expiresInSeconds ?? DEFAULT_ACCESS_TOKEN_LIFETIME_SECONDS;
    const delayMs = Math.max(
      lifetimeSeconds * 1000 * PROACTIVE_REFRESH_FRACTION,
      MIN_PROACTIVE_REFRESH_DELAY_MS,
    );
    const timer = setTimeout(() => {
      void tryRefreshToken();
    }, delayMs);
    return () => clearTimeout(timer);
  }, [user, tokenSchedule]);

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
// eslint-disable-next-line react-refresh/only-export-components -- hook co-located with AuthProvider by convention; moving would cascade across many consumers
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
// eslint-disable-next-line react-refresh/only-export-components -- hook co-located with AuthProvider by convention
export function useAuthRequired(): boolean {
  const { authStatus, isLoading } = useAuth();

  if (isLoading || !authStatus) {
    return false;
  }

  return authStatus.require_auth && authStatus.setup_complete;
}
