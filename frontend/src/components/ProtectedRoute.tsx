/**
 * Protected route wrapper component.
 *
 * Wraps content that requires authentication.
 * Shows setup page if first-time setup needed, login page if not authenticated.
 * Also handles public password reset pages.
 */
import { ReactNode, useState, useEffect, useCallback } from 'react';
import { useAuth, useAuthRequirement } from '../hooks/useAuth';
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
/**
 * Result of the GET /api/auth/setup-required probe.
 *
 * `'unknown'` exists for the same reason `AuthRequirement`'s `'resolving'`
 * does (bead enhancedchannelmanager-p388h): this probe used to answer
 * `'not-required'` when it THREW, under the comment "if we can't check, assume
 * setup is not required". An unreachable check is unknown, not no.
 */
type SetupCheck = 'checking' | 'required' | 'not-required' | 'unknown';

/** Same SPA navigation idiom LoginPage uses: push, then wake the listeners. */
function navigateTo(path: string) {
  window.history.pushState({}, '', path);
  window.dispatchEvent(new PopStateEvent('popstate'));
}

export function ProtectedRoute({ children, requireAdmin = false }: ProtectedRouteProps) {
  const { user, authStatus, isLoading, isAuthenticated, refreshUser, refreshAuthStatus } = useAuth();
  const authRequirement = useAuthRequirement();
  const [setupCheck, setSetupCheck] = useState<SetupCheck>('checking');
  const [retrying, setRetrying] = useState(false);
  const [currentPath, setCurrentPath] = useState(window.location.pathname);

  // Listen for URL changes (for navigation within password reset flow)
  useEffect(() => {
    const handlePopState = () => {
      setCurrentPath(window.location.pathname);
    };
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  const runSetupCheck = useCallback(async () => {
    try {
      const result = await checkSetupRequired();
      setSetupCheck(result.required ? 'required' : 'not-required');
    } catch {
      // Deliberately NOT 'not-required'. See the SetupCheck doc comment.
      setSetupCheck('unknown');
    }
  }, []);

  // Check if setup is required on mount
  useEffect(() => {
    void runSetupCheck();
  }, [runSetupCheck]);

  // Retry for the unreachable screen below. Re-runs BOTH probes, because
  // either one can be the reason we are stuck: refreshAuthStatus() re-reads
  // /api/auth/status, runSetupCheck() re-reads the setup probe. Neither
  // throws, so the button always settles.
  const handleRetry = useCallback(async () => {
    setRetrying(true);
    try {
      await Promise.all([refreshAuthStatus(), runSetupCheck()]);
    } finally {
      setRetrying(false);
    }
  }, [refreshAuthStatus, runSetupCheck]);

  // Handle setup completion
  //
  // Re-read auth status BEFORE dropping the setup page. POST /api/auth/setup
  // creates the admin row and persists `setup_complete`, but issues no session
  // cookie, so the status cached at mount still reports setup_complete=false.
  // Rendering children on that
  // stale value makes useAuthRequired() return false, and a brand-new instance
  // lands in the app with no session instead of at the login page. There the
  // auto-opened Settings modal's "Restore from Backup" calls
  // /api/backup/restore-initial with no cookie and is refused by its identity
  // gate (bead enhancedchannelmanager-lf29s). A page reload used to hide this,
  // because the reload fetches the newly persisted status.
  //
  // refreshUser() stays because setup leaves no session: it resolves the
  // now-current user, or clears a stale one on the 401 the fresh instance
  // returns. Neither call throws, so the setup page is always dismissed.
  const handleSetupComplete = useCallback(async () => {
    await refreshAuthStatus();
    await refreshUser();
    setSetupCheck('not-required');
  }, [refreshAuthStatus, refreshUser]);

  // Show loading spinner during initial checks
  if (isLoading || setupCheck === 'checking') {
    return (
      <div className="protected-route-loading">
        <span className="material-icons spinning">sync</span>
        <p>Loading...</p>
      </div>
    );
  }

  // Whether first-run setup is needed, or null if nothing told us.
  //
  // The two probes are NOT equivalent, and an earlier version of this comment
  // claimed they were "equivalent in both directions". Live QA disproved that
  // (bead enhancedchannelmanager-9kwzp): on an instance with `setup_complete:
  // true` and zero user rows, GET /api/auth/setup-required answered
  // `{"required": true}` while GET /api/auth/status answered
  // `{"setup_complete": true}`. Only ONE direction holds, because the
  // auto-correction in `auth/routes.py` only runs one way:
  //
  //   setup-required false (user_count > 0)  =>  setup_complete true.
  //     The status handler flips a stale false to true whenever users exist,
  //     and persists the fix. Sound.
  //   setup_complete true                    =>  setup-required false.
  //     NOT sound. Nothing ever flips a persisted true back to false when the
  //     last user row goes away, so `setup_complete` can outlive the account
  //     it was set for.
  //
  // So the derivation below uses only the sound direction: it may conclude
  // setup IS required from `!setup_complete`, and it declines to conclude the
  // opposite from `setup_complete`. That is why the last branch yields null
  // rather than false. Behaviourally the two are the same today, since the
  // only reader is the `=== true` check immediately below and both null and
  // false fall through it; writing null is what keeps the code from asserting
  // the direction that does not hold.
  //
  // This is NOT the permissive guess bead enhancedchannelmanager-p388h
  // removed. It substitutes an answer the server actually gave for one it
  // failed to give, rather than inventing "no". When neither probe answered,
  // the value stays null and the unresolved branch below refuses to render the
  // app. Deriving it matters because a flaky setup probe alone would otherwise
  // wedge an instance that is working fine.
  //
  // No privilege turns on any of this: the identity primitives 401/403 on
  // their own. The residual edge is a UX one, and it needs BOTH the zero-user
  // `setup_complete: true` state above AND a dead setup probe: the app then
  // shows a login page for an instance with no account to log into. With the
  // setup probe alive, which is the normal case, `setupCheck === 'required'`
  // wins outright and the operator gets the setup page, so the edge is not
  // worth code to special-case.
  const setupRequired: boolean | null =
    setupCheck === 'required' ? true
      : setupCheck === 'not-required' ? false
        : authStatus && !authStatus.setup_complete ? true
          : null;

  // Show setup page if first-time setup is required
  if (setupRequired === true) {
    return <SetupPage onSetupComplete={handleSetupComplete} />;
  }

  // Handle public password reset pages (accessible without auth).
  //
  // These stay AHEAD of the unresolved gate below on purpose. They are
  // explicitly-navigated recovery surfaces that carry their own token and do
  // not depend on auth status, so refusing to render them while the status
  // probe is down would remove a recovery path rather than protect one.
  if (currentPath === '/forgot-password') {
    return <ForgotPasswordPage />;
  }
  if (currentPath === '/reset-password') {
    return <ResetPasswordPage />;
  }

  // We could not determine the instance's auth posture. Render neither the app
  // nor a guess (bead enhancedchannelmanager-p388h). /login stays reachable
  // here, because the old behaviour's worst consequence was that an operator
  // could not get to it even by typing the URL.
  //
  // `setupRequired === null` implies this too (it needs authStatus to be null,
  // which is exactly what makes the requirement unresolved once isLoading is
  // false), so this one condition covers both dead probes.
  if (authRequirement === 'resolving') {
    if (currentPath === '/login') {
      return <LoginPage />;
    }
    return (
      <div className="protected-route-unreachable">
        <span className="material-icons">cloud_off</span>
        <h2>Cannot reach ECM</h2>
        <p>
          ECM could not confirm whether this instance requires a sign-in, so it has not
          loaded. The server may still be starting, or it may be unreachable from this
          browser.
        </p>
        <div className="protected-route-unreachable-actions">
          <button type="button" onClick={() => void handleRetry()} disabled={retrying}>
            {retrying ? 'Retrying...' : 'Retry'}
          </button>
          <a
            href="/login"
            onClick={(e) => {
              e.preventDefault();
              navigateTo('/login');
            }}
          >
            Log in
          </a>
        </div>
      </div>
    );
  }

  // If auth is not required, show children directly
  if (authRequirement === 'not-required') {
    // /login stays reachable on an auth-disabled instance that already has an
    // operator identity (authStatus.setup_complete). Bead
    // enhancedchannelmanager-jy006 made three identity primitives (initial
    // restore, the MCP API key, and TLS key material) require an authenticated
    // human admin even while require_auth is false, and the API accepts a
    // login in that mode. Rewriting /login to / left those surfaces
    // UI-unreachable on exactly the instances that need them, which is the
    // same recoverability defect as the branch above.
    if (currentPath === '/login' && authStatus?.setup_complete && !isAuthenticated) {
      return <LoginPage />;
    }
    // Clean up auth-related paths that cannot resolve to anything here: no
    // operator identity to log into, or already signed in.
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
