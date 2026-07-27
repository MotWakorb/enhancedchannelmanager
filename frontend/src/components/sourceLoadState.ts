import { HttpError } from '../services/httpClient';

export type SourceLoadState = 'loading' | 'success' | 'error' | 'permission';

export function classifySourceLoadError(error: unknown): Exclude<SourceLoadState, 'loading' | 'success'> {
  return error instanceof HttpError && error.status === 403 ? 'permission' : 'error';
}
