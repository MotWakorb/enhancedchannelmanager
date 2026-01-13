import { useState, useCallback } from 'react';

export interface UseAsyncOperationReturn<T = void> {
  loading: boolean;
  error: string | null;
  execute: (operation: () => Promise<T>) => Promise<T | undefined>;
  clearError: () => void;
  setError: (error: string | null) => void;
}

/**
 * Hook to manage async operation state (loading, error handling).
 *
 * Consolidates the common pattern of:
 * - const [loading, setLoading] = useState(false);
 * - const [error, setError] = useState<string | null>(null);
 * - try { setLoading(true); await operation(); } catch (err) { setError(...); } finally { setLoading(false); }
 *
 * @returns Object containing loading state, error state, and execute wrapper
 *
 * @example
 * const { loading, error, execute, clearError } = useAsyncOperation();
 *
 * const handleSave = async () => {
 *   await execute(async () => {
 *     await api.saveData(formData);
 *     onSuccess();
 *   });
 * };
 *
 * return (
 *   <>
 *     {error && <div className="error">{error}</div>}
 *     <button onClick={handleSave} disabled={loading}>
 *       {loading ? 'Saving...' : 'Save'}
 *     </button>
 *   </>
 * );
 */
export function useAsyncOperation<T = void>(): UseAsyncOperationReturn<T> {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const execute = useCallback(async (operation: () => Promise<T>): Promise<T | undefined> => {
    setLoading(true);
    setError(null);
    try {
      const result = await operation();
      return result;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'An unexpected error occurred';
      setError(message);
      return undefined;
    } finally {
      setLoading(false);
    }
  }, []);

  const clearError = useCallback(() => {
    setError(null);
  }, []);

  return {
    loading,
    error,
    execute,
    clearError,
    setError,
  };
}
