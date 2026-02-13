/**
 * Authentication provider for managing user auth state.
 */
import { useState, useEffect, useCallback, ReactNode } from 'react';
import type { User, AuthStatus } from '../types';
import { AuthContext, type AuthContextState } from '../contexts/AuthContext';
import {
    login as apiLogin,
    dispatcharrLogin as apiDispatcharrLogin,
    logout as apiLogout,
    getCurrentUser,
    getAuthStatus,
} from '../services/api';

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