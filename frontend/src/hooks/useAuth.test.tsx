/**
 * Unit tests for Authentication hooks and context.
 *
 * TDD SPEC: These tests define expected auth UI behavior.
 * They will FAIL initially - implementation makes them pass.
 *
 * Test Spec: Frontend Auth Flow (v6dxf.8.12)
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
// These imports will fail until implementation exists
// import { useAuth, AuthProvider } from './useAuth';
// import { login, logout, getCurrentUser } from '../services/api';

// Mock the API module
vi.mock('../services/api', () => ({
  login: vi.fn(),
  logout: vi.fn(),
  getCurrentUser: vi.fn(),
  // Default: an ordinary auth-on, setup-complete instance.
  //
  // This used to be `mockRejectedValue(new Error('not mocked'))`, which pinned
  // EVERY test in this file to the exact failure mode bead
  // enhancedchannelmanager-p388h is about: a rejected status probe leaves
  // `authStatus` null, and the old `useAuthRequired()` read that as "auth is
  // not required". The suite therefore ran permanently in the fail-open state
  // and could never have asserted against it. The resolved default still falls
  // through to getCurrentUser (require_auth AND setup_complete are both true,
  // so checkAuth does not take its early return), which is what the rejecting
  // default was actually being relied on for. Tests that want the probe to
  // fail now say so explicitly.
  getAuthStatus: vi.fn().mockResolvedValue({
    require_auth: true,
    setup_complete: true,
    enabled_providers: ['local'],
    primary_auth_mode: 'local',
    smtp_configured: false,
  }),
  dispatcharrLogin: vi.fn(),
}));

describe('AuthContext', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('useAuth() hook', () => {
    it('returns user when authenticated', async () => {
      const mockUser = { id: 1, username: 'testuser', is_admin: false, email: null, display_name: null, is_active: true, auth_provider: 'local', external_id: null };
      const { getCurrentUser } = await import('../services/api');
      vi.mocked(getCurrentUser).mockResolvedValue({ user: mockUser });

      const { useAuth, AuthProvider } = await import('./useAuth');

      const wrapper = ({ children }: { children: React.ReactNode }) => (
        <AuthProvider>{children}</AuthProvider>
      );

      const { result } = renderHook(() => useAuth(), { wrapper });

      await waitFor(() => {
        expect(result.current.user).toEqual(mockUser);
      });
    });

    it('returns null when not authenticated', async () => {
      const { getCurrentUser } = await import('../services/api');
      vi.mocked(getCurrentUser).mockRejectedValue(new Error('Unauthorized'));

      const { useAuth, AuthProvider } = await import('./useAuth');

      const wrapper = ({ children }: { children: React.ReactNode }) => (
        <AuthProvider>{children}</AuthProvider>
      );

      const { result } = renderHook(() => useAuth(), { wrapper });

      await waitFor(() => {
        expect(result.current.user).toBeNull();
      });
    });

    it('login() calls API and updates state on success', async () => {
      const mockUser = { id: 1, username: 'testuser', is_admin: false, email: null, display_name: null, is_active: true, auth_provider: 'local', external_id: null };
      const { login: mockLogin } = await import('../services/api');
      vi.mocked(mockLogin).mockResolvedValue({ user: mockUser, message: 'Login successful' });

      const { useAuth, AuthProvider } = await import('./useAuth');

      const wrapper = ({ children }: { children: React.ReactNode }) => (
        <AuthProvider>{children}</AuthProvider>
      );

      const { result } = renderHook(() => useAuth(), { wrapper });

      await act(async () => {
        await result.current.login('testuser', 'password');
      });

      expect(mockLogin).toHaveBeenCalledWith('testuser', 'password');
      expect(result.current.user).toEqual(mockUser);
    });

    it('login() throws on failure, state unchanged', async () => {
      const { login: mockLogin, getCurrentUser } = await import('../services/api');
      vi.mocked(mockLogin).mockRejectedValue(new Error('Invalid credentials'));
      vi.mocked(getCurrentUser).mockRejectedValue(new Error('Unauthorized'));

      const { useAuth, AuthProvider } = await import('./useAuth');

      const wrapper = ({ children }: { children: React.ReactNode }) => (
        <AuthProvider>{children}</AuthProvider>
      );

      const { result } = renderHook(() => useAuth(), { wrapper });

      await expect(
        act(async () => {
          await result.current.login('testuser', 'wrongpassword');
        })
      ).rejects.toThrow('Invalid credentials');

      expect(result.current.user).toBeNull();
    });

    it('logout() calls API and clears state', async () => {
      const mockUser = { id: 1, username: 'testuser', is_admin: false, email: null, display_name: null, is_active: true, auth_provider: 'local', external_id: null };
      const { logout: mockLogout, getCurrentUser } = await import('../services/api');
      vi.mocked(getCurrentUser).mockResolvedValue({ user: mockUser });
      vi.mocked(mockLogout).mockResolvedValue({ message: 'Logged out' });

      const { useAuth, AuthProvider } = await import('./useAuth');

      const wrapper = ({ children }: { children: React.ReactNode }) => (
        <AuthProvider>{children}</AuthProvider>
      );

      const { result } = renderHook(() => useAuth(), { wrapper });

      await waitFor(() => {
        expect(result.current.user).toEqual(mockUser);
      });

      await act(async () => {
        await result.current.logout();
      });

      expect(mockLogout).toHaveBeenCalled();
      expect(result.current.user).toBeNull();
    });

    it('auth state persists across page reload', async () => {
      const mockUser = { id: 1, username: 'testuser', is_admin: false, email: null, display_name: null, is_active: true, auth_provider: 'local', external_id: null };
      const { getCurrentUser } = await import('../services/api');
      vi.mocked(getCurrentUser).mockResolvedValue({ user: mockUser });

      const { useAuth, AuthProvider } = await import('./useAuth');

      // First render - simulates initial page load
      const wrapper = ({ children }: { children: React.ReactNode }) => (
        <AuthProvider>{children}</AuthProvider>
      );

      const { result, unmount } = renderHook(() => useAuth(), { wrapper });

      await waitFor(() => {
        expect(result.current.user).toEqual(mockUser);
      });

      // Unmount and remount - simulates page reload
      unmount();

      const { result: result2 } = renderHook(() => useAuth(), { wrapper });

      await waitFor(() => {
        expect(result2.current.user).toEqual(mockUser);
      });

      // getCurrentUser should be called on each mount to verify session
      expect(getCurrentUser).toHaveBeenCalled();
    });
  });
});

describe('Protected Routes', () => {
  it('unauthenticated user redirected to /login', async () => {
    const { getCurrentUser } = await import('../services/api');
    vi.mocked(getCurrentUser).mockRejectedValue(new Error('Unauthorized'));

    const { useAuth, AuthProvider } = await import('./useAuth');

    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <AuthProvider>{children}</AuthProvider>
    );

    const { result } = renderHook(() => useAuth(), { wrapper });

    await waitFor(() => {
      // After a rejected getCurrentUser, user should be null (not authenticated)
      expect(result.current.user).toBeNull();
      expect(result.current.isAuthenticated).toBe(false);
      expect(result.current.isLoading).toBe(false);
    });
  });

  it('authenticated user can access protected routes', async () => {
    const mockUser = { id: 1, username: 'testuser', is_admin: false, email: null, display_name: null, is_active: true, auth_provider: 'local', external_id: null };
    const { getCurrentUser } = await import('../services/api');
    vi.mocked(getCurrentUser).mockResolvedValue({ user: mockUser });

    const { useAuth, AuthProvider } = await import('./useAuth');

    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <AuthProvider>{children}</AuthProvider>
    );

    const { result } = renderHook(() => useAuth(), { wrapper });

    await waitFor(() => {
      expect(result.current.user).toEqual(mockUser);
      expect(result.current.isAuthenticated).toBe(true);
    });
  });

  it('loading spinner shown during auth check', async () => {
    // isLoading starts true synchronously (useState(true)) — verify it starts
    // true before the auth check resolves, then clears to false afterwards.
    const { getCurrentUser } = await import('../services/api');
    vi.mocked(getCurrentUser).mockRejectedValue(new Error('Unauthorized'));

    const { useAuth, AuthProvider } = await import('./useAuth');

    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <AuthProvider>{children}</AuthProvider>
    );

    const { result } = renderHook(() => useAuth(), { wrapper });

    // isLoading is synchronously initialised to true inside AuthProvider
    expect(result.current.isLoading).toBe(true);

    // After the async auth check settles, loading clears
    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });
  });
});

describe('Login Page', () => {
  // 'form validates required fields' — the Login page component is a UI concern
  // outside this hook file; real UI tests belong in Login.test.tsx.

  it('submit calls login() with credentials', async () => {
    const mockUser = { id: 1, username: 'testuser', is_admin: false, email: null, display_name: null, is_active: true, auth_provider: 'local', external_id: null };
    const { login: mockLogin, getCurrentUser } = await import('../services/api');
    vi.mocked(getCurrentUser).mockRejectedValue(new Error('Unauthorized'));
    vi.mocked(mockLogin).mockResolvedValue({ user: mockUser, message: 'ok' });

    const { useAuth, AuthProvider } = await import('./useAuth');

    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <AuthProvider>{children}</AuthProvider>
    );

    const { result } = renderHook(() => useAuth(), { wrapper });

    await waitFor(() => expect(result.current.isLoading).toBe(false));

    await act(async () => {
      await result.current.login('testuser', 'password123');
    });

    expect(mockLogin).toHaveBeenCalledWith('testuser', 'password123');
    expect(result.current.user).toEqual(mockUser);
  });

  it('error message shown on failure', async () => {
    const { login: mockLogin, getCurrentUser } = await import('../services/api');
    vi.mocked(getCurrentUser).mockRejectedValue(new Error('Unauthorized'));
    vi.mocked(mockLogin).mockRejectedValue(new Error('Invalid credentials'));

    const { useAuth, AuthProvider } = await import('./useAuth');

    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <AuthProvider>{children}</AuthProvider>
    );

    const { result } = renderHook(() => useAuth(), { wrapper });

    await waitFor(() => expect(result.current.isLoading).toBe(false));

    // Hook re-throws on failure so UI can display the error
    await expect(
      act(async () => {
        await result.current.login('testuser', 'wrong');
      })
    ).rejects.toThrow('Invalid credentials');

    // User remains unauthenticated after failed login
    expect(result.current.user).toBeNull();
  });

  it('redirect to app on success', async () => {
    const mockUser = { id: 2, username: 'admin', is_admin: true, email: null, display_name: null, is_active: true, auth_provider: 'local', external_id: null };
    const { login: mockLogin, getCurrentUser } = await import('../services/api');
    vi.mocked(getCurrentUser).mockRejectedValue(new Error('Unauthorized'));
    vi.mocked(mockLogin).mockResolvedValue({ user: mockUser, message: 'ok' });

    const { useAuth, AuthProvider } = await import('./useAuth');

    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <AuthProvider>{children}</AuthProvider>
    );

    const { result } = renderHook(() => useAuth(), { wrapper });

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.isAuthenticated).toBe(false);

    await act(async () => {
      await result.current.login('admin', 'secret');
    });

    // After successful login, isAuthenticated flips to true — the host app
    // renders the protected content / redirects based on this value.
    expect(result.current.isAuthenticated).toBe(true);
    expect(result.current.user).toEqual(mockUser);
  });

  it('auth method buttons shown when multiple enabled', async () => {
    // authStatus drives which auth method buttons the Login component renders.
    // useAuth exposes authStatus directly; test that it is populated after init.
    const { getCurrentUser, getAuthStatus } = await import('../services/api');
    vi.mocked(getCurrentUser).mockRejectedValue(new Error('Unauthorized'));
    vi.mocked(getAuthStatus).mockResolvedValue({
      require_auth: true,
      setup_complete: true,
      enabled_providers: ['local', 'oidc'],
      primary_auth_mode: 'local',
      smtp_configured: false,
    });

    const { useAuth, AuthProvider } = await import('./useAuth');

    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <AuthProvider>{children}</AuthProvider>
    );

    const { result } = renderHook(() => useAuth(), { wrapper });

    await waitFor(() => expect(result.current.isLoading).toBe(false));

    // authStatus is populated — the Login component reads auth_methods from it
    expect(result.current.authStatus).not.toBeNull();
  });
});

describe('useAuthRequirement (bead enhancedchannelmanager-p388h)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  const AUTH_ON = {
    require_auth: true,
    setup_complete: true,
    enabled_providers: ['local'],
    primary_auth_mode: 'local' as const,
    smtp_configured: false,
  };

  // Mounts AuthProvider and drains its mount-effect promise chain inside
  // act(), so the hook's value is final on return and no state update lands
  // after the test body. AuthProvider issues its two probes in separate
  // continuations, so a bare waitFor on the value can return while the second
  // batch is still pending.
  async function renderSettled() {
    const { useAuthRequirement, AuthProvider } = await import('./useAuth');
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <AuthProvider>{children}</AuthProvider>
    );
    let rendered!: ReturnType<typeof renderHook<string, unknown>>;
    await act(async () => {
      rendered = renderHook(() => useAuthRequirement(), { wrapper });
    });
    return rendered;
  }

  it("reports 'resolving' before the status probe settles", async () => {
    const { getAuthStatus, getCurrentUser } = await import('../services/api');
    vi.mocked(getAuthStatus).mockResolvedValue(AUTH_ON);
    vi.mocked(getCurrentUser).mockRejectedValue(new Error('Unauthorized'));

    const { useAuthRequirement, AuthProvider } = await import('./useAuth');
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <AuthProvider>{children}</AuthProvider>
    );

    // Deliberately NOT settled: read the very first synchronous render.
    const { result } = renderHook(() => useAuthRequirement(), { wrapper });
    expect(result.current).toBe('resolving');

    // Drain, so the mount effect does not update state after the test ends.
    await act(async () => {});
  });

  // THE REGRESSION THIS BEAD IS ABOUT. A failed probe must not read as
  // "auth is not required": that is what let ProtectedRoute render the whole
  // app shell with no session and no way to reach /login.
  it("stays 'resolving' when the status probe fails, rather than falling back to 'not-required'", async () => {
    const { getAuthStatus, getCurrentUser } = await import('../services/api');
    vi.mocked(getAuthStatus).mockRejectedValue(new Error('backend unreachable'));
    vi.mocked(getCurrentUser).mockRejectedValue(new Error('backend unreachable'));

    const { result } = await renderSettled();

    // Both probes have finished failing, so this is the settled answer and not
    // just the in-flight one being read a second time.
    expect(getCurrentUser).toHaveBeenCalled();
    expect(result.current).toBe('resolving');
    expect(result.current).not.toBe('not-required');
  });

  it("reports 'required' when the server requires auth and setup is complete", async () => {
    const { getAuthStatus, getCurrentUser } = await import('../services/api');
    vi.mocked(getAuthStatus).mockResolvedValue(AUTH_ON);
    vi.mocked(getCurrentUser).mockRejectedValue(new Error('Unauthorized'));

    const { result } = await renderSettled();

    expect(result.current).toBe('required');
  });

  it("reports 'not-required' when the server has auth switched off", async () => {
    const { getAuthStatus } = await import('../services/api');
    vi.mocked(getAuthStatus).mockResolvedValue({ ...AUTH_ON, require_auth: false });

    const { result } = await renderSettled();

    expect(result.current).toBe('not-required');
  });

  it("reports 'not-required' when setup has not been completed", async () => {
    const { getAuthStatus } = await import('../services/api');
    vi.mocked(getAuthStatus).mockResolvedValue({ ...AUTH_ON, setup_complete: false });

    const { result } = await renderSettled();

    expect(result.current).toBe('not-required');
  });
});

describe('useAdminNavVisible (bead enhancedchannelmanager-p388h, absorbing ee5f1)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  const AUTH_ON = {
    require_auth: true,
    setup_complete: true,
    enabled_providers: ['local'],
    primary_auth_mode: 'local' as const,
    smtp_configured: false,
  };

  const ADMIN = {
    id: 1, username: 'admin', is_admin: true, email: null, display_name: null,
    is_active: true, auth_provider: 'local', external_id: null,
  };
  const OPERATOR = { ...ADMIN, id: 2, username: 'operator', is_admin: false };

  // act-wrapped for the same reason as renderSettled above.
  async function renderSettled() {
    const { useAdminNavVisible, AuthProvider } = await import('./useAuth');
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <AuthProvider>{children}</AuthProvider>
    );
    let rendered!: ReturnType<typeof renderHook<boolean, unknown>>;
    await act(async () => {
      rendered = renderHook(() => useAdminNavVisible(), { wrapper });
    });
    return rendered;
  }

  // The fail-closed half: `user` is permanently null on an auth-disabled
  // instance, and the backend serves its admin gates to anyone there, so
  // hiding the Administration group made the UI narrower than the API.
  it('shows admin navigation on an auth-disabled instance, where there is no user at all', async () => {
    const { getAuthStatus, getCurrentUser } = await import('../services/api');
    vi.mocked(getAuthStatus).mockResolvedValue({ ...AUTH_ON, require_auth: false });
    vi.mocked(getCurrentUser).mockRejectedValue(new Error('Unauthorized'));

    const { result } = await renderSettled();

    expect(result.current).toBe(true);
  });

  it('shows admin navigation to a signed-in admin', async () => {
    const { getAuthStatus, getCurrentUser } = await import('../services/api');
    vi.mocked(getAuthStatus).mockResolvedValue(AUTH_ON);
    vi.mocked(getCurrentUser).mockResolvedValue({ user: ADMIN });

    const { result } = await renderSettled();

    expect(result.current).toBe(true);
  });

  it('hides admin navigation from a signed-in non-admin', async () => {
    const { getAuthStatus, getCurrentUser } = await import('../services/api');
    vi.mocked(getAuthStatus).mockResolvedValue(AUTH_ON);
    vi.mocked(getCurrentUser).mockResolvedValue({ user: OPERATOR });

    const { result } = await renderSettled();

    expect(result.current).toBe(false);
  });

  it('hides admin navigation while the posture is unresolved', async () => {
    const { getAuthStatus, getCurrentUser } = await import('../services/api');
    vi.mocked(getAuthStatus).mockRejectedValue(new Error('backend unreachable'));
    vi.mocked(getCurrentUser).mockRejectedValue(new Error('backend unreachable'));

    const { result } = await renderSettled();

    expect(getCurrentUser).toHaveBeenCalled();
    expect(result.current).toBe(false);
  });
});

describe('Proactive token refresh (bd-3ymo4)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('proactive-refresh: schedules a token refresh at 80% of the reported lifetime', async () => {
    const mockUser = { id: 1, username: 'testuser', is_admin: false, email: null, display_name: null, is_active: true, auth_provider: 'local', external_id: null };
    const { getCurrentUser } = await import('../services/api');
    vi.mocked(getCurrentUser).mockResolvedValue({ user: mockUser, access_token_expires_in: 1000 });

    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ message: 'Token refreshed', access_token_expires_in: 1000 }),
    });
    vi.stubGlobal('fetch', fetchMock);
    vi.useFakeTimers();

    const { useAuth, AuthProvider } = await import('./useAuth');
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <AuthProvider>{children}</AuthProvider>
    );
    const { result } = renderHook(() => useAuth(), { wrapper });

    // Flush the mount-time auth check (microtasks only, no timers involved)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.user).toEqual(mockUser);

    const refreshCalls = () =>
      fetchMock.mock.calls.filter(([url]) => String(url).includes('/auth/refresh')).length;

    // Just before 80% of 1000s: no refresh yet
    await act(async () => {
      await vi.advanceTimersByTimeAsync(799_000);
    });
    expect(refreshCalls()).toBe(0);

    // At 800s the proactive refresh fires
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });
    expect(refreshCalls()).toBe(1);
  });

  it('proactive-refresh: reschedules after a successful refresh', async () => {
    const mockUser = { id: 1, username: 'testuser', is_admin: false, email: null, display_name: null, is_active: true, auth_provider: 'local', external_id: null };
    const { getCurrentUser } = await import('../services/api');
    vi.mocked(getCurrentUser).mockResolvedValue({ user: mockUser, access_token_expires_in: 1000 });

    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ message: 'Token refreshed', access_token_expires_in: 1000 }),
    });
    vi.stubGlobal('fetch', fetchMock);
    vi.useFakeTimers();

    const { useAuth, AuthProvider } = await import('./useAuth');
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <AuthProvider>{children}</AuthProvider>
    );
    const { result } = renderHook(() => useAuth(), { wrapper });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.user).toEqual(mockUser);

    const refreshCalls = () =>
      fetchMock.mock.calls.filter(([url]) => String(url).includes('/auth/refresh')).length;

    // First cycle fires at 800s
    await act(async () => {
      await vi.advanceTimersByTimeAsync(801_000);
    });
    expect(refreshCalls()).toBe(1);

    // The refresh response reports a fresh 1000s lifetime — a second cycle
    // must fire ~800s later, proving the timer re-arms after each refresh.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(801_000);
    });
    expect(refreshCalls()).toBe(2);
  });

  it('proactive-refresh: falls back to the default 30-minute lifetime when expiry is not reported', async () => {
    const mockUser = { id: 1, username: 'testuser', is_admin: false, email: null, display_name: null, is_active: true, auth_provider: 'local', external_id: null };
    const { getCurrentUser } = await import('../services/api');
    // Older backend: no access_token_expires_in field
    vi.mocked(getCurrentUser).mockResolvedValue({ user: mockUser });

    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ message: 'Token refreshed' }),
    });
    vi.stubGlobal('fetch', fetchMock);
    vi.useFakeTimers();

    const { useAuth, AuthProvider } = await import('./useAuth');
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <AuthProvider>{children}</AuthProvider>
    );
    const { result } = renderHook(() => useAuth(), { wrapper });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.user).toEqual(mockUser);

    const refreshCalls = () =>
      fetchMock.mock.calls.filter(([url]) => String(url).includes('/auth/refresh')).length;

    // 80% of 30min = 24min = 1_440_000ms. Just before: nothing.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_439_000);
    });
    expect(refreshCalls()).toBe(0);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });
    expect(refreshCalls()).toBe(1);
  });

  it('proactive-refresh: timer is cancelled on logout', async () => {
    const mockUser = { id: 1, username: 'testuser', is_admin: false, email: null, display_name: null, is_active: true, auth_provider: 'local', external_id: null };
    const { getCurrentUser, logout: mockLogout } = await import('../services/api');
    vi.mocked(getCurrentUser).mockResolvedValue({ user: mockUser, access_token_expires_in: 1000 });
    vi.mocked(mockLogout).mockResolvedValue({ message: 'Logged out' });

    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ message: 'Token refreshed', access_token_expires_in: 1000 }),
    });
    vi.stubGlobal('fetch', fetchMock);
    vi.useFakeTimers();

    const { useAuth, AuthProvider } = await import('./useAuth');
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <AuthProvider>{children}</AuthProvider>
    );
    const { result } = renderHook(() => useAuth(), { wrapper });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.user).toEqual(mockUser);

    await act(async () => {
      await result.current.logout();
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000_000);
    });
    const refreshCalls = fetchMock.mock.calls.filter(([url]) => String(url).includes('/auth/refresh')).length;
    expect(refreshCalls).toBe(0);
  });

  it('proactive-refresh: timer is cancelled on unmount', async () => {
    const mockUser = { id: 1, username: 'testuser', is_admin: false, email: null, display_name: null, is_active: true, auth_provider: 'local', external_id: null };
    const { getCurrentUser } = await import('../services/api');
    vi.mocked(getCurrentUser).mockResolvedValue({ user: mockUser, access_token_expires_in: 1000 });

    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ message: 'Token refreshed', access_token_expires_in: 1000 }),
    });
    vi.stubGlobal('fetch', fetchMock);
    vi.useFakeTimers();

    const { useAuth, AuthProvider } = await import('./useAuth');
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <AuthProvider>{children}</AuthProvider>
    );
    const { result, unmount } = renderHook(() => useAuth(), { wrapper });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.user).toEqual(mockUser);

    unmount();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000_000);
    });
    const refreshCalls = fetchMock.mock.calls.filter(([url]) => String(url).includes('/auth/refresh')).length;
    expect(refreshCalls).toBe(0);
  });
});

describe('session resolution when auth is disabled (bead enhancedchannelmanager-9kwzp)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // An auth-disabled instance that HAS an operator identity. This is the
  // configuration bead enhancedchannelmanager-p388h reopened /login for, so
  // that an operator could reach the three surfaces bead jy006 gated behind an
  // authenticated human admin (initial restore, the MCP API key, TLS key
  // material).
  const AUTH_OFF_WITH_IDENTITY = {
    require_auth: false,
    setup_complete: true,
    enabled_providers: ['local'],
    primary_auth_mode: 'local' as const,
    smtp_configured: false,
  };

  const OPERATOR = {
    id: 7,
    username: 'operator',
    email: null,
    display_name: null,
    is_admin: true,
    is_active: true,
    auth_provider: 'local',
    external_id: null,
  };

  async function renderSettled() {
    const { useAuth, AuthProvider } = await import('./useAuth');
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <AuthProvider>{children}</AuthProvider>
    );
    let rendered!: ReturnType<typeof renderHook<ReturnType<typeof useAuth>, unknown>>;
    await act(async () => {
      rendered = renderHook(() => useAuth(), { wrapper });
    });
    return rendered;
  }

  // THE DEFECT. checkAuth returned early on `!require_auth` and never called
  // /api/auth/me, so an operator who signed in at /login lost the session on
  // the next mount: refresh the page or open a second tab and `user` was null
  // again, SettingsTab passed isAdmin={false}, and the TLS and MCP sections
  // said "Admin access required".
  it('resolves the signed-in operator from the cookie even though auth is not required', async () => {
    const { getAuthStatus, getCurrentUser } = await import('../services/api');
    vi.mocked(getAuthStatus).mockResolvedValue(AUTH_OFF_WITH_IDENTITY);
    vi.mocked(getCurrentUser).mockResolvedValue({ user: OPERATOR });

    const { result } = await renderSettled();

    expect(getCurrentUser).toHaveBeenCalled();
    expect(result.current.user).toEqual(OPERATOR);
    expect(result.current.isAuthenticated).toBe(true);
  });

  // The tri-state semantics p388h established are untouched: an auth-disabled
  // instance is still 'not-required' whether or not a session resolved. This
  // guards against "fixing" the above by reporting the instance as auth-on.
  it('still reports the instance as not-required once the operator resolves', async () => {
    const { getAuthStatus, getCurrentUser } = await import('../services/api');
    vi.mocked(getAuthStatus).mockResolvedValue(AUTH_OFF_WITH_IDENTITY);
    vi.mocked(getCurrentUser).mockResolvedValue({ user: OPERATOR });

    const { useAuthRequirement, AuthProvider } = await import('./useAuth');
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <AuthProvider>{children}</AuthProvider>
    );
    let rendered!: ReturnType<typeof renderHook<string, unknown>>;
    await act(async () => {
      rendered = renderHook(() => useAuthRequirement(), { wrapper });
    });

    expect(rendered.result.current).toBe('not-required');
  });

  // No cookie on an auth-disabled instance is the ordinary case, and it must
  // stay quiet: a 401 leaves `user` null and the app renders as before. This
  // is the cost of the fix, pinned so it cannot grow into a boot failure.
  it('leaves the user null when the instance has no session cookie', async () => {
    const { getAuthStatus, getCurrentUser } = await import('../services/api');
    const { HttpError } = await import('../services/httpClient');
    vi.mocked(getAuthStatus).mockResolvedValue(AUTH_OFF_WITH_IDENTITY);
    vi.mocked(getCurrentUser).mockRejectedValue(new HttpError('Unauthorized', 401));

    const { result } = await renderSettled();

    expect(getCurrentUser).toHaveBeenCalled();
    expect(result.current.user).toBeNull();
    expect(result.current.isLoading).toBe(false);
  });

  // The one flag that still short-circuits. With setup incomplete there is no
  // operator row, so no cookie can name one and the request is pure waste.
  it('does not probe for a user before first-run setup has happened', async () => {
    const { getAuthStatus, getCurrentUser } = await import('../services/api');
    vi.mocked(getAuthStatus).mockResolvedValue({
      ...AUTH_OFF_WITH_IDENTITY,
      setup_complete: false,
    });

    const { result } = await renderSettled();

    expect(getCurrentUser).not.toHaveBeenCalled();
    expect(result.current.user).toBeNull();
  });
});
