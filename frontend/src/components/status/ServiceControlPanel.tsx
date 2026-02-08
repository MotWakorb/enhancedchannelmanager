/**
 * Service Control Panel Component (v0.11.6)
 *
 * Admin panel for managing services (enable/disable, restart, etc.)
 */
import { useState } from 'react';
import type { ServiceWithStatus } from '../../types';
import { StatusIndicator } from './StatusIndicator';
import * as api from '../../services/api';
import './ServiceControlPanel.css';

export interface ServiceControlPanelProps {
  /** List of services */
  services: ServiceWithStatus[];
  /** Refresh callback */
  onRefresh?: () => void;
}

export function ServiceControlPanel({
  services,
  onRefresh,
}: ServiceControlPanelProps) {
  const [loading, setLoading] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);

  // Handle enable/disable toggle
  const handleToggleEnabled = async (serviceId: string, currentlyEnabled: boolean) => {
    setLoading(prev => ({ ...prev, [serviceId]: true }));
    setError(null);

    try {
      if (currentlyEnabled) {
        await api.disableService(serviceId);
      } else {
        await api.enableService(serviceId);
      }
      onRefresh?.();
    } catch (err) {
      setError(`Failed to ${currentlyEnabled ? 'disable' : 'enable'} service`);
    } finally {
      setLoading(prev => ({ ...prev, [serviceId]: false }));
    }
  };

  // Handle restart service
  const handleRestart = async (serviceId: string) => {
    setLoading(prev => ({ ...prev, [`restart-${serviceId}`]: true }));
    setError(null);

    try {
      await api.restartService(serviceId);
      onRefresh?.();
    } catch (err) {
      setError('Failed to restart service');
    } finally {
      setLoading(prev => ({ ...prev, [`restart-${serviceId}`]: false }));
    }
  };

  // Handle trigger check
  const handleTriggerCheck = async (serviceId: string) => {
    setLoading(prev => ({ ...prev, [`check-${serviceId}`]: true }));
    setError(null);

    try {
      await api.triggerHealthCheck(serviceId);
    } catch (err) {
      setError('Failed to trigger health check');
    } finally {
      setLoading(prev => ({ ...prev, [`check-${serviceId}`]: false }));
    }
  };

  return (
    <div className="service-control-panel">
      <div className="panel-header">
        <h3>
          <span className="material-icons">tune</span>
          Service Control
        </h3>
        {onRefresh && (
          <button className="btn-secondary" onClick={onRefresh}>
            <span className="material-icons">refresh</span>
          </button>
        )}
      </div>

      {error && (
        <div className="error-banner">
          <span className="material-icons">error</span>
          {error}
        </div>
      )}

      <div className="service-list">
        {services.map(service => (
          <div
            key={service.id}
            className={`service-row ${service.enabled ? '' : 'disabled'}`}
          >
            <div className="service-info">
              <StatusIndicator status={service.status} size="small" />
              <div className="service-details">
                <span className="service-name">{service.name}</span>
                <span className="service-type">{service.type}</span>
              </div>
            </div>

            <div className="service-controls">
              {/* Trigger Check */}
              <button
                className="btn-icon-small"
                onClick={() => handleTriggerCheck(service.id)}
                disabled={loading[`check-${service.id}`] || !service.enabled}
                title="Trigger Health Check"
              >
                <span className={`material-icons ${loading[`check-${service.id}`] ? 'spinning' : ''}`}>
                  {loading[`check-${service.id}`] ? 'sync' : 'refresh'}
                </span>
              </button>

              {/* Restart (internal services only) */}
              {service.type === 'internal' && (
                <button
                  className="btn-icon-small"
                  onClick={() => handleRestart(service.id)}
                  disabled={loading[`restart-${service.id}`] || !service.enabled}
                  title="Restart Service"
                >
                  <span className={`material-icons ${loading[`restart-${service.id}`] ? 'spinning' : ''}`}>
                    {loading[`restart-${service.id}`] ? 'sync' : 'restart_alt'}
                  </span>
                </button>
              )}

              {/* Enable/Disable Toggle */}
              <button
                className={`toggle-btn ${service.enabled ? 'enabled' : 'disabled'}`}
                onClick={() => handleToggleEnabled(service.id, service.enabled)}
                disabled={loading[service.id] || service.critical}
                title={service.critical ? 'Critical services cannot be disabled' : (service.enabled ? 'Disable' : 'Enable')}
              >
                {loading[service.id] ? (
                  <span className="material-icons spinning">sync</span>
                ) : (
                  <span className="toggle-track">
                    <span className="toggle-thumb" />
                  </span>
                )}
              </button>
            </div>
          </div>
        ))}
      </div>

      <div className="panel-footer">
        <span className="footer-note">
          <span className="material-icons">info</span>
          Critical services cannot be disabled
        </span>
      </div>
    </div>
  );
}

export default ServiceControlPanel;
