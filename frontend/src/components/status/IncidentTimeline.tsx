/**
 * Incident Timeline Component (v0.11.6)
 *
 * Timeline view of incidents with status updates.
 */
import { useState } from 'react';
import type { Incident, IncidentStatus, IncidentSeverity } from '../../types';
import './IncidentTimeline.css';

export interface IncidentTimelineProps {
  /** List of incidents to display */
  incidents: Incident[];
  /** Compact mode for embedding */
  compact?: boolean;
  /** Refresh callback */
  onRefresh?: () => void;
}

// Format timestamp for display
function formatTimestamp(isoString: string): string {
  const date = new Date(isoString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMin = Math.floor(diffMs / 60000);
  const diffHour = Math.floor(diffMs / 3600000);
  const diffDay = Math.floor(diffMs / 86400000);

  if (diffMin < 1) return 'Just now';
  if (diffMin < 60) return `${diffMin}m ago`;
  if (diffHour < 24) return `${diffHour}h ago`;
  if (diffDay < 7) return `${diffDay}d ago`;

  return date.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: date.getFullYear() !== now.getFullYear() ? 'numeric' : undefined,
    hour: 'numeric',
    minute: '2-digit',
  });
}

// Get icon for incident status
function getStatusIcon(status: IncidentStatus): string {
  switch (status) {
    case 'investigating':
      return 'search';
    case 'identified':
      return 'bug_report';
    case 'monitoring':
      return 'visibility';
    case 'resolved':
      return 'check_circle';
    default:
      return 'help';
  }
}

// Get label for incident status
function getStatusLabel(status: IncidentStatus): string {
  switch (status) {
    case 'investigating':
      return 'Investigating';
    case 'identified':
      return 'Identified';
    case 'monitoring':
      return 'Monitoring';
    case 'resolved':
      return 'Resolved';
    default:
      return status;
  }
}

// Get severity color class
function getSeverityClass(severity: IncidentSeverity): string {
  switch (severity) {
    case 'critical':
      return 'severity-critical';
    case 'major':
      return 'severity-major';
    case 'minor':
      return 'severity-minor';
    default:
      return '';
  }
}

export function IncidentTimeline({
  incidents,
  compact = false,
  onRefresh,
}: IncidentTimelineProps) {
  const [expandedIncidents, setExpandedIncidents] = useState<Set<number>>(new Set());

  const toggleExpanded = (incidentId: number) => {
    setExpandedIncidents(prev => {
      const next = new Set(prev);
      if (next.has(incidentId)) {
        next.delete(incidentId);
      } else {
        next.add(incidentId);
      }
      return next;
    });
  };

  if (incidents.length === 0) {
    return (
      <div className={`incident-timeline ${compact ? 'compact' : ''}`}>
        <div className="no-incidents">
          <span className="material-icons">check_circle</span>
          <span>No active incidents</span>
        </div>
      </div>
    );
  }

  return (
    <div className={`incident-timeline ${compact ? 'compact' : ''}`}>
      {!compact && onRefresh && (
        <div className="timeline-header">
          <h3>Incident History</h3>
          <button className="btn-secondary" onClick={onRefresh}>
            <span className="material-icons">refresh</span>
            Refresh
          </button>
        </div>
      )}

      <div className="timeline-list">
        {incidents.map(incident => (
          <div
            key={incident.id}
            className={`incident-item ${incident.status} ${getSeverityClass(incident.severity)}`}
          >
            {/* Incident Header */}
            <div
              className="incident-header"
              onClick={() => !compact && toggleExpanded(incident.id)}
              role={compact ? undefined : 'button'}
            >
              <div className="incident-status-icon">
                <span className="material-icons">{getStatusIcon(incident.status)}</span>
              </div>
              <div className="incident-content">
                <div className="incident-title-row">
                  <h4 className="incident-title">{incident.title}</h4>
                  <span className={`severity-badge ${getSeverityClass(incident.severity)}`}>
                    {incident.severity}
                  </span>
                </div>
                <div className="incident-meta">
                  <span className={`status-badge ${incident.status}`}>
                    {getStatusLabel(incident.status)}
                  </span>
                  <span className="incident-service">{incident.service_id}</span>
                  <span className="incident-time">{formatTimestamp(incident.created_at)}</span>
                  {incident.auto_created && (
                    <span className="auto-badge" title="Automatically created">
                      <span className="material-icons">smart_toy</span>
                    </span>
                  )}
                </div>
              </div>
              {!compact && incident.updates && incident.updates.length > 0 && (
                <button className="expand-btn">
                  <span className="material-icons">
                    {expandedIncidents.has(incident.id) ? 'expand_less' : 'expand_more'}
                  </span>
                </button>
              )}
            </div>

            {/* Incident Updates */}
            {!compact && expandedIncidents.has(incident.id) && incident.updates && (
              <div className="incident-updates">
                {incident.updates.map(update => (
                  <div key={update.id} className="update-item">
                    <div className="update-status">
                      <span className={`status-dot ${update.status}`} />
                      <span>{getStatusLabel(update.status)}</span>
                    </div>
                    <p className="update-message">{update.message}</p>
                    <div className="update-meta">
                      <span>{update.created_by}</span>
                      <span>{formatTimestamp(update.created_at)}</span>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* Resolution Info */}
            {incident.status === 'resolved' && incident.resolved_at && (
              <div className="resolution-info">
                <span className="material-icons">check</span>
                Resolved {formatTimestamp(incident.resolved_at)}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

export default IncidentTimeline;
