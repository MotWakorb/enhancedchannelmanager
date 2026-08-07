import { useEffect, useRef } from 'react';

/**
 * Cross-component invalidation for server-derived state (bead
 * enhancedchannelmanager-5z7c9).
 *
 * ECM keeps server data in whichever component renders it, refetched on that
 * component's own mount. That works right up to the point where the component
 * performing a write is not the component displaying the result — and then the
 * display goes stale with no way to know it. Drill run 2026-08-06-run9 hit
 * three of these in one session (finding P-4): a saved Dispatcharr connection
 * the Settings summary card kept calling "Not configured", a logo uploaded in
 * Logo Manager that the Edit Channel picker could not offer, and an encrypted
 * backup absent from the Saved Backups list beside the card that created it.
 * Each needed a full page reload; in-app navigation was not always enough.
 *
 * This is the missing channel. A component that mutates something publishes
 * the affected key; components holding a copy of that data subscribe and
 * refetch. Deliberately NOT a cache and NOT a store — the holders keep owning
 * their own state, so wiring one panel changes nothing about any other.
 *
 * Two rules for using it:
 *
 * 1. **Publish on the mutation, not on render or on an interval.** There is no
 *    polling and no refetch-on-focus here, on purpose: those trade a cosmetic
 *    staleness bug for a standing load problem against Dispatcharr. Call
 *    {@link invalidateServerData} after the write succeeds, and not when it
 *    fails — a mutation that did not happen did not invalidate anything.
 * 2. **A component that mutates its OWN data should just refetch directly.**
 *    Publishing is for the copies you cannot see from where you are.
 */

/**
 * A body of server-derived data that more than one component mirrors.
 *
 * Keep this list short. A new key is a claim that two components hold the
 * same data and one of them can change it; if only one component reads it,
 * that component should refetch itself instead.
 */
export type ServerDataKey =
  /** `GET /api/settings` — the Settings tab's copy and App's copy. */
  | 'settings'
  /** The logo catalogue behind the Edit Channel picker (`App.logos`). */
  | 'logos'
  /** `GET /api/backup/saved` — the Saved Backups list. */
  | 'saved-backups';

type Subscriber = () => void;

const subscribers = new Map<ServerDataKey, Set<Subscriber>>();

/**
 * Announce that `key`'s server-side data has changed, so every component
 * holding a copy refetches it.
 *
 * Safe to call with nothing listening — a mutation must never fail because the
 * panel that would display it happens to be unmounted.
 */
export function invalidateServerData(key: ServerDataKey): void {
  const listeners = subscribers.get(key);
  if (!listeners) return;
  // Copy first: a subscriber is free to unmount (and unsubscribe) in response.
  for (const listener of [...listeners]) {
    listener();
  }
}

/**
 * Refetch `key` whenever another component reports having changed it.
 *
 * `reload` may be a fresh function on every render — the latest one is always
 * the one invoked, so it can close over current props and state without the
 * caller having to memoize it.
 */
export function useServerDataInvalidation(key: ServerDataKey, reload: () => void): void {
  const reloadRef = useRef(reload);

  useEffect(() => {
    reloadRef.current = reload;
  }, [reload]);

  useEffect(() => {
    const listener: Subscriber = () => reloadRef.current();
    let listeners = subscribers.get(key);
    if (!listeners) {
      listeners = new Set();
      subscribers.set(key, listeners);
    }
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
      if (listeners.size === 0) subscribers.delete(key);
    };
  }, [key]);
}
