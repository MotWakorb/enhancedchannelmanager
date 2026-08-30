import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useHashRoute, _parseHash, _buildHash, type RouteChangeGuardDetail } from './useHashRoute';
import { SETTINGS_PAGE_IDS } from '../components/settingsSections';

describe('parseHash', () => {
  it('preserves supported section deep links on audited long pages', () => {
    expect(_parseHash('#stats?section=stats-section-watch-history')).toEqual({
      tab: 'stats', settingsPage: null, section: 'stats-section-watch-history',
    });
    expect(_buildHash('settings', 'integrations', null, 'settings-integrations-section-plex'))
      .toBe('#settings/integrations?section=settings-integrations-section-plex');
  });
  it('preserves the supported M3U changes time window', () => {
    expect(_parseHash('#m3u-changes?hours=24')).toEqual({
      tab: 'm3u-changes', settingsPage: null, m3uChangesHours: 24,
    });
    expect(_buildHash('m3u-changes', null, 24)).toBe('#m3u-changes?hours=24');
  });
  it('returns default tab for empty hash', () => {
    expect(_parseHash('')).toEqual({ tab: 'channel-manager', settingsPage: null });
  });

  it('returns default tab for just #', () => {
    expect(_parseHash('#')).toEqual({ tab: 'channel-manager', settingsPage: null });
  });

  it('parses valid tab hashes', () => {
    expect(_parseHash('#dashboard')).toEqual({ tab: 'dashboard', settingsPage: null });
    expect(_parseHash('#m3u-manager')).toEqual({ tab: 'm3u-manager', settingsPage: null });
    expect(_parseHash('#epg-manager')).toEqual({ tab: 'epg-manager', settingsPage: null });
    expect(_parseHash('#channel-manager')).toEqual({ tab: 'channel-manager', settingsPage: null });
    expect(_parseHash('#guide')).toEqual({ tab: 'guide', settingsPage: null });
    expect(_parseHash('#logo-manager')).toEqual({ tab: 'logo-manager', settingsPage: null });
    expect(_parseHash('#channel-pipeline')).toEqual({ tab: 'channel-pipeline', settingsPage: null });
    expect(_parseHash('#journal')).toEqual({ tab: 'journal', settingsPage: null });
    expect(_parseHash('#stats')).toEqual({ tab: 'stats', settingsPage: null });
    expect(_parseHash('#settings')).toEqual({ tab: 'settings', settingsPage: null });
  });

  it('parses settings sub-pages', () => {
    expect(_parseHash('#settings/normalization')).toEqual({ tab: 'settings', settingsPage: 'normalization' });
    expect(_parseHash('#settings/channel-defaults')).toEqual({ tab: 'settings', settingsPage: 'channel-defaults' });
    expect(_parseHash('#settings/email')).toEqual({ tab: 'settings', settingsPage: 'email' });
    expect(_parseHash('#settings/scheduled-tasks')).toEqual({ tab: 'settings', settingsPage: 'scheduled-tasks' });
    expect(_parseHash('#settings/auth-settings')).toEqual({ tab: 'settings', settingsPage: 'auth-settings' });
    expect(_parseHash('#settings/tls-settings')).toEqual({ tab: 'settings', settingsPage: 'tls-settings' });
  });

  it('returns default for invalid hash', () => {
    expect(_parseHash('#bogus')).toEqual({ tab: 'channel-manager', settingsPage: null });
    expect(_parseHash('#not-a-tab')).toEqual({ tab: 'channel-manager', settingsPage: null });
  });

  it('returns settings with null page for invalid settings sub-page', () => {
    expect(_parseHash('#settings/invalid-page')).toEqual({ tab: 'settings', settingsPage: null });
  });

  // Bead enhancedchannelmanager-70u0r.7 made SETTINGS_SECTIONS the only place a
  // settings id is written down; the router derives its admission check from it.
  // Every declared destination must therefore be reachable by its own hash — a
  // sidebar entry that falls through to the invalid-sub-page branch would land
  // the operator on General with no error, which is the silent failure the
  // previous three parallel lists could produce.
  it('routes every declared settings destination by its own hash', () => {
    for (const id of SETTINGS_PAGE_IDS) {
      expect(_parseHash(`#settings/${id}`), `#settings/${id}`)
        .toEqual({ tab: 'settings', settingsPage: id });
    }
  });
});

