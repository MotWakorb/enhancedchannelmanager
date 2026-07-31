import { HttpError } from '../services/httpClient';

export type SourceLoadState = 'loading' | 'success' | 'error' | 'permission';

/**
 * Source views may retain the last successful response after a transient
 * failure, but must label it as previously loaded data. A permission failure
 * is different: consumers must hide cached protected data and mutations.
 */
export function classifySourceLoadError(error: unknown): Exclude<SourceLoadState, 'loading' | 'success'> {
  return error instanceof HttpError && error.status === 403 ? 'permission' : 'error';
}
