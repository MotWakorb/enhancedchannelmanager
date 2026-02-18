/**
 * Unit tests for useAsyncOperation hook.
 */
import { describe, it, expect } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useAsyncOperation } from './useAsyncOperation';

describe('useAsyncOperation', () => {
  describe('initial state', () => {
    it('starts with loading false', () => {
      const { result } = renderHook(() => useAsyncOperation());
      expect(result.current.loading).toBe(false);
    });

    it('starts with error null', () => {
      const { result } = renderHook(() => useAsyncOperation());
      expect(result.current.error).toBeNull();
    });
  });

  describe('execute', () => {
    it('sets loading true during operation', async () => {
      const { result } = renderHook(() => useAsyncOperation());
      let resolvePromise: () => void;
      const promise = new Promise<void>((resolve) => {
        resolvePromise = resolve;
      });

      act(() => {
        result.current.execute(() => promise);
      });

      expect(result.current.loading).toBe(true);

      await act(async () => {
        resolvePromise!();
        await promise;
      });

      expect(result.current.loading).toBe(false);
    });

    it('returns result on success', async () => {
      const { result } = renderHook(() => useAsyncOperation<string>());

      let returnValue: string | undefined;
      await act(async () => {
        returnValue = await result.current.execute(async () => 'success');
      });

      expect(returnValue).toBe('success');
    });

    it('clears previous error on new operation', async () => {
      const { result } = renderHook(() => useAsyncOperation());

      // First operation fails
      await act(async () => {
        await result.current.execute(async () => {
          throw new Error('first error');
        });
      });

      expect(result.current.error).toBe('first error');

      // Start new operation
      const promise = new Promise<void>((resolve) => setTimeout(resolve, 10));
      act(() => {
        result.current.execute(() => promise);
      });

      // Error should be cleared
      expect(result.current.error).toBeNull();
    });

    it('sets error on failure', async () => {
      const { result } = renderHook(() => useAsyncOperation());

      await act(async () => {
        await result.current.execute(async () => {
          throw new Error('test error');
        });
      });

      expect(result.current.error).toBe('test error');
      expect(result.current.loading).toBe(false);
    });

    it('returns undefined on failure', async () => {
      const { result } = renderHook(() => useAsyncOperation<string>());

      let returnValue: string | undefined = 'initial';
      await act(async () => {
        returnValue = await result.current.execute(async () => {
          throw new Error('test error');
        });
      });

      expect(returnValue).toBeUndefined();
    });

    it('handles non-Error objects', async () => {
      const { result } = renderHook(() => useAsyncOperation());

      await act(async () => {
        await result.current.execute(async () => {
          throw 'string error'; // Non-Error object
        });
      });

      expect(result.current.error).toBe('An unexpected error occurred');
    });

    it('sets loading false after error', async () => {
      const { result } = renderHook(() => useAsyncOperation());

      await act(async () => {
        await result.current.execute(async () => {
          throw new Error('error');
        });
      });

      expect(result.current.loading).toBe(false);
    });
  });

  describe('clearError', () => {
    it('clears the error state', async () => {
      const { result } = renderHook(() => useAsyncOperation());

      await act(async () => {
        await result.current.execute(async () => {
          throw new Error('test error');
        });
      });

      expect(result.current.error).toBe('test error');

      act(() => {
        result.current.clearError();
      });

      expect(result.current.error).toBeNull();
    });
  });

  describe('setError', () => {
    it('sets error manually', () => {
      const { result } = renderHook(() => useAsyncOperation());

      act(() => {
        result.current.setError('manual error');
      });

      expect(result.current.error).toBe('manual error');
    });

    it('clears error when set to null', async () => {
      const { result } = renderHook(() => useAsyncOperation());

      await act(async () => {
        await result.current.execute(async () => {
          throw new Error('test error');
        });
      });

      act(() => {
        result.current.setError(null);
      });

      expect(result.current.error).toBeNull();
    });
  });

  describe('with generic type', () => {
    it('works with different return types', async () => {
      const { result: stringResult } = renderHook(() => useAsyncOperation<string>());
      const { result: numberResult } = renderHook(() => useAsyncOperation<number>());
      const { result: objectResult } = renderHook(() => useAsyncOperation<{ name: string }>());

      let stringValue: string | undefined;
      let numberValue: number | undefined;
      let objectValue: { name: string } | undefined;

      await act(async () => {
        stringValue = await stringResult.current.execute(async () => 'hello');
        numberValue = await numberResult.current.execute(async () => 42);
        objectValue = await objectResult.current.execute(async () => ({ name: 'test' }));
      });

      expect(stringValue).toBe('hello');
      expect(numberValue).toBe(42);
      expect(objectValue).toEqual({ name: 'test' });
    });
  });

  describe('concurrent operations', () => {
    it('handles rapid successive calls', async () => {
      const { result } = renderHook(() => useAsyncOperation<number>());

      // Start multiple operations in quick succession
      let lastResult: number | undefined;
      await act(async () => {
        const p1 = result.current.execute(async () => {
          await new Promise(r => setTimeout(r, 10));
          return 1;
        });
        const p2 = result.current.execute(async () => {
          await new Promise(r => setTimeout(r, 5));
          return 2;
        });
        const p3 = result.current.execute(async () => {
          return 3;
        });

        const results = await Promise.all([p1, p2, p3]);
        lastResult = results[2]; // Last one to start
      });

      expect(lastResult).toBe(3);
      expect(result.current.loading).toBe(false);
    });
  });
});
