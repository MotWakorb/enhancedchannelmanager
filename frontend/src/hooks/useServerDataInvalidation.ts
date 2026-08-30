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
  /**
   * The guide catalogue behind the Edit Channel "EPG Data" picker
   * (`App.epgData`, `GET /api/epg/data`).
   *
   * Published by EPG Manager when a source finishes downloading its guide.
   * Drill run 2026-08-08-run17 (bead enhancedchannelmanager-3vtim) added a
   * source, watched it reach `status=success`, and then got "No EPG data found"
   * for every channel — with ZERO `/api/epg/*` requests leaving the browser,
   * because `App.epgData` was still the empty array loaded before the source
   * existed. Only a full reload fixed it.
   */
  | 'epg-data'
  /**
   * The channel-group list (`App.channelGroups`, `GET /api/channel-groups`).
   *
   * Published by the DBAS restore modals: a restore creates and renames groups
   * that the Channel Manager's group filter is holding a pre-restore copy of.
   * The same drill reported "No groups match Drill17" for groups the restore
   * had just created.
   */
  | 'channel-groups'
  /**
   * The channel list itself (`App.channels`, `GET /api/channels/channels`).
   *
   * Same publisher as `channel-groups` and the same reason. Drill run
   * 2026-08-09-run18 applied a restore that reported "Channels 12 CREATED",
   * and with the restored group selected the Channel Manager still rendered
   * `CHANNELS 0` / "Empty" — the group FILTER had refreshed, the channels
   * behind it had not. Unlike the Streams pane there is no refresh control, so
   * the only cure was a full page reload, which signs the operator out (bead
   * enhancedchannelmanager-eelgi).
   */
  | 'channels'
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
