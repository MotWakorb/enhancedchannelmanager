/**
 * Authentication context for managing user auth state.
 */
import { createContext } from 'react';
import type { User, AuthStatus } from '../types';

// Auth context state
export interface AuthContextState {
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
export const AuthContext = createContext<AuthContextState | undefined>(undefined);

