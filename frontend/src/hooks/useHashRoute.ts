import { useState, useCallback, useEffect, useRef } from 'react';
import type { TabId } from '../components/TabNavigation';
import { isSettingsPage, type SettingsPage } from '../components/settingsSections';

// The union and the set of routable settings ids used to be declared here, in
// parallel with `SETTINGS_SECTIONS`. Both are now derived from that single
// declaration (bead `enhancedchannelmanager-70u0r.7`); re-exported because most
// of the app imports the type from the router rather than from the registry.
export type { SettingsPage };

const VALID_TABS: Set<string> = new Set([
  'dashboard', 'm3u-manager', 'epg-manager', 'channel-manager', 'guide',
  'logo-manager', 'm3u-changes', 'channel-pipeline', 'journal',
  'stats', 'settings',
]);

/**
 * Legacy hash values from before the Auto-Creation → Channel Pipeline rename
 * (enhancedchannelmanager-3udrl phase 4). Bookmarked/shared URLs using the old
 * tab id or settings sub-page must keep resolving to the renamed destination
 * instead of silently falling back to the default tab.
 */
const LEGACY_TAB_ALIASES: Record<string, TabId> = {
  'auto-creation': 'channel-pipeline',
};

const LEGACY_SETTINGS_PAGE_ALIASES: Record<string, SettingsPage> = {
  'auto-creation': 'channel-pipeline',
  // Administration → "Security" page removed (bead 09x38.12): its one
  // setting (backup-destination SSRF allowlist) relocated to Backup &
  // Restore. Bookmarked/shared #settings/security URLs keep resolving there
  // instead of silently falling back to settings/general.
  security: 'backup-restore',
  // "Lookup Tables" page removed with the whole Lookup Tables feature (bead
  // 70u0r.1, PO decision D2 — the |lookup: pipe, CRUD router, model and table
  // went too). It has no successor page, so bookmarked #settings/lookup-tables
  // URLs land on General deliberately rather than hitting the silent
  // invalid-subpage fallback. Follows the `security` precedent above.
  'lookup-tables': 'general',
};

const DEFAULT_TAB: TabId = 'channel-manager';

/**
 * How long the router waits for the `popstate` a traversal it asked for was
 * supposed to produce, before deciding it is never coming.
 *
 * `window.history.go()` is a request, not an instruction: the browser may
 * clamp it to a no-op at the ends of the stack, coalesce it, or ignore it
 * outright, and every one of those cases fires nothing at all. Anything armed
 * in anticipation of that `popstate` — a rewind to chase, a guard bypass to
 * spend — has to come down on a timer rather than wait forever, or the NEXT
 * genuine Back inherits it (bead enhancedchannelmanager-6fi7p).
 */
const TRAVERSAL_SETTLE_MS = 1000;

/**
 * Corrective traversals the router will spend chasing the accepted entry
 * before it gives up and re-anchors where it actually is.
 *
 * A traversal the router asks for and an operator gesture land on the same
 * FIFO queue, so a rewind computed from one position can be executed from
 * another and overshoot. Recomputing from the position each `popstate`
 * reports converges, but nothing guarantees it converges QUICKLY against an
 * adversarial stack, and an unbounded chase is a worse failure than a
 * re-anchor: it holds the operator in a loop they cannot break.
 */
const MAX_RESTORE_TRAVERSALS = 3;

/**
 * A programmatic traversal the router has asked for and is waiting on.
 *
 * The point of the record — as against the two bare booleans this replaces —
 * is `expectedIndex`. A boolean says "a popstate I asked for is coming" and
 * therefore claims the next one that arrives, whoever caused it. An operator
 * pressing Back while a rewind is in flight produces exactly that popstate,
 * and consuming it silently moves the browser while the router's bookkeeping
 * still describes the entry it left.
 */
interface PendingTraversal {
  /**
   * `restore` rewinds a refused transition back to the accepted entry.
   * `resume` replays a transition the operator has since confirmed.
   */
  intent: 'restore' | 'resume';
  /** Route index the resulting `popstate` must land on to be the one we armed. */
  expectedIndex: number;
  /** Corrective traversals already spent chasing `expectedIndex`. */
  corrections: number;
  timer: ReturnType<typeof setTimeout> | null;
  /** `resume` only: carry the operator to the destination another way. */
  onUnfulfilled?: () => void;
}