describe('legacy hash aliases (Auto-Creation -> Channel Pipeline rename)', () => {
  it('resolves the old top-level auto-creation hash to channel-pipeline', () => {
    expect(_parseHash('#auto-creation')).toEqual({ tab: 'channel-pipeline', settingsPage: null });
  });

  it('resolves the old settings/auto-creation sub-page hash to settings/channel-pipeline', () => {
    expect(_parseHash('#settings/auto-creation')).toEqual({ tab: 'settings', settingsPage: 'channel-pipeline' });
  });
});

describe('legacy hash alias (Security page removal, bead 09x38.12)', () => {
  it('resolves the old settings/security sub-page hash to settings/backup-restore', () => {
    expect(_parseHash('#settings/security')).toEqual({ tab: 'settings', settingsPage: 'backup-restore' });
  });
});

describe('legacy hash alias (Lookup Tables removal, bead 70u0r.1)', () => {
  // The feature was removed outright, so there is no successor page. The alias
  // exists so a bookmarked URL resolves to General EXPLICITLY rather than via
  // the invalid-subpage fallback, which returns settingsPage: null.
  it('resolves the retired settings/lookup-tables hash to settings/general', () => {
    expect(_parseHash('#settings/lookup-tables')).toEqual({ tab: 'settings', settingsPage: 'general' });
  });

  it('is a real alias, not the invalid-subpage fallback', () => {
    expect(_parseHash('#settings/no-such-page')).toEqual({ tab: 'settings', settingsPage: null });
  });
});

describe('buildHash', () => {
  it('builds simple tab hashes', () => {
    expect(_buildHash('dashboard')).toBe('#dashboard');
    expect(_buildHash('channel-manager')).toBe('#channel-manager');
    expect(_buildHash('m3u-manager')).toBe('#m3u-manager');
    expect(_buildHash('settings')).toBe('#settings');
  });

  it('builds settings sub-page hashes', () => {
    expect(_buildHash('settings', 'normalization')).toBe('#settings/normalization');
    expect(_buildHash('settings', 'email')).toBe('#settings/email');
  });

  it('omits general sub-page (default)', () => {
    expect(_buildHash('settings', 'general')).toBe('#settings');
    expect(_buildHash('settings', null)).toBe('#settings');
  });
});

