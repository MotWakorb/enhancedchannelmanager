/**
 * Status Tab (v0.11.6)
 *
 * Main status page showing service health, incidents, and system status.
 * Uses WebSocket for real-time updates.
 * Settings-style sidebar layout.
 */
import { useState, useEffect, useCallback, useMemo } from 'react';
import type {
  ServiceWithStatus,
  StatusOverview,
  Incident,
  MaintenanceWindow,
} from '../../types';
import { useStatusWebSocket } from '../../hooks';
import * as api from '../../services/api';
import { StatusIndicator } from '../status/StatusIndicator';
import { IncidentTimeline } from '../status/IncidentTimeline';
import { MaintenanceBanner } from '../status/MaintenanceBanner';
import { ServiceControlPanel } from '../status/ServiceControlPanel';
import { AlertConfigurationPanel } from '../status/AlertConfigurationPanel';
import './StatusTab.css';

// Tab options for the status page
type StatusTabView = 'overview' | 'incidents' | 'admin';

export function StatusTab() {
  // View state
  const [activeView, setActiveView] = useState<StatusTabView>('overview');

  // Data state
  const [overview, setOverview] = useState<StatusOverview | null>(null);
  const [services, setServices] = useState<ServiceWithStatus[]>([]);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [maintenanceWindows, setMaintenanceWindows] = useState<MaintenanceWindow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Selected service for detail view
  const [selectedServiceId, setSelectedServiceId] = useState<string | null>(null);

  // WebSocket for real-time updates
  const {
    isConnected,
    connectionState,
    serviceStatuses,
  } = useStatusWebSocket({
    handlers: {
      onStatusUpdate: (serviceId, status) => {
        // Update service status in local state
        setServices(prev => prev.map(s =>
          s.id === serviceId ? { ...s, status } : s
        ));
        // Refresh overview when status changes
        fetchOverview();
      },
      onIncidentCreated: (incident) => {
        setIncidents(prev => [incident, ...prev]);
        fetchOverview();
      },
      onIncidentResolved: (incidentId) => {
        setIncidents(prev => prev.map(i =>
          i.id === incidentId ? { ...i, status: 'resolved' } : i
        ));
        fetchOverview();
      },
      onMaintenanceStarted: (window) => {
        setMaintenanceWindows(prev => [...prev, window]);
      },
      onMaintenanceEnded: (windowId) => {
        setMaintenanceWindows(prev => prev.filter(w => w.id !== windowId));
      },
      onInitialStatus: (statuses) => {
        // Merge WebSocket status with fetched services
        setServices(prev => prev.map(s => {
          const wsStatus = statuses[s.id];
          if (wsStatus) {
            return { ...s, status: wsStatus.status, last_check: wsStatus };
          }
          return s;
        }));
      },
    },
  });

  // Fetch overview data
  const fetchOverview = useCallback(async () => {
    try {
      const data = await api.getStatusOverview();
      setOverview(data);
    } catch (err) {
      console.error('Failed to fetch status overview:', err);
    }
  }, []);

  // Fetch services
  const fetchServices = useCallback(async () => {
    try {
      const data = await api.getServices();
      setServices(data);
    } catch (err) {
      console.error('Failed to fetch services:', err);
      setError('Failed to load services');
    }
  }, []);

  // Fetch incidents
  const fetchIncidents = useCallback(async () => {
    try {
      const data = await api.getIncidents({ status: 'active' });
      setIncidents(data);
    } catch (err) {
      console.error('Failed to fetch incidents:', err);
    }
  }, []);

  // Fetch maintenance windows
  const fetchMaintenance = useCallback(async () => {
    try {
      const data = await api.getMaintenanceWindows({ active: true });
      setMaintenanceWindows(data);
    } catch (err) {
      console.error('Failed to fetch maintenance windows:', err);
    }
  }, []);

  // Initial data load
  useEffect(() => {
    const loadData = async () => {
      setLoading(true);
      setError(null);
      try {
        await Promise.all([
          fetchOverview(),
          fetchServices(),
          fetchIncidents(),
          fetchMaintenance(),
        ]);
      } catch (err) {
        setError('Failed to load status data');
      } finally {
        setLoading(false);
      }
    };
    loadData();
  }, [fetchOverview, fetchServices, fetchIncidents, fetchMaintenance]);

  // Merge WebSocket statuses with services
  const servicesWithLiveStatus = useMemo(() => {
    return services.map(service => {
      const wsStatus = serviceStatuses[service.id];
      if (wsStatus) {
        return {
          ...service,
          status: wsStatus.status,
          last_check: wsStatus,
        };
      }
      return service;
    });
  }, [services, serviceStatuses]);

  // Group services by type
  const groupedServices = useMemo(() => {
    const internal = servicesWithLiveStatus.filter(s => s.type === 'internal');
    const external = servicesWithLiveStatus.filter(s => s.type === 'external');
    return { internal, external };
  }, [servicesWithLiveStatus]);

  // Active incidents (not resolved)
  const activeIncidents = useMemo(() => {
    return incidents.filter(i => i.status !== 'resolved');
  }, [incidents]);

  // Active maintenance windows
  const activeMaintenance = useMemo(() => {
    const now = new Date();
    return maintenanceWindows.filter(w => {
      const start = new Date(w.start_time);
      const end = new Date(w.end_time);
      return start <= now && end >= now;
    });
  }, [maintenanceWindows]);

  // Handle service click
  const handleServiceClick = (serviceId: string) => {
    setSelectedServiceId(serviceId === selectedServiceId ? null : serviceId);
  };

  // Handle trigger health check
  const handleTriggerCheck = async (serviceId: string) => {
    try {
      await api.triggerHealthCheck(serviceId);
      // Refresh will come via WebSocket
    } catch (err) {
      console.error('Failed to trigger health check:', err);
    }
  };

  // Render loading state
  if (loading) {
    return (
      <div className="status-tab">
        <div className="status-loading">
          <span className="material-icons spinning">sync</span>
          <span>Loading status...</span>
        </div>
      </div>
    );
  }

  // Render error state
  if (error) {
    return (
      <div className="status-tab">
        <div className="status-error">
          <span className="material-icons">error</span>
          <span>{error}</span>
          <button className="btn-secondary" onClick={() => window.location.reload()}>
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="status-tab">
      {/* Sidebar Navigation */}
      <nav className="status-sidebar">
        <div className="sidebar-header">
          <h2>System Status</h2>
          {overview && (
            <div className="status-overview">
              <StatusIndicator
                status={overview.overall_status}
                size="small"
                showLabel
              />
            </div>
          )}
        </div>

        <ul className="status-nav">
          <li
            className={`status-nav-item ${activeView === 'overview' ? 'active' : ''}`}
            onClick={() => setActiveView('overview')}
          >
            <span className="material-icons">dashboard</span>
            <span>Overview</span>
          </li>
          <li
            className={`status-nav-item ${activeView === 'incidents' ? 'active' : ''}`}
            onClick={() => setActiveView('incidents')}
          >
            <span className="material-icons">warning</span>
            <span>Incidents</span>
            {activeIncidents.length > 0 && (
              <span className="badge">{activeIncidents.length}</span>
            )}
          </li>
          <li
            className={`status-nav-item ${activeView === 'admin' ? 'active' : ''}`}
            onClick={() => setActiveView('admin')}
          >
            <span className="material-icons">admin_panel_settings</span>
            <span>Admin</span>
          </li>
        </ul>

        {/* Connection Status */}
        <div className="sidebar-connection">
          <span className={`connection-dot ${isConnected ? 'connected' : 'disconnected'}`} />
          <span>
            {connectionState === 'connected' ? 'Live' :
             connectionState === 'connecting' ? 'Connecting...' :
             connectionState === 'reconnecting' ? 'Reconnecting...' : 'Offline'}
          </span>
        </div>
      </nav>

      {/* Main Content Area */}
      <div className="status-main">
        {/* Maintenance Banner */}
        {activeMaintenance.length > 0 && (
          <div className="maintenance-banner-container">
            <MaintenanceBanner windows={activeMaintenance} />
          </div>
        )}

        {/* Content */}
        <div className="status-content">
          {activeView === 'overview' && (
            <div className="status-page">
              <div className="status-page-header">
                <h3>
                  <span className="material-icons">dashboard</span>
                  Overview
                </h3>
                <p>Current status of all services and recent activity.</p>
              </div>

              {/* Stats Summary - Compact inline */}
              {overview && (
                <div className="stats-summary-inline">
                  <span className="stat-inline">
                    <strong>{overview.services.total}</strong> Services
                  </span>
                  <span className="stat-inline stat-healthy">
                    <span className="status-dot healthy" />
                    <strong>{overview.services.healthy}</strong> Healthy
                  </span>
                  {overview.services.degraded > 0 && (
                    <span className="stat-inline stat-degraded">
                      <span className="status-dot degraded" />
                      <strong>{overview.services.degraded}</strong> Degraded
                    </span>
                  )}
                  {overview.services.unhealthy > 0 && (
                    <span className="stat-inline stat-unhealthy">
                      <span className="status-dot unhealthy" />
                      <strong>{overview.services.unhealthy}</strong> Unhealthy
                    </span>
                  )}
                  {overview.services.unconfigured > 0 && (
                    <span className="stat-inline stat-unconfigured">
                      <span className="status-dot unconfigured" />
                      <strong>{overview.services.unconfigured}</strong> Not Configured
                    </span>
                  )}
                </div>
              )}

              {/* Services Table */}
              <div className="services-table-container">
                <table className="services-table">
                  <colgroup>
                    <col style={{ width: '60px' }} />
                    <col style={{ width: '200px' }} />
                    <col style={{ width: '80px' }} />
                    <col style={{ minWidth: '180px' }} />
                    <col style={{ width: '70px' }} />
                    <col style={{ width: '100px' }} />
                    <col style={{ width: '50px' }} />
                  </colgroup>
                  <thead>
                    <tr>
                      <th>Status</th>
                      <th>Service</th>
                      <th>Type</th>
                      <th>Message</th>
                      <th>Response</th>
                      <th>Last Check</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {servicesWithLiveStatus.map(service => (
                      <tr
                        key={service.id}
                        className={`service-row ${selectedServiceId === service.id ? 'selected' : ''} status-${service.status || 'unknown'}`}
                        onClick={() => handleServiceClick(service.id)}
                      >
                        <td>
                          <span className={`status-dot ${service.status || 'unknown'}`} />
                        </td>
                        <td>
                          <div className="service-name-cell">
                            <span className="service-name">{service.name}</span>
                          </div>
                          <span className="service-description">{service.description}</span>
                        </td>
                        <td>
                          <span className={`type-badge ${service.type}`}>
                            {service.type === 'internal' ? 'Internal' : 'External'}
                          </span>
                        </td>
                        <td>
                          <span className="status-message">
                            {service.last_check?.message || '—'}
                          </span>
                        </td>
                        <td>
                          {service.last_check?.response_time_ms !== undefined
                            ? `${service.last_check.response_time_ms}ms`
                            : '—'}
                        </td>
                        <td>
                          {service.last_check?.checked_at
                            ? new Date(service.last_check.checked_at).toLocaleTimeString()
                            : '—'}
                        </td>
                        <td>
                          <button
                            className="btn-icon"
                            onClick={(e) => {
                              e.stopPropagation();
                              handleTriggerCheck(service.id);
                            }}
                            title="Trigger health check"
                          >
                            <span className="material-icons">refresh</span>
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Recent Incidents */}
              {activeIncidents.length > 0 && (
                <div className="recent-incidents">
                  <h4>
                    <span className="material-icons">warning</span>
                    Active Incidents
                  </h4>
                  <IncidentTimeline
                    incidents={activeIncidents.slice(0, 3)}
                    compact
                  />
                  {activeIncidents.length > 3 && (
                    <button
                      className="btn-text"
                      onClick={() => setActiveView('incidents')}
                    >
                      View all {activeIncidents.length} incidents
                    </button>
                  )}
                </div>
              )}
            </div>
          )}

          {activeView === 'incidents' && (
            <div className="status-page">
              <div className="status-page-header">
                <h3>
                  <span className="material-icons">warning</span>
                  Incidents
                </h3>
                <p>View and manage service incidents.</p>
              </div>
              <div className="incidents-view">
                <IncidentTimeline
                  incidents={incidents}
                  onRefresh={fetchIncidents}
                />
              </div>
            </div>
          )}

          {activeView === 'admin' && (
            <div className="status-page">
              <div className="status-page-header">
                <h3>
                  <span className="material-icons">admin_panel_settings</span>
                  Admin
                </h3>
                <p>Manage service controls and alert configuration.</p>
              </div>
              <div className="admin-view">
                <div className="admin-panels">
                  <ServiceControlPanel
                    services={servicesWithLiveStatus}
                    onRefresh={fetchServices}
                  />
                  <AlertConfigurationPanel
                    onRefresh={fetchServices}
                  />
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        {overview && (
          <div className="status-footer">
            <span className="last-updated">
              Last updated: {new Date(overview.last_updated).toLocaleTimeString()}
            </span>
            {overview.scheduler_running ? (
              <span className="scheduler-status running">
                <span className="material-icons">check_circle</span>
                Health checks running
              </span>
            ) : (
              <span className="scheduler-status stopped">
                <span className="material-icons">pause_circle</span>
                Health checks paused
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export default StatusTab;
