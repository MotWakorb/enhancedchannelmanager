/**
 * Shared HTTP client utilities.
 *
 * Provides fetchJson, fetchText, and buildQuery used by api.ts and autoCreationApi.ts.
 * Includes automatic 401 token refresh retry logic.
 */
import { logger } from '../utils/logger';

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
 * Attempt to refresh the access token via the refresh endpoint.
 * Uses a mutex so concurrent 401s only trigger one refresh.
 * Returns true if refresh succeeded, false otherwise.
 */
async function tryRefreshToken(): Promise<boolean> {
  if (refreshPromise) return refreshPromise;

  refreshPromise = (async () => {
    try {
      logger.info('Access token expired, attempting refresh...');
      const response = await fetch('/api/auth/refresh', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
      });
      if (response.ok) {
        logger.info('Token refresh successful');
        return true;
      }
      logger.error(`Token refresh failed: ${response.status}`);
      return false;
    } catch (err) {
      logger.error('Token refresh request failed:', err);
      return false;
    } finally {
      refreshPromise = null;
    }
  })();

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
            if (errorBody.detail) errorDetail = errorBody.detail;
          } catch { /* not JSON */ }
          logger.error(`${logPrefix} error after retry: ${method} ${url} - ${retryResponse.status} ${errorDetail}`);
          throw new Error(errorDetail);
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
          errorDetail = errorBody.detail;
        }
      } catch {
        // Response body isn't JSON or couldn't be parsed
      }
      logger.error(`${logPrefix} error: ${method} ${url} - ${response.status} ${errorDetail}`);
      throw new Error(errorDetail);
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
            if (errorBody.detail) errorDetail = errorBody.detail;
          } catch { /* not JSON */ }
          logger.error(`${logPrefix} error after retry: ${method} ${url} - ${retryResponse.status} ${errorDetail}`);
          throw new Error(errorDetail);
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
          errorDetail = errorBody.detail;
        }
      } catch {
        // Response body isn't JSON
      }
      logger.error(`${logPrefix} error: ${method} ${url} - ${response.status} ${errorDetail}`);
      throw new Error(errorDetail);
    }

    const text = await response.text();
    logger.info(`${logPrefix} success: ${method} ${url} - ${response.status}`);
    return text;
  } catch (error) {
    logger.exception(`${logPrefix} request failed: ${method} ${url}`, error as Error);
    throw error;
  }
}
