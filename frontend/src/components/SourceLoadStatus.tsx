import type { SourceLoadState } from './sourceLoadState';
import './SourceLoadStatus.css';

export function SourceLoadStatus({
  state,
  successText,
  sourceName = 'source data',
  stale = false,
  onRetry,
  announce = true,
}: {
  state: SourceLoadState;
  successText: string;
  sourceName?: string;
  stale?: boolean;
  onRetry?: () => void;
  announce?: boolean;
}) {
  const displayName = sourceName.charAt(0).toUpperCase() + sourceName.slice(1);
  const content = state === 'loading'
    ? { icon: 'sync', text: `Loading ${sourceName}…`, label: `Loading ${sourceName}` }
    : state === 'permission'
      ? {
          icon: 'lock',
          text: `${displayName} require${sourceName.endsWith('s') ? '' : 's'} administrator access`,
          label: `${displayName} access denied`,
        }
      : state === 'error'
        ? {
            icon: 'cloud_off',
            text: `${displayName} unavailable${stale ? ' — showing previously loaded data' : ''}`,
            label: `${displayName} unavailable`,
          }
        : { icon: 'check_circle', text: successText, label: `${displayName} loaded` };

  const announcementProps = announce
    ? {
        role: 'status',
        'aria-live': 'polite' as const,
        'aria-atomic': true,
        'aria-busy': state === 'loading',
        'aria-label': content.label,
      }
    : {};

  return (
    <span className={`source-load-status source-load-status-${state}`}>
      <span className="source-load-announcement" {...announcementProps}>
        <span className={`material-icons${state === 'loading' ? ' spinning' : ''}`} aria-hidden="true">
          {content.icon}
        </span>
        <span>{content.text}</span>
      </span>
      {state === 'error' && onRetry && (
        <button
          type="button"
          className="source-load-retry"
          onClick={onRetry}
          aria-label={`Retry loading ${sourceName}`}
        >
          Retry
        </button>
      )}
    </span>
  );
}
