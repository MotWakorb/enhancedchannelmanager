/**
 * Tests for the frontend session tracker (bd-arp3o, spike bd-1tl01).
 *
 * Covers:
 *  - First-mount path: generate UUID + persist + POST.
 *  - Idempotency: a re-install in the same tab does not re-POST.
 *  - Existing sessionStorage entry: no UUID generation, no POST.
 *  - Fail-open: sessionStorage unavailable / crypto.randomUUID
 *    unavailable / setItem throws — never raises, never POSTs.
 *  - Telemetry runtime toggle disables the beacon.
 *  - UUIDv4 schema sanity (the persisted/sent value matches the regex
 *    the backend enforces).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  installSessionTracker,
  resetSessionTrackerForTests,
  SESSION_START_ENDPOINT,
  SESSION_STORAGE_KEY,
} from './sessionTracker';
import {
  setTelemetryRuntimeEnabled,
} from './clientErrorReporter';

const UUID_V4_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

/** Settle one microtask + macrotask so fire-and-forget POSTs land. */
async function flush(): Promise<void> {
  await Promise.resolve();
  await new Promise((r) => setTimeout(r, 0));
}

describe('installSessionTracker — happy path (first SPA mount)', () => {
  beforeEach(() => {
    resetSessionTrackerForTests();
    setTelemetryRuntimeEnabled(undefined);
    window.sessionStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    setTelemetryRuntimeEnabled(undefined);
    window.sessionStorage.clear();
  });

  it('generates a UUIDv4, persists it, and POSTs once', async () => {
    // Force the fetch path by stubbing sendBeacon to undefined so we
    // can spy on a single transport.
    Object.defineProperty(navigator, 'sendBeacon', {
      configurable: true,
      value: undefined,
    });
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ deduplicated: false }), { status: 200 }),
    );
    vi.stubGlobal('fetch', fetchSpy);

    await installSessionTracker();
    await flush();

    // sessionStorage now holds a UUIDv4.
    const stored = window.sessionStorage.getItem(SESSION_STORAGE_KEY);
    expect(stored).not.toBeNull();
    expect(stored).toMatch(UUID_V4_RE);

    // Exactly one POST to /api/session-start with that UUID.
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(SESSION_START_ENDPOINT);
    expect(init.method).toBe('POST');
    expect(init.credentials).toBe('include');
    expect(init.keepalive).toBe(true);
    const body = JSON.parse(init.body as string);
    expect(body).toEqual({ session_id: stored });
  });

  it('prefers sendBeacon when available and it queues', async () => {
    const sendBeacon = vi.fn().mockReturnValue(true);
    Object.defineProperty(navigator, 'sendBeacon', {
      configurable: true,
      writable: true,
      value: sendBeacon,
    });
    const fetchSpy = vi.fn();
    vi.stubGlobal('fetch', fetchSpy);

    await installSessionTracker();
    await flush();

    expect(sendBeacon).toHaveBeenCalledTimes(1);
    expect(sendBeacon.mock.calls[0][0]).toBe(SESSION_START_ENDPOINT);
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

describe('installSessionTracker — idempotency', () => {
  beforeEach(() => {
    resetSessionTrackerForTests();
    setTelemetryRuntimeEnabled(undefined);
    window.sessionStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    setTelemetryRuntimeEnabled(undefined);
    window.sessionStorage.clear();
  });

  it('does NOT re-POST when sessionStorage already has a session_id (simulated reload)', async () => {
    // Simulate a reload inside the same tab — sessionStorage entry from
    // a prior mount still exists. The new install must not re-POST.
    window.sessionStorage.setItem(
      SESSION_STORAGE_KEY,
      '11111111-1111-4111-8111-111111111111',
    );
    Object.defineProperty(navigator, 'sendBeacon', {
      configurable: true,
      value: undefined,
    });
    const fetchSpy = vi.fn();
    vi.stubGlobal('fetch', fetchSpy);

    await installSessionTracker();
    await flush();

    expect(fetchSpy).not.toHaveBeenCalled();
    // The pre-existing session_id is left untouched.
    expect(window.sessionStorage.getItem(SESSION_STORAGE_KEY)).toBe(
      '11111111-1111-4111-8111-111111111111',
    );
  });

  it('does NOT re-POST on a second call within the same module lifetime', async () => {
    Object.defineProperty(navigator, 'sendBeacon', {
      configurable: true,
      value: undefined,
    });
    const fetchSpy = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ deduplicated: false }), { status: 200 }),
      );
    vi.stubGlobal('fetch', fetchSpy);

    await installSessionTracker();
    await flush();
    await installSessionTracker();
    await flush();

    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });
});

