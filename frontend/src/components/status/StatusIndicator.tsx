/**
 * Status Indicator Component (v0.11.6)
 *
 * Visual indicator for service health status.
 */
import type { ServiceStatus } from '../../types';
import './StatusIndicator.css';

export interface StatusIndicatorProps {
  /** The status to display */
  status: ServiceStatus;
  /** Size variant */
  size?: 'small' | 'medium' | 'large';
  /** Whether to show the status label */
  showLabel?: boolean;
  /** Whether to animate (pulse) */
  animate?: boolean;
  /** Custom class name */
  className?: string;
}

const STATUS_LABELS: Record<ServiceStatus, string> = {
  healthy: 'Operational',
  degraded: 'Degraded',
  unhealthy: 'Outage',
  unconfigured: 'Not Configured',
  unknown: 'Unknown',
};

const STATUS_ICONS: Record<ServiceStatus, string> = {
  healthy: 'check_circle',
  degraded: 'warning',
  unhealthy: 'error',
  unconfigured: 'settings',
  unknown: 'help',
};

export function StatusIndicator({
  status,
  size = 'medium',
  showLabel = false,
  animate = false,
  className = '',
}: StatusIndicatorProps) {
  return (
    <div
      className={`status-indicator status-${status} size-${size} ${animate ? 'animate' : ''} ${className}`}
      title={STATUS_LABELS[status]}
    >
      <span className="status-dot" />
      {showLabel && (
        <>
          <span className="material-icons status-icon">{STATUS_ICONS[status]}</span>
          <span className="status-label">{STATUS_LABELS[status]}</span>
        </>
      )}
    </div>
  );
}

export default StatusIndicator;
