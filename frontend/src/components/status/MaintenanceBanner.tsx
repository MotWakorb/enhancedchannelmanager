/**
 * Maintenance Banner Component (v0.11.6)
 *
 * Banner showing active maintenance windows.
 */
import { useState } from 'react';
import type { MaintenanceWindow } from '../../types';
import './MaintenanceBanner.css';

export interface MaintenanceBannerProps {
  /** Active maintenance windows */
  windows: MaintenanceWindow[];
}

// Format time range
function formatTimeRange(start: string, end: string): string {
  const startDate = new Date(start);
  const endDate = new Date(end);
  const now = new Date();

  const formatTime = (date: Date) => {
    return date.toLocaleTimeString('en-US', {
      hour: 'numeric',
      minute: '2-digit',
    });
  };

  const formatDate = (date: Date) => {
    return date.toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
    });
  };

  // Same day
  if (startDate.toDateString() === endDate.toDateString()) {
    if (startDate.toDateString() === now.toDateString()) {
      return `${formatTime(startDate)} - ${formatTime(endDate)}`;
    }
    return `${formatDate(startDate)} ${formatTime(startDate)} - ${formatTime(endDate)}`;
  }

  // Different days
  return `${formatDate(startDate)} ${formatTime(startDate)} - ${formatDate(endDate)} ${formatTime(endDate)}`;
}

// Calculate remaining time
function getRemainingTime(end: string): string {
  const endDate = new Date(end);
  const now = new Date();
  const diffMs = endDate.getTime() - now.getTime();

  if (diffMs <= 0) return 'Ending soon';

  const diffMin = Math.floor(diffMs / 60000);
  const diffHour = Math.floor(diffMs / 3600000);

  if (diffMin < 60) return `${diffMin}m remaining`;
  if (diffHour < 24) return `${diffHour}h ${diffMin % 60}m remaining`;
  return `${Math.floor(diffHour / 24)}d remaining`;
}

export function MaintenanceBanner({ windows }: MaintenanceBannerProps) {
  const [expanded, setExpanded] = useState(false);

  if (windows.length === 0) return null;

  const primaryWindow = windows[0];
  const hasMultiple = windows.length > 1;

  return (
    <div className="maintenance-banner">
      <div className="banner-content" onClick={() => hasMultiple && setExpanded(!expanded)}>
        <div className="banner-icon">
          <span className="material-icons">construction</span>
        </div>
        <div className="banner-text">
          <div className="banner-title">
            <strong>Scheduled Maintenance</strong>
            {hasMultiple && <span className="badge">{windows.length}</span>}
          </div>
          <div className="banner-details">
            <span className="maintenance-title">{primaryWindow.title}</span>
            <span className="maintenance-time">
              {formatTimeRange(primaryWindow.start_time, primaryWindow.end_time)}
            </span>
            <span className="maintenance-remaining">
              {getRemainingTime(primaryWindow.end_time)}
            </span>
          </div>
        </div>
        {hasMultiple && (
          <button className="expand-btn">
            <span className="material-icons">
              {expanded ? 'expand_less' : 'expand_more'}
            </span>
          </button>
        )}
        {primaryWindow.suppress_alerts && (
          <span className="alerts-suppressed" title="Alerts are suppressed during maintenance">
            <span className="material-icons">notifications_off</span>
          </span>
        )}
      </div>

      {expanded && hasMultiple && (
        <div className="banner-expanded">
          {windows.slice(1).map(window => (
            <div key={window.id} className="additional-window">
              <span className="window-title">{window.title}</span>
              <span className="window-time">
                {formatTimeRange(window.start_time, window.end_time)}
              </span>
              <span className="window-remaining">
                {getRemainingTime(window.end_time)}
              </span>
              {window.suppress_alerts && (
                <span className="material-icons alerts-icon" title="Alerts suppressed">
                  notifications_off
                </span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default MaintenanceBanner;