describe('installSessionTracker — fail-open behavior', () => {
  beforeEach(() => {
    resetSessionTrackerForTests();
    setTelemetryRuntimeEnabled(undefined);
    window.sessionStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    setTelemetryRuntimeEnabled(undefined);
    window.sessionStorage.clear();
  });

  it('does not throw and does not POST when sessionStorage.setItem throws', async () => {
    // Simulate a strict-privacy / quota-exhausted browser.
    // In this test environment, sessionStorage may be implemented via a
    // getter that does not reliably share a stable instance/prototype,
    // so stub the sessionStorage property itself.
    const originalSessionStorage = window.sessionStorage;
    const throwingStorage = {
      getItem: vi.fn(() => null),
      setItem: vi.fn(() => {
        throw new Error('SecurityError: storage disabled');
      }),
      removeItem: vi.fn(() => {}),
      clear: vi.fn(() => {}),
      key: vi.fn(() => null),
      get length() {
        return 0;
      },
    } as unknown as Storage;
    Object.defineProperty(window, 'sessionStorage', {
      configurable: true,
      value: throwingStorage,
    });
    Object.defineProperty(navigator, 'sendBeacon', {
      configurable: true,
      value: undefined,
    });
    const fetchSpy = vi.fn();
    vi.stubGlobal('fetch', fetchSpy);

    let threw = false;
    try {
      await installSessionTracker();
      await flush();
    } catch {
      threw = true;
    }
    expect(threw).toBe(false);
    expect(fetchSpy).not.toHaveBeenCalled();
    Object.defineProperty(window, 'sessionStorage', {
      configurable: true,
      value: originalSessionStorage,
    });
  });

  it('does not POST when crypto.randomUUID is unavailable', async () => {
    // Stash the original crypto and replace with a stub missing randomUUID.
    const originalCrypto = globalThis.crypto;
    Object.defineProperty(globalThis, 'crypto', {
      configurable: true,
      value: { subtle: originalCrypto?.subtle } as Crypto,
    });
    Object.defineProperty(navigator, 'sendBeacon', {
      configurable: true,
      value: undefined,
    });
    const fetchSpy = vi.fn();
    vi.stubGlobal('fetch', fetchSpy);

    try {
      await installSessionTracker();
      await flush();
      expect(fetchSpy).not.toHaveBeenCalled();
      // Nothing persisted because we couldn't generate an id.
      expect(window.sessionStorage.getItem(SESSION_STORAGE_KEY)).toBeNull();
    } finally {
      Object.defineProperty(globalThis, 'crypto', {
        configurable: true,
        value: originalCrypto,
      });
    }
  });

  it('swallows fetch errors — does not rethrow', async () => {
    Object.defineProperty(navigator, 'sendBeacon', {
      configurable: true,
      value: undefined,
    });
    const fetchSpy = vi.fn().mockRejectedValue(new Error('network down'));
    vi.stubGlobal('fetch', fetchSpy);

    let threw = false;
    try {
      await installSessionTracker();
      await flush();
    } catch {
      threw = true;
    }
    expect(threw).toBe(false);
    // sessionStorage was still seeded — the network call is best-effort.
    expect(window.sessionStorage.getItem(SESSION_STORAGE_KEY)).toMatch(
      UUID_V4_RE,
    );
  });
});

describe('installSessionTracker — telemetry runtime toggle', () => {
  beforeEach(() => {
    resetSessionTrackerForTests();
    setTelemetryRuntimeEnabled(undefined);
    window.sessionStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    setTelemetryRuntimeEnabled(undefined);
    window.sessionStorage.clear();
  });

  it('does NOT POST or persist when telemetry is runtime-disabled', async () => {
    setTelemetryRuntimeEnabled(false);
    Object.defineProperty(navigator, 'sendBeacon', {
      configurable: true,
      value: undefined,
    });
    const fetchSpy = vi.fn();
    vi.stubGlobal('fetch', fetchSpy);

    await installSessionTracker();
    await flush();

    expect(fetchSpy).not.toHaveBeenCalled();
    // No sessionStorage write either — short-circuit before generation.
    expect(window.sessionStorage.getItem(SESSION_STORAGE_KEY)).toBeNull();
  });
});
