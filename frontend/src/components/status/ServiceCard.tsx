/**
 * Service Card Component (v0.11.6)
 *
 * Card displaying service status, response time, and quick actions.
 */
import { useState } from 'react';
import type { ServiceWithStatus } from '../../types';
import { StatusIndicator } from './StatusIndicator';
import './ServiceCard.css';

export interface ServiceCardProps {
  /** The service to display */
  service: ServiceWithStatus;
  /** Whether the card is selected */
  selected?: boolean;
  /** Click handler */
  onClick?: () => void;
  /** Trigger health check handler */
  onTriggerCheck?: () => void;
}

// Format response time
function formatResponseTime(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '-';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

// Format last check time
function formatLastCheck(timestamp: string | null | undefined): string {
  if (!timestamp) return 'Never';
  const date = new Date(timestamp);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffSec = Math.floor(diffMs / 1000);

  if (diffSec < 60) return 'Just now';
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
  return date.toLocaleDateString();
}

export function ServiceCard({
  service,
  selected = false,
  onClick,
  onTriggerCheck,
}: ServiceCardProps) {
  const [checking, setChecking] = useState(false);

  const handleTriggerCheck = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (checking || !onTriggerCheck) return;

    setChecking(true);
    try {
      await onTriggerCheck();
    } finally {
      // Reset after a delay to allow WebSocket update
      setTimeout(() => setChecking(false), 2000);
    }
  };

  return (
    <div
      className={`service-card ${selected ? 'selected' : ''} ${service.enabled ? '' : 'disabled'}`}
      onClick={onClick}
      role="button"
      tabIndex={0}
    >
      {/* Header */}
      <div className="service-card-header">
        <div className="service-info">
          <h4 className="service-name">{service.name}</h4>
          {service.critical && (
            <span className="critical-badge" title="Critical Service">
              <span className="material-icons">priority_high</span>
            </span>
          )}
        </div>
        <StatusIndicator status={service.status} size="medium" animate={service.status === 'unhealthy'} />
      </div>

      {/* Description */}
      {service.description && (
        <p className="service-description">{service.description}</p>
      )}

      {/* Stats */}
      <div className="service-stats">
        <div className="stat">
          <span className="stat-label">Response</span>
          <span className="stat-value">
            {formatResponseTime(service.last_check?.response_time_ms)}
          </span>
        </div>
        <div className="stat">
          <span className="stat-label">Last Check</span>
          <span className="stat-value">
            {formatLastCheck(service.last_check?.checked_at)}
          </span>
        </div>
        <div className="stat">
          <span className="stat-label">Interval</span>
          <span className="stat-value">{service.check_interval}s</span>
        </div>
      </div>

      {/* Message (if any) */}
      {service.last_check?.message && (
        <div className={`service-message status-${service.status}`}>
          <span className="material-icons">info</span>
          <span>{service.last_check.message}</span>
        </div>
      )}

      {/* Actions */}
      <div className="service-actions">
        <button
          className="btn-check"
          onClick={handleTriggerCheck}
          disabled={checking || !service.enabled}
          title="Trigger Health Check"
        >
          <span className={`material-icons ${checking ? 'spinning' : ''}`}>
            {checking ? 'sync' : 'refresh'}
          </span>
          {checking ? 'Checking...' : 'Check Now'}
        </button>
      </div>

      {/* Disabled Overlay */}
      {!service.enabled && (
        <div className="disabled-overlay">
          <span>Disabled</span>
        </div>
      )}
    </div>
  );
}

export default ServiceCard;
