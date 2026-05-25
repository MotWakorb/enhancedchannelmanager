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
import { fetchJson, HttpError } from './httpClient';

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
});
