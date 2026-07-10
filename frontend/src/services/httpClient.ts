/**
 * Shared HTTP client utilities.
 *
 * Provides fetchJson, fetchText, and buildQuery used by api.ts and channelPipelineApi.ts.
 * Includes automatic 401 token refresh retry logic.
 */
import { logger } from '../utils/logger';

/** HTTP error with status code so callers can distinguish auth errors from server errors. */
export class HttpError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = 'HttpError';
    this.status = status;
  }
}

/** Extract a human-readable message from a FastAPI error detail field. */
function extractDetail(detail: unknown): string {
  let msg: string;
  if (typeof detail === 'string') {
    msg = detail;
  } else if (Array.isArray(detail)) {
    // FastAPI validation errors: [{loc: [...], msg: "...", type: "..."}]
    msg = detail.map((e: Record<string, unknown>) => e.msg || JSON.stringify(e)).join('; ');
  } else {
    msg = JSON.stringify(detail);
  }
  return msg.trim() || 'Server returned an empty error';
}

/**
 * Build a query string from an object of parameters.
 * Filters out undefined/null values and converts to string.
 */
export function buildQuery(params: Record<string, string | number | boolean | undefined | null>): string {
  const searchParams = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') {
      searchParams.set(key, String(value));
    }
  }
  const query = searchParams.toString();
  return query ? `?${query}` : '';
}

// Token refresh state — shared across all concurrent requests
let refreshPromise: Promise<boolean> | null = null;

/**
 * Listener invoked after every successful token refresh (reactive 401 retry
 * or proactive timer). Receives the new access token lifetime in seconds, or
 * null when the server did not report one (older backend).
 */
type TokenRefreshListener = (expiresInSeconds: number | null) => void;

const tokenRefreshListeners = new Set<TokenRefreshListener>();

/**
 * Subscribe to successful token refreshes (bd-3ymo4). Used by useAuth to
 * reschedule its proactive refresh timer whenever a refresh happens through
 * ANY path (proactive timer or reactive 401 retry), so the timer always
 * tracks the newest token. Returns an unsubscribe function.
 */
export function subscribeTokenRefresh(listener: TokenRefreshListener): () => void {
  tokenRefreshListeners.add(listener);
  return () => tokenRefreshListeners.delete(listener);
}

function notifyTokenRefresh(expiresInSeconds: number | null): void {
  for (const listener of tokenRefreshListeners) {
    try {
      listener(expiresInSeconds);
    } catch (err) {
      logger.error('Token refresh listener failed:', err);
    }
  }
}

/**
 * Perform the actual refresh call. Never throws — resolves true/false.
 */
async function performTokenRefresh(): Promise<boolean> {
  try {
    logger.info('Refreshing access token...');
    const response = await fetch('/api/auth/refresh', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
    });
    if (response.ok) {
      logger.info('Token refresh successful');
      // Surface the new token lifetime (if the server reports it) so the
      // proactive refresh timer can reschedule. Body parse is best-effort.
      let expiresIn: number | null = null;
      try {
        const body = await response.json();
        if (typeof body?.access_token_expires_in === 'number') {
          expiresIn = body.access_token_expires_in;
        }
      } catch {
        // Body not JSON or already consumed — lifetime stays unknown
      }
      notifyTokenRefresh(expiresIn);
      return true;
    }
    logger.error(`Token refresh failed: ${response.status}`);
    return false;
  } catch (err) {
    logger.error('Token refresh request failed:', err);
    return false;
  }
}

/**
 * Run the refresh while holding the cross-tab 'ecm-token-refresh' Web Lock
 * when the Locks API is available (bd-x67qe). The refreshPromise mutex is
 * per-tab; two tabs crossing the token-expiry boundary can still race
 * POST /auth/refresh with the same pre-rotation cookie — the rotated-away
 * loser used to hard-logout its tab. Holding a browser-wide lock serializes
 * the tabs (the second one refreshes with the winner's already-rotated
 * cookie); the server-side rotation grace window covers whatever the lock
 * cannot (e.g. two different browsers). Falls back silently to the direct
 * path on browsers without the Locks API.
 */
async function refreshHoldingCrossTabLock(): Promise<boolean> {
  const locks =
    typeof navigator !== 'undefined' ? navigator.locks : undefined;
  if (locks?.request) {
    let refreshed = false;
    try {
      await locks.request('ecm-token-refresh', async () => {
        refreshed = await performTokenRefresh();
      });
      return refreshed;
    } catch (err) {
      // Lock machinery failure only — performTokenRefresh never throws.
      // Degrade to the per-tab path rather than failing the refresh.
      logger.warn('Web Locks unavailable for token refresh, using per-tab fallback:', err);
      return performTokenRefresh();
    }
  }
  return performTokenRefresh();
}

/**
 * Attempt to refresh the access token via the refresh endpoint.
 * Uses a mutex so concurrent 401s only trigger one refresh per tab, plus a
 * cross-tab Web Lock (when available) so concurrent TABS only trigger one
 * refresh at a time (bd-x67qe).
 * Returns true if refresh succeeded, false otherwise.
 *
 * Exported so useAuth's proactive refresh timer reuses this single refresh
 * path (same mutex) instead of creating a second one (bd-3ymo4).
 */
