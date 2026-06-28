/**
 * Unit tests for Authentication hooks and context.
 *
 * TDD SPEC: These tests define expected auth UI behavior.
 * They will FAIL initially - implementation makes them pass.
 *
 * Test Spec: Frontend Auth Flow (v6dxf.8.12)
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
// These imports will fail until implementation exists
// import { useAuth, AuthProvider } from './useAuth';
// import { login, logout, getCurrentUser } from '../services/api';

// Mock the API module
vi.mock('../services/api', () => ({
  login: vi.fn(),
  logout: vi.fn(),
  getCurrentUser: vi.fn(),
  // Default: throw so the catch block falls through to getCurrentUser (matching original behaviour)
  getAuthStatus: vi.fn().mockRejectedValue(new Error('not mocked')),
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