describe('useHashRoute', () => {
  let pushStateSpy: ReturnType<typeof vi.spyOn>;
  let replaceStateSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    window.history.replaceState(null, '', '#');
    pushStateSpy = vi.spyOn(window.history, 'pushState');
    replaceStateSpy = vi.spyOn(window.history, 'replaceState');
  });

  afterEach(() => {
    pushStateSpy.mockRestore();
    replaceStateSpy.mockRestore();
  });

  it('defaults to channel-manager with no hash', () => {
    const { result } = renderHook(() => useHashRoute());
    expect(result.current.activeTab).toBe('channel-manager');
    expect(result.current.settingsPage).toBeNull();
  });

  it('reads initial hash on mount', () => {
    window.location.hash = '#m3u-manager';
    const { result } = renderHook(() => useHashRoute());
    expect(result.current.activeTab).toBe('m3u-manager');
  });

  it('reads settings sub-page from initial hash', () => {
    window.location.hash = '#settings/normalization';
    const { result } = renderHook(() => useHashRoute());
    expect(result.current.activeTab).toBe('settings');
    expect(result.current.settingsPage).toBe('normalization');
  });

  it('setHash updates tab and calls pushState', () => {
    const { result } = renderHook(() => useHashRoute());

    act(() => {
      result.current.setHash('epg-manager');
    });

    expect(result.current.activeTab).toBe('epg-manager');
    expect(pushStateSpy).toHaveBeenCalledWith(expect.objectContaining({ ecmRouteIndex: 1 }), '', '#epg-manager');
  });

  it('keeps setHash stable while observing the latest route', () => {
    const { result } = renderHook(() => useHashRoute());
    const initialSetHash = result.current.setHash;
    act(() => result.current.setHash('guide'));
    expect(result.current.setHash).toBe(initialSetHash);
    act(() => result.current.setHash('guide'));
    expect(pushStateSpy).toHaveBeenCalledTimes(1);
  });

  it('setHash with settings page', () => {
    const { result } = renderHook(() => useHashRoute());

    act(() => {
      result.current.setHash('settings', 'normalization');
    });

    expect(result.current.activeTab).toBe('settings');
    expect(result.current.settingsPage).toBe('normalization');
    expect(pushStateSpy).toHaveBeenCalledWith(expect.objectContaining({ ecmRouteIndex: 1 }), '', '#settings/normalization');
  });

  it('setSettingsPage updates settings sub-page', () => {
    window.location.hash = '#settings';
    const { result } = renderHook(() => useHashRoute());

    act(() => {
      result.current.setSettingsPage('email');
    });

    expect(result.current.activeTab).toBe('settings');
    expect(result.current.settingsPage).toBe('email');
    expect(pushStateSpy).toHaveBeenCalledWith(expect.objectContaining({ ecmRouteIndex: 1 }), '', '#settings/email');
  });

  it('responds to popstate events', () => {
    const { result } = renderHook(() => useHashRoute());

    // Simulate browser back to a different hash
    act(() => {
      window.location.hash = '#stats';
      window.dispatchEvent(new PopStateEvent('popstate'));
    });

    expect(result.current.activeTab).toBe('stats');
  });

  it('keeps route state and URL aligned when a history transition is rejected', () => {
    window.location.hash = '#settings';
    const reject = (event: Event) => event.preventDefault();
    window.addEventListener('ecm:before-route-change', reject);
    const { result } = renderHook(() => useHashRoute());

    act(() => {
      window.location.hash = '#stats';
      window.dispatchEvent(new PopStateEvent('popstate'));
    });

    expect(result.current.activeTab).toBe('settings');
    expect(window.location.hash).toBe('#settings');
    window.removeEventListener('ecm:before-route-change', reject);
  });

  /**
   * bead enhancedchannelmanager-6fi7p. The router always asked permission
   * before a Back/Forward, and the ONE production listener for that question
   * was SettingsTab's unsaved-form guard — so Channel Manager's Edit Mode
   * guard, which lives in App, never heard it. App could not have distinguished
   * a Back from its own setHash even if it had listened, and it has to: it
   * resolves programmatic navigation itself, before calling setHash, and
   * guarding that again would veto the operator's own confirmed exit.
   */
  describe('guard event detail', () => {
    function captureGuardDetails() {
      const seen: unknown[] = [];
      const listener = (event: Event) => seen.push((event as CustomEvent).detail);
      window.addEventListener('ecm:before-route-change', listener);
      return {
        seen,
        stop: () => window.removeEventListener('ecm:before-route-change', listener),
      };
    }

    it('marks a Back/Forward as pop, parses the destination, and says how to re-run it', () => {
      const { result } = renderHook(() => useHashRoute());
      act(() => result.current.setHash('stats'));
      const captured = captureGuardDetails();

      act(() => {
        // A real Back lands on an earlier entry, which carries that entry's
        // own route index. Written directly so the delta is deterministic.
        window.history.replaceState({ ecmRouteIndex: 0 }, '', '#settings/normalization');
        window.dispatchEvent(new PopStateEvent('popstate'));
      });
      captured.stop();

      expect(captured.seen).toEqual([
        expect.objectContaining({
          source: 'pop',
          to: '#settings/normalization',
          tab: 'settings',
          settingsPage: 'normalization',
          historyDelta: -1,
        }),
      ]);
    });

    it('marks a setHash navigation as push, with no delta to re-run', () => {
      const { result } = renderHook(() => useHashRoute());
      const captured = captureGuardDetails();

      act(() => result.current.setHash('settings', 'email'));
      captured.stop();

      expect(captured.seen).toEqual([
        expect.objectContaining({
          source: 'push',
          to: '#settings/email',
          tab: 'settings',
          settingsPage: 'email',
          historyDelta: null,
        }),
      ]);
    });

    it('reports no delta when the target entry carries no route index', () => {
      const { result } = renderHook(() => useHashRoute());
      act(() => result.current.setHash('stats'));
      const captured = captureGuardDetails();

      act(() => {
        window.history.replaceState({}, '', '#guide');
        window.dispatchEvent(new PopStateEvent('popstate'));
      });
      captured.stop();

      expect(captured.seen).toEqual([expect.objectContaining({ historyDelta: null })]);
    });
  });

  describe('resumeRejectedNavigation (bead enhancedchannelmanager-6fi7p)', () => {
    // The happy path — replay the confirmed transition, skip the guard for
    // exactly that popstate, re-arm for the next — is exercised against a real
    // session-history stack in "history bookkeeping under interleaved
    // traversals" at the bottom of this file. It used to live here, driving a
    // hand-written popstate whose route index did not match the entry the
    // replay was aimed at, which is precisely the confusion the destination
    // token now refuses.

    it('arms nothing on a zero delta, which has no transition to replay', () => {
      const goSpy = vi.spyOn(window.history, 'go').mockImplementation(() => {});
      const guard = vi.fn((event: Event) => event.preventDefault());
      window.addEventListener('ecm:before-route-change', guard);
      const { result } = renderHook(() => useHashRoute());

      act(() => result.current.resumeRejectedNavigation(0));
      expect(goSpy).not.toHaveBeenCalled();

      act(() => {
        window.history.replaceState({ ecmRouteIndex: 0 }, '', '#stats');
        window.dispatchEvent(new PopStateEvent('popstate'));
      });
      expect(guard).toHaveBeenCalledTimes(1);

      window.removeEventListener('ecm:before-route-change', guard);
      goSpy.mockRestore();
    });
  });

  it('sets initial hash via replaceState if none present', () => {
    window.location.hash = '';
    renderHook(() => useHashRoute());
    expect(replaceStateSpy).toHaveBeenCalledWith(expect.anything(), '', '#channel-manager');
  });

  it('adds route history metadata without changing an existing valid hash', () => {
    window.location.hash = '#guide';
    renderHook(() => useHashRoute());
    expect(window.location.hash).toBe('#guide');
    expect(window.history.state).toEqual(expect.objectContaining({ ecmRouteIndex: 0 }));
  });

  it('canonicalizes the legacy top-level alias with replaceState', () => {
    window.location.hash = '#auto-creation';
    const { result } = renderHook(() => useHashRoute());
    expect(result.current.activeTab).toBe('channel-pipeline');
    expect(replaceStateSpy).toHaveBeenCalledWith(expect.anything(), '', '#channel-pipeline');
    expect(pushStateSpy).not.toHaveBeenCalled();
  });

  it('canonicalizes settings aliases and invalid routes without adding history', () => {
    window.location.hash = '#settings/auto-creation';
    renderHook(() => useHashRoute());
    expect(replaceStateSpy).toHaveBeenCalledWith(expect.anything(), '', '#settings/channel-pipeline');
    expect(pushStateSpy).not.toHaveBeenCalled();
  });
});