export async function tryRefreshToken(): Promise<boolean> {
  if (refreshPromise) return refreshPromise;

  refreshPromise = refreshHoldingCrossTabLock().finally(() => {
    refreshPromise = null;
  });

  return refreshPromise;
}

/** Check if a URL is the refresh endpoint itself (to avoid infinite loops). */
function isRefreshRequest(url: string): boolean {
  return url.includes('/auth/refresh');
}

/**
 * Fetch JSON with error handling and automatic 401 token refresh.
 */
export async function fetchJson<T>(url: string, options?: RequestInit, logPrefix = 'API'): Promise<T> {
  const method = options?.method || 'GET';
  logger.debug(`${logPrefix} request: ${method} ${url}`);

  try {
    const response = await fetch(url, {
      ...options,
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        ...options?.headers,
      },
    });

    // On 401, try refreshing the token and retry once
    if (response.status === 401 && !isRefreshRequest(url)) {
      const refreshed = await tryRefreshToken();
      if (refreshed) {
        logger.debug(`${logPrefix} retrying after token refresh: ${method} ${url}`);
        const retryResponse = await fetch(url, {
          ...options,
          credentials: 'include',
          headers: {
            'Content-Type': 'application/json',
            ...options?.headers,
          },
        });

        if (!retryResponse.ok) {
          let errorDetail = retryResponse.statusText;
          try {
            const errorBody = await retryResponse.json();
            if (errorBody.detail) errorDetail = extractDetail(errorBody.detail);
          } catch { /* not JSON */ }
          logger.error(`${logPrefix} error after retry: ${method} ${url} - ${retryResponse.status} ${errorDetail}`);
          throw new HttpError(errorDetail, retryResponse.status);
        }

        if (retryResponse.status === 204) {
          logger.info(`${logPrefix} success (after refresh): ${method} ${url} - ${retryResponse.status}`);
          return undefined as T;
        }
        const data = await retryResponse.json();
        logger.info(`${logPrefix} success (after refresh): ${method} ${url} - ${retryResponse.status}`);
        return data;
      }
    }

    if (!response.ok) {
      let errorDetail = response.statusText;
      try {
        const errorBody = await response.json();
        if (errorBody.detail) {
          errorDetail = extractDetail(errorBody.detail);
        }
      } catch {
        // Response body isn't JSON or couldn't be parsed
      }
      logger.error(`${logPrefix} error: ${method} ${url} - ${response.status} ${errorDetail}`);
      throw new HttpError(errorDetail, response.status);
    }

    // 204 No Content has no body to parse
    if (response.status === 204) {
      logger.info(`${logPrefix} success: ${method} ${url} - ${response.status}`);
      return undefined as T;
    }

    const data = await response.json();
    logger.info(`${logPrefix} success: ${method} ${url} - ${response.status}`);
    return data;
  } catch (error) {
    logger.exception(`${logPrefix} request failed: ${method} ${url}`, error as Error);
    throw error;
  }
}

/**
 * Fetch text content with error handling and automatic 401 token refresh.
 */
export async function fetchText(url: string, options?: RequestInit, logPrefix = 'API'): Promise<string> {
  const method = options?.method || 'GET';
  logger.debug(`${logPrefix} request (text): ${method} ${url}`);

  try {
    const response = await fetch(url, {
      ...options,
      credentials: 'include',
    });

    // On 401, try refreshing the token and retry once
    if (response.status === 401 && !isRefreshRequest(url)) {
      const refreshed = await tryRefreshToken();
      if (refreshed) {
        logger.debug(`${logPrefix} retrying after token refresh: ${method} ${url}`);
        const retryResponse = await fetch(url, {
          ...options,
          credentials: 'include',
        });

        if (!retryResponse.ok) {
          let errorDetail = retryResponse.statusText;
          try {
            const errorBody = await retryResponse.json();
            if (errorBody.detail) errorDetail = extractDetail(errorBody.detail);
          } catch { /* not JSON */ }
          logger.error(`${logPrefix} error after retry: ${method} ${url} - ${retryResponse.status} ${errorDetail}`);
          throw new HttpError(errorDetail, retryResponse.status);
        }

        const text = await retryResponse.text();
        logger.info(`${logPrefix} success (after refresh): ${method} ${url} - ${retryResponse.status}`);
        return text;
      }
    }

    if (!response.ok) {
      let errorDetail = response.statusText;
      try {
        const errorBody = await response.json();
        if (errorBody.detail) {
          errorDetail = extractDetail(errorBody.detail);
        }
      } catch {
        // Response body isn't JSON
      }
      logger.error(`${logPrefix} error: ${method} ${url} - ${response.status} ${errorDetail}`);
      throw new HttpError(errorDetail, response.status);
    }

    const text = await response.text();
    logger.info(`${logPrefix} success: ${method} ${url} - ${response.status}`);
    return text;
  } catch (error) {
    logger.exception(`${logPrefix} request failed: ${method} ${url}`, error as Error);
    throw error;
  }
}
