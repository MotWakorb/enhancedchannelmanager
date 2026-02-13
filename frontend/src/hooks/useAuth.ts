/**
 * Authentication hooks for accessing auth context.
 */
import { useContext } from 'react';
import { AuthContext, type AuthContextState } from '../contexts/AuthContext';

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