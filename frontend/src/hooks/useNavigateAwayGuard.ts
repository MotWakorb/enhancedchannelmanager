/**
 * Guard against navigating away / closing the tab while a critical operation
 * is in progress.
 *
 * Mirrors the existing `beforeunload` pattern in `App.tsx` (the
 * "unsaved changes" warning). A DBAS restore is a multi-minute, non-atomic job
 * whose compensating rollback depends on the operation COMPLETING — killing the
 * tab mid-flight (Dispatcharr has no DB transactions, ADR-012) can leave a
 * half-applied archive. While `active` is true we attach a `beforeunload`
 * handler so the browser shows its native "leave site?" confirmation; when the
 * op reaches a terminal state the caller flips `active` to false and the
 * handler is removed so a completed restore never nags the operator.
 *
 * This is the browser-level half of the guard. The in-app half (confirm-on-close
 * inside the modal) lives in the modal that consumes the restore surface — the
 * repo has no router-level `useBlocker`, so `beforeunload` + a modal-close
 * confirm is the established pattern.
 */
import { useEffect } from 'react';

/**
 * Attach a `beforeunload` guard while `active` is true.
 *
 * @param active - Whether the guard is currently armed (op in progress).
 */
export function useNavigateAwayGuard(active: boolean): void {
  useEffect(() => {
    if (!active) return;

    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      // Legacy browsers require a returnValue to trigger the native prompt;
      // the string itself is ignored by modern browsers (they show a generic
      // message), but setting it is what arms the dialog.
      e.returnValue = 'A restore is in progress. Leaving now may leave your data in a partial state.';
      return e.returnValue;
    };

    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => window.removeEventListener('beforeunload', handleBeforeUnload);
  }, [active]);
}
