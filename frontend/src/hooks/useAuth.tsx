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
  // Re-read the server's auth configuration (see refreshAuthStatus below)
  refreshAuthStatus: () => Promise<void>;
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

  // Re-read the server's auth configuration.
  //
  // The mount effect above fetches /api/auth/status exactly once, so anything
  // that changes the server's answer mid-session leaves `authStatus` stale.
  // First-run setup is exactly such an event: POST /api/auth/setup creates the
  // operator row and persists `setup_complete` (bead
  // enhancedchannelmanager-qg14z), while the value fetched before setup stays
  // stale. Callers that change auth state must therefore re-read it rather than
  // trust the value cached at mount (bead enhancedchannelmanager-lf29s).
  //
  // Failures deliberately leave the previous status in place, matching
  // refreshUser(): a transient network error should not change what the app
  // believes about the server's auth configuration.
  const refreshAuthStatus = useCallback(async () => {
    try {
      const status = await getAuthStatus();
      setAuthStatus(status);
    } catch {
      // Keep the cached status; see comment above.
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
    refreshAuthStatus,
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
 * What the server said about whether this app needs an authenticated session.
 *
 * `'resolving'` is a real third answer, not a transient nicety. GET
 * /api/auth/status is fetched exactly once at mount, and when that fetch fails
 * `authStatus` stays null forever. Collapsing that into `false` (bead
 * enhancedchannelmanager-p388h) made an unreachable backend indistinguishable
 * from a deliberately auth-disabled instance, so ProtectedRoute rendered the
 * whole app shell with no session, no login prompt, and no way back: its
 * `!authRequired` branch also rewrote /login to / so typing the URL did not
 * help either.
 */
export type AuthRequirement = 'resolving' | 'required' | 'not-required';

/**
 * Hook reporting whether auth is required for the app.
 *
 * - `'resolving'` while the initial check is in flight, AND after it has
 *   failed. Both are the same fact: the server has not told us. Callers must
 *   treat this as unknown and must NOT fall through to rendering the app.
 * - `'required'` when the server reports require_auth AND setup_complete.
 * - `'not-required'` when the server answered and one of those is false.
 */
// eslint-disable-next-line react-refresh/only-export-components -- hook co-located with AuthProvider by convention
export function useAuthRequirement(): AuthRequirement {
  const { authStatus, isLoading } = useAuth();

  // isLoading covers the in-flight case. A null authStatus once loading has
  // finished means the fetch threw and was swallowed in checkAuth's inner
  // catch, which is unknown, not "no".
  if (isLoading || !authStatus) {
    return 'resolving';
  }

  return authStatus.require_auth && authStatus.setup_complete ? 'required' : 'not-required';
}

/**
 * Whether admin-only navigation destinations should be offered to this viewer.
 *
 * The other half of bead enhancedchannelmanager-p388h (absorbed from ee5f1),
 * with the opposite polarity. Nav visibility was `Boolean(user?.is_admin)`,
 * which resolves "no identity" to "not an admin" and hides the Administration
 * settings group. On a `require_auth: false` instance there is never a user
 * row in the client, so that group stayed hidden permanently while the backend
 * served it to anyone: `auth.dependencies.require_admin_if_enabled` returns
 * early when auth is disabled. The UI was strictly less permissive than the
 * API, which is a usability defect and not a control.
 *
 * This is navigation only. Backend enforcement is independent, so showing a
 * destination never grants anything; hiding one only prevented an operator
 * from reaching what they were already allowed to use.
 */
// eslint-disable-next-line react-refresh/only-export-components -- hook co-located with AuthProvider by convention
export function useAdminNavVisible(): boolean {
  const { user } = useAuth();
  const requirement = useAuthRequirement();

  // Auth off: the backend admits anonymous callers to its admin gates, so the
  // nav must not be narrower than the API.
  if (requirement === 'not-required') {
    return true;
  }

  // 'resolving' lands here and yields false. ProtectedRoute does not render
  // the app in that state at all, so this is a defensive default rather than a
  // reachable one.
  return Boolean(user?.is_admin);
}