interface SessionEntry {
  hash: string;
  state: Record<string, unknown> | null;
}

/**
 * A session history stack with the browser's traversal queue in front of it.
 *
 * `history.go()` does not move the browser synchronously: it enqueues a
 * traversal, and the resulting `popstate` arrives later. An operator gesture
 * enqueues on the SAME queue, so a Back pressed while the router's own rewind
 * is still in flight is not a hypothetical interleaving — it is the ordinary
 * consequence of two producers and one FIFO consumer. Modelling that queue is
 * what makes the tests below drive the real ordering rather than assert on a
 * hand-picked sequence of popstate events: `press()` and the hook's own
 * `history.go()` both enqueue, and `flush()` drains in arrival order, running
 * the hook's reaction to each popstate before the next traversal is taken.
 */
function installSessionHistory(entries: SessionEntry[], startIndex: number) {
  const realReplaceState = window.history.replaceState.bind(window.history);
  const stack: SessionEntry[] = entries.map((entry) => ({ ...entry }));
  const queue: number[] = [];
  let index = startIndex;
  const sync = () => realReplaceState(stack[index].state, '', stack[index].hash);
  sync();

  const pushSpy = vi.spyOn(window.history, 'pushState').mockImplementation((state, _title, url) => {
    stack.splice(index + 1);
    stack.push({ hash: String(url), state: state as Record<string, unknown> | null });
    index = stack.length - 1;
    sync();
  });
  const replaceSpy = vi.spyOn(window.history, 'replaceState').mockImplementation((state, _title, url) => {
    stack[index] = {
      hash: url === undefined || url === null ? stack[index].hash : String(url),
      state: state as Record<string, unknown> | null,
    };
    sync();
  });
  const goSpy = vi.spyOn(window.history, 'go').mockImplementation((delta) => {
    queue.push(delta ?? 0);
  });

  return {
    /** An operator pressing Back or Forward — same queue as `history.go()`. */
    press: (delta: number) => { queue.push(delta); },
    /** The browser accepting a traversal and then never running it. */
    swallowNextGo: () => { goSpy.mockImplementationOnce(() => {}); },
    /** The browser refusing a traversal outright. */
    throwOnNextGo: () => {
      goSpy.mockImplementationOnce(() => { throw new Error('traversal refused'); });
    },
    flush: () => {
      let steps = 0;
      while (queue.length > 0) {
        if (++steps > 60) throw new Error('session history never settled');
        const delta = queue.shift() as number;
        const target = Math.min(stack.length - 1, Math.max(0, index + delta));
        // A traversal that moves nothing fires no popstate, which is exactly
        // how a flag armed in anticipation of one gets stranded.
        if (target === index) continue;
        index = target;
        sync();
        window.dispatchEvent(new PopStateEvent('popstate'));
      }
    },
    get position() { return index; },
    get currentEntry() { return stack[index]; },
    get depth() { return stack.length; },
    restore: () => {
      pushSpy.mockRestore();
      replaceSpy.mockRestore();
      goSpy.mockRestore();
    },
  };
}

