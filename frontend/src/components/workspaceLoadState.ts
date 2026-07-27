import type { SourceLoadState } from './sourceLoadState';

export interface WorkspaceSource {
  key: string;
  label: string;
  state: SourceLoadState;
  hasSnapshot: boolean;
  retry: () => Promise<unknown> | unknown;
}

export interface WorkspaceAggregate {
  state: SourceLoadState;
  stale: boolean;
  failed: WorkspaceSource[];
}

export function aggregateWorkspaceSources(sources: readonly WorkspaceSource[]): WorkspaceAggregate {
  const failed = sources.filter((source) => source.state === 'error');
  const state: SourceLoadState = sources.some((source) => source.state === 'permission')
    ? 'permission'
    : failed.length > 0
      ? 'error'
      : sources.some((source) => source.state === 'loading')
        ? 'loading'
        : 'success';
  return {
    state,
    stale: state === 'error' && sources.every((source) => source.hasSnapshot),
    failed,
  };
}

export async function retryFailedSources(sources: readonly WorkspaceSource[]): Promise<void> {
  await Promise.all(
    sources
      .filter((source) => source.state === 'error')
      .map((source) => Promise.resolve(source.retry())),
  );
}