/**
 * The router's own bookkeeping, carried on each history entry's state.
 *
 * `ecmRouteIndex` is an ordinal within a contiguous run of entries this router
 * numbered itself, which is what makes `go(delta)` addressable. `ecmRouteEpoch`
 * identifies the run. When the router lands on an entry it did not number — a
 * legacy entry, or one pushed by something else — it cannot know that entry's
 * ordinal, so it starts a NEW run anchored there instead of pretending the old
 * ordinals still describe the stack. Deltas are then refused across the
 * boundary rather than computed wrongly, and the callers already have the
 * fallback for a refused delta: navigate by hash.
 */
interface RouteHistoryState {
  ecmRouteIndex?: number;
  ecmRouteEpoch?: number;
  [key: string]: unknown;
}

const historyState = (): RouteHistoryState => (window.history.state ?? {}) as RouteHistoryState;

/**
 * The entry's ordinal, or `null` when this router cannot place it in the run
 * it is currently numbering. A missing epoch reads as run 0 so entries stamped
 * before epochs existed stay addressable.
 */
function placedRouteIndex(state: RouteHistoryState, epoch: number): number | null {
  if (typeof state.ecmRouteIndex !== 'number') return null;
  return (state.ecmRouteEpoch ?? 0) === epoch ? state.ecmRouteIndex : null;
}

interface HashRoute {
  tab: TabId;
  settingsPage: SettingsPage | null;
  m3uChangesHours?: number;
  section?: string;
}