/** Four entries this router numbered itself, operator sitting on the last. */
const ROUTER_NUMBERED: SessionEntry[] = [
  { hash: '#channel-manager', state: { ecmRouteIndex: 0 } },
  { hash: '#guide', state: { ecmRouteIndex: 1 } },
  { hash: '#journal', state: { ecmRouteIndex: 2 } },
  { hash: '#stats', state: { ecmRouteIndex: 3 } },
];

/** The middle entry carries no route index — legacy, or pushed by something else. */
const WITH_UNNUMBERED_ENTRY: SessionEntry[] = [
  { hash: '#channel-manager', state: { ecmRouteIndex: 0 } },
  { hash: '#guide', state: {} },
  { hash: '#stats', state: { ecmRouteIndex: 2 } },
];

/**
 * Bookkeeping under interleaved traversals — the fix round on the Back/Forward
 * exit guard shipped in `cfefeaed` (bead enhancedchannelmanager-6fi7p).
 *
 * The acceptance criteria here are properties, not the reproductions that
 * found them:
 *
 *  1. After ANY sequence of Back/Forward, veto, dialog answer and programmatic
 *     navigation, the accepted hash and route index describe the entry the
 *     browser is actually on.
 *  2. No navigation with staged work behind it passes the guard without an
 *     operator decision, on any interleaving.
 *  3. A flag armed in anticipation of a popstate is disarmed whether or not
 *     that popstate ever arrives.
 *  4. The operator can always still navigate.
 */
