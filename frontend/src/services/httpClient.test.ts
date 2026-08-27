/**
 * Unit tests for httpClient extractDetail fallback (bd-p3jpl).
 *
 * extractDetail() is module-private; we test it via fetchJson's observable
 * behaviour: the HttpError thrown on a non-ok response must always carry a
 * non-empty message, even when the backend sends detail: '' or detail: [].
 *
 * Reproduces the bug scenario: FastAPI returns a 422 with an empty detail
 * field, which previously caused extractDetail to return '' and the modal
 * error banner to be silently suppressed.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { fetchJson, tryRefreshToken, HttpError } from './httpClient';

// Helper: return a minimal Response-like object that fetch() can return.
function mockResponse(status: number, body: unknown, statusText = 'Error'): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
    statusText,
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('extractDetail fallback (bd-p3jpl)', () => {
  it('returns a non-empty fallback when detail is empty string', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      mockResponse(422, { detail: '' }, 'Unprocessable Entity'),
    );

    await expect(fetchJson('/test')).rejects.toSatisfy((err: unknown) => {
      expect(err).toBeInstanceOf(HttpError);
      expect((err as HttpError).message.trim()).not.toBe('');
      return true;
    });
  });

  it('returns a non-empty fallback when detail is an empty array', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      mockResponse(422, { detail: [] }, 'Unprocessable Entity'),
    );

    await expect(fetchJson('/test')).rejects.toSatisfy((err: unknown) => {
      expect(err).toBeInstanceOf(HttpError);
      expect((err as HttpError).message.trim()).not.toBe('');
      return true;
    });
  });

  it('returns the real detail string when detail is non-empty', async () => {
    const detail = 'Source channels [999] no longer exist';
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      mockResponse(422, { detail }, 'Unprocessable Entity'),
    );

    await expect(fetchJson('/test')).rejects.toSatisfy((err: unknown) => {
      expect(err).toBeInstanceOf(HttpError);
      expect((err as HttpError).message).toBe(detail);
      return true;
    });
  });

  it('joins FastAPI validation-error array entries into a single string', async () => {
    const detail = [
      { loc: ['body', 'name'], msg: 'field required', type: 'value_error.missing' },
      { loc: ['body', 'id'], msg: 'value is not a valid integer', type: 'type_error.integer' },
    ];
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      mockResponse(422, { detail }, 'Unprocessable Entity'),
    );

    await expect(fetchJson('/test')).rejects.toSatisfy((err: unknown) => {
      expect(err).toBeInstanceOf(HttpError);
      expect((err as HttpError).message).toBe(
        'field required; value is not a valid integer',
      );
      return true;
    });
  });

  it('falls back to statusText when response body has no detail field', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      mockResponse(500, { error: 'oops' }, 'Internal Server Error'),
    );

    await expect(fetchJson('/test')).rejects.toSatisfy((err: unknown) => {
      expect(err).toBeInstanceOf(HttpError);
      expect((err as HttpError).message).toBe('Internal Server Error');
      return true;
    });
  });

  it('retains structured detail but logs only its safe message', async () => {
    const activeKey = ['active', 'rotation', 'value'].join('-');
    const detail = {
      code: 'mcp_api_key_durability_indeterminate',
      message: 'The key is active but crash durability is indeterminate.',
      operation: 'rotation',
      mcp_api_key: activeKey,
    };
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      mockResponse(503, { detail }, 'Service Unavailable'),
    );

    await expect(fetchJson('/test')).rejects.toSatisfy((err: unknown) => {
      expect(err).toBeInstanceOf(HttpError);
      expect((err as HttpError).message).toBe(detail.message);
      expect((err as HttpError).detail).toEqual(detail);
      return true;
    });

    expect(consoleError.mock.calls.flat().join(' ')).not.toContain(activeKey);
  });
});

describe('tryRefreshToken cross-tab lock (bd-x67qe)', () => {
  /**
   * The per-tab refreshPromise mutex cannot stop two TABS from racing
   * POST /auth/refresh with the same pre-rotation cookie. When the Web
   * Locks API is available, the refresh must run inside
   * navigator.locks.request('ecm-token-refresh', ...) so tabs serialize;
   * when it is not (older browsers, non-secure contexts), the refresh must
   * silently fall back to the direct path.
   */
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  function mockRefreshOk() {
    return vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(
        mockResponse(200, { message: 'Token refreshed', access_token_expires_in: 1800 }, 'OK'),
      );
  }

  it('runs the refresh inside navigator.locks.request when available', async () => {
    const fetchMock = mockRefreshOk();
    const requestMock = vi.fn(
      async (_name: string, cb: () => Promise<void> | void) => await cb(),
    );
    vi.stubGlobal('navigator', { locks: { request: requestMock } });

    await expect(tryRefreshToken()).resolves.toBe(true);

    expect(requestMock).toHaveBeenCalledTimes(1);
    expect(requestMock).toHaveBeenCalledWith(
      'ecm-token-refresh',
      expect.any(Function),
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/auth/refresh',
      expect.objectContaining({ method: 'POST' }),
    );
  });

  it('keeps a single flight across concurrent callers (lock acquired once)', async () => {
    const fetchMock = mockRefreshOk();
    const requestMock = vi.fn(
      async (_name: string, cb: () => Promise<void> | void) => await cb(),
    );
    vi.stubGlobal('navigator', { locks: { request: requestMock } });

    const results = await Promise.all([
      tryRefreshToken(),
      tryRefreshToken(),
      tryRefreshToken(),
    ]);

    expect(results).toEqual([true, true, true]);
    expect(requestMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('falls back to the direct refresh when navigator.locks is undefined', async () => {
    const fetchMock = mockRefreshOk();
    vi.stubGlobal('navigator', {});

    await expect(tryRefreshToken()).resolves.toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('falls back to the direct refresh when the lock machinery rejects', async () => {
    const fetchMock = mockRefreshOk();
    vi.stubGlobal('navigator', {
      locks: { request: vi.fn().mockRejectedValue(new Error('SecurityError')) },
    });

    await expect(tryRefreshToken()).resolves.toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('returns false when the refresh fails inside the lock', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      mockResponse(401, { detail: 'Session not found or revoked' }, 'Unauthorized'),
    );
    const requestMock = vi.fn(
      async (_name: string, cb: () => Promise<void> | void) => await cb(),
    );
    vi.stubGlobal('navigator', { locks: { request: requestMock } });

    await expect(tryRefreshToken()).resolves.toBe(false);
    expect(requestMock).toHaveBeenCalledTimes(1);
  });
});
