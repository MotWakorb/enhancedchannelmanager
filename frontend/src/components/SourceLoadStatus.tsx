import type { SourceLoadState } from './sourceLoadState';

export function SourceLoadStatus({
  state,
  successText,
}: {
  state: SourceLoadState;
  successText: string;
}) {
  const content = state === 'loading'
    ? { icon: 'sync', text: 'Loading source data…' }
    : state === 'permission'
      ? { icon: 'lock', text: 'Source data requires administrator access' }
      : state === 'error'
        ? { icon: 'cloud_off', text: 'Source data unavailable' }
        : { icon: 'check_circle', text: successText };

  return (
    <span className={`source-load-status source-load-status-${state}`}>
      <span className={`material-icons${state === 'loading' ? ' spinning' : ''}`} aria-hidden="true">
        {content.icon}
      </span>
      <span>{content.text}</span>
    </span>
  );
}
