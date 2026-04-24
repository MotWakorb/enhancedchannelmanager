/**
 * Tests for the frontend client-error reporter (ADR-006, bd-i6a1m).
 *
 * Covers:
 *  - stack scrubbing (absolute paths → basename)
 *  - message / route scrubbing (length + query-string stripping)
 *  - UA hash SHA-256 determinism
 *  - sliding-window rate limiter
 *  - payload builder output shape
 *  - send transport selection (sendBeacon vs. fetch)
 *  - end-to-end reportClientError: rate-limit, telemetry disable, sink
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  buildPayload,
  ClientRateLimiter,
  CLIENT_ERRORS_ENDPOINT,
  getTelemetryRuntimeOverride,
  hashUserAgent,
  isTelemetryEnabled,
  MAX_MESSAGE_CHARS,
  MAX_STACK_CHARS,
  RATE_LIMIT_EVENTS,
  RATE_LIMIT_WINDOW_MS,
  reportClientError,
  resetReporterForTests,
  scrubMessage,
  scrubRoute,
  scrubStack,
  sendPayload,
  setTelemetryRuntimeEnabled,
  withImportTelemetry,
} from './clientErrorReporter';

describe('scrubStack', () => {
  it('strips POSIX absolute paths to basenames', () => {
    const stack =
      'at handleClick (/Users/operator/projects/ecm/bundle.js:42:17)\n' +
      'at onClick (/Users/operator/projects/ecm/bundle.js:10:5)';
    const scrubbed = scrubStack(stack);
    expect(scrubbed).not.toContain('/Users/operator');
    expect(scrubbed).toContain('bundle.js:42:17');
    expect(scrubbed).toContain('bundle.js:10:5');
  });

  it('strips Windows absolute paths to basenames', () => {
    const stack = 'at foo (C:\\Users\\op\\bundle.js:10:5)';
    const scrubbed = scrubStack(stack);
    expect(scrubbed).not.toContain('C:\\Users');
    expect(scrubbed).toContain('bundle.js:10:5');
  });

  it('strips file:// URLs to basenames', () => {
    const stack = 'at bar (file:///app/static/assets/chunk.js:5:1)';
    const scrubbed = scrubStack(stack);
    expect(scrubbed).not.toContain('file://');
    expect(scrubbed).not.toContain('/app/static');
    expect(scrubbed).toContain('chunk.js:5:1');
  });

  it('truncates stacks over 4096 chars', () => {
    const stack = 'x'.repeat(5000);
    expect(scrubStack(stack).length).toBe(MAX_STACK_CHARS);
  });

  it('returns empty string for null/undefined', () => {
    expect(scrubStack(null)).toBe('');
    expect(scrubStack(undefined)).toBe('');
    expect(scrubStack('')).toBe('');
  });
});

describe('scrubMessage', () => {
  it('truncates messages over 512 chars', () => {
    expect(scrubMessage('a'.repeat(600)).length).toBe(MAX_MESSAGE_CHARS);
  });

  it('preserves short messages', () => {
    expect(scrubMessage('boom')).toBe('boom');
  });

  it('handles null/undefined defensively', () => {
    expect(scrubMessage(null)).toBe('');
    expect(scrubMessage(undefined)).toBe('');
  });
});

describe('scrubRoute', () => {
  it('drops query strings', () => {
    expect(scrubRoute('/channels?filter=foo')).toBe('/channels');
  });

  it('drops fragments', () => {
    expect(scrubRoute('/channels#section')).toBe('/channels');
  });

  it('drops both', () => {
    expect(scrubRoute('/channels?token=secret#hash')).toBe('/channels');
  });

  it('preserves clean pathnames', () => {
    expect(scrubRoute('/channels')).toBe('/channels');
  });
});

describe('hashUserAgent', () => {
  it('returns a 64-char hex digest', async () => {
    const hash = await hashUserAgent('Mozilla/5.0 (Test)');
    expect(hash).toMatch(/^[0-9a-f]{64}$/);
  });

  it('is deterministic', async () => {
    const a = await hashUserAgent('Mozilla/5.0 (Test)');
    const b = await hashUserAgent('Mozilla/5.0 (Test)');
    expect(a).toBe(b);
  });

  it('differs across inputs', async () => {
    const a = await hashUserAgent('Mozilla/5.0 (FirefoxTest)');
    const b = await hashUserAgent('Mozilla/5.0 (ChromeTest)');
    expect(a).not.toBe(b);
  });
});

describe('ClientRateLimiter', () => {
  it('allows up to maxEvents inside the window', () => {
    const limiter = new ClientRateLimiter(3, 1000);
    expect(limiter.tryConsume(0)).toBe(true);
    expect(limiter.tryConsume(10)).toBe(true);
    expect(limiter.tryConsume(20)).toBe(true);
    expect(limiter.tryConsume(30)).toBe(false);
  });

  it('re-admits events after they age out of the window', () => {
    const limiter = new ClientRateLimiter(2, 1000);
    expect(limiter.tryConsume(0)).toBe(true);
    expect(limiter.tryConsume(500)).toBe(true);
    expect(limiter.tryConsume(600)).toBe(false);
    // first event at t=0 ages out at t=1001
    expect(limiter.tryConsume(1001)).toBe(true);
  });

  it('uses ADR-006 defaults when instantiated without args', () => {
    const limiter = new ClientRateLimiter();
    // 10 events allowed, 11th blocked.
    for (let i = 0; i < RATE_LIMIT_EVENTS; i += 1) {
      expect(limiter.tryConsume(i)).toBe(true);
    }
    expect(limiter.tryConsume(RATE_LIMIT_EVENTS)).toBe(false);
    // All 10 still in the window, window is 60s.
    expect(RATE_LIMIT_WINDOW_MS).toBe(60_000);
  });
});

describe('buildPayload', () => {
  it('produces a payload with the expected shape', async () => {
    const payload = await buildPayload(
      {
        kind: 'boundary',
        message: 'boom',
        stack: 'at onClick (/app/bundle.js:1:1)',
      },
      {
        release: 'v1.2.3',
        userAgent: 'Mozilla/5.0 (UnitTest)',
        pathname: '/channels?filter=foo',
        now: () => new Date('2026-04-24T12:00:00Z'),
      },
    );
    expect(payload).not.toBeNull();
    expect(payload!.kind).toBe('boundary');
    expect(payload!.message).toBe('boom');
    expect(payload!.stack).toBe('at onClick (bundle.js:1:1)');
    expect(payload!.release).toBe('v1.2.3');
    expect(payload!.route).toBe('/channels');
    expect(payload!.user_agent_hash).toMatch(/^[0-9a-f]{64}$/);
    expect(payload!.ts).toBe('2026-04-24T12:00:00.000Z');
  });

  it('scrubs long messages defensively', async () => {
    const payload = await buildPayload(
      { kind: 'other', message: 'a'.repeat(600) },
      { release: 'v1', userAgent: 'ua', pathname: '/x' },
    );
    expect(payload!.message.length).toBe(MAX_MESSAGE_CHARS);
  });
});

describe('sendPayload', () => {
  const payload = {
    kind: 'boundary' as const,
    message: 'boom',
    stack: '',
    release: 'v1',
    route: '/channels',
    user_agent_hash: 'a'.repeat(64),
    ts: '2026-04-24T12:00:00Z',
  };

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('prefers sendBeacon when available and it queues', async () => {
    const sendBeacon = vi.fn().mockReturnValue(true);
    // Ensure navigator has sendBeacon defined.
    Object.defineProperty(navigator, 'sendBeacon', {
      value: sendBeacon,
      configurable: true,
      writable: true,
    });
    const fetchSpy = vi.fn();
    vi.stubGlobal('fetch', fetchSpy);

    const ok = await sendPayload(payload);
    expect(ok).toBe(true);
    expect(sendBeacon).toHaveBeenCalledTimes(1);
    expect(sendBeacon.mock.calls[0][0]).toBe(CLIENT_ERRORS_ENDPOINT);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('falls back to fetch when sendBeacon returns false', async () => {
    const sendBeacon = vi.fn().mockReturnValue(false);
    Object.defineProperty(navigator, 'sendBeacon', {
      value: sendBeacon,
      configurable: true,
      writable: true,
    });
    const fetchSpy = vi
      .fn()
      .mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal('fetch', fetchSpy);

    const ok = await sendPayload(payload);
    expect(ok).toBe(true);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe(CLIENT_ERRORS_ENDPOINT);
    expect(init.method).toBe('POST');
    expect(init.credentials).toBe('include');
    expect(init.keepalive).toBe(true);
  });

  it('swallows fetch errors — never rethrows', async () => {
    Object.defineProperty(navigator, 'sendBeacon', {
      value: undefined,
      configurable: true,
      writable: true,
    });
    const fetchSpy = vi.fn().mockRejectedValue(new Error('network down'));
    vi.stubGlobal('fetch', fetchSpy);

    const ok = await sendPayload(payload);
    expect(ok).toBe(false);
  });
});

describe('reportClientError', () => {
  beforeEach(() => {
    resetReporterForTests();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    resetReporterForTests();
  });

  it('sends a payload on the happy path', async () => {
    const fetchSpy = vi
      .fn()
      .mockResolvedValue(new Response(null, { status: 204 }));
    Object.defineProperty(navigator, 'sendBeacon', {
      value: undefined,
      configurable: true,
      writable: true,
    });
    vi.stubGlobal('fetch', fetchSpy);

    const ok = await reportClientError({
      kind: 'boundary',
      message: 'crash',
      stack: 'at handle (/app/bundle.js:1:1)',
    });
    expect(ok).toBe(true);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const body = JSON.parse(fetchSpy.mock.calls[0][1].body);
    expect(body.kind).toBe('boundary');
    expect(body.message).toBe('crash');
    expect(body.stack).toBe('at handle (bundle.js:1:1)');
    expect(body.route).not.toMatch(/[?#]/);
    expect(body.user_agent_hash).toMatch(/^[0-9a-f]{64}$/);
  });

  it('drops events beyond the rate limit without sending', async () => {
    const fetchSpy = vi
      .fn()
      .mockResolvedValue(new Response(null, { status: 204 }));
    Object.defineProperty(navigator, 'sendBeacon', {
      value: undefined,
      configurable: true,
      writable: true,
    });
    vi.stubGlobal('fetch', fetchSpy);

    for (let i = 0; i < RATE_LIMIT_EVENTS; i += 1) {
      const ok = await reportClientError({
        kind: 'boundary',
        message: `crash ${i}`,
      });
      expect(ok).toBe(true);
    }
    // 11th event drops.
    const dropped = await reportClientError({
      kind: 'boundary',
      message: 'crash overflow',
    });
    expect(dropped).toBe(false);
    expect(fetchSpy).toHaveBeenCalledTimes(RATE_LIMIT_EVENTS);
  });

  it('swallows all exceptions — never rethrows', async () => {
    // fetch throws, sendBeacon missing. reportClientError must still return
    // false, not throw.
    Object.defineProperty(navigator, 'sendBeacon', {
      value: undefined,
      configurable: true,
      writable: true,
    });
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(() => {
        throw new Error('sync throw');
      }),
    );
    let threw = false;
    try {
      await reportClientError({ kind: 'boundary', message: 'x' });
    } catch {
      threw = true;
    }
    expect(threw).toBe(false);
  });
});

describe('isTelemetryEnabled', () => {
  afterEach(() => {
    // Clear any runtime override so other suites see defaults.
    setTelemetryRuntimeEnabled(undefined);
  });

  it('defaults to enabled when the env flag is unset', () => {
    expect(isTelemetryEnabled()).toBe(true);
  });

  it('returns false when the runtime override is set to false', () => {
    setTelemetryRuntimeEnabled(false);
    expect(isTelemetryEnabled()).toBe(false);
  });

  it('returns true when the runtime override is set to true', () => {
    setTelemetryRuntimeEnabled(true);
    expect(isTelemetryEnabled()).toBe(true);
  });

  it('falls back to the build flag when the runtime override is undefined', () => {
    setTelemetryRuntimeEnabled(undefined);
    expect(isTelemetryEnabled()).toBe(true);
    expect(getTelemetryRuntimeOverride()).toBeUndefined();
  });
});

describe('setTelemetryRuntimeEnabled (ADR-006 §10 operator toggle)', () => {
  beforeEach(() => {
    resetReporterForTests();
    setTelemetryRuntimeEnabled(undefined);
  });

  afterEach(() => {
    setTelemetryRuntimeEnabled(undefined);
    vi.restoreAllMocks();
  });

  it('short-circuits reportClientError without issuing a network call when disabled', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('{}', { status: 200 }) as unknown as Response,
    );
    setTelemetryRuntimeEnabled(false);

    const sent = await reportClientError({
      kind: 'boundary',
      message: 'should not be sent',
    });

    expect(sent).toBe(false);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('allows reportClientError to send when re-enabled', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('{}', { status: 200 }) as unknown as Response,
    );
    // sendBeacon usually wins on jsdom — stub it to force the fetch path
    // so we can assert delivery from a single spy.
    const originalBeacon = navigator.sendBeacon;
    Object.defineProperty(navigator, 'sendBeacon', {
      configurable: true,
      value: undefined,
    });
    setTelemetryRuntimeEnabled(true);

    try {
      const sent = await reportClientError({
        kind: 'boundary',
        message: 'sent once',
      });
      expect(sent).toBe(true);
      expect(fetchSpy).toHaveBeenCalledTimes(1);
    } finally {
      Object.defineProperty(navigator, 'sendBeacon', {
        configurable: true,
        value: originalBeacon,
      });
    }
  });
});

describe('withImportTelemetry (ADR-006 §6.5 dynamic-import chunk-load)', () => {
  beforeEach(() => {
    resetReporterForTests();
    setTelemetryRuntimeEnabled(undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('passes the resolved module through on success without extra reports', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('{}', { status: 200 }) as unknown as Response,
    );
    const module = { default: { tag: 'ok' } };
    const wrapped = await withImportTelemetry(Promise.resolve(module));
    expect(wrapped).toBe(module);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('reports a chunk_load error and re-rejects on failure', async () => {
    const originalBeacon = navigator.sendBeacon;
    Object.defineProperty(navigator, 'sendBeacon', {
      configurable: true,
      value: undefined,
    });
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('{}', { status: 200 }) as unknown as Response,
    );

    const err = new Error('Failed to fetch dynamically imported module: ./Tab.js');
    let rethrown: unknown;
    try {
      await withImportTelemetry(Promise.reject(err));
    } catch (caught) {
      rethrown = caught;
    }

    // The wrapper must re-reject with the original error so callers'
    // ErrorBoundary / Suspense still sees the failure.
    expect(rethrown).toBe(err);

    // Allow the fire-and-forget report to settle.
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(fetchSpy).toHaveBeenCalled();
    const [, init] = fetchSpy.mock.calls[0] as [unknown, RequestInit];
    expect(init.method).toBe('POST');
    const body = JSON.parse(init.body as string);
    expect(body.kind).toBe('chunk_load');
    expect(body.message).toContain('Failed to fetch');

    Object.defineProperty(navigator, 'sendBeacon', {
      configurable: true,
      value: originalBeacon,
    });
  });
});