describe('useHashRoute history bookkeeping under interleaved traversals', () => {
  let session: ReturnType<typeof installSessionHistory> | null = null;
  const refuseEverything = (event: Event) => event.preventDefault();

  afterEach(() => {
    window.removeEventListener('ecm:before-route-change', refuseEverything);
    session?.restore();
    session = null;
    vi.useRealTimers();
  });

  function stageEdits() {
    window.addEventListener('ecm:before-route-change', refuseEverything);
  }

  function operatorLeftEditMode() {
    window.removeEventListener('ecm:before-route-change', refuseEverything);
  }

  function watchGuard() {
    const seen: RouteChangeGuardDetail[] = [];
    const listener = (event: Event) => seen.push((event as CustomEvent<RouteChangeGuardDetail>).detail);
    window.addEventListener('ecm:before-route-change', listener);
    return {
      seen,
      clear: () => { seen.length = 0; },
      stop: () => window.removeEventListener('ecm:before-route-change', listener),
    };
  }

  it('holds the accepted entry when a Back is refused', () => {
    session = installSessionHistory(ROUTER_NUMBERED, 3);
    stageEdits();
    const { result } = renderHook(() => useHashRoute());

    act(() => { session!.press(-1); session!.flush(); });

    expect(result.current.activeTab).toBe('stats');
    expect(window.location.hash).toBe('#stats');
    expect(session.position).toBe(3);
  });

  it('holds the accepted entry when a second Back arrives before the rewind', () => {
    session = installSessionHistory(ROUTER_NUMBERED, 3);
    stageEdits();
    const { result } = renderHook(() => useHashRoute());

    act(() => { session!.press(-1); session!.press(-1); session!.flush(); });

    expect(result.current.activeTab).toBe('stats');
    expect(window.location.hash).toBe('#stats');
    expect(session.position).toBe(3);
  });

  it('holds the accepted entry when a Forward arrives before the rewind', () => {
    session = installSessionHistory(ROUTER_NUMBERED, 2);
    stageEdits();
    const { result } = renderHook(() => useHashRoute());

    act(() => { session!.press(-1); session!.press(1); session!.flush(); });

    expect(result.current.activeTab).toBe('journal');
    expect(window.location.hash).toBe('#journal');
    expect(session.position).toBe(2);
  });

  /**
   * The Finding-1 case. A rewind is armed, the operator presses Back again
   * before it lands, and the boolean this used to be consumed whatever popstate
   * came next as "mine". The damage is not the momentary confusion — the router
   * re-vetoes and stumbles back — it is that the last rewind can end up being a
   * traversal that moves nothing, fires no popstate, and leaves the flag armed
   * forever. From then on the router silently swallows every Back: no guard
   * question, no route change, and a URL that no longer matches the page.
   */
  it('does not go deaf to the next Back after a burst of refused ones', () => {
    session = installSessionHistory(ROUTER_NUMBERED, 3);
    stageEdits();
    const { result } = renderHook(() => useHashRoute());

    act(() => {
      session!.press(-1);
      session!.press(-1);
      session!.press(-1);
      session!.flush();
    });
    expect(result.current.activeTab).toBe('stats');
    expect(window.location.hash).toBe('#stats');

    const guard = watchGuard();
    act(() => { session!.press(-1); session!.flush(); });
    guard.stop();

    expect(guard.seen).not.toHaveLength(0);
    expect(result.current.activeTab).toBe('stats');
    expect(window.location.hash).toBe('#stats');
  });

  /**
   * The Finding-2 case. The refused entry carries no route index, so there is
   * no delta that addresses the accepted entry and the router cannot traverse
   * back to it. Rewriting the URL in place is the right screen, but it leaves
   * the browser one slot behind bookkeeping that still claims the old slot.
   * The router has to re-anchor onto the entry the operator is actually on.
   */
  it('numbers the entry it lands on when a refused Back cannot be addressed by delta', () => {
    session = installSessionHistory(WITH_UNNUMBERED_ENTRY, 2);
    stageEdits();
    const { result } = renderHook(() => useHashRoute());

    act(() => { session!.press(-1); session!.flush(); });

    expect(result.current.activeTab).toBe('stats');
    expect(window.location.hash).toBe('#stats');
    expect(session.position).toBe(1);
    expect(session.currentEntry.state).toEqual(
      expect.objectContaining({ ecmRouteIndex: expect.any(Number) }),
    );
  });

  it('addresses the re-anchored entry by delta on the next Back', () => {
    session = installSessionHistory(WITH_UNNUMBERED_ENTRY, 2);
    stageEdits();
    const { result } = renderHook(() => useHashRoute());
    act(() => { session!.press(-1); session!.flush(); });
    operatorLeftEditMode();

    act(() => result.current.setHash('guide'));
    const guard = watchGuard();
    act(() => { session!.press(-1); session!.flush(); });
    guard.stop();

    expect(guard.seen).toEqual([
      expect.objectContaining({ source: 'pop', to: '#stats', historyDelta: -1 }),
    ]);
    expect(result.current.activeTab).toBe('stats');
    expect(window.location.hash).toBe('#stats');
  });

  it('pushes a programmatic navigation from the accepted entry after a refused Back', () => {
    session = installSessionHistory(ROUTER_NUMBERED, 3);
    stageEdits();
    const { result } = renderHook(() => useHashRoute());
    act(() => { session!.press(-1); session!.flush(); });
    operatorLeftEditMode();

    act(() => result.current.setHash('guide'));

    expect(session.depth).toBe(5);
    expect(session.currentEntry.hash).toBe('#guide');
    expect(session.currentEntry.state).toEqual(expect.objectContaining({ ecmRouteIndex: 4 }));
  });

  it('keeps asking after Keep Editing, which answers nothing', () => {
    session = installSessionHistory(ROUTER_NUMBERED, 3);
    stageEdits();
    const { result } = renderHook(() => useHashRoute());
    act(() => { session!.press(-1); session!.flush(); });

    const guard = watchGuard();
    act(() => { session!.press(-1); session!.flush(); });
    guard.stop();

    expect(guard.seen).toEqual([expect.objectContaining({ source: 'pop', historyDelta: -1 })]);
    expect(result.current.activeTab).toBe('stats');
    expect(session.position).toBe(3);
  });

  it('replays the confirmed transition, skips the guard for it, and re-arms', () => {
    session = installSessionHistory(ROUTER_NUMBERED, 3);
    stageEdits();
    const { result } = renderHook(() => useHashRoute());
    const guard = watchGuard();

    act(() => { session!.press(-1); session!.flush(); });
    expect(guard.seen).toEqual([expect.objectContaining({ to: '#journal', historyDelta: -1 })]);

    // Apply or Discard: the operator answered, so replay the transition they
    // asked for without putting the same question a second time.
    operatorLeftEditMode();
    guard.clear();
    act(() => { result.current.resumeRejectedNavigation(-1); session!.flush(); });

    expect(guard.seen).toHaveLength(0);
    expect(result.current.activeTab).toBe('journal');
    expect(window.location.hash).toBe('#journal');
    expect(session.position).toBe(2);

    // The NEXT Back is a fresh question. A bypass that stuck would leave the
    // guard permanently deaf after one confirmed exit.
    stageEdits();
    act(() => { session!.press(-1); session!.flush(); });
    guard.stop();

    expect(guard.seen).toEqual([expect.objectContaining({ to: '#guide', historyDelta: -1 })]);
    expect(result.current.activeTab).toBe('journal');
    expect(session.position).toBe(2);
  });

  /**
   * The Finding-3 case, in its most dangerous shape: the bypass survives the
   * transition it was armed for and is spent on the operator's next genuine
   * gesture instead, which then leaves Edit Mode with no confirmation at all —
   * the exact defect this branch exists to fix.
   */
  it('never spends the confirmed-exit bypass on an entry the operator did not confirm', () => {
    session = installSessionHistory(ROUTER_NUMBERED, 3);
    stageEdits();
    const { result } = renderHook(() => useHashRoute());
    const guard = watchGuard();

    session.swallowNextGo();
    act(() => { result.current.resumeRejectedNavigation(-2); session!.flush(); });

    guard.clear();
    act(() => { session!.press(-1); session!.flush(); });
    guard.stop();

    expect(guard.seen).not.toHaveLength(0);
    expect(result.current.activeTab).toBe('stats');
    expect(window.location.hash).toBe('#stats');
  });

  it('takes the confirmed-exit bypass down when the browser refuses the traversal', () => {
    session = installSessionHistory(ROUTER_NUMBERED, 3);
    stageEdits();
    const { result } = renderHook(() => useHashRoute());
    const guard = watchGuard();

    session.throwOnNextGo();
    act(() => { result.current.resumeRejectedNavigation(-1); session!.flush(); });

    guard.clear();
    act(() => { session!.press(-1); session!.flush(); });
    guard.stop();

    expect(guard.seen).not.toHaveLength(0);
    expect(result.current.activeTab).toBe('stats');
    expect(window.location.hash).toBe('#stats');
  });

  it('carries the operator by hash when the confirmed traversal produces nothing', () => {
    session = installSessionHistory(ROUTER_NUMBERED, 3);
    const { result } = renderHook(() => useHashRoute());
    const unfulfilled = vi.fn();

    vi.useFakeTimers();
    session.swallowNextGo();
    act(() => { result.current.resumeRejectedNavigation(-1, unfulfilled); session!.flush(); });
    expect(unfulfilled).not.toHaveBeenCalled();

    act(() => { vi.advanceTimersByTime(5000); });

    expect(unfulfilled).toHaveBeenCalledTimes(1);
  });

  it('re-anchors instead of staying armed when the rewind produces nothing', () => {
    session = installSessionHistory(ROUTER_NUMBERED, 3);
    stageEdits();
    const { result } = renderHook(() => useHashRoute());

    vi.useFakeTimers();
    session.swallowNextGo();
    act(() => { session!.press(-1); session!.flush(); });
    expect(result.current.activeTab).toBe('stats');

    act(() => { vi.advanceTimersByTime(5000); });

    // The rewind never happened, so the entry the operator is actually on
    // becomes the accepted one rather than the bookkeeping keeping a slot the
    // browser left.
    expect(window.location.hash).toBe('#stats');
    expect(session.position).toBe(2);

    const guard = watchGuard();
    act(() => { session!.press(-1); session!.flush(); });
    guard.stop();

    expect(guard.seen).not.toHaveLength(0);
    expect(result.current.activeTab).toBe('stats');
  });
});