function parseHash(hash: string): HashRoute {
  // Strip leading '#'
  const raw = hash.replace(/^#/, '');
  if (!raw) return { tab: DEFAULT_TAB, settingsPage: null };
  const [path, query = ''] = raw.split('?', 2);
  const hoursParam = new URLSearchParams(query).get('hours');
  const m3uChangesHours = hoursParam && ['24', '72', '168', '720', '2160'].includes(hoursParam)
    ? Number(hoursParam) : null;
  const sectionParam = new URLSearchParams(query).get('section');
  const section = sectionParam && /^[a-z0-9-]+$/.test(sectionParam) ? sectionParam : undefined;

  // Check for settings/sub-page format
  if (path.startsWith('settings/')) {
    const subPage = path.slice('settings/'.length);
    if (subPage in LEGACY_SETTINGS_PAGE_ALIASES) {
      return { tab: 'settings', settingsPage: LEGACY_SETTINGS_PAGE_ALIASES[subPage], ...(section ? { section } : {}) };
    }
    if (isSettingsPage(subPage)) {
      return { tab: 'settings', settingsPage: subPage, ...(section ? { section } : {}) };
    }
    // Invalid settings sub-page → fall back to settings/general
    return { tab: 'settings', settingsPage: null };
  }

  if (path === 'settings') {
    return { tab: 'settings', settingsPage: null, ...(section ? { section } : {}) };
  }

  if (path in LEGACY_TAB_ALIASES) {
    return { tab: LEGACY_TAB_ALIASES[path], settingsPage: null };
  }

  if (VALID_TABS.has(path)) {
    return path === 'm3u-changes' && m3uChangesHours
      ? { tab: path, settingsPage: null, m3uChangesHours }
      : { tab: path as TabId, settingsPage: null, ...(section ? { section } : {}) };
  }

  // Invalid hash → default
  return { tab: DEFAULT_TAB, settingsPage: null };
}

function buildHash(tab: TabId, settingsPage?: SettingsPage | null, m3uChangesHours?: number | null, section?: string): string {
  const query = section ? `?section=${encodeURIComponent(section)}` : '';
  if (tab === 'settings' && settingsPage && settingsPage !== 'general') {
    return `#settings/${settingsPage}${query}`;
  }
  if (tab === 'm3u-changes' && m3uChangesHours) return `#${tab}?hours=${m3uChangesHours}`;
  return `#${tab}${query}`;
}

/**
 * `detail` of the cancelable `ecm:before-route-change` event.
 *
 * A guard calls `event.preventDefault()` to refuse the navigation.
 *
 * `source` exists because the two callers need different treatment (bead
 * enhancedchannelmanager-6fi7p). Programmatic navigation (`push`) already
 * passes through `App`'s `handleRouteChange`, which consults the Edit Mode
 * guard BEFORE calling `setHash`; a second veto there would fight its own
 * resolution — including the `setHash` that carries the operator to the route
 * they just confirmed leaving Edit Mode for. Back/Forward (`pop`) never
 * touches `handleRouteChange` at all, and is the only reason a guard for it
 * has to live on this event. `SettingsTab`'s unsaved-form guard predates the
 * field and deliberately still guards both.
 */
export interface RouteChangeGuardDetail {
  /** Hash the app currently considers accepted. */
  from: string;
  /** Hash being navigated to. */
  to: string;
  source: 'push' | 'pop';
  /**
   * `pop` only: the `window.history.go()` argument that re-runs the very
   * navigation about to be vetoed, so a guard that defers to an operator can
   * later honour it as a real Back/Forward rather than a new pushState.
   * `null` when the router cannot place the target entry in the run of
   * entries it is currently numbering — no route index at all, or one from an
   * earlier run — and there is therefore no reliable delta.
   */
  historyDelta: number | null;
  /** Destination route, already parsed — guards decide on the tab, not the hash. */
  tab: TabId;
  settingsPage: SettingsPage | null;
}

export interface UseHashRouteReturn {
  activeTab: TabId;
  settingsPage: SettingsPage | null;
  m3uChangesHours: number | null;
  setHash: (tab: TabId, settingsPage?: SettingsPage | null) => void;
  setSettingsPage: (page: SettingsPage) => void;
  /**
   * Re-run a Back/Forward navigation this hook vetoed, once the guard that
   * vetoed it has been satisfied (bead enhancedchannelmanager-6fi7p).
   *
   * Takes the `historyDelta` from the guard event. The resulting `popstate`
   * skips the guard exactly once — the operator has already answered — so the
   * operator lands on the entry they asked for, with the forward/back entries
   * around it intact. A `pushState` to the same hash would instead leave them
   * on a NEW entry with the one they pressed Back from still behind them.
   *
   * The bypass is spent on the entry the replay was AIMED at and on no other:
   * a `popstate` from anywhere else is a transition the operator has not
   * answered for, and putting it through unguarded is the silent Edit Mode
   * exit this whole mechanism exists to prevent.
   *
   * `onUnfulfilled` runs when the browser produces no `popstate` at all —
   * a refused or clamped traversal. The operator asked to go somewhere and
   * answered for it, so swallowing that is not an option; the caller passes
   * the hash-based route to the same destination.
   */
  resumeRejectedNavigation: (historyDelta: number, onUnfulfilled?: () => void) => void;
}

export function useHashRoute(): UseHashRouteReturn {
  const [route, setRoute] = useState<HashRoute>(() => parseHash(window.location.hash));
  const routeRef = useRef(route);
  const acceptedHashRef = useRef(window.location.hash);
  const routeEpochRef = useRef<number>(historyState().ecmRouteEpoch ?? 0);
  const acceptedHistoryIndexRef = useRef<number>(
    placedRouteIndex(historyState(), routeEpochRef.current) ?? 0,
  );
  const pendingTraversalRef = useRef<PendingTraversal | null>(null);

  const canNavigate = useCallback((
    detail: Omit<RouteChangeGuardDetail, 'from'>,
  ) => window.dispatchEvent(new CustomEvent<RouteChangeGuardDetail>('ecm:before-route-change', {
    cancelable: true,
    detail: { from: acceptedHashRef.current, ...detail },
  })), []);

  const disarmTraversal = useCallback(() => {
    const pending = pendingTraversalRef.current;
    if (!pending) return;
    if (pending.timer !== null) clearTimeout(pending.timer);
    pendingTraversalRef.current = null;
  }, []);

  /**
   * Make the entry the operator is ACTUALLY on the accepted entry.
   *
   * The router is authoritative about which ROUTE is accepted; the browser is
   * authoritative about which ENTRY the operator is on. When a traversal
   * cannot reconcile the two — no addressable delta, or a rewind that will not
   * converge — the only honest move is to stop claiming a slot the browser has
   * left and stamp the accepted route onto the slot it is on, so the two agree
   * by construction rather than by hope.
   *
   * Landing somewhere the router cannot place also means its ordinals no
   * longer describe this stack, so it starts a new numbering run here. Deltas
   * against entries from earlier runs are then refused rather than computed
   * wrongly — see {@link placedRouteIndex}.
   */
  const anchorAcceptedRouteHere = useCallback(() => {
    const state = historyState();
    const placed = placedRouteIndex(state, routeEpochRef.current);
    const onAcceptedEntry = placed !== null && placed === acceptedHistoryIndexRef.current;
    if (onAcceptedEntry && window.location.hash === acceptedHashRef.current) return;
    if (!onAcceptedEntry) {
      routeEpochRef.current += 1;
      acceptedHistoryIndexRef.current = 0;
    }
    window.history.replaceState(
      {
        ...state,
        ecmRouteIndex: acceptedHistoryIndexRef.current,
        ecmRouteEpoch: routeEpochRef.current,
      },
      '',
      acceptedHashRef.current,
    );
  }, []);

  const settleUnfulfilledTraversal = useCallback((traversal: PendingTraversal) => {
    if (traversal.intent === 'restore') anchorAcceptedRouteHere();
    else traversal.onUnfulfilled?.();
  }, [anchorAcceptedRouteHere]);

  /**
   * Ask the browser for a traversal and record what it has to produce for the
   * router to treat it as answered. Nothing here can leave a flag armed: the
   * traversal either lands on `expectedIndex`, or it is taken down when the
   * browser refuses it, or the settle timer takes it down.
   */
  const armTraversal = useCallback((
    traversal: Omit<PendingTraversal, 'timer'>,
    run: () => void,
  ) => {
    disarmTraversal();
    const armed: PendingTraversal = { ...traversal, timer: null };
    pendingTraversalRef.current = armed;
    try {
      run();
    } catch {
      // The browser refused outright, so its popstate is definitively not
      // coming. Finish the traversal here rather than letting the caller's
      // failure leave the router waiting on an event that cannot arrive.
      if (pendingTraversalRef.current === armed) pendingTraversalRef.current = null;
      settleUnfulfilledTraversal(armed);
      return;
    }
    armed.timer = setTimeout(() => {
      if (pendingTraversalRef.current !== armed) return;
      pendingTraversalRef.current = null;
      settleUnfulfilledTraversal(armed);
    }, TRAVERSAL_SETTLE_MS);
  }, [disarmTraversal, settleUnfulfilledTraversal]);

  const resumeRejectedNavigation = useCallback((historyDelta: number, onUnfulfilled?: () => void) => {
    // `history.go(0)` reloads in some browsers and fires no popstate in
    // others, and there is nothing to resume at zero anyway — so hand the
    // navigation straight to the caller's fallback.
    if (!historyDelta) {
      onUnfulfilled?.();
      return;
    }
    armTraversal(
      {
        intent: 'resume',
        expectedIndex: acceptedHistoryIndexRef.current + historyDelta,
        corrections: 0,
        onUnfulfilled,
      },
      () => window.history.go(historyDelta),
    );
  }, [armTraversal]);

  // Bail out when the route is unchanged so a caller that loops can't churn pushState + a fresh-object re-render. Uses pushState (not assign) to avoid a hashchange/popstate echo.
  const setHash = useCallback((tab: TabId, settingsPage?: SettingsPage | null) => {
    const nextSettingsPage = settingsPage ?? null;
    if (routeRef.current.tab === tab && routeRef.current.settingsPage === nextSettingsPage) {
      return;
    }
    const nextHash = buildHash(tab, settingsPage);
    if (!canNavigate({
      to: nextHash, source: 'push', historyDelta: null, tab, settingsPage: nextSettingsPage,
    })) return;
    const nextHistoryIndex = acceptedHistoryIndexRef.current + 1;
    window.history.pushState(
      { ...historyState(), ecmRouteIndex: nextHistoryIndex, ecmRouteEpoch: routeEpochRef.current },
      '',
      nextHash,
    );
    acceptedHistoryIndexRef.current = nextHistoryIndex;
    acceptedHashRef.current = nextHash;
    const nextRoute = { tab, settingsPage: nextSettingsPage };
    routeRef.current = nextRoute;
    setRoute(nextRoute);
  }, [canNavigate]);

  // Update just the settings sub-page
  const setSettingsPage = useCallback((page: SettingsPage) => {
    setHash('settings', page);
  }, [setHash]);

  // Listen for popstate (back/forward buttons)
  useEffect(() => {
    const canonicalizeCurrentHash = () => {
      const parsed = parseHash(window.location.hash);
      const canonicalHash = buildHash(parsed.tab, parsed.settingsPage, parsed.m3uChangesHours, parsed.section);
      if (window.location.hash !== canonicalHash) {
        window.history.replaceState(window.history.state, '', canonicalHash);
      }
      return parsed;
    };

    const acceptCurrentEntry = () => {
      const nextRoute = canonicalizeCurrentHash();
      acceptedHashRef.current = window.location.hash;
      const placed = placedRouteIndex(historyState(), routeEpochRef.current);
      if (placed !== null) {
        acceptedHistoryIndexRef.current = placed;
      } else {
        // Accepting an entry this router never numbered. Keeping the index it
        // was carrying would leave the bookkeeping describing the entry the
        // operator LEFT, so number this one and start a new run from it.
        routeEpochRef.current += 1;
        acceptedHistoryIndexRef.current = 0;
        window.history.replaceState(
          { ...historyState(), ecmRouteIndex: 0, ecmRouteEpoch: routeEpochRef.current },
          '',
          window.location.hash,
        );
      }
      routeRef.current = nextRoute;
      setRoute(nextRoute);
    };

    /**
     * Put the operator back on the accepted entry after a refused transition.
     *
     * Recomputed from the position each `popstate` actually reports rather
     * than from the delta the refusal was raised with, because a traversal the
     * router asked for and one the operator asked for share a queue: the
     * rewind can be executed from a slot the operator has since moved off, and
     * a delta computed against the old slot lands somewhere else again. When
     * the entry cannot be placed at all, or the chase will not converge inside
     * its budget, re-anchor rather than keep traversing.
     */
    const restoreAcceptedEntry = (currentIndex: number | null, corrections: number) => {
      const acceptedIndex = acceptedHistoryIndexRef.current;
      if (currentIndex === null
        || currentIndex === acceptedIndex
        || corrections >= MAX_RESTORE_TRAVERSALS) {
        disarmTraversal();
        anchorAcceptedRouteHere();
        return;
      }
      armTraversal(
        { intent: 'restore', expectedIndex: acceptedIndex, corrections: corrections + 1 },
        () => window.history.go(acceptedIndex - currentIndex),
      );
    };

    const handlePopState = () => {
      const currentIndex = placedRouteIndex(historyState(), routeEpochRef.current);
      const pending = pendingTraversalRef.current;

      if (pending) {
        if (currentIndex !== null && currentIndex === pending.expectedIndex) {
          disarmTraversal();
          if (pending.intent === 'restore') {
            // Back where the refusal started. Nothing to accept, but the entry
            // still has to say what the router says it says.
            anchorAcceptedRouteHere();
            return;
          }
          // `resume`: this exact transition was put to the operator and
          // answered, so putting it to the guard again would ask twice.
          acceptCurrentEntry();
          return;
        }
        if (pending.intent === 'restore') {
          // Not the rewind we armed — the operator moved again while it was in
          // flight. The refusal stands and their dialog is still open, so this
          // is not a second question to ask; it is a position to correct.
          restoreAcceptedEntry(currentIndex, pending.corrections);
          return;
        }
        // A `resume` bypass is armed for one entry and this is not it. Take it
        // down: a transition the operator has not answered for must never
        // inherit an answer they gave about a different one.
        disarmTraversal();
      } else if (currentIndex !== null
        && currentIndex === acceptedHistoryIndexRef.current
        && window.location.hash === acceptedHashRef.current) {
        // Landed on the entry the router already considers accepted, showing
        // the route it already considers accepted. There is no transition here
        // to put to a guard.
        return;
      }

      const requestedHash = window.location.hash;
      const historyDelta = currentIndex === null
        ? null
        : currentIndex - acceptedHistoryIndexRef.current;
      const requested = parseHash(requestedHash);
      if (!canNavigate({
        to: requestedHash,
        source: 'pop',
        historyDelta,
        tab: requested.tab,
        settingsPage: requested.settingsPage,
      })) {
        restoreAcceptedEntry(currentIndex, 0);
        return;
      }
      acceptCurrentEntry();
    };

    const initialRoute = canonicalizeCurrentHash();
    if (placedRouteIndex(historyState(), routeEpochRef.current) === null) {
      window.history.replaceState(
        {
          ...historyState(),
          ecmRouteIndex: acceptedHistoryIndexRef.current,
          ecmRouteEpoch: routeEpochRef.current,
        },
        '',
        window.location.hash,
      );
    }
    acceptedHashRef.current = window.location.hash;
    routeRef.current = initialRoute;
    setRoute(initialRoute);
    window.addEventListener('popstate', handlePopState);
    const handleRouteReplaced = () => {
      acceptedHashRef.current = window.location.hash;
    };
    window.addEventListener('ecm:route-replaced', handleRouteReplaced);
    return () => {
      window.removeEventListener('popstate', handlePopState);
      window.removeEventListener('ecm:route-replaced', handleRouteReplaced);
      // The settle timer outlives the listener it exists to protect, and its
      // callback touches history. Nothing armed may survive the unmount.
      disarmTraversal();
    };
  }, [canNavigate, disarmTraversal, armTraversal, anchorAcceptedRouteHere]);

  return {
    activeTab: route.tab,
    settingsPage: route.settingsPage,
    m3uChangesHours: route.m3uChangesHours ?? null,
    setHash,
    setSettingsPage,
    resumeRejectedNavigation,
  };
}

// Export for testing
export { parseHash as _parseHash, buildHash as _buildHash };
