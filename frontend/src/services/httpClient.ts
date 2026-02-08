/**
 * Shared HTTP client utilities.
 *
 * Provides fetchJson, fetchText, and buildQuery used by api.ts and autoCreationApi.ts.
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

/**
 * Fetch JSON with error handling.
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
 * Fetch text content with error handling (e.g. for YAML export).
 */
export async function fetchText(url: string, options?: RequestInit, logPrefix = 'API'): Promise<string> {
  const method = options?.method || 'GET';
  logger.debug(`${logPrefix} request (text): ${method} ${url}`);

  try {
    const response = await fetch(url, {
      ...options,
      credentials: 'include',
    });

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
