/**
 * Integration test — ErrorBoundary render crash → reportClientError.
 *
 * Proves the ADR-006 capture path: a component that throws during
 * render produces a telemetry POST to /api/client-errors.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render } from '@testing-library/react';

import { ErrorBoundary } from '../components/ErrorBoundary';
import {
  reportClientError,
  resetReporterForTests,
} from './clientErrorReporter';

function Boom({ message = 'render-crash' }: { message?: string }): never {
  throw new Error(message);
}

describe('ErrorBoundary → reportClientError', () => {
  let consoleErrorSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    resetReporterForTests();
    // Force the reporter to use fetch (easier to inspect bodies than Blob).
    Object.defineProperty(navigator, 'sendBeacon', {
      value: undefined,
      configurable: true,
      writable: true,
    });
  });

  afterEach(() => {
    consoleErrorSpy.mockRestore();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    resetReporterForTests();
  });

  it('reports a render crash via fetch fallback', async () => {
    const fetchSpy = vi
      .fn()
      .mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal('fetch', fetchSpy);

    render(
      <ErrorBoundary
        onError={(error) => {
          void reportClientError({
            kind: 'boundary',
            message: error.message,
            stack: error.stack ?? '',
          });
        }}
      >
        <Boom />
      </ErrorBoundary>,
    );

    // reportClientError is async; flush microtasks for the hash + send.
    await new Promise((resolve) => setTimeout(resolve, 50));

    expect(fetchSpy).toHaveBeenCalled();
    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe('/api/client-errors');
    expect(init.method).toBe('POST');
    const parsed = JSON.parse(init.body as string);
    expect(parsed.kind).toBe('boundary');
    expect(parsed.message).toBe('render-crash');
    expect(parsed.user_agent_hash).toMatch(/^[0-9a-f]{64}$/);
  });
});
