import './EditMode.css';

interface EditModeBannerProps {
  stagedCount: number;
  duration: number | null;
}

function formatDuration(ms: number): string {
  const seconds = Math.floor(ms / 1000);
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);

  if (hours > 0) {
    return `${hours}h ${minutes % 60}m`;
  }
  if (minutes > 0) {
    return `${minutes}m ${seconds % 60}s`;
  }
  return `${seconds}s`;
}

export function EditModeBanner({
  stagedCount,
  duration,
}: EditModeBannerProps) {
  const showWarning = duration !== null && duration > 10 * 60 * 1000; // 10 minutes

  return (
    <div className={`edit-mode-banner ${showWarning ? 'warning' : ''}`}>
      <div className="edit-mode-banner-content">
        <span className="material-icons edit-mode-banner-icon">edit</span>
        <span className="edit-mode-banner-text">
          Edit Mode Active
          {stagedCount > 0 && (
            <span className="edit-mode-banner-count">
              {' '}- {stagedCount} pending change{stagedCount !== 1 ? 's' : ''}
            </span>
          )}
          {duration !== null && (
            <span className="edit-mode-banner-duration">
              {' '}({formatDuration(duration)})
            </span>
          )}
        </span>
        {showWarning && (
          <span className="edit-mode-banner-warning">
            Long session - consider applying changes
          </span>
        )}
      </div>
    </div>
  );
}
